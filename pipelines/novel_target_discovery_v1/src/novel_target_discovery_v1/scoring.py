"""Transparent, leakage-aware scoring for Novel Target Discovery v1."""

from __future__ import annotations

from typing import Mapping


EVIDENCE_WEIGHTS = {"gwas": 0.35, "eqtl": 0.25, "single_cell": 0.25, "network": 0.15}
NOVELTY_WEIGHTS = {"no_disease_drug": 0.35, "no_clinical_precedence": 0.25, "low_literature": 0.20, "low_prior_association": 0.20}
FINAL_WEIGHTS = {"evidence_strength": 0.55, "novelty": 0.25, "feasibility": 0.20}


def clamp(value: float | int | None) -> float:
    return min(1.0, max(0.0, float(value or 0.0)))


def weighted_score(values: Mapping[str, float | int | None], weights: Mapping[str, float]) -> float:
    return round(sum(clamp(values.get(key)) * weight for key, weight in weights.items()) / sum(weights.values()), 6)


def evidence_strength(gwas: float, eqtl: float, single_cell: float, network: float) -> float:
    return weighted_score({"gwas": gwas, "eqtl": eqtl, "single_cell": single_cell, "network": network}, EVIDENCE_WEIGHTS)


def colocalization_score(h4: float | int | None, clpp: float | int | None) -> float:
    """Combine two colocalisation quantities without a hard 0.02 ceiling.

    H4 is already probability-like. CLPP is transformed by a saturating curve
    whose half-response is 0.02, so larger values remain distinguishable while
    the result stays bounded. The components remain available in the snapshot.
    """
    bounded_h4 = clamp(h4)
    bounded_clpp = max(0.0, float(clpp or 0.0))
    transformed_clpp = bounded_clpp / (bounded_clpp + 0.02) if bounded_clpp else 0.0
    return round(0.60 * bounded_h4 + 0.40 * transformed_clpp, 6)


def novelty_score(known_drug: float, clinical_stage: float, literature: float, prior_association: float) -> float:
    return weighted_score(
        {
            "no_disease_drug": 1.0 - clamp(known_drug),
            "no_clinical_precedence": 1.0 - clamp(clinical_stage),
            "low_literature": 1.0 - clamp(literature),
            "low_prior_association": 1.0 - clamp(prior_association),
        },
        NOVELTY_WEIGHTS,
    )


def final_score(evidence: float, novelty: float, feasibility: float) -> float:
    return weighted_score({"evidence_strength": evidence, "novelty": novelty, "feasibility": feasibility}, FINAL_WEIGHTS)


def novelty_class(evidence: float, novelty: float) -> str:
    if evidence >= 0.35 and novelty >= 0.60:
        return "emerging"
    if evidence >= 0.35:
        return "established"
    if novelty >= 0.65:
        return "exploratory"
    return "supported"


def candidate_gate(gwas: float, eqtl: float, single_cell: float, network: float) -> bool:
    direct = max(clamp(gwas), clamp(eqtl), clamp(single_cell))
    return direct >= 0.10 or (direct >= 0.02 and clamp(network) >= 0.10)
