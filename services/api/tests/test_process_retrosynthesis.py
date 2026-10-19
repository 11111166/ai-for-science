from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth import UserContext, get_current_user
from app.database import Base, get_db
from app.models import Project
from app.process_retrosynthesis import RetrosynthesisInput, analyze, router


def test_aspirin_structure_disconnection_is_not_misrepresented_as_patent_route():
    result = analyze(RetrosynthesisInput(target="50-78-2", patent_publication="CN123456A"))
    assert result["target"]["name"] == "阿司匹林"
    assert len(result["candidates"]) == 1
    candidate = result["candidates"][0]
    assert candidate["disconnection"] == "酯键断裂"
    assert {item["smiles"] for item in candidate["precursors"]} == {"CC(=O)O", "O=C(O)c1ccccc1O"}
    assert "结构规则推断" in candidate["evidence_level"]
    assert "未自动核对" in result["patent"]["status"]
    assert result["known_route_summaries"]


def test_arbitrary_smiles_and_no_rule_result_are_explicit():
    result = analyze(RetrosynthesisInput(target="CC(=O)Nc1ccc(O)cc1"))
    assert any(item["disconnection"] == "酰胺键断裂" for item in result["candidates"])
    empty = analyze(RetrosynthesisInput(target="CCO"))
    assert empty["candidates"] == []
    assert "没有可信的一步拆解" in empty["decision"]


def test_endpoint_requires_project_access_and_rejects_unmapped_names(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/retrosynthesis.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        db.add(Project(id="p", name="P", disease="D", owner_id="owner"))
        db.commit()
    app = FastAPI()
    app.include_router(router)
    def connection():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = connection
    app.dependency_overrides[get_current_user] = lambda: UserContext("owner", "owner", "researcher")
    client = TestClient(app)
    base = "/api/v1/drug-discovery/projects/p/process/retrosynthesis/analyze"
    assert client.post(base, json={"target": "50-78-2"}).status_code == 200
    bad = client.post(base, json={"target": "未收录化合物"})
    assert bad.status_code == 422 and "SMILES" in bad.json()["detail"]
    unknown = client.post(base.replace("/projects/p/", "/projects/unknown/"), json={"target": "50-78-2"})
    assert unknown.status_code in (403, 404)
