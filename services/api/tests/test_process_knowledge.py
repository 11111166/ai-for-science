from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth import UserContext, get_current_user
from app.database import Base, get_db
from app.models import Project
from app.process_knowledge import development_topics, materials, principles, reaction_templates, router


def make_client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/knowledge.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        db.add(Project(id="p", name="P", disease="D", owner_id="owner")); db.commit()
    app = FastAPI(); app.include_router(router)
    def connection():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = connection
    app.dependency_overrides[get_current_user] = lambda: UserContext("owner", "owner", "researcher")
    return TestClient(app)


def test_material_snapshot_has_usable_identity_and_guidance():
    assert len(materials()) == 80
    thf = next(x for x in materials() if x["id"] == "tetrahydrofuran")
    assert thf["cas"] == "109-99-9"
    assert thf["pubchem_cid"] == 8028
    assert "过氧化物" in thf["process_caution"]
    hydrogen = next(x for x in materials() if x["id"] == "hydrogen")
    assert hydrogen["category_label"] == "工艺气体"
    assert hydrogen["decision_questions"] and hydrogen["verify_before_scale"]


def test_reactions_link_to_complete_engineering_principles():
    assert len(reaction_templates()) == 30
    assert len(principles()) == 20
    principle_ids = {item["id"] for item in principles()}
    assert all({link["id"] for link in item["principles"]} <= principle_ids for item in reaction_templates())
    hydrogenation = next(x for x in reaction_templates() if x["id"] == "hydrogenation")
    assert any(x["id"] == "gas-liquid-transfer" for x in hydrogenation["principles"])


def test_knowledge_searches_chinese_english_cas_and_reaction(tmp_path):
    client = make_client(tmp_path); base = "/api/v1/drug-discovery/projects/p/process/knowledge"
    summary = client.get(base + "/summary")
    assert summary.status_code == 200
    assert summary.json()["material_count"] == 80 and summary.json()["reaction_template_count"] == 30
    assert summary.json()["principle_count"] == 20 and summary.json()["topic_count"] == 8
    assert len(summary.json()["method_basis"]) == 4
    assert "不访问外部网站" in summary.json()["runtime"]
    for query in ("四氢呋喃", "tetrahydrofuran", "109-99-9", "THF"):
        response = client.get(base + "/materials", params={"q": query})
        assert response.status_code == 200 and response.json()["items"][0]["id"] == "tetrahydrofuran"
    reaction = client.get(base + "/reaction-templates", params={"q": "碳—氮"}).json()
    assert any(x["id"] == "buchwald-hartwig" for x in reaction["items"])
    detail = client.get(base + "/reaction-templates/salt-crystallization")
    assert detail.status_code == 200 and detail.json()["scale_risks"] and detail.json()["principles"]
    principle = client.get(base + "/principles", params={"q": "Q = m × Cp × ΔT"})
    assert principle.status_code == 200 and principle.json()["items"][0]["id"] == "energy-balance"
    principle_detail = client.get(base + "/principles/liquid-liquid-extraction")
    assert principle_detail.status_code == 200 and "分配比" in principle_detail.json()["formulas"][0]["name"]
    assert client.get(base + "/reaction-templates", params={"q": "重氮盐积累"}).json()["items"][0]["id"] == "diazotization-sandmeyer"
    topic = client.get(base + "/topics", params={"q": "技术转移"})
    assert topic.status_code == 200 and topic.json()["items"][0]["id"] == "scale-up-transfer"
    topic_detail = client.get(base + "/topics/impurity-control-strategy")
    assert topic_detail.status_code == 200 and topic_detail.json()["deliverables"] and topic_detail.json()["red_flags"]
    assert len(development_topics()) == 8


def test_knowledge_rejects_unknown_category_and_missing_record(tmp_path):
    client = make_client(tmp_path); base = "/api/v1/drug-discovery/projects/p/process/knowledge"
    assert client.get(base + "/materials", params={"category": "unknown"}).status_code == 422
    assert client.get(base + "/reaction-templates", params={"category": "unknown"}).status_code == 422
    assert client.get(base + "/principles", params={"category": "unknown"}).status_code == 422
    assert client.get(base + "/topics", params={"category": "unknown"}).status_code == 422
    assert client.get(base + "/materials/not-in-snapshot").status_code == 404
    assert client.get(base + "/principles/not-in-snapshot").status_code == 404
    assert client.get(base + "/topics/not-in-snapshot").status_code == 404
