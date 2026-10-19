import json

import pytest

from app.novel_target_snapshot import NovelTargetSnapshotAdapter
from app.open_targets import OpenTargetsError


def make_snapshot(path):
    path.write_text(json.dumps({
        "schema_version": "novel-target-discovery-product-snapshot-v1",
        "snapshot_id": "novel-test-v1",
        "created_at": "2026-09-03T00:00:00+00:00",
        "scientific_status": "computational_hypothesis",
        "source_registry_version": "novel-sources-v1",
        "score_contract": {"version": "novel-target-score-v1"},
        "disease": {"id": "MONDO_0008383", "name": "rheumatoid arthritis", "name_zh": "类风湿关节炎", "aliases": ["RA"]},
        "quality": {"build": {"counts": {"candidate_genes": 10}}, "benchmark": {"status": "computed", "top20_multimodal_count": 4, "permutation_p_value": 0.02}},
        "targets": [{
            "target_id": "ENSG00000000001", "symbol": "GENE1", "approved_name": "gene one", "rank": 1,
            "novel_target_score": 0.72, "evidence_strength": 0.67, "novelty_score": 0.84, "development_feasibility": 0.51,
            "gwas_score": 0.8, "eqtl_score": 0.6, "single_cell_score": 0.5, "network_score": 0.4,
            "direct_modality_count": 3, "candidate_origin": "GWAS+eQTL+single-cell+network", "novelty_class": "emerging",
            "suggested_direction": "inhibit", "known_drug_label": 0, "fine_mapped_loci": 2, "gwas_catalog_studies": 3,
            "colocalised_loci": 1, "eqtl_biosamples": "blood", "best_cell_type": "monocyte", "single_cell_log2fc": 0.8,
            "single_cell_fdr": 0.01, "case_donors": 12, "control_donors": 12,
        }],
    }), encoding="utf-8")


def test_novel_snapshot_exposes_transparent_axes_and_evidence(tmp_path):
    path = tmp_path / "novel.json"
    make_snapshot(path)
    result = NovelTargetSnapshotAdapter(path).discover("类风湿关节炎")
    target = result["targets"][0]
    assert target["score_origin"] == "ai4science_novel_target_score_v1"
    assert target["evidence_breakdown"]["discovery_strategy"] == "novel"
    assert target["evidence_breakdown"]["held_out"]["known_drug_label"] is False
    assert {item["datatype"] for item in result["evidence"]} == {"gwas", "eqtl", "single_cell", "network"}
    assert result["data_quality"]["coverage"]["mean"] == 1.0


def test_novel_snapshot_does_not_silently_fallback_for_unsupported_disease(tmp_path):
    path = tmp_path / "novel.json"
    make_snapshot(path)
    with pytest.raises(OpenTargetsError) as error:
        NovelTargetSnapshotAdapter(path).discover("Alzheimer's disease")
    assert error.value.code == "NOVEL_TARGET_DISEASE_NOT_FOUND"


def test_availability_distinguishes_coverage_empty_and_broken(tmp_path):
    from app.novel_target_snapshot import novel_target_availability
    path = tmp_path / 'novel-target-discovery-v1.json'
    make_snapshot(path)
    for query in ['RA', '类风湿关节炎', 'MONDO_0008383']:
        assert novel_target_availability(tmp_path, query)['available'] is True
    missing = novel_target_availability(tmp_path, 'MONDO_0005148')
    assert missing['available'] is False
    assert missing['code'] == 'NOVEL_TARGET_DISEASE_NOT_FOUND'
    assert missing['diseases'] == [{'id': 'MONDO_0008383', 'name': '类风湿关节炎'}]
    payload = json.loads(path.read_text())
    payload['targets'] = []
    path.write_text(json.dumps(payload))
    assert novel_target_availability(tmp_path, 'RA')['code'] == 'NOVEL_TARGET_EMPTY'
    path.write_text('{broken')
    assert novel_target_availability(tmp_path, 'RA')['code'] == 'NOVEL_TARGET_SNAPSHOT_INVALID'
    assert novel_target_availability(None, 'RA')['code'] == 'NOVEL_TARGET_NOT_CONFIGURED'
