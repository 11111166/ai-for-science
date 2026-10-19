import json

import pytest

from app.open_targets import OpenTargetsError
from app.perturbation_snapshot import PerturbationKnockoutSnapshotAdapter


def test_adapter_rejects_failed_quality_gate(tmp_path):
    path = tmp_path / "failed.json"
    path.write_text(json.dumps({
        "schema_version": "perturbation-knockout-product-snapshot-v1",
        "quality_passed": False,
    }), encoding="utf-8")
    with pytest.raises(OpenTargetsError) as error:
        PerturbationKnockoutSnapshotAdapter(path)
    assert error.value.code == "PERTURBATION_KNOCKOUT_QUALITY_FAILED"


def test_adapter_checks_disease_and_module(tmp_path):
    path = tmp_path / "passed.json"
    path.write_text(json.dumps({
        "schema_version": "perturbation-knockout-product-snapshot-v1", "quality_passed": True,
        "snapshot_id": "test", "disease": {"id": "MONDO_0008383", "name": "rheumatoid arthritis", "name_zh": "类风湿关节炎"},
        "virtual_intervention": {"results": [], "model_version": "v1"},
    }), encoding="utf-8")
    adapter = PerturbationKnockoutSnapshotAdapter(path)
    adapter.validate_disease("Rheumatoid arthritis")
    assert adapter.module("virtual_intervention")["results"] == []
    with pytest.raises(OpenTargetsError) as error:
        adapter.validate_disease("Crohn disease")
    assert error.value.code == "PERTURBATION_KNOCKOUT_DISEASE_NOT_FOUND"


def test_adapter_pools_depmap_gene_coverage_across_disease_packs(tmp_path):
    def write_pack(folder, snapshot_id, disease_id, symbol, screened=True):
        folder.mkdir()
        (folder / "perturbation-knockout-v1.json").write_text(json.dumps({
            "schema_version": "perturbation-knockout-product-snapshot-v1",
            "quality_passed": True,
            "snapshot_id": snapshot_id,
            "disease": {"id": disease_id, "name": snapshot_id},
            "gene_knockout": {
                "scientific_status": "measured_cell_fitness_out_of_domain_proxy",
                "model_version": "knockout-test-v1",
                "benchmark": {"screened_models": 1000},
                "results": [{"symbol": symbol, "screened": screened, "knockout_tolerability": 0.8}],
            },
        }), encoding="utf-8")

    write_pack(tmp_path / "a", "pack-a", "EFO_A", "GENE1")
    write_pack(tmp_path / "b", "pack-b", "EFO_B", "GENE2")
    adapter = PerturbationKnockoutSnapshotAdapter(tmp_path)
    module = adapter.pooled_gene_knockout_module()
    assert {item["symbol"] for item in module["results"]} == {"GENE1", "GENE2"}
    assert module["benchmark"]["pooled_snapshot_count"] == 2
    assert module["benchmark"]["pooled_screened_gene_count"] == 2
    provenance = adapter.pooled_gene_knockout_provenance()
    assert provenance["coverage_scope"] == "gene_level_global"
    assert provenance["snapshot_ids"] == ["pack-a", "pack-b"]
