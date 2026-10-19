from novel_target_discovery_v1.scoring import candidate_gate, colocalization_score, evidence_strength, final_score, novelty_class, novelty_score


def test_novelty_is_separate_from_evidence():
    evidence = evidence_strength(0.8, 0.6, 0.4, 0.2)
    novel = novelty_score(0, 0, 0.1, 0.1)
    mature = novelty_score(1, 1, 1, 1)
    assert evidence == 0.56
    assert novel > mature
    assert final_score(evidence, novel, 0.5) > 0.5


def test_candidate_gate_requires_biological_signal():
    assert candidate_gate(0.2, 0, 0, 0)
    assert candidate_gate(0.03, 0, 0, 0.4)
    assert not candidate_gate(0, 0, 0, 1)
    assert novelty_class(0.5, 0.8) == "emerging"


def test_colocalization_score_is_bounded_and_does_not_hard_saturate():
    weak = colocalization_score(0.50, 0.01)
    medium = colocalization_score(0.70, 0.02)
    strong = colocalization_score(0.90, 0.10)
    assert 0 < weak < medium < strong < 1
