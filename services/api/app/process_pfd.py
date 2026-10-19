"""Browser-editable concept-level PFD/CAD workspace for process engineering.

The workspace generates an editable draft from a saved scale-up assessment,
lets the user move/edit unit-operation nodes and material streams inside the
project permission boundary, keeps immutable versions, and exports SVG/DXF.

Concept level only. The generator never invents pipe diameters, equipment
mechanical strength, instruments, safety interlocks or validated capacity;
every placed parameter carries its source and unit, and missing engineering
data is labelled 待补. Saving a diagram never writes back to the assessment.
"""
from __future__ import annotations

import html
import math
import re
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import ForeignKey, JSON, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from . import pfd_cad_sheet as _sheet
from . import pfd_symbols as _symbols
from .auth import UserContext, get_current_user, require_project_access
from .database import Base, get_db
from .models import AuditEvent, Project, new_id

router = APIRouter(prefix="/api/v1/drug-discovery/projects/{project_id}/process/pfd")

DISCLAIMER = (
    "概念级工艺流程图，仅供研发讨论，非施工图或已验证 P&ID。"
    "管径、仪表、安全联锁、设备强度与已验证产能均待合格工程人员设计。"
)
PENDING = "待补"
PENDING_SOURCE = "需合格工程人员设计（概念图不提供）"
EMPIRICAL_SOURCE = "通用单元操作经验范围，不是对该化合物的保证值"
MAX_DIAGRAMS_PER_PROJECT = 60

NodeKind = Literal[
    "feed", "reactor", "vessel", "pump", "heat_exchanger", "separation",
    "filter", "dryer", "column", "compressor", "product", "waste", "analysis", "other",
]

KIND_LABELS: dict[str, str] = {
    "feed": "进料/计量",
    "reactor": "反应设备",
    "vessel": "储罐/暂存",
    "pump": "泵/输送",
    "heat_exchanger": "换热/冷凝",
    "separation": "分离/纯化",
    "filter": "过滤",
    "dryer": "干燥",
    "column": "精馏/层析柱",
    "compressor": "压缩/风机",
    "product": "产品",
    "waste": "废物/母液",
    "analysis": "分析点（待设计）",
    "other": "其他单元",
}
KIND_ORDER: tuple[str, ...] = tuple(KIND_LABELS)


class PfdDiagram(Base):
    __tablename__ = "process_pfd_diagrams"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    data: Mapped[dict] = mapped_column(JSON)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)


class Param(Strict):
    """A parameter shown on the diagram. Unit and source are mandatory."""

    name: str = Field(min_length=1, max_length=40)
    value: str = Field(default=PENDING, max_length=60)
    unit: str = Field(default="", max_length=20)
    source: str = Field(max_length=160)

    @field_validator("source")
    @classmethod
    def _source_is_mandatory(cls, value: str) -> str:
        if not value:
            raise ValueError("参数来源不能为空；请填写来源出处，或明确标注“待补”。")
        return value


ComponentKind = Literal[
    "valve_gate", "valve_globe", "valve_ball", "valve_butterfly", "valve_check",
    "valve_control", "valve_electric", "valve_safety", "valve_regulator",
]


class ComponentSpec(Strict):
    """画在管线上的管道附件（阀门等），不新增节点、不影响物料守恒。"""

    kind: ComponentKind
    tag: str = Field(default="", max_length=24)


class OutletSpec(Strict):
    """单元的差额去向（外送/排放），例如底部排污 50 kg/h。"""

    name: str = Field(min_length=1, max_length=40)
    value: str = Field(default=PENDING, max_length=40)
    unit: str = Field(default="kg/h", max_length=20)
    source: str = Field(max_length=160)

    @field_validator("source")
    @classmethod
    def _source_is_mandatory(cls, value: str) -> str:
        if not value:
            raise ValueError("差额去向必须填写来源/依据，或明确标注“待补”。")
        return value


class UtilitySpec(Strict):
    """单元上的一条公用工程接管：介质、规格、用量与接入方位。"""

    medium: str = Field(default="", max_length=40)
    service: str = Field(default="", max_length=80)
    value: str = Field(default=PENDING, max_length=40)
    unit: str = Field(default="", max_length=20)
    port: Literal["", "w", "e", "n", "s"] = ""
    direction: Literal["", "in", "out"] = ""
    source: str = Field(default="", max_length=160)


class ReliefSpec(Strict):
    """安全阀组 / 放空 / 排空的位置、定压与依据。"""

    kind: Literal["safety_valve", "vent", "drain", "rupture_disc"] = "safety_valve"
    tag: str = Field(default="", max_length=24)
    value: str = Field(default="", max_length=40)
    unit: str = Field(default="", max_length=20)
    to: str = Field(default="", max_length=40)
    source: str = Field(default="", max_length=160)


class SignalSpec(Strict):
    """仪表信号回路：由哪个位号控制哪个执行机构，信号形式是什么。"""

    tag: str = Field(min_length=1, max_length=24)
    target: str = Field(default="", max_length=24)
    kind: Literal["", "electric", "pneumatic", "interlock"] = ""
    source: str = Field(default="", max_length=160)


class PfdNode(Strict):
    id: str = Field(min_length=2, max_length=40, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    kind: NodeKind
    tag: str = Field(default="", max_length=24)
    label: str = Field(min_length=1, max_length=80)
    x: float = Field(ge=-100000, le=100000)
    y: float = Field(ge=-100000, le=100000)
    w: float = Field(default=180, ge=20, le=1200)
    h: float = Field(default=84, ge=20, le=800)
    params: list[Param] = Field(default_factory=list, max_length=12)
    notes: list[str] = Field(default_factory=list, max_length=6)
    outlets: list[OutletSpec] = Field(default_factory=list, max_length=6)
    utilities: list[UtilitySpec] = Field(default_factory=list, max_length=8)
    reliefs: list[ReliefSpec] = Field(default_factory=list, max_length=6)
    synthetic: bool = False


class PfdEdge(Strict):
    id: str = Field(min_length=2, max_length=40, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    source: str = Field(min_length=2, max_length=40)
    target: str = Field(min_length=2, max_length=40)
    label: str = Field(default="", max_length=80)
    params: list[Param] = Field(default_factory=list, max_length=8)
    media: Literal["material", "utility"] = "material"
    components: list[ComponentSpec] = Field(default_factory=list, max_length=6)
    instruments: list[str] = Field(default_factory=list, max_length=4)
    service: str = Field(default="", max_length=80)
    signals: list[SignalSpec] = Field(default_factory=list, max_length=6)


class PfdGraph(Strict):
    nodes: list[PfdNode] = Field(default_factory=list, max_length=200)
    edges: list[PfdEdge] = Field(default_factory=list, max_length=400)

    @model_validator(mode="after")
    def validate_references(self):
        node_ids = [node.id for node in self.nodes]
        if len(set(node_ids)) != len(node_ids):
            raise ValueError("节点编号不能重复")
        known = set(node_ids)
        edge_ids = [edge.id for edge in self.edges]
        if len(set(edge_ids)) != len(edge_ids):
            raise ValueError("连接线编号不能重复")
        for edge in self.edges:
            if edge.source not in known or edge.target not in known:
                raise ValueError("连接线必须指向图中已存在的节点")
            if edge.source == edge.target:
                raise ValueError("连接线不能自连")
        return self


class UpdateRequest(Strict):
    title: str = Field(min_length=1, max_length=160)
    graph: PfdGraph
    base_revision: int = Field(ge=1, le=1_000_000)


class CreateRequest(Strict):
    title: str = Field(default="概念 PFD", min_length=1, max_length=160)
    source_assessment_id: str | None = Field(default=None, max_length=36)
    version_of: str | None = Field(default=None, max_length=36)
    graph: PfdGraph | None = None


class VersionRequest(Strict):
    title: str | None = Field(default=None, min_length=1, max_length=160)


def _num(value) -> str:
    """Render numeric parameters without noisy trailing zeros."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer() and abs(number) < 10 ** 15:
        return str(int(number))
    text = f"{number:.4f}".rstrip("0").rstrip(".")
    return text or "0"


def _param(name: str, value, unit: str, source: str) -> dict:
    if value is None or value == "":
        text = PENDING
    elif isinstance(value, bool):
        text = str(value)
    elif isinstance(value, (int, float)):
        text = _num(value)
    else:
        text = str(value)
    return {"name": name, "value": text, "unit": unit, "source": source}


def _kind_for(label: str) -> str:
    for keyword, kind in (
        ("废", "waste"), ("产品", "product"), ("灌装", "product"), ("充装", "product"),
        ("干燥", "dryer"), ("分析", "analysis"), ("压缩", "compressor"), ("风机", "compressor"),
        ("泵", "pump"), ("换热", "heat_exchanger"), ("冷凝", "heat_exchanger"),
        ("层析", "column"), ("精馏", "column"), ("蒸馏", "column"),
        ("结晶", "separation"), ("过滤", "filter"), ("萃取", "separation"),
        ("净化", "separation"), ("浓缩", "separation"), ("分离", "separation"),
        ("计量", "feed"), ("投料", "feed"), ("进料", "feed"),
        ("暂存", "vessel"), ("储罐", "vessel"), ("罐", "vessel"),
        ("原料", "feed"), ("反应", "reactor"),
    ):
        if keyword in label:
            return kind
    return "other"

def build_draft(output: dict) -> dict:
    """Build an editable concept PFD draft from one saved scale-up assessment.

    Only values that already exist in the assessment are placed, each tagged
    with its origin. Engineering details the platform does not compute are
    written as 待补 instead of being guessed.
    """
    spec = output["spec"]
    result = output["result"]
    name = str(spec.get("name", ""))[:60]
    src_input = f"放大评估《{name}》输入"
    src_result = f"放大评估《{name}》计算结果"
    src_equip = f"放大评估《{name}》设备初选"
    src_stream = f"放大评估《{name}》物流表"
    steps = [str(step) for step in result.get("pfd") or []]
    if not steps:
        raise HTTPException(422, "该放大评估没有可用的概念流程步骤，无法生成图纸")

    equipment = result.get("equipment") or []
    reactor_spec = equipment[0] if equipment else {}
    downstream_spec = equipment[1] if len(equipment) > 1 else {}
    streams = {row.get("id"): row for row in result.get("aspen_streams") or []}

    def stream_param(stream_id: str) -> dict | None:
        row = streams.get(stream_id)
        if not row:
            return None
        return _param(
            f"{stream_id} {row.get('name', '')}".strip(),
            f"{row.get('mass_kg_batch')}",
            "kg/批",
            src_stream,
        )

    nodes: list[dict] = []
    for index, step in enumerate(steps):
        nodes.append({
            "id": f"n{index + 1}",
            "kind": _kind_for(step),
            "tag": "",
            "label": step[:80],
            "x": float(40 + index * 220),
            "y": 140.0,
            "w": 180.0,
            "h": 84.0,
            "params": [],
            "notes": [],
        })

    reactor = next((node for node in nodes if node["kind"] == "reactor"), None)
    separation = next((node for node in nodes if node["kind"] == "separation"), None)
    product = next((node for node in nodes if node["kind"] == "product"), None)
    first_feed = next((node for node in nodes if node["kind"] == "feed"), None)
    inlet = first_feed or next((node for node in nodes if node["kind"] == "vessel"), None)

    if inlet is not None:
        raw = stream_param("S01")
        if raw:
            inlet["params"].append(raw)
    if reactor is not None:
        solvent = stream_param("S02")
        if solvent:
            reactor["params"].append(solvent)
        reactor["tag"] = str(reactor_spec.get("tag", ""))[:24]
        reactor["params"].extend([
            _param("工作体积", reactor_spec.get("working_volume_l"), "L", src_result),
            _param("名义容积（初选）", reactor_spec.get("suggested_size_l"), "L", src_equip),
            _param("装载率", reactor_spec.get("fill_pct"), "%", src_result),
            _param("材质初选", reactor_spec.get("material") or result.get("materials_of_construction", {}).get("recommended"), "", src_equip),
            _param("温度", spec.get("temperature_c"), "℃", src_input),
            _param("压力", spec.get("pressure_bar"), "bar(a)", src_input),
            _param("反应时间", spec.get("reaction_time_h"), "h", src_input),
        ])
        reactor["notes"] = [f"评估风险：{result.get('risk_level', PENDING)}"] + [
            f"待补实验：{item}" for item in (reactor_spec.get("lab_data_needed") or [])[:2]
        ]
        reactor["params"].extend([
            _param("管径", None, "", PENDING_SOURCE),
            _param("仪表与安全联锁", None, "", PENDING_SOURCE),
            _param("设备强度/机械设计", None, "", PENDING_SOURCE),
        ])
    if separation is not None:
        separation["tag"] = str(downstream_spec.get("tag", ""))[:24]
        separation["params"].extend([
            _param("分离方式", spec.get("separation"), "", src_input),
            _param("目标纯度", spec.get("target_purity_pct"), "%", src_input),
            _param("通用经验含量区间", "–".join(str(v) for v in (result.get("purification", {}).get("expected_assay_range_pct") or [])), "%", EMPIRICAL_SOURCE),
        ])
    if product is not None:
        product_stream = stream_param("S04")
        if product_stream:
            product["params"].append(product_stream)
        product["params"].append(_param("产品物态", spec.get("product_state"), "", src_input))

    for node in nodes:
        if not node["params"]:
            node["params"].append(_param("设备参数", None, "", "需补充实验或工程数据"))

    anchor = next((node for node in reversed(nodes) if node["kind"] == "separation"), None) or reactor or nodes[-1]
    waste = {
        "id": f"n{len(nodes) + 1}",
        "kind": "waste",
        "tag": "",
        "label": "母液/废物流",
        "x": anchor["x"],
        "y": anchor["y"] + 170.0,
        "w": 180.0,
        "h": 84.0,
        "params": [item for item in [stream_param("S05")] if item] or [_param("物流数据", None, "", "需补充物流表")],
        "notes": ["处置与回收方式待确认"],
    }
    nodes.append(waste)

    by_id = {node["id"]: node for node in nodes}
    order = {node["id"]: index for index, node in enumerate(nodes)}
    cut = order[anchor["id"]]
    edges: list[dict] = []
    for left, right in zip(nodes, nodes[1:]):
        if right["id"] == waste["id"]:
            continue
        edges.append({"id": f"e{len(edges) + 1}", "source": left["id"], "target": right["id"], "label": "", "params": []})
    edges.append({"id": f"e{len(edges) + 1}", "source": anchor["id"], "target": waste["id"], "label": "母液/废物流", "params": []})

    def stream_value(stream_id: str) -> float | None:
        row = streams.get(stream_id) or {}
        value = row.get("mass_kg_batch")
        return float(value) if isinstance(value, (int, float)) else None

    charge_total = None
    first_in = stream_value("S01")
    second_in = stream_value("S02")
    if first_in is not None and second_in is not None:
        charge_total = _param("进料合计（S01 + S02，按质量守恒相加）", first_in + second_in, "kg/批", src_stream)

    for edge in edges:
        source_node = by_id[edge["source"]]
        target_node = by_id[edge["target"]]
        stream_id = None
        if target_node["kind"] == "product":
            stream_id = "S04"
        elif target_node["kind"] == "waste":
            stream_id = "S05"
        elif source_node["kind"] == "reactor":
            stream_id = "S03"
        elif order[source_node["id"]] >= cut:
            stream_id = "S04"
        item = stream_param(stream_id) if stream_id else charge_total
        if item:
            edge["params"].append(item)
            if stream_id:
                edge["label"] = str(streams.get(stream_id, {}).get("name", ""))[:80]
            else:
                edge["label"] = "反应进料"
        else:
            edge["params"].append(_param("物流数据", None, "", "需补充流量、组成与物料闭合"))

    draft = {"nodes": nodes, "edges": edges}
    PfdGraph(**draft)
    return {
        "title": f"{str(spec.get('name', ''))[:120]} · 概念 PFD",
        "source_assessment_id": output["id"],
        "graph": draft,
        "disclaimer": DISCLAIMER,
    }

KIND_FILL: dict[str, str] = {
    "feed": "#eef8f4", "reactor": "#e3f2ec", "vessel": "#eef4f1", "pump": "#f2f7f5",
    "heat_exchanger": "#f6f1e7", "separation": "#e9f4f7", "filter": "#e9f4f7",
    "dryer": "#f7f2e7", "column": "#eef0f7", "compressor": "#f2f7f5",
    "product": "#e6f4e9", "waste": "#f8efef", "analysis": "#f5f4ee", "other": "#f4f6f5",
}


def _param_text(param: dict) -> str:
    unit = f" {param['unit']}" if param.get("unit") else ""
    return f"{param['name']}：{param['value']}{unit}"


def _param_rows(graph: dict) -> list[tuple[str, str, str, str, str]]:
    rows: list[tuple[str, str, str, str, str]] = []
    for node in graph["nodes"]:
        for param in node["params"]:
            rows.append((node["tag"] or node["label"], param["name"], param["value"], param["unit"], param["source"]))
    for edge in graph["edges"]:
        for param in edge["params"]:
            rows.append((f"连线 {edge['id']}", param["name"], param["value"], param["unit"], param["source"]))
    return rows


def _extents(graph: dict) -> tuple[float, float, float, float]:
    nodes = graph["nodes"]
    if not nodes:
        return 0.0, 0.0, 900.0, 320.0
    min_x = min(node["x"] for node in nodes) - 60.0
    min_y = min(node["y"] for node in nodes) - 60.0
    max_x = max(node["x"] + node["w"] for node in nodes) + 60.0
    max_y = max(node["y"] + node["h"] for node in nodes) + 60.0
    return min_x, min_y, max_x, max_y


def _anchors(source: dict, target: dict) -> tuple[tuple[float, float], tuple[float, float]]:
    if target["y"] >= source["y"] + source["h"] - 1:
        return (source["x"] + source["w"] / 2, source["y"] + source["h"]), (target["x"] + target["w"] / 2, target["y"])
    if target["x"] >= source["x"] + source["w"] - 1:
        return (source["x"] + source["w"], source["y"] + source["h"] / 2), (target["x"], target["y"] + target["h"] / 2)
    if target["x"] + target["w"] <= source["x"] + 1:
        return (source["x"], source["y"] + source["h"] / 2), (target["x"] + target["w"], target["y"] + target["h"] / 2)
    return (source["x"] + source["w"] / 2, source["y"]), (target["x"] + target["w"] / 2, target["y"] + target["h"])


def render_svg(title: str, graph: dict) -> str:
    """Render a standalone, script-free SVG that never trusts user markup."""
    nodes = graph["nodes"]
    edges = graph["edges"]
    by_id = {node["id"]: node for node in nodes}
    min_x, min_y, max_x, max_y = _extents(graph)
    draw_w = max(700.0, max_x - min_x)
    draw_h = max(260.0, max_y - min_y)
    table_top = 78.0 + draw_h + 30.0
    rows = _param_rows(graph)
    width = max(960.0, draw_w + 64.0)
    height = table_top + 46.0 + 22.0 * max(1, len(rows)) + 46.0

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
        f'viewBox="0 0 {width:.0f} {height:.0f}" font-family="Inter, Noto Sans SC, Microsoft YaHei, Arial, sans-serif">',
        '<defs><marker id="pfd-arrow" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto">'
        '<path d="M0,0 L0,6 L9,3 z" fill="#0f7d68"/></marker></defs>',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="32" y="40" font-size="19" font-weight="700" fill="#123d35">{html.escape(title)}</text>',
        '<text x="32" y="62" font-size="11.5" fill="#607c74">概念级工艺流程图，仅供研发讨论，非施工图或已验证 P&amp;ID；'
        '管径、仪表、安全联锁、设备强度与已验证产能均待合格工程人员设计。</text>',
        f'<g transform="translate({32 - min_x:.2f},{78 - min_y:.2f})">',
    ]
    for edge in edges:
        source = by_id.get(edge["source"])
        target = by_id.get(edge["target"])
        if not source or not target:
            continue
        (x1, y1), (x2, y2) = _anchors(source, target)
        parts.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="#0f7d68" stroke-width="1.6" marker-end="url(#pfd-arrow)"/>')
        if edge.get("label"):
            parts.append(f'<text x="{(x1 + x2) / 2:.1f}" y="{(y1 + y2) / 2 - 5:.1f}" font-size="10.5" text-anchor="middle" fill="#3c6a60">{html.escape(edge["label"])}</text>')
    for node in nodes:
        fill = KIND_FILL.get(node["kind"], "#f4f6f5")
        stroke = "#a63b3b" if node["kind"] == "waste" else "#087e6b"
        parts.append(f'<rect x="{node["x"]:.1f}" y="{node["y"]:.1f}" width="{node["w"]:.1f}" height="{node["h"]:.1f}" rx="9" fill="{fill}" stroke="{stroke}" stroke-width="1.4"/>')
        header = f'{node["tag"]} {node["label"]}'.strip()
        parts.append(f'<text x="{node["x"] + 10:.1f}" y="{node["y"] + 19:.1f}" font-size="12.5" font-weight="700" fill="#123d35">{html.escape(header)}</text>')
        parts.append(f'<text x="{node["x"] + 10:.1f}" y="{node["y"] + 33:.1f}" font-size="9.5" fill="#6b837c">{html.escape(KIND_LABELS.get(node["kind"], node["kind"]))}</text>')
        for index, param in enumerate(node["params"][:3]):
            parts.append(f'<text x="{node["x"] + 10:.1f}" y="{node["y"] + 48 + index * 12:.1f}" font-size="9.5" fill="#38564f">{html.escape(_param_text(param))}</text>')
        if len(node["params"]) > 3:
            parts.append(f'<text x="{node["x"] + 10:.1f}" y="{node["y"] + 48 + 3 * 12:.1f}" font-size="9" fill="#8a9c96">另有 {len(node["params"]) - 3} 项参数，见下方参数表</text>')
    parts.append("</g>")

    column_x = [32.0, 190.0, 330.0, 420.0, 470.0]
    parts.append(f'<text x="32" y="{table_top - 10:.0f}" font-size="13" font-weight="700" fill="#123d35">参数、单位与来源（图纸依据）</text>')
    header = ["对象", "参数", "数值", "单位", "来源"]
    for x, text in zip(column_x, header):
        parts.append(f'<text x="{x:.0f}" y="{table_top + 12:.0f}" font-size="11" font-weight="700" fill="#0f5f50">{text}</text>')
    if not rows:
        parts.append(f'<text x="32" y="{table_top + 36:.0f}" font-size="11" fill="#607c74">图中暂无参数；请在编辑器中补充并注明来源与单位。</text>')
    for index, row in enumerate(rows):
        y = table_top + 34.0 + index * 22.0
        for x, value in zip(column_x, row):
            parts.append(f'<text x="{x:.0f}" y="{y:.0f}" font-size="10.5" fill="#38564f">{html.escape(str(value))}</text>')
    parts.append(f'<text x="32" y="{height - 18:.0f}" font-size="10.5" fill="#8a9c96">导出时间戳与版本见文件名；图面数值来自放大评估或用户填写，缺失项标为“待补”。</text>')
    parts.append("</svg>")
    return "".join(parts)


def render_dxf(title: str, graph: dict) -> str:
    """Render an AutoCAD R12 (AC1009) DXF using LINE/TEXT entities only.

    The file is written with $DWGCODEPAGE ANSI_936 so Chinese labels survive in
    AutoCAD and other CAD readers that follow the R12 codepage convention.
    """
    nodes = graph["nodes"]
    edges = graph["edges"]
    by_id = {node["id"]: node for node in nodes}
    min_x, min_y, max_x, max_y = _extents(graph)
    lines: list[str] = []

    def put(code, value=""):
        lines.append(str(code))
        lines.append(str(value))

    def layer(name, color, linetype="CONTINUOUS"):
        put(0, "LAYER")
        put(2, name)
        put(70, 0)
        put(62, color)
        put(6, linetype)

    def text(x, y, height, value, layer_name, style="PFD_CN"):
        put(0, "TEXT")
        put(8, layer_name)
        put(10, f"{x:.3f}")
        put(20, f"{y:.3f}")
        put(30, "0.0")
        put(40, f"{height:.3f}")
        put(1, value)
        put(7, style)
        put(72, 0)
        put(11, f"{x:.3f}")
        put(21, f"{y:.3f}")
        put(31, "0.0")

    def line(x1, y1, x2, y2, layer_name):
        put(0, "LINE")
        put(8, layer_name)
        put(10, f"{x1:.3f}")
        put(20, f"{y1:.3f}")
        put(30, "0.0")
        put(11, f"{x2:.3f}")
        put(21, f"{y2:.3f}")
        put(31, "0.0")

    put(0, "SECTION")
    put(2, "HEADER")
    put(9, "$ACADVER")
    put(1, "AC1009")
    put(9, "$DWGCODEPAGE")
    put(3, "ANSI_936")
    put(9, "$MEASUREMENT")
    put(70, 1)
    put(9, "$EXTMIN")
    put(10, f"{min_x:.3f}")
    put(20, f"{min_y:.3f}")
    put(30, "0.0")
    put(9, "$EXTMAX")
    put(10, f"{max_x:.3f}")
    put(20, f"{max_y:.3f}")
    put(30, "0.0")
    put(0, "ENDSEC")

    put(0, "SECTION")
    put(2, "TABLES")
    put(0, "TABLE")
    put(2, "LTYPE")
    put(70, 3)
    for name, description in (("CONTINUOUS", "Solid line"), ("ByBlock", ""), ("ByLayer", "")):
        put(0, "LTYPE")
        put(2, name)
        put(70, 0)
        put(3, description)
        put(72, 65)
        put(73, 0)
        put(40, "0.0")
    put(0, "ENDTAB")
    put(0, "TABLE")
    put(2, "LAYER")
    put(70, 8)
    for name, color in (
        ("PFD_EQUIPMENT", 5), ("PFD_TAG", 7), ("PFD_LABEL", 3), ("PFD_PARAM", 8),
        ("PFD_FLOW", 4), ("PFD_ARROW", 4), ("PFD_TITLE", 7), ("PFD_NOTE", 8),
    ):
        layer(name, color)
    put(0, "ENDTAB")
    put(0, "TABLE")
    put(2, "STYLE")
    put(70, 2)
    for name, font, big in (("Standard", "txt", ""), ("PFD_CN", "simplex.shx", "gbcbig.shx")):
        put(0, "STYLE")
        put(2, name)
        put(70, 0)
        put(40, "0.0")
        put(41, "1.0")
        put(50, "0.0")
        put(71, 0)
        put(42, "4.0")
        put(3, font)
        put(4, big)
    put(0, "ENDTAB")
    put(0, "TABLE")
    put(2, "APPID")
    put(70, 1)
    put(0, "APPID")
    put(2, "ACAD")
    put(70, 0)
    put(0, "ENDTAB")
    put(0, "ENDSEC")

    put(0, "SECTION")
    put(2, "ENTITIES")
    text(min_x, max_y + 12.0, 9.0, f"{title}（概念级，非施工图）", "PFD_TITLE")
    for edge in edges:
        source = by_id.get(edge["source"])
        target = by_id.get(edge["target"])
        if not source or not target:
            continue
        (x1, y1), (x2, y2) = _anchors(source, target)
        line(x1, y1, x2, y2, "PFD_FLOW")
        length = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5 or 1.0
        ux, uy = (x2 - x1) / length, (y2 - y1) / length
        for spread in (0.42, -0.42):
            bx = x2 - ux * 6.0 * 0.9 + (-uy) * 6.0 * spread * 0.72
            by = y2 - uy * 6.0 * 0.9 + (ux) * 6.0 * spread * 0.72
            line(x2, y2, bx, by, "PFD_ARROW")
        if edge.get("label"):
            text((x1 + x2) / 2, (y1 + y2) / 2 + 3.0, 3.0, str(edge["label"]), "PFD_LABEL")
        for index, param in enumerate(edge["params"]):
            text(x1 + 2.0, y1 - 4.0 - index * 5.0, 2.6, _param_text(param), "PFD_PARAM")
    for node in nodes:
        x, y, w, h = node["x"], node["y"], node["w"], node["h"]
        for x1, y1, x2, y2 in ((x, y, x + w, y), (x + w, y, x + w, y + h), (x + w, y + h, x, y + h), (x, y + h, x, y)):
            line(x1, y1, x2, y2, "PFD_EQUIPMENT")
        text(x + 6.0, y + h - 8.0, 4.2, f'{node["tag"]} {node["label"]}'.strip(), "PFD_TAG")
        text(x + 6.0, y + h - 15.0, 3.0, KIND_LABELS.get(node["kind"], node["kind"]), "PFD_LABEL")
        for index, param in enumerate(node["params"]):
            text(x + 6.0, y + h - 22.0 - index * 6.0, 3.0, _param_text(param), "PFD_PARAM")
        for index, note in enumerate(node["notes"]):
            text(x + 6.0, y - 5.0 - index * 5.0, 2.8, note, "PFD_NOTE")
    text(min_x, min_y - 10.0, 3.2, "概念级工艺流程图，仅供研发讨论，非施工图或已验证 P&ID；管径、仪表、安全联锁、设备强度与已验证产能均待合格工程人员设计。", "PFD_NOTE")
    put(0, "ENDSEC")
    put(0, "EOF")
    return "\n".join(lines) + "\n"

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_data(title: str, graph: dict, version: int, root_id: str | None,
              previous_id: str | None, source_assessment_id: str | None) -> dict:
    stamp = _now()
    return {
        "title": title,
        "version": version,
        "root_id": root_id,
        "previous_id": previous_id,
        "source_assessment_id": source_assessment_id,
        "revision": 1,
        "created_at": stamp,
        "updated_at": stamp,
        "graph": graph,
    }


def _load(db: Session, project_id: str, diagram_id: str, user, minimum: str = "viewer") -> PfdDiagram:
    require_project_access(db, project_id, user, minimum)
    item = db.get(PfdDiagram, diagram_id)
    if not item or item.project_id != project_id:
        raise HTTPException(404, "图纸不存在")
    return item


def _graph_of(item: PfdDiagram) -> dict:
    graph = item.data.get("graph") or {}
    return {"nodes": graph.get("nodes") or [], "edges": graph.get("edges") or []}


def _summary(item: PfdDiagram) -> dict:
    graph = _graph_of(item)
    return {
        "id": item.id,
        "title": item.data.get("title", ""),
        "version": item.data.get("version", 1),
        "root_id": item.data.get("root_id") or item.id,
        "previous_id": item.data.get("previous_id"),
        "source_assessment_id": item.data.get("source_assessment_id"),
        "revision": item.data.get("revision", 1),
        "created_at": item.data.get("created_at"),
        "updated_at": item.data.get("updated_at"),
        "node_count": len(graph["nodes"]),
        "edge_count": len(graph["edges"]),
    }


def _detail(item: PfdDiagram) -> dict:
    return {**_summary(item), "graph": _graph_of(item), "disclaimer": DISCLAIMER, "pending_label": PENDING}


def _assert_capacity(db: Session, project_id: str) -> None:
    total = len(db.scalars(select(PfdDiagram.id).where(PfdDiagram.project_id == project_id)).all())
    if total >= MAX_DIAGRAMS_PER_PROJECT:
        raise HTTPException(422, f"每个项目最多保留 {MAX_DIAGRAMS_PER_PROJECT} 份概念图纸（含历史版本）")


def _load_assessment_output(db: Session, project_id: str, assessment_id: str) -> dict:
    from .process_scaleup import ScaleAssessment, record_output

    item = db.get(ScaleAssessment, assessment_id)
    if not item or item.project_id != project_id:
        raise HTTPException(404, "放大评估不存在")
    try:
        return record_output(item)
    except HTTPException:
        raise
    except Exception:
        # An assessment saved by an earlier release may no longer satisfy the current
        # input schema. Fall back to the stored payload so a drawing can still be
        # produced, and report a clear message when even that has no concept steps.
        data = dict(item.data or {})
        result = data.get("result")
        if not isinstance(result, dict) or not (result.get("pfd") or []):
            raise HTTPException(422, "该放大评估没有可用的概念流程步骤，无法生成图纸")
        for key in ("materials_of_construction", "purification"):
            if not isinstance(result.get(key), dict):
                result[key] = {}
        if not isinstance(data.get("spec"), dict):
            data["spec"] = {}
        data["result"] = result
        return {"id": item.id, **data}


@router.get("/draft-from-assessment")
def draft_from_assessment(project_id: str, assessment_id: str, db: Session = Depends(get_db),
                          user: UserContext = Depends(get_current_user)):
    """Return an unsaved draft graph generated from a saved scale-up assessment."""
    require_project_access(db, project_id, user)
    return build_draft(_load_assessment_output(db, project_id, assessment_id))


@router.get("/diagrams")
def list_diagrams(project_id: str, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    require_project_access(db, project_id, user)
    rows = db.scalars(select(PfdDiagram).where(PfdDiagram.project_id == project_id)).all()
    items = sorted((_summary(row) for row in rows), key=lambda row: (row["title"], row["version"], row["created_at"] or ""))
    return items


@router.post("/diagrams")
def create_diagram(project_id: str, payload: CreateRequest, db: Session = Depends(get_db),
                   user: UserContext = Depends(get_current_user)):
    require_project_access(db, project_id, user, "member")
    _assert_capacity(db, project_id)
    if payload.version_of:
        previous = _load(db, project_id, payload.version_of, user, "member")
        data = previous.data
        new_data = _new_data(
            payload.title or data.get("title") or "概念 PFD",
            _graph_of(previous),
            int(data.get("version", 1)) + 1,
            data.get("root_id") or previous.id,
            previous.id,
            data.get("source_assessment_id"),
        )
        action = "process.pfd_version_created"
        detail = {"previous_id": previous.id, "version": new_data["version"]}
    elif payload.graph is not None:
        # A submitted graph is the drawing the user edited and saved: persist it verbatim.
        # The source assessment is recorded for provenance only and is never re-read to
        # rebuild the graph here, otherwise edits made on a generated draft would be lost.
        source = payload.source_assessment_id
        if source:
            _load_assessment_output(db, project_id, source)
        new_data = _new_data(payload.title, payload.graph.model_dump(), 1, None, None, source)
        action = "process.pfd_created"
        detail = {"source_assessment_id": source, "nodes": len(new_data["graph"]["nodes"])}
    elif payload.source_assessment_id:
        draft = build_draft(_load_assessment_output(db, project_id, payload.source_assessment_id))
        new_data = _new_data(payload.title or draft["title"], draft["graph"], 1, None, None, draft["source_assessment_id"])
        action = "process.pfd_created"
        detail = {"source_assessment_id": draft["source_assessment_id"], "nodes": len(draft["graph"]["nodes"])}
    else:
        raise HTTPException(422, "新建图纸需要提供初始图或选择来源评估")
    item = PfdDiagram(project_id=project_id, data=new_data)
    db.add(item)
    db.flush()
    item.data = {**new_data, "root_id": new_data["root_id"] or item.id}
    db.add(AuditEvent(project_id=project_id, actor_id=user.id, action=action,
                      entity_type="process_pfd_diagram", entity_id=item.id, details=detail))
    db.commit()
    db.refresh(item)
    return _detail(item)


@router.post("/diagrams/{diagram_id}/versions")
def create_version(project_id: str, diagram_id: str, payload: VersionRequest | None = None,
                   db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    previous = _load(db, project_id, diagram_id, user, "member")
    _assert_capacity(db, project_id)
    data = previous.data
    title = (payload.title if payload and payload.title else None) or data.get("title") or "概念 PFD"
    new_data = _new_data(title, _graph_of(previous), int(data.get("version", 1)) + 1,
                         data.get("root_id") or previous.id, previous.id, data.get("source_assessment_id"))
    item = PfdDiagram(project_id=project_id, data=new_data)
    db.add(item)
    db.flush()
    item.data = {**new_data, "root_id": new_data["root_id"] or item.id}
    db.add(AuditEvent(project_id=project_id, actor_id=user.id, action="process.pfd_version_created",
                      entity_type="process_pfd_diagram", entity_id=item.id,
                      details={"previous_id": previous.id, "version": new_data["version"]}))
    db.commit()
    db.refresh(item)
    return _detail(item)


@router.get("/diagrams/{diagram_id}")
def get_diagram(project_id: str, diagram_id: str, db: Session = Depends(get_db),
                user: UserContext = Depends(get_current_user)):
    return _detail(_load(db, project_id, diagram_id, user))


@router.put("/diagrams/{diagram_id}")
def update_diagram(project_id: str, diagram_id: str, payload: UpdateRequest,
                   db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    item = _load(db, project_id, diagram_id, user, "member")
    current = int(item.data.get("revision", 1))
    if payload.base_revision != current:
        raise HTTPException(409, "图纸已被其他编辑更新，请重新打开后再保存。")
    item.data = {
        **item.data,
        "title": payload.title,
        "graph": payload.graph.model_dump(),
        "revision": current + 1,
        "updated_at": _now(),
    }
    db.add(AuditEvent(project_id=project_id, actor_id=user.id, action="process.pfd_updated",
                      entity_type="process_pfd_diagram", entity_id=item.id,
                      details={"revision": current + 1, "nodes": len(payload.graph.nodes), "edges": len(payload.graph.edges)}))
    db.commit()
    db.refresh(item)
    return _detail(item)


def _sheet_view(value: str | None) -> str:
    """Only the two published sheets exist: the section drawing and the legend."""
    view = str(value or "section").strip().lower()
    if view not in ("section", "legend"):
        raise HTTPException(400, "view 只能是 section（工段图）或 legend（图例与说明）。")
    return view


def _sheet_stamp() -> str:
    """One timestamp format for every rendered sheet, preview or export."""
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")


def _sheet_meta_for(meta: SheetMeta, db: Session, project_id: str, graph: dict,
                    item: PfdDiagram | None = None) -> SheetMeta:
    """Fill the title-block fields the caller left blank; never overwrite a value.

    The preview and the downloads therefore print the same project, product and
    version, and a saved diagram carries its own version, revision and
    data-source text without the browser having to know them.
    """
    project = meta.project
    if not project:
        record = db.get(Project, project_id)
        project = str(getattr(record, "name", "") or "")
    product = meta.product
    if not product:
        for node in graph.get("nodes", []):
            if node.get("kind") == "product":
                product = str(node.get("label") or node.get("tag") or "")
                break
    version = meta.version
    revision = meta.revision
    source = meta.source
    if item is not None:
        data = item.data or {}
        version = version or f"v{int(data.get('version', 1))}"
        revision = revision or str(data.get("revision", 1))
        if not source:
            source = ("由已保存放大评估生成" if data.get("source_assessment_id")
                      else "手工绘制或演示草稿")
    return SheetMeta(project=project[:80], product=product[:80], version=version[:40],
                     revision=revision[:40], source=source[:120])


@router.get("/diagrams/{diagram_id}/export")
def export_diagram_sheet(project_id: str, diagram_id: str, view: str = "section",
                       db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    """Download the saved drawing as the same CAD sheet the preview shows.

    view="section" downloads the process section sheet (第 2 张);
    view="legend" downloads the legend and convention sheet (第 1 张).

    路径刻意不带静态后缀（.svg/.js/.css/...）：共享门户对这些后缀的 GET 响应
    只按 URL 缓存、不校验 Authorization，缓存命中会把已保存图纸回放给未授权
    客户端；不带后缀的导出路径每次都必须通过项目权限校验。
    """
    sheet_view = _sheet_view(view)
    item = _load(db, project_id, diagram_id, user)
    graph = _graph_of(item)
    meta = _sheet_meta_for(SheetMeta(), db, project_id, graph, item)
    svg = sheet_svg(str(item.data.get("title") or "概念 PFD"), graph, meta, _sheet_stamp(), sheet_view)
    name = "legend" if sheet_view == "legend" else "sheet"
    return Response(svg, media_type="image/svg+xml", headers={
        "Content-Disposition": f'attachment; filename="concept-pfd-{name}-v{int(item.data.get("version", 1))}.svg"',
        "X-Content-Type-Options": "nosniff",
    })


@router.get("/diagrams/{diagram_id}/export.dxf")
def export_diagram_dxf(project_id: str, diagram_id: str, view: str = "section",
                       db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    """Download the saved drawing as the same R12 CAD sheet DXF the preview shows.

    view="section" downloads the process section sheet (第 2 张);
    view="legend" downloads the legend and convention sheet (第 1 张).
    """
    sheet_view = _sheet_view(view)
    item = _load(db, project_id, diagram_id, user)
    graph = _graph_of(item)
    meta = _sheet_meta_for(SheetMeta(), db, project_id, graph, item)
    dxf = sheet_dxf(str(item.data.get("title") or "概念 PFD"), graph, meta, _sheet_stamp(), sheet_view)
    name = "legend" if sheet_view == "legend" else "sheet"
    return Response(_gbk_safe(dxf).encode("gbk"), media_type="application/dxf", headers={
        "Content-Disposition": f'attachment; filename="concept-pfd-{name}-v{int(item.data.get("version", 1))}.dxf"',
        "X-Content-Type-Options": "nosniff",
    })


@router.get("/diagrams/{diagram_id}/export.svg")
def export_diagram_svg_legacy(project_id: str, diagram_id: str, view: str = "section",
                              user: UserContext = Depends(get_current_user)):
    """旧扩展名链接：303 跳转到无扩展名导出路径。

    .svg 是共享门户的静态缓存后缀，缓存按 URL 命中且不校验 Authorization；
    在该路径直接返回图纸会绕过项目鉴权，因此这里只跳转，内容由 /export 输出。
    """
    sheet_view = _sheet_view(view)
    return RedirectResponse(url=f"export?view={sheet_view}", status_code=303)


# --- CAD sheet rendering (in-browser preview + A-size sheet export) -------
#
# A sheet is a read-only, CAD-style presentation of the same concept graph:
# drawing frame, equipment symbols, material streams, tables and a title
# block. It is what the in-browser CAD preview shows and what the sheet
# download writes. It never introduces engineering data that is not already
# in the graph, and the concept-level disclaimer is printed on the sheet.

SHEET_STAGE = "概念设计 · 非施工图"
SHEET_SCALE = "不按比例（NTS）"
SHEET_SIZE = "A2 横放（自动排版）"
SHEET_FOOTNOTE = "本图由平台按概念级数据自动生成，供研发讨论；不得作为施工、采购或设计验证依据。"
SHEET_DATA_NOTE = (
    "参数、单位与来源：单元位号与名称标在符号旁，每条物流的流量与单位标在管线上；"
    "各类附表（设备、物流、管道附件与仪表、守恒校核）统一排在第 1 张图例图里，"
    "本图只留图面与右下角图签栏；缺失工程数据一律标“待补”，平台不推算管径、仪表、"
    "安全联锁、设备强度与已验证产能。"
)
SHEET_SYMBOL_NOTE = "设备图形为概念级通用单元操作符号，用于表达流程拓扑，不是定型设备图或施工详图。"
SHEET_BALANCE_NOTE = (
    "物料守恒：图中每条物流线的流量与单位相加校核；同一单元与全流程的进口合计必须等于出口合计，"
    "差额必须在图上注明去向（底部排污/排渣、放空或蒸汽夹带、母液与洗液、取样、设备持液与损耗），"
    "未标注的一律按“待补”处理。"
)


class SheetMeta(Strict):
    project: str = Field(default="", max_length=80)
    product: str = Field(default="", max_length=80)
    version: str = Field(default="", max_length=40)
    revision: str = Field(default="", max_length=40)
    source: str = Field(default="", max_length=120)


class SheetRequest(Strict):
    title: str = Field(default="概念 PFD", min_length=1, max_length=160)
    graph: PfdGraph
    meta: SheetMeta = Field(default_factory=SheetMeta)
    view: Literal["section", "legend"] = "section"


# --- material balance -------------------------------------------------------
#
# Every process stream may carry one mass-flow parameter (value + unit). The
# balance check adds those up per unit operation and over the whole flowsheet:
# a unit whose outlet mass is smaller than its inlet mass is an imbalance, and
# the difference has to be reported together with the route it leaves by
# (bottom purge, vent or steam stripping, mother liquor, sampling, hold-up and
# losses). The platform never guesses a split and never silently absorbs it.

FLOW_UNITS: tuple[str, ...] = ("kg/批", "kg/h", "t/批", "t/h")
_FLOW_UNIT_KEYS = frozenset(unit.lower() for unit in FLOW_UNITS)
BALANCE_ABS_TOL = 0.05
BALANCE_REL_TOL = 0.005
BALANCE_ROUTE_HINT = (
    "须注明去向：底部排污/排渣、放空或蒸汽夹带、母液与洗液、取样与留样、"
    "设备持液与损耗、循环回用"
)
BALANCE_RULES: tuple[str, ...] = (
    "物料守恒规则：同一单元（以及全流程）的“进料合计”必须等于“出料合计”，每条物流线都要标注流量与单位。",
    "进出口不相等时，差额必须在图上标注去向：底部排污/排渣、放空或蒸汽夹带、母液与洗液、取样与留样、设备持液与损耗、循环回用。",
    "未标注流量的物流按“待补”处理；平台不推测分配比例，也不代替合格工程人员完成物料衡算。",
    "公用工程线（蒸汽、冷却水、冷冻水等）不计入物料衡算，只标注接口位置与用量来源。",
)
SHEET_BAL_COLUMNS = ("单元（位号）", "进料合计", "出料合计", "差额（进−出）", "差额去向 / 说明", "校核")
SHEET_BAL_WEIGHTS = (0.20, 0.11, 0.11, 0.09, 0.36, 0.13)
SHEET_BAL_ROW = 16.0
SHEET_BAL_TITLE = "物料守恒校核（按图中已标注流量的物料物流相加；公用工程线不计入）"
SHEET_LINE_LEGEND_H = 104.0


def _parse_flow(param: dict) -> tuple[str, float] | None:
    """Read one stream flow from a parameter whose unit is a mass flow unit."""
    unit = str(param.get("unit") or "").strip()
    if unit.lower() not in _FLOW_UNIT_KEYS:
        return None
    text = str(param.get("value") or "").strip().replace(",", "").replace("，", "")
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    return unit, value


def _edge_flow(edge: dict) -> tuple[str, float] | None:
    for param in edge.get("params") or []:
        found = _parse_flow(param)
        if found is not None:
            return found
    return None


def _edge_is_utility(edge: dict) -> bool:
    """Utility lines are drawn and listed, but never added into the mass balance."""
    return str(edge.get("media") or "material") == "utility"


def _stream_flow_text(edge: dict) -> str:
    """Flow text printed under a stream tag on the drawing."""
    if _edge_is_utility(edge):
        return "公用工程（不计入物料衡算）"
    flow = _edge_flow(edge)
    if flow is None:
        return f"流量{PENDING}"
    return f"{_num(round(flow[1], 3))} {flow[0]}"


def _combined(*buckets: dict) -> dict:
    """把同一单元的两档出料（管线 + 差额去向）按单位相加。"""
    total: dict[str, float] = {}
    for bucket in buckets:
        for unit, value in bucket.items():
            total[unit] = total.get(unit, 0.0) + value
    return total


def _mass_text(values: dict) -> str:
    if not values:
        return PENDING
    if len(values) > 1:
        return "单位不一致"
    unit, total = next(iter(values.items()))
    return f"{_num(round(total, 3))} {unit}"


def _mass_gap(difference: float, unit: str) -> str:
    if abs(difference) <= BALANCE_ABS_TOL:
        return f"0 {unit}" if unit else "0"
    sign = "+" if difference > 0 else "−"
    return f"{sign}{_num(round(abs(difference), 3))} {unit}".strip()


def _node_name(node: dict) -> str:
    return f'{(node.get("tag") or "").strip()} {node.get("label") or ""}'.strip() or str(node.get("id"))


_LOSS_PARAM_MARK = "去向"


def _loss_outlet(param: dict) -> dict | None:
    """参数里写的差额去向（名称含“去向”且单位是质量流量单位）也算一条出料。

    这样在现有检查器里加一行参数就能标清“少了的 50 kg/h 去哪了”，
    不必等界面加控件；名称、数值、单位、来源一个都不少。
    """
    if _LOSS_PARAM_MARK not in str(param.get("name") or ""):
        return None
    if _parse_flow(param) is None:
        return None
    return {"name": str(param.get("name") or "").strip(), "value": param.get("value"),
            "unit": param.get("unit") or "", "source": param.get("source") or ""}


def _node_outlets(node: dict) -> list[dict]:
    outlets = [dict(item) for item in node.get("outlets") or []]
    for param in node.get("params") or []:
        found = _loss_outlet(param)
        if found is not None:
            outlets.append(found)
    return outlets


def _material_balance(graph: dict) -> dict:
    """Close the mass balance over the labelled streams of one concept graph.

    The result drives three things on every sheet: the flow printed under each
    stream tag, the per-unit 物料守恒校核 schedule, and the flowsheet verdict
    that names the route an unclosed difference has to leave by.
    """
    nodes = {node["id"]: node for node in graph.get("nodes") or []}
    entries: dict[str, dict] = {
        node_id: {"in": {}, "out": {}, "outlet": {}, "unknown_in": [], "unknown_out": [],
                  "utility": 0, "outlets": []}
        for node_id in nodes
    }
    for index, edge in enumerate(graph.get("edges") or [], 1):
        source = nodes.get(edge.get("source"))
        target = nodes.get(edge.get("target"))
        utility = _edge_is_utility(edge)
        flow = None if utility else _edge_flow(edge)
        for bucket, node in (("out", source), ("in", target)):
            if node is None:
                continue
            entry = entries[node["id"]]
            if utility:
                entry["utility"] += 1
            elif flow is None:
                entry["unknown_" + bucket].append(f"S{index:02d}")
            else:
                entry[bucket][flow[0]] = entry[bucket].get(flow[0], 0.0) + flow[1]

    # 差额去向（外送 / 排放 / 放空 / 母液 …）计入该单元的出料：
    # 填了去向，单元与全流程才可能闭合；没填的差额一律按“待补”处理，绝不默认消失。
    for node in nodes.values():
        for outlet in _node_outlets(node):
            found = _parse_flow(outlet)
            if found is None:
                continue
            unit, value = found
            entry = entries[node["id"]]
            entry["outlet"][unit] = entry["outlet"].get(unit, 0.0) + value
            entry["outlets"].append(f'{outlet.get("name")} {_num(round(value, 3))} {unit}')

    def unlabelled(entry: dict) -> list[str]:
        return list(dict.fromkeys(entry["unknown_in"] + entry["unknown_out"]))

    rows: list[tuple[str, str, str, str, str, str]] = []
    imbalanced: list[str] = []
    pending: list[str] = []
    for node_id, node in nodes.items():
        entry = entries[node_id]
        if not (entry["in"] or entry["out"] or entry["unknown_in"] or entry["unknown_out"]):
            continue
        name = _node_name(node)
        kind = node.get("kind")
        out_total = _combined(entry["out"], entry["outlet"])
        in_text = _mass_text(entry["in"])
        out_text = _mass_text(out_total)
        tags = unlabelled(entry)
        is_source = not (entry["in"] or entry["unknown_in"])
        is_sink = not (out_total or entry["unknown_out"])
        if kind in ("product", "waste", "analysis") or is_sink:
            if kind == "analysis":
                what = "取样/分析点：进料合计即留样量（自成品中分出，含于产品量内，不返回流程）"
            elif kind == "product":
                what = "流程终点：产品出厂量 = 进料合计（图中成品留样含于产品量内，不额外新增出料）"
            elif kind == "waste":
                what = "流程终点：废物/母液量 = 进料合计"
            else:
                what = "流程终点（图上无出料连线）：进料合计即该点外送或收集量，去向待确认"
            if tags or not entry["in"]:
                pending.append(name)
                reason = "、".join(tags) if tags else "进口物流"
                rows.append((name, in_text, "—（流程终点）", "—",
                             f"{what}；未标注流量：{reason}（待补）", PENDING))
            else:
                rows.append((name, in_text, "—（流程终点）", "—", what, "闭合"))
            continue
        if is_source:
            if tags:
                pending.append(name)
                rows.append((name, "—（进料源）", out_text, "—",
                             f"未标注流量：{'、'.join(tags)}（待补）", PENDING))
            else:
                rows.append((name, "—（进料源）", out_text, "—", "进料源：投料量即出料量", "闭合"))
            continue
        if tags:
            pending.append(name)
            rows.append((name, in_text, out_text, "—",
                         f"未标注流量：{'、'.join(tags)}（待补，暂无法校核）", PENDING))
            continue
        units = set(entry["in"]) | set(out_total)
        if len(units) > 1:
            pending.append(name)
            rows.append((name, in_text, out_text, "—",
                         f"单位不一致（{'、'.join(sorted(units))}），请统一单位后再校核", "待校核"))
            continue
        unit = next(iter(units), "")
        total_in = sum(entry["in"].values())
        total_out = sum(out_total.values())
        difference = total_in - total_out
        if abs(difference) <= max(BALANCE_ABS_TOL, abs(total_in) * BALANCE_REL_TOL):
            outlets = entry["outlets"]
            note = "闭合：进料合计 = 出料合计" + (f'（去向：{"；".join(outlets)}）' if outlets else "")
            rows.append((name, in_text, out_text, _mass_gap(0.0, unit), note, "闭合"))
            continue
        imbalanced.append(name)
        if difference > 0:
            route = f"少出 {_num(round(difference, 3))} {unit}：{BALANCE_ROUTE_HINT}"
        else:
            route = f"多出 {_num(round(-difference, 3))} {unit}：需补进料（回流、补加或计量误差）"
        rows.append((name, in_text, out_text, _mass_gap(difference, unit), route, "不平衡"))

    global_in: dict[str, float] = {}
    global_out: dict[str, float] = {}
    global_pending: list[str] = []
    boundaries = ("product", "waste", "analysis")
    sources = [node for node in nodes.values()
               if node.get("kind") not in boundaries
               and not (entries[node["id"]]["in"] or entries[node["id"]]["unknown_in"])]
    if not sources:
        sources = [node for node in nodes.values() if node.get("kind") in ("feed", "other")]
    for node in sources:
        entry = entries[node["id"]]
        if entry["unknown_out"]:
            global_pending.append(_node_name(node))
        for unit, total in entry["out"].items():
            global_in[unit] = global_in.get(unit, 0.0) + total
    for node in nodes.values():
        if node.get("kind") not in ("product", "waste"):
            continue
        entry = entries[node["id"]]
        if entry["unknown_in"]:
            global_pending.append(_node_name(node))
        for unit, total in entry["in"].items():
            global_out[unit] = global_out.get(unit, 0.0) + total
    for node in nodes.values():
        if node.get("kind") in boundaries:
            continue
        for unit, value in entries[node["id"]]["outlet"].items():
            global_out[unit] = global_out.get(unit, 0.0) + value
    units = set(global_in) | set(global_out)
    unaccounted: dict[str, float] = {}
    for unit in sorted(units):
        difference = global_in.get(unit, 0.0) - global_out.get(unit, 0.0)
        if abs(difference) > max(BALANCE_ABS_TOL, abs(global_in.get(unit, 0.0)) * BALANCE_REL_TOL):
            unaccounted[unit] = difference
    if not units:
        status = PENDING
        note = "图中尚无可校核的物料流量：请为物流线填写流量与单位，缺失数据标“待补”。"
        gap_text = "—"
    elif global_pending:
        status = PENDING
        note = f"存在未标注流量的进出口物流（{'、'.join(global_pending)}），暂无法完成全流程校核。"
        gap_text = "—"
    elif len(units) > 1:
        status = "待校核"
        note = f"进出口单位不一致（{'、'.join(sorted(units))}），请统一单位后再校核。"
        gap_text = "—"
    elif unaccounted:
        status = "不平衡"
        note = f"未闭合差额 {_mass_gap(next(iter(unaccounted.values())), next(iter(unaccounted)))}；{BALANCE_ROUTE_HINT}。"
        gap_text = "、".join(_mass_gap(value, unit) for unit, value in unaccounted.items())
    else:
        status = "闭合"
        outlets = [item for entry in entries.values() for item in entry["outlets"]]
        note = ("闭合：进口合计 = 产品出厂量 + 废物/母液量"
                + (f'（含图上注明的差额去向：{"；".join(outlets)}）' if outlets else "") + "。")
        gap_text = _mass_gap(0.0, sorted(units)[0])
    rows.insert(0, (
        "全流程（进口 → 出口）",
        _mass_text(global_in),
        _mass_text(global_out),
        gap_text,
        note,
        status,
    ))

    node_gaps: dict[str, str] = {}
    node_losses: dict[str, list[str]] = {}
    for node_id, entry in entries.items():
        if entry["outlets"]:
            node_losses[node_id] = list(entry["outlets"])
        if entry["unknown_in"] or entry["unknown_out"]:
            continue
        out_total = _combined(entry["out"], entry["outlet"])
        if not entry["in"] or not out_total:
            continue
        if nodes[node_id].get("kind") in ("product", "waste", "analysis"):
            continue
        units = set(entry["in"]) | set(out_total)
        if len(units) != 1:
            continue
        unit = next(iter(units))
        difference = entry["in"].get(unit, 0.0) - out_total.get(unit, 0.0)
        if abs(difference) > max(BALANCE_ABS_TOL, abs(entry["in"].get(unit, 0.0)) * BALANCE_REL_TOL):
            node_gaps[node_id] = _mass_gap(difference, unit)

    return {
        "rows": rows,
        "nodes": entries,
        "node_gaps": node_gaps,
        "node_losses": node_losses,
        "global_in": global_in,
        "global_out": global_out,
        "unaccounted": unaccounted,
        "status": status,
        "note": note,
        "imbalanced": imbalanced,
        "pending": pending,
        "global_pending": global_pending,
    }


def _balance_rows(graph_or_balance) -> list[tuple[str, str, str, str, str, str]]:
    balance = graph_or_balance
    if isinstance(balance, dict) and "rows" not in balance and ("nodes" in balance or "edges" in balance):
        balance = _material_balance(balance)
    return list(balance.get("rows") or [])


def _balance_verdict(balance: dict) -> str:
    """One sentence naming inlet, outlet and any unclosed difference."""
    in_text = _mass_text(balance["global_in"])
    out_text = _mass_text(balance["global_out"])
    if balance["status"] == "闭合":
        return f"本次图纸校核：全流程进口 {in_text} ＝ 出口 {out_text}（产品 + 废物/母液），未闭合差额 0。"
    if balance["status"] == "不平衡":
        gaps = "、".join(_mass_gap(value, unit) for unit, value in sorted(balance["unaccounted"].items()))
        return f"本次图纸校核：全流程进口 {in_text}，出口 {out_text}，未闭合差额 {gaps}；{BALANCE_ROUTE_HINT}。"
    return f"本次图纸校核：全流程进口 {in_text}，出口 {out_text}；{balance['note']}"


def _sheet_equipment(graph: dict) -> list[tuple[str, str, str, str, str]]:
    rows: list[tuple[str, str, str, str, str]] = []
    for node in graph["nodes"]:
        params = "；".join(_param_text(param) for param in node["params"][:2]) or PENDING
        sources = "；".join(dict.fromkeys(param["source"] for param in node["params"][:2])) or PENDING
        rows.append((node["tag"] or "—", node["label"], KIND_LABELS.get(node["kind"], node["kind"]), params, sources))
    return rows


def _sheet_streams(graph: dict) -> list[tuple[str, str, str, str]]:
    by_id = {node["id"]: node for node in graph["nodes"]}
    rows: list[tuple[str, str, str, str]] = []
    # 先排单元上的安全阀组 / 放空 / 排污，再排公用工程接管：这些都要能从表里追到依据
    for node in graph["nodes"]:
        where = f'{node.get("tag") or "—"} {node.get("label") or ""}'.strip()
        for position, item in enumerate(node.get("reliefs") or [], 1):
            kind = str(item.get("kind") or "safety_valve")
            name = _symbols.RELIEF_LABELS.get(kind, "安全阀组")
            tag = str(item.get("tag") or "").strip() or f'{name}-{node.get("tag") or position}'
            detail = "；".join(value for value in (
                f'定压 {item.get("value")}{item.get("unit") or ""}'.strip() if item.get("value") else "",
                f'去向 {item.get("to")}'.strip() if item.get("to") else "",
                str(item.get("source") or "").strip() or "依据待补",
            ) if value)
            rows.append((tag, name, where, detail))
        for position, item in enumerate(node.get("utilities") or [], 1):
            medium = str(item.get("medium") or "").strip()
            name = _symbols.MEDIA_LABELS.get(medium) or medium or "公用工程"
            tag = f"{name}-{node.get('tag') or position}"
            detail = "；".join(value for value in (
                str(item.get("service") or "").strip(),
                f'{item.get("value")}{item.get("unit") or ""}'.strip() if item.get("value") else "",
                str(item.get("source") or "").strip() or "依据待补",
            ) if value)
            rows.append((tag, f"{name}接管", where, detail))
    for index, edge in enumerate(graph["edges"], 1):
        source = by_id.get(edge["source"], {})
        target = by_id.get(edge["target"], {})
        detail = _param_text(edge["params"][0]) if edge["params"] else PENDING
        rows.append((
            f"S{index:02d}",
            edge.get("label") or "物流",
            f'{source.get("tag") or "—"} → {target.get("tag") or "—"}',
            detail,
        ))
    return rows


# --- CAD 图纸输出（第 1 张图例、第 2 张工段图） -----------------------------
#
# 图纸基准（符号尺寸、线宽、字高、图幅与比例系列）落在 app.pfd_symbols，
# 版面合成落在 app.pfd_cad_sheet；本模块只把「图数据 + 物料守恒结果 + 表格行
# + 说明文字」组装成图纸上下文，再分发给两种输出：
#   * SVG —— 网页预览与下载，深色工作区垫一张白纸；
#   * DXF —— AutoCAD R12（AC1009，GBK 文字），按图幅 1:1 出图；图层表按院标
#     《图层标准》预设（图层名 / 色号 / 线型），图元写各自的院标层名。
# 两者共用同一份图元列表，几何完全一致。
#
# 一个 CAD 文档固定两张图：第 1 张是图例与符号说明（不含流程节点），
# 第 2 张是工段概念流程图（节点、物流、流量、物料守恒去向与校核表）。
# 设备、阀门与仪表符号取 pfd_symbols 的固定基准尺寸，全图比例一致；
# 管线只在符号接口点进出：方框设备取轮廓边中点，圆形容器（换热器/冷凝器）
# 取圆周外径，因此不会出现「管线穿过冷凝器圆」或「气动阀膜头脱离阀体」。

SHEET_T_CELL = 2.6
SHEET_SECTION_NAME = "工段概念流程图"
SHEET_LEGEND_NAME = "图例与符号说明"
SHEET_SECTION_INDEX = "第 2 张：工段概念流程图 · 共 2 张"
SHEET_LEGEND_INDEX = "第 1 张：图例与符号说明 · 共 2 张"
SHEET_LINETYPE_ROWS: tuple[tuple[str, str, float], ...] = (
    ("主物料线（进料端 → 出料端，计入物料守恒校核）　粗实线 0.90 mm", "PFD_MAIN", _symbols.W_MAIN_HEAVY),
    ("工艺物料支线（塔顶冷凝液、母液与三废等）　中实线 0.70 mm", "PFD_SECONDARY", _symbols.W_MAIN),
    ("公用工程总管与支管：蒸汽、冷却水、冷冻水、氮气等（不计入物料衡算）　细实线 0.30 mm（主物料线的 1/3）",
     "PFD_UTILITY", _symbols.W_UTILITY),
    ("安全阀组 / 放空 / 排污支管（带箭头指向排放去向）　细实线 0.30 mm", "PFD_MINOR", _symbols.W_UTILITY),
    ("仪表信号与引线；调节回路信号线（仪表端带小圆点，指向执行机构）　细虚线 0.25 mm",
     "PFD_SIGNAL", _symbols.W_THIN),
    ("预留管线 / 备用接口　细点划线 0.25 mm", "PFD_RESERVED", _symbols.W_THIN),
)
SHEET_UTIL_COLUMNS = ("介质", "规格 / 温度", "用量", "接入单元", "依据（来源）")
SHEET_UTIL_WEIGHTS = (0.15, 0.29, 0.13, 0.17, 0.26)
SHEET_UTIL_TITLE = "公用工程条件（逐段温度要求与加热/冷却介质）"
SHEET_UTIL_EMPTY = "这份资料里没有读到公用工程条件：蒸汽、冷却水、冷冻水、氮气、电伴热等的规格与接入点均待补。"
SHEET_WARN_TITLE = "物料不守恒：差额必须标清去向"
SHEET_WARN_HOWTO = (
    "怎么输入：选中该单元，在“参数”里加一行去向（名称写去往哪里，数值写差多少，"
    "单位 kg/h，来源写依据），图上就会多一条带箭头的去向支线并计入出料。"
)
SHEET_ASSET_COLUMNS = ("位号", "名称", "所在位置", "说明")
SHEET_ASSET_WEIGHTS = (0.16, 0.26, 0.24, 0.34)
SHEET_ASSET_NOTE = "概念示意：型式与位置由流程图拓扑给出；规格、材质、量程与联锁待合格工程人员设计。"
SHEET_EQ_COLUMNS = ("位号", "名称", "类型", "关键参数（含单位）", "来源")
SHEET_EQ_WEIGHTS = (0.12, 0.20, 0.15, 0.31, 0.22)
SHEET_ST_COLUMNS = ("编号", "名称", "走向", "参数（含单位）")
SHEET_ST_WEIGHTS = (0.11, 0.25, 0.22, 0.42)
SHEET_EMPTY_TABLE = "（空图）暂无参数"
SHEET_EMPTY_DRAWING = "暂无参数：图中还没有单元操作节点与物流线，请先添加节点或载入演示案例，再生成图纸。"
SHEET_LEGEND_DATA_NOTE = (
    "参数、单位与来源：第 2 张工段图把位号、名称与流量直接标在符号和管线上；"
    "设备一览表、物流表、管道附件与仪表表、物料守恒校核表都排在本图（第 1 张图例图）下方；"
    "缺失工程数据一律标“待补”，平台不推算管径、仪表、安全联锁、设备强度与已验证产能。"
)
SHEET_MEDIA_NOTE = (
    "线型、介质与流向：主物料线（加粗实线）由左端物料入口贯通到右端物料出口、箭头指向下游，"
    "并计入物料守恒校核；进料支路、母液与三废支线用中实线；进出车间的流股（主物料进出口、"
    "三废出口与公用工程总管）在图框边线处画界区旗标——方框加尖端、尖端顺着介质流动方向，"
    "框内写流股名称与流量，所以物料从哪进车间、最后往哪走一眼可辨；公用工程（不计入物料衡算）"
    "的蒸汽、冷却水、冷冻水等与安全阀组 / 放空 / 排污支管一律用细实线（线宽为主物料线的 1/3），"
    "只标接口与用量。"
)
SHEET_LAYER_NOTE = (
    "图层：出图前先把图层设好、再按内容选层画线（和手工在 AutoCAD 里一样）。"
    "导出的 DXF 自带院标图层表——主物料线、次物料线、设备轮廓线、设备内部件、进出物流标志、"
    "辅助及次要物流、仪表线、信号线、文字标注、设备位号、箭头、物料表与标题栏、图框各占一层，"
    "图层名、颜色号与线型都按院标预设好，打开就在「图层管理器」里，选层即可继续画；"
    "打印线宽按院标习惯由打印样式表（CTB）随色号控制。逐层的用途见第 1 张图例图的图层表。"
)
_INSTRUMENT_NAMES = {
    "P": "压力", "T": "温度", "F": "流量", "L": "液位", "A": "分析",
    "W": "重量", "S": "转速", "H": "手动", "I": "指示",
}

def _sheet_graph(graph: dict) -> dict:
    """补齐节点/连线的可选字段，让版面与守恒代码读到的结构永远完整。"""
    nodes: list[dict] = []
    for node in graph.get("nodes") or []:
        nodes.append({
            "id": node["id"],
            "kind": str(node.get("kind") or "other"),
            "tag": str(node.get("tag") or ""),
            "label": str(node.get("label") or ""),
            "x": float(node.get("x") or 0.0),
            "y": float(node.get("y") or 0.0),
            "w": float(node.get("w") or 180.0),
            "h": float(node.get("h") or 84.0),
            "params": [dict(param) for param in node.get("params") or []],
            "outlets": [dict(item) for item in node.get("outlets") or []],
            "utilities": [dict(item) for item in node.get("utilities") or []],
            "reliefs": [dict(item) for item in node.get("reliefs") or []],
        })
    edges: list[dict] = []
    for edge in graph.get("edges") or []:
        edges.append({
            "id": str(edge.get("id") or ""),
            "source": str(edge.get("source") or ""),
            "target": str(edge.get("target") or ""),
            "label": str(edge.get("label") or ""),
            "media": "utility" if str(edge.get("media") or "material") == "utility" else "material",
            "params": [dict(param) for param in edge.get("params") or []],
            "components": [dict(item) for item in edge.get("components") or []],
            "instruments": [str(tag) for tag in edge.get("instruments") or []],
            "service": str(edge.get("service") or ""),
            "signals": [dict(item) for item in edge.get("signals") or []],
        })
    return {"nodes": nodes, "edges": edges}


def _sheet_notes(graph: dict, view: str) -> list[str]:
    """图面说明：第 1 张（图例）与第 2 张（工段图）各自只留与自己有关的说明。"""
    if view == "legend":
        notes = [DISCLAIMER, SHEET_LEGEND_DATA_NOTE, SHEET_SYMBOL_NOTE, SHEET_LAYER_NOTE]
    else:
        # 第 2 张工段图不再排「图面顶部说明框」：说明性文字统一放到第 1 张图例图，
        # 第 2 张只留图面、主物料进出口标注与右下角签名档。
        notes = []
    if not graph["nodes"]:
        notes.insert(0, SHEET_EMPTY_DRAWING)
    return notes


def _instrument_name(tag: str) -> str:
    letters = "".join(char for char in str(tag).split("-")[0] if char.isalpha()).upper()
    return f"{_INSTRUMENT_NAMES.get(letters[:1] or 'I', '指示')}指示仪表"


def _sheet_assets(graph: dict) -> list[tuple[str, str, str, str]]:
    """管道附件与仪表表：画在图上的阀门、仪表逐条列出所在位置。"""
    by_id = {node["id"]: node for node in graph["nodes"]}
    rows: list[tuple[str, str, str, str]] = []
    for index, edge in enumerate(graph["edges"], 1):
        source = by_id.get(edge["source"]) or {}
        target = by_id.get(edge["target"]) or {}
        where = f'{source.get("tag") or "—"} → {target.get("tag") or "—"}'
        for position, component in enumerate(edge.get("components") or [], 1):
            kind = str(component.get("kind") or "")
            name = _symbols.VALVE_LABELS.get(kind) or _symbols.catalog_name(kind)
            tag = str(component.get("tag") or "").strip() or f"{name}-{index:02d}{position}"
            rows.append((tag, name, where, SHEET_ASSET_NOTE))
        for tag in edge.get("instruments") or []:
            text = str(tag).strip()
            if text:
                rows.append((text, _instrument_name(text), where, SHEET_ASSET_NOTE))
        for signal in edge.get("signals") or []:
            tag = str(signal.get("tag") or "").strip()
            if not tag:
                continue
            target = str(signal.get("target") or "").strip() or "执行机构待补"
            kind = {"electric": "电信号", "pneumatic": "气信号", "interlock": "联锁信号"}.get(
                str(signal.get("kind") or ""), "信号形式待补")
            detail = "；".join(value for value in (
                f"{kind} → {target}",
                str(signal.get("source") or "").strip() or "依据待补",
            ) if value)
            rows.append((f"{tag}→{target}"[:24], "仪表信号回路", where, detail))
    return rows


def _sheet_utilities(graph: dict) -> list[tuple[str, str, str, str, str]]:
    """公用工程条件表：每一条接管都要能追到输入资料里的哪一句（依据）。"""
    rows: list[tuple[str, str, str, str, str]] = []
    for index, edge in enumerate(graph["edges"], 1):
        if str(edge.get("media") or "material") != "utility":
            continue
        target = next((node for node in graph["nodes"] if node["id"] == edge["target"]), {})
        service = str(edge.get("service") or "").strip()
        label = str(edge.get("label") or "").strip() or "公用工程"
        amount = ""
        for param in edge.get("params") or []:
            text = _param_text(param)
            if text and text != PENDING:
                amount = text
                break
        source = "；".join(dict.fromkeys(
            str(param.get("source") or "").strip() for param in edge.get("params") or []
            if str(param.get("source") or "").strip())) or "依据待补"
        rows.append((label, service or "规格/温度待补", amount or PENDING,
                     f'{target.get("tag") or "—"} {target.get("label") or ""}'.strip(), source))
    for node in graph["nodes"]:
        where = f'{node.get("tag") or "—"} {node.get("label") or ""}'.strip()
        for item in node.get("utilities") or []:
            medium = str(item.get("medium") or "").strip()
            label = _symbols.MEDIA_LABELS.get(medium) or medium or "公用工程"
            amount = f'{item.get("value") or ""}{item.get("unit") or ""}'.strip() or PENDING
            rows.append((label, str(item.get("service") or "").strip() or "规格/温度待补",
                         amount, where, str(item.get("source") or "").strip() or "依据待补"))
    return rows


def _sheet_warning(balance: dict) -> list[str]:
    """不守恒时的红框提示：差额多少、必须注明去向、以及怎么输入。"""
    if balance["status"] != "不平衡":
        return []
    gaps = "、".join(_mass_gap(value, unit) for unit, value in sorted(balance["unaccounted"].items()))
    return [
        f'全流程进料 {_mass_text(balance["global_in"])}，出料 {_mass_text(balance["global_out"])}，'
        f'未闭合差额 {gaps}。{BALANCE_ROUTE_HINT}：',
        SHEET_WARN_HOWTO,
    ]


def _sheet_convention(balance: dict) -> tuple[list[str], int]:
    """第 1 张图的守恒说明：规则行 + 本图的校核结论（结论按红字排最后）。"""
    rules = list(BALANCE_RULES)
    return rules + [_balance_verdict(balance)], len(rules)


def _sheet_ctx(title: str, graph: dict, meta: SheetMeta, stamp: str, view: str) -> dict:
    graph = _sheet_graph(graph)
    ctx_view = "legend" if str(view).strip().lower() == "legend" else "section"
    balance = _material_balance(graph)
    convention, verdict_from = _sheet_convention(balance)
    return {
        "title": str(title or "概念 PFD"),
        "view": "legend" if str(view).strip().lower() == "legend" else "section",
        "stamp": stamp,
        "meta": meta.model_dump(),
        "stage": SHEET_STAGE,
        "graph": graph,
        "balance": balance,
        "balance_rows": balance["rows"],
        "equipment_rows": _sheet_equipment(graph),
        "stream_rows": _sheet_streams(graph),
        "asset_rows": _sheet_assets(graph),
        "utility_rows": _sheet_utilities(graph),
        "notes": _sheet_notes(graph, ctx_view),
        "warning_title": SHEET_WARN_TITLE,
        "warning_lines": _sheet_warning(balance),
        "convention_lines": convention,
        "convention_verdict_from": verdict_from,
        "linetype_rows": SHEET_LINETYPE_ROWS,
        "flow_of_edge": {edge["id"]: _stream_flow_text(edge) for edge in graph["edges"]},
        "flow_values": {edge["id"]: _edge_flow(edge) for edge in graph["edges"]},
        "losses": dict(balance.get("node_losses") or {}),
        "node_gaps": dict(balance.get("node_gaps") or {}),
        "equipment_columns": SHEET_EQ_COLUMNS,
        "equipment_weights": SHEET_EQ_WEIGHTS,
        "stream_columns": SHEET_ST_COLUMNS,
        "stream_weights": SHEET_ST_WEIGHTS,
        "asset_columns": SHEET_ASSET_COLUMNS,
        "utility_columns": SHEET_UTIL_COLUMNS,
        "utility_weights": SHEET_UTIL_WEIGHTS,
        "utility_title": SHEET_UTIL_TITLE,
        "utility_empty": SHEET_UTIL_EMPTY,
        "asset_weights": SHEET_ASSET_WEIGHTS,
        "balance_title": SHEET_BAL_TITLE,
        "balance_columns": SHEET_BAL_COLUMNS,
        "balance_weights": SHEET_BAL_WEIGHTS,
        "empty_table": SHEET_EMPTY_TABLE,
        "section_name": SHEET_SECTION_NAME,
        "section_index": SHEET_SECTION_INDEX,
        "legend_name": SHEET_LEGEND_NAME,
        "legend_index": SHEET_LEGEND_INDEX,
        "table_left_w": 320.0,
    }


def _sheet_draw(title: str, graph: dict, meta: SheetMeta | None, stamp: str, view: str) -> dict:
    return _sheet.compose(_sheet_ctx(title, graph, meta or SheetMeta(), stamp, view))


def sheet_svg(title: str, graph: dict, meta: SheetMeta | None = None, stamp: str = "",
              view: str = "section") -> str:
    """把一张图纸渲染成 SVG。view: section（第 2 张工段图）| legend（第 1 张图例）。"""
    return _sheet.emit_svg(_sheet_draw(title, graph, meta, stamp, view))


def sheet_dxf(title: str, graph: dict, meta: SheetMeta | None = None, stamp: str = "",
              view: str = "section") -> str:
    """把同一张图纸渲染成 AutoCAD R12（AC1009）DXF。view 同上。"""
    return _sheet.emit_dxf(_sheet_draw(title, graph, meta, stamp, view))


_DXF_GBK_FALLBACK = {
    "\u00b2": "2", "\u00b3": "3", "\u2070": "0", "\u00b9": "1",
    "\u2265": ">=", "\u2264": "<=", "\u2248": "~", "\u2192": "->", "\u2190": "<-",
    "\u2191": "^", "\u2193": "v", "\u00d7": "x", "\u2013": "-", "\u2014": "-",
    "\u2026": "...", "\u00a0": " ", "\u2212": "-", "\uff0d": "-", "\u00b7": ".",
    "\u2018": "'", "\u2019": "'", "\u201c": "\"", "\u201d": "\"", "\u00b0": "deg",
}


def _gbk_safe(text: str) -> str:
    """把图纸文字收进 GBK：CAD 只按 GBK 读 DXF 文字，写出非法字符就是乱码。

    先用等价写法替换掉图纸排版用到的几个 Unicode 符号，剩余仍不在 GBK 的字符
    直接丢弃——宁可在图上少一个装饰符号，也不要在 CAD 里显示成问号或方块。
    """
    out: list[str] = []
    for char in text:
        try:
            char.encode("gbk")
        except UnicodeEncodeError:
            out.append(_DXF_GBK_FALLBACK.get(char, ""))
        else:
            out.append(char)
    return "".join(out)

@router.post("/preview/sheet.svg")
def preview_sheet_svg(project_id: str, payload: SheetRequest, db: Session = Depends(get_db),
                      user: UserContext = Depends(get_current_user)):
    """Render the current drawing as a CAD-style sheet (no database write)."""
    require_project_access(db, project_id, user)
    graph = payload.graph.model_dump()
    meta = _sheet_meta_for(payload.meta, db, project_id, graph)
    svg = sheet_svg(payload.title, graph, meta, _sheet_stamp(), payload.view)
    return Response(svg, media_type="image/svg+xml", headers={
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    })


@router.post("/preview/sheet.dxf")
def preview_sheet_dxf(project_id: str, payload: SheetRequest, db: Session = Depends(get_db),
                      user: UserContext = Depends(get_current_user)):
    """Render the current drawing as an AutoCAD R12 sheet DXF (no database write)."""
    require_project_access(db, project_id, user)
    graph = payload.graph.model_dump()
    meta = _sheet_meta_for(payload.meta, db, project_id, graph)
    dxf = sheet_dxf(payload.title, graph, meta, _sheet_stamp(), payload.view)
    name = "legend" if payload.view == "legend" else "sheet"
    return Response(_gbk_safe(dxf).encode("gbk"), media_type="application/dxf", headers={
        "Content-Disposition": f'attachment; filename="concept-pfd-{name}.dxf"',
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    })
