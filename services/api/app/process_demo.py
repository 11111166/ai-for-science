"""Demo concept process packages for the PFD/CAD workspace.

The module ships a small, fixed set of fully populated demonstration cases so
that a reviewer can open 工艺工程与生产设计 and see every input filled without
typing data first. Everything is read-only: nothing is written to the database
and no assessment is created.

Honesty rules follow the rest of the process module. Numbers are either taken
from the same calculation engine the platform uses for real assessments
(``process_scaleup.calculate``), or explicitly labelled as 待补 together with
the engineering work that is still required. The case never invents pipe
diameters, instruments, safety interlocks, mechanical design or validated
capacity, and the case data itself is marked as fictional demonstration input.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .auth import UserContext, get_current_user, require_project_access
from .database import get_db
from .process_pfd import EMPIRICAL_SOURCE, PENDING, PENDING_SOURCE, PfdGraph
from .process_scaleup import ScaleInput, calculate

router = APIRouter(prefix="/api/v1/drug-discovery/projects/{project_id}/process/demo")

DEMO_INPUT_SOURCE = "演示案例输入（虚构设计值，不得用于工程设计）"
DEMO_ROUTE_SOURCE = "演示案例路线假设（需以路线文献与实验复核）"
DEMO_RESULT_SOURCE = "按本案例输入调用平台放大评估引擎计算"
PENDING_LAB = PENDING_SOURCE
NEEDS_LAB = "需补充实验或工程数据"
DEMO_ALLOC_SOURCE = "演示案例分配假设（虚构设计值，必须由实验实测替代）"
DEMO_CONSERVED_SOURCE = "按演示分配假设与质量守恒推导（S01+S02=S03=S04+S05）"

DISCLAIMER = (
    "概念级工艺流程图，仅供研发讨论，非施工图或已验证 P&ID。"
    "管径、仪表、安全联锁、设备强度与已验证产能均待合格工程人员设计。"
)


def _fmt(value, digits: int = 3) -> str:
    if value is None:
        return PENDING
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        number = float(value)
        if number.is_integer() and abs(number) < 10**15:
            return str(int(number))
        return f"{number:.{digits}f}".rstrip("0").rstrip(".")
    return str(value)


def _p(name: str, value, unit: str, source: str) -> dict:
    return {"name": name[:40], "value": str(_fmt(value))[:60], "unit": unit[:20], "source": source[:160]}


def _row(result: dict, stream_id: str) -> dict:
    return next((item for item in result.get("aspen_streams") or [] if item.get("id") == stream_id), {})


def _mass(name: str, value, source: str, unit: str = "kg/批") -> dict:
    """One stream flow on the drawing: value + mass unit + where it comes from."""
    return _p(name, value, unit, source)


def _stream_param(result: dict, stream_id: str) -> dict:
    row = _row(result, stream_id)
    return _p(f"{stream_id} {row.get('name', '物流')}".strip(), row.get("mass_kg_batch"), "kg/批", DEMO_RESULT_SOURCE)


# --- demonstration input -------------------------------------------------

PHENYLALANINE_SPEC: dict = {
    "name": "L-苯丙氨酸 酶法转化—结晶精制 概念放大评估（演示）",
    "product": "L-苯丙氨酸",
    "product_state": "solid",
    "process_system": "homogeneous_liquid",
    "product_molecular_weight": 165.19,
    "product_density_kg_l": 0.6,
    "target_purity_pct": 99.0,
    "target_product_kg": 100.0,
    "conversion_pct": 95.0,
    "isolation_yield_pct": 88.0,
    "product_concentration_kg_l": 0.11,
    "temperature_c": 37.0,
    "pressure_bar": 1.0,
    "reaction_time_h": 24.0,
    "separation": "结晶",
    "solvent": "工艺水",
    "solvent_density_kg_l": 1.0,
    "heat_capacity_kj_kg_k": 4.18,
    "reaction_enthalpy_kj_mol": -58.0,
    "separation_energy_kwh": 120.0,
    "solvent_unit_cost_cny_per_kg": 0.005,
    "utility_cost_cny_per_kwh": 0.8,
    "waste_cost_cny_per_kg": 3.0,
    "fill_fraction": 0.7,
    "corrosive": False,
    "materials": [
        {"name": "反式肉桂酸", "role": "limiting", "molecular_weight": 148.16, "equivalents": 1.0, "phase": "solid", "unit_cost_cny_per_kg": 45.0},
        {"name": "氨水（以 NH3 计）", "role": "reagent", "molecular_weight": 17.03, "equivalents": 3.0, "phase": "liquid", "unit_cost_cny_per_kg": 1.2},
        {"name": "磷酸吡哆醛（辅因子）", "role": "catalyst", "molecular_weight": 247.14, "equivalents": 0.002, "phase": "solid", "unit_cost_cny_per_kg": 1800.0},
    ],
}

PHENYLALANINE_CASE: dict = {
    "slug": "lphenylalanine",
    "title": "L-苯丙氨酸 酶法转化—结晶精制 概念工艺包（演示）",
    "product": "L-苯丙氨酸",
    "case_note": "演示案例：所有输入框（含可选的反应焓与产品密度）均已填入虚构设计值，用于演示从放大评估到概念 PFD、SVG/DXF 与 CAD 预览的完整链路；这些数值不得用于工程设计。",
    "route": (
        "演示路线假设：苯丙氨酸解氨酶（PAL）催化反式肉桂酸与氨加成生成 L-苯丙氨酸（酶法转化，37 ℃、常压、24 h）；"
        "后处理为酶灭活与调 pH、固液分离、膜浓缩、活性炭脱色、离子交换精制、浓缩结晶、离心洗涤、真空干燥与包装。"
        "该路线仅用于演示结构与数据链路，实际路线需以路线文献、酶工程与实验数据确认。"
    ),
    "data_note": (
        "图中数值分三类：① 演示案例输入（虚构设计值）；② 平台按该输入计算的结果；③ 明确标注“待补”的工程数据。"
        "本案例不提供管径、仪表、安全联锁、设备强度与已验证产能，这些必须由合格工程人员设计。"
    ),
    "spec": PHENYLALANINE_SPEC,
}

CASES: dict[str, dict] = {PHENYLALANINE_CASE["slug"]: PHENYLALANINE_CASE}


def _layout() -> list[tuple[float, float]]:
    columns = [40.0, 270.0, 500.0, 730.0, 960.0, 1190.0]
    rows = [120.0, 330.0, 540.0]
    order = [(row, index) for row in range(3) for index in range(6)]
    points: list[tuple[float, float]] = []
    for row, index in order:
        column = index if row != 1 else 5 - index
        points.append((columns[column], rows[row]))
    return points


def build_demo_graph(case: dict) -> dict:
    """Assemble the demonstration flowsheet from the case input and results."""
    spec = dict(case["spec"])
    result = calculate(spec)
    equipment = result.get("equipment") or []
    reactor = equipment[0] if equipment else {}
    downstream = equipment[1] if len(equipment) > 1 else {}
    materials = result.get("materials") or []
    construction = (result.get("materials_of_construction") or {}).get("recommended")
    utilities = result.get("utilities") or {}
    mass = result.get("mass_metrics") or {}
    positions = _layout()

    def first(role: str) -> dict:
        return next((row for row in materials if row.get("role") == role), {})

    limiting = first("limiting")
    reagent = first("reagent")
    cofactor = first("catalyst")

    blocks: list[dict] = [
        {
            "kind": "feed", "tag": "V-101", "label": "反式肉桂酸投料罐",
            "params": [
                _p("投料量（限量原料）", limiting.get("amount_kg"), "kg/批", DEMO_RESULT_SOURCE),
                _p("投料当量", limiting.get("equivalents"), "", DEMO_INPUT_SOURCE),
                _p("物料物态", "固体（演示输入）", "", DEMO_INPUT_SOURCE),
                _p("溶解与配制温度", None, "℃", NEEDS_LAB),
            ],
        },
        {
            "kind": "feed", "tag": "V-102", "label": "氨水与辅料计量罐",
            "params": [
                _p("氨投料量（以 NH3 计）", reagent.get("amount_kg"), "kg/批", DEMO_RESULT_SOURCE),
                _p("投料当量", reagent.get("equivalents"), "", DEMO_INPUT_SOURCE),
                _p("辅因子投料量", cofactor.get("amount_kg"), "kg/批", DEMO_RESULT_SOURCE),
                _p("氨水浓度（演示输入）", 25.0, "%", DEMO_INPUT_SOURCE),
            ],
        },
        {
            "kind": "pump", "tag": "P-101", "label": "进料计量泵",
            "params": [
                _p("批量进料量", _row(result, "S01").get("mass_kg_batch"), "kg/批", DEMO_RESULT_SOURCE),
                _p("流量与计量精度", None, "", NEEDS_LAB),
                _p("泵型与扬程", None, "", NEEDS_LAB),
                _p("过流材质", construction, "", DEMO_RESULT_SOURCE),
            ],
        },
        {
            "kind": "heat_exchanger", "tag": "HX-101", "label": "进料预热器",
            "params": [
                _p("进口温度（演示输入）", 25.0, "℃", DEMO_INPUT_SOURCE),
                _p("出口温度", spec["temperature_c"], "℃", DEMO_INPUT_SOURCE),
                _p("显热负荷", utilities.get("sensible_kwh"), "kWh/批", DEMO_RESULT_SOURCE),
                _p("换热面积与备用", None, "", NEEDS_LAB),
                _p("加热介质", "低压蒸汽（接口形式待工程确认）", "", DEMO_ROUTE_SOURCE),
            ],
        },
        {
            "kind": "reactor", "tag": "R-201", "label": "固定化酶转化反应器",
            "params": [
                _p("工作体积", reactor.get("working_volume_l"), "L", DEMO_RESULT_SOURCE),
                _p("名义容积（初选）", reactor.get("suggested_size_l"), "L", "放大评估设备初选"),
                _p("装载率", reactor.get("fill_pct"), "%", DEMO_RESULT_SOURCE),
                _p("转化率", spec["conversion_pct"], "%", DEMO_INPUT_SOURCE),
                _p("温度", spec["temperature_c"], "℃", DEMO_INPUT_SOURCE),
                _p("压力", spec["pressure_bar"], "bar(a)", DEMO_INPUT_SOURCE),
                _p("反应时间", spec["reaction_time_h"], "h", DEMO_INPUT_SOURCE),
                _p("材质初选", construction, "", DEMO_RESULT_SOURCE),
                _p("管径", None, "", PENDING_LAB),
                _p("仪表与安全联锁", None, "", PENDING_LAB),
                _p("设备强度/机械设计", None, "", PENDING_LAB),
            ],
            "notes": ["固定化酶填装、传质与批间稳定性需实验确认"],
        },
        {
            "kind": "vessel", "tag": "K-201", "label": "酶灭活与调 pH 罐",
            "params": [
                _p("处理量", _row(result, "S03").get("mass_kg_batch"), "kg/批", DEMO_RESULT_SOURCE),
                _p("灭活温度与时间", None, "", NEEDS_LAB),
                _p("pH 目标", None, "", NEEDS_LAB),
                _p("材质初选", construction, "", DEMO_RESULT_SOURCE),
            ],
        },
        {
            "kind": "separation", "tag": "S-201", "label": "固液分离离心机",
            "params": [
                _p("处理量", _row(result, "S03").get("mass_kg_batch"), "kg/批", DEMO_RESULT_SOURCE),
                _p("固相去除率", None, "%", NEEDS_LAB),
                _p("离心机型号与转速", None, "", NEEDS_LAB),
                _p("分离收率（后处理总收率）", spec["isolation_yield_pct"], "%", DEMO_INPUT_SOURCE),
            ],
        },
        {
            "kind": "separation", "tag": "S-202", "label": "超滤膜浓缩",
            "params": [
                _p("浓缩倍数", None, "", NEEDS_LAB),
                _p("膜材质与截留分子量", None, "", NEEDS_LAB),
                _p("膜通量与清洗周期", None, "", NEEDS_LAB),
                _p("透过液去向", "返回配制或按水质确认后排放（待确认）", "", DEMO_ROUTE_SOURCE),
            ],
        },
        {
            "kind": "vessel", "tag": "T-201", "label": "活性炭脱色罐",
            "params": [
                _p("活性炭用量", None, "kg/批", NEEDS_LAB),
                _p("脱色温度", None, "℃", NEEDS_LAB),
                _p("脱色时间", None, "h", NEEDS_LAB),
                _p("透光率/色度目标", None, "", NEEDS_LAB),
            ],
        },
        {
            "kind": "filter", "tag": "F-201", "label": "脱色过滤",
            "params": [
                _p("过滤面积", None, "m²", NEEDS_LAB),
                _p("滤材与精度", None, "", NEEDS_LAB),
                _p("滤液澄清度", None, "", NEEDS_LAB),
                _p("压差与周期", None, "", NEEDS_LAB),
            ],
        },
        {
            "kind": "column", "tag": "C-201", "label": "离子交换精制柱",
            "params": [
                _p("树脂型号", None, "", NEEDS_LAB),
                _p("上样负荷", None, "", NEEDS_LAB),
                _p("洗脱与再生条件", None, "", NEEDS_LAB),
                _p("精制段收率", None, "%", NEEDS_LAB),
            ],
        },
        {
            "kind": "separation", "tag": "E-201", "label": "浓缩结晶釜",
            "params": [
                _p("分离方式", spec["separation"], "", DEMO_INPUT_SOURCE),
                _p("目标含量", spec["target_purity_pct"], "%", DEMO_INPUT_SOURCE),
                _p("通用经验含量区间", "–".join(str(value) for value in ((result.get("purification") or {}).get("expected_assay_range_pct") or [])), "%", EMPIRICAL_SOURCE),
                _p("结晶温度与过饱和度", None, "", NEEDS_LAB),
            ],
        },
        {
            "kind": "separation", "tag": "S-203", "label": "结晶离心与洗涤",
            "params": [
                _p("母液量", _row(result, "S05").get("mass_kg_batch"), "kg/批", DEMO_RESULT_SOURCE),
                _p("洗涤水量", None, "kg/批", NEEDS_LAB),
                _p("滤饼含湿", None, "%", NEEDS_LAB),
                _p("后段分离收率", spec["isolation_yield_pct"], "%", DEMO_INPUT_SOURCE),
            ],
        },
        {
            "kind": "dryer", "tag": "D-201", "label": "真空干燥",
            "params": [
                _p("干燥温度", None, "℃", NEEDS_LAB),
                _p("干燥时间", None, "h", NEEDS_LAB),
                _p("成品水分", None, "%", NEEDS_LAB),
                _p("分离/干燥能耗", spec["separation_energy_kwh"], "kWh/批", DEMO_INPUT_SOURCE),
            ],
        },
        {
            "kind": "product", "tag": "B-201", "label": "产品暂存与包装",
            "params": [
                _stream_param(result, "S04"),
                _p("产品物态", spec["product_state"], "", DEMO_INPUT_SOURCE),
                _p("目标含量", spec["target_purity_pct"], "%", DEMO_INPUT_SOURCE),
                _p("包装规格", None, "", NEEDS_LAB),
            ],
        },
        {
            "kind": "analysis", "tag": "AT-201", "label": "成品分析点",
            "params": [
                _p("含量（HPLC）", None, "%", NEEDS_LAB),
                _p("比旋度", None, "", NEEDS_LAB),
                _p("有关物质", None, "%", NEEDS_LAB),
                _p("分析方法与标准品", None, "", NEEDS_LAB),
            ],
            "notes": ["分析点仅表示取样位置，仪表与联锁待设计"],
        },
    ]

    nodes: list[dict] = []
    for index, block in enumerate(blocks):
        x, y = positions[index]
        nodes.append({
            "id": f"n{index + 1}",
            "kind": block["kind"],
            "tag": block["tag"],
            "label": block["label"],
            "x": x,
            "y": y,
            "w": 190.0,
            "h": 92.0,
            "params": block["params"],
            "notes": block.get("notes", []),
        })

    def node(identifier: str) -> dict:
        return next(item for item in nodes if item["id"] == identifier)

    waste_id = f"n{len(nodes) + 1}"
    nodes.append({
        "id": waste_id,
        "kind": "waste",
        "tag": "W-201",
        "label": "母液与废液暂存",
        "x": 40.0,
        "y": 750.0,
        "w": 190.0,
        "h": 92.0,
        "params": [
            _stream_param(result, "S05"),
            _p("处置方式", None, "", NEEDS_LAB),
            _p("可回收组分", None, "", NEEDS_LAB),
            _p("废物处置单价（演示输入）", spec["waste_cost_cny_per_kg"], "元/kg", DEMO_INPUT_SOURCE),
        ],
        "notes": ["处置与回收路线需按组成和当地法规确认"],
    })
    utility_id = f"n{len(nodes) + 1}"
    nodes.append({
        "id": utility_id,
        "kind": "other",
        "tag": "U-101",
        "label": "公用工程接口",
        "x": 1190.0,
        "y": 750.0,
        "w": 190.0,
        "h": 92.0,
        "params": [
            _p("冷却水", None, "m³/批", NEEDS_LAB),
            _p("低压蒸汽", None, "kg/批", NEEDS_LAB),
            _p("冷冻水", None, "kWh/批", NEEDS_LAB),
            _p("装机电力", None, "kW", NEEDS_LAB),
            _p("接口形式与用量", None, "", PENDING_LAB),
        ],
        "notes": ["仅表示公用工程接口位置，管网与计量待设计"],
    })

    stream_mass = {row.get("id"): row.get("mass_kg_batch") for row in result.get("aspen_streams") or []}

    def mass(stream_id: str) -> float:
        try:
            return float(stream_mass.get(stream_id))
        except (TypeError, ValueError):
            return 0.0

    s01, s02, s03, s04, s05 = (mass(item) for item in ("S01", "S02", "S03", "S04", "S05"))
    charge = round(s03 or (s01 + s02), 3)
    cake = 12.902
    permeate = 80.0
    regen = 8.0
    wet = 108.0
    clear = round(charge - cake, 3)
    concentrate = round(clear - permeate, 3)
    purified = round(concentrate - regen, 3)
    mother = round(purified - wet, 3)
    condensate = round(wet - s04, 3)
    sample = 0.5
    engine = DEMO_RESULT_SOURCE
    allocate = DEMO_ALLOC_SOURCE
    conserved = DEMO_CONSERVED_SOURCE

    # Every material stream carries a flow, and every unit closes: the whole
    # batch (S01 + S02 = S03) leaves as product (S04) plus the five labelled
    # waste routes (S05 = 离心滤饼 + 膜透过液 + 再生废液 + 母液/洗液 + 冷凝液).
    # The split itself is a fictional demonstration allocation; the totals come
    # from the platform assessment engine.
    chain: list[tuple[str, str, str, list[dict], str]] = [
        ("n1", "n3", "反式肉桂酸与辅料投料", [_mass("平台物流 S01 原料与试剂", s01, engine)], "material"),
        ("n2", "n3", "氨水与工艺水", [_mass("平台物流 S02 工艺水（溶剂）", s02, engine)], "material"),
        ("n3", "n4", "混合进料", [_mass("混合进料 = S01 + S02", charge, conserved)], "material"),
        ("n4", "n5", "预热进料", [_mass("预热进料（换热不改变质量）", charge, conserved)], "material"),
        ("n5", "n6", "反应混合物", [_mass("平台物流 S03 反应混合物", charge, engine)], "material"),
        ("n6", "n7", "灭活液", [_mass("灭活液（暂按反应混合物总量；酸碱加入量待补）", charge, conserved)], "material"),
        ("n7", "n8", "离心清液", [_mass("离心清液 = 反应混合物 − 离心滤饼", clear, conserved)], "material"),
        ("n8", "n9", "浓缩液", [_mass("浓缩液 = 离心清液 − 膜透过液", concentrate, conserved)], "material"),
        ("n9", "n10", "脱色混合液", [_mass("脱色混合液（脱色助剂不计入物料衡算）", concentrate, conserved)], "material"),
        ("n10", "n11", "脱色清液", [_mass("脱色清液（脱色助剂不计入物料衡算）", concentrate, conserved)], "material"),
        ("n11", "n12", "精制液", [_mass("精制液 = 脱色清液 − 再生与洗脱废液", purified, conserved)], "material"),
        ("n12", "n13", "结晶浆料", [_mass("结晶浆料（结晶不改变质量）", purified, conserved)], "material"),
        ("n13", "n14", "湿品", [_mass("离心湿品（含母液）", wet, allocate)], "material"),
        ("n14", "n15", "成品", [_mass("平台物流 S04 产品", s04, engine)], "material"),
        ("n15", "n16", "成品留样（自 S04 成品中分出）", [_mass("成品留样量（含于 S04 成品量内）", sample, allocate)], "material"),
        ("n13", waste_id, "母液与洗液", [_mass("母液与洗液 = 结晶浆料 − 湿品", mother, conserved)], "material"),
        ("n7", waste_id, "离心滤饼", [_mass("离心滤饼（未反应原料、菌体与助滤剂）", cake, allocate)], "material"),
        ("n8", waste_id, "膜透过液", [_mass("膜透过液（水与低分子杂质）", permeate, allocate)], "material"),
        ("n11", waste_id, "再生与洗脱废液", [_mass("离子交换再生与洗脱废液", regen, allocate)], "material"),
        ("n14", waste_id, "干燥冷凝液", [_mass("干燥冷凝液", condensate, allocate)], "material"),
        (utility_id, "n4", "低压蒸汽", [_p("蒸汽用量", None, "kg/批", NEEDS_LAB)], "utility"),
        (utility_id, "n5", "冷却水", [_p("冷却水用量", None, "m³/批", NEEDS_LAB)], "utility"),
        (utility_id, "n12", "冷冻水", [_p("冷冻水用量", None, "kWh/批", NEEDS_LAB)], "utility"),
    ]

    # 管道附件与仪表：画在图上的阀门/仪表位置（概念示意，规格与联锁待设计）。
    # 有阀门才能看出「阀门画法」是否规范，也才能说明阀门画在管线上而不是
    # 单独一个方块里；位号与型式由流程图拓扑给出，不新增节点、不影响物料守恒。
    valves: dict[int, tuple[str, str]] = {
        1: ("valve_gate", "GV-101"), 2: ("valve_gate", "GV-102"),
        3: ("valve_control", "FV-201"), 5: ("valve_check", "CV-201"),
        7: ("valve_gate", "GV-103"), 10: ("valve_gate", "GV-104"),
        12: ("valve_control", "TV-202"), 14: ("valve_check", "CV-202"),
        16: ("valve_control", "LV-201"), 17: ("valve_gate", "GV-105"),
        20: ("valve_safety", "PSV-201"), 21: ("valve_control", "TV-201"),
        22: ("valve_gate", "GV-106"), 23: ("valve_control", "TCV-203"),
    }
    instruments: dict[int, list[str]] = {
        3: ["FIC-201"], 6: ["TI-201"], 12: ["TI-202"],
        14: ["QI-201"], 21: ["PI-201"], 23: ["TI-203"],
    }

    edges: list[dict] = []
    for index, (source, target, label, params, media) in enumerate(chain):
        position = index + 1
        component = valves.get(position)
        edges.append({
            "id": f"e{position}",
            "source": source,
            "target": target,
            "label": label,
            "params": params,
            "media": media,
            "components": ([{"kind": component[0], "tag": component[1]}] if component else []),
            "instruments": list(instruments.get(position) or []),
        })

    graph = {"nodes": nodes, "edges": edges}
    PfdGraph(**graph)
    return graph


def demo_detail(case: dict) -> dict:
    spec = ScaleInput(**case["spec"])
    result = calculate(case["spec"])
    graph = build_demo_graph(case)
    return {
        "slug": case["slug"],
        "title": case["title"],
        "product": case["product"],
        "case_note": case["case_note"],
        "route": case["route"],
        "data_note": case["data_note"],
        "disclaimer": DISCLAIMER,
        "pending_label": PENDING,
        "assessment": spec.model_dump(),
        "graph": graph,
        "node_count": len(graph["nodes"]),
        "edge_count": len(graph["edges"]),
        "result_summary": {
            "risk_level": result.get("risk_level"),
            "pmi": (result.get("mass_metrics") or {}).get("pmi"),
            "e_factor": (result.get("mass_metrics") or {}).get("e_factor"),
            "total_kwh": (result.get("utilities") or {}).get("total_kwh"),
            "known_total_cny": (result.get("cost") or {}).get("known_total_cny"),
            "equipment": [item.get("tag") for item in result.get("equipment") or []],
        },
    }


def demo_summary(case: dict) -> dict:
    spec = case["spec"]
    return {
        "slug": case["slug"],
        "title": case["title"],
        "product": case["product"],
        "case_note": case["case_note"],
        "target_product_kg": spec["target_product_kg"],
        "material_count": len(spec["materials"]),
    }


@router.get("/cases")
def list_demo_cases(project_id: str, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    require_project_access(db, project_id, user)
    return [demo_summary(case) for case in CASES.values()]


@router.get("/cases/{slug}")
def get_demo_case(project_id: str, slug: str, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    require_project_access(db, project_id, user)
    case = CASES.get(slug)
    if not case:
        raise HTTPException(404, "演示案例不存在")
    return demo_detail(case)
