import json
from pathlib import Path

import pytest

from app.open_targets import OpenTargetsError
from app.target_snapshot import TargetSnapshotAdapter


def make_snapshot(path: Path):
    payload = {
        "schema_version": "target-discovery-product-snapshot-v1",
        "snapshot_id": "snapshot-test-v1",
        "created_at": "2026-09-02T00:00:00+00:00",
        "source_registry_version": "sources-v1",
        "diseases": [{"id": "D1", "name": "test disease", "area": "test", "aliases": ["TD"]}],
        "quality": {"benchmark": {"status": "computed", "macro": {"discovery_score": {"ndcg_at_20": 0.8}}}},
        "targets": [{
            "disease_id": "D1", "disease_name": "test disease", "therapeutic_area": "test",
            "target_id": "ENSG00000000001", "symbol": "GENE1", "approved_name": "gene one", "biotype": "protein_coding",
            "rank": 1, "discovery_score": 0.72, "causal_confidence": 0.8, "disease_context": 0.6,
            "development_feasibility": 0.7, "evidence_coverage": 0.5, "nonclinical_evidence_count": 12,
            "nonclinical_datatype_count": 3, "known_drug_label": 1, "hgnc_status": "matched", "hpa_status": "matched",
            "hgnc_id": "HGNC:1", "uniprot_ids": "P00001", "genetic_association": 0.9,
            "genetic_literature": 0.0, "somatic_mutation": 0.0, "rna_expression": 0.6,
            "affected_pathway": 0.0, "animal_model": 0.0,
            "chemistry_start_score": 0.75, "biologic_start_score": 0.0,
            "tissue_selectivity_score": 0.55, "disease_evidence_score": 0.7,
            "target_to_hit_score": 0.68, "target_to_hit_level": "ready_for_hit_search",
            "recommended_modality": "small_molecule", "hasSmallMoleculeBinder": 1,
            "hasPocket": 1, "hasLigand": 1, "hasHighQualityChemicalProbes": 0,
            "isInMembrane": 0, "isSecreted": 0, "known_safety_signal": False,
        }],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_snapshot_adapter_exposes_three_axes_and_held_out_label(tmp_path):
    path = tmp_path / "snapshot.json"
    make_snapshot(path)
    result = TargetSnapshotAdapter(path).discover("TD", top_n=10)
    target = result["targets"][0]
    assert target["score_origin"] == "ai4science_three_axis_score_v1"
    assert target["causal_confidence"] == 0.8
    assert target["evidence_breakdown"]["held_out"]["known_drug_label"] is True
    assert result["data_quality"]["score_contract_version"] == "three-axis-score-v1"
    assert result["benchmark"]["status"] == "computed"
    hit = target["evidence_breakdown"]["target_to_hit"]
    assert hit["contract_version"] == "target-to-hit-v3"
    assert hit["decision"] == "start_hit_search"
    assert hit["structure_readiness"]["level"] == "strong"
    assert hit["evidence_replication"] == {"source_count": 2, "level": "moderate"}
    assert {route["id"] for route in hit["routes"]} == {"small_molecule", "antibody_or_protein", "targeted_degradation", "rna_or_gene_medicine"}
    assert hit["safety_review"]["status"] == "unresolved"
    assert result["data_quality"]["target_to_hit_contract_version"] == "target-to-hit-v3"


def test_snapshot_adapter_rejects_unvalidated_disease(tmp_path):
    path = tmp_path / "snapshot.json"
    make_snapshot(path)
    with pytest.raises(OpenTargetsError) as error:
        TargetSnapshotAdapter(path).discover("another disease")
    assert error.value.code == "TARGET_SNAPSHOT_DISEASE_NOT_FOUND"


def test_snapshot_adapter_accepts_validated_chinese_disease_name(tmp_path):
    path = tmp_path / "snapshot.json"
    make_snapshot(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["diseases"][0]["id"] = "MONDO_0008383"
    payload["targets"][0]["disease_id"] = "MONDO_0008383"
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = TargetSnapshotAdapter(path).discover("类风湿关节炎", top_n=10)

    assert result["resolution"]["disease_id"] == "MONDO_0008383"
    assert len(result["targets"]) == 1
