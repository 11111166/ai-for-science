from __future__ import annotations

import math


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return min(upper, max(lower, float(value)))


def potency_component(pchembl_median: float, pchembl_best: float) -> float:
    robust = clamp((pchembl_median - 5.0) / 3.0)
    best = clamp((pchembl_best - 5.0) / 4.0)
    return 0.70 * robust + 0.30 * best


def evidence_match_score(pchembl_median: float, pchembl_best: float, assay_count: int, document_count: int, direction_evidence: float) -> float:
    potency = potency_component(pchembl_median, pchembl_best)
    replication = 0.65 * clamp(math.log1p(assay_count) / math.log(11)) + 0.35 * clamp(math.log1p(document_count) / math.log(6))
    return round(clamp(0.60 * potency + 0.20 * replication + 0.15 * direction_evidence + 0.05), 6)


def developability_score(qed: float, lipinski_violations: int, tpsa: float, rotatable_bonds: int, solubility: float | None, hia_probability: float | None, alert_count: int) -> float:
    lipinski = clamp(1 - lipinski_violations / 3)
    veber = float(tpsa <= 140 and rotatable_bonds <= 10)
    solubility_component = 0.5 if solubility is None else clamp((solubility + 6) / 5)
    hia_component = 0.5 if hia_probability is None else clamp(hia_probability)
    alert_penalty = min(0.25, 0.07 * alert_count)
    return round(clamp(0.35 * qed + 0.20 * lipinski + 0.10 * veber + 0.20 * solubility_component + 0.15 * hia_component - alert_penalty), 6)


def safety_score(ames_probability: float | None, herg_probability: float | None, alert_count: int, black_box: bool, withdrawn: bool) -> float:
    risks = [value for value in (ames_probability, herg_probability) if value is not None]
    model_risk = sum(risks) / len(risks) if risks else 0.5
    penalty = min(0.25, 0.06 * alert_count) + (0.20 if black_box else 0) + (0.35 if withdrawn else 0)
    return round(clamp(1 - model_risk - penalty), 6)

