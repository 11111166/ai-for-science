from compound_evaluation_v1.scoring import developability_score, evidence_match_score, safety_score


def test_evidence_score_rewards_potency_replication_and_direction():
    weak = evidence_match_score(5.1, 5.3, 1, 1, 0.5)
    strong = evidence_match_score(8.0, 9.0, 8, 4, 1.0)
    assert 0 < weak < strong <= 1


def test_safety_and_developability_penalize_flags():
    assert safety_score(0.1, 0.1, 0, False, False) > safety_score(0.8, 0.8, 2, True, True)
    assert developability_score(0.8, 0, 80, 4, -2, 0.9, 0) > developability_score(0.3, 3, 180, 15, -7, 0.2, 3)

