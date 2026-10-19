"""Build a compact, donor-aware counterfactual cell-state snapshot.

This is deliberately not a generative perturbation model.  It validates a
case-versus-control state axis using held-out donors, then projects a bounded
fraction of the disease programme toward the healthy centroid.  The fraction
is supplied by the independently built virtual-intervention module and is
reported as an assumption, not observed drug efficacy.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anndata as ad
import numpy as np
import pandas as pd
from scipy import sparse

from .metrics import balanced_accuracy, percentile_interval, roc_auc


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


def _distribution(values: list[float]) -> dict[str, float | int | None]:
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    if not len(finite):
        return {"minimum": None, "median": None, "maximum": None, "unique_values": 0}
    return {
        "minimum": round(float(finite.min()), 6),
        "median": round(float(np.median(finite)), 6),
        "maximum": round(float(finite.max()), 6),
        "unique_values": int(np.unique(np.round(finite, 6)).size),
    }


def _loo_scores(matrix: np.ndarray, labels: np.ndarray, maximum_features: int = 64) -> tuple[list[float], list[int]]:
    """Leave-one-donor-out diagonal LDA with fold-local feature selection.

    Selecting features inside each training fold avoids leaking the held-out
    donor while preventing thousands of noisy genes from dominating a nearest
    centroid distance.
    """
    scores: list[float] = []
    predictions: list[int] = []
    for index, row in enumerate(matrix):
        train = np.arange(len(matrix)) != index
        healthy_rows = matrix[train & (labels == 0)]
        disease_rows = matrix[train & (labels == 1)]
        healthy = healthy_rows.mean(axis=0)
        disease = disease_rows.mean(axis=0)
        scale = matrix[train].std(axis=0, ddof=1)
        scale[~np.isfinite(scale) | (scale < 0.10)] = 0.10
        standardized_effect = np.abs(disease - healthy) / scale
        selected = np.argsort(standardized_effect)[-min(maximum_features, len(standardized_effect)):]
        midpoint = (healthy[selected] + disease[selected]) / 2
        direction = (disease[selected] - healthy[selected]) / scale[selected]
        score = float(np.dot((row[selected] - midpoint) / scale[selected], direction) / max(1, len(selected)))
        scores.append(score)
        predictions.append(int(score >= 0))
    return scores, predictions


class Pipeline:
    def __init__(self, root: Path, h5ad_path: Path, target_snapshot: Path, intervention_snapshot: Path, model_config: Path | None = None, disease_config: Path | None = None):
        self.root = root.resolve()
        self.h5ad_path = h5ad_path.resolve()
        self.target_snapshot = target_snapshot.resolve()
        self.intervention_snapshot = intervention_snapshot.resolve()
        self.model_config = (model_config or CONFIG_ROOT / "model.json").resolve()
        self.config = json.loads(self.model_config.read_text(encoding="utf-8"))
        if disease_config:
            disease_payload = json.loads(disease_config.resolve().read_text(encoding="utf-8"))
            self.config["snapshot_id"] = disease_payload["virtual_cell_snapshot_id"]
            self.config["disease"] = {
                "id": disease_payload["id"], "name": disease_payload["name"], "name_zh": disease_payload["name_zh"],
                "aliases": [disease_payload["name"], disease_payload["name_zh"]],
            }
            self.config["single_cell"].update(disease_payload["single_cell"])
            cell_source = next(source for source in disease_payload.get("source_overrides", []) if source["kind"] == "file")
            self.config["single_cell"].update({"dataset_version": cell_source["version"], "citation": cell_source.get("citation", cell_source["name"])})
        self.snapshot_id = self.config["snapshot_id"]
        self.result_root = self.root / "results" / self.snapshot_id
        self.manifest_root = self.root / "manifests"
        self.release_path = self.result_root / "virtual-cell-v1.json"

    def ensure_layout(self) -> None:
        self.result_root.mkdir(parents=True, exist_ok=True)
        self.manifest_root.mkdir(parents=True, exist_ok=True)

    def doctor(self) -> dict[str, Any]:
        self.ensure_layout()
        inputs = [self.h5ad_path, self.target_snapshot, self.intervention_snapshot]
        free = shutil.disk_usage(self.root).free
        report = {
            "status": "passed" if all(path.is_file() for path in inputs) and free >= 2 * 1024**3 else "failed",
            "inputs": [{"path": str(path), "present": path.is_file(), "sha256": sha256_file(path) if path.is_file() else None} for path in inputs],
            "free_bytes": free,
            "checked_at": now_iso(),
        }
        atomic_json(self.manifest_root / f"{self.snapshot_id}.doctor.json", report)
        if report["status"] != "passed":
            raise RuntimeError("Virtual Cell v1 requires all three inputs and at least 2 GiB free space")
        return report

    def _pseudobulk(self, target_symbols: set[str]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
        cfg = self.config["single_cell"]
        container = ad.read_h5ad(self.h5ad_path)
        matrix_adata = container.raw.to_adata() if container.raw is not None else container
        matrix = matrix_adata.X.tocsr() if sparse.issparse(matrix_adata.X) else sparse.csr_matrix(matrix_adata.X)
        obs = container.obs.copy()
        disease = obs[cfg["disease_column"]].astype(str).to_numpy()
        keep = np.isin(disease, [cfg["case_label"], cfg["control_label"]])
        if cfg.get("primary_column") in obs:
            keep &= obs[cfg["primary_column"]].astype(bool).to_numpy()
        total = np.asarray(matrix.sum(axis=1)).ravel()
        detected = matrix.getnnz(axis=1)
        keep &= detected >= int(cfg["minimum_genes"])
        keep &= detected <= int(cfg["maximum_genes"])
        keep &= total >= float(cfg["minimum_counts"])
        symbols = matrix_adata.var.get("feature_name", pd.Series(matrix_adata.var_names, index=matrix_adata.var_names)).astype(str).to_numpy()
        mitochondria = np.char.startswith(np.char.upper(symbols.astype(str)), "MT-")
        if mitochondria.any():
            mt = np.asarray(matrix[:, mitochondria].sum(axis=1)).ravel()
            mt_percent = np.divide(mt * 100, total, out=np.zeros_like(total, dtype=float), where=total > 0)
            keep &= mt_percent <= float(cfg["maximum_mitochondrial_percent"])
        matrix = matrix[keep]
        obs = obs.iloc[np.flatnonzero(keep)].copy()
        disease = obs[cfg["disease_column"]].astype(str).to_numpy()
        donor = obs[cfg["donor_column"]].astype(str).to_numpy()
        cell_type = obs[cfg["cell_type_column"]].astype(str).to_numpy()
        outputs: dict[str, dict[str, Any]] = {}
        min_cells = int(cfg["minimum_cells_per_donor_cell_type"])
        min_donors = int(cfg["minimum_donors_per_group"])
        rng = np.random.default_rng(int(cfg["random_seed"]))

        for current in sorted(set(cell_type)):
            mask = cell_type == current
            rows: list[np.ndarray] = []
            labels: list[int] = []
            donors: list[str] = []
            cell_counts: list[int] = []
            for current_donor in sorted(set(donor[mask])):
                donor_mask = mask & (donor == current_donor)
                count = int(donor_mask.sum())
                if count < min_cells:
                    continue
                values, counts = np.unique(disease[donor_mask], return_counts=True)
                label = str(values[int(np.argmax(counts))])
                if label not in (cfg["case_label"], cfg["control_label"]):
                    continue
                rows.append(np.asarray(matrix[donor_mask].sum(axis=0)).ravel())
                labels.append(int(label == cfg["case_label"]))
                donors.append(current_donor)
                cell_counts.append(count)
            label_array = np.asarray(labels, dtype=int)
            if int(label_array.sum()) < min_donors or int((label_array == 0).sum()) < min_donors:
                continue
            counts = np.vstack(rows)
            libraries = counts.sum(axis=1, keepdims=True)
            log_cpm = np.log2((counts + 0.5) / (libraries + 1.0) * 1_000_000)
            variances = log_cpm.var(axis=0)
            expressed = (counts > 0).mean(axis=0) >= 0.25
            eligible = np.flatnonzero(expressed & np.isfinite(variances))
            variable_genes = eligible[np.argsort(variances[eligible])[-int(cfg["maximum_state_genes"]):]]
            target_genes = np.asarray([i for i in eligible if str(symbols[i]).upper() in target_symbols], dtype=int)
            keep_genes = np.unique(np.concatenate([variable_genes, target_genes]))
            state = log_cpm[:, keep_genes]
            state_symbols = symbols[keep_genes].astype(str)
            case = state[label_array == 1]
            control = state[label_array == 0]
            disease_vector = case.mean(axis=0) - control.mean(axis=0)
            scores, predictions = _loo_scores(state, label_array)
            auc = roc_auc(label_array, scores)
            bacc = balanced_accuracy(label_array, predictions)
            bootstrap_norms = []
            case_indices = np.flatnonzero(label_array == 1)
            control_indices = np.flatnonzero(label_array == 0)
            for _ in range(int(cfg["bootstrap_iterations"])):
                boot_case = state[rng.choice(case_indices, len(case_indices), replace=True)].mean(axis=0)
                boot_control = state[rng.choice(control_indices, len(control_indices), replace=True)].mean(axis=0)
                bootstrap_norms.append(float(np.sqrt(np.mean((boot_case - boot_control) ** 2))))
            axis_strength = float(np.sqrt(np.mean(disease_vector**2)))
            low, high = percentile_interval(bootstrap_norms)
            order = np.argsort(np.abs(disease_vector))[::-1]
            top_genes = [{"symbol": str(state_symbols[i]), "disease_log2fc": round(float(disease_vector[i]), 6)} for i in order[:30]]
            symbol_index = {str(symbol).upper(): i for i, symbol in enumerate(state_symbols)}
            outputs[current] = {
                "cell_type": current,
                "donors": donors,
                "cell_counts": cell_counts,
                "labels": label_array,
                "state": state,
                "symbols": state_symbols,
                "symbol_index": symbol_index,
                "disease_vector": disease_vector,
                "axis_strength": axis_strength,
                "axis_strength_ci95": [low, high],
                "held_out_donor_auroc": auc,
                "held_out_donor_balanced_accuracy": bacc,
                "case_donors": int(label_array.sum()),
                "control_donors": int((label_array == 0).sum()),
                "pseudobulk_samples": len(rows),
                "cells": int(sum(cell_counts)),
                "top_genes": top_genes,
            }
        qc = {
            "input_cells": int(container.n_obs),
            "retained_cells": int(matrix.shape[0]),
            "input_genes": int(container.n_vars),
            "cell_types_evaluated": len(outputs),
            "case_donors": len(set(donor[disease == cfg["case_label"]])),
            "control_donors": len(set(donor[disease == cfg["control_label"]])),
            "statistical_unit": "donor_by_cell_type_pseudobulk",
            "normalization": "library_size_log2_CPM",
            "cell_level_pseudoreplication_avoided": True,
        }
        return outputs, qc

    def build(self) -> dict[str, Any]:
        self.ensure_layout()
        target_payload = json.loads(self.target_snapshot.read_text(encoding="utf-8"))
        intervention_payload = json.loads(self.intervention_snapshot.read_text(encoding="utf-8"))
        if target_payload.get("schema_version") != "novel-target-discovery-product-snapshot-v1":
            raise RuntimeError("Novel target snapshot schema mismatch")
        if intervention_payload.get("schema_version") != "perturbation-knockout-product-snapshot-v1":
            raise RuntimeError("Virtual intervention snapshot schema mismatch")
        interventions = {str(row.get("symbol") or "").upper(): row for row in intervention_payload["virtual_intervention"]["results"]}
        targets = {str(row.get("symbol") or "").upper(): row for row in target_payload["targets"]}
        all_contexts, qc = self._pseudobulk(set(interventions))
        single_cell_cfg = self.config["single_cell"]
        minimum_auc = float(single_cell_cfg["minimum_context_auroc"])
        minimum_bacc = float(single_cell_cfg["minimum_context_balanced_accuracy"])
        contexts = {
            name: context
            for name, context in all_contexts.items()
            if float(context["held_out_donor_auroc"]) >= minimum_auc
            and float(context["held_out_donor_balanced_accuracy"]) >= minimum_bacc
        }
        rejected_contexts = [
            {
                "cell_type": name,
                "held_out_donor_auroc": round(float(context["held_out_donor_auroc"]), 6),
                "held_out_donor_balanced_accuracy": round(float(context["held_out_donor_balanced_accuracy"]), 6),
                "reason": "held_out_donor_discrimination_below_threshold",
            }
            for name, context in all_contexts.items()
            if name not in contexts
        ]
        qc["cell_types_evaluated"] = len(all_contexts)
        qc["cell_types_validated"] = len(contexts)
        qc["cell_types_rejected"] = len(rejected_contexts)
        results = []
        for symbol, intervention in interventions.items():
            target = targets.get(symbol, {})
            target_contexts = []
            for name, context in contexts.items():
                index = context["symbol_index"].get(symbol)
                if index is None:
                    continue
                target_log2fc = float(context["disease_vector"][index])
                direction = str(intervention.get("direction") or "unknown")
                direction_consistent = (direction == "inhibit" and target_log2fc > 0) or (direction == "activate" and target_log2fc < 0)
                signal = 1.0 - math.exp(-abs(target_log2fc) / 0.5)
                state_quality = 0.5 * max(0.0, min(1.0, float(context["held_out_donor_auroc"]))) + 0.5 * max(0.0, min(1.0, float(context["held_out_donor_balanced_accuracy"])))
                evidence = float(intervention.get("virtual_intervention_score") or 0)
                confidence = float(intervention.get("confidence") or 0)
                reversal = min(0.65, evidence * (0.35 + 0.35 * signal + 0.30 * state_quality) * (0.85 if direction_consistent else 0.45))
                uncertainty = max(0.04, 0.22 * (1 - confidence) + 0.10 / math.sqrt(max(1, min(context["case_donors"], context["control_donors"]))))
                low = max(0.0, reversal - 1.96 * uncertainty)
                high = min(0.75, reversal + 1.96 * uncertainty)
                restored = []
                for gene in context["top_genes"][:12]:
                    change = -reversal * float(gene["disease_log2fc"])
                    restored.append({**gene, "projected_change": round(change, 6), "projected_residual": round(float(gene["disease_log2fc"]) + change, 6)})
                target_contexts.append({
                    "cell_type": name,
                    "case_donors": context["case_donors"],
                    "control_donors": context["control_donors"],
                    "cells": context["cells"],
                    "target_disease_log2fc": round(target_log2fc, 6),
                    "direction_consistent": direction_consistent,
                    "state_axis_auroc": round(float(context["held_out_donor_auroc"]), 6),
                    "state_axis_balanced_accuracy": round(float(context["held_out_donor_balanced_accuracy"]), 6),
                    "disease_burden_before": 1.0,
                    "projected_disease_burden_after": round(1.0 - reversal, 6),
                    "projected_reversal": round(reversal, 6),
                    "projected_reversal_ci95": [round(low, 6), round(high, 6)],
                    "top_projected_restored_genes": restored,
                })
            if not target_contexts:
                continue
            target_contexts.sort(key=lambda item: (-item["projected_reversal"], item["cell_type"]))
            best = target_contexts[0]
            results.append({
                "target_id": target.get("target_id") or intervention.get("target_id"),
                "symbol": symbol,
                "approved_name": target.get("approved_name") or intervention.get("approved_name"),
                "target_rank": target.get("rank") or intervention.get("target_rank"),
                "direction": intervention.get("direction"),
                "cell_context_count": len(target_contexts),
                "best_cell_type": best["cell_type"],
                "projected_reversal": best["projected_reversal"],
                "projected_reversal_ci95": best["projected_reversal_ci95"],
                "state_axis_auroc": best["state_axis_auroc"],
                "confidence": round(float(intervention.get("confidence") or 0) * (0.5 + 0.5 * best["state_axis_auroc"]), 6),
                "contexts": target_contexts,
                "upstream_intervention_score": intervention.get("virtual_intervention_score"),
                "assumption": "bounded_disease_program_attenuation_scenario",
            })
        results.sort(key=lambda row: (-row["projected_reversal"], int(row.get("target_rank") or 10**9), row["symbol"]))
        for rank, row in enumerate(results, 1):
            row["rank"] = rank
        all_aucs = [float(context["held_out_donor_auroc"]) for context in all_contexts.values()]
        all_baccs = [float(context["held_out_donor_balanced_accuracy"]) for context in all_contexts.values()]
        aucs = [float(context["held_out_donor_auroc"]) for context in contexts.values()]
        baccs = [float(context["held_out_donor_balanced_accuracy"]) for context in contexts.values()]
        target_coverage = len(results) / max(1, len(interventions))
        gates = {
            "validated_cell_type_count": len(contexts) >= int(single_cell_cfg["minimum_validated_contexts"]),
            "held_out_donor_auroc": bool(aucs) and min(aucs) >= minimum_auc,
            "held_out_balanced_accuracy": bool(baccs) and min(baccs) >= minimum_bacc,
            "target_context_coverage": target_coverage >= float(single_cell_cfg["minimum_target_context_coverage"]),
            "donor_level_statistical_unit": qc["statistical_unit"] == "donor_by_cell_type_pseudobulk",
        }
        benchmark = {
            "version": "virtual-cell-state-axis-benchmark-v1",
            "scope": "held-out donor evaluation validates disease-state separation, not intervention efficacy",
            "cell_type_count": len(contexts),
            "evaluated_cell_type_count": len(all_contexts),
            "rejected_cell_type_count": len(rejected_contexts),
            "context_acceptance_thresholds": {
                "held_out_donor_auroc": minimum_auc,
                "held_out_donor_balanced_accuracy": minimum_bacc,
                "target_context_coverage": float(single_cell_cfg["minimum_target_context_coverage"]),
            },
            "held_out_donor_auroc": _distribution(aucs),
            "held_out_donor_balanced_accuracy": _distribution(baccs),
            "all_evaluated_held_out_donor_auroc": _distribution(all_aucs),
            "all_evaluated_held_out_donor_balanced_accuracy": _distribution(all_baccs),
            "target_count": len(interventions),
            "target_context_coverage": round(target_coverage, 6),
            "projected_reversal_distribution": _distribution([row["projected_reversal"] for row in results]),
            "quality_gates": {**gates, "passed": all(gates.values())},
        }
        release = {
            "schema_version": "virtual-cell-product-snapshot-v1",
            "snapshot_id": self.snapshot_id,
            "created_at": now_iso(),
            "quality_passed": bool(benchmark["quality_gates"]["passed"]),
            "scientific_status": "counterfactual_projection_not_mechanistic_perturbation",
            "model_version": self.config["model_version"],
            "disease": self.config["disease"],
            "source_registry": {
                "cellxgene": self.config["single_cell"],
                "upstream_target_snapshot": {"snapshot_id": target_payload.get("snapshot_id"), "sha256": sha256_file(self.target_snapshot)},
                "upstream_intervention_snapshot": {"snapshot_id": intervention_payload.get("snapshot_id"), "sha256": sha256_file(self.intervention_snapshot)},
            },
            "qc": qc,
            "benchmark": benchmark,
            "limitations": [
                "The model projects a bounded fraction of an observed disease axis; it does not generate a mechanistic post-perturbation transcriptome.",
                "Held-out donor metrics validate case-control state separation only and must not be interpreted as intervention efficacy validation.",
                "Targets absent from the selected state-gene panel are not scored; confidence intervals quantify scenario uncertainty, not clinical uncertainty.",
                "Only cell types meeting both prespecified held-out-donor thresholds are published; rejected contexts remain in QC and are not used for target ranking.",
            ],
            "cell_type_models": [{key: value for key, value in context.items() if key not in {"donors", "cell_counts", "labels", "state", "symbols", "symbol_index", "disease_vector"}} for context in contexts.values()],
            "rejected_cell_type_models": rejected_contexts,
            "results": results,
        }
        atomic_json(self.release_path, release)
        manifest = {
            "snapshot_id": self.snapshot_id,
            "release_path": str(self.release_path),
            "release_sha256": sha256_file(self.release_path),
            "bytes": self.release_path.stat().st_size,
            "quality_passed": release["quality_passed"],
            "cell_type_models": len(contexts),
            "result_count": len(results),
            "built_at": now_iso(),
        }
        atomic_json(self.result_root / "release-manifest.json", manifest)
        if not release["quality_passed"]:
            raise RuntimeError("Virtual Cell v1 quality gates failed")
        return manifest
