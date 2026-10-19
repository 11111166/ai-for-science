"""Build compact RA virtual-intervention and DepMap knockout snapshots."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .scoring import average_precision, cell_line_dependency_risk, clamp, ndcg_at_k, roc_auc, virtual_intervention_score


PACKAGE_ROOT = Path(__file__).resolve().parents[2]
CONFIG_ROOT = PACKAGE_ROOT / "config"
GENE_PATTERN = re.compile(r"^(.+?)\s*\(\d+\)$")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def digest(path: Path, algorithm: str = "sha256") -> str:
    value = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def gene_symbol(value: str) -> str:
    match = GENE_PATTERN.match(str(value).strip())
    return (match.group(1) if match else str(value)).upper()


def distribution(values: list[float]) -> dict[str, float | int | None]:
    """Compact audit summary used by the UI to expose score separation."""
    finite = np.asarray([float(value) for value in values if np.isfinite(float(value))], dtype=float)
    if not len(finite):
        return {"minimum": None, "median": None, "maximum": None, "unique_values": 0}
    return {
        "minimum": round(float(finite.min()), 6),
        "median": round(float(np.median(finite)), 6),
        "maximum": round(float(finite.max()), 6),
        "unique_values": int(np.unique(np.round(finite, 6)).size),
    }


class Pipeline:
    def __init__(self, root: Path, target_snapshot: Path, snapshot_id: str | None = None):
        self.root = root.resolve()
        self.target_snapshot = target_snapshot.resolve()
        self.registry = json.loads((CONFIG_ROOT / "sources.json").read_text(encoding="utf-8"))
        self.snapshot_id = snapshot_id or self.registry["snapshot_id"]
        self.raw_root = self.root / "data" / "raw" / "depmap" / "24Q4"
        self.manifest_root = self.root / "manifests"
        self.result_root = self.root / "results" / self.snapshot_id
        self.download_manifest = self.manifest_root / f"{self.snapshot_id}.downloads.json"
        self.release_path = self.result_root / "perturbation-knockout-v1.json"

    def ensure_layout(self) -> None:
        self.raw_root.mkdir(parents=True, exist_ok=True)
        self.manifest_root.mkdir(parents=True, exist_ok=True)
        self.result_root.mkdir(parents=True, exist_ok=True)

    def doctor(self) -> dict:
        self.ensure_layout()
        free = shutil.disk_usage(self.root).free
        report = {
            "status": "passed" if free >= 2 * 1024**3 and self.target_snapshot.is_file() else "failed",
            "target_snapshot": str(self.target_snapshot),
            "target_snapshot_present": self.target_snapshot.is_file(),
            "free_bytes": free,
            "checked_at": now_iso(),
        }
        atomic_json(self.manifest_root / f"{self.snapshot_id}.doctor.json", report)
        if report["status"] != "passed":
            raise RuntimeError("Target snapshot is required and at least 2 GiB free space is needed")
        return report

    @staticmethod
    def _download(url: str, destination: Path) -> None:
        if destination.exists() and destination.stat().st_size:
            return
        partial = destination.with_suffix(destination.suffix + ".part")
        offset = partial.stat().st_size if partial.exists() else 0
        headers = {"User-Agent": "AI-for-Science-perturbation-knockout-v1"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        response = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=300)
        append = offset > 0 and response.status == 206
        with response, partial.open("ab" if append else "wb") as handle:
            shutil.copyfileobj(response, handle, length=8 * 1024 * 1024)
        partial.replace(destination)

    def download(self) -> dict:
        self.ensure_layout()
        artifacts = []
        for source in self.registry["sources"]:
            destination = self.raw_root / source["filename"]
            if destination.exists() and destination.stat().st_size != source["bytes"]:
                destination.replace(destination.with_suffix(destination.suffix + ".part"))
            self._download(source["url"], destination)
            if destination.stat().st_size != source["bytes"]:
                raise RuntimeError(f"Unexpected byte count for {source['filename']}")
            actual_md5 = digest(destination, "md5")
            if actual_md5 != source["md5"]:
                raise RuntimeError(f"MD5 mismatch for {source['filename']}")
            artifacts.append({
                "source_id": source["id"], "path": str(destination), "bytes": destination.stat().st_size,
                "md5": actual_md5, "sha256": digest(destination),
            })
            print(f"verified {source['filename']} ({destination.stat().st_size} bytes)", flush=True)
        report = {
            "manifest_version": "perturbation-knockout-download-manifest-v1",
            "snapshot_id": self.snapshot_id,
            "created_at": now_iso(),
            "artifacts": artifacts,
        }
        atomic_json(self.download_manifest, report)
        return report

    def _virtual_interventions(self, target_payload: dict) -> tuple[list[dict], dict]:
        rows = []
        targets = target_payload.get("targets", [])
        genetic_raw = [
            0.65 * max(float(target.get("gwas_score") or 0), float(target.get("eqtl_score") or 0))
            + 0.35 * min(float(target.get("gwas_score") or 0), float(target.get("eqtl_score") or 0))
            for target in targets
        ]
        genetic_percentiles = pd.Series(genetic_raw).rank(method="average", pct=True).to_numpy() if targets else np.array([])
        network_percentiles = pd.Series([float(target.get("network_score") or 0) for target in targets]).rank(method="average", pct=True).to_numpy() if targets else np.array([])
        for index, target in enumerate(targets):
            score = virtual_intervention_score(target, float(genetic_percentiles[index]), float(network_percentiles[index]))
            if score["direction"] == "unknown" or score["direction_component"] == 0:
                continue
            rows.append({
                "target_id": target.get("target_id"), "symbol": target.get("symbol"), "approved_name": target.get("approved_name"),
                "target_rank": target.get("rank"), "cell_type": target.get("best_cell_type"),
                "case_donors": target.get("case_donors"), "control_donors": target.get("control_donors"),
                "single_cell_log2fc": target.get("single_cell_log2fc"), "single_cell_fdr": target.get("single_cell_fdr"),
                "known_drug_label": int(target.get("known_drug_label") or 0),
                **score,
                "effect_interpretation": "attenuate_disease_upregulated_target" if score["direction"] == "inhibit" else "restore_disease_downregulated_target",
                "model_scope": "target-direction and unsigned disease-module attenuation; no post-perturbation expression matrix",
            })
        rows.sort(key=lambda row: (-row["virtual_intervention_score"], str(row["target_id"])))
        for rank, row in enumerate(rows, 1):
            row["rank"] = rank
        top = rows[:20]
        benchmark = {
            "benchmark_version": "virtual-intervention-benchmark-v2",
            "candidate_count": len(rows),
            "direction_coverage": round(len(rows) / max(1, len(target_payload.get("targets", []))), 6),
            "top20_multimodal_count": sum(row["direct_modality_count"] >= 2 for row in top),
            "top20_high_grade_count": sum(row["evidence_grade"] == "high" for row in top),
            "known_drug_ndcg_at_20_sanity_only": ndcg_at_k(rows, "virtual_intervention_score", "known_drug_label"),
            "score_distributions": {
                "intervention_score": distribution([row["virtual_intervention_score"] for row in rows]),
                "confidence": distribution([row["confidence"] for row in rows]),
                "genetic_support": distribution([row["genetic_support"] for row in rows]),
                "network_module_reach": distribution([row["network_module_reach"] for row in rows]),
            },
            "quality_gates": {},
            "limitations": [
                "The unsigned protein network estimates disease-module reach and cannot predict activating versus inhibitory edges.",
                "Intervention direction is inferred from donor-aware disease-versus-control expression and is context dependent.",
                "Known-drug recovery is a held-out retrospective sanity check and never enters the intervention score.",
                "This baseline does not generate a post-perturbation transcriptome and is not a virtual-cell model.",
            ],
        }
        gates = {
            "candidate_count": len(rows) >= 20,
            "direction_coverage": benchmark["direction_coverage"] >= 0.10 and len(rows) >= 20,
            "multimodal_top20": benchmark["top20_multimodal_count"] >= 3,
            "donor_context_present": sum(bool(row.get("case_donors")) and bool(row.get("control_donors")) for row in rows) >= 20,
        }
        benchmark["quality_gates"] = {**gates, "passed": all(gates.values())}
        return rows, benchmark

    def _knockout(self, target_payload: dict) -> tuple[list[dict], dict]:
        common = pd.read_csv(self.raw_root / "AchillesCommonEssentialControls.csv")["Gene"].map(gene_symbol).tolist()
        nonessential = pd.read_csv(self.raw_root / "AchillesNonessentialControls.csv")["Gene"].map(gene_symbol).tolist()
        requested = [str(item.get("symbol") or "").upper() for item in target_payload.get("targets", []) if item.get("symbol")]
        header = pd.read_csv(self.raw_root / "CRISPRGeneEffect.csv", nrows=0).columns.tolist()
        symbol_to_column = {gene_symbol(column): column for column in header[1:]}
        needed_symbols = set(requested) | set(common) | set(nonessential)
        selected_symbols = sorted(needed_symbols & set(symbol_to_column))
        columns = [header[0]] + [symbol_to_column[symbol] for symbol in selected_symbols]
        effects = pd.read_csv(self.raw_root / "CRISPRGeneEffect.csv", usecols=columns).set_index(header[0])
        effects.columns = [gene_symbol(column) for column in effects.columns]
        metadata = pd.read_csv(self.raw_root / "Model.csv").set_index("ModelID")
        shared_models = effects.index.intersection(metadata.index)
        effects = effects.loc[shared_models]
        metadata = metadata.loc[shared_models]
        heme_models = metadata.index[metadata["OncotreeLineage"].isin(["Lymphoid", "Myeloid"])]

        rows = []
        target_lookup = {str(item.get("symbol") or "").upper(): item for item in target_payload.get("targets", [])}
        for symbol in requested:
            if symbol not in effects.columns:
                rows.append({"symbol": symbol, "target_id": target_lookup[symbol].get("target_id"), "screened": False, "reason": "gene_not_in_depmap_24q4"})
                continue
            values = effects[symbol].dropna()
            heme = effects.loc[effects.index.intersection(heme_models), symbol].dropna()
            pan_median = float(values.median())
            heme_median = float(heme.median()) if len(heme) else None
            dependency_fraction = float((values <= -0.50).mean())
            strong_dependency_fraction = float((values <= -1.0).mean())
            risk = cell_line_dependency_risk(pan_median, dependency_fraction, strong_dependency_fraction)
            context_delta = pan_median - heme_median if heme_median is not None else None
            context_shift = 1.0 / (1.0 + np.exp(-(context_delta / 0.15))) if context_delta is not None else None
            rows.append({
                "target_id": target_lookup[symbol].get("target_id"), "symbol": symbol,
                "approved_name": target_lookup[symbol].get("approved_name"), "target_rank": target_lookup[symbol].get("rank"),
                "screened": True, "pan_model_count": int(values.size), "hematopoietic_model_count": int(heme.size),
                "pan_median_gene_effect": round(pan_median, 6), "pan_q10_gene_effect": round(float(values.quantile(0.10)), 6),
                "pan_q25_gene_effect": round(float(values.quantile(0.25)), 6), "pan_q75_gene_effect": round(float(values.quantile(0.75)), 6),
                "hematopoietic_median_gene_effect": None if heme_median is None else round(heme_median, 6),
                "hematopoietic_q25_gene_effect": None if not len(heme) else round(float(heme.quantile(0.25)), 6),
                "hematopoietic_q75_gene_effect": None if not len(heme) else round(float(heme.quantile(0.75)), 6),
                "pan_dependency_fraction": round(dependency_fraction, 6),
                "pan_strong_dependency_fraction": round(strong_dependency_fraction, 6),
                "hematopoietic_dependency_fraction": None if not len(heme) else round(float((heme <= -0.50).mean()), 6),
                "cell_line_dependency_risk": round(risk, 6),
                "universal_essentiality_risk": round(risk, 6), "knockout_tolerability": round(1.0 - risk, 6),
                "hematopoietic_effect_delta": None if context_delta is None else round(context_delta, 6),
                "hematopoietic_context_sensitivity": None if context_shift is None else round(float(context_shift), 6),
                "common_essential_control": symbol in common, "nonessential_control": symbol in nonessential,
                "domain": "cancer-cell fitness; hematopoietic lineages are an out-of-domain proxy for RA immune cells",
            })

        control_rows = []
        for symbol, label in [(value, 1) for value in common] + [(value, 0) for value in nonessential]:
            if symbol in effects.columns:
                median = float(effects[symbol].median(skipna=True))
                if np.isfinite(median):
                    control_rows.append({"symbol": symbol, "label": label, "score": -median, "median": median})
        labels = [row["label"] for row in control_rows]
        scores = [row["score"] for row in control_rows]
        predictions = [row["median"] <= -0.50 for row in control_rows]
        positives = sum(labels)
        negatives = len(labels) - positives
        sensitivity = sum(bool(pred) and label == 1 for pred, label in zip(predictions, labels)) / max(1, positives)
        specificity = sum(not bool(pred) and label == 0 for pred, label in zip(predictions, labels)) / max(1, negatives)
        target_screened = sum(bool(row.get("screened")) for row in rows)
        gates = {
            "model_count": len(shared_models) >= 700,
            "hematopoietic_proxy_models": len(heme_models) >= 100,
            "target_coverage": target_screened / max(1, len(rows)) >= 0.80,
            "control_coverage": len(control_rows) / max(1, len(common) + len(nonessential)) >= 0.95,
            "control_auroc": roc_auc(labels, scores) >= 0.90,
        }
        benchmark = {
            "benchmark_version": "depmap-knockout-control-benchmark-v2",
            "screened_models": len(shared_models), "hematopoietic_proxy_models": len(heme_models),
            "positive_controls": positives, "negative_controls": negatives,
            "control_auroc": roc_auc(labels, scores), "control_auprc": average_precision(labels, scores),
            "threshold_minus_0_5_sensitivity": round(sensitivity, 6), "threshold_minus_0_5_specificity": round(specificity, 6),
            "target_count": len(rows), "target_screened": target_screened,
            "risk_distribution": distribution([row["cell_line_dependency_risk"] for row in rows if row.get("screened")]),
            "quality_gates": {**gates, "passed": all(gates.values())},
            "limitations": [
                "DepMap models are predominantly cancer cell lines; hematopoietic lineages are not rheumatoid-arthritis primary immune cells.",
                "Gene effect measures proliferation or survival after knockout, not disease reversal or therapeutic benefit.",
                "The continuous cell-line dependency risk combines the pan-model median and dependency prevalence; it is a safety/development warning, not an organism-level toxicity probability.",
            ],
        }
        rows.sort(key=lambda row: (not bool(row.get("screened")), int(row.get("target_rank") or 10**9), row["symbol"]))
        return rows, benchmark

    def build(self) -> dict:
        if not self.download_manifest.exists():
            raise RuntimeError("Download manifest missing")
        target_payload = json.loads(self.target_snapshot.read_text(encoding="utf-8"))
        if target_payload.get("schema_version") != "novel-target-discovery-product-snapshot-v1":
            raise RuntimeError("Novel target snapshot schema mismatch")
        interventions, intervention_benchmark = self._virtual_interventions(target_payload)
        knockouts, knockout_benchmark = self._knockout(target_payload)
        quality_passed = intervention_benchmark["quality_gates"]["passed"] and knockout_benchmark["quality_gates"]["passed"]
        release = {
            "schema_version": "perturbation-knockout-product-snapshot-v1",
            "snapshot_id": self.snapshot_id,
            "created_at": now_iso(),
            "disease": target_payload["disease"],
            "upstream_target_snapshot": {"snapshot_id": target_payload["snapshot_id"], "sha256": digest(self.target_snapshot)},
            "source_registry_version": self.registry["registry_version"],
            "sources": self.registry["sources"],
            "quality_passed": quality_passed,
            "virtual_intervention": {
                "scientific_status": "model_based_hypothesis",
                "model_version": "calibrated-disease-module-attenuation-v2",
                "score_contract": {
                    "direction_component": 0.45, "genetic_support": 0.30,
                    "network_module_reach": 0.15, "modality_concordance": 0.10,
                    "confidence": "direction/FDR reliability 40% + calibrated genetics 30% + donor reliability 20% + modality concordance 10%",
                    "genetics_calibration": "70% absolute evidence + 30% candidate-set percentile",
                    "network_calibration": "50% global percentile + 50% candidate-set percentile",
                },
                "benchmark": intervention_benchmark,
                "results": interventions[:500],
            },
            "gene_knockout": {
                "scientific_status": "measured_cell_fitness_out_of_domain_proxy",
                "model_version": "depmap-chronos-24q4-continuous-risk-v2",
                "score_contract": {
                    "gene_effect_nonessential_reference": 0.0, "gene_effect_common_essential_reference": -1.0,
                    "dependency_threshold": -0.50, "strong_dependency_threshold": -1.0,
                    "cell_line_dependency_risk": "60% median-effect sigmoid + 25% dependency prevalence + 15% strong-dependency prevalence",
                },
                "benchmark": knockout_benchmark,
                "results": knockouts[:500],
            },
        }
        atomic_json(self.release_path, release)
        manifest = {
            "snapshot_id": self.snapshot_id, "release_path": str(self.release_path), "release_sha256": digest(self.release_path),
            "bytes": self.release_path.stat().st_size, "quality_passed": quality_passed,
            "virtual_intervention_rows": len(release["virtual_intervention"]["results"]),
            "gene_knockout_rows": len(release["gene_knockout"]["results"]), "built_at": now_iso(),
        }
        atomic_json(self.result_root / "release-manifest.json", manifest)
        if not quality_passed:
            raise RuntimeError("Perturbation/knockout quality gates failed")
        return manifest
