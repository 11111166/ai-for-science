import json

import pytest

from app.compound_snapshot import CompoundEvaluationSnapshotAdapter
from app.open_targets import OpenTargetsError
from app.virtual_cell_snapshot import VirtualCellSnapshotAdapter


def write_snapshot(tmp_path, name, payload):
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def disease():
    return {
        "id": "MONDO_0008383",
        "name": "rheumatoid arthritis",
        "name_zh": "类风湿关节炎",
        "aliases": ["RA"],
    }


def test_virtual_cell_snapshot_requires_passed_quality_and_resolves_alias(tmp_path):
    path = write_snapshot(tmp_path, "virtual.json", {
        "schema_version": "virtual-cell-product-snapshot-v1",
        "snapshot_id": "vc-test",
        "model_version": "state-axis-test",
        "quality_passed": True,
        "disease": disease(),
        "results": [{"symbol": "TYK2", "projected_reversal": 0.42}],
    })
    adapter = VirtualCellSnapshotAdapter(path)
    adapter.validate_disease("RA")
    assert adapter.results()[0]["symbol"] == "TYK2"
    assert len(adapter.provenance()["snapshot_sha256"]) == 64
    with pytest.raises(OpenTargetsError) as exc_info:
        adapter.validate_disease("asthma")
    assert "尚无通过质量门" in exc_info.value.message

    failed = json.loads(path.read_text())
    failed["quality_passed"] = False
    failed_path = write_snapshot(tmp_path, "virtual-failed.json", failed)
    with pytest.raises(OpenTargetsError) as exc_info:
        VirtualCellSnapshotAdapter(failed_path)
    assert "未通过独立质量门" in exc_info.value.message


def test_compound_snapshot_reports_only_observed_target_coverage(tmp_path):
    path = write_snapshot(tmp_path, "compound.json", {
        "schema_version": "compound-evaluation-product-snapshot-v1",
        "snapshot_id": "compound-test",
        "model_version": "chembl-test",
        "quality_passed": True,
        "disease": disease(),
        "compounds": [
            {"molecule_chembl_id": "CHEMBL1", "target_matches": [{"target_symbol": "TYK2"}]},
            {"molecule_chembl_id": "CHEMBL2", "target_matches": [{"target_symbol": "PTPN22"}]},
        ],
    })
    adapter = CompoundEvaluationSnapshotAdapter(path)
    adapter.validate_disease("类风湿关节炎")
    assert adapter.available_symbols() == {"TYK2", "PTPN22"}
    assert adapter.pooled_available_symbols() == {"TYK2", "PTPN22"}
    assert adapter.pooled_provenance()["coverage_scope"] == "gene_and_molecule_level_global"
    assert adapter.provenance()["data_mode"] == "real"
