from novel_target_discovery_v1.benchmark import evaluate


def test_benchmark_reports_multimodal_enrichment_and_ablation():
    rows = []
    for index in range(40):
        strong = index < 10
        rows.append({
            "target_id": f"G{index:03d}",
            "novel_target_score": 1 - index / 50,
            "evidence_strength": 0.9 if strong else 0.1,
            "gwas_score": 0.8 if strong else 0.0,
            "eqtl_score": 0.7 if strong else 0.0,
            "single_cell_score": 0.6 if strong else 0.05,
            "network_score": 0.4,
            "known_drug_label": index in (0, 2),
            "novelty_class": "emerging" if strong else "exploratory",
            "prior_association": 0.0,
        })
    report = evaluate(rows, permutations=20)
    assert report["top20_multimodal_count"] == 10
    assert report["multimodal_enrichment_at_20"] > 1
    assert report["permutation_p_value"] <= 0.10
    assert "ranking remains fixed" in report["permutation_null"]
    assert set(report["ablation_top20_jaccard"]) == {"gwas_score", "eqtl_score", "single_cell_score", "network_score"}
