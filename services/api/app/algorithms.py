"""Small, deterministic scientific baselines used by traceable adapters.

These functions implement transparent calculations. They do not turn the
demonstration fixtures into validated biological or clinical predictions.
"""

from math import isfinite


DEFAULT_RANKING_WEIGHTS = {
    "compound_match": 0.35,
    "efficacy": 0.30,
    "safety": 0.20,
    "developability": 0.15,
}


def normalize_weights(weights: dict[str, float] | None = None) -> dict[str, float]:
    """Validate and normalize multi-criteria decision weights to sum to one."""
    values = dict(DEFAULT_RANKING_WEIGHTS if not weights else weights)
    unknown = sorted(set(values) - set(DEFAULT_RANKING_WEIGHTS))
    if unknown:
        raise ValueError(f"INVALID_RANKING_WEIGHT_KEYS:{','.join(unknown)}")
    if set(values) != set(DEFAULT_RANKING_WEIGHTS):
        missing = sorted(set(DEFAULT_RANKING_WEIGHTS) - set(values))
        raise ValueError(f"MISSING_RANKING_WEIGHT_KEYS:{','.join(missing)}")
    if any(not isfinite(float(value)) or float(value) < 0 for value in values.values()):
        raise ValueError("INVALID_RANKING_WEIGHT_VALUE")
    total = sum(float(value) for value in values.values())
    if total <= 0:
        raise ValueError("RANKING_WEIGHTS_SUM_TO_ZERO")
    return {key: float(value) / total for key, value in values.items()}


def weighted_score(values: dict[str, float | None], weights: dict[str, float]) -> float:
    """Calculate a bounded, transparent weighted score."""
    return round(
        max(0.0, min(1.0, sum((values.get(key) or 0.0) * weight for key, weight in weights.items()))),
        4,
    )


def enrichment_score(query_genes: list[str], ranked_genes: list[str]) -> tuple[float, float]:
    """Unweighted running-sum enrichment score similar to GSEA/CMap.

    Returns the signed enrichment score and query coverage. Positive values
    mean query genes accumulate near the top of the ranked perturbation list.
    """
    ranked = [gene.upper() for gene in ranked_genes]
    query = {gene.upper() for gene in query_genes}
    hits = query.intersection(ranked)
    if not ranked or not hits:
        return 0.0, 0.0
    hit_step = 1.0 / len(hits)
    misses = len(ranked) - len(hits)
    miss_step = 1.0 / misses if misses else 0.0
    running = 0.0
    maximum = float("-inf")
    minimum = float("inf")
    for gene in ranked:
        running += hit_step if gene in hits else -miss_step
        maximum = max(maximum, running)
        minimum = min(minimum, running)
    score = maximum if abs(maximum) >= abs(minimum) else minimum
    return round(max(-1.0, min(1.0, score)), 6), round(len(hits) / len(query), 6)


def signature_reversal(
    disease_up: list[str],
    disease_down: list[str],
    perturbation_ranked: list[str],
) -> dict[str, float | str]:
    """Score whether a perturbation reverses a disease expression signature.

    The ranked list is ordered from perturbation-upregulated to
    perturbation-downregulated genes. A desirable reversal therefore places
    disease-down genes near the top and disease-up genes near the bottom.
    """
    up_es, up_coverage = enrichment_score(disease_up, perturbation_ranked)
    down_es, down_coverage = enrichment_score(disease_down, perturbation_ranked)
    raw = max(-1.0, min(1.0, (down_es - up_es) / 2.0))
    coverage = (up_coverage + down_coverage) / 2.0
    normalized = (raw + 1.0) / 2.0
    confidence = coverage * (0.5 + 0.5 * abs(raw))
    return {
        "algorithm": "rank_based_signature_reversal_v1",
        "raw_connectivity": round(raw, 4),
        "reversal_score": round(normalized, 4),
        "coverage": round(coverage, 4),
        "confidence": round(confidence, 4),
        "disease_up_es": round(up_es, 4),
        "disease_down_es": round(down_es, 4),
    }
