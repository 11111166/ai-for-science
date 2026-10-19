import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.auth import UserContext, get_current_user
from app.database import Base, get_db
from app.models import Project
from app.process_demo import CASES, build_demo_graph, demo_detail, demo_summary, router as demo_router
from app.process_pfd import PfdDiagram, router as pfd_router
from app.process_scaleup import ScaleInput

BASE = "/api/v1/drug-discovery/projects/p/process"
DEMO = BASE + "/demo"
SHEET = BASE + "/pfd/preview"
SLUG = "lphenylalanine"


def make_client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/demo.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        db.add(Project(id="p", name="P", disease="D", owner_id="owner"))
        db.add(Project(id="q", name="Q", disease="D", owner_id="someone-else"))
        db.commit()
    app = FastAPI()
    app.include_router(demo_router)
    app.include_router(pfd_router)

    def connection():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = connection
    app.dependency_overrides[get_current_user] = lambda: UserContext("owner", "owner", "researcher")
    return app, TestClient(app), factory


def test_demo_graph_covers_a_full_flowsheet_with_sources_on_every_value():
    graph = build_demo_graph(CASES[SLUG])
    assert len(graph["nodes"]) == 18
    assert len(graph["edges"]) == 23  # 20 条物料流 + 3 条公用工程线
    ids = {node["id"] for node in graph["nodes"]}
    tags = [node["tag"] for node in graph["nodes"]]
    assert len(set(tags)) == len(tags)
    for node in graph["nodes"]:
        assert node["tag"] and node["label"], node
        assert node["params"], node
        for param in node["params"]:
            assert param["name"].strip(), node["tag"]
            assert param["value"].strip(), (node["tag"], param)
            assert param["source"].strip(), (node["tag"], param)
    for edge in graph["edges"]:
        assert edge["source"] in ids and edge["target"] in ids, edge
        assert edge["label"].strip(), edge
        for param in edge["params"]:
            assert param["value"].strip() and param["source"].strip(), edge
    kinds = {node["kind"] for node in graph["nodes"]}
    assert {"feed", "reactor", "pump", "heat_exchanger", "separation", "filter", "column", "dryer",
            "product", "waste", "analysis", "other"} <= kinds


def test_demo_case_states_its_fictional_status_and_pending_engineering_data():
    case = CASES[SLUG]
    detail = demo_detail(case)
    assert "虚构" in case["case_note"]
    assert "非施工图" in detail["disclaimer"]
    assert "待补" in json.dumps(detail, ensure_ascii=False)
    reactor = next(node for node in detail["graph"]["nodes"] if node["kind"] == "reactor")
    names = {param["name"]: param for param in reactor["params"]}
    for gap in ("管径", "仪表与安全联锁", "设备强度/机械设计"):
        assert names[gap]["value"] == "待补"
        assert names[gap]["unit"] == ""
    assert not any("DN" in param["value"] for param in reactor["params"])
    summary = demo_summary(case)
    assert summary["target_product_kg"] == 100 and summary["material_count"] == 3


def test_demo_spec_fills_every_assessment_input_box():
    data = ScaleInput(**CASES[SLUG]["spec"]).model_dump()
    assert [key for key, value in data.items() if value is None] == []
    assert data["reaction_enthalpy_kj_mol"] is not None
    assert data["product_density_kg_l"] is not None
    for material in data["materials"]:
        assert material["name"].strip()
        assert material["molecular_weight"] and material["equivalents"]
        assert material["unit_cost_cny_per_kg"] is not None


def test_demo_case_routes_are_project_scoped(tmp_path):
    app, client, factory = make_client(tmp_path)
    listing = client.get(DEMO + "/cases")
    assert listing.status_code == 200, listing.text
    assert [item["slug"] for item in listing.json()] == [SLUG]
    detail = client.get(f"{DEMO}/cases/{SLUG}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["node_count"] == 18 and body["edge_count"] == 23
    assert body["assessment"]["product"] == "L-苯丙氨酸"
    assert "演示" in body["case_note"]
    assert client.get(f"{DEMO}/cases/not-a-case").status_code == 404
    # 要求 27：权限放开后，别的项目也照样能看（只有项目不存在才 404）
    assert client.get("/api/v1/drug-discovery/projects/q/process/demo/cases").status_code == 200
    assert client.get("/api/v1/drug-discovery/projects/missing/process/demo/cases").status_code == 404
    assert client.get(DEMO + "/cases/missing").status_code == 404


def test_sheet_preview_matches_the_drawing_and_writes_nothing(tmp_path):
    app, client, factory = make_client(tmp_path)
    demo = client.get(f"{DEMO}/cases/{SLUG}").json()
    payload = {"title": demo["title"], "graph": demo["graph"], "meta": {
        "project": "AI for Science 演示项目", "product": demo["product"],
        "version": "未保存", "revision": "-", "source": "演示案例草稿",
    }}
    svg = client.post(SHEET + "/sheet.svg", json=payload)
    assert svg.status_code == 200, svg.text
    assert svg.headers["content-type"].startswith("image/svg+xml")
    assert svg.headers["cache-control"] == "no-store"
    body = svg.text
    assert body.startswith("<svg")
    assert "设备一览表" not in body and "物流表" not in body  # 附表不出图，只保留图签栏
    assert "图名" in body
    assert "非施工图或已验证 P&amp;ID" in body
    assert "V-101" in body and "AT-201" in body and "R-201" in body
    dxf = client.post(SHEET + "/sheet.dxf", json=payload)
    assert dxf.status_code == 200, dxf.text
    assert dxf.headers["content-type"].startswith("application/dxf")
    text = dxf.content.decode("gbk")
    assert text.startswith("0\nSECTION") and text.rstrip().endswith("EOF")
    # 图元按院标图层分类落图（设备轮廓 / 文字说明 / 设备位号 / 图框与标题栏）
    for layer in ("3-设备轮廓线", "1文字说明", "20-设备位号", "15-图框与标题栏"):
        assert layer in text
    with factory() as db:
        assert db.scalars(select(PfdDiagram)).all() == []
    assert client.post("/api/v1/drug-discovery/projects/q/process/pfd/preview/sheet.svg",
                       json=payload).status_code == 200


def test_sheet_preview_rejects_invalid_graphs_and_escapes_markup(tmp_path):
    app, client, factory = make_client(tmp_path)
    bad = {"title": "坏图", "graph": {
        "nodes": [{"id": "n1", "kind": "feed", "label": "进料", "x": 0, "y": 0}],
        "edges": [{"id": "e1", "source": "n1", "target": "ghost"}],
    }}
    assert client.post(SHEET + "/sheet.svg", json=bad).status_code == 422
    assert client.post(SHEET + "/sheet.svg", json={"title": "", "graph": {"nodes": [], "edges": []}}).status_code == 422
    escaped = {"title": "越权测试", "graph": {
        "nodes": [{"id": "n1", "kind": "reactor", "tag": "R-1", "label": "<script>alert(1)</script> m²",
                   "x": 10, "y": 20,
                   "params": [{"name": "过滤面积", "value": "待补", "unit": "m²", "source": "需合格工程人员设计（概念图不提供）"}]}],
        "edges": [],
    }}
    svg = client.post(SHEET + "/sheet.svg", json=escaped)
    assert svg.status_code == 200, svg.text
    assert "<script>alert(1)</script>" not in svg.text
    assert "&lt;script&gt;" in svg.text
    dxf = client.post(SHEET + "/sheet.dxf", json=escaped)
    assert dxf.status_code == 200, dxf.text
    text = dxf.content.decode("gbk")
    # 图面上的用户文字（此处含 m²）在 DXF 里必须降级成 GBK 能写的写法
    assert "m2" in text
    assert "²" not in text

def test_demo_flowsheet_closes_the_mass_balance_and_routes_every_waste_stream():
    """演示案例的物料守恒必须闭合，且每一条废物流都写明去向。"""
    from app.process_pfd import _material_balance, sheet_svg

    graph = build_demo_graph(CASES[SLUG])
    balance = _material_balance(graph)
    assert balance["status"] == "闭合", balance["rows"]
    assert balance["unaccounted"] == {}
    labels = [edge["label"] for edge in graph["edges"]]
    for route in ("离心滤饼", "膜透过液", "再生与洗脱废液", "母液与洗液", "干燥冷凝液"):
        assert route in labels, route
    assert {edge["media"] for edge in graph["edges"]} == {"material", "utility"}
    assert all(edge["params"] for edge in graph["edges"])
    rows = {row[0]: row for row in balance["rows"]}
    assert rows["W-201 母液与废液暂存"][5] == "闭合"

    svg = sheet_svg(CASES[SLUG]["title"], graph, None, "", "section")
    # 附表不再出图：衡算结论只保留在右下角图签栏
    assert "物料守恒校核" in svg and "闭合" in svg
    assert "全流程（进口 → 出口）" not in svg and "设备一览表" not in svg
    assert "kg/批" in svg and "公用工程（不计入物料衡算）" in svg
    legend = sheet_svg(CASES[SLUG]["title"], graph, None, "", "legend")
    assert "全流程进口" in legend and "未闭合差额 0" in legend
