"""Donor-aware pseudobulk analysis for a disease single-cell H5AD."""

from __future__ import annotations

import math
from typing import Any

import anndata as ad
import numpy as np
import pandas as pd
from scipy import sparse, stats


def benjamini_hochberg(values: np.ndarray) -> np.ndarray:
    values = np.nan_to_num(np.asarray(values, dtype=float), nan=1.0, posinf=1.0, neginf=1.0)
    order = np.argsort(values)
    ranked = values[order]
    adjusted = ranked * len(ranked) / np.arange(1, len(ranked) + 1)
    adjusted = np.minimum.accumulate(adjusted[::-1])[::-1]
    result = np.empty_like(adjusted)
    result[order] = np.clip(adjusted, 0, 1)
    return result


def _as_strings(series: pd.Series) -> np.ndarray:
    return series.astype(str).to_numpy()


def analyze_h5ad(path: str, config: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    container = ad.read_h5ad(path)
    matrix_adata = container.raw.to_adata() if container.raw is not None else container
    matrix = matrix_adata.X.tocsr() if sparse.issparse(matrix_adata.X) else sparse.csr_matrix(matrix_adata.X)
    obs = container.obs.copy()

    disease_column = config["disease_column"]
    donor_column = config["donor_column"]
    cell_type_column = config["cell_type_column"]
    case_label = config["case_label"]
    control_label = config["control_label"]
    diseases = _as_strings(obs[disease_column])
    keep = np.isin(diseases, [case_label, control_label])
    primary_column = config.get("primary_column")
    if primary_column in obs:
        keep &= obs[primary_column].astype(bool).to_numpy()

    total_counts = np.asarray(matrix.sum(axis=1)).ravel()
    detected_genes = matrix.getnnz(axis=1)
    keep &= detected_genes >= int(config.get("minimum_genes", 200))
    keep &= detected_genes <= int(config.get("maximum_genes", 7500))
    keep &= total_counts >= float(config.get("minimum_counts", 500))

    symbols = matrix_adata.var.get("feature_name", pd.Series(matrix_adata.var_names, index=matrix_adata.var_names)).astype(str).to_numpy()
    feature_ids = matrix_adata.var.get("feature_id", pd.Series(matrix_adata.var_names, index=matrix_adata.var_names)).astype(str).str.replace(r"\.\d+$", "", regex=True).to_numpy()
    mitochondrial = np.char.startswith(np.char.upper(symbols.astype(str)), "MT-")
    if mitochondrial.any():
        mitochondrial_counts = np.asarray(matrix[:, mitochondrial].sum(axis=1)).ravel()
        mt_percent = np.divide(mitochondrial_counts * 100, total_counts, out=np.zeros_like(total_counts, dtype=float), where=total_counts > 0)
        keep &= mt_percent <= float(config.get("maximum_mitochondrial_percent", 20))
    else:
        mt_percent = np.zeros(matrix.shape[0], dtype=float)

    matrix = matrix[keep]
    obs = obs.iloc[np.flatnonzero(keep)].copy()
    diseases = _as_strings(obs[disease_column])
    donors = _as_strings(obs[donor_column])
    cell_types = _as_strings(obs[cell_type_column])
    min_cells = int(config["minimum_cells_per_donor_cell_type"])
    min_donors = int(config["minimum_donors_per_group"])
    best: dict[str, dict[str, Any]] = {}
    evaluated_cell_types = []

    for cell_type in sorted(set(cell_types)):
        cell_mask = cell_types == cell_type
        group_rows: list[np.ndarray] = []
        group_labels: list[str] = []
        group_donors: list[str] = []
        for donor in sorted(set(donors[cell_mask])):
            donor_mask = cell_mask & (donors == donor)
            if int(donor_mask.sum()) < min_cells:
                continue
            labels, counts = np.unique(diseases[donor_mask], return_counts=True)
            label = labels[int(np.argmax(counts))]
            if label not in (case_label, control_label):
                continue
            group_rows.append(np.asarray(matrix[donor_mask].sum(axis=0)).ravel())
            group_labels.append(label)
            group_donors.append(donor)
        case_count = group_labels.count(case_label)
        control_count = group_labels.count(control_label)
        if case_count < min_donors or control_count < min_donors:
            continue
        counts = np.vstack(group_rows)
        library = counts.sum(axis=1, keepdims=True)
        log_cpm = np.log2((counts + 0.5) / (library + 1.0) * 1_000_000)
        label_array = np.asarray(group_labels)
        cases = log_cpm[label_array == case_label]
        controls = log_cpm[label_array == control_label]
        log2fc = cases.mean(axis=0) - controls.mean(axis=0)
        with np.errstate(invalid="ignore", divide="ignore"):
            pvalues = stats.ttest_ind(cases, controls, axis=0, equal_var=False, nan_policy="omit").pvalue
        fdr = benjamini_hochberg(pvalues)
        # Candidate generation uses an explicit exploratory FDR ceiling. This
        # prevents nearly every expressed gene from receiving a non-zero score
        # merely because its nominal test statistic is finite.
        exploratory_fdr = float(config.get("exploratory_fdr", 0.20))
        effect_component = 1.0 - np.exp(-np.abs(log2fc) / 0.5)
        significance_component = np.sqrt(np.clip((exploratory_fdr - fdr) / exploratory_fdr, 0, 1))
        donor_component = min(1.0, math.sqrt(min(case_count, control_count) / 5.0))
        scores = np.sqrt(effect_component * significance_component) * donor_component
        evaluated_cell_types.append({"cell_type": cell_type, "case_donors": case_count, "control_donors": control_count, "pseudobulk_samples": len(group_rows)})
        for index in np.flatnonzero(scores > 0):
            target_id = str(feature_ids[index])
            candidate = {
                "target_id": target_id,
                "symbol": str(symbols[index]),
                "single_cell_score": round(float(scores[index]), 6),
                "single_cell_log2fc": round(float(log2fc[index]), 6),
                "single_cell_fdr": round(float(fdr[index]), 8),
                "best_cell_type": cell_type,
                "case_donors": case_count,
                "control_donors": control_count,
            }
            if target_id not in best or candidate["single_cell_score"] > best[target_id]["single_cell_score"]:
                best[target_id] = candidate

    result = pd.DataFrame(best.values())
    qc = {
        "input_cells": int(container.n_obs),
        "retained_cells": int(matrix.shape[0]),
        "input_genes": int(container.n_vars),
        "analysis_genes": int(matrix.shape[1]),
        "case_cells": int((diseases == case_label).sum()),
        "control_cells": int((diseases == control_label).sum()),
        "case_donors": len(set(donors[diseases == case_label])),
        "control_donors": len(set(donors[diseases == control_label])),
        "evaluated_cell_types": evaluated_cell_types,
        "cell_types_evaluated": len(evaluated_cell_types),
        "genes_with_score": int((result["single_cell_score"] > 0).sum()) if not result.empty else 0,
        "method": "donor_celltype_pseudobulk_logCPM_Welch_t_BH_v1",
        "cell_level_pseudoreplication_avoided": True,
    }
    return result, qc
