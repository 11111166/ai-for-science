"""Build a compact compound evidence and ADMET snapshot."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import time
import urllib.parse
import urllib.request
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from rdkit import Chem, DataStructs
from rdkit.Chem import Crippen, Descriptors, FilterCatalog, Lipinski, QED, rdFingerprintGenerator
from scipy.stats import spearmanr
from sklearn.ensemble import ExtraTreesRegressor, RandomForestClassifier
from sklearn.metrics import average_precision_score, mean_absolute_error, roc_auc_score

from .scoring import developability_score, evidence_match_score, safety_score


PACKAGE_ROOT = Path(__file__).resolve().parents[2]
CONFIG_ROOT = PACKAGE_ROOT / "config"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def finite(value: Any, default: float | None = None) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if np.isfinite(result) else default


class Pipeline:
    def __init__(self, root: Path, target_snapshot: Path, intervention_snapshot: Path, model_config: Path | None = None, snapshot_id: str | None = None):
        self.root = root.resolve()
        self.target_snapshot = target_snapshot.resolve()
        self.intervention_snapshot = intervention_snapshot.resolve()
        self.model_config = (model_config or CONFIG_ROOT / "model.json").resolve()
        self.config = json.loads(self.model_config.read_text(encoding="utf-8"))
        self.snapshot_id = snapshot_id or self.config["snapshot_id"]
        self.raw_root = self.root / "data" / "raw" / "compound_evaluation_v1"
        self.cache_root = self.raw_root / "chembl37"
        self.result_root = self.root / "results" / self.snapshot_id
        self.manifest_root = self.root / "manifests"
        self.tdc_archive = self.raw_root / "tdc-admet-group-4426004.zip"
        self.release_path = self.result_root / "compound-evaluation-v1.json"
        self.user_agent = "AI-for-Science-compound-evaluation-v1"
        self.fingerprint_generator = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048)

    def ensure_layout(self) -> None:
        for path in (self.raw_root, self.cache_root, self.result_root, self.manifest_root):
            path.mkdir(parents=True, exist_ok=True)

    def doctor(self) -> dict[str, Any]:
        self.ensure_layout()
        free = shutil.disk_usage(self.root).free
        inputs = [self.target_snapshot, self.intervention_snapshot]
        report = {
            "status": "passed" if all(path.is_file() for path in inputs) and free >= 3 * 1024**3 else "failed",
            "inputs": [{"path": str(path), "present": path.is_file(), "sha256": sha256_file(path) if path.is_file() else None} for path in inputs],
            "free_bytes": free,
            "checked_at": now_iso(),
        }
        atomic_json(self.manifest_root / f"{self.snapshot_id}.doctor.json", report)
        if report["status"] != "passed":
            raise RuntimeError("Compound Evaluation v1 requires both upstream snapshots and at least 3 GiB free space")
        return report

    def _request(self, path: str, params: dict[str, Any] | None = None, cache: Path | None = None) -> dict[str, Any]:
        if cache and cache.exists() and cache.stat().st_size:
            return json.loads(cache.read_text(encoding="utf-8"))
        query = urllib.parse.urlencode(params or {}, doseq=True)
        url = f"{self.config['chembl']['api_base']}/{path}.json" + (f"?{query}" if query else "")
        last_error: Exception | None = None
        for attempt in range(4):
            try:
                request = urllib.request.Request(url, headers={"User-Agent": self.user_agent, "Accept": "application/json"})
                with urllib.request.urlopen(request, timeout=120) as response:
                    payload = json.load(response)
                if cache:
                    atomic_json(cache, payload)
                return payload
            except Exception as exc:  # network boundary; retry with bounded backoff
                last_error = exc
                time.sleep(2**attempt)
        raise RuntimeError(f"ChEMBL request failed for {path}: {type(last_error).__name__}")

    def _download_tdc(self) -> dict[str, Any]:
        expected = self.config["tdc"]["archive_sha256"]
        if not self.tdc_archive.exists() or sha256_file(self.tdc_archive) != expected:
            partial = self.tdc_archive.with_suffix(".zip.part")
            request = urllib.request.Request(self.config["tdc"]["archive_url"], headers={"User-Agent": self.user_agent})
            with urllib.request.urlopen(request, timeout=300) as response, partial.open("wb") as handle:
                shutil.copyfileobj(response, handle, length=4 * 1024 * 1024)
            partial.replace(self.tdc_archive)
        actual = sha256_file(self.tdc_archive)
        if actual != expected:
            raise RuntimeError("TDC ADMET archive SHA-256 mismatch")
        return {"path": str(self.tdc_archive), "bytes": self.tdc_archive.stat().st_size, "sha256": actual}

    @staticmethod
    def _exact_target(payload: dict[str, Any], symbol: str) -> dict[str, Any] | None:
        candidates = []
        for target in payload.get("targets", []):
            synonyms = {
                str(item.get("component_synonym") or "").upper()
                for component in target.get("target_components") or []
                for item in component.get("target_component_synonyms") or []
                if item.get("syn_type") == "GENE_SYMBOL"
            }
            if target.get("organism") == "Homo sapiens" and target.get("target_type") == "SINGLE PROTEIN" and symbol.upper() in synonyms:
                candidates.append(target)
        return sorted(candidates, key=lambda item: str(item.get("target_chembl_id")))[0] if candidates else None

    def _collect_target_activities(self, symbol: str, target: dict[str, Any], direction: str) -> list[dict[str, Any]]:
        chembl_id = target["target_chembl_id"]
        cache = self.cache_root / "activities" / f"{symbol}-{chembl_id}.json"
        payload = self._request("activity", {
            "target_chembl_id": chembl_id,
            "target_organism": "Homo sapiens",
            "assay_type": "B",
            "standard_flag": 1,
            "standard_relation": "=",
            "pchembl_value__isnull": "false",
            "data_validity_comment__isnull": "true",
            "potential_duplicate": 0,
            "limit": int(self.config["chembl"]["maximum_activities_per_target"]),
            "order_by": "-pchembl_value",
        }, cache)
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in payload.get("activities", []):
            molecule_id = str(row.get("parent_molecule_chembl_id") or row.get("molecule_chembl_id") or "")
            pchembl = finite(row.get("pchembl_value"))
            if molecule_id and pchembl is not None:
                grouped[molecule_id].append(row)
        output = []
        for molecule_id, rows in grouped.items():
            pchembl_values = [float(row["pchembl_value"]) for row in rows]
            assay_ids = {str(row.get("assay_chembl_id")) for row in rows if row.get("assay_chembl_id")}
            documents = {str(row.get("document_chembl_id")) for row in rows if row.get("document_chembl_id")}
            types = sorted({str(row.get("standard_type")) for row in rows if row.get("standard_type")})
            output.append({
                "molecule_chembl_id": molecule_id,
                "target_symbol": symbol,
                "target_chembl_id": chembl_id,
                "target_name": target.get("pref_name"),
                "intervention_direction": direction,
                "pchembl_best": max(pchembl_values),
                "pchembl_median": float(np.median(pchembl_values)),
                "activity_count": len(rows),
                "assay_count": len(assay_ids),
                "document_count": len(documents),
                "activity_types": types,
                "representative_assay": rows[0].get("assay_chembl_id"),
                "representative_document": rows[0].get("document_chembl_id"),
            })
        output.sort(key=lambda row: (-row["pchembl_median"], -row["pchembl_best"], -row["assay_count"], row["molecule_chembl_id"]))
        return output[: int(self.config["chembl"]["compounds_per_target"])]

    def _molecule(self, molecule_id: str) -> dict[str, Any] | None:
        cache = self.cache_root / "molecules" / f"{molecule_id}.json"
        try:
            return self._request(f"molecule/{molecule_id}", cache=cache)
        except RuntimeError:
            return None

    def _mechanisms(self, molecule_id: str) -> list[dict[str, Any]]:
        cache = self.cache_root / "mechanisms" / f"{molecule_id}.json"
        try:
            return self._request("mechanism", {"molecule_chembl_id": molecule_id, "limit": 100}, cache).get("mechanisms", [])
        except RuntimeError:
            return []

    @staticmethod
    def _direction_evidence(direction: str, mechanisms: list[dict[str, Any]], target_chembl_id: str) -> tuple[float, str | None]:
        relevant = [row for row in mechanisms if row.get("target_chembl_id") == target_chembl_id]
        actions = " ".join(str(row.get("action_type") or "") + " " + str(row.get("mechanism_of_action") or "") for row in relevant).lower()
        inhibitory = any(token in actions for token in ("inhibitor", "antagonist", "blocker", "negative modulator", "degrader"))
        activating = any(token in actions for token in ("agonist", "activator", "positive modulator"))
        matched = (direction == "inhibit" and inhibitory) or (direction == "activate" and activating)
        if matched:
            return 1.0, actions.strip() or None
        if relevant:
            return 0.25, actions.strip() or None
        return 0.50, None

    @staticmethod
    def _descriptors(smiles: str) -> tuple[Chem.Mol | None, dict[str, Any]]:
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return None, {}
        mw = Descriptors.MolWt(mol)
        logp = Crippen.MolLogP(mol)
        hbd = Lipinski.NumHDonors(mol)
        hba = Lipinski.NumHAcceptors(mol)
        tpsa = Descriptors.TPSA(mol)
        rotatable = Lipinski.NumRotatableBonds(mol)
        violations = int(mw > 500) + int(logp > 5) + int(hbd > 5) + int(hba > 10)
        params = FilterCatalog.FilterCatalogParams()
        params.AddCatalog(FilterCatalog.FilterCatalogParams.FilterCatalogs.PAINS)
        params.AddCatalog(FilterCatalog.FilterCatalogParams.FilterCatalogs.BRENK)
        alerts = sorted({entry.GetDescription() for entry in FilterCatalog.FilterCatalog(params).GetMatches(mol)})
        return mol, {
            "molecular_weight": round(float(mw), 3),
            "logp": round(float(logp), 3),
            "tpsa": round(float(tpsa), 3),
            "h_bond_donors": int(hbd),
            "h_bond_acceptors": int(hba),
            "rotatable_bonds": int(rotatable),
            "fraction_csp3": round(float(Lipinski.FractionCSP3(mol)), 4),
            "qed": round(float(QED.qed(mol)), 4),
            "lipinski_violations": violations,
            "lipinski_pass": violations <= 1,
            "veber_pass": bool(tpsa <= 140 and rotatable <= 10),
            "structural_alerts": alerts,
            "structural_alert_count": len(alerts),
        }

    def _features(self, smiles_values: list[str]) -> tuple[np.ndarray, list[Any], list[int]]:
        rows = []
        fingerprints = []
        valid = []
        for index, smiles in enumerate(smiles_values):
            mol = Chem.MolFromSmiles(str(smiles))
            if mol is None:
                continue
            fp = self.fingerprint_generator.GetFingerprint(mol)
            array = np.zeros(2048, dtype=np.float32)
            DataStructs.ConvertToNumpyArray(fp, array)
            descriptors = np.asarray([
                Descriptors.MolWt(mol) / 500,
                Crippen.MolLogP(mol) / 5,
                Descriptors.TPSA(mol) / 140,
                Lipinski.NumHDonors(mol) / 5,
                Lipinski.NumHAcceptors(mol) / 10,
                Lipinski.NumRotatableBonds(mol) / 10,
            ], dtype=np.float32)
            rows.append(np.concatenate([array, descriptors]))
            fingerprints.append(fp)
            valid.append(index)
        return np.vstack(rows) if rows else np.empty((0, 2054), dtype=np.float32), fingerprints, valid

    def _train_admet(self, candidate_smiles: list[str]) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        candidate_x, candidate_fp, candidate_valid = self._features(candidate_smiles)
        predictions: dict[str, dict[str, Any]] = {str(index): {} for index in range(len(candidate_smiles))}
        benchmarks = {}
        with zipfile.ZipFile(self.tdc_archive) as archive:
            for endpoint in self.config["tdc"]["endpoints"]:
                endpoint_id = endpoint["id"]
                with archive.open(f"admet_group/{endpoint_id}/train_val.csv") as handle:
                    train = pd.read_csv(handle)
                with archive.open(f"admet_group/{endpoint_id}/test.csv") as handle:
                    test = pd.read_csv(handle)
                train_x, train_fp, train_valid = self._features(train["Drug"].astype(str).tolist())
                test_x, _, test_valid = self._features(test["Drug"].astype(str).tolist())
                train_y = train.iloc[train_valid]["Y"].astype(float).to_numpy()
                test_y = test.iloc[test_valid]["Y"].astype(float).to_numpy()
                if endpoint["task"] == "classification":
                    model = RandomForestClassifier(n_estimators=300, min_samples_leaf=2, class_weight="balanced", random_state=20260903, n_jobs=8, max_features="sqrt")
                    model.fit(train_x, train_y.astype(int))
                    test_pred = model.predict_proba(test_x)[:, 1]
                    candidate_pred = model.predict_proba(candidate_x)[:, 1] if len(candidate_x) else np.asarray([])
                    metrics = {"auroc": float(roc_auc_score(test_y, test_pred)), "auprc": float(average_precision_score(test_y, test_pred))}
                else:
                    model = ExtraTreesRegressor(n_estimators=300, min_samples_leaf=2, random_state=20260903, n_jobs=8, max_features=0.5)
                    model.fit(train_x, train_y)
                    test_pred = model.predict(test_x)
                    candidate_pred = model.predict(candidate_x) if len(candidate_x) else np.asarray([])
                    rho = spearmanr(test_y, test_pred).statistic
                    metrics = {"mae": float(mean_absolute_error(test_y, test_pred)), "spearman": float(rho)}
                for position, original_index in enumerate(candidate_valid):
                    similarities = DataStructs.BulkTanimotoSimilarity(candidate_fp[position], train_fp)
                    nearest = max(similarities) if similarities else 0.0
                    predictions[str(original_index)][endpoint_id] = {
                        "value": round(float(candidate_pred[position]), 6),
                        "applicability_similarity": round(float(nearest), 6),
                        "domain": "inside" if nearest >= 0.50 else "borderline" if nearest >= 0.35 else "outside",
                    }
                benchmarks[endpoint_id] = {
                    "label_zh": endpoint["label_zh"],
                    "task": endpoint["task"],
                    "metric": endpoint["metric"],
                    "train_count": int(len(train_y)),
                    "held_out_scaffold_count": int(len(test_y)),
                    **{key: round(value, 6) for key, value in metrics.items()},
                }
                print(f"ADMET {endpoint_id}: {benchmarks[endpoint_id]}", flush=True)
        return predictions, benchmarks

    def build(self) -> dict[str, Any]:
        self.ensure_layout()
        tdc_artifact = self._download_tdc()
        status = self._request("status", cache=self.cache_root / "status.json")
        if status.get("chembl_db_version") != self.config["chembl"]["expected_release"]:
            raise RuntimeError(f"Expected {self.config['chembl']['expected_release']}, got {status.get('chembl_db_version')}")
        target_payload = json.loads(self.target_snapshot.read_text(encoding="utf-8"))
        intervention_payload = json.loads(self.intervention_snapshot.read_text(encoding="utf-8"))
        target_rows = target_payload.get("targets", [])[: int(self.config["chembl"]["maximum_targets"])]
        interventions = {str(row.get("symbol") or "").upper(): row for row in intervention_payload["virtual_intervention"]["results"]}
        target_matches = []
        target_registry = []
        for index, row in enumerate(target_rows, 1):
            symbol = str(row.get("symbol") or "").upper()
            try:
                search = self._request("target/search", {"q": symbol, "limit": 20}, self.cache_root / "targets" / f"{symbol}.json")
            except RuntimeError as exc:
                target_registry.append({
                    "symbol": symbol,
                    "target_chembl_id": None,
                    "status": "request_failed",
                    "error": str(exc),
                })
                print(f"ChEMBL {index}/{len(target_rows)} {symbol}: request failed; retained as unavailable", flush=True)
                continue
            chembl_target = self._exact_target(search, symbol)
            if not chembl_target:
                target_registry.append({"symbol": symbol, "target_chembl_id": None, "status": "not_mapped"})
                continue
            direction = str(interventions.get(symbol, {}).get("direction") or row.get("suggested_direction") or "unknown")
            matches = self._collect_target_activities(symbol, chembl_target, direction)
            target_matches.extend(matches)
            target_registry.append({"symbol": symbol, "target_chembl_id": chembl_target["target_chembl_id"], "target_name": chembl_target.get("pref_name"), "status": "activity_available" if matches else "no_qualified_activity", "qualified_compounds": len(matches)})
            print(f"ChEMBL {index}/{len(target_rows)} {symbol}: {len(matches)} candidates", flush=True)
        molecule_ids = sorted({row["molecule_chembl_id"] for row in target_matches})
        molecules: dict[str, dict[str, Any]] = {}
        mechanisms: dict[str, list[dict[str, Any]]] = {}
        with ThreadPoolExecutor(max_workers=8) as executor:
            molecule_futures = {executor.submit(self._molecule, value): value for value in molecule_ids}
            mechanism_futures = {executor.submit(self._mechanisms, value): value for value in molecule_ids}
            for future in as_completed(molecule_futures):
                value = future.result()
                if value:
                    molecules[molecule_futures[future]] = value
            for future in as_completed(mechanism_futures):
                mechanisms[mechanism_futures[future]] = future.result()
        grouped_matches: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for match in target_matches:
            direction_evidence, mechanism = self._direction_evidence(match["intervention_direction"], mechanisms.get(match["molecule_chembl_id"], []), match["target_chembl_id"])
            match["direction_evidence"] = direction_evidence
            match["mechanism"] = mechanism
            match["match_score"] = evidence_match_score(match["pchembl_median"], match["pchembl_best"], match["assay_count"], match["document_count"], direction_evidence)
            grouped_matches[match["molecule_chembl_id"]].append(match)
        candidates = []
        for molecule_id, matches in grouped_matches.items():
            molecule = molecules.get(molecule_id) or {}
            structures = molecule.get("molecule_structures") or {}
            smiles = structures.get("canonical_smiles")
            if not smiles:
                continue
            mol, descriptors = self._descriptors(smiles)
            if mol is None:
                continue
            hierarchy = molecule.get("molecule_hierarchy") or {}
            warnings = molecule.get("molecule_properties") or {}
            matches.sort(key=lambda item: (-item["match_score"], item["target_symbol"]))
            candidates.append({
                "molecule_chembl_id": molecule_id,
                "parent_molecule_chembl_id": hierarchy.get("parent_chembl_id") or molecule_id,
                "name": molecule.get("pref_name") or molecule_id,
                "canonical_smiles": smiles,
                "max_phase": finite(molecule.get("max_phase"), 0),
                "first_approval": molecule.get("first_approval"),
                "oral": molecule.get("oral"),
                "black_box_warning": bool(molecule.get("black_box_warning")),
                "withdrawn_flag": bool(molecule.get("withdrawn_flag")),
                "therapeutic_flag": bool(molecule.get("therapeutic_flag")),
                "molecule_type": molecule.get("molecule_type"),
                "target_matches": matches,
                "match_score": matches[0]["match_score"],
                "descriptors": descriptors,
                "chembl_calculated_properties": warnings,
            })
        candidates.sort(key=lambda row: (-row["match_score"], row["molecule_chembl_id"]))
        admet_predictions, admet_benchmarks = self._train_admet([row["canonical_smiles"] for row in candidates])
        for index, row in enumerate(candidates):
            prediction = admet_predictions[str(index)]
            row["admet_predictions"] = prediction
            ames = prediction.get("ames", {}).get("value")
            herg = prediction.get("herg", {}).get("value")
            hia = prediction.get("hia_hou", {}).get("value")
            solubility = prediction.get("solubility_aqsoldb", {}).get("value")
            alerts = int(row["descriptors"]["structural_alert_count"])
            row["efficacy_score"] = row["match_score"]
            row["safety_score"] = safety_score(ames, herg, alerts, row["black_box_warning"], row["withdrawn_flag"])
            row["developability_score"] = developability_score(row["descriptors"]["qed"], row["descriptors"]["lipinski_violations"], row["descriptors"]["tpsa"], row["descriptors"]["rotatable_bonds"], solubility, hia, alerts)
            domains = [value["applicability_similarity"] for value in prediction.values()]
            row["admet_applicability"] = round(float(np.mean(domains)), 6) if domains else None
        candidates.sort(key=lambda row: (-(0.50 * row["efficacy_score"] + 0.25 * row["safety_score"] + 0.25 * row["developability_score"]), row["molecule_chembl_id"]))
        for rank, row in enumerate(candidates, 1):
            row["rank"] = rank
        mapped = sum(item["status"] in {"activity_available", "no_qualified_activity"} for item in target_registry)
        activity_targets = sum(item["status"] == "activity_available" for item in target_registry)
        request_failures = sum(item["status"] == "request_failed" for item in target_registry)
        classifiers = [value for value in admet_benchmarks.values() if value["task"] == "classification"]
        gates = {
            "chembl_release_pinned": status.get("chembl_db_version") == self.config["chembl"]["expected_release"],
            "chembl_request_reliability": request_failures / max(1, len(target_registry)) <= 0.05,
            "target_mapping": mapped / max(1, len(target_registry)) >= 0.50,
            "activity_target_count": activity_targets >= 5,
            "compound_count": len(candidates) >= 20,
            "admet_scaffold_benchmark": sum(float(value.get("auroc") or 0) >= 0.60 for value in classifiers) >= 2,
        }
        benchmark = {
            "version": "compound-evaluation-benchmark-v1",
            "chembl_target_count": len(target_registry),
            "chembl_mapped_targets": mapped,
            "chembl_activity_targets": activity_targets,
            "chembl_request_failures": request_failures,
            "qualified_compounds": len(candidates),
            "admet": admet_benchmarks,
            "quality_gates": {**gates, "passed": all(gates.values())},
        }
        release = {
            "schema_version": "compound-evaluation-product-snapshot-v1",
            "snapshot_id": self.snapshot_id,
            "created_at": now_iso(),
            "quality_passed": bool(benchmark["quality_gates"]["passed"]),
            "scientific_status": "experimental_activity_plus_computational_property_baseline",
            "model_version": self.config["model_version"],
            "disease": target_payload.get("disease"),
            "sources": {
                "chembl": {"version": status.get("chembl_db_version"), "release_date": status.get("chembl_release_date"), "api_status": status, "retrieved_at": now_iso()},
                "rdkit": {"role": "structure standardization, descriptors, QED, Lipinski, PAINS and Brenk alerts"},
                "tdc": {**tdc_artifact, "label": self.config["tdc"]["benchmark_snapshot"], "split": "official scaffold-held-out test"},
                "upstream_target_snapshot": {"snapshot_id": target_payload.get("snapshot_id"), "sha256": sha256_file(self.target_snapshot)},
                "upstream_intervention_snapshot": {"snapshot_id": intervention_payload.get("snapshot_id"), "sha256": sha256_file(self.intervention_snapshot)},
            },
            "score_contract": {
                "match": "60% robust pChEMBL potency + 20% assay/document replication + 15% mechanism-direction evidence + 5% strict human single-protein quality gate",
                "safety": "Ames/hERG random-forest risk with structural-alert, black-box and withdrawal penalties",
                "developability": "QED, Lipinski, Veber, predicted solubility and HIA; all components shown separately",
            },
            "benchmark": benchmark,
            "target_registry": target_registry,
            "compounds": candidates[:300],
            "limitations": [
                "ChEMBL binding activity is in-vitro evidence and does not establish in-vivo efficacy or disease benefit.",
                "TDC random-forest outputs are baseline predictions, not measured ADMET values; low-similarity compounds are marked outside the applicability domain.",
                "PAINS/Brenk alerts and Lipinski/Veber rules are triage heuristics, not pass/fail safety decisions.",
            ],
        }
        atomic_json(self.release_path, release)
        manifest = {"snapshot_id": self.snapshot_id, "release_path": str(self.release_path), "release_sha256": sha256_file(self.release_path), "bytes": self.release_path.stat().st_size, "quality_passed": release["quality_passed"], "compound_count": len(release["compounds"]), "built_at": now_iso()}
        atomic_json(self.result_root / "release-manifest.json", manifest)
        if not release["quality_passed"]:
            raise RuntimeError("Compound Evaluation v1 quality gates failed")
        return manifest
