from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth import UserContext, get_current_user
from app.database import Base, get_db
from app.models import Project
from app.process_portfolio import PortfolioInput, router, summarize
from app.fluralaner_public_case import build_public_case


def sample():
    return {
        "target_name": "测试中间体 X", "cas": "", "smiles": "", "product_class": "intermediate",
        "intended_use": "兽药中间体", "jurisdiction": "CN", "target_purity_pct": 98,
        "routes": [
            {"name": "公开路线 A", "kind": "patent", "source_reference": "CN123456A 实施例 1", "steps": [
                {"transformation": "形成中间体 A", "intermediate": "中间体 A", "yield_pct": 80, "source_reference": "实施例 1 第一步"},
                {"transformation": "形成目标", "yield_pct": 50, "source_reference": "实施例 1 第二步"},
            ]},
            {"name": "自拟路线 B", "kind": "user", "steps": [
                {"transformation": "备选转化", "yield_pct": None},
            ]},
        ],
    }


def test_portfolio_yield_and_gaps_are_evidence_bounded():
    result = summarize(PortfolioInput(**sample()))
    assert result["route_summaries"][0]["overall_yield_pct"] == 40
    assert result["route_summaries"][1]["overall_yield_pct"] is None
    assert result["route_summaries"][1]["missing_yield_steps"] == [1]
    assert result["published_route_count"] == 1
    assert result["user_route_count"] == 1
    assert len(result["scorecard"]) == 9
    assert result["scorecard"][1]["status"] == "需补证"
    assert any("生产商" in gap for gap in result["gaps"])


def test_portfolio_requires_source_for_public_routes():
    payload = sample()
    payload["routes"][0]["source_reference"] = ""
    try:
        PortfolioInput(**payload)
    except ValueError as exc:
        assert "来源编号" in str(exc)
    else:
        raise AssertionError("public route without source was accepted")


def test_fluralaner_public_source_snapshot_has_traceable_steps_and_quality_caveat():
    case = build_public_case()
    assert len(case["wo_steps"]) == 7
    assert len(case["ep_variants"]) == 2
    assert case["wo_steps"][0]["paragraphs"][0]["url"].endswith("#p0215")
    assert "50 g" in case["wo_steps"][0]["paragraphs"][0]["text"]
    assert case["wo_steps"][6]["reported_yield_pct"] == 82
    assert case["wo_steps"][6]["accepted_yield_pct"] is None
    assert "110 mg" in case["wo_steps"][6]["paragraphs"][-1]["text"]
    assert "HPLC" in case["hplc_method"][0]["text"]
    assert summarize(PortfolioInput(**case["input"]))["route_summaries"][0]["overall_yield_pct"] is None


def test_intermediate_notes_keep_source_counts_separate_from_manufacturers():
    screen = build_public_case()["intermediate_screen"]
    by_step = {item["step"]: item for item in screen["entries"]}
    assert len(by_step) == 6
    assert by_step[1]["supplier_listings"] == 202
    assert by_step[2]["cas"] == "1138444-80-0"
    assert by_step[6]["supplier_listings"] == 35
    assert by_step[4]["supplier_listings"] is None
    assert by_step[5]["supplier_listings"] is None
    assert all(item["manufacturer_count"] is None for item in screen["entries"])
    assert all(item["patent_source"] and item["difficulty_basis"] and item["barrier_to_verify"] for item in screen["entries"])
    assert "shortlist_count" not in screen and "screening_rule" not in screen
    assert all("decision_status" not in item for item in screen["entries"])


def test_portfolio_api_persists_without_inventing_missing_route_data(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/portfolio.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        db.add(Project(id="p", name="P", disease="D", owner_id="owner"))
        db.add(Project(id="other", name="Other", disease="D", owner_id="someone-else"))
        db.commit()
    app = FastAPI()
    app.include_router(router)
    def connection():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = connection
    app.dependency_overrides[get_current_user] = lambda: UserContext("owner", "owner", "researcher")
    client = TestClient(app)
    base = "/api/v1/drug-discovery/projects/p/process/portfolios"
    saved = client.post(base, json=sample())
    assert saved.status_code == 200, saved.text
    assert saved.json()["result"]["route_summaries"][0]["overall_yield_pct"] == 40
    assert client.get(base).json()[0]["id"] == saved.json()["id"]
    # 要求 27：权限放开后，别的项目也能直接读
    assert client.get("/api/v1/drug-discovery/projects/other/process/portfolios").status_code == 200
    case = client.get(base + "/public-case/fluralaner")
    assert case.status_code == 200 and len(case.json()["wo_steps"]) == 7
    assert client.get(base.replace("/projects/p/", "/projects/other/") + "/public-case/fluralaner").status_code == 200
    unknown = sample()
    unknown["routes"] = []
    unknown["target_name"] = "完全新目标"
    new = client.post(base, json=unknown)
    assert new.status_code == 200
    assert new.json()["result"]["route_summaries"] == []
    assert new.json()["result"]["best_reported_yield_route"] is None
