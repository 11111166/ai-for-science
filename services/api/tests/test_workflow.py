import os
import shutil
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import pytest
from pydantic import ValidationError

os.environ.update({
    "APP_ENV": "test",
    "AUTH_PROVIDER": "dev",
    "DATABASE_URL": "sqlite:///./test_drug_discovery.db",
    "JOB_EXECUTION_MODE": "inline",
    "OBJECT_STORAGE_MODE": "local",
    "UPLOAD_DIR": "./test_uploads",
})

from fastapi.testclient import TestClient

from app.database import Base, SessionLocal, engine
from app.config import Settings
from app.main import app
from app.models import Job
from app.routes import REUSABLE_ANALYSIS_JOB_TYPES


client = TestClient(app)
HEADERS = {"X-Dev-User": "alice", "X-Dev-Role": "researcher"}


TEST_DB_MARKER = "test_drug_discovery.db"


def _engine_is_test_database() -> bool:
    """Guard: never drop tables unless the engine really points at the throwaway DB.

    ``app.database`` builds its engine from the first ``Settings`` load, so when
    another test module imports it first this module silently inherits that
    engine. Dropping through it would wipe a real database.
    """
    return TEST_DB_MARKER in str(engine.url)


def setup_module():
    if not _engine_is_test_database():
        pytest.skip(f"engine is bound to {engine.url}; refusing to modify a non-test database")
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def teardown_module():
    if not _engine_is_test_database():
        return
    Base.metadata.drop_all(engine)
    Path("test_drug_discovery.db").unlink(missing_ok=True)
    shutil.rmtree("test_uploads", ignore_errors=True)


def request(method, path, **kwargs):
    headers = {**HEADERS, **kwargs.pop("headers", {})}
    response = client.request(method, f"/api/v1/drug-discovery{path}", headers=headers, **kwargs)
    assert response.status_code < 400, response.text
    return response


def test_auth_me_and_logout_support_development_provider():
    me = request("GET", "/auth/me").json()
    assert me == {"username": "alice", "display_name": "alice", "role": "研发用户"}
    response = client.post("/api/v1/drug-discovery/auth/logout", headers=HEADERS)
    assert response.status_code == 204
    cookie = response.headers["set-cookie"]
    assert "path=/apps/ai4science" in cookie
    assert "Path=/" not in cookie


def submit(project_id, path, mode="precomputed", parameters=None):
    response = request("POST", f"/projects/{project_id}/{path}", json={"adapter_mode": mode, "input_version": "test-v1", "parameters": parameters or {}})
    assert response.status_code == 202
    job = request("GET", f"/jobs/{response.json()['job_id']}").json()
    assert job["status"] == "completed"
    return job


def test_complete_ipf_workflow():
    project = request("POST", "/projects", json={"name": "IPF Workflow Test", "disease": "IPF", "objective": "Validate complete workflow"}).json()
    project_id = project["id"]

    submit(project_id, "target-discovery-jobs")
    targets = request("GET", f"/projects/{project_id}/targets").json()
    assert len(targets) == 5
    request("PATCH", f"/targets/{targets[0]['id']}", json={"selected": True, "note": "Review next"})

    submit(project_id, "perturbation-jobs")
    perturbations = request("GET", f"/projects/{project_id}/perturbations").json()
    assert len(perturbations) == 1

    submit(project_id, "compound-matching-jobs")
    compounds = request("GET", f"/projects/{project_id}/compounds").json()
    assert len(compounds) == 3
    assert all(item["evaluation"] is None for item in compounds)

    submit(project_id, "admet-jobs")
    compounds = request("GET", f"/projects/{project_id}/compounds").json()
    assert all(item["evaluation"] for item in compounds)

    submit(project_id, "ranking-jobs", parameters={"weights": {"compound_match": 35, "efficacy": 40, "safety": 15, "developability": 10}})
    rankings = request("GET", f"/projects/{project_id}/candidates").json()
    assert [item["rank"] for item in rankings] == [1, 2, 3]
    assert rankings[0]["weights"]["efficacy"] == pytest.approx(0.4)
    assert rankings[0]["compound"]["evaluation"]
    request("PATCH", f"/rankings/{rankings[0]['id']}", json={"decision": "selected", "rationale": "Workflow test"})

    report = request("POST", f"/projects/{project_id}/reports", json={}).json()
    assert report["version"] == 1
    html = request("GET", f"/reports/{report['id']}")
    assert "综合候选排序与人工决策" in html.text
    assert "方法与适用边界" in html.text
    assert "任务与版本追溯" in html.text
    assert "不构成实验、临床、医疗或投资结论" in html.text
    pdf = request("GET", f"/reports/{report['id']}/pdf")
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF")

    jobs = request("GET", f"/projects/{project_id}/jobs").json()
    assert len(jobs) == 5
    assert all(job["result_status"] == "demonstration" for job in jobs)


def test_completed_analysis_jobs_are_reused_across_modules():
    expected = {
        "target_discovery", "perturbation", "gene_knockout", "virtual_cell",
        "compound_matching", "admet", "ranking", "omics_analysis",
        "fungal_target", "fungal_compound",
    }
    assert REUSABLE_ANALYSIS_JOB_TYPES == expected
    project = request("POST", "/projects", json={"name": "Cache coverage", "disease": "IPF", "objective": "Reuse every analysis module"}).json()
    paths = {
        "perturbation": "perturbation-jobs",
        "gene_knockout": "gene-knockout-jobs",
        "virtual_cell": "virtual-cell-jobs",
        "compound_matching": "compound-matching-jobs",
        "admet": "admet-jobs",
        "ranking": "ranking-jobs",
        "fungal_compound": "fungal-compound-jobs",
    }
    cached_ids = {}
    parameters = {
        "perturbation": {}, "gene_knockout": {}, "virtual_cell": {},
        "compound_matching": {}, "admet": {}, "ranking": {},
        "fungal_compound": {"target_ids": ["P12345"]},
    }
    with SessionLocal() as db:
        for job_type in paths:
            cached = Job(
                project_id=project["id"], type=job_type, status="completed", stage="completed", progress=100,
                adapter_mode="precomputed", parameters=parameters[job_type], input_version="cache-test-v1",
                output_ref=f"cached/{job_type}", requested_by="alice",
            )
            db.add(cached)
            db.flush()
            cached_ids[job_type] = cached.id
        db.commit()
    for job_type, path in paths.items():
        response = request("POST", f"/projects/{project['id']}/{path}", json={
            "adapter_mode": "precomputed", "input_version": "cache-test-v1", "parameters": parameters[job_type],
        })
        assert response.headers["X-AI4Science-Cache-Reused"] == "1"
        assert response.json()["cache_hit"] is True
        assert response.json()["job_id"] == cached_ids[job_type]


def test_scientific_data_source_registry_is_explicit():
    sources = request("GET", "/data-sources").json()
    connected = {item["id"] for item in sources if item["status"] == "connected_snapshot"}
    queued = {item["id"] for item in sources if item["status"] == "queued_integration"}
    assert {"open_targets", "gwas_catalog", "cellxgene", "hgnc", "hpa", "depmap"} <= connected
    assert {"reactome"} <= queued
    assert "chembl" in connected
    assert all(item["boundary"] and item["version"] for item in sources)


def test_project_profiles_hide_internal_validation_by_default():
    research = request("POST", "/projects", json={
        "name": "RA Research Project",
        "disease": "类风湿关节炎",
        "disease_id": "MONDO_0008383",
        "research_track": "full",
        "objective": "Product research",
    }).json()
    assert research["project_type"] == "research"
    assert research["disease_id"] == "MONDO_0008383"

    admin_headers = {"X-Dev-User": "release-bot", "X-Dev-Role": "admin"}
    validation = request("POST", "/projects", headers=admin_headers, json={
        "name": "Internal Release Validation",
        "disease": "类风湿关节炎",
        "project_type": "validation",
        "research_track": "target_discovery",
    }).json()
    visible = request("GET", "/projects?limit=100", headers=admin_headers).json()
    internal = request("GET", "/projects?project_type=validation&limit=100", headers=admin_headers).json()
    assert research["id"] in {item["id"] for item in visible}
    assert validation["id"] not in {item["id"] for item in visible}
    assert validation["id"] in {item["id"] for item in internal}


def test_batch_target_selection_is_scoped_to_one_discovery_run():
    project = request("POST", "/projects", json={"name": "Batch Selection Test", "disease": "IPF", "objective": "批量选择"}).json()
    job = submit(project["id"], "target-discovery-jobs")
    response = request("PATCH", f"/projects/{project['id']}/targets/selection", json={
        "job_id": job["id"], "mode": "top_n", "top_n": 3,
    }).json()
    assert response["selected_count"] == 3
    targets = request("GET", f"/projects/{project['id']}/targets?job_id={job['id']}").json()
    assert [item["rank"] for item in targets if item["selected"]] == [1, 2, 3]
    cleared = request("PATCH", f"/projects/{project['id']}/targets/selection", json={
        "job_id": job["id"], "mode": "clear", "top_n": 10,
    }).json()
    assert cleared["selected_count"] == 0


def test_not_configured_mode_is_honest_empty_state():
    project = request("POST", "/projects", json={"name": "Empty Adapter Test", "disease": "Generic disease", "objective": "Validate empty state"}).json()
    job = submit(project["id"], "target-discovery-jobs", mode="not_configured")
    assert job["result_status"] == "no_data"
    assert request("GET", f"/projects/{project['id']}/targets").json() == []


def test_cross_project_access_is_open():
    project = request("POST", "/projects", json={"name": "Private Project", "disease": "IPF", "objective": "RBAC"}).json()
    # 要求 27：权限全面放开，任何账号都能打开任意项目
    response = client.get(
        f"/api/v1/drug-discovery/projects/{project['id']}",
        headers={"X-Dev-User": "bob", "X-Dev-Role": "researcher"},
    )
    assert response.status_code == 200


def test_upload_retry_and_cancel_interfaces():
    project = request("POST", "/projects", json={"name": "Lifecycle Test", "disease": "IPF", "objective": "Jobs and assets"}).json()
    project_id = project["id"]
    upload = request(
        "POST",
        f"/projects/{project_id}/assets?kind=expression-matrix",
        files={"file": ("counts.csv", b"gene,sample\nTGFB1,10\n", "text/csv")},
    ).json()
    assert upload["format"] == "csv"
    assert upload["size"] > 0
    assert len(upload["content_hash"]) == 64

    completed = submit(project_id, "target-discovery-jobs")
    retried = request("POST", f"/jobs/{completed['id']}/retry").json()
    retried_job = request("GET", f"/jobs/{retried['job_id']}").json()
    assert retried_job["status"] == "completed"
    assert retried_job["retry_of"] == completed["id"]

    with SessionLocal() as db:
        queued = Job(project_id=project_id, type="ranking", requested_by="alice")
        db.add(queued)
        db.commit()
        db.refresh(queued)
        queued_id = queued.id
    cancelled = request("POST", f"/jobs/{queued_id}/cancel").json()
    assert cancelled["status"] == "cancelled"


def test_development_auth_is_rejected_in_production():
    with pytest.raises(ValidationError):
        Settings(app_env="production", auth_provider="dev")


def test_real_target_discovery_contract_and_legacy_runtime_alias(monkeypatch):
    class FakeOpenTargetsAdapter:
        def __init__(self, db):
            self.db = db

        def close(self):
            return None

        def discover(self, query_text, top_n=50, include_indirect=True, include_evidence_records=False):
            retrieved_at = datetime.now(timezone.utc)
            categories = {
                name: {"status": "available" if name == "genetics" else "not_configured" if name in {"safety", "druggability"} else "unavailable", "score": 0.8 if name == "genetics" else None, "datatypes": ["genetic_association"] if name == "genetics" else []}
                for name in ("genetics", "expression", "pathway", "functional", "known_drug", "literature", "safety", "druggability")
            }
            target = {
                "external_id": "ENSG00000000001", "symbol": "GENE1", "name": "gene one", "biotype": "protein_coding",
                "rank": 1, "score": 0.91, "score_origin": "open_targets_association_source_score", "derived_score": 0.16,
                "evidence_coverage": 0.125, "evidence_count": 1, "source_count": 1,
                "evidence_breakdown": {"registry_version": "v1", "categories": categories, "datatype_scores": {"genetic_association": 0.8}, "datasource_scores": {"eva": 0.8}},
                "direction": "not_available", "raw_reference": "file:///raw/association.json.gz",
            }
            evidence = {
                "target_external_id": target["external_id"], "external_evidence_id": "EV1", "disease_id": "EFO_123",
                "evidence_type": "source_evidence", "datatype": "genetic_association", "datasource": "eva", "category": "genetics",
                "source": "eva", "summary": "Evidence", "score": 0.8, "direction_on_target": "LoF", "direction_on_trait": "risk",
                "evidence_count": 1, "confidence": "high", "raw_reference": "file:///raw/evidence.json.gz#EV1", "details": {},
            }
            return {
                "metadata": {"source": "Open Targets Platform", "source_version": "26.06", "api_version": "26.6.3", "endpoint": "https://example.test/graphql"},
                "resolution": {"query": query_text, "canonical_name": "test disease", "disease_id": "EFO_123", "ontology": "EFO", "synonyms": ["TD"], "source": "Open Targets Platform", "resolution_status": "resolved", "resolution_confidence": 0.95, "resolution_method": "test", "raw_reference": "file:///raw/search.json.gz", "cache_hit": False},
                "parameters": {"top_n": top_n, "include_indirect": include_indirect}, "targets": [target], "evidence": [evidence],
                "data_quality": {"disease_resolution_status": "resolved", "canonical_disease_id": "EFO_123", "targets_retrieved": 1, "warnings": [], "errors": []},
                "benchmark": {"status": "not_yet_validated", "reason": "test"}, "warnings": [],
                "raw_reference": "file:///raw/association.json.gz", "retrieved_at": retrieved_at,
            }

    monkeypatch.setattr("app.jobs.OpenTargetsAdapter", FakeOpenTargetsAdapter)
    project = request("POST", "/projects", json={"name": "Generic Target Test", "disease": "TD", "objective": "Real adapter contract"}).json()
    accepted = request(
        "POST",
        f"/projects/{project['id']}/target-discovery-jobs",
        json={"adapter_mode": "runtime", "parameters": {"top_n": 10, "disease_query": "TD"}},
    ).json()
    job = request("GET", f"/jobs/{accepted['job_id']}").json()
    assert job["status"] == "completed"
    assert job["adapter_mode"] == "real"
    assert job["result_status"] == "real"
    assert request("GET", f"/projects/{project['id']}").json()["disease_id"] == "EFO_123"

    result = request("GET", f"/jobs/{job['id']}/target-discovery-result").json()
    assert result["disease_id"] == "EFO_123"
    assert result["source_version"] == "26.06"
    targets = request("GET", f"/projects/{project['id']}/targets").json()
    assert targets[0]["external_id"] == "ENSG00000000001"
    assert targets[0]["score_origin"] == "open_targets_association_source_score"
    detail = request("GET", f"/targets/{targets[0]['id']}").json()
    assert detail["evidence_total"] == 1
    evidence = request("GET", f"/targets/{targets[0]['id']}/evidence?category=genetics").json()
    assert evidence["total"] == 1
    assert evidence["items"][0]["score"] == 0.8


def test_real_target_failure_never_falls_back_to_precomputed(monkeypatch):
    from app.open_targets import OpenTargetsError

    class FailingOpenTargetsAdapter:
        def __init__(self, db):
            self.db = db

        def close(self):
            return None

        def discover(self, *args, **kwargs):
            raise OpenTargetsError("OPEN_TARGETS_UNAVAILABLE", "Source unavailable in test.")

    monkeypatch.setattr("app.jobs.OpenTargetsAdapter", FailingOpenTargetsAdapter)
    project = request("POST", "/projects", json={"name": "No Fallback Test", "disease": "IPF", "objective": "Honest failure"}).json()
    accepted = request(
        "POST",
        f"/projects/{project['id']}/target-discovery-jobs",
        json={"adapter_mode": "real", "parameters": {"top_n": 10}},
    ).json()
    job = request("GET", f"/jobs/{accepted['job_id']}").json()
    assert job["status"] == "failed"
    assert job["error_code"] == "OPEN_TARGETS_UNAVAILABLE"
    assert request("GET", f"/projects/{project['id']}/targets").json() == []


def test_novel_target_strategy_uses_its_own_snapshot_without_baseline_fallback(monkeypatch, tmp_path):
    snapshot = tmp_path / "novel-target.json"
    snapshot.write_text(json.dumps({
        "schema_version": "novel-target-discovery-product-snapshot-v1", "snapshot_id": "novel-job-test-v1",
        "created_at": "2026-09-03T00:00:00+00:00", "scientific_status": "computational_hypothesis",
        "source_registry_version": "novel-sources-v1", "score_contract": {"version": "novel-target-score-v1"},
        "disease": {"id": "MONDO_0008383", "name": "rheumatoid arthritis", "name_zh": "类风湿关节炎"},
        "quality": {"build": {"counts": {}}, "benchmark": {"status": "computed", "top20_multimodal_count": 1, "permutation_p_value": 0.05}},
        "targets": [{
            "target_id": "ENSG00000000001", "symbol": "GENE1", "approved_name": "gene one", "rank": 1,
            "novel_target_score": 0.7, "evidence_strength": 0.6, "novelty_score": 0.8, "development_feasibility": 0.5,
            "gwas_score": 0.7, "eqtl_score": 0.6, "single_cell_score": 0.4, "network_score": 0.3,
            "direct_modality_count": 3, "candidate_origin": "GWAS+eQTL+single-cell", "novelty_class": "emerging",
            "suggested_direction": "inhibit", "known_drug_label": 0,
        }, {
            "target_id": "ENSG00000000002", "symbol": "GENE2", "approved_name": "gene two", "rank": 2,
            "novel_target_score": 0.6, "evidence_strength": 0.5, "novelty_score": 0.8, "development_feasibility": 0.4,
            "gwas_score": 0.5, "eqtl_score": 0.4, "single_cell_score": 0.3, "network_score": 0.2,
            "direct_modality_count": 3, "candidate_origin": "GWAS+eQTL+single-cell", "novelty_class": "emerging",
            "suggested_direction": "inhibit", "known_drug_label": 0,
        }],
    }), encoding="utf-8")
    perturbation_snapshot = tmp_path / "perturbation-knockout.json"
    perturbation_snapshot.write_text(json.dumps({
        "schema_version": "perturbation-knockout-product-snapshot-v1", "snapshot_id": "perturb-ko-test-v1",
        "quality_passed": True, "source_registry_version": "sources-test-v1",
        "upstream_target_snapshot": {"snapshot_id": "novel-job-test-v1", "sha256": "abc"},
        "disease": {"id": "MONDO_0008383", "name": "rheumatoid arthritis", "name_zh": "类风湿关节炎"},
        "virtual_intervention": {
            "scientific_status": "model_based_hypothesis", "model_version": "intervention-test-v1",
            "benchmark": {"quality_gates": {"passed": True}, "candidate_count": 1},
            "results": [{"target_id": "ENSG00000000001", "symbol": "GENE1", "direction": "inhibit",
                         "cell_type": "synovial fibroblast", "virtual_intervention_score": 0.71,
                         "confidence": 0.68, "evidence_grade": "high", "direct_modality_count": 3},
                        {"target_id": "ENSG00000000002", "symbol": "GENE2", "direction": "inhibit",
                         "cell_type": "synovial fibroblast", "virtual_intervention_score": 0.61,
                         "confidence": 0.58, "evidence_grade": "moderate", "direct_modality_count": 3}],
        },
        "gene_knockout": {
            "scientific_status": "measured_cell_fitness_out_of_domain_proxy", "model_version": "knockout-test-v1",
            "benchmark": {"quality_gates": {"passed": True}, "control_auroc": 0.99, "control_auprc": 0.98,
                          "screened_models": 1000, "hematopoietic_proxy_models": 120},
            "results": [{"target_id": "ENSG00000000001", "symbol": "GENE1", "screened": True,
                         "pan_median_gene_effect": -0.15, "hematopoietic_median_gene_effect": -0.24,
                         "pan_dependency_fraction": 0.08, "universal_essentiality_risk": 0.06,
                         "knockout_tolerability": 0.94},
                        {"target_id": "ENSG00000000002", "symbol": "GENE2", "screened": False,
                         "reason": "gene_not_in_depmap_24q4"}],
        },
    }), encoding="utf-8")
    monkeypatch.setattr("app.jobs.get_settings", lambda: SimpleNamespace(
        novel_target_discovery_snapshot_path=str(snapshot), target_discovery_snapshot_path="",
        perturbation_knockout_snapshot_path=str(perturbation_snapshot),
    ))
    monkeypatch.setattr("app.routes.get_settings", lambda: SimpleNamespace(
        novel_target_catalog_path=str(tmp_path / "unconfigured-catalog"), novel_target_discovery_snapshot_path=str(snapshot), perturbation_knockout_snapshot_path=str(perturbation_snapshot), job_execution_mode="inline",
    ))
    project = request("POST", "/projects", json={"name": "Novel Target Job", "disease": "类风湿关节炎", "objective": "策略路由验证"}).json()
    available = request("GET", "/target-discovery-availability?disease_query=MONDO_0008383").json()
    assert available["available"] is True
    before = request("GET", f"/projects/{project['id']}/jobs").json()
    rejected = client.post(f"/api/v1/drug-discovery/projects/{project['id']}/target-discovery-jobs", headers=HEADERS, json={
        "adapter_mode": "real", "input_version": "test",
        "parameters": {"disease_query": "MONDO_0005148", "discovery_strategy": "novel"},
    })
    assert rejected.status_code == 409
    assert "多组学数据" in rejected.json()["detail"]
    assert request("GET", f"/projects/{project['id']}/jobs").json() == before
    accepted = request("POST", f"/projects/{project['id']}/target-discovery-jobs", json={
        "adapter_mode": "real", "input_version": "novel-v1",
        "parameters": {"disease_query": "类风湿关节炎", "top_n": 20, "discovery_strategy": "novel"},
    }).json()
    job = request("GET", f"/jobs/{accepted['job_id']}").json()
    assert job["status"] == "completed"
    targets = request("GET", f"/projects/{project['id']}/targets").json()
    assert targets[0]["score_origin"] == "ai4science_novel_target_score_v1"
    assert targets[0]["evidence_breakdown"]["discovery_strategy"] == "novel"
    result = request("GET", f"/jobs/{job['id']}/target-discovery-result").json()
    assert result["source_version"] == "novel-job-test-v1"
    assert result["benchmark_status"] == "computed"

    compatibility = request("GET", f"/projects/{project['id']}/target-compatibility?target_job_id={job['id']}&module=virtual_intervention&top_n=10").json()
    assert compatibility["compatible"] is True
    assert compatibility["computable_symbols"] == ["GENE1", "GENE2"]
    assert compatibility["selection_mode"] == "top_n_default"

    knockout_compatibility = request("GET", f"/projects/{project['id']}/target-compatibility?target_job_id={job['id']}&module=gene_knockout&top_n=10").json()
    assert knockout_compatibility["computable_symbols"] == ["GENE1"]
    assert knockout_compatibility["unavailable_symbols"] == ["GENE2"]

    intervention_job = submit(project["id"], "perturbation-jobs", mode="real", parameters={"target_job_id": job["id"], "top_n": 10})
    assert intervention_job["result_status"] == "real"
    interventions = request("GET", f"/projects/{project['id']}/perturbations?module=virtual_intervention").json()
    assert len(interventions) == 2
    assert interventions[0]["result"]["module"] == "virtual_intervention"
    assert interventions[0]["reversal_score"] == 0.71
    assert interventions[0]["demo"] is False

    knockout_job = submit(project["id"], "gene-knockout-jobs", mode="real", parameters={"target_job_id": job["id"], "top_n": 10})
    assert knockout_job["result_status"] == "real"
    knockouts = request("GET", f"/projects/{project['id']}/perturbations?module=gene_knockout").json()
    assert knockouts[0]["result"]["module"] == "gene_knockout"
    assert knockouts[0]["result"]["knockout_tolerability"] == 0.94
    assert knockouts[0]["demo"] is False

    ipf_job = submit(project["id"], "target-discovery-jobs", mode="precomputed")
    mismatch = request("GET", f"/projects/{project['id']}/target-compatibility?target_job_id={ipf_job['id']}&module=gene_knockout&top_n=10").json()
    assert mismatch["compatible"] is False
    assert mismatch["reason_code"] == "UPSTREAM_DISEASE_MISMATCH"
    assert "Idiopathic pulmonary fibrosis" in mismatch["message"]

    # A real target run for a disease without a dedicated perturbation snapshot
    # remains usable through the explicitly labelled evidence baseline.
    mismatched_payload = json.loads(perturbation_snapshot.read_text(encoding="utf-8"))
    mismatched_payload["disease"] = {"id": "EFO_0000768", "name": "Idiopathic pulmonary fibrosis", "name_zh": "特发性肺纤维化"}
    perturbation_snapshot.write_text(json.dumps(mismatched_payload), encoding="utf-8")
    cross_disease_knockout = request("GET", f"/projects/{project['id']}/target-compatibility?target_job_id={job['id']}&module=gene_knockout&top_n=10").json()
    assert cross_disease_knockout["compatible"] is True
    assert cross_disease_knockout["analysis_tier"] == "gene_level_global"
    assert cross_disease_knockout["computable_symbols"] == ["GENE1"]
    fallback = request("GET", f"/projects/{project['id']}/target-compatibility?target_job_id={job['id']}&module=virtual_intervention&top_n=10").json()
    assert fallback["compatible"] is True
    assert fallback["analysis_tier"] == "evidence_baseline"
    assert fallback["computable_symbols"] == ["GENE1", "GENE2"]

    fallback_job = submit(project["id"], "perturbation-jobs", mode="real", parameters={"target_job_id": job["id"], "top_n": 10})
    assert fallback_job["result_status"] == "real"
    fallback_results = request("GET", f"/projects/{project['id']}/perturbations?module=virtual_intervention").json()
    assert len(fallback_results) == 2
    assert all(item["result"]["analysis_tier"] == "evidence_baseline" for item in fallback_results)
    assert all(item["result"]["direction_status"] == "upstream_supported" for item in fallback_results)


def test_drug_catalog_routes_validate_identity_and_open_access(monkeypatch):
    from app import drug_catalog
    monkeypatch.setattr(drug_catalog, 'lookup', lambda kind, value: {'status':'available','items':[], 'lookup':kind,'value':value})
    project=request('POST','/projects',json={'name':'Drug dossier auth','disease':'IPF'}).json()
    submit(project['id'],'target-discovery-jobs')
    target=request('GET',f"/projects/{project['id']}/targets").json()[0]
    opened=client.get(f"/api/v1/drug-discovery/targets/{target['id']}/molecules",headers={'X-Dev-User':'outsider','X-Dev-Role':'researcher'})
    assert opened.status_code==200
    assert client.get(f"/api/v1/drug-discovery/projects/{project['id']}/qsar",headers={'X-Dev-User':'outsider','X-Dev-Role':'researcher'}).status_code==200
    demo=request('GET',f"/targets/{target['id']}/molecules").json()
    assert demo['items']==[] and '演示' in demo['warnings'][0]
    assert client.get('/api/v1/drug-discovery/molecules/invalid/dossier',headers=HEADERS).status_code==422
    assert request('GET','/molecules/chembl1/dossier').json()['value']=='CHEMBL1'


def test_catalog_exploration_job_completes_with_evidence_and_explicit_tier(monkeypatch, tmp_path):
    from test_novel_catalog import make_catalog
    make_catalog(tmp_path)
    settings = Settings(novel_target_discovery_snapshot_path="", novel_target_catalog_path=str(tmp_path), job_execution_mode="inline")
    monkeypatch.setattr("app.routes.get_settings", lambda: settings)
    monkeypatch.setattr("app.jobs.get_settings", lambda: settings)
    project = request("POST", "/projects", json={"name":"Public exploration", "disease":"测试疾病", "objective":"验证目录探索"}).json()
    availability = request("GET", "/target-discovery-availability?disease_query=MONDO_TEST").json()
    assert availability["analysis_tier"] == "public_evidence_exploration"
    job = submit(project["id"], "target-discovery-jobs", mode="real", parameters={"disease_query":"MONDO_TEST", "discovery_strategy":"novel"})
    result = request("GET", f"/jobs/{job['id']}/target-discovery-result").json()
    assert result["data_quality"]["analysis_tier"] == "public_evidence_exploration"
    assert result["benchmark_status"] == "not_validated"
    targets = request("GET", f"/projects/{project['id']}/targets").json()
    assert len(targets) == 1 and targets[0]["direction"] == "not_available"
    evidence = request("GET", f"/targets/{targets[0]['id']}/evidence").json()
    assert evidence["total"] == 1
