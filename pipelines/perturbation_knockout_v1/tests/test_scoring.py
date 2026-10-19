from perturbation_knockout_v1.scoring import average_precision, cell_line_dependency_risk, ndcg_at_k, roc_auc, virtual_intervention_score


def test_virtual_intervention_uses_direction_and_not_known_drug_label():
    base = {
        "single_cell_log2fc": 1.2, "single_cell_score": 0.8,
        "gwas_score": 0.5, "eqtl_score": 0.2, "network_score": 0.4,
        "single_cell_fdr": 0.01, "case_donors": 12, "control_donors": 12,
    }
    first = virtual_intervention_score({**base, "known_drug_label": 0})
    second = virtual_intervention_score({**base, "known_drug_label": 1})
    assert first == second
    assert first["direction"] == "inhibit"
    assert first["evidence_grade"] == "high"


def test_confidence_and_calibrated_evidence_are_continuous():
    target = {
        "single_cell_log2fc": 0.8, "single_cell_score": 0.7, "single_cell_fdr": 0.08,
        "gwas_score": 0.45, "eqtl_score": 0.30, "network_score": 0.80,
        "case_donors": 8, "control_donors": 6,
    }
    result = virtual_intervention_score(target, genetic_percentile=0.65, network_candidate_percentile=0.40)
    weaker = virtual_intervention_score({**target, "single_cell_fdr": 0.19, "control_donors": 3}, genetic_percentile=0.65, network_candidate_percentile=0.40)
    assert 0 < result["confidence"] < 1
    assert 0 < result["genetic_support"] < 1
    assert result["network_module_reach"] == 0.6
    assert weaker["confidence"] < result["confidence"]


def test_cell_line_dependency_risk_preserves_effect_differences():
    low = cell_line_dependency_risk(-0.05, 0.01, 0.0)
    middle = cell_line_dependency_risk(-0.40, 0.25, 0.05)
    high = cell_line_dependency_risk(-1.05, 0.90, 0.70)
    assert 0 < low < middle < high < 1


def test_control_metrics_separate_positives():
    labels = [1, 1, 0, 0]
    scores = [1.0, 0.8, 0.2, 0.0]
    assert roc_auc(labels, scores) == 1.0
    assert average_precision(labels, scores) == 1.0


def test_ndcg_is_bounded():
    rows = [
        {"symbol": "A", "score": 0.8, "label": 1},
        {"symbol": "B", "score": 0.7, "label": 0},
    ]
    assert 0 <= ndcg_at_k(rows, "score", "label") <= 1
