# -*- coding: utf-8 -*-
"""Aspen 风格「流程模拟图」草稿：先模拟、后出 CAD 图。

平台本机没有 Aspen Plus 授权，也没有热力学 / 物性数据库，这里不假装跑了真模拟器：
按 Aspen 出图的习惯（方块单元操作 + 带物流号与流量的物流线 + 物流表 + 单元表）把
识别出来的单元操作排成一张流程模拟图，并给出可粘贴进 Aspen 的输入文件骨架，让工程师
在真 Aspen 里补全组分、物性方法、热力学数据与设备尺寸，跑通模拟后再回到 CAD 出
第 1 张图例与第 2 张工段 PFD。
"""
from __future__ import annotations

import html
from datetime import date

INK = "#123c52"
SOFT = "#4d6273"
BLOCK_FILL = "#eaf3fa"
BLOCK_LINE = "#0b5c8a"
UTILITY = "#2f6f9f"
MATERIAL = "#0b5c8a"
WARN = "#a1341f"

WIDTH = 1280.0
HEIGHT = 800.0
FLOW_TOP = 88.0
FLOW_BOTTOM = 484.0
BLOCK_W = 116.0
BLOCK_H = 62.0

# 单元操作 → Aspen 模型建议（仅“建议”，最终由工程师确认）
ASPEN_MODEL = {
    "reactor": ("RCSTR / RStoic", "反应器"),
    "column": ("RadFrac", "精馏塔 / 吸收塔"),
    "vessel": ("Flash2 / 储罐", "储罐 / 回流罐"),
    "tank_h": ("Flash2 / 储罐", "卧式储罐"),
    "pump": ("Pump", "泵"),
    "compressor": ("Compr", "压缩机 / 风机"),
    "heat_exchanger": ("Heater / HeatX", "换热器"),
    "condenser": ("Heater / HeatX", "冷凝器"),
    "separation": ("Decanter / Sep", "分相 / 结晶"),
    "filter": ("Sep / Filter", "过滤 / 离心"),
    "dryer": ("Dryer / RStoic", "干燥"),
    "feed": ("—（进料流）", "固体投料 / 计量"),
    "product": ("—（产品流）", "产品外送"),
    "waste": ("—（排放流）", "三废 / 排放"),
    "analysis": ("—（分析）", "取样分析"),
    "other": ("待定", "未定型单元"),
}

VALVE_NAMES = {"valve_gate": "闸阀", "valve_globe": "截止阀", "valve_check": "止回阀",
               "valve_ball": "球阀", "valve_control": "调节阀", "valve_actuated": "气动阀"}


def _esc(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _clip(value: str, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: max(1, limit - 1)] + "…"


def _flow_text(edge: dict) -> str:
    """从物流参数里挑一个带数值的量（Aspen 物流表的写法：数值 + 单位）。"""
    params = [param for param in (edge.get("params") or []) if isinstance(param, dict)]
    for param in params:
        name = str(param.get("name") or "")
        value = str(param.get("value") or "").strip()
        unit = str(param.get("unit") or "").strip()
        if value and value not in ("待补", "-", "—") and any(
                word in name for word in ("流量", "进料量", "采出量", "循环量")):
            return f"{value} {unit}".strip()
    for param in params:
        value = str(param.get("value") or "").strip()
        unit = str(param.get("unit") or "").strip()
        if value and value not in ("待补", "-", "—") and str(param.get("name") or "").strip():
            return f"{value} {unit}".strip()
    return "待补"


def _block_params(node: dict) -> str:
    items = []
    for param in (node.get("params") or [])[:3]:
        name = str(param.get("name") or "").strip()
        value = str(param.get("value") or "").strip()
        unit = str(param.get("unit") or "").strip()
        if name and value:
            items.append(f"{name} {value} {unit}".strip())
    return "；".join(items) if items else "待补：温度 / 压力 / 规模等"


def _rows(graph: dict):
    """把图整理成画流程模拟图要用的三张清单：单元、物流、附件。"""
    nodes = {node["id"]: node for node in graph.get("nodes") or []}
    blocks, streams, assets = [], [], []
    for index, node in enumerate(graph.get("nodes") or [], 1):
        kind = str(node.get("kind") or "other")
        model, kind_name = ASPEN_MODEL.get(kind, ("待定", kind))
        blocks.append({
            "id": node["id"], "no": index,
            "tag": str(node.get("tag") or "").strip() or f"B{index:02d}",
            "name": str(node.get("label") or "").strip() or kind_name,
            "kind": kind, "kind_name": kind_name, "model": model,
            "params": _block_params(node),
        })
    for index, edge in enumerate(graph.get("edges") or [], 1):
        source = nodes.get(edge["source"], {})
        target = nodes.get(edge["target"], {})
        utility = str(edge.get("media") or "material") == "utility"
        components = [VALVE_NAMES.get(str(item.get("kind")), str(item.get("kind")))
                      for item in (edge.get("components") or [])]
        instruments = [str(tag) for tag in (edge.get("instruments") or [])]
        streams.append({
            "id": edge["id"], "no": index, "code": f"S{index:02d}",
            "label": str(edge.get("label") or "").strip() or "物流",
            "source": (source.get("tag") or "").strip() or f"B{index:02d}",
            "target": (target.get("tag") or "").strip() or "",
            "flow": _flow_text(edge),
            "media": "公用工程" if utility else "工艺物料",
            "utility": utility,
            "assets": "、".join([item for item in components + instruments if item]) or "—",
        })
        for item in components + instruments:
            assets.append({"stream": f"S{index:02d}", "name": item,
                           "where": f'{(source.get("tag") or "?").strip()} → {(target.get("tag") or "?").strip()}'})
    return blocks, streams, assets


def _place_blocks(blocks: list[dict], graph: dict) -> dict:
    """按节点自身的 x/y 分列分行排方块（Aspen 流程图也是方块对齐排的）。"""
    nodes = {node["id"]: node for node in graph.get("nodes") or []}
    xs = sorted({round(float(node.get("x") or 0.0), 1) for node in graph.get("nodes") or []})
    ys = sorted({round(float(node.get("y") or 0.0), 1) for node in graph.get("nodes") or []})
    cols, rows = max(1, len(xs)), max(1, len(ys))
    step_x = (WIDTH - 2 * 76.0 - BLOCK_W) / max(1, cols - 1) if cols > 1 else 0.0
    step_y = (FLOW_BOTTOM - FLOW_TOP - BLOCK_H) / max(1, rows - 1) if rows > 1 else 0.0
    centre, rects = {}, {}
    for block in blocks:
        node = nodes[block["id"]]
        col = xs.index(round(float(node.get("x") or 0.0), 1))
        row = ys.index(round(float(node.get("y") or 0.0), 1))
        cx = 76.0 + BLOCK_W / 2.0 + col * step_x
        cy = FLOW_TOP + BLOCK_H / 2.0 + row * step_y
        centre[block["id"]] = (cx, cy)
        rects[block["id"]] = {"x1": cx - BLOCK_W / 2 - 6.0, "x2": cx + BLOCK_W / 2 + 6.0,
                              "y1": cy - BLOCK_H / 2 - 6.0, "y2": cy + BLOCK_H / 2 + 10.0}
    return {"centre": centre, "rects": rects, "cols": cols, "rows": rows}


def _terminal_ports(graph: dict):
    material = [edge for edge in graph.get("edges") or []
                if str(edge.get("media") or "material") != "utility"]
    has_in = {edge["target"] for edge in material}
    has_out = {edge["source"] for edge in material}
    entries = [node for node in graph.get("nodes") or []
               if node["id"] in has_out and node["id"] not in has_in]
    exits = [node for node in graph.get("nodes") or []
             if node["id"] in has_in and node["id"] not in has_out]
    return entries, exits
def _overlap(a: dict, b: dict) -> bool:
    return not (a["x2"] <= b["x1"] or a["x1"] >= b["x2"] or a["y2"] <= b["y1"] or a["y1"] >= b["y2"])


def _text_w(value: str, size: float) -> float:
    """粗略文字宽度：中文按 1 em、西文按 0.62 em 估。"""
    return sum(size * (0.62 if ord(char) < 0x2000 else 1.0) for char in str(value))


def _router():
    """复用 CAD 模块的正交走线：流程模拟图的管线同样要绕开方块、不许穿方块。"""
    try:
        from app import pfd_cad_sheet as module
    except ImportError:  # 单独跑脚本 / 单元测试时
        import pfd_cad_sheet as module  # type: ignore
    return module

def _svg(title: str, graph: dict, blocks: list[dict], streams: list[dict], stamp: str) -> str:
    layout = _place_blocks(blocks, graph)
    centre, rects = layout["centre"], layout["rects"]
    sheet = _router()
    obstacles = list(rects.values())
    bounds = (18.0, 60.0, WIDTH - 18.0, FLOW_BOTTOM + 10.0)
    parts: list[str] = []
    parts.append(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH:.0f} {HEIGHT:.0f}" '
                 f'width="{WIDTH:.0f}" height="{HEIGHT:.0f}" '
                 f"font-family=\"Helvetica,Arial,'Microsoft YaHei',sans-serif\">")
    parts.append(f'<rect x="0" y="0" width="{WIDTH:.0f}" height="{HEIGHT:.0f}" fill="#ffffff"/>')
    parts.append(f'<rect x="6" y="6" width="{WIDTH-12:.0f}" height="{HEIGHT-12:.0f}" fill="none" '
                 f'stroke="{INK}" stroke-width="1.6"/>')
    parts.append(f'<text x="20" y="34" font-size="19" font-weight="700" fill="{INK}">'
                 f'{_esc(title)} · Aspen 流程模拟图（概念级）</text>')
    parts.append(f'<text x="20" y="53" font-size="11.5" fill="{SOFT}">方块 = 单元操作（附模型建议）；'
                 f'箭头 = 物流（附物流号与流量）；下方为物流表与单元表。生成日期 {_esc(stamp)}</text>')
    parts.append(f'<text x="{WIDTH-20:.0f}" y="34" font-size="11.5" fill="{SOFT}" text-anchor="end">'
                 f'用途：先在这张流程模拟图上复核拓扑与流量，再生成 CAD 的 PFD 与图例</text>')
    parts.append(f'<text x="{WIDTH-20:.0f}" y="53" font-size="11.5" fill="{WARN}" text-anchor="end">'
                 f'非 Aspen 运行结果：本机未运行 Aspen 模拟器，组分 / 物性 / 热力学数据待补</text>')

    def halo(x, y, size, value, colour, anchor="middle", weight="700"):
        return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" font-weight="{weight}" fill="{colour}" '
                f'text-anchor="{anchor}" stroke="#ffffff" stroke-width="3.2" paint-order="stroke" '
                f'stroke-linejoin="round">{_esc(value)}</text>')

    def ports_of(cx: float, cy: float) -> dict:
        return {"w": (cx - BLOCK_W / 2.0, cy), "e": (cx + BLOCK_W / 2.0, cy),
                "n": (cx, cy - BLOCK_H / 2.0), "s": (cx, cy + BLOCK_H / 2.0)}

    def run_edge(edge) -> list:
        source = centre.get(edge["source"])
        target = centre.get(edge["target"])
        if source is None or target is None:
            return []
        src_box, tgt_box = rects[edge["source"]], rects[edge["target"]]
        src = {"cx": source[0], "cy": source[1]}
        tgt = {"cx": target[0], "cy": target[1]}
        local = [box for box in obstacles if box is not src_box and box is not tgt_box]
        return sheet.route_ports(src, tgt, ports_of(*source), ports_of(*target), local, bounds,
                                 (src_box, tgt_box))

    def run_free(node: dict, entry: bool) -> list:
        box = rects[node["id"]]
        cx, cy = centre[node["id"]]
        local = [item for item in obstacles if item is not box]
        if entry:
            src = {"cx": 22.0, "cy": cy}
            tgt = {"cx": cx, "cy": cy}
            return sheet.route_ports(src, tgt, {"e": (22.0, cy)}, ports_of(cx, cy), local, bounds,
                                     (None, box))
        src = {"cx": cx, "cy": cy}
        tgt = {"cx": WIDTH - 22.0, "cy": cy}
        return sheet.route_ports(src, tgt, ports_of(cx, cy), {"w": (WIDTH - 22.0, cy)}, local, bounds,
                                 (box, None))

    parts.append('<defs><marker id="aspen-arrow" markerWidth="9" markerHeight="9" refX="8" refY="3" '
                 f'orient="auto"><path d="M0,0 L0,6 L8,3 z" fill="{MATERIAL}"/></marker>'
                 '<marker id="aspen-arrow-u" markerWidth="9" markerHeight="9" refX="8" refY="3" '
                 f'orient="auto"><path d="M0,0 L0,6 L8,3 z" fill="{UTILITY}"/></marker></defs>')

    used_labels: list[dict] = []

    def draw_flow(points: list, colour: str, width: float, utility: bool, label_parts=None) -> None:
        if len(points) < 2:
            return
        path = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
        dash = ' stroke-dasharray="7 4"' if utility else ""
        marker = "aspen-arrow-u" if utility else "aspen-arrow"
        parts.append(f'<polyline points="{path}" fill="none" stroke="{colour}" stroke-width="{width}"{dash} '
                     f'marker-end="url(#{marker})"/>')
        if not label_parts:
            return
        head, flow = label_parts
        need = max(_text_w(head, 10.5), _text_w(flow, 10.0))
        candidates = []
        for a, b in zip(points, points[1:]):
            horizontal = abs(b[0] - a[0]) >= abs(b[1] - a[1])
            length = abs(b[0] - a[0]) + abs(b[1] - a[1])
            candidates.append((0 if horizontal else 1, -length, a, b))
        for _kind, _length, a, b in sorted(candidates):
            horizontal = abs(b[0] - a[0]) >= abs(b[1] - a[1])
            mx, my = (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0
            if horizontal:
                box = {"x1": mx - need / 2.0, "x2": mx + need / 2.0, "y1": my - 16.0, "y2": my + 5.0}
            else:
                box = {"x1": mx + 6.0, "x2": mx + 6.0 + need, "y1": my - 14.0, "y2": my + 3.0}
            if (abs(b[0] - a[0]) + abs(b[1] - a[1])) < need * 0.8:
                continue
            if box["x1"] < 14.0 or box["x2"] > WIDTH - 14.0:
                continue
            if any(_overlap(box, rect) for rect in obstacles):
                continue
            if any(_overlap(box, other) for other in used_labels):
                continue
            used_labels.append(box)
            if horizontal:
                parts.append(halo(mx, my - 7.0, 10.5, head, colour))
                parts.append(halo(mx, my + 14.0, 10.0, flow, SOFT, weight="400"))
            else:
                parts.append(halo(mx + 7.0, my - 4.0, 10.5, head, colour, anchor="start"))
                parts.append(halo(mx + 7.0, my + 10.0, 10.0, flow, SOFT, anchor="start", weight="400"))
            break

    entries, exits = _terminal_ports(graph)
    entry_node = entries[0] if entries else None
    product = next((node for node in exits if str(node.get("kind")) == "product"),
                   exits[0] if exits else None)
    free_lines: list[dict] = []
    if entry_node and entry_node["id"] in centre:
        free_lines.append({"points": run_free(entry_node, True), "label": "主物料入口（来源）",
                           "width": 2.2, "side": "left"})
    if product and product["id"] in centre:
        free_lines.append({"points": run_free(product, False), "label": "主物料出口（去向）",
                           "width": 2.2, "side": "right"})
    for node in exits:
        if product is not None and node["id"] == product["id"]:
            continue
        if node["id"] in centre:
            free_lines.append({"points": run_free(node, False),
                               "label": f'{_clip(str(node.get("label") or "三废出口"), 10)}（去向）',
                               "width": 1.7, "side": "right"})

    for index, edge in enumerate(graph.get("edges") or []):
        stream = streams[index]
        utility = stream["utility"]
        draw_flow(run_edge(edge), UTILITY if utility else MATERIAL, 1.7, utility,
                  (f'{stream["code"]} {_clip(stream["label"], 12)}', _clip(stream["flow"], 14)))

    for item in free_lines:
        points, label, width, side = item["points"], item["label"], item["width"], item["side"]
        draw_flow(points, MATERIAL, width, False)
        if len(points) < 2:
            continue
        left = side == "left"
        x = 22.0 if left else WIDTH - 22.0
        need = _text_w(label, 11.0)
        anchor = "start" if left else "end"
        base_y = points[0][1] if left else points[-1][1]
        box_x1 = x if left else x - need
        placed_y = None
        for dy in (-8.0, BLOCK_H / 2.0 + 30.0, -(BLOCK_H / 2.0 + 30.0), BLOCK_H + 46.0):
            y = base_y + dy
            box = {"x1": box_x1, "x2": box_x1 + need, "y1": y - 15.0, "y2": y + 3.0}
            if any(_overlap(box, rect) for rect in obstacles):
                continue
            if any(_overlap(box, other) for other in used_labels):
                continue
            placed_y = y
            break
        y = placed_y if placed_y is not None else base_y + BLOCK_H / 2.0 + 30.0
        used_labels.append({"x1": box_x1, "x2": box_x1 + need, "y1": y - 15.0, "y2": y + 3.0})
        parts.append(halo(x, y, 11.0, label, MATERIAL, anchor=anchor))

    for block in blocks:
        cx, cy = centre[block["id"]]
        parts.append(f'<rect x="{cx-BLOCK_W/2.0:.1f}" y="{cy-BLOCK_H/2.0:.1f}" width="{BLOCK_W:.0f}" '
                     f'height="{BLOCK_H:.0f}" fill="{BLOCK_FILL}" stroke="{BLOCK_LINE}" stroke-width="1.8"/>')
        parts.append(f'<text x="{cx:.1f}" y="{cy-6:.1f}" font-size="14" font-weight="700" fill="{INK}" '
                     f'text-anchor="middle">{_esc(_clip(block["tag"], 12))}</text>')
        parts.append(f'<text x="{cx:.1f}" y="{cy+8:.1f}" font-size="10.5" fill="{INK}" text-anchor="middle">'
                     f'{_esc(_clip(block["name"], 13))}</text>')
        parts.append(f'<text x="{cx:.1f}" y="{cy+22:.1f}" font-size="9.5" fill="{SOFT}" text-anchor="middle">'
                     f'{_esc(_clip(block["kind_name"], 14))}</text>')

    table_y = FLOW_BOTTOM + 24.0
    parts.append(f'<text x="20" y="{table_y:.0f}" font-size="12.5" font-weight="700" fill="{INK}">'
                 f'物流表（Aspen Streams）</text>')
    columns = [(40.0, "物流号"), (150.0, "说明"), (300.0, "来源 → 去向"),
               (560.0, "流量 / 单位"), (760.0, "阀门 / 仪表"), (1000.0, "介质")]
    for x, name in columns:
        parts.append(f'<text x="{x+4:.0f}" y="{table_y+17:.0f}" font-size="10.5" font-weight="700" '
                     f'fill="{SOFT}">{_esc(name)}</text>')
    parts.append(f'<line x1="20" y1="{table_y+22:.0f}" x2="{WIDTH-20:.0f}" y2="{table_y+22:.0f}" '
                 f'stroke="{SOFT}" stroke-width="1"/>')
    for index, stream in enumerate(streams):
        y = table_y + 35.0 + index * 13.6
        if y > HEIGHT - 84.0:
            parts.append(f'<text x="24" y="{y:.0f}" font-size="10" fill="{SOFT}">'
                         f'… 其余物流见流程图与 CAD 图册物流表</text>')
            break
        values = [stream["code"], _clip(stream["label"], 20),
                  f'{stream["source"]} → {stream["target"]}'.strip(" →"),
                  _clip(stream["flow"], 16), _clip(stream["assets"], 26), stream["media"]]
        for (x, _name), value in zip(columns, values):
            parts.append(f'<text x="{x+4:.0f}" y="{y:.0f}" font-size="10.5" fill="{INK}">{_esc(value)}</text>')
    parts.append(f'<line x1="20" y1="{HEIGHT-80:.0f}" x2="{WIDTH-20:.0f}" y2="{HEIGHT-80:.0f}" '
                 f'stroke="{SOFT}" stroke-width="1"/>')
    parts.append(f'<text x="20" y="{HEIGHT-60:.0f}" font-size="12.5" font-weight="700" fill="{INK}">'
                 f'单元操作表（Aspen Blocks）</text>')
    for index, block in enumerate(blocks):
        col, row = index % 3, index // 3
        x = 20.0 + col * 420.0
        y = HEIGHT - 42.0 + row * 13.6
        if y > HEIGHT - 6.0:
            break
        parts.append(f'<text x="{x:.0f}" y="{y:.0f}" font-size="10.5" fill="{INK}">'
                     f'{_esc(_clip(block["tag"], 14))} · {_esc(_clip(block["name"], 12))} · '
                     f'建议模型 {_esc(block["model"])}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _inp(title: str, graph: dict, blocks: list[dict], streams: list[dict], stamp: str) -> str:
    lines = [
        "; ============================================================================",
        "; Aspen Plus 输入文件骨架（由「工艺信息 → 概念流程图」自动生成）",
        f"; 图名：{title}",
        f"; 生成：{stamp}（AI for Science 工艺工作区）",
        "; 说明：单元操作、物流连接与已识别的参数按 Aspen 输入文件的写法列在这里；",
        ";       组分、物性方法、热力学数据、设备尺寸与操作条件必须在 Aspen 里补全并运行。",
        ";       本机没有 Aspen 授权，也没有跑过任何模拟，全部“待补”项都必须填。",
        "; ============================================================================",
        "",
        "DYNAMICS",
        " DYNAMIC  NO",
        "",
        "; ---------- 组分（待补：按资料填写组分与分子式） ----------",
        "COMPONENTS",
        "; 例：METHANOL   C H4O / WATER  H2O / METHYL-ACETATE  C3H6O2",
        "",
        "; ---------- 物性方法（待工程师确认） ----------",
        "PROPERTIES  ; 待补：例如 NRTL-RK / RK-SOAVE / UNIQUAC",
        "",
        "; ---------- 单元操作（Blocks） ----------",
    ]
    for block in blocks:
        lines.append(f'BLOCK  {block["tag"]:<14}{block["model"].split(" ")[0]:<14}'
                     f'; {block["name"]}（{block["kind_name"]}）参数：{block["params"]}')
    lines.append("")
    lines.append("; ---------- 物流（Streams） ----------")
    for stream in streams:
        lines.append(f'STREAM {stream["code"]:<8}; {stream["label"]}  '
                     f'{stream["source"]} → {stream["target"]}  流量 {stream["flow"]}  {stream["media"]}')
    lines.append("")
    lines.append("; ---------- 连接（Flowsheet） ----------")
    for stream in streams:
        lines.append(f'CONNECT  {stream["code"]}  {stream["source"]}  {stream["target"]}')
    lines.append("")
    lines.append("; ---------- 待补清单（必须由工程师补全） ----------")
    for item in ("组分与分子式", "物性方法（热力学模型）", "每个单元操作的温度 / 压力 / 规模",
                 "每条物流的流量 / 组成 / 相态", "设备尺寸、塔板数与回流比等设计参数",
                 "公用工程等级与用量、安全联锁"):
        lines.append(f"; TODO  {item}")
    lines.append("; 注：以上清单来自概念流程图，不是已验证的设计数据。")
    return "\n".join(lines) + "\n"


def build_stage(title: str, graph: dict, stamp: str | None = None) -> dict:
    """生成 Aspen 流程模拟图（SVG）+ 物流 / 单元清单 + 输入文件骨架。"""
    graph = {"nodes": list(graph.get("nodes") or []), "edges": list(graph.get("edges") or [])}
    blocks, streams, assets = _rows(graph)
    stamp = stamp or date.today().isoformat()
    if not blocks:
        return {"title": title, "svg": "", "blocks": [], "streams": [], "assets": [],
                "inp": "", "stamp": stamp,
                "notes": ["图中还没有单元操作：先识别工艺信息或手工加节点，再生成流程模拟图。"]}
    notes = [
        f'共 {len(blocks)} 个单元操作、{len(streams)} 条物流：先在这张流程模拟图上复核拓扑与流量，'
        f'再生成 CAD 的 PFD 与图例。',
        '本机没有 Aspen Plus 授权：这张图与输入文件骨架是「模拟前的草稿」，'
        '组分、物性方法、热力学数据与设备尺寸都标为待补，必须由工程师在 Aspen 里补全并运行。',
    ]
    return {"title": title, "svg": _svg(title, graph, blocks, streams, stamp),
            "blocks": blocks, "streams": streams, "assets": assets,
            "inp": _inp(title, graph, blocks, streams, stamp),
            "stamp": stamp, "notes": notes}
