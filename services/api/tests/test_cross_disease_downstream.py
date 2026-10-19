from types import SimpleNamespace

import httpx

from app.chembl_runtime import ChEMBLRuntimeAdapter
from app.virtual_cell_baseline import build_virtual_cell_baselines, disease_context_profile


def target(symbol="APP", rank=1):
    return SimpleNamespace(
        id=f"target-{symbol}", symbol=symbol, name=f"{symbol} full name", rank=rank,
        score=0.72, causal_confidence=0.66, disease_context=0.58,
        evidence_coverage=0.50, source_count=3, direction="inhibit",
        evidence_breakdown={"target_to_hit": {"has_small_molecule_binder": True}},
    )


def test_alzheimer_context_baseline_is_differentiated_and_explicit():
    rows, benchmark, qc, provenance = build_virtual_cell_baselines(
        [target("APP", 1), target("TREM2", 2), target("SORL1", 3)],
        "Alzheimer disease",
        {"target-SORL1": {"direction": "activate", "direction_confidence": 0.88}},
    )
    assert disease_context_profile("Alzheimer disease")[0] == "神经系统"
    assert len(rows) == 3
    assert all(row["analysis_tier"] == "context_prior_baseline" for row in rows)
    assert all(row["best_cell_type"] in {"小胶质细胞", "兴奋性神经元", "星形胶质细胞"} for row in rows)
    assert rows[2]["direction"] == "activate"
    assert len({row["context_priority_score"] for row in rows}) == 3
    assert benchmark["quality_gates"]["passed"] is True
    assert qc["dedicated_single_cell_data"] is False
    assert provenance["scientific_conclusion"] is False
    assert "不是单细胞表达实测" in rows[0]["boundary"]


def test_chembl_runtime_returns_strict_activity_structure_and_direction():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/target/search.json"):
            return httpx.Response(200, json={"targets": [{
                "target_chembl_id": "CHEMBL2487", "pref_name": "Amyloid-beta precursor protein",
                "organism": "Homo sapiens", "target_type": "SINGLE PROTEIN",
                "target_components": [{"target_component_synonyms": [{"syn_type": "GENE_SYMBOL", "component_synonym": "APP"}]}],
            }]})
        if path.endswith("/activity.json"):
            return httpx.Response(200, json={"activities": [
                {"molecule_chembl_id": "CHEMBL100", "pchembl_value": "8.2", "assay_chembl_id": "A1", "document_chembl_id": "D1", "standard_type": "IC50"},
                {"molecule_chembl_id": "CHEMBL100", "pchembl_value": "7.8", "assay_chembl_id": "A2", "document_chembl_id": "D2", "standard_type": "Ki"},
            ]})
        if path.endswith("/molecule/CHEMBL100.json"):
            return httpx.Response(200, json={
                "molecule_chembl_id": "CHEMBL100", "pref_name": "TEST COMPOUND", "max_phase": 2,
                "molecule_structures": {"canonical_smiles": "CCO"},
                "molecule_properties": {"full_mwt": "46.1", "alogp": "-0.1", "psa": "20.2", "hbd": 1, "hba": 1, "rtb": 0, "qed_weighted": "0.41", "num_ro5_violations": 0},
            })
        if path.endswith("/mechanism.json"):
            return httpx.Response(200, json={"mechanisms": [{"target_chembl_id": "CHEMBL2487", "action_type": "INHIBITOR", "mechanism_of_action": "APP inhibitor"}]})
        return httpx.Response(404, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = ChEMBLRuntimeAdapter(client=client, max_workers=1)
    result = adapter.discover([target("APP")], {"APP": "inhibit"}, max_targets=1, compounds_per_target=2)
    adapter.close()
    assert result["benchmark"]["chembl_activity_targets"] == 1
    assert result["benchmark"]["qualified_compounds"] == 1
    compound = result["compounds"][0]
    assert compound["molecule_chembl_id"] == "CHEMBL100"
    assert compound["canonical_smiles"] == "CCO"
    assert compound["target_matches"][0]["direction_evidence"] == 1.0
    assert compound["evaluation_tier"] == "experimental_activity_plus_chembl_properties"
    assert compound["admet_predictions"] == {}


def test_direction_matching_distinguishes_antagonist_inverse_and_conflicts():
    def assess(action, direction="activate", text=""):
        return ChEMBLRuntimeAdapter._direction_evidence(direction, [{
            "target_chembl_id": "T", "action_type": action, "mechanism_of_action": text,
        }], "T")
    assert assess("ANTAGONIST")[0] == 0.25
    assert assess("ANTAGONIST", "inhibit")[0] == 1.0
    assert assess("INVERSE AGONIST")[0] == 0.25
    assert assess("INVERSE AGONIST", "inhibit")[0] == 1.0
    assert assess("PARTIAL AGONIST")[0] == 1.0
    assert assess("AGONIST", "unknown")[0] == 0.5
    assert assess("", text="not an agonist")[0] == 0.5
    assert assess("BINDING AGENT")[0] == 0.5
    score, _, note = assess("AGONIST | ANTAGONIST")
    assert score == 0.25 and "冲突" in note
    assert ChEMBLRuntimeAdapter._direction_evidence("activate", [{"target_chembl_id": "OTHER", "action_type": "AGONIST"}], "T")[0] == 0.5
