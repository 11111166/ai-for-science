"""Bounded ChEMBL target-to-compound lookup for cross-disease product use."""

from __future__ import annotations

import math
import re
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Iterable

import httpx


MODEL_VERSION = "chembl-live-human-single-protein-v2-direction-review"
API_BASE = "https://www.ebi.ac.uk/chembl/api/data"


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))


class ChEMBLRuntimeAdapter:
    """Retrieve a small, strict and explainable activity set without heavy models."""

    def __init__(self, client: httpx.Client | None = None, max_workers: int = 6):
        self.client = client or httpx.Client(timeout=httpx.Timeout(35.0, connect=10.0), headers={"User-Agent": "AI-for-Science-cross-disease-compound-v1"})
        self._owns_client = client is None
        self.max_workers = max(1, max_workers)
        self.request_failures = 0

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = self.client.get(f"{API_BASE}/{path}.json", params=params or {})
                if response.status_code == 429 or response.status_code >= 500:
                    raise httpx.HTTPStatusError("retryable ChEMBL response", request=response.request, response=response)
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(0.35 * (2**attempt))
        self.request_failures += 1
        raise RuntimeError(f"ChEMBL request failed: {path}: {type(last_error).__name__}")

    @staticmethod
    def _exact_target(payload: dict[str, Any], symbol: str) -> dict[str, Any] | None:
        exact = []
        for target in payload.get("targets") or []:
            synonyms = {
                str(item.get("component_synonym") or "").upper()
                for component in target.get("target_components") or []
                for item in component.get("target_component_synonyms") or []
                if str(item.get("syn_type") or "").upper() == "GENE_SYMBOL"
            }
            if target.get("organism") == "Homo sapiens" and target.get("target_type") == "SINGLE PROTEIN" and symbol.upper() in synonyms:
                exact.append(target)
        return sorted(exact, key=lambda row: str(row.get("target_chembl_id") or ""))[0] if exact else None

    def _target_compounds(self, target: Any, direction: str, compounds_per_target: int) -> dict[str, Any]:
        symbol = str(getattr(target, "symbol", "") or "").upper()
        try:
            search = self._get("target/search", {"q": symbol, "limit": 20})
            chembl_target = self._exact_target(search, symbol)
            if not chembl_target:
                return {"symbol": symbol, "status": "not_mapped", "matches": []}
            target_id = chembl_target["target_chembl_id"]
            payload = self._get("activity", {
                "target_chembl_id": target_id,
                "target_organism": "Homo sapiens",
                "assay_type": "B",
                "standard_flag": 1,
                "standard_relation": "=",
                "pchembl_value__isnull": "false",
                "data_validity_comment__isnull": "true",
                "potential_duplicate": 0,
                "limit": 500,
                "order_by": "-pchembl_value",
            })
            grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in payload.get("activities") or []:
                molecule_id = str(row.get("parent_molecule_chembl_id") or row.get("molecule_chembl_id") or "")
                if molecule_id and _number(row.get("pchembl_value")) is not None:
                    grouped[molecule_id].append(row)
            matches = []
            for molecule_id, rows in grouped.items():
                values = sorted(float(row["pchembl_value"]) for row in rows)
                assays = {row.get("assay_chembl_id") for row in rows if row.get("assay_chembl_id")}
                documents = {row.get("document_chembl_id") for row in rows if row.get("document_chembl_id")}
                median = values[len(values) // 2]
                matches.append({
                    "molecule_chembl_id": molecule_id,
                    "target_symbol": symbol,
                    "target_chembl_id": target_id,
                    "target_name": chembl_target.get("pref_name"),
                    "intervention_direction": direction,
                    "pchembl_best": max(values),
                    "pchembl_median": median,
                    "activity_count": len(rows),
                    "assay_count": len(assays),
                    "document_count": len(documents),
                    "activity_types": sorted({str(row.get("standard_type")) for row in rows if row.get("standard_type")}),
                    "representative_assay": rows[0].get("assay_chembl_id"),
                    "representative_document": rows[0].get("document_chembl_id"),
                })
            matches.sort(key=lambda row: (-float(row["pchembl_median"]), -float(row["pchembl_best"]), -int(row["assay_count"]), str(row["molecule_chembl_id"])))
            return {"symbol": symbol, "target_chembl_id": target_id, "target_name": chembl_target.get("pref_name"), "status": "activity_available" if matches else "no_qualified_activity", "matches": matches[:compounds_per_target]}
        except RuntimeError as exc:
            return {"symbol": symbol, "status": "request_failed", "error": str(exc), "matches": []}

    @staticmethod
    def _direction_evidence(direction: str, mechanisms: list[dict[str, Any]], target_id: str) -> tuple[float, str | None, str]:
        relevant = [row for row in mechanisms if row.get("target_chembl_id") == target_id]
        actions = " ".join(f"{row.get('action_type') or ''} {row.get('mechanism_of_action') or ''}" for row in relevant).strip().lower()
        # Structured action types are the direction source; free text may contain
        # negation or comparisons and is retained for review, not auto-scored.
        action_types = " | ".join(str(row.get("action_type") or "").lower() for row in relevant)
        inhibitory = bool(re.search(r"\b(inhibitor|antagonist|blocker|negative (?:allosteric )?modulator|degrader|inverse agonist)\b", action_types))
        activating_types = re.sub(r"\binverse agonist\b", "", action_types)
        activating = bool(re.search(r"\b(agonist|activator|positive (?:allosteric )?modulator)\b", activating_types))
        if direction not in {"inhibit", "activate"}:
            return 0.50, actions or None, "上游干预方向未知，不能判断机制一致性"
        if inhibitory and activating:
            return 0.25, actions or None, "机制记录同时含抑制与激活方向，需要复核冲突"
        if (direction == "inhibit" and inhibitory) or (direction == "activate" and activating):
            return 1.0, actions or None, "机制记录与上游干预方向一致"
        if inhibitory or activating:
            return 0.25, actions or None, "机制记录与上游干预方向相反，需要复核"
        if relevant:
            return 0.50, actions or None, "有机制记录但缺少明确作用类型，方向待确认"
        return 0.50, None, "暂无可用机制方向记录，需实验确认"

    @staticmethod
    def _descriptors(molecule: dict[str, Any]) -> dict[str, Any]:
        props = molecule.get("molecule_properties") or {}
        violations = int(_number(props.get("num_ro5_violations"), 0) or 0)
        return {
            "molecular_weight": _number(props.get("full_mwt")),
            "logp": _number(props.get("alogp")),
            "tpsa": _number(props.get("psa")),
            "h_bond_donors": _number(props.get("hbd")),
            "h_bond_acceptors": _number(props.get("hba")),
            "rotatable_bonds": _number(props.get("rtb")),
            "qed": _number(props.get("qed_weighted")),
            "lipinski_violations": violations,
            "lipinski_pass": violations <= 1,
            "veber_pass": (_number(props.get("psa"), 999) or 999) <= 140 and (_number(props.get("rtb"), 999) or 999) <= 10,
            "structural_alerts": [],
            "structural_alert_count": None,
            "descriptor_source": "ChEMBL calculated molecule properties",
        }

    def _enrich_molecule(self, molecule_id: str) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]]]:
        try:
            molecule = self._get(f"molecule/{molecule_id}")
        except RuntimeError:
            return molecule_id, None, []
        try:
            mechanisms = self._get("mechanism", {"molecule_chembl_id": molecule_id, "limit": 100}).get("mechanisms") or []
        except RuntimeError:
            mechanisms = []
        return molecule_id, molecule, mechanisms

    def discover(
        self,
        targets: Iterable[Any],
        directions: dict[str, str] | None = None,
        max_targets: int = 20,
        compounds_per_target: int = 4,
        max_compounds: int = 100,
    ) -> dict[str, Any]:
        directions = directions or {}
        all_targets = list(targets)
        # Chemistry-ready targets are queried first; rank order remains the final tie-break.
        prioritized = sorted(all_targets, key=lambda target: (
            not bool(((getattr(target, "evidence_breakdown", None) or {}).get("target_to_hit") or {}).get("has_small_molecule_binder")),
            int(getattr(target, "rank", 10**6) or 10**6),
        ))[:max_targets]
        registries = []
        with ThreadPoolExecutor(max_workers=min(self.max_workers, max(1, len(prioritized)))) as executor:
            futures = {
                executor.submit(self._target_compounds, target, directions.get(str(getattr(target, "symbol", "")).upper(), str(getattr(target, "direction", "") or "unknown")), compounds_per_target): target
                for target in prioritized
            }
            for future in as_completed(futures):
                registries.append(future.result())
        registries.sort(key=lambda row: next((int(getattr(target, "rank", 10**6) or 10**6) for target in prioritized if str(getattr(target, "symbol", "")).upper() == row["symbol"]), 10**6))
        raw_matches = [match for registry in registries for match in registry.get("matches") or []]
        molecule_ids = sorted({str(match["molecule_chembl_id"]) for match in raw_matches})
        molecules: dict[str, dict[str, Any]] = {}
        mechanisms: dict[str, list[dict[str, Any]]] = {}
        with ThreadPoolExecutor(max_workers=min(self.max_workers, max(1, len(molecule_ids)))) as executor:
            futures = {executor.submit(self._enrich_molecule, molecule_id): molecule_id for molecule_id in molecule_ids}
            for future in as_completed(futures):
                molecule_id, molecule, mechanism_rows = future.result()
                if molecule:
                    molecules[molecule_id] = molecule
                mechanisms[molecule_id] = mechanism_rows
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for match in raw_matches:
            direction_score, mechanism, direction_note = self._direction_evidence(
                str(match.get("intervention_direction") or "unknown"),
                mechanisms.get(str(match["molecule_chembl_id"]), []),
                str(match["target_chembl_id"]),
            )
            potency = _clamp((float(match["pchembl_median"]) - 5.0) / 4.0)
            replication = _clamp(0.45 * math.log1p(int(match["assay_count"])) / math.log(6) + 0.55 * math.log1p(int(match["document_count"])) / math.log(5))
            match["direction_evidence"] = direction_score
            match["direction_note"] = direction_note
            match["mechanism"] = mechanism
            match["match_score"] = round(_clamp(0.60 * potency + 0.22 * replication + 0.18 * direction_score), 6)
            grouped[str(match["molecule_chembl_id"])].append(match)
        compounds = []
        for molecule_id, matches in grouped.items():
            molecule = molecules.get(molecule_id)
            if not molecule:
                continue
            structures = molecule.get("molecule_structures") or {}
            smiles = structures.get("canonical_smiles")
            if not smiles:
                continue
            matches.sort(key=lambda row: (-float(row["match_score"]), str(row["target_symbol"])))
            descriptors = self._descriptors(molecule)
            qed = _number(descriptors.get("qed"), 0.45) or 0.45
            violations = int(descriptors.get("lipinski_violations") or 0)
            withdrawn = bool(molecule.get("withdrawn_flag"))
            black_box = bool(molecule.get("black_box_warning"))
            developability = _clamp(0.55 * qed + 0.30 * (1 - min(3, violations) / 3) + 0.15 * (1 if descriptors.get("veber_pass") else 0))
            safety = _clamp(0.72 - 0.28 * black_box - 0.42 * withdrawn)
            compounds.append({
                "molecule_chembl_id": molecule_id,
                "name": molecule.get("pref_name") or molecule_id,
                "canonical_smiles": smiles,
                "max_phase": _number(molecule.get("max_phase"), 0),
                "first_approval": molecule.get("first_approval"),
                "oral": molecule.get("oral"),
                "black_box_warning": black_box,
                "withdrawn_flag": withdrawn,
                "therapeutic_flag": bool(molecule.get("therapeutic_flag")),
                "molecule_type": molecule.get("molecule_type"),
                "target_matches": matches,
                "match_score": matches[0]["match_score"],
                "efficacy_score": matches[0]["match_score"],
                "safety_score": round(safety, 6),
                "developability_score": round(developability, 6),
                "descriptors": descriptors,
                "admet_predictions": {},
                "admet_applicability": None,
                "evaluation_tier": "experimental_activity_plus_chembl_properties",
                "limitations": [
                    "活性来自严格筛选的人源单蛋白体外结合实验，不等同于疾病模型药效。",
                    "在线基线仅使用 ChEMBL 结构与临床元数据；完整五项 ADMET 模型需进入离线评价队列。",
                ],
            })
        compounds.sort(key=lambda row: (-float(row["match_score"]), str(row["molecule_chembl_id"])))
        compounds = compounds[:max_compounds]
        mapped = sum(row.get("status") in {"activity_available", "no_qualified_activity"} for row in registries)
        activity_targets = sum(row.get("status") == "activity_available" for row in registries)
        benchmark = {
            "version": "chembl-runtime-benchmark-v1",
            "lookup_scope": "bounded_live_human_single_protein",
            "requested_target_count": len(all_targets),
            "chembl_target_count": len(prioritized),
            "chembl_mapped_targets": mapped,
            "chembl_activity_targets": activity_targets,
            "chembl_request_failures": sum(row.get("status") == "request_failed" for row in registries),
            "qualified_compounds": len(compounds),
            "target_registry": [{key: value for key, value in row.items() if key != "matches"} for row in registries],
            "quality_gates": {
                "strict_human_single_protein": True,
                "qualified_activity_present": bool(compounds),
                "passed": bool(compounds),
            },
        }
        provenance = {
            "source": "ChEMBL REST API",
            "snapshot_id": f"runtime-{MODEL_VERSION}-{datetime.now(timezone.utc).date().isoformat()}",
            "model_version": MODEL_VERSION,
            "scientific_status": "experimental_activity_and_structure_metadata_baseline",
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "data_mode": "live_public_database",
            "scientific_conclusion": False,
            "benchmark": benchmark,
        }
        for compound in compounds:
            compound["provenance"] = provenance
        return {"compounds": compounds, "benchmark": benchmark, "provenance": provenance}
