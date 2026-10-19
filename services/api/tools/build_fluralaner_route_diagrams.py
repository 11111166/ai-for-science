"""Build source-labelled, static fluralaner diagrams; requires local RDKit.

Run with .venv-qsar/bin/python. The engineering figure is an editorial block
flow sketch, not an equipment design or a patent drawing.
"""
from __future__ import annotations

import html
import json
from pathlib import Path

from rdkit import Chem
from rdkit.Chem import rdMolDescriptors
from rdkit.Chem.Draw import rdMolDraw2D

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "apps/web/data/fluralaner_route_structures.json"
PATENT = ROOT / "services/api/app/data/fluralaner_patent_sources.json"
OUT = ROOT / "apps/web/public/process/fluralaner"


def text(x: float, y: float, value: str, size: int = 19, color: str = "#25423c", weight: int = 400) -> str:
    return (f'<text x="{x}" y="{y}" font-family="Arial, PingFang SC, Noto Sans CJK SC, sans-serif" '
            f'font-size="{size}" font-weight="{weight}" fill="{color}">{html.escape(value)}</text>')


def line(x1: float, y1: float, x2: float, y2: float, *, dashed: bool = False) -> str:
    dash = ' stroke-dasharray="8 6"' if dashed else ""
    return f'<path d="M{x1} {y1} L{x2} {y2}" fill="none" stroke="#13846d" stroke-width="3" marker-end="url(#arrow)"{dash}/>'


def chem_svg(smiles: str, x: int, y: int, w: int = 340, h: int = 170) -> str:
    mol = Chem.MolFromSmiles(smiles)
    assert mol is not None
    drawing = rdMolDraw2D.MolDraw2DSVG(w, h)
    drawing.drawOptions().bondLineWidth = 1.8
    rdMolDraw2D.PrepareAndDrawMolecule(drawing, mol)
    drawing.FinishDrawing()
    raw = drawing.GetDrawingText()
    inner = raw[raw.index("<!-- END OF HEADER -->") + len("<!-- END OF HEADER -->"):raw.rindex("</svg>")]
    return f'<svg x="{x}" y="{y}" width="{w}" height="{h}" viewBox="0 0 {w} {h}">{inner}</svg>'


def shell(width: int, height: int) -> list[str]:
    return [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">',
            '<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="9" refY="5" orient="auto"><path d="M0 0 L10 5 L0 10 Z" fill="#13846d"/></marker></defs>',
            f'<rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff"/>']


def route_svg(data: dict) -> str:
    width, height = 1840, 1060
    bits = shell(width, height)
    bits.append('<title>氟雷拉纳公开专利七步合成路线与中间体二维结构</title>')
    bits.append(text(48, 51, "氟雷拉纳 · 公开专利合成路线", 30, "#0a6755", 700))
    bits.append(text(48, 83, "WO2023012821A1 实施例 1 · 结构为资料库或名称转换后的示意图，不是专利原图", 17, "#5a7069"))
    nodes = data["nodes"]
    positions = [(40, 115), (490, 115), (940, 115), (1390, 115), (1390, 480), (940, 480), (490, 480), (40, 480)]
    for item, (x, y) in zip(nodes, positions):
        bits.append(f'<rect x="{x}" y="{y}" width="410" height="290" rx="15" fill="#f9fcfb" stroke="#bddbd2" stroke-width="2"/>')
        bits.append(text(x + 18, y + 32, item["label"], 19, "#0a6755", 700))
        bits.append(chem_svg(item["smiles"], x + 35, y + 48))
        bits.append(text(x + 18, y + 253, item["name"], 19, "#223e38", 700))
        bits.append(text(x + 18, y + 278, item["formula"], 15, "#5a7069"))
    # Continuous serpentine order: start → 1 → 2 → 3 ↓ 4 ← 5 ← 6 ← 7.
    for i in range(3):
        x, y = positions[i]
        bits.append(line(x + 410, y + 145, positions[i + 1][0] - 4, y + 145))
    bits.append('<path d="M1800 260 L1820 260 L1820 625 L1805 625" fill="none" stroke="#13846d" stroke-width="3" marker-end="url(#arrow)"/>')
    bits.append(text(1666, 452, "续第 4 步", 16, "#0a6755", 700))
    for i in range(4, 7):
        x, y = positions[i]
        bits.append(line(x, y + 145, positions[i + 1][0] + 414, y + 145))
    reagent = data["branch_reagent"]
    bits.append('<rect x="945" y="795" width="610" height="170" rx="14" fill="#f5f9f2" stroke="#a6c8a9" stroke-width="2" stroke-dasharray="7 5"/>')
    bits.append(chem_svg(reagent["smiles"], 963, 809, 250, 136))
    bits.append(text(1220, 844, "步骤 5 另投入", 18, "#0a6755", 700))
    bits.append(text(1220, 876, reagent["name"], 17, "#223e38"))
    bits.append(text(1220, 906, reagent["formula"], 15, "#5a7069"))
    bits.append(line(1148, 795, 1148, 774, dashed=True))
    bits.append(text(42, 1007, "注：第 4、5 步结构由专利产物名称转换；第 6 步按实验所得羧酸绘制，原文标题仍写乙酯，需复核。", 16, "#735a2c"))
    bits.append(text(42, 1034, "未据此指定异噁唑啉立体构型；第 7 步原文质量数字矛盾，不给出全路线实测总收率。", 16, "#735a2c"))
    bits.append('</svg>')
    return "\n".join(bits)


def flow_svg() -> str:
    width, height = 1840, 920
    bits = shell(width, height)
    bits.append('<title>氟雷拉纳拟议工艺流程图，概念级设备和物流草图</title>')
    bits.append(text(48, 53, "氟雷拉纳 · 拟议工艺流程图", 30, "#0a6755", 700))
    bits.append('<rect x="43" y="75" width="1754" height="49" rx="10" fill="#fff1e7" stroke="#e4bb92"/>')
    bits.append(text(59, 107, "概念级 BFD/PFD 草图 · 非专利原图、非设备选型或施工图；设备、物料衡算和放大条件仍待验证", 18, "#8a5126", 700))
    stages = [
        ("R1 · 酰化", "投料/搅拌反应", "淬灭、萃取、柱层析*", "WO 步骤 1"),
        ("R2 · 氰化", "反应/温控", "萃取、柱层析*", "WO 步骤 2 · EHS 核查"),
        ("R3 · 腈水解", "酸性反应", "调 pH、萃取", "WO 步骤 3"),
        ("R4 · 活化/偶联", "酰氯化、酰胺化", "水洗、浓缩", "WO 步骤 4"),
        ("R5 · 缩合", "回流反应/支路投料", "过滤、萃取", "WO 步骤 5"),
        ("R6 · 环化/水解", "反应/水解", "酸化沉淀、过滤", "WO 步骤 6"),
        ("R7 · 末端偶联", "酰胺化", "萃取、浓缩", "WO 步骤 7"),
        ("可选结晶", "末端分离实验", "结晶、过滤、QC", "EP4077285A1 实施例"),
    ]
    positions = [(40, 160), (490, 160), (940, 160), (1390, 160), (1390, 465), (940, 465), (490, 465), (40, 465)]
    for (title, operation, separation, source), (x, y) in zip(stages, positions):
        extra = ' stroke-dasharray="8 6"' if title == "可选结晶" else ""
        bits.append(f'<rect x="{x}" y="{y}" width="410" height="225" rx="15" fill="#f9fcfb" stroke="#bddbd2" stroke-width="2"{extra}/>')
        bits.append(text(x + 18, y + 33, title, 20, "#0a6755", 700))
        bits.append(f'<rect x="{x + 18}" y="{y + 55}" width="169" height="90" rx="10" fill="#e8f6f1" stroke="#8fc3b4"/>')
        bits.append(f'<rect x="{x + 222}" y="{y + 55}" width="169" height="90" rx="10" fill="#f4f8f7" stroke="#b8d2ca"/>')
        bits.append(text(x + 30, y + 88, "反应/操作", 15, "#566d66", 700))
        bits.append(text(x + 30, y + 117, operation, 17, "#223e38"))
        bits.append(line(x + 189, y + 100, x + 217, y + 100))
        bits.append(text(x + 234, y + 88, "后处理", 15, "#566d66", 700))
        bits.append(text(x + 234, y + 117, separation, 16, "#223e38"))
        bits.append(text(x + 18, y + 196, source, 15, "#6b7b74"))
    for i in range(3):
        x, y = positions[i]
        bits.append(line(x + 410, y + 110, positions[i + 1][0] - 4, y + 110))
    bits.append('<path d="M1800 270 L1820 270 L1820 575 L1805 575" fill="none" stroke="#13846d" stroke-width="3" marker-end="url(#arrow)"/>')
    for i in range(4, 7):
        x, y = positions[i]
        bits.append(line(x, y + 110, positions[i + 1][0] + 414, y + 110))
    bits.append(text(40, 741, "输入与旁路接口（仅占位）：步骤 5 三氟乙酮投料；溶剂回收、母液/废液和公用工程连接待设计。", 17, "#566d66"))
    bits.append('<rect x="40" y="767" width="1760" height="107" rx="14" fill="#fff9ee" stroke="#e4d2aa"/>')
    bits.append(text(58, 798, "工程验证清单", 19, "#806128", 700))
    bits.append(text(58, 830, "设备规格/材质与连接   ·   各步物料衡算及杂质去向   ·   热量衡算/冷却能力   ·   放大与危险工艺评估", 18, "#5c5848"))
    bits.append(text(58, 858, "* 专利实验使用柱层析；量产替代分离未选定。图中 R1–R7 为流程占位编号，不代表反应釜已完成选型。", 16, "#785e37"))
    bits.append('</svg>')
    return "\n".join(bits)


def main() -> None:
    data = json.loads(DATA.read_text(encoding="utf-8"))
    patent = json.loads(PATENT.read_text(encoding="utf-8"))["sources"][data["patent"]]
    assert patent["source_sha256"] == data["patent_source_sha256"], "Patent source changed; review the diagrams"
    paragraphs = {row["locator"]: row["text"] for row in patent["paragraphs"]}
    assert len(data["nodes"]) == 8
    for item in [*data["nodes"], data["branch_reagent"]]:
        mol = Chem.MolFromSmiles(item["smiles"])
        assert mol and rdMolDescriptors.CalcMolFormula(mol) == item["formula"], item["id"]
        assert item["patent_locator"] in paragraphs, item["id"]
    assert "glycine" in paragraphs["p0235"].lower() and "acid" not in paragraphs["p0234"].lower()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "synthesis-route.svg").write_text(route_svg(data), encoding="utf-8")
    (OUT / "process-flow-concept.svg").write_text(flow_svg(), encoding="utf-8")
    print(f"Wrote two source-labelled figures to {OUT}")


if __name__ == "__main__":
    main()
