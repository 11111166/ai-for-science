"""多模态工艺资料接入的确定性测试：模型返回 JSON -> 可校验的概念 PFD 图。

不打模型接口：只测规整、排布、物料守恒与诚实标注这几段纯逻辑。
"""
from __future__ import annotations

from app import process_intake as intake
from app import process_pfd as pfd


PAYLOAD = {
    "title": "环氧化合物 A 反应-精制工段 概念 PFD",
    "units": [
        {"id": "U1", "label": "原料罐 V-101", "kind": "vessel", "tag": "V-101",
         "params": [{"name": "进料流量", "value": "500", "unit": "kg/h", "source": "原文：进料流量 500 kg/h"}]},
        {"id": "U2", "label": "反应器 R-101", "kind": "reactor", "tag": "R-101",
         "params": [{"name": "操作温度", "value": "80", "unit": "℃", "source": "原文：操作温度 80 ℃"},
                    {"name": "转化率", "value": "92%", "unit": "%", "source": "原文：转化率 92%"}],
         "outlets": [{"name": "底部排污", "value": "50", "unit": "kg/h", "source": "原文：底部排污 50 kg/h"}]},
        {"id": "U3", "label": "精馏塔 T-101", "kind": "column", "tag": "T-101",
         "params": [{"name": "塔顶压力", "value": "0.2", "unit": "bar(a)", "source": "原文：塔顶压力 0.2 bar(a)"}]},
        {"id": "U4", "label": "废水罐 V-105", "kind": "vessel", "tag": "V-105",
         "params": [{"name": "进料流量", "value": "50", "unit": "kg/h", "source": "原文：排污 50 kg/h"}]},
        {"id": "U5", "label": "产品罐 V-104", "kind": "vessel", "tag": "V-104",
         "params": [{"name": "产品量", "value": "450", "unit": "kg/h", "source": "原文：产品 450 kg/h"}]},
    ],
    "streams": [
        {"id": "S1", "source": "U1", "target": "U2", "label": "原料 A", "media": "material",
         "params": [{"name": "流量", "value": "500", "unit": "kg/h", "source": "原文：500 kg/h"}],
         "components": [{"kind": "valve_check", "tag": "未指定"}], "instruments": []},
        {"id": "S2", "source": "U2", "target": "U3", "label": "反应液", "media": "material",
         "params": [{"name": "流量", "value": "450", "unit": "kg/h", "source": "原文：反应液 450 kg/h"}],
         "components": [{"kind": "valve_control", "tag": "FV-101"}], "instruments": ["PIC-101"]},
        {"id": "S3", "source": "U2", "target": "U4", "label": "底部排污", "media": "material",
         "params": [{"name": "流量", "value": "50", "unit": "kg/h", "source": "原文：排污 50 kg/h"}]},
        {"id": "S4", "source": "U3", "target": "U5", "label": "塔釜产品", "media": "material",
         "params": [{"name": "流量", "value": "450", "unit": "kg/h", "source": "原文：塔釜 450 kg/h"}]},
        {"id": "S5", "source": "U1", "target": "U1", "label": "自连", "media": "material", "params": []},
        {"id": "S6", "source": "U9", "target": "U5", "label": "悬空", "media": "material", "params": []},
    ],
    "unreadable": ["管径与仪表回路定值资料未给出"],
    "notes": ["本图为概念级流程图"],
}


def test_graph_from_model_payload_is_valid_and_laid_out():
    graph, warnings, order = intake._build_graph(PAYLOAD)
    graph = intake._trim_graph(graph)
    pfd.PfdGraph(**graph)

    assert [node["id"] for node in graph["nodes"]] == ["n1", "n2", "n3", "n4", "n5"]
    assert len(graph["edges"]) == 4, "自连与悬空连线必须被丢掉"
    assert any("同一单元" in item for item in warnings)
    assert any("未识别到的单元" in item for item in warnings)

    # 从左到右分层，且同一列不重叠
    by_id = {node["id"]: node for node in graph["nodes"]}
    assert by_id["n1"]["x"] < by_id["n2"]["x"] < by_id["n3"]["x"] < by_id["n5"]["x"]
    assert len({node["y"] for node in graph["nodes"]}) > 1


def test_placeholder_tags_and_unit_duplication_are_cleaned():
    graph, _warnings, _order = intake._build_graph(PAYLOAD)
    reactor = next(node for node in graph["nodes"] if node["id"] == "n2")
    assert reactor["params"][1]["value"] == "92", "value 里重复的单位要去掉"
    assert reactor["params"][1]["unit"] == "%"

    edge = next(item for item in graph["edges"] if item["id"] == "e1")
    assert edge["components"][0]["tag"] == "", "占位位号“未指定”要清空"
    assert edge["components"][0]["kind"] == "valve_check"
    assert edge["instruments"] == []


def test_terminal_sinks_become_product_and_waste_so_balance_closes():
    graph, _warnings, _order = intake._build_graph(PAYLOAD)
    kinds = {node["label"]: node["kind"] for node in graph["nodes"]}
    assert kinds["废水罐 V-105"] == "waste"
    assert kinds["产品罐 V-104"] == "product"

    balance = pfd._material_balance(graph)
    assert balance["status"] == "闭合", pfd._balance_verdict(balance)
    assert balance["global_in"] == {"kg/h": 500.0}
    assert balance["global_out"] == {"kg/h": 500.0}


def test_missing_flow_becomes_pending_instead_of_being_invented():
    graph, _warnings, _order = intake._build_graph(PAYLOAD)
    edge = next(item for item in graph["edges"] if item["id"] == "e4")
    assert edge["label"]
    for param in edge["params"]:
        assert param["source"], "每个参数都必须带来源"
    incomplete = {"units": [{"id": "U1", "label": "未知单元", "kind": "other", "params": []}], "streams": []}
    graph2, _w, _o = intake._build_graph(incomplete)
    node = graph2["nodes"][0]
    assert node["params"][0]["value"] == pfd.PENDING
    assert node["params"][0]["source"]


def test_payload_without_units_is_rejected():
    import pytest

    with pytest.raises(Exception):
        intake._build_graph({"units": [], "streams": []})


def test_required_inputs_only_list_what_is_missing_and_split_by_level():
    """有什么画什么：只提示资料里没给、而图上要用到的输入，并分两级。"""
    graph, _warnings, _order = intake._build_graph({
        "units": [
            {"id": "U1", "label": "反应器 R-101", "kind": "reactor", "tag": "R-101",
             "params": [{"name": "操作温度", "value": "80", "unit": "℃", "source": "原文：80 ℃"}]},
            {"id": "U2", "label": "产品罐 V-104", "kind": "product", "tag": "V-104",
             "params": [{"name": "产品量", "value": "450", "unit": "kg/h", "source": "原文：450 kg/h"}]},
        ],
        "streams": [
            {"id": "S1", "source": "U1", "target": "U2", "label": "反应液", "media": "material",
             "params": [{"name": "流量", "value": "450", "unit": "kg/h", "source": "原文：450 kg/h"}],
             "components": [{"kind": "valve_control", "tag": "FV-101"}], "instruments": ["PIC-101"]},
        ],
    })
    graph = intake._trim_graph(graph)
    parsed = {"required_inputs": [
        {"key": "pipe_class", "status": "需补充", "detail": "C1F/C2F/A2 只给了代号",
         "impact": "管线无法标等级", "how": "管道材料等级表"},
        {"key": "composition", "status": "已给全", "detail": ""},
        {"key": "equipment_tags", "status": "已给全", "detail": ""},
        {"key": "flows", "status": "已给全", "detail": ""},
        {"key": "valves", "status": "已给全", "detail": ""},
        {"key": "conditions", "status": "已给全", "detail": ""},
        {"key": "instruments", "status": "已给全", "detail": ""},
    ]}
    required = intake._build_required_inputs(parsed, graph)
    keys = [item["key"] for item in required]

    assert "composition" not in keys, "模型确认已给全的条目不再出现"
    for key in ("equipment_tags", "flows", "valves", "conditions", "instruments"):
        assert key not in keys, f"这份资料里已经给全的 {key} 不该再问"
    for key in ("materials", "relief", "thermo", "waste", "utilities"):
        assert key not in keys, f"详细设计资料缺了也不拦出图，模型没说就不打扰设计人员：{key}"

    pipe = next(item for item in required if item["key"] == "pipe_class")
    assert pipe["level"] == "detail", "管径/等级属于详细设计，不能和影响成图的项一样重"
    assert pipe["detail"] == "C1F/C2F/A2 只给了代号", "模型给的 detail 优先于兜底文案"
    assert pipe["how"] == "管道材料等级表"
    assert pipe["status"] == "资料未提供"

    assert "topology" in keys, "图上接不上的管线直接影响图面，必须提示"
    assert next(item for item in required if item["key"] == "topology")["level"] == "key"
    order = [item["key"] for item in intake.INPUT_CHECKLIST]
    assert keys == [key for key in order if key in keys], "顺序按固定清单走，便于逐条对照"


def test_required_inputs_marks_graph_gaps_and_keeps_detail_quiet():
    graph, _warnings, _order = intake._build_graph({
        "units": [{"id": "U1", "label": "反应器", "kind": "reactor", "params": []}],
        "streams": [{"id": "S1", "source": "U1", "target": "U2", "label": "出料", "media": "material", "params": []}],
    })
    graph = intake._trim_graph(graph)
    required = intake._build_required_inputs({}, graph)
    by_key = {item["key"]: item for item in required}

    assert by_key["equipment_tags"]["status"] == "图上缺值"
    assert by_key["flows"]["status"] == "图上缺值"
    assert by_key["valves"]["status"] == "图上缺值"
    assert "没有位号" in by_key["equipment_tags"]["detail"]
    assert "materials" not in by_key, "模型没说、图上也看不出的详细设计资料不打扰设计人员"
    assert all(item["how"] and item["detail"] for item in required), "每条都要写清缺什么、怎么补"
    assert all(item["level"] in ("key", "detail") for item in required), "每条都要带级别，界面才能分级显示"


RECYCLE_PAYLOAD = {
    "title": "带循环物料的工段 概念 PFD",
    "units": [
        {"id": "U1", "label": "原料罐 V-101", "kind": "vessel", "tag": "V-101"},
        {"id": "U2", "label": "计量泵 P-101", "kind": "pump", "tag": "P-101"},
        {"id": "U3", "label": "反应器 R-101", "kind": "reactor", "tag": "R-101"},
        {"id": "U4", "label": "冷凝器 E-102", "kind": "heat_exchanger", "tag": "E-102"},
        {"id": "U5", "label": "精馏塔 T-101", "kind": "column", "tag": "T-101"},
        {"id": "U6", "label": "废水罐 V-105", "kind": "vessel", "tag": "V-105"},
    ],
    "streams": [
        {"id": "S1", "source": "U1", "target": "U2", "label": "原料", "media": "material",
         "params": [{"name": "流量", "value": "500", "unit": "kg/h", "source": "原文"}]},
        {"id": "S2", "source": "U2", "target": "U3", "label": "进料", "media": "material",
         "params": [{"name": "流量", "value": "500", "unit": "kg/h", "source": "原文"}]},
        {"id": "S3", "source": "U3", "target": "U5", "label": "反应液", "media": "material",
         "params": [{"name": "流量", "value": "450", "unit": "kg/h", "source": "原文"}]},
        {"id": "S4", "source": "U3", "target": "U4", "label": "顶部尾气", "media": "material",
         "params": [{"name": "流量", "value": "30", "unit": "kg/h", "source": "原文"}]},
        {"id": "S5", "source": "U4", "target": "U3", "label": "冷凝液返回", "media": "material",
         "params": [{"name": "流量", "value": "20", "unit": "kg/h", "source": "原文"}]},
        {"id": "S6", "source": "U5", "target": "U6", "label": "母液", "media": "material",
         "params": [{"name": "流量", "value": "50", "unit": "kg/h", "source": "原文"}]},
    ],
    "unreadable": [],
    "notes": [],
}


def test_recycle_streams_do_not_collapse_the_layout_into_one_column():
    """回流管线（冷凝液返回反应器）不能把分层算法拉到上限。"""
    graph, _warnings, _order = intake._build_graph(RECYCLE_PAYLOAD)
    graph = intake._trim_graph(graph)
    by_label = {node["label"]: node for node in graph["nodes"]}

    columns = sorted({node["x"] for node in graph["nodes"]})
    assert len(columns) >= 4, f"有回路时也要按流程分列，实际只有 {len(columns)} 列"
    widest = max(len([n for n in graph["nodes"] if n["x"] == x]) for x in columns)
    assert widest <= 2, f"单列堆了 {widest} 个单元，图形会叠在一起"

    chain = ["原料罐 V-101", "计量泵 P-101", "反应器 R-101", "精馏塔 T-101"]
    xs = [by_label[label]["x"] for label in chain]
    assert xs == sorted(xs) and len(set(xs)) == len(xs), f"主物料线要从左到右：{xs}"
    ys = {by_label[label]["y"] for label in chain}
    assert len(ys) == 1, f"主物料线要走在同一行，实际 {ys}"

    recycle = by_label["冷凝器 E-102"]
    assert recycle["y"] != by_label["反应器 R-101"]["y"], "回流支线要脱离主物料线"

LONG_PAYLOAD = {
    "title": "长流程工段 概念 PFD",
    "units": [
        {"id": "U1", "label": "原料罐 V-101", "kind": "vessel", "tag": "V-101"},
        {"id": "U2", "label": "计量泵 P-101", "kind": "pump", "tag": "P-101"},
        {"id": "U3", "label": "催化剂计量泵 P-102", "kind": "pump", "tag": "P-102"},
        {"id": "U4", "label": "反应器 R-101", "kind": "reactor", "tag": "R-101"},
        {"id": "U5", "label": "换热器 E-101", "kind": "heat_exchanger", "tag": "E-101"},
        {"id": "U6", "label": "冷凝器 E-102", "kind": "heat_exchanger", "tag": "E-102"},
        {"id": "U7", "label": "离心机 C-101", "kind": "separator", "tag": "C-101"},
        {"id": "U8", "label": "流化床干燥器 D-101", "kind": "dryer", "tag": "D-101"},
        {"id": "U9", "label": "废水罐 V-105", "kind": "vessel", "tag": "V-105"},
    ],
    "streams": [
        {"id": "S1", "source": "U1", "target": "U2", "label": "原料A"},
        {"id": "S2", "source": "U2", "target": "U4", "label": "原料A进料"},
        {"id": "S3", "source": "U3", "target": "U4", "label": "催化剂"},
        {"id": "S4", "source": "U4", "target": "U5", "label": "反应液"},
        {"id": "S5", "source": "U5", "target": "U7", "label": "冷却后反应液"},
        {"id": "S6", "source": "U7", "target": "U8", "label": "滤饼"},
        {"id": "S7", "source": "U7", "target": "U9", "label": "母液"},
        {"id": "S8", "source": "U4", "target": "U6", "label": "顶部尾气"},
        {"id": "S9", "source": "U6", "target": "U4", "label": "冷凝液返回"},
    ],
    "unreadable": [],
    "notes": [],
}


# 要求19① 之后，图面顶部的说明框已经取消（免责声明缩进右下角图签栏），
# 因此页眉与图签栏当作图纸留白，不算「图面里的空白带」。
# 页眉与右下角图签栏是图纸边饰：和它们相邻的空档属于图纸留白，不是图面里的空白带。
# （要求19① 取消图面顶部说明框之后，页眉到图面之间的这块留白会明显变大。）
SHEET_MARGIN_LAYERS = {'PFD_TITLE', 'PFD_TITLEBLOCK'}


def _largest_blank_stripe(prims) -> float:
    """图面里最大的一块空白带（mm）：图元按纵向合并后，相邻两段之间的空档。

    要求19① 之后图面顶部不再画说明框（免责声明缩进右下角图签栏），所以页眉与图签栏
    之间的留白是正常的图纸边距，不参与统计；只统计流程图本体内部有没有空出一大块。
    """
    spans = []
    for item in prims:
        if item["layer"] == "PFD_FRAME":
            continue
        if item["t"] == "line":
            ys = (item["y1"], item["y2"])
        elif item["t"] == "text":
            ys = (item["y"] - item["h"], item["y"])
        elif item["t"] in ("poly", "solid"):
            values = [point[1] for point in item["pts"]]
            ys = (min(values), max(values))
        elif item["t"] == "circle":
            ys = (item["cy"] - item["r"], item["cy"] + item["r"])
        else:
            ys = (item["y"], item["y"] + item["h"])
        spans.append((min(ys), max(ys), item["layer"]))
    spans.sort()
    merged: list[dict] = []
    for low, high, layer in spans:
        if merged and low <= merged[-1]["hi"] + 0.6:
            merged[-1]["hi"] = max(merged[-1]["hi"], high)
            merged[-1]["layers"].add(layer)
        else:
            merged.append({"lo": low, "hi": high, "layers": {layer}})
    gaps = []
    for index in range(len(merged) - 1):
        left, right = merged[index], merged[index + 1]
        if left["layers"] & SHEET_MARGIN_LAYERS or right["layers"] & SHEET_MARGIN_LAYERS:
            continue
        gaps.append(right["lo"] - left["hi"])
    return max(gaps, default=0.0)

def test_section_sheet_fills_the_drawing_band_instead_of_leaving_a_blank_stripe():
    """图幅是被表格篇幅顶上去的：布置要在纵向摊开填满，不能在图面中段空出一大块。"""
    graph = intake._trim_graph(intake._build_graph(LONG_PAYLOAD)[0])
    sheet = pfd._sheet_draw(LONG_PAYLOAD["title"], graph, None, "2026-09-30", "section")
    blank = _largest_blank_stripe(sheet["prims"])
    assert blank <= 25.0, f"图面最大空白带 {blank:.1f} mm，布置没有摊开填满图面"


def test_branch_pump_sits_next_to_the_equipment_it_feeds():
    """没有上游连线的支线（催化剂泵）要往右靠到合流设备前一列，不能留在图纸最左边。"""
    graph = intake._trim_graph(intake._build_graph(LONG_PAYLOAD)[0])
    by_label = {node["label"]: node for node in graph["nodes"]}
    feed, pump, reactor = by_label["原料罐 V-101"], by_label["计量泵 P-101"], by_label["反应器 R-101"]
    catalyst = by_label["催化剂计量泵 P-102"]
    assert catalyst["x"] > feed["x"], "催化剂泵不该留在最左边那一列"
    assert catalyst["x"] == pump["x"], "催化剂泵要和主物料线的同类设备对齐"
    assert catalyst["x"] < reactor["x"], "支线不能越过它要去的设备"


def test_recycle_pair_stacks_and_the_return_line_goes_around_the_side():
    """反应器与顶置冷凝器上下叠放；回程管线从侧面绕，不能从两台设备的符号里穿过去。"""
    graph = intake._trim_graph(intake._build_graph(RECYCLE_PAYLOAD)[0])
    by_label = {node["label"]: node for node in graph["nodes"]}
    reactor, condenser = by_label["反应器 R-101"], by_label["冷凝器 E-102"]
    assert condenser["x"] == reactor["x"], "冷凝器要画在反应器同一列上下叠放"
    assert condenser["y"] > reactor["y"], "冷凝器要画在反应器下面"

    ctx = pfd._sheet_ctx(RECYCLE_PAYLOAD["title"], graph, pfd.SheetMeta(), "2026-09-30", "section")
    calls = []
    original = pfd._sheet.route_ports

    def spy(source, target, src_ports, tgt_ports, obstacles=(), bounds=None, leave=()):
        points = original(source, target, src_ports, tgt_ports, obstacles, bounds, leave)
        calls.append((source, target, points))
        return points

    pfd._sheet.route_ports = spy
    try:
        pfd._sheet.compose(ctx)
    finally:
        pfd._sheet.route_ports = original
    back = [points for source, target, points in calls
            if abs(source["cx"] - target["cx"]) < 1e-6 and target["cy"] < source["cy"]]
    assert back, "没找到回程管线"
    for points in back:
        assert len({round(point[0], 3) for point in points}) > 1, \
            "回程管线不能是一条贯通的竖线，那会从两台设备的符号里穿过去"


def test_payload_with_only_unit_names_still_draws_a_draft():
    """要求16：只有单元名称也要出图，缺的部分只是图上的「待补」，不是拦出图的理由。"""
    graph, warnings, _order = intake._build_graph({
        "title": "只有一句话描述的工段",
        "units": [
            {"id": "U1", "label": "反应器", "kind": "reactor"},
            {"id": "U2", "label": "精馏塔", "kind": "column"},
        ],
        "streams": [],
    })
    graph = intake._trim_graph(graph)
    graph = intake._repair_graph(graph, warnings)
    pfd.PfdGraph(**graph)

    assert len(graph["nodes"]) == 2, "读到单元名称就先建节点"
    for node in graph["nodes"]:
        assert node["params"], "缺参数也要给「待补」占位"
        assert all(param["source"] for param in node["params"]), "占位参数也要带来源"


def test_repair_graph_keeps_drawing_instead_of_failing_the_request():
    """要求16：图面数据不合规范也不再整单失败，修完照画，问题降级为提示。"""
    graph = {
        "nodes": [
            {"id": "n1", "kind": "reactor", "tag": "R-101", "label": "反应器", "x": 0.0, "y": 0.0,
             "w": 180.0, "h": 84.0, "params": [], "notes": [], "outlets": []},
            {"id": "n1", "kind": "pump", "tag": "P-101", "label": "泵", "x": 0.0, "y": 0.0,
             "w": 180.0, "h": 84.0, "params": [], "notes": [], "outlets": []},
        ],
        "edges": [
            {"id": "e1", "source": "n1", "target": "n9", "label": "出料", "params": [],
             "components": [], "instruments": []},
        ],
    }
    warnings = []
    repaired = intake._repair_graph(graph, warnings)
    pfd.PfdGraph(**repaired)

    assert len(repaired["nodes"]) == 1, "重复的节点编号只保留一个"
    assert repaired["edges"] == [], "指向不存在单元的连线丢掉"
    assert repaired["nodes"][0]["params"], "节点还是要带上「待补」参数占位"


def test_key_coverage_says_what_the_drawing_was_built_from():
    """界面要能说清这次是拿哪几项输入画的图、哪几项还缺。"""
    graph, _warnings, _order = intake._build_graph({
        "units": [
            {"id": "U1", "label": "原料罐 V-101", "kind": "feed", "tag": "V-101"},
            {"id": "U2", "label": "产品罐 V-104", "kind": "product", "tag": "V-104"},
        ],
        "streams": [
            {"id": "S1", "source": "U1", "target": "U2", "label": "产品", "media": "material",
             "params": [{"name": "流量", "value": "500", "unit": "kg/h", "source": "原文：500 kg/h"}]},
        ],
    })
    graph = intake._trim_graph(graph)
    rows = {row["key"]: row["state"] for row in intake._key_coverage(graph, {"status": "闭合"})}

    assert rows["equipment_tags"] == "已用于成图"
    assert rows["flows"] == "已用于成图"
    assert rows["balance_gap"] == "已用于成图"
    assert rows["conditions"] == "图上缺值", "缺的项要如实说成图上缺值"
    assert list(rows) == [spec["key"] for spec in intake.INPUT_CHECKLIST if spec["level"] == "key"]
