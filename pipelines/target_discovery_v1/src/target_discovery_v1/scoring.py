"""Transparent target-score calculations shared by ETL and unit tests."""

from __future__ import annotations

from typing import Mapping


CAUSAL_WEIGHTS = {
    "genetic_association": 0.60,
    "genetic_literature": 0.25,
    "somatic_mutation": 0.15,
}
CONTEXT_WEIGHTS = {
    "rna_expression": 0.45,
    "affected_pathway": 0.30,
    "animal_model": 0.25,
}
DISCOVERY_WEIGHTS = {
    "causal_confidence": 0.50,
    "disease_context": 0.30,
    "development_feasibility": 0.20,
}

TARGET_TO_HIT_WEIGHTS = {
    "starting_point": 0.55,
    "disease_evidence": 0.25,
    "tissue_selectivity": 0.20,
}


def clamp(value: float | int | None) -> float:
    if value is None:
        return 0.0
    return min(1.0, max(0.0, float(value)))


def weighted_axis(values: Mapping[str, float | int | None], weights: Mapping[str, float]) -> float:
    """Score a configured axis. Absent evidence is an explicit zero, not imputed."""
    return round(sum(clamp(values.get(key)) * weight for key, weight in weights.items()) / sum(weights.values()), 6)


def signed_to_unit(value: float | int | None) -> float:
    """Convert Open Targets' [-1, 1] priority features to [0, 1]."""
    if value is None:
        return 0.0
    return clamp((float(value) + 1.0) / 2.0)


def feasibility_score(values: Mapping[str, float | int | None]) -> float:
    positive = (
        0.25 * clamp(values.get("hasSmallMoleculeBinder"))
        + 0.15 * clamp(values.get("hasPocket"))
        + 0.10 * clamp(values.get("hasLigand"))
        + 0.10 * max(clamp(values.get("isInMembrane")), clamp(values.get("isSecreted")))
        + 0.10 * clamp(values.get("hasHighQualityChemicalProbes"))
        + 0.15 * signed_to_unit(values.get("tissueSpecificity"))
        + 0.15 * (1.0 - signed_to_unit(values.get("tissueDistribution")))
    )
    penalty = 0.20 * clamp(values.get("hasSafetyEvent"))
    return round(clamp(positive - penalty), 6)


def discovery_score(causal: float, context: float, feasibility: float) -> float:
    return round(
        DISCOVERY_WEIGHTS["causal_confidence"] * clamp(causal)
        + DISCOVERY_WEIGHTS["disease_context"] * clamp(context)
        + DISCOVERY_WEIGHTS["development_feasibility"] * clamp(feasibility),
        6,
    )


def target_to_hit_profile(values: Mapping[str, float | int | None], causal: float, context: float) -> dict[str, float | str | bool]:
    """Create a route-specific Target-to-Hit v2 assessment.

    A missing safety event is never treated as evidence of safety. Safety is a
    separate review flag and is deliberately excluded from the readiness score.
    """
    chemistry = (
        0.40 * clamp(values.get("hasSmallMoleculeBinder"))
        + 0.25 * clamp(values.get("hasPocket"))
        + 0.20 * clamp(values.get("hasLigand"))
        + 0.15 * clamp(values.get("hasHighQualityChemicalProbes"))
    )
    biologic = 0.55 * clamp(values.get("isInMembrane")) + 0.45 * clamp(values.get("isSecreted"))
    tissue = 0.50 * signed_to_unit(values.get("tissueSpecificity")) + 0.50 * (1.0 - signed_to_unit(values.get("tissueDistribution")))
    starting_point = max(chemistry, biologic)
    disease_evidence = 0.5 * clamp(causal) + 0.5 * clamp(context)
    readiness = (
        TARGET_TO_HIT_WEIGHTS["starting_point"] * starting_point
        + TARGET_TO_HIT_WEIGHTS["disease_evidence"] * disease_evidence
        + TARGET_TO_HIT_WEIGHTS["tissue_selectivity"] * tissue
    )
    if chemistry >= 0.45 and biologic >= 0.45:
        modality = "small_molecule_or_biologic"
    elif chemistry >= biologic and chemistry >= 0.20:
        modality = "small_molecule"
    elif biologic >= 0.20:
        modality = "biologic"
    else:
        modality = "evidence_generation"
    if readiness >= 0.60 and starting_point >= 0.50:
        readiness_level = "ready_for_hit_search"
    elif readiness >= 0.40:
        readiness_level = "prioritise_assay_and_structure"
    else:
        readiness_level = "evidence_gap"
    return {
        "chemistry_start_score": round(chemistry, 6),
        "biologic_start_score": round(biologic, 6),
        "tissue_selectivity_score": round(tissue, 6),
        "disease_evidence_score": round(disease_evidence, 6),
        "target_to_hit_score": round(clamp(readiness), 6),
        "recommended_modality": modality,
        "readiness_level": readiness_level,
        "known_safety_signal": clamp(values.get("hasSafetyEvent")) > 0,
    }
