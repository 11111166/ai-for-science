from target_discovery_v1.scoring import CAUSAL_WEIGHTS, discovery_score, feasibility_score, signed_to_unit, target_to_hit_profile, weighted_axis


def test_score_contract_is_bounded_and_known_drug_is_not_an_input():
    causal = weighted_axis({"genetic_association": 0.8, "genetic_literature": 0.4, "somatic_mutation": 0.0}, CAUSAL_WEIGHTS)
    feasibility = feasibility_score({"hasSmallMoleculeBinder": 1, "hasPocket": 1, "hasSafetyEvent": 1})
    assert 0 <= causal <= 1
    assert 0 <= feasibility <= 1
    assert 0 <= discovery_score(causal, 0.5, feasibility) <= 1
    assert "known_drug" not in CAUSAL_WEIGHTS


def test_signed_feature_conversion():
    assert signed_to_unit(-1) == 0
    assert signed_to_unit(0) == 0.5
    assert signed_to_unit(1) == 1


def test_target_to_hit_profile_separates_route_from_safety():
    profile = target_to_hit_profile(
        {
            "hasSmallMoleculeBinder": 1,
            "hasPocket": 1,
            "hasLigand": 1,
            "hasHighQualityChemicalProbes": 1,
            "tissueSpecificity": 0.4,
            "tissueDistribution": -0.2,
            "hasSafetyEvent": 1,
        },
        causal=0.8,
        context=0.6,
    )
    assert profile["recommended_modality"] == "small_molecule"
    assert profile["readiness_level"] == "ready_for_hit_search"
    assert profile["known_safety_signal"] is True
    assert 0 < profile["target_to_hit_score"] <= 1


def test_missing_safety_is_not_rewarded():
    missing = target_to_hit_profile({}, causal=0.0, context=0.0)
    explicit_zero = target_to_hit_profile({"hasSafetyEvent": 0}, causal=0.0, context=0.0)
    assert missing["target_to_hit_score"] == explicit_zero["target_to_hit_score"]
