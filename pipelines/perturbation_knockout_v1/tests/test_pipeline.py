import json

import pandas as pd

from perturbation_knockout_v1.pipeline import Pipeline


def test_builds_both_modules_and_passes_independent_gates(tmp_path):
    target_path = tmp_path / "novel-target.json"
    targets = []
    for index in range(20):
        targets.append({
            "target_id": f"ENSG{index:011d}", "symbol": f"GENE{index}", "approved_name": f"gene {index}", "rank": index + 1,
            "single_cell_log2fc": 1.0, "single_cell_score": 0.8, "single_cell_fdr": 0.01,
            "gwas_score": 0.6, "eqtl_score": 0.4, "network_score": 0.5,
            "best_cell_type": "synovial fibroblast", "case_donors": 12, "control_donors": 12,
            "known_drug_label": int(index < 2),
        })
    target_path.write_text(json.dumps({
        "schema_version": "novel-target-discovery-product-snapshot-v1", "snapshot_id": "novel-test-v1",
        "disease": {"id": "MONDO_0008383", "name": "rheumatoid arthritis", "name_zh": "类风湿关节炎"},
        "targets": targets,
    }), encoding="utf-8")

    pipeline = Pipeline(tmp_path / "workspace", target_path)
    pipeline.ensure_layout()
    models = [f"ACH-{index:06d}" for index in range(800)]
    pd.DataFrame({
        "ModelID": models,
        "OncotreeLineage": ["Lymphoid" if index < 120 else "Lung" for index in range(800)],
    }).to_csv(pipeline.raw_root / "Model.csv", index=False)
    pd.DataFrame({"Gene": ["CE1", "CE2"]}).to_csv(pipeline.raw_root / "AchillesCommonEssentialControls.csv", index=False)
    pd.DataFrame({"Gene": ["NE1", "NE2"]}).to_csv(pipeline.raw_root / "AchillesNonessentialControls.csv", index=False)
    effects = {"ModelID": models, "CE1 (1)": [-1.1] * 800, "CE2 (2)": [-0.9] * 800,
               "NE1 (3)": [0.0] * 800, "NE2 (4)": [0.1] * 800}
    for index in range(20):
        effects[f"GENE{index} ({1000 + index})"] = [-0.1 - index / 1000] * 800
    pd.DataFrame(effects).to_csv(pipeline.raw_root / "CRISPRGeneEffect.csv", index=False)
    pipeline.download_manifest.write_text("{}", encoding="utf-8")

    manifest = pipeline.build()
    payload = json.loads(pipeline.release_path.read_text(encoding="utf-8"))
    assert manifest["quality_passed"] is True
    assert payload["virtual_intervention"]["benchmark"]["quality_gates"]["passed"] is True
    assert payload["gene_knockout"]["benchmark"]["quality_gates"]["passed"] is True
    assert payload["gene_knockout"]["benchmark"]["control_auroc"] == 1.0
    assert len(payload["virtual_intervention"]["results"]) == 20
    assert len(payload["gene_knockout"]["results"]) == 20
    confidence = {row["confidence"] for row in payload["virtual_intervention"]["results"]}
    risks = [row["cell_line_dependency_risk"] for row in payload["gene_knockout"]["results"]]
    assert all(0 < value < 1 for value in confidence)
    assert len(set(risks)) > 10
    assert all(0 < value < 1 for value in risks)
