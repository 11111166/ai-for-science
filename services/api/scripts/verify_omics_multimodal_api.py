#!/usr/bin/env python3
"""End-to-end acceptance test for the four built-in omics workflows."""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request


BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:19133/api/v1/drug-discovery"
HEADERS = {"Content-Type": "application/json", "X-Dev-User": "omics-v5-staging", "X-Dev-Role": "admin"}


def records(rows: list, required_key: str) -> None:
    assert isinstance(rows, list)
    assert all(isinstance(row, dict) and required_key in row for row in rows), f"invalid {required_key} record shape"


def request(method: str, path: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload).encode() if payload is not None else None
    call = urllib.request.Request(f"{BASE}{path}", data=body, method=method, headers=HEADERS)
    try:
        with urllib.request.urlopen(call, timeout=650) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")
        raise RuntimeError(f"{method} {path} failed: HTTP {error.code}: {detail}") from error


project = request("POST", "/projects", {
    "name": "多组学 v5 隔离验收",
    "disease": "staging",
    "objective": "four modality end-to-end verification",
    "research_track": "full",
})
project_id = project["id"]


def run(modality: str) -> dict:
    installed = request("POST", f"/projects/{project_id}/omics/examples/{modality}")
    asset_id = installed["asset"]["id"]
    preview = request("POST", f"/projects/{project_id}/omics/preview", {"asset_id": asset_id, "modality": modality})
    parameters: dict = {
        "asset_id": asset_id,
        "modality": modality,
        "fdr_threshold": 0.05,
        "effect_threshold": 0.585 if modality == "metabolomics" else 1.0,
    }
    if modality == "single_cell":
        parameters.update({
            "reference_group": "control",
            "comparison_group": "stimulated",
            "advanced_multimodal": True,
            "species": "human",
            "gene_id_type": "auto",
        })
    else:
        reference = [item["name"] for item in preview["sample_columns"] if item["suggested_group"] == "reference"]
        comparison = [item["name"] for item in preview["sample_columns"] if item["suggested_group"] == "comparison"]
        parameters.update({
            "reference_samples": reference,
            "comparison_samples": comparison,
            "reference_label": installed["example"]["reference_label"],
            "comparison_label": installed["example"]["comparison_label"],
            "scale": installed["example"]["recommended_scale"],
            "zero_is_missing": installed["example"]["zero_is_missing"],
        })
        if modality == "bulk_transcriptomics":
            parameters.update({"advanced_transcriptomics": True, "species": "human", "gene_id_type": "auto"})
        elif modality == "proteomics":
            parameters.update({"advanced_multimodal": True, "species": "human", "gene_id_type": "auto"})
        else:
            parameters.update({"advanced_multimodal": True, "metabolite_id_type": "auto"})
    accepted = request("POST", f"/projects/{project_id}/omics-analysis-jobs", {
        "adapter_mode": "real",
        "input_version": "omics-analysis-v1.3.1",
        "parameters": parameters,
    })
    job_id = accepted["job_id"]
    for _ in range(360):
        job = request("GET", f"/jobs/{job_id}")
        if job["status"] == "completed":
            break
        if job["status"] == "failed":
            raise RuntimeError(f"{modality} failed: {job.get('error_code')} {job.get('error_message')}")
        time.sleep(2)
    else:
        raise RuntimeError(f"{modality} timed out")
    result = request("GET", f"/jobs/{job_id}/omics-result")
    assert result["model_version"] == "omics-analysis-v1.3.1"
    return result


summary: dict[str, dict] = {}
for modality in ("bulk_transcriptomics", "single_cell", "proteomics", "metabolomics"):
    result = run(modality)
    if modality == "bulk_transcriptomics":
        advanced = result["advanced_transcriptomics"]
        assert advanced["deseq2"]["significant_count"] > 0
        assert advanced["edger"]["significant_count"] > 0
        assert advanced["consensus"]["significant_count"] > 0
        assert all(advanced["enrichment"][key] for key in ("go", "kegg", "gsea_go", "gsea_kegg"))
        for key in ("deseq2", "edger", "consensus"):
            records(advanced[key]["rows"], "feature")
        for key in ("go", "kegg", "gsea_go", "gsea_kegg"):
            records(advanced["enrichment"][key], "Description")
        summary[modality] = {
            "deseq2": advanced["deseq2"]["significant_count"],
            "edger": advanced["edger"]["significant_count"],
            "consensus": advanced["consensus"]["significant_count"],
        }
    elif modality == "single_cell":
        advanced = result["advanced_single_cell"]
        assert advanced["input"]["retained_cell_count"] > 0
        assert advanced["embedding"]["cluster_count"] >= 2
        assert "UMAP" in advanced["embedding"]["method"]
        assert advanced["markers"]["rows"]
        assert advanced["pseudobulk"]["significant_count"] > 0
        assert all(advanced["enrichment"][key] for key in ("go", "kegg", "gsea_go", "gsea_kegg"))
        records(advanced["embedding"]["cells"], "cell_id")
        records(advanced["markers"]["rows"], "feature")
        records(advanced["pseudobulk"]["rows"], "feature")
        for key in ("go", "kegg", "gsea_go", "gsea_kegg"):
            records(advanced["enrichment"][key], "Description")
        summary[modality] = {
            "cells": advanced["input"]["retained_cell_count"],
            "clusters": advanced["embedding"]["cluster_count"],
            "pseudobulk": advanced["pseudobulk"]["significant_count"],
        }
    elif modality == "proteomics":
        advanced = result["advanced_proteomics"]
        assert advanced["limma"]["significant_count"] > 0
        assert advanced["filtering"]["group_specific_missing_count"] > 0
        assert advanced["enrichment"]["mapping"]["success_fraction"] >= 0.8
        assert all(advanced["enrichment"][key] for key in ("go", "kegg", "gsea_go", "gsea_kegg"))
        records(advanced["limma"]["rows"], "feature")
        records(advanced["missingness"]["rows"], "feature")
        for key in ("go", "kegg", "gsea_go", "gsea_kegg"):
            records(advanced["enrichment"][key], "Description")
        summary[modality] = {
            "modelled": advanced["input"]["modelled_protein_count"],
            "limma": advanced["limma"]["significant_count"],
            "group_specific_missing": advanced["filtering"]["group_specific_missing_count"],
        }
    else:
        advanced = result["advanced_metabolomics"]
        assert advanced["input"]["retained_feature_count"] >= 50
        assert advanced["plsda"]["permutation_count"] == 100
        assert advanced["enrichment"]["mapping"]["success_fraction"] >= 0.8
        assert advanced["enrichment"]["gsea"]
        records(advanced["pca"]["points"], "sample")
        records(advanced["plsda"]["points"], "sample")
        records(advanced["plsda"]["vip"], "feature")
        records(advanced["limma"]["rows"], "feature")
        records(advanced["enrichment"]["gsea"], "Description")
        summary[modality] = {
            "retained": advanced["input"]["retained_feature_count"],
            "balanced_accuracy": advanced["plsda"]["balanced_accuracy"],
            "permutation_p": advanced["plsda"]["permutation_p_value"],
            "limma": advanced["limma"]["significant_count"],
        }

print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
