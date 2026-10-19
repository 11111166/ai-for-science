import io
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth import UserContext, get_current_user
from app.database import Base, get_db
from app.hte_demo import dataset, router as hte_router
from app.models import Project
from app.order_assessment import assess, router as order_router
from app.process_scaleup import ScaleAssessment, ScaleInput, calculate, router as scale_router


SCALE = {
    "name": "API-X1 百公斤初评", "product": "API-X1", "product_molecular_weight": 250,
    "target_product_kg": 100, "conversion_pct": 90, "isolation_yield_pct": 80,
    "product_concentration_kg_l": .1, "temperature_c": 60, "pressure_bar": 1,
    "reaction_time_h": 6, "separation": "结晶", "solvent": "乙腈",
    "solvent_density_kg_l": .786, "heat_capacity_kj_kg_k": 2, "fill_fraction": .7,
    "corrosive": False,
    "materials": [
        {"name": "限量原料 A", "role": "limiting", "molecular_weight": 180, "equivalents": 1},
        {"name": "试剂 B", "role": "reagent", "molecular_weight": 90, "equivalents": 1.1},
        {"name": "催化剂 C", "role": "catalyst", "molecular_weight": 400, "equivalents": .02},
    ],
}


def test_public_hte_snapshot_and_bayesian_loop(tmp_path):
    assert len(dataset()["rows"]) == 48
    assert dataset()["target"]["name"] == "固定底物的 C–N 偶联产物"
    assert "未报告" in dataset()["target"]["state"]
    app, client = make_client(tmp_path)
    base = "/api/v1/drug-discovery/projects/p/process/hte"
    created = client.post(base + "/sessions", json={"dataset_id": "buchwald-hartwig-48", "method": "bayesian"})
    assert created.status_code == 200, created.text
    session = created.json()
    assert session["next"]["predicted_yield"] is None
    for _ in range(4):
        before = session["next"]
        session = client.post(base + f"/sessions/{session['id']}/step", json={"revision": session["revision"], "row_index": before["row_index"]}).json()
    assert session["observations"][0]["yield"] == dataset()["rows"][0][3]
    assert session["next"]["model"] == "兼顾较高收率的可能性和还没有充分尝试的条件"
    assert session["next"]["predicted_yield"] is not None
    assert client.get(base + "/sessions").json()[0]["revision"] == 5


def test_scale_up_calculation_is_reproducible():
    spec = ScaleInput(**SCALE).model_dump()
    result = calculate(spec)
    assert result["basis"]["product_kmol"] == .4
    assert result["materials"][0]["amount_kg"] == 100
    assert result["equipment"][0]["suggested_size_l"] == 2000
    assert result["pfd"][2] == "带搅拌间歇反应釜"
    assert "显热" in result["utilities"]["scope"]
    assert result["mass_metrics"]["pmi"] > 1
    assert result["purification"]["expected_assay_range_pct"] == [95, 99.5]
    assert len(result["aspen_streams"]) == 5 and len(result["lab_to_plant"]) == 4


def test_order_assessment_matches_and_exposes_evidence_gaps():
    found = assess({"query": "50-78-2", "quantity_kg": 100, "purity_pct": 98, "max_steps": 6, "chromatography_allowed": False, "material_cost_cny_per_kg": 300})
    assert found["status"] == "matched"
    assert found["identity"]["name"] == "阿司匹林"
    assert found["recommended_route"]["chromatography"] is False
    assert found["cost_estimate"]["material_cost_cny"] == 34090.91
    assert found["cost_estimate"]["suggested_quote_cny"] > found["cost_estimate"]["direct_cost_cny"]
    assert found["commercial_decision"]["grade"] == "可进入有条件报价"
    assert found["purification"]["expected_assay_range_pct"] == [95, 99.5]
    assert "FTO" in found["patent_review"]["status"]
    missing = assess({"query": "unknown-structure", "quantity_kg": 1, "purity_pct": 98, "max_steps": 6, "chromatography_allowed": False, "material_cost_cny_per_kg": None})
    assert missing["status"] == "evidence_gap"
    assert missing["routes"] == [] and len(missing["next_actions"]) == 4


def test_scale_and_order_endpoints_save_results(tmp_path):
    app, client = make_client(tmp_path)
    scale = client.post("/api/v1/drug-discovery/projects/p/process/scale-up/assessments", json=SCALE)
    assert scale.status_code == 200, scale.text
    scale_id = scale.json()["id"]
    assert client.get("/api/v1/drug-discovery/projects/p/process/scale-up/assessments").json()[0]["result"]["equipment"]
    # Historical rows only contain the result fields produced by the older release.
    # Every download must rebuild the engineering package from the stored spec.
    connection = client.app.dependency_overrides[get_db]()
    db = next(connection)
    item = db.get(ScaleAssessment, scale_id)
    item.data = {**item.data, "result": {"pfd": item.data["result"]["pfd"]}}
    db.commit()
    connection.close()
    streams = client.get(f"/api/v1/drug-discovery/projects/p/process/scale-up/assessments/{scale_id}/streams.csv")
    assert streams.status_code == 200 and "S01" in streams.text and "mass_kg_batch" in streams.text
    legacy_svg = client.get(f"/api/v1/drug-discovery/projects/p/process/scale-up/assessments/{scale_id}/pfd.svg", follow_redirects=False)
    assert legacy_svg.status_code == 303 and not legacy_svg.content
    svg = client.get(f"/api/v1/drug-discovery/projects/p/process/scale-up/assessments/{scale_id}/pfd")
    assert svg.status_code == 200 and "<svg" in svg.text and "概念 PFD" in svg.text
    dxf = client.get(f"/api/v1/drug-discovery/projects/p/process/scale-up/assessments/{scale_id}/pfd.dxf")
    assert dxf.status_code == 200 and "SECTION" in dxf.text and "ENTITIES" in dxf.text
    package = client.get(f"/api/v1/drug-discovery/projects/p/process/scale-up/assessments/{scale_id}/aspen-package.zip")
    assert package.status_code == 200
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        assert set(archive.namelist()) == {"streams.csv", "unit-operations.json", "assumptions.json", "aspen_bridge.py", "README_CN.txt"}
        assert "ASPEN_WRITE" in archive.read("aspen_bridge.py").decode()
    order = client.post("/api/v1/drug-discovery/projects/p/process/orders/assessments", json={"query": "布洛芬", "quantity_kg": 100, "purity_pct": 98, "max_steps": 6, "chromatography_allowed": False})
    assert order.status_code == 200, order.text
    assert order.json()["result"]["recommended_route"]["name"] == "BHC 催化三步路线"
    assert len(client.get("/api/v1/drug-discovery/projects/p/process/orders/catalog").json()) == 3


def make_client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/three-tools.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        db.add(Project(id="p", name="P", disease="D", owner_id="owner")); db.commit()
    app = FastAPI(); app.include_router(hte_router); app.include_router(scale_router); app.include_router(order_router)
    def connection():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = connection
    app.dependency_overrides[get_current_user] = lambda: UserContext("owner", "owner", "researcher")
    return app, TestClient(app)
