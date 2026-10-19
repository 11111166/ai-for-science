import json

import pytest

from app.compound_snapshot import CompoundEvaluationSnapshotAdapter
from app.novel_target_snapshot import NovelTargetSnapshotAdapter
from app.open_targets import OpenTargetsError
from app.perturbation_snapshot import PerturbationKnockoutSnapshotAdapter
from app.virtual_cell_snapshot import VirtualCellSnapshotAdapter


DISEASES = {
    "ra": {"id": "MONDO_0008383", "name": "rheumatoid arthritis", "name_zh": "类风湿关节炎", "aliases": ["RA"]},
    "sle": {"id": "MONDO_0007915", "name": "systemic lupus erythematosus", "name_zh": "系统性红斑狼疮", "aliases": ["SLE"]},
}


def write(root, disease_key, filename, payload):
    path = root / disease_key / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_novel_snapshot_directory_routes_by_disease(tmp_path):
    for key, disease in DISEASES.items():
        write(tmp_path, key, "novel-target-discovery-v1.json", {
            "schema_version": "novel-target-discovery-product-snapshot-v1", "snapshot_id": f"novel-{key}",
            "created_at": "2026-09-07T00:00:00+00:00", "source_registry_version": "test",
            "scientific_status": "computational_hypothesis", "score_contract": {"version": "novel-target-score-v2"},
            "disease": disease, "quality": {"build": {}, "benchmark": {}}, "targets": [],
        })
    adapter = NovelTargetSnapshotAdapter(tmp_path)
    assert adapter.discover("SLE")["resolution"]["disease_id"] == "MONDO_0007915"
    with pytest.raises(OpenTargetsError) as error:
        adapter.discover("Crohn disease")
    assert error.value.code == "NOVEL_TARGET_DISEASE_NOT_FOUND"


def test_downstream_snapshot_directories_keep_diseases_isolated(tmp_path):
    for key, disease in DISEASES.items():
        write(tmp_path / "perturb", key, "perturbation-knockout-v1.json", {
            "schema_version": "perturbation-knockout-product-snapshot-v1", "quality_passed": True,
            "snapshot_id": f"perturb-{key}", "disease": disease,
            "virtual_intervention": {"results": [{"symbol": key.upper()}]},
        })
        write(tmp_path / "virtual", key, "virtual-cell-v1.json", {
            "schema_version": "virtual-cell-product-snapshot-v1", "quality_passed": True,
            "snapshot_id": f"virtual-{key}", "disease": disease, "results": [{"symbol": key.upper()}],
        })
        write(tmp_path / "compound", key, "compound-evaluation-v1.json", {
            "schema_version": "compound-evaluation-product-snapshot-v1", "quality_passed": True,
            "snapshot_id": f"compound-{key}", "disease": disease,
            "compounds": [{"target_matches": [{"target_symbol": key.upper()}]}],
        })

    perturb = PerturbationKnockoutSnapshotAdapter(tmp_path / "perturb")
    perturb.validate_disease("系统性红斑狼疮")
    assert perturb.module("virtual_intervention")["results"][0]["symbol"] == "SLE"
    assert perturb.provenance("virtual_intervention")["snapshot_id"] == "perturb-sle"

    virtual = VirtualCellSnapshotAdapter(tmp_path / "virtual")
    virtual.validate_disease("MONDO_0008383")
    assert virtual.results()[0]["symbol"] == "RA"
    assert virtual.payload["disease"]["id"] == "MONDO_0008383"

    compound = CompoundEvaluationSnapshotAdapter(tmp_path / "compound")
    compound.validate_disease("SLE")
    assert compound.available_symbols() == {"SLE"}
    assert compound.payload["disease"]["id"] == "MONDO_0007915"
    with pytest.raises(OpenTargetsError):
        compound.validate_disease("multiple sclerosis")


def test_directory_adapters_index_headers_without_decoding_result_arrays(tmp_path, monkeypatch):
    write(tmp_path, "ra", "virtual-cell-v1.json", {
        "schema_version": "virtual-cell-product-snapshot-v1", "quality_passed": True,
        "snapshot_id": "virtual-ra", "disease": DISEASES["ra"],
        "results": [{"symbol": "TYK2", "payload": "x" * 100_000}],
    })
    monkeypatch.setattr("app.virtual_cell_snapshot.json.loads", lambda _: (_ for _ in ()).throw(AssertionError("eager decode")))
    adapter = VirtualCellSnapshotAdapter(tmp_path)
    assert len(adapter.snapshot_headers) == 1
    assert adapter.adapters == []
    assert adapter.selected is None
