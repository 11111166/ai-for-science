import csv
import io
import json

import pytest

import app.fungal_target as fungal_target_module
from app.fungal_target import FungalTargetError, _parse_table, analyse_payload, preview_payload, sc5314_example_payload
from app.models import Asset


def asset(data: bytes, name: str = "fungal.csv") -> Asset:
    return Asset(
        id="asset-fungal",
        project_id="project-1",
        name=name,
        kind="fungal-target-evidence",
        storage_uri="file:///tmp/not-used",
        content_hash="f" * 64,
        size=len(data),
        format="csv",
        created_by="owner",
    )


def prioritization_csv() -> bytes:
    return (
        "protein_id,gene_symbol,protein_name,pathway,essentiality,virulence,infection_expression,resistance_relevance,human_similarity,druggability,pathogen_conservation,pocket_score\n"
        "P001,ERG11,Lanosterol 14-alpha demethylase,ergosterol,0.98,0.83,0.72,0.91,0.20,0.95,0.88,0.86\n"
        "P002,FKS1,Beta-1 3-glucan synthase,cell wall,0.96,0.91,0.61,0.80,0.08,0.82,0.79,0.66\n"
        "P003,HSP90,Heat shock protein,stress,0.90,0.75,0.80,0.70,0.86,0.75,0.84,0.55\n"
        "P004,UNK1,Uncharacterized protein,unknown,0.30,,,,,0.20,,\n"
    ).encode()


def test_prioritization_preview_preserves_missing_evidence():
    data = prioritization_csv()
    preview = preview_payload(asset(data), data, "prioritization")
    assert preview["row_count"] == 4
    assert preview["metric_coverage"]["essentiality"] == 4
    assert preview["metric_coverage"]["virulence"] == 3


def test_prioritization_applies_biology_host_and_druggability_funnel():
    data = prioritization_csv()
    result = analyse_payload(asset(data), data, "prioritization", {
        "organism": "Candida albicans",
        "top_n": 20,
        "evidence_threshold": 0.5,
        "human_similarity_threshold": 0.35,
    })
    assert [stage["count"] for stage in result["funnel"]] == [4, 3, 2, 2, 2]
    assert {item["gene_symbol"] for item in result["candidates"]} == {"ERG11", "FKS1"}
    assert result["candidates"][0]["candidate_label"] == "计算候选靶标（待实验验证）"
    assert "不等于已发现或已确认靶标" in result["boundary"]


def test_target_fishing_requires_two_convergent_evidence_channels():
    data = (
        "protein_id,gene_symbol,ligand_similarity,ai_prediction,inverse_docking,pathway_evidence,resistance_evidence,genetic_evidence,human_similarity\n"
        "P1,TGT1,0.90,0.85,0.80,0.70,0.10,0.20,0.15\n"
        "P2,TGT2,0.90,0.20,,,,,0.10\n"
        "P3,TGT3,0.75,0.72,0.10,,,,0.80\n"
    ).encode()
    result = analyse_payload(asset(data), data, "target_fishing", {
        "organism": "Aspergillus fumigatus",
        "compound_name": "AF-01",
        "top_n": 10,
        "evidence_threshold": 0.5,
        "human_similarity_threshold": 0.35,
    })
    assert [stage["count"] for stage in result["funnel"]] == [3, 3, 2, 1, 1]
    assert result["candidates"][0]["gene_symbol"] == "TGT1"


@pytest.mark.parametrize("bad_value", ["1.2", "-0.1", "not-a-number"])
def test_invalid_evidence_scores_are_rejected(bad_value):
    data = f"protein_id,gene_symbol,essentiality\nP1,G1,{bad_value}\n".encode()
    with pytest.raises(FungalTargetError):
        _parse_table(data, "bad.csv", "prioritization")


def test_template_contract_can_be_written_as_csv():
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["protein_id", "gene_symbol", "essentiality"])
    writer.writerow(["P1", "G1", "0.8"])
    rows, header = _parse_table(buffer.getvalue().encode(), "small.csv", "prioritization")
    assert header == ["protein_id", "gene_symbol", "essentiality"]
    assert rows[0]["essentiality"] == pytest.approx(0.8)


def test_sc5314_example_loads_both_complete_workflows_with_provenance(tmp_path, monkeypatch):
    # Exercise the embedded fallback independently of any evidence bundle that
    # may be mounted on a developer or production host.
    monkeypatch.setattr(fungal_target_module, "FUNGAL_TARGET_DATA_DIR", tmp_path / "not-configured")
    example = sc5314_example_payload()
    assert example["strain"] == "SC5314"
    assert example["identifiers"]["bioproject"] == "PRJNA10701"
    assert example["genome"]["chromosome_count"] == 8
    assert example["genome"]["protein_sequence_count"] == 6030
    assert "disease" not in example
    assert len(example["sources"]) >= 6

    prioritization = example["workflows"]["prioritization"]
    fishing = example["workflows"]["target_fishing"]
    assert prioritization["all_candidate_count"] == 12
    assert prioritization["candidates"]
    assert fishing["all_candidate_count"] == 8
    assert fishing["candidates"][0]["gene_symbol"] == "ERG11"
    assert fishing["compound"]["name"] == "Fluconazole / 氟康唑"
    assert prioritization["example_mode"] is True
    assert "演示性标准化编码" in prioritization["score_interpretation"]
    assert all(item["candidate_label"] == "计算候选靶标（待实验验证）" for item in fishing["candidates"])


def test_sc5314_example_prefers_versioned_server_evidence_bundle(tmp_path, monkeypatch):
    derived = tmp_path / "derived"
    derived.mkdir()
    matrix = derived / "sc5314.csv"
    matrix.write_bytes(prioritization_csv())
    (tmp_path / "manifest.json").write_text(json.dumps({
        "configured": True,
        "bundle_version": "test-v1",
        "generated_at": "2026-09-24T00:00:00Z",
        "organism": "Candida albicans SC5314",
        "case_files": {"sc5314_prioritization": "derived/sc5314.csv"},
        "counts": {"proteins": 4, "cgd_phenotype_rows": 10, "phi_base_candida_albicans_rows": 2},
        "sources": [{"id": "test", "name": "Test source", "version": "1", "url": "https://example.org", "license": "CC0"}],
        "algorithms": [{"name": "DIAMOND", "version": "2.2.8", "purpose": "homology", "parameters": "ultra-sensitive"}],
    }), encoding="utf-8")
    monkeypatch.setattr(fungal_target_module, "FUNGAL_TARGET_DATA_DIR", tmp_path)

    example = sc5314_example_payload()
    prioritization = example["workflows"]["prioritization"]
    assert prioritization["data_mode"] == "server_evidence_bundle"
    assert prioritization["bundle_version"] == "test-v1"
    assert prioritization["all_candidate_count"] == 4
    assert example["genome"]["analysis_protein_count"] == 4
    assert example["evidence_bundle"]["configured"] is True
