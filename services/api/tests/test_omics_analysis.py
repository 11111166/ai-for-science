import math
import json
from types import SimpleNamespace

import pytest

from app import omics_analysis
from app.models import Asset
from app.omics_analysis import (
    EXAMPLE_ROOT,
    OMICS_EXAMPLES,
    OmicsAnalysisError,
    _advanced_multimodal,
    _advanced_transcriptomics,
    _example_payload,
    _matrix_analysis,
    _normalise_legacy_r_rows,
    _single_cell_analysis,
    _welch_test,
    get_omics_result,
    preview_payload,
)


def asset(name="matrix.csv", data=b""):
    return Asset(
        id="asset-1",
        project_id="project-1",
        name=name,
        kind="omics-data",
        storage_uri="file:///tmp/not-used",
        content_hash="a" * 64,
        size=len(data),
        format=name.rsplit(".", 1)[-1],
        created_by="owner",
    )


def test_welch_test_uses_student_t_probability():
    statistic, p_value = _welch_test([1, 2, 3], [4, 5, 6])
    assert statistic == pytest.approx(3.674234, rel=1e-5)
    assert p_value == pytest.approx(0.0213116, rel=1e-4)


def test_legacy_r_dataframe_rows_are_flattened_without_changing_numeric_matrices():
    value = {
        "rows": [[{"feature": "GENE1", "adjusted_p_value": 0.01}], [{"feature": "GENE2", "adjusted_p_value": 0.02}]],
        "enrichment": {"go": [[{"ID": "GO:1", "Description": "response"}]], "kegg": []},
        "matrix": [[1.0, 0.5], [0.5, 1.0]],
    }
    result = _normalise_legacy_r_rows(value)
    assert result["rows"][0]["feature"] == "GENE1"
    assert result["enrichment"]["go"][0]["ID"] == "GO:1"
    assert result["matrix"] == value["matrix"]


def test_saved_legacy_omics_result_is_normalised_at_api_read(monkeypatch):
    saved = {
        "job_id": "job-1", "modality": "bulk_transcriptomics",
        "advanced_transcriptomics": {"deseq2": {"rows": [[{"feature": "GENE1"}]]}},
        "advanced_single_cell": {"embedding": {"cells": [[{"cell_id": "cell-1"}]]}},
    }
    job = SimpleNamespace(id="job-1", type="omics_analysis", project_id="p", status="completed", output_ref="file:///legacy.json")
    db = SimpleNamespace(get=lambda model, identifier: job)
    monkeypatch.setattr(omics_analysis, "require_project_access", lambda *args: None)
    monkeypatch.setattr(omics_analysis, "read_storage_bytes", lambda *args: json.dumps(saved).encode())
    result = get_omics_result("job-1", db, SimpleNamespace(id="user"))
    assert result["advanced_transcriptomics"]["deseq2"]["rows"][0]["feature"] == "GENE1"
    assert result["advanced_single_cell"]["embedding"]["cells"][0]["cell_id"] == "cell-1"


def test_bulk_matrix_analysis_normalizes_and_adjusts_p_values():
    data = (
        "feature_id,control_1,control_2,case_1,case_2\n"
        "GENE_UP,1,1.2,6,6.2\n"
        "GENE_DOWN,8,8.2,2,2.1\n"
        "GENE_FLAT,3,3.1,3,3.1\n"
        "GENE_MISSING,NA,2,4,NA\n"
    ).encode()
    result = _matrix_analysis(asset(data=data), data, "bulk_transcriptomics", {
        "reference_samples": ["control_1", "control_2"],
        "comparison_samples": ["case_1", "case_2"],
        "scale": "log2",
        "zero_is_missing": False,
        "fdr_threshold": 0.05,
        "effect_threshold": 1.0,
    })
    assert result["summary"]["feature_count"] == 4
    assert result["summary"]["tested_feature_count"] == 3
    by_feature = {item["feature"]: item for item in result["top_features"]}
    assert by_feature["GENE_UP"]["effect"] > 4
    assert by_feature["GENE_DOWN"]["effect"] < -5
    assert 0 <= by_feature["GENE_UP"]["adjusted_p_value"] <= 1
    assert result["heatmap"]["samples"] == ["control_1", "control_2", "case_1", "case_2"]
    assert len(result["sample_pca"]["points"]) == 4
    assert result["sample_pca"]["features_used"] >= 2
    assert result["sample_correlation"]["samples"] == result["heatmap"]["samples"]
    assert len(result["sample_correlation"]["matrix"]) == 4
    assert result["sample_correlation"]["matrix"][0][0] == 1.0
    assert result["summary"]["significant_up_count"] >= 1
    assert result["summary"]["significant_down_count"] >= 1


def test_advanced_transcriptomics_rejects_non_count_input():
    with pytest.raises(OmicsAnalysisError, match="OMICS_ADVANCED_COUNTS_REQUIRED"):
        _advanced_transcriptomics(asset(), b"feature,a,b,c,d\nG1,1,2,3,4\n", {}, {
            "input": {"scale": "log2"},
        })


def test_advanced_transcriptomics_runs_isolated_r_and_returns_structured_result(monkeypatch):
    payload = {
        "status": "completed",
        "input": {"detected_gene_id_type": "ensembl"},
        "deseq2": {"significant_count": 2, "rows": []},
        "edger": {"significant_count": 2, "rows": []},
        "consensus": {"significant_count": 2, "rows": []},
        "enrichment": {"go": [], "kegg": [], "gsea_go": [], "gsea_kegg": []},
        "warnings": [],
    }

    def fake_run(command, **kwargs):
        assert command[0] == "/bin/sh"
        assert command[1] == "--vanilla"
        metadata = json.loads(command[4])
        assert metadata["species"] == "human"
        assert metadata["reference_samples"] == ["c1", "c2"]
        with open(command[5], "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(omics_analysis, "get_settings", lambda: SimpleNamespace(omics_rscript="/bin/sh", omics_advanced_timeout=600))
    monkeypatch.setattr(omics_analysis.subprocess, "run", fake_run)
    quick = {
        "input": {"scale": "counts"},
        "comparison": {"reference_samples": ["c1", "c2"], "comparison_samples": ["t1", "t2"], "reference_label": "control", "comparison_label": "treated"},
        "summary": {"fdr_threshold": .05, "effect_threshold": 1},
    }
    result = _advanced_transcriptomics(asset(data=b"feature,c1,c2,t1,t2\nG1,1,2,3,4\n"), b"feature,c1,c2,t1,t2\nG1,1,2,3,4\n", {"species": "human", "gene_id_type": "auto"}, quick)
    assert result["deseq2"]["significant_count"] == 2
    assert result["runtime"]["execution"] == "local_isolated_subprocess"
    assert result["runtime"]["threads"] == 1


def test_advanced_multimodal_runs_isolated_r_with_modality_metadata(monkeypatch):
    payload = {
        "status": "completed",
        "input": {"modelled_protein_count": 25},
        "limma": {"significant_count": 3, "rows": []},
        "enrichment": {"go": [], "kegg": [], "gsea_go": [], "gsea_kegg": []},
        "warnings": [],
    }

    def fake_run(command, **kwargs):
        assert command[0] == "/bin/sh"
        assert command[1] == "--vanilla"
        assert command[2].endswith("omics_multimodal.R")
        metadata = json.loads(command[4])
        assert metadata["modality"] == "proteomics"
        assert metadata["species"] == "human"
        assert metadata["gene_id_type"] == "uniprot"
        assert metadata["scale"] == "raw"
        assert metadata["zero_is_missing"] is True
        assert metadata["reference_samples"] == ["c1", "c2"]
        with open(command[5], "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(omics_analysis, "get_settings", lambda: SimpleNamespace(omics_rscript="/bin/sh", omics_advanced_timeout=600))
    monkeypatch.setattr(omics_analysis.subprocess, "run", fake_run)
    quick = {
        "input": {"scale": "raw", "zero_is_missing": True},
        "comparison": {"reference_samples": ["c1", "c2"], "comparison_samples": ["t1", "t2"], "reference_label": "control", "comparison_label": "treated"},
        "summary": {"fdr_threshold": .05, "effect_threshold": 1},
    }
    result = _advanced_multimodal(asset(data=b"feature,c1,c2,t1,t2\nP1,1,2,3,4\n"), b"feature,c1,c2,t1,t2\nP1,1,2,3,4\n", "proteomics", {"species": "human", "gene_id_type": "uniprot"}, quick)
    assert result["limma"]["significant_count"] == 3
    assert result["runtime"]["execution"] == "local_isolated_subprocess"
    assert result["runtime"]["threads"] == 1


def test_advanced_multimodal_rejects_invalid_metabolite_identifier_type():
    quick = {
        "input": {"scale": "raw", "zero_is_missing": False},
        "comparison": {"reference_samples": ["c1", "c2", "c3"], "comparison_samples": ["t1", "t2", "t3"], "reference_label": "control", "comparison_label": "case"},
        "summary": {"fdr_threshold": .05, "effect_threshold": .585},
    }
    with pytest.raises(OmicsAnalysisError, match="OMICS_MULTIMODAL_METABOLITE_ID"):
        _advanced_multimodal(asset(), b"feature,c1,c2,c3,t1,t2,t3\nX,1,2,3,4,5,6\n", "metabolomics", {"metabolite_id_type": "hmdb"}, quick)


def test_single_cell_analysis_uses_samples_not_cells_as_replicates():
    header = "cell_id,sample_id,group,cell_type," + ",".join([f"G{i}" for i in range(10)]) + "\n"
    rows = []
    for sample, group, base in [("c1", "control", 1), ("c2", "control", 2), ("p1", "case", 8), ("p2", "case", 9)]:
        for cell in range(3):
            values = [base + cell if gene == 0 else 1 for gene in range(10)]
            rows.append(f"{sample}_{cell},{sample},{group},T cell," + ",".join(map(str, values)))
    data = (header + "\n".join(rows) + "\n").encode()
    preview = preview_payload(asset(data=data), data, "single_cell")
    assert preview["feature_count_detected"] == 10
    result = _single_cell_analysis(asset(data=data), data, {"reference_group": "control", "comparison_group": "case"})
    assert result["summary"]["cell_count"] == 12
    assert result["summary"]["sample_count"] == 4
    assert len(result["sample_qc"]) == 4
    assert "G0" in {item["feature"] for item in result["top_features"]}
    assert next(item for item in result["top_features"] if item["feature"] == "G0")["effect"] > 0
    assert math.isfinite(result["top_features"][0]["effect"])
    assert not any(key.startswith("_") for key in result["top_features"][0])
    assert len(result["sample_pca"]["points"]) == 4
    assert len(result["sample_correlation"]["matrix"]) == 4
    assert "伪汇总" in result["warnings"][0]


def test_binary_input_is_rejected_with_compute_tier_guidance():
    data = b"\x89HDF\x00\x01"
    with pytest.raises(OmicsAnalysisError, match="OMICS_BINARY_INPUT") as exc:
        preview_payload(asset("matrix.h5ad", data), data, "single_cell")
    assert "计算节点" in exc.value.message


@pytest.mark.parametrize(
    ("modality", "minimum_significant"),
    [
        ("bulk_transcriptomics", 20),
        ("single_cell", 10),
        ("proteomics", 1),
        ("metabolomics", 10),
    ],
)
def test_built_in_example_is_small_traceable_and_produces_signal(modality, minimum_significant):
    example = _example_payload(modality)
    path = EXAMPLE_ROOT / example["filename"]
    data = path.read_bytes()
    assert path.is_file()
    assert example["size"] == len(data) < 200_000
    assert len(example["sha256"]) == 64
    assert example["accession"]
    assert example["source_url"].startswith("https://")
    example_asset = asset(path.name, data)
    preview = preview_payload(example_asset, data, modality)

    if modality == "single_cell":
        result = _single_cell_analysis(example_asset, data, {
            "reference_group": "control",
            "comparison_group": "stimulated",
            "fdr_threshold": 0.05,
            "effect_threshold": 1.0,
        })
        assert result["summary"]["cell_count"] == 160
        assert result["summary"]["sample_count"] == 8
    else:
        reference = [item["name"] for item in preview["sample_columns"] if item["suggested_group"] == "reference"]
        comparison = [item["name"] for item in preview["sample_columns"] if item["suggested_group"] == "comparison"]
        result = _matrix_analysis(example_asset, data, modality, {
            "reference_samples": reference,
            "comparison_samples": comparison,
            "scale": OMICS_EXAMPLES[modality]["recommended_scale"],
            "zero_is_missing": OMICS_EXAMPLES[modality]["zero_is_missing"],
            "fdr_threshold": 0.05,
            "effect_threshold": 0.585 if modality == "metabolomics" else 1.0,
        })

    assert result["summary"]["significant_count"] >= minimum_significant
