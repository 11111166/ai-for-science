import os
from pathlib import Path

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("AUTH_PROVIDER", "dev")
os.environ.setdefault("OBJECT_STORAGE_MODE", "local")
os.environ.setdefault("UPLOAD_DIR", "./test_open_targets_uploads")

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.open_targets import (
    EVIDENCE_CATEGORY_REGISTRY,
    OpenTargetsAdapter,
    OpenTargetsError,
    build_benchmark,
    normalize_disease_text,
)


def payload(data):
    return httpx.Response(200, json={"data": data})


@pytest.fixture()
def session(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    from app.config import get_settings

    get_settings.cache_clear()
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as db:
        yield db
    get_settings.cache_clear()


def test_normalize_disease_text_handles_possessives_and_hyphens():
    assert normalize_disease_text(" Alzheimer's disease ") == "alzheimer disease"
    assert normalize_disease_text("Non-small-cell lung carcinoma") == "non small cell lung carcinoma"
    assert normalize_disease_text(" 类风湿关节炎 ") == "类风湿关节炎"


def test_adapter_resolves_normalizes_caches_and_benchmarks(session):
    calls = []

    def handler(request: httpx.Request):
        body = __import__("json").loads(request.content)
        query = body["query"]
        variables = body["variables"]
        calls.append((query, variables))
        if "SourceMetadata" in query:
            return payload({"meta": {"product": "platform", "name": "OT", "dataPrefix": "platform2606", "apiVersion": {"x": "26", "y": "6", "z": "3", "suffix": None}, "dataVersion": {"year": "26", "month": "06", "iteration": None}}})
        if "DiseaseSearch" in query:
            return payload({"search": {"total": 1, "hits": [{"id": "EFO_1", "name": "test disease", "entity": "disease", "score": 10.0, "description": "test"}]}})
        if "DiseaseDetails" in query:
            return payload({"disease": {"id": "EFO_1", "name": "test disease", "synonyms": [{"relation": "hasExactSynonym", "terms": ["TD"]}]}})
        if "AssociatedTargets" in query:
            return payload({"disease": {"id": "EFO_1", "name": "test disease", "associatedTargets": {"count": 2, "rows": [
                {"score": 0.9, "novelty": None, "target": {"id": "ENSG1", "approvedSymbol": "GENE1", "approvedName": "gene one", "biotype": "protein_coding"}, "datatypeScores": [{"id": "genetic_association", "score": 0.8}, {"id": "known_drug", "score": 0.7}], "datasourceScores": [{"id": "eva", "score": 0.8}]},
                {"score": 0.7, "novelty": None, "target": {"id": "ENSG2", "approvedSymbol": "GENE2", "approvedName": "gene two", "biotype": "protein_coding"}, "datatypeScores": [{"id": "rna_expression", "score": 0.6}], "datasourceScores": [{"id": "expression_atlas", "score": 0.6}]},
            ]}}})
        if "TargetEvidence" in query:
            return payload({"disease": {"evidences": {"count": 2, "cursor": None, "rows": [
                {"id": "EV1", "score": 0.8, "resourceScore": 0.8, "datatypeId": "genetic_association", "datasourceId": "eva", "confidence": "high", "releaseVersion": None, "directionOnTarget": "LoF", "directionOnTrait": "risk", "literature": ["1"], "studyId": "S1", "publicationYear": 2025, "target": {"id": "ENSG1", "approvedSymbol": "GENE1"}, "disease": {"id": "EFO_1", "name": "test disease"}, "drug": None},
                {"id": "EV2", "score": 0.6, "resourceScore": 0.6, "datatypeId": "rna_expression", "datasourceId": "expression_atlas", "confidence": None, "releaseVersion": None, "directionOnTarget": None, "directionOnTrait": None, "literature": [], "studyId": None, "publicationYear": None, "target": {"id": "ENSG2", "approvedSymbol": "GENE2"}, "disease": {"id": "EFO_1", "name": "test disease"}, "drug": None},
            ]}}})
        raise AssertionError(query)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = OpenTargetsAdapter(session, client=client)
    exact = adapter.resolve_disease("EFO_1")
    assert exact["disease_id"] == "EFO_1"
    assert exact["resolution_method"] == "open_targets_exact_ontology_id"
    assert not any("DiseaseSearch" in query for query, _ in calls)
    first = adapter.discover("TD", top_n=2, include_evidence_records=True)
    first_call_count = len(calls)
    session.commit()
    second = adapter.discover("TD", top_n=2, include_evidence_records=True)

    assert first["resolution"]["disease_id"] == "EFO_1"
    assert first["metadata"]["source_version"] == "26.06"
    assert first["targets"][0]["external_id"] == "ENSG1"
    assert first["targets"][0]["evidence_breakdown"]["categories"]["safety"]["status"] == "not_configured"
    assert first["targets"][0]["evidence_coverage"] == 0.25
    assert first["targets"][0]["derived_score"] == 0.16
    assert first["benchmark"]["status"] == "computed"
    assert len(first["evidence"]) == 2
    assert first["evidence"][0]["raw_reference"].endswith("#EV1")
    assert second["data_quality"]["cache_hit"] is True
    assert len(calls) == first_call_count
    assert len(EVIDENCE_CATEGORY_REGISTRY) == 8


def test_resolver_refuses_search_only_guess(session):
    def handler(request: httpx.Request):
        query = __import__("json").loads(request.content)["query"]
        if "SourceMetadata" in query:
            return payload({"meta": {"product": "platform", "name": "OT", "dataPrefix": "platform2606", "apiVersion": {"x": "26", "y": "6", "z": "3", "suffix": None}, "dataVersion": {"year": "26", "month": "06", "iteration": None}}})
        if "DiseaseSearch" in query:
            return payload({"search": {"total": 1, "hits": [{"id": "EFO_2", "name": "different disease", "entity": "disease", "score": 2.0, "description": None}]}})
        return payload({"disease": {"id": "EFO_2", "name": "different disease", "synonyms": []}})

    adapter = OpenTargetsAdapter(session, client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(OpenTargetsError, match="DISEASE_AMBIGUOUS") as error:
        adapter.resolve_disease("vague query")
    assert error.value.code == "DISEASE_AMBIGUOUS"


def test_directional_evidence_is_bounded_per_target_and_cached(session):
    calls = []

    def handler(request: httpx.Request):
        body = __import__("json").loads(request.content)
        query, variables = body["query"], body["variables"]
        calls.append((query, variables))
        if "SourceMetadata" in query:
            return payload({"meta": {"product": "platform", "name": "OT", "dataPrefix": "platform2606", "apiVersion": {"x": "26", "y": "6", "z": "3", "suffix": None}, "dataVersion": {"year": "26", "month": "06", "iteration": None}}})
        if "DirectionEvidence" in query:
            assert "target_ids" not in variables
            assert variables["size"] == 25
            return payload({"disease": {
                "e0": {"count": 2, "rows": [{"id": "D1", "score": 0.9, "datatypeId": "genetic_association", "datasourceId": "gene_burden", "directionOnTarget": "LoF", "directionOnTrait": "risk", "target": {"id": "ENSG1", "approvedSymbol": "GENE1"}}]},
                "e1": {"count": 1, "rows": [{"id": "D2", "score": 0.7, "datatypeId": "clinical", "datasourceId": "clinical_precedence", "directionOnTarget": None, "directionOnTrait": "protect", "target": {"id": "ENSG2", "approvedSymbol": "GENE2"}}]},
            }})
        raise AssertionError(query)

    adapter = OpenTargetsAdapter(session, client=httpx.Client(transport=httpx.MockTransport(handler)))
    first, meta = adapter.get_directional_evidence("EFO_1", ["ENSG1", "ENSG2"])
    session.commit()
    call_count = len(calls)
    second, cached_meta = adapter.get_directional_evidence("EFO_1", ["ENSG1", "ENSG2"])
    assert list(first) == ["ENSG1"]
    assert first["ENSG1"][0]["direction_on_target"] == "LoF"
    assert meta["directional_target_count"] == 1
    assert second == first
    assert cached_meta["cache_hit"] is True
    assert len(calls) == call_count


def test_benchmark_does_not_invent_metrics_without_reference():
    targets = [{"external_id": "ENSG1", "score": 0.8, "derived_score": None, "evidence_breakdown": {"categories": {"known_drug": {"status": "unavailable"}}}}]
    assert build_benchmark(targets)["status"] == "not_yet_validated"


def test_exact_orphanet_identifier_preserves_source_namespace_case(session):
    def handler(request):
        import json
        body=json.loads(request.content)
        if 'SourceMetadata' in body['query']:
            return payload({'meta':{'product':'platform','name':'OT','dataPrefix':'2606','apiVersion':{'x':'26','y':'6','z':'3','suffix':None},'dataVersion':{'year':'26','month':'06','iteration':None}}})
        assert body['variables']['id']=='Orphanet_43'
        return payload({'disease':{'id':'Orphanet_43','name':'X-linked adrenoleukodystrophy','synonyms':[]}})
    adapter=OpenTargetsAdapter(session,client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert adapter.resolve_disease('Orphanet_43')['disease_id']=='Orphanet_43'
