"""Small deterministic scoring functions with explicit biological limits."""

from __future__ import annotations

import math
from typing import Iterable

import numpy as np


def clamp(value: float | int | None) -> float:
    try:
        number = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return min(1.0, max(0.0, number)) if math.isfinite(number) else 0.0


def virtual_intervention_score(
    target: dict,
    genetic_percentile: float | None = None,
    network_candidate_percentile: float | None = None,
) -> dict:
    """Prioritize a direction that attenuates a measured disease-state target.

    This is deliberately a low-complexity baseline. The single-cell effect
    supplies direction; genetics anchors disease relevance; an unsigned PPI
    diffusion score supplies module reach. It does not predict a full
    post-perturbation transcriptome.
    """
    log2fc = float(target.get("single_cell_log2fc") or 0.0)
    expression = clamp(target.get("single_cell_score"))
    gwas = clamp(target.get("gwas_score"))
    eqtl = clamp(target.get("eqtl_score"))
    genetics_raw = 0.65 * max(gwas, eqtl) + 0.35 * min(gwas, eqtl)
    genetics_relative = clamp(genetic_percentile) if genetic_percentile is not None else genetics_raw
    genetics = 0.70 * genetics_raw + 0.30 * genetics_relative
    network_global = clamp(target.get("network_score"))
    network_relative = clamp(network_candidate_percentile) if network_candidate_percentile is not None else network_global
    network = 0.50 * network_global + 0.50 * network_relative
    modality_count = sum(value >= 0.10 for value in (gwas, eqtl, expression))
    direction = "inhibit" if log2fc > 0 else "activate" if log2fc < 0 else "unknown"
    effect_magnitude = clamp(abs(log2fc) / 1.5)
    direction_component = math.sqrt(expression * effect_magnitude) if direction != "unknown" else 0.0
    concordance = modality_count / 3.0
    score = 0.45 * direction_component + 0.30 * genetics + 0.15 * network + 0.10 * concordance
    fdr_value = target.get("single_cell_fdr")
    fdr = min(1.0, max(0.0, float(fdr_value if fdr_value is not None else 1.0)))
    fdr_reliability = max(0.0, 1.0 - fdr / 0.20)
    case_donors = max(0, int(target.get("case_donors") or 0))
    control_donors = max(0, int(target.get("control_donors") or 0))
    donor_reliability = min(1.0, math.sqrt(min(case_donors, control_donors) / 10.0))
    direction_reliability = math.sqrt(direction_component * fdr_reliability) if direction != "unknown" else 0.0
    confidence = 0.40 * direction_reliability + 0.30 * genetics + 0.20 * donor_reliability + 0.10 * concordance
    if direction_component >= 0.20 and genetics >= 0.20 and modality_count >= 2 and fdr <= 0.10:
        evidence_grade = "high"
    elif direction_component > 0 and fdr <= 0.20 and (genetics > 0 or network > 0):
        evidence_grade = "moderate"
    else:
        evidence_grade = "low"
    return {
        "direction": direction,
        "virtual_intervention_score": round(clamp(score), 6),
        "confidence": round(clamp(confidence), 6),
        "direction_component": round(direction_component, 6),
        "genetic_support": round(genetics, 6),
        "genetic_support_raw": round(genetics_raw, 6),
        "genetic_support_percentile": round(genetics_relative, 6),
        "network_module_reach": round(network, 6),
        "network_global_percentile": round(network_global, 6),
        "network_candidate_percentile": round(network_relative, 6),
        "single_cell_fdr_reliability": round(fdr_reliability, 6),
        "donor_reliability": round(donor_reliability, 6),
        "direct_modality_count": modality_count,
        "evidence_grade": evidence_grade,
    }


def cell_line_dependency_risk(
    median_gene_effect: float,
    dependency_fraction: float,
    strong_dependency_fraction: float,
) -> float:
    """Continuous warning score from measured DepMap fitness summaries.

    The curve is centred near a modest pan-cell-line effect (-0.35), then
    augmented by the prevalence of effects below -0.5 and -1.0. It is not an
    organism-level toxicity probability.
    """
    logistic_input = max(-30.0, min(30.0, (-float(median_gene_effect) - 0.35) / 0.15))
    median_component = 1.0 / (1.0 + math.exp(-logistic_input))
    value = (
        0.60 * median_component
        + 0.25 * math.sqrt(clamp(dependency_fraction))
        + 0.15 * math.sqrt(clamp(strong_dependency_fraction))
    )
    return round(clamp(value), 6)


def roc_auc(labels: Iterable[int], scores: Iterable[float]) -> float:
    labels_array = np.asarray(list(labels), dtype=int)
    scores_array = np.asarray(list(scores), dtype=float)
    positives = int(labels_array.sum())
    negatives = len(labels_array) - positives
    if not positives or not negatives:
        return 0.0
    order = np.argsort(scores_array, kind="mergesort")
    ranks = np.empty(len(scores_array), dtype=float)
    sorted_scores = scores_array[order]
    start = 0
    while start < len(sorted_scores):
        end = start + 1
        while end < len(sorted_scores) and sorted_scores[end] == sorted_scores[start]:
            end += 1
        ranks[order[start:end]] = (start + 1 + end) / 2.0
        start = end
    value = (ranks[labels_array == 1].sum() - positives * (positives + 1) / 2) / (positives * negatives)
    return round(float(value), 6)


def average_precision(labels: Iterable[int], scores: Iterable[float]) -> float:
    pairs = sorted(zip(scores, labels), key=lambda item: -float(item[0]))
    positives = sum(int(label) for _, label in pairs)
    if not positives:
        return 0.0
    hits = 0
    precision_sum = 0.0
    for rank, (_, label) in enumerate(pairs, 1):
        if int(label):
            hits += 1
            precision_sum += hits / rank
    return round(precision_sum / positives, 6)


def ndcg_at_k(rows: list[dict], score_key: str, label_key: str, k: int = 20) -> float:
    ordered = sorted(rows, key=lambda row: (-float(row.get(score_key) or 0), str(row.get("target_id") or row.get("symbol"))))[:k]
    ideal = sorted((int(bool(row.get(label_key))) for row in rows), reverse=True)[:k]
    dcg = sum(int(bool(row.get(label_key))) / math.log2(index + 2) for index, row in enumerate(ordered))
    idcg = sum(label / math.log2(index + 2) for index, label in enumerate(ideal))
    return round(dcg / idcg, 6) if idcg else 0.0
