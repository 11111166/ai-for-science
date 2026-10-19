from target_discovery_v1.benchmark import evaluate


def test_benchmark_rewards_positive_at_top_and_reports_ablation_axes():
    rows = [
        {"disease_id": "D1", "disease_name": "disease", "therapeutic_area": "test", "target_id": "T1", "biotype": "protein_coding", "known_drug_label": 1, "discovery_score": 0.9, "causal_confidence": 0.8, "disease_context": 0.7, "development_feasibility": 0.6},
        {"disease_id": "D1", "disease_name": "disease", "therapeutic_area": "test", "target_id": "T2", "biotype": "protein_coding", "known_drug_label": 0, "discovery_score": 0.1, "causal_confidence": 0.2, "disease_context": 0.1, "development_feasibility": 0.2},
    ]
    result = evaluate(rows)
    assert result["status"] == "computed"
    assert result["macro"]["discovery_score"]["average_precision"] == 1.0
    assert result["macro"]["discovery_score"]["matched_control_win_rate"] == 1.0
    assert set(result["macro"]) == {"discovery_score", "causal_confidence", "disease_context", "development_feasibility"}
