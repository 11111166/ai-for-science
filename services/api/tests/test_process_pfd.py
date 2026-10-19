import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.auth import UserContext, get_current_user
from app.database import Base, get_db
from app.models import Project
from app.process_pfd import PfdDiagram, SHEET_STAGE, build_draft, render_dxf, render_svg, router as pfd_router
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
    ],
}

BASE = "/api/v1/drug-discovery/projects/p/process"
PFD = BASE + "/pfd/diagrams"
SCALE_UP = BASE + "/scale-up"


def test_first_save_keeps_edits_made_on_a_generated_draft(tmp_path):
    """Saving a generated draft must persist the user's edits, not a regenerated draft."""
    app, client, factory = make_client(tmp_path)
    assessment = client.post(SCALE_UP + "/assessments", json=SCALE).json()
    draft = client.get(BASE + "/pfd/draft-from-assessment",
                       params={"assessment_id": assessment["id"]}).json()
    edited = json.loads(json.dumps(draft["graph"]))
    edited["nodes"][2]["x"] = 873
    template = json.loads(json.dumps(edited["edges"][0]))
    template["id"] = "e9"
    template["source"] = edited["nodes"][4]["id"]
    template["target"] = edited["nodes"][7]["id"]
    edited["edges"].append(template)
    with factory() as db:
        before = json.dumps(db.get(ScaleAssessment, assessment["id"]).data, sort_keys=True, ensure_ascii=False)
    created = client.post(PFD, json={"title": "edited draft", "graph": edited,
                                     "source_assessment_id": assessment["id"]})
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["source_assessment_id"] == assessment["id"]
    assert body["edge_count"] == len(edited["edges"])
    assert body["node_count"] == len(edited["nodes"])
    reopened = client.get(PFD + f"/{body['id']}").json()["graph"]
    assert reopened["nodes"][2]["x"] == 873
    assert [edge["id"] for edge in reopened["edges"]] == [edge["id"] for edge in edited["edges"]]
    with factory() as db:
        after = json.dumps(db.get(ScaleAssessment, assessment["id"]).data, sort_keys=True, ensure_ascii=False)
        assert after == before


def make_client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/pfd.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        db.add(Project(id="p", name="P", disease="D", owner_id="owner"))
        db.add(Project(id="q", name="Q", disease="D", owner_id="someone-else"))
        db.commit()
    app = FastAPI()
    app.include_router(scale_router)
    app.include_router(pfd_router)

    def connection():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = connection
    app.dependency_overrides[get_current_user] = lambda: UserContext("owner", "owner", "researcher")
    return app, TestClient(app), factory


def test_draft_records_units_sources_and_pending_gaps():
    spec = ScaleInput(**SCALE).model_dump()
    draft = build_draft({"id": "a1", "spec": spec, "result": calculate(spec)})
    graph = draft["graph"]
    assert draft["source_assessment_id"] == "a1"
    assert "概念级工艺流程图" in draft["disclaimer"]
    kinds = [node["kind"] for node in graph["nodes"]]
    assert "reactor" in kinds and "waste" in kinds
    assert len(graph["edges"]) == len(graph["nodes"]) - 1
    for node in graph["nodes"]:
        for param in node["params"]:
            assert param["source"], param
            assert param["value"], param
    reactor = next(node for node in graph["nodes"] if node["kind"] == "reactor")
    names = {param["name"]: param for param in reactor["params"]}
    assert names["名义容积（初选）"]["value"] == "2000" and names["名义容积（初选）"]["unit"] == "L"
    assert names["名义容积（初选）"]["source"].startswith("放大评估《API-X1")
    assert names["温度"]["value"] == "60" and names["温度"]["unit"] == "℃"
    for gap in ("管径", "仪表与安全联锁", "设备强度/机械设计"):
        assert names[gap]["value"] == "待补"
    assert not any("管径" in param["value"] for param in reactor["params"])


def test_saved_diagram_never_rewrites_the_source_assessment(tmp_path):
    app, client, factory = make_client(tmp_path)
    assessment = client.post(SCALE_UP + "/assessments", json=SCALE).json()
    with factory() as db:
        before = json.dumps(db.get(ScaleAssessment, assessment["id"]).data, sort_keys=True, ensure_ascii=False)
    created = client.post(PFD, json={"source_assessment_id": assessment["id"]})
    assert created.status_code == 200, created.text
    diagram = created.json()
    assert diagram["version"] == 1 and diagram["revision"] == 1
    assert diagram["root_id"] == diagram["id"]
    moved = json.loads(json.dumps(diagram["graph"]))
    moved["nodes"][0]["label"] = "改动后的标签"
    moved["nodes"][0]["x"] = 999
    updated = client.put(PFD + f"/{diagram['id']}", json={"title": "改后", "graph": moved, "base_revision": 1})
    assert updated.status_code == 200, updated.text
    assert updated.json()["revision"] == 2
    assert client.get(PFD).json()[0]["node_count"] == len(moved["nodes"])
    with factory() as db:
        after = json.dumps(db.get(ScaleAssessment, assessment["id"]).data, sort_keys=True, ensure_ascii=False)
        assert after == before
        assert len(db.scalars(select(PfdDiagram)).all()) == 1


def test_optimistic_concurrency_versions_and_open_project_access(tmp_path):
    app, client, factory = make_client(tmp_path)
    assessment = client.post(SCALE_UP + "/assessments", json=SCALE).json()
    created = client.post(PFD, json={"source_assessment_id": assessment["id"], "title": "初版"}).json()
    stale = client.put(PFD + f"/{created['id']}", json={"title": "旧", "graph": created["graph"], "base_revision": 99})
    assert stale.status_code == 409
    version = client.post(PFD + f"/{created['id']}/versions", json={})
    assert version.status_code == 200, version.text
    body = version.json()
    assert body["version"] == 2 and body["previous_id"] == created["id"] and body["root_id"] == created["id"]
    assert body["revision"] == 1
    assert len(client.get(PFD).json()) == 2
    assert client.get(PFD + f"/{created['id']}").json()["title"] == "初版"
    # 要求 27：权限全面放开，跨项目读取不再被拦（项目 q 存在就直接放行）
    other = client.get("/api/v1/drug-discovery/projects/q/process/pfd/diagrams")
    assert other.status_code == 200
    # p 项目的评估在 q 项目下找不到，属于「本项目的资源不存在」的 404，不是权限拦截
    assert client.get("/api/v1/drug-discovery/projects/q/process/pfd/draft-from-assessment",
                      params={"assessment_id": assessment["id"]}).status_code == 404
    assert client.get(PFD + "/missing-diagram").status_code == 404
    assert client.get(BASE + "/pfd/draft-from-assessment", params={"assessment_id": "nope"}).status_code == 404


def test_invalid_graphs_are_rejected(tmp_path):
    app, client, factory = make_client(tmp_path)
    bad_edge = {
        "title": "坏图", "graph": {
            "nodes": [{"id": "n1", "kind": "feed", "label": "进料", "x": 0, "y": 0}],
            "edges": [{"id": "e1", "source": "n1", "target": "ghost"}],
        },
    }
    assert client.post(PFD, json=bad_edge).status_code == 422
    duplicate = {
        "title": "重号", "graph": {
            "nodes": [
                {"id": "n1", "kind": "feed", "label": "进料", "x": 0, "y": 0},
                {"id": "n1", "kind": "product", "label": "产品", "x": 200, "y": 0},
            ],
            "edges": [],
        },
    }
    assert client.post(PFD, json=duplicate).status_code == 422
    missing_param_source = {
        "title": "缺来源", "graph": {
            "nodes": [{"id": "n1", "kind": "feed", "label": "进料", "x": 0, "y": 0,
                       "params": [{"name": "管径", "value": "DN25", "unit": "mm"}]}],
            "edges": [],
        },
    }
    assert client.post(PFD, json=missing_param_source).status_code == 422

    blank_param_source = {
        "title": "空来源", "graph": {
            "nodes": [{"id": "n1", "kind": "feed", "label": "进料", "x": 0, "y": 0,
                       "params": [{"name": "管径", "value": "DN25", "unit": "mm", "source": "   "}]}],
            "edges": [],
        },
    }
    blank = client.post(PFD, json=blank_param_source)
    assert blank.status_code == 422
    assert "参数来源不能为空" in blank.text


def test_exports_escape_user_markup_and_stay_readable(tmp_path):
    app, client, factory = make_client(tmp_path)
    payload = {
        "title": "导出测试", "graph": {
            "nodes": [{"id": "n1", "kind": "reactor", "tag": "R-1", "label": "<script>alert(1)</script> m²",
                       "x": 10, "y": 20, "params": [{"name": "压力", "value": "1", "unit": "bar(a)", "source": "放大评估输入"}]}],
            "edges": [],
        },
    }
    created = client.post(PFD, json=payload)
    assert created.status_code == 200, created.text
    diagram_id = created.json()["id"]
    svg = client.get(PFD + f"/{diagram_id}/export")
    assert svg.status_code == 200
    assert svg.headers["content-disposition"].startswith("attachment")
    body = svg.text
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body
    assert "非施工图或已验证 P&amp;ID" in body
    # 设备一览表已从图面移除：单元参数（名称/值/单位/来源）不再画在图纸上
    assert "bar(a)" not in body and "压力：" not in body
    dxf = client.get(PFD + f"/{diagram_id}/export.dxf")
    assert dxf.status_code == 200
    assert dxf.headers["content-disposition"].endswith('.dxf"')
    text = dxf.content.decode("gbk")
    assert text.startswith("0\nSECTION")
    assert "AC1009" in text and "ANSI_936" in text
    # 图元按院标图层分类落图：设备轮廓、流量/文字说明各归各层
    assert "3-设备轮廓线" in text and "1文字说明" in text
    # 附表移除后 DXF 只写几何、位号/名称与物流标注；非 GBK 字符仍按降级写法落盘
    assert "压力：1 bar(a)" not in text
    assert "m2" in text and "²" not in text
    # DXF is CAD geometry data, not markup: the label is stored literally and is never executed.
    assert "<script>alert(1)</script>" in text
    assert text.rstrip().endswith("EOF")

def test_saved_diagram_export_matches_the_cad_preview_sheet(tmp_path):
    """导出图纸与预览必须是同一套 CAD 图面，不能退回浅色示意图。"""
    app, client, factory = make_client(tmp_path)
    assessment = client.post(SCALE_UP + "/assessments", json=SCALE).json()
    draft = client.get(BASE + "/pfd/draft-from-assessment",
                       params={"assessment_id": assessment["id"]}).json()
    created = client.post(PFD, json={"title": "CAD 图面导出", "graph": draft["graph"],
                                     "source_assessment_id": assessment["id"]})
    assert created.status_code == 200, created.text
    diagram_id = created.json()["id"]

    svg = client.get(PFD + f"/{diagram_id}/export")
    assert svg.status_code == 200
    assert 'concept-pfd-sheet-v1.svg' in svg.headers["content-disposition"]
    body = svg.text
    assert "#0a0c0c" in body
    assert "设备一览表" not in body and "物流表" not in body  # 附表一律不出图
    assert "图名" in body and "审核 / 批准" in body  # 只保留右下角图签栏
    assert "图例 · 概念级通用单元操作符号" not in body  # 图例单独出一张图
    assert "非施工图或已验证 P&amp;ID" in body
    assert "审核 / 批准" in body and SHEET_STAGE in body
    assert "<script" not in body.lower()

    dxf = client.get(PFD + f"/{diagram_id}/export.dxf")
    assert dxf.status_code == 200
    assert 'concept-pfd-sheet-v1.dxf' in dxf.headers["content-disposition"]
    text = dxf.content.decode("gbk")
    assert text.startswith("0\nSECTION") and "AC1009" in text
    assert "POLYLINE" in text and "SOLID" in text and "15-图框与标题栏" in text
    assert "待补" in text and "?" not in text
    assert text.rstrip().endswith("EOF")


def test_export_title_block_fills_project_and_product_from_the_saved_diagram(tmp_path):
    """调用方留空时，图签栏用项目名与产品节点补齐，而不是留空。"""
    app, client, factory = make_client(tmp_path)
    with factory() as db:
        record = db.get(Project, "p")
        record.name = "格氏偶联工艺项目"
        db.commit()
    created = client.post(PFD, json={"title": "图签栏", "graph": {"nodes": [
        {"id": "n1", "kind": "product", "tag": "B-201", "label": "产品暂存与包装",
         "x": 0, "y": 0, "params": []}], "edges": []}})
    assert created.status_code == 200, created.text
    body = client.get(PFD + f"/{created.json()['id']}/export").text
    assert "格氏偶联工艺项目" in body
    assert "产品暂存与包装" in body
    assert "手工绘制或演示草稿" in body


def test_blank_diagram_and_renderers_stay_valid(tmp_path):
    app, client, factory = make_client(tmp_path)
    empty = {"nodes": [], "edges": []}
    svg = render_svg("空图", empty)
    assert "暂无参数" in svg and "非施工图" in svg
    dxf = render_dxf("空图", empty)
    assert dxf.encode("gbk").decode("gbk") == dxf
    created = client.post(PFD, json={"title": "空白图", "graph": empty})
    assert created.status_code == 200, created.text
    assert created.json()["source_assessment_id"] is None
    assert created.json()["node_count"] == 0
    saved = created.json()
    assert client.get(PFD + f"/{saved['id']}/export").status_code == 200
    assert client.post(PFD, json={"title": "缺图"}).status_code == 422


def test_legacy_assessment_still_generates_a_draft(tmp_path):
    """An assessment stored by an earlier release must not break drawing generation."""
    app, client, factory = make_client(tmp_path)
    with factory() as db:
        db.add(ScaleAssessment(project_id="p", data={
            "spec": {"name": "旧版放大评估"}, "result": {"pfd": ["反应", "结晶", "干燥"]},
            "created_at": "2026-01-05T00:00:00+00:00"}))
        db.commit()
        legacy_id = db.scalars(select(ScaleAssessment.id)).one()
    draft = client.get(BASE + "/pfd/draft-from-assessment", params={"assessment_id": legacy_id})
    assert draft.status_code == 200, draft.text
    graph = draft.json()["graph"]
    assert [node["label"] for node in graph["nodes"]][:3] == ["反应", "结晶", "干燥"]
    for node in graph["nodes"]:
        for param in node["params"]:
            assert param["source"], param


def test_legacy_assessment_without_steps_returns_a_clear_422(tmp_path):
    app, client, factory = make_client(tmp_path)
    with factory() as db:
        db.add(ScaleAssessment(project_id="p", data={"spec": {"name": "无步骤"}, "result": {"pfd": []}}))
        db.commit()
        legacy_id = db.scalars(select(ScaleAssessment.id)).one()
    response = client.get(BASE + "/pfd/draft-from-assessment", params={"assessment_id": legacy_id})
    assert response.status_code == 422, response.text
    assert "概念流程步骤" in response.json()["detail"]


def test_assessment_list_survives_one_unparseable_row(tmp_path):
    """One stale row must not take the whole project assessment list down."""
    app, client, factory = make_client(tmp_path)
    assert client.post(SCALE_UP + "/assessments", json=SCALE).status_code == 200
    with factory() as db:
        db.add(ScaleAssessment(project_id="p", data={"spec": {"name": "损坏行"}}))
        db.commit()
    listed = client.get(SCALE_UP + "/assessments")
    assert listed.status_code == 200, listed.text
    names = [row["spec"].get("name") for row in listed.json()]
    assert "API-X1 百公斤初评" in names
    assert "损坏行" in names


BALANCE_GRAPH = {
    "nodes": [
        {"id": "n1", "kind": "feed", "tag": "V-101", "label": "投料罐", "x": 0, "y": 0,
         "w": 180, "h": 84, "params": [], "notes": []},
        {"id": "n2", "kind": "reactor", "tag": "R-101", "label": "反应釜", "x": 260, "y": 0,
         "w": 180, "h": 84, "params": [], "notes": []},
        {"id": "n3", "kind": "product", "tag": "B-201", "label": "产品包装", "x": 520, "y": 0,
         "w": 180, "h": 84, "params": [], "notes": []},
        {"id": "n4", "kind": "waste", "tag": "W-201", "label": "母液暂存", "x": 260, "y": 220,
         "w": 180, "h": 84, "params": [], "notes": []},
        {"id": "n5", "kind": "other", "tag": "U-101", "label": "公用工程接口", "x": 520, "y": 220,
         "w": 180, "h": 84, "params": [], "notes": []},
    ],
    "edges": [
        {"id": "e1", "source": "n1", "target": "n2", "label": "进料", "media": "material",
         "params": [{"name": "进料量", "value": "500", "unit": "kg/h", "source": "放大评估《X》物流表"}]},
        {"id": "e2", "source": "n2", "target": "n3", "label": "产品", "media": "material",
         "params": [{"name": "产品量", "value": "450", "unit": "kg/h", "source": "放大评估《X》物流表"}]},
        {"id": "e3", "source": "n5", "target": "n2", "label": "低压蒸汽", "media": "utility",
         "params": [{"name": "蒸汽用量", "value": "待补", "unit": "kg/批", "source": "需补充实验或工程数据"}]},
    ],
}


def test_material_balance_flags_the_unclosed_difference_and_names_its_route():
    """进口 500、出口 450：差额 50 必须列为不平衡，并指明需要标注的去向。"""
    from app.process_pfd import _material_balance, sheet_dxf, sheet_svg

    balance = _material_balance(BALANCE_GRAPH)
    assert balance["status"] == "不平衡"
    assert balance["unaccounted"] == {"kg/h": 50.0}
    reactor = next(row for row in balance["rows"] if row[0].startswith("R-101"))
    assert reactor[3] == "+50 kg/h"
    assert "须注明去向" in reactor[4] and "排污" in reactor[4]
    assert reactor[5] == "不平衡"
    # 公用工程线不计入物料衡算：U-101 不出现在校核表里
    assert not any(row[0].startswith("U-101") for row in balance["rows"])
    global_row = balance["rows"][0]
    assert global_row[1] == "500 kg/h" and global_row[2] == "450 kg/h" and global_row[3] == "+50 kg/h"

    section = sheet_svg("差额图", BALANCE_GRAPH)
    assert "物料守恒校核" in section and "+50 kg/h" in section and "排污" in section
    assert "500 kg/h" in section and "450 kg/h" in section
    assert "待补" in section
    dxf = sheet_dxf("差额图", BALANCE_GRAPH)
    assert "物料守恒校核" in dxf and "+50 kg/h" in dxf and "?" not in dxf
    legend = sheet_svg("差额图", BALANCE_GRAPH, None, "", "legend")
    assert "物料流向与守恒说明" in legend and "未闭合差额" in legend and "不平衡" in legend


def test_utility_lines_are_excluded_from_the_mass_balance():
    """公用工程线（media=utility）不参与物料守恒校核。"""
    from app.process_pfd import _material_balance

    graph = {
        "nodes": [
            {"id": "n1", "kind": "feed", "tag": "V-101", "label": "投料罐", "x": 0, "y": 0, "params": []},
            {"id": "n2", "kind": "reactor", "tag": "R-101", "label": "反应釜", "x": 260, "y": 0, "params": []},
            {"id": "n3", "kind": "waste", "tag": "W-201", "label": "废液", "x": 520, "y": 0, "params": []},
            {"id": "n4", "kind": "other", "tag": "U-101", "label": "公用工程接口", "x": 260, "y": 220, "params": []},
        ],
        "edges": [
            {"id": "e1", "source": "n1", "target": "n2", "label": "进料",
             "params": [{"name": "进料量", "value": "500", "unit": "kg/h", "source": "评估物流表"}]},
            {"id": "e2", "source": "n2", "target": "n3", "label": "废液",
             "params": [{"name": "废液量", "value": "500", "unit": "kg/h", "source": "评估物流表"}]},
            {"id": "e3", "source": "n4", "target": "n2", "label": "冷却水", "media": "utility",
             "params": [{"name": "冷却水用量", "value": "待补", "unit": "m³/批", "source": "需补充实验或工程数据"}]},
        ],
    }
    balance = _material_balance(graph)
    assert balance["status"] == "闭合"
    assert balance["unaccounted"] == {}
    assert not any(row[0].startswith("U-101") for row in balance["rows"])
    reactor = next(row for row in balance["rows"] if row[0].startswith("R-101"))
    assert reactor[5] == "闭合"


def test_legend_sheet_is_the_first_drawing_of_a_two_sheet_set():
    """图例单独成图：第 1 张为图例与说明，第 2 张为工段概念流程图。"""
    from app.process_pfd import sheet_dxf, sheet_svg

    legend = sheet_svg("图册", BALANCE_GRAPH, None, "", "legend")
    assert "第 1 张：图例与符号说明" in legend
    assert "图例与符号说明" in legend and "线型、介质与流向图例" in legend
    assert "物料流向与守恒说明" in legend
    assert "公用工程总管与支管：蒸汽、冷却水、冷冻水、氮气等（不计入物料衡算）" in legend
    assert "安全阀组 / 放空 / 排污支管" in legend and "公用工程条件" in legend
    assert "设备一览表" in legend and "物流表" in legend  # 附表册随第 1 张图例图交付
    assert "<polyline" not in legend  # 不画流程节点与物流线
    assert "非施工图或已验证 P&amp;ID" in legend

    section = sheet_svg("图册", BALANCE_GRAPH, None, "", "section")
    assert "第 2 张：工段概念流程图" in section
    assert "设备一览表" not in section and "物流表" not in section  # 图面不再排附表
    assert "图名" in section and "节点 / 连线" in section  # 仅保留右下角图签栏
    assert "图例 · 概念级通用单元操作符号" not in section
    assert "<polyline" in section

    legend_dxf = sheet_dxf("图册", BALANCE_GRAPH, None, "", "legend")
    assert "12-辅助及次要物流" in legend_dxf and "18-信号线" in legend_dxf
    assert "?" not in legend_dxf and "DASHDOT" in legend_dxf
    section_dxf = sheet_dxf("图册", BALANCE_GRAPH, None, "", "section")
    # 辅助物料线（公用工程、安全阀组 / 放空 / 排污支管）画实线：图层的线型是 CONTINUOUS
    assert "\n2\n12-辅助及次要物流\n70\n0\n62\n201\n6\nCONTINUOUS\n" in section_dxf
    assert "\n2\n1-主物料线\n70\n0\n62\n5\n6\nCONTINUOUS\n" in section_dxf
    assert "\n2\n2-次物料线\n70\n0\n62\n6\n6\nCONTINUOUS\n" in section_dxf


def test_dxf_ships_the_institute_layer_standard():
    """导出的 DXF 自带院标图层表：图层名、色号、线型预先设好，图元全部落在这些层上。"""
    import re

    from app.pfd_cad_sheet import CAD_LAYERS, LAYER_ACI, LAYER_MAP, LAYER_ORDER, layer_key_rows
    from app.process_pfd import sheet_dxf, sheet_svg

    # 院标表里的层名与色号原样照抄（重号、跳号都保留）
    assert len(CAD_LAYERS) == len({row[0] for row in CAD_LAYERS})
    assert LAYER_ACI["1-主物料线"] == 5 and LAYER_ACI["2-次物料线"] == 6
    assert LAYER_ACI["3-设备轮廓线"] == 33 and LAYER_ACI["5-设备内部件"] == 150
    assert LAYER_ACI["12-辅助及次要物流"] == 201 and LAYER_ACI["18-信号线"] == 4
    # 每个内部图元层都映射到一个院标层，且都在出图顺序里
    assert set(LAYER_MAP) == set(LAYER_ORDER)
    assert set(LAYER_MAP.values()) <= set(LAYER_ACI)

    for view in ("section", "legend"):
        dxf = sheet_dxf("图层图册", BALANCE_GRAPH, None, "", view)
        for name, aci, _rgb, linetype, _use in CAD_LAYERS:
            assert "\n2\n%s\n70\n0\n62\n%d\n6\n%s\n" % (name, aci, linetype) in dxf, (view, name)
        # 图元只写院标层名，不再出现内部层名
        body = dxf.split("ENTITIES", 1)[1]
        layers_used = set(re.findall(r"\n8\n([^\n]+)\n", body))
        assert not any(name.startswith("PFD_") for name in layers_used)
        for name in layers_used:
            assert name in LAYER_ACI, (view, name)

    # 图例图里信号线按虚线、预留管线按点划线画（图元级线型覆盖，不影响层内继续画线）
    legend_body = sheet_dxf("图层图册", BALANCE_GRAPH, None, "", "legend").split("ENTITIES", 1)[1]
    assert "\n6\nDASHED\n" in legend_body and "\n6\nDASHDOT\n" in legend_body

    # 图例图的图层表：只列这张图纸用到的层，色块画在对应的图层上
    rows = layer_key_rows()
    assert rows and all(row[0] in LAYER_ACI for row in rows)
    assert {row[0] for row in rows} == set(LAYER_MAP.values())
    legend = sheet_svg("图层图册", BALANCE_GRAPH, None, "", "legend")
    assert "CAD 图层与颜色（院标图层表）" in legend
    assert "色号" in legend and "1-主物料线" in legend
    assert "图层：出图前先把图层设好" in legend


def test_auxiliary_pipes_are_solid_at_one_third_of_the_main_line():
    """辅助物料线画实线，线宽为主物料线的 1/3（主物料线 0.90 mm -> 0.30 mm）。"""
    import re

    from app import pfd_symbols as sym
    from app.process_pfd import sheet_svg

    assert abs(sym.W_UTILITY * 3.0 - sym.W_MAIN_HEAVY) < 0.011, sym.W_UTILITY
    assert sym.W_THIN < sym.W_UTILITY < sym.W_MAIN < sym.W_MAIN_HEAVY

    section = sheet_svg("图册", BALANCE_GRAPH, None, "", "section")
    group = re.search(r'<g id="PFD_UTILITY"[^>]*>', section)
    assert group is not None and "dasharray" not in group.group(0)  # 实线，不是虚线
    block = section.split('<g id="PFD_UTILITY"', 1)[1].split("</g>", 1)[0]
    widths = {round(float(value), 2) for value in re.findall(r'stroke-width="([\d.]+)"', block)}
    assert widths == {round(sym.W_UTILITY, 2)}, widths


def test_towers_and_tanks_are_the_biggest_symbols_on_the_sheet():
    """塔最高最大，反应器次之，储罐不能比换热器还小。"""
    from app import pfd_symbols as sym
    from app.pfd_cad_sheet import _symbol_frame

    scale = sym.SYM_KIND_SCALE
    assert scale["column"] > scale["reactor"] >= scale["vessel"] > scale["heat_exchanger"]
    assert scale["vessel"] > scale["heat_exchanger"] and scale["tank_h"] > scale["heat_exchanger"]
    base = sym.SYM_EQUIPMENT
    col = _symbol_frame("column", base * scale["column"])
    ves = _symbol_frame("vessel", base * scale["vessel"])
    ex = _symbol_frame("heat_exchanger", base * scale["heat_exchanger"])
    assert col["h"] > 2.5 * ex["h"]          # 塔比换热器的圆壳高一截以上
    assert ves["h"] > ex["h"]                # 储罐比换热器高
    assert ves["w"] > 0.85 * ex["w"]         # 储罐也不比换热器窄


def test_battery_limit_streams_use_the_standard_boundary_flag():
    """进出车间的流股画界区旗标：方框 + 尖端，尖端指向介质流动方向。"""
    import re

    from app.pfd_cad_sheet import _boundary_box, _boundary_size
    from app.process_pfd import sheet_dxf, sheet_svg

    total, height = _boundary_size(["主物料入口", "（来源）"])
    assert total > height > 0.0                       # 方框外还要多出一个尖端
    entry_box = _boundary_box(30.0, 120.0, ["主物料入口", "（来源）"], True)
    exit_box = _boundary_box(800.0, 120.0, ["主物料出口", "（去向）"], False)
    assert abs(entry_box["x1"] - 29.2) < 0.01         # 进料端：平边贴图框左边线
    assert abs(exit_box["x2"] - 800.8) < 0.01         # 出料端：尖端贴图框右边线
    assert abs((entry_box["x2"] - entry_box["x1"]) - (total + 1.6)) < 0.01

    section = sheet_svg("界区图", BALANCE_GRAPH, None, "", "section")
    assert "主物料入口" in section and "（来源）" in section
    assert "主物料出口" in section and "（去向）" in section
    block = section.split('<g id="PFD_BOUNDARY"', 1)[1].split("</g>", 1)[0]
    flags = re.findall(r'<path d="M ([^"]+) Z"', block)
    assert len(flags) >= 2                            # 进料端 + 出料端各一个旗标
    for points in flags:
        assert len(points.split(" L ")) == 5          # 方框 + 尖端：五边形

    dxf = sheet_dxf("界区图", BALANCE_GRAPH, None, "", "section")
    assert "主物料入口" in dxf and "主物料出口" in dxf
    assert dxf.count("\n66\n1\n70\n1\n") >= 2      # 闭合折线 = 旗标

    legend = sheet_svg("界区图", BALANCE_GRAPH, None, "", "legend")
    assert "界区旗标" in legend


def test_utility_headers_enter_through_a_boundary_flag():
    """公用工程总管从图框左边进车间，端头同样画界区旗标（介质名写在旗标里）。"""
    from app.process_pfd import sheet_svg

    graph = {
        "nodes": [
            {"id": "n1", "kind": "feed", "tag": "V-101", "label": "投料罐", "x": 0, "y": 0, "params": []},
            {"id": "n2", "kind": "reactor", "tag": "R-101", "label": "反应釜", "x": 260, "y": 0,
             "params": [], "utilities": [{"medium": "cooling_water",
                                          "service": "循环冷却水 上水 32 ℃/回水 40 ℃",
                                          "port": "n", "value": "12", "unit": "m³/h"}]},
            {"id": "n3", "kind": "product", "tag": "B-201", "label": "产品罐", "x": 520, "y": 0,
             "params": []},
        ],
        "edges": [
            {"id": "e1", "source": "n1", "target": "n2", "label": "进料",
             "params": [{"name": "进料量", "value": "500", "unit": "kg/h", "source": "评估物流表"}]},
            {"id": "e2", "source": "n2", "target": "n3", "label": "产品",
             "params": [{"name": "产品量", "value": "500", "unit": "kg/h", "source": "评估物流表"}]},
        ],
    }
    import re

    section = sheet_svg("界区图", graph, None, "", "section")
    block = section.split('<g id="PFD_BOUNDARY"', 1)[1].split("</g>", 1)[0]
    assert ">循环冷却水<" in section             # 介质名写在旗标框里
    flag = re.search(r'<path d="M ([^"]+) Z"', block)
    assert flag is not None and len(flag.group(1).split(" L ")) == 5   # 旗标是闭合五边形
    assert "公用工程总管（" in section           # 规格跟在旗标后面，标题不再重复介质名
    assert "公用工程总管 ·" not in section


def test_two_sheet_exports_and_view_validation(tmp_path):
    """网页最终交付两张 CAD 图：工段图与图例，各自可导出 SVG 与 DXF。"""
    app, client, factory = make_client(tmp_path)
    created = client.post(PFD, json={"title": "两张图纸", "graph": BALANCE_GRAPH})
    assert created.status_code == 200, created.text
    diagram_id = created.json()["id"]

    section = client.get(PFD + f"/{diagram_id}/export")
    assert "concept-pfd-sheet-v1.svg" in section.headers["content-disposition"]
    legend = client.get(PFD + f"/{diagram_id}/export", params={"view": "legend"})
    assert legend.status_code == 200
    assert "concept-pfd-legend-v1.svg" in legend.headers["content-disposition"]
    assert "线型、介质与流向图例" in legend.text
    assert "设备一览表" in legend.text and "物流表" in legend.text  # 附表排在第 1 张图例图

    legend_dxf = client.get(PFD + f"/{diagram_id}/export.dxf", params={"view": "legend"})
    assert "concept-pfd-legend-v1.dxf" in legend_dxf.headers["content-disposition"]
    text = legend_dxf.content.decode("gbk")
    assert "DASHED" in text and "?" not in text

    bad = client.get(PFD + f"/{diagram_id}/export", params={"view": "3d-plant"})
    assert bad.status_code == 400

    payload = {"title": "两张图纸", "graph": BALANCE_GRAPH, "view": "legend"}
    preview = client.post(BASE + "/pfd/preview/sheet.svg", json=payload)
    assert preview.status_code == 200 and "线型、介质与流向图例" in preview.text
    bad_preview = client.post(BASE + "/pfd/preview/sheet.svg", json={**payload, "view": "plant3d"})
    assert bad_preview.status_code == 422
    dxf_preview = client.post(BASE + "/pfd/preview/sheet.dxf", json=payload)
    assert "concept-pfd-legend.dxf" in dxf_preview.headers["content-disposition"]

def test_saved_export_avoids_portal_static_cache_extensions(tmp_path):
    """导出路径不得使用 .svg 等静态后缀：共享门户会按 URL 缓存这些 GET 响应。

    缓存命中时不校验 Authorization，扩展名导出会把已保存图纸回放给未授权
    客户端；因此 /export 是唯一返回图纸内容的路径，旧的 .svg 链接只做 303
    跳转，绝不返回图纸正文。
    """
    app, client, factory = make_client(tmp_path)
    created = client.post(PFD, json={"title": "缓存后缀", "graph": BALANCE_GRAPH})
    assert created.status_code == 200, created.text
    diagram_id = created.json()["id"]

    sheet = client.get(PFD + f"/{diagram_id}/export")
    assert sheet.status_code == 200
    assert sheet.headers["content-type"].startswith("image/svg+xml")
    assert "非施工图或已验证 P&amp;ID" in sheet.text

    legend = client.get(PFD + f"/{diagram_id}/export", params={"view": "legend"})
    assert legend.status_code == 200 and "线型、介质与流向图例" in legend.text

    legacy = client.get(PFD + f"/{diagram_id}/export.svg", follow_redirects=False)
    assert legacy.status_code == 303
    assert legacy.headers["location"] == "export?view=section"
    assert legacy.content == b""
    assert "<svg" not in legacy.text

    legacy_legend = client.get(PFD + f"/{diagram_id}/export.svg",
                               params={"view": "legend"}, follow_redirects=False)
    assert legacy_legend.status_code == 303
    assert legacy_legend.headers["location"] == "export?view=legend"

    assert client.get(PFD + f"/{diagram_id}/export.svg",
                      params={"view": "plant3d"}).status_code == 400

    followed = client.get(PFD + f"/{diagram_id}/export.svg")
    assert followed.status_code == 200 and "非施工图或已验证 P&amp;ID" in followed.text
