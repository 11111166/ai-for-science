from types import SimpleNamespace

from app.intervention_baseline import MODEL_VERSION, build_intervention_baseline


def test_baseline_is_differentiated_and_returns_labelled_product_hypothesis():
    stronger = SimpleNamespace(
        symbol="APOE", causal_confidence=0.82, disease_context=0.71,
        evidence_coverage=0.67, source_count=5, score=0.76,
        direction="not_available", source_version="target-snapshot-test-v1",
    )
    weaker = SimpleNamespace(
        symbol="GENE2", causal_confidence=0.18, disease_context=0.22,
        evidence_coverage=0.17, source_count=1, score=0.24,
        direction="not_available", source_version="target-snapshot-test-v1",
    )

    high = build_intervention_baseline(stronger, "Alzheimer disease")
    low = build_intervention_baseline(weaker, "Alzheimer disease")

    assert high["analysis_tier"] == "evidence_baseline"
    assert high["metric_type"] == "intervention_validation_priority"
    assert high["model_version"] == MODEL_VERSION
    assert high["direction"] == "inhibit"
    assert high["direction_status"] == "model_hypothesis"
    assert high["direction_label"] == "模型推测：优先验证抑制"
    assert high["direction_confidence"] < 0.75
    assert high["direction_support"]["inhibit"] > high["direction_support"]["activate"]
    assert high["direction_evidence"]
    assert high["recommended_experiments"]
    assert high["virtual_intervention_score"] > low["virtual_intervention_score"]
    assert high["confidence"] > low["confidence"]
    assert high["confidence"] <= 0.74
    assert "不是疾病状态逆转实测" in high["boundary"]


def test_baseline_preserves_supported_upstream_direction():
    target = SimpleNamespace(
        symbol="GENE1", causal_confidence=0.6, disease_context=0.5,
        evidence_coverage=0.5, source_count=3, score=0.6,
        direction="inhibit", source_version="novel-target-test-v1",
    )
    result = build_intervention_baseline(target, "test disease")
    assert result["direction"] == "inhibit"
    assert result["direction_status"] == "upstream_supported"


def test_baseline_uses_signed_human_evidence_before_product_prior():
    target = SimpleNamespace(
        symbol="GENE3", name="test target", causal_confidence=0.6, disease_context=0.5,
        development_feasibility=0.6, evidence_coverage=0.5,
        source_count=3, score=0.6, direction="not_available",
        source_version="target-snapshot-test-v1", evidence_breakdown={},
    )
    evidence = [
        SimpleNamespace(direction_on_target="LoF", direction_on_trait="risk", score=0.9, datasource="eva"),
        SimpleNamespace(direction_on_target="LoF", direction_on_trait="risk", score=0.8, datasource="gene_burden"),
    ]
    result = build_intervention_baseline(target, "test disease", evidence_records=evidence)
    assert result["direction"] == "activate"
    assert result["direction_status"] == "directional_evidence_supported"
    assert result["direction_support"]["activate"] == 1.0


def test_baseline_uses_depmap_as_safety_context_not_disease_direction_proof():
    target = SimpleNamespace(
        symbol="GENE4", name="test kinase", causal_confidence=0.5, disease_context=0.4,
        development_feasibility=0.7, evidence_coverage=0.5, source_count=2, score=0.55,
        direction="not_available", source_version="target-snapshot-test-v1",
        evidence_breakdown={"held_out": {"known_drug_label": True}, "target_to_hit": {"has_small_molecule_binder": True}},
    )
    result = build_intervention_baseline(target, "test disease", knockout_profile={"knockout_tolerability": 0.2})
    assert result["direction"] == "inhibit"
    assert result["direction_status"] == "model_hypothesis"
    assert result["counter_signals"]
