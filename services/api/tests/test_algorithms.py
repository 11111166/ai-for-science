import pytest

from app.algorithms import enrichment_score, normalize_weights, signature_reversal, weighted_score


def test_signature_reversal_rewards_opposite_expression_direction():
    disease_up = ["A", "B", "C"]
    disease_down = ["X", "Y", "Z"]
    reversed_signature = ["X", "Y", "N1", "Z", "N2", "A", "N3", "B", "C"]
    aligned_signature = ["A", "B", "N1", "C", "N2", "X", "N3", "Y", "Z"]
    reversed_result = signature_reversal(disease_up, disease_down, reversed_signature)
    aligned_result = signature_reversal(disease_up, disease_down, aligned_signature)
    assert reversed_result["reversal_score"] > 0.5
    assert aligned_result["reversal_score"] < 0.5
    assert reversed_result["coverage"] == 1.0


def test_enrichment_reports_missing_gene_coverage():
    value, coverage = enrichment_score(["A", "B"], ["A", "X", "Y"])
    assert value > 0
    assert coverage == 0.5


def test_ranking_weights_are_strict_and_normalized():
    weights = normalize_weights({"compound_match": 7, "efficacy": 6, "safety": 4, "developability": 3})
    assert sum(weights.values()) == pytest.approx(1.0)
    assert weighted_score({"compound_match": 1, "efficacy": 1, "safety": 1, "developability": 1}, weights) == 1.0
    with pytest.raises(ValueError, match="INVALID_RANKING_WEIGHT_KEYS"):
        normalize_weights({"target_evidence": 1, "efficacy": 1, "safety": 1, "developability": 1})
