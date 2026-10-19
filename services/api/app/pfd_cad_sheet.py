"""概念级 CAD 图纸合成器：按制图习惯把流程图排成标准图面的图元。

输入是 process_pfd 组装好的 ctx（图数据、物料守恒结果、表格行、说明文字），
输出是与制图无关的图元列表（线 / 折线 / 圆 / 实心箭头 / 文字），再由
``emit_svg`` 与 ``emit_dxf`` 两种后端输出。SVG 预览与 DXF 导出因此几何完全一致。

版面基准（全部按图纸毫米）：
* 图幅按 A4→A0 横放逐级试排，取第一个能容纳「图面 + 表格 + 图签栏」的幅面；
* 图面比例按标准比例系列（1:1/1:2/1:5/1:10/1:20/1:25/1:50/1:100…）取整，
  并保证相邻设备间距不小于 4 倍设备符号宽度，符号不会互相压叠；
* 设备、阀门、仪表符号按 pfd_symbols 的固定基准尺寸绘制，大小比例全图一致；
* 管线终止在符号接口点上：方框设备取轮廓边中点，圆形容器取圆周（外径）。
"""
from __future__ import annotations

import math

from . import pfd_symbols as sym

PAPER = "#ffffff"
# 深色工作区底衬：导出/预览都是一张白纸放在深色工作区上，和界面主题一致，
# 也让「纸面（白）＝图纸范围」一目了然；DXF 里不含这层底衬。
WORKSPACE = "#0a0c0c"
WORKSPACE_PAD = 5.0
INK = "#000000"
INK_SOFT = "#3d3d3d"
INK_RED = "#c00000"
SVG_FONT = "'Microsoft YaHei','Noto Sans CJK SC','SimHei',SimSun,sans-serif"

# --- 图层：先建图层，再按内容选层画线 ---------------------------------------
# 手工制图的习惯是「先在 AutoCAD 里把图层设好、颜色与线型预设好，画哪一类
# 内容就选哪一层」。下面这张表就是院里的《图层标准》原表（图层名 / 色号 /
# 颜色 / 线型 / 用途），出图时照抄进 DXF 的 LAYER 表，图元也写各自的院标层名，
# 因此在 AutoCAD 里打开就是一套分好类、带颜色与线型的图层，选层即可继续画。
# 原表中的重号与跳号（12 / 15 / 18 / 21 各有两个、没有 6 号）按原样保留，
# 不重编号；被工具提示挡住名字的那一层（在 5-设备内部件 与 7-热公用工程 之间）
# 未收录，等用户确认名称后再补。
CAD_LAYERS: tuple[tuple[str, int, str, str, str], ...] = (
    ("0", 7, "#FFFFFF", "CONTINUOUS", "保留层：院标模板自带，一般不直接使用"),
    ("1-主物料线", 5, "#0000FF", "CONTINUOUS", "主物料线：进料端 → 出料端（粗实线）"),
    ("1文字说明", 7, "#FFFFFF", "CONTINUOUS", "主物料线旁的说明与流量标注"),
    ("2-次物料线", 6, "#FF00FF", "CONTINUOUS", "次物料线 / 工艺物料支线（塔顶冷凝液、母液、三废等）"),
    ("3-设备轮廓线", 33, "#A57C52", "CONTINUOUS", "设备、阀门与管件轮廓"),
    ("4-循环物料线", 40, "#FFBF00", "CONTINUOUS", "循环物料线"),
    ("5-设备内部件", 150, "#0080FF", "CONTINUOUS", "设备内部件：塔板、填料、换热管等"),
    ("7-热公用工程", 4, "#00FFFF", "CONTINUOUS", "热公用工程：蒸汽、热水、导热油"),
    ("8-高压蒸汽", 1, "#FF0000", "CONTINUOUS", "高压蒸汽"),
    ("9-冷剂", 112, "#00A552", "CONTINUOUS", "冷剂 / 冷冻水"),
    ("10-中压蒸汽", 2, "#FFFF00", "CONTINUOUS", "中压蒸汽"),
    ("11-进出物流标志", 7, "#FFFFFF", "CONTINUOUS", "进出车间（界区）的流股标志：界区旗标"),
    ("12", 30, "#FF8000", "CONTINUOUS", "院标模板预留层（原表如此）"),
    ("12-辅助及次要物流", 201, "#DF80FF", "CONTINUOUS",
     "公用工程总管与支管、安全阀组 / 放空 / 排污支管、预留管线"),
    ("13-控制阀值线", 51, "#FFFF80", "CONTINUOUS", "控制阀值线"),
    ("14-控制线", 51, "#FFFF80", "CONTINUOUS", "控制线"),
    ("15-图框与标题栏", 7, "#FFFFFF", "CONTINUOUS", "图框、标题栏与图签栏"),
    ("15-仪表线", 4, "#00FFFF", "CONTINUOUS", "仪表引线与就地仪表符号"),
    ("16-文字标注", 7, "#FFFFFF", "CONTINUOUS", "文字标注、注释与说明"),
    ("17-物料表和标题栏", 7, "#FFFFFF", "CONTINUOUS", "物料表、设备一览表等表格"),
    ("18-对中符号", 7, "#FFFFFF", "CONTINUOUS", "对中符号"),
    ("18-信号线", 4, "#00FFFF", "DASHED", "仪表信号线（细虚线）"),
    ("19-设备", 7, "#FFFFFF", "CONTINUOUS", "设备（模板预留层）"),
    ("20-设备位号", 7, "#FFFFFF", "CONTINUOUS", "设备位号"),
    ("21-箭头", 7, "#FFFFFF", "CONTINUOUS", "流向箭头"),
    ("21-设备位号线", 7, "#FFFFFF", "CONTINUOUS", "设备位号引线"),
    ("22-图框", 7, "#FFFFFF", "CONTINUOUS", "图框"),
    ("23-图例与代号", 7, "#FFFFFF", "CONTINUOUS", "图例与代号"),
    ("24-管道编号", 30, "#FF8000", "CONTINUOUS", "管道编号"),
)
LAYER_ACI = {name: aci for name, aci, _rgb, _lt, _use in CAD_LAYERS}
LAYER_RGB = {name: rgb for name, _aci, rgb, _lt, _use in CAD_LAYERS}
LAYER_LTYPE_DXF = {name: lt for name, _aci, _rgb, lt, _use in CAD_LAYERS}
LAYER_USE = {name: use for name, _aci, _rgb, _lt, use in CAD_LAYERS}

# 内部图元层（PFD_*，SVG 预览按它分组）→ 院标图层名；一个院标层可承载多类图元。
LAYER_MAP = {
    "PFD_MAIN": "1-主物料线",
    "PFD_SECONDARY": "2-次物料线",
    "PFD_EQUIPMENT": "3-设备轮廓线",
    "PFD_VALVE": "3-设备轮廓线",
    "PFD_INTERNALS": "5-设备内部件",
    "PFD_BOUNDARY": "11-进出物流标志",
    "PFD_UTILITY": "12-辅助及次要物流",
    "PFD_MINOR": "12-辅助及次要物流",
    "PFD_RESERVED": "12-辅助及次要物流",
    "PFD_INSTRUMENT": "15-仪表线",
    "PFD_TITLEBLOCK": "15-图框与标题栏",
    "PFD_TITLE": "15-图框与标题栏",
    "PFD_LABEL": "16-文字标注",
    "PFD_NOTE": "16-文字标注",
    "PFD_WARN": "16-文字标注",
    "PFD_PARAM": "16-文字标注",
    "PFD_FLOW": "1文字说明",
    "PFD_TABLE": "17-物料表和标题栏",
    "PFD_SIGNAL": "18-信号线",
    "PFD_TAG": "20-设备位号",
    "PFD_ARROW": "21-箭头",
    "PFD_FRAME": "22-图框",
}
# 图元级覆盖：预留管线落在「12-辅助及次要物流」层上，仍按点划线画；物料不守恒
# 警告与图名保留原来的红色强调（只覆盖图元颜色，不改图层）。
ENTITY_LTYPE = {"PFD_RESERVED": "DASHDOT", "PFD_SIGNAL": "DASHED"}
ENTITY_COLOR = {"PFD_WARN": 1, "PFD_TITLE": 1}

# 辅助物料线（公用工程、安全阀组 / 放空 / 排污支管）一律实线：只有「预留管线」
# 用点划线与「仪表信号」用虚线。
LAYER_LTYPE = {"PFD_RESERVED": "DASHDOT", "PFD_SIGNAL": "DASHED"}
# SVG 预览的出图顺序（先画的在下层）：保持原有相对次序，只把新增的图元层插进去。
LAYER_ORDER = (
    "PFD_FRAME", "PFD_EQUIPMENT", "PFD_VALVE", "PFD_INTERNALS", "PFD_INSTRUMENT",
    "PFD_MAIN", "PFD_SECONDARY", "PFD_MINOR", "PFD_UTILITY", "PFD_RESERVED", "PFD_SIGNAL",
    "PFD_TAG", "PFD_LABEL", "PFD_NOTE", "PFD_FLOW", "PFD_BOUNDARY", "PFD_ARROW",
    "PFD_TITLE", "PFD_TABLE", "PFD_TITLEBLOCK", "PFD_WARN", "PFD_PARAM",
)
SVG_COLOR = {
    "PFD_TITLE": INK_RED, "PFD_WARN": INK_RED, "PFD_NOTE": INK_SOFT,
    "PFD_LABEL": INK_SOFT, "PFD_TAG": INK_SOFT, "PFD_TABLE": INK_SOFT,
    "PFD_TITLEBLOCK": INK_SOFT,
}

GAP = 6.0
HEADER_H = 22.0
# 图面框最小高度：再矮就把符号、位号与流量标注挤在一起了。
# 取 110（A3 横放在表格占去大半后仍留有这个高度），比 118 更容易选中
# 小一号的图幅：图面比例被间距上限卡住时，换更大的纸只会多出空白。
DRAWING_MIN_H = 110.0
DRAWING_EDGE = 18.0         # 图面上下各留的空白（mm）
VERTICAL_FILL_MAX = 1.8     # 行距最多摊开到 1.8 倍，避免两排设备离得太远
CAPTION_ROOM = 12.0
EDGE_NAME_ROOM = 30.0       # 图面左右各留给位号/名称的横向余量（mm）         # 图面上下给位号/名称与进出口标注留的高度
ROUTE_ROOM = 14.0           # 图面上沿额外留的走线空间（塔顶汽相要从塔顶接口向上出线）
TITLE_BLOCK_W = 190.0
# 图签栏行高：13 行标签要在一张 A3 上留出足够图面，行高收紧到 4.0 mm（仍高于字高 3.0）
TITLE_BLOCK_ROW = 4.0
MAX_STREAM_LABEL_W = 84.0
# 标注最少要放得下的管段长度减 10：符号放大后相邻设备之间的直管段变短，
# 阈值卡在 22 会把主干物流（S01/S06/S08/S09）的编号与流量一起丢掉。
MIN_STREAM_LABEL_ROOM = 15.0
LEGEND_CELL_MIN = 30.0
LEGEND_CELL_MAX = 52.0
# 图签栏比例栏：节点坐标是画布排版单位，图纸一律按“不按比例”出图。
NTS = "不按比例（NTS）"
UTILITY_LANE_STEP = 9.0     # 相邻两条公用工程总管之间的行距（mm）
RELIEF_ROOM = 19.0          # 图面上沿给安全阀组/放空阀预留的高度（mm）
UTILITY_TEXT = 2.6          # 公用工程总管标注的字高（与图面注记一致）
SIGNAL_DOT = 1.1            # 信号线在仪表一端的端点小圆点半径（mm）

# --- 进出车间（界区）流股旗标 -----------------------------------------------
# 设计院通用画法：进出车间的流股在端头画一个「方框 + 尖端」的旗标，旗标尖端顺着
# 介质流动方向，框内写流股名称与来源 / 去向。进料端的平边贴着图框左边线、尖端朝
# 车间内；出料端的尖端贴着图框右边线。这样「物料从哪来、最后往哪去」一眼可辨。
FLAG_TEXT = sym.T_NAME      # 旗标内文字的字高（mm）
FLAG_TIP_RATIO = 0.55       # 旗标尖端长度 = 旗标高度的 0.55
FLAG_PAD_X = 1.6            # 旗标内文字左右留白（mm）
FLAG_PAD_Y = 1.0            # 旗标内文字上下留白（mm）
FLAG_MIN_BODY = 9.0         # 旗标方框的最小宽度（mm）


# --- 文字尺寸估算 -----------------------------------------------------------
def text_width(value: str, size: float) -> float:
    total = 0.0
    for char in str(value):
        total += size if ord(char) > 0x2E80 else size * 0.60
    return total


def clip(value: str, size: float, limit: float) -> str:
    text_value = str(value)
    if limit <= 0 or text_width(text_value, size) <= limit:
        return text_value
    out = ""
    for char in text_value:
        if text_width(out + char + "…", size) > limit:
            break
        out += char
    return (out + "…") if out else ""


def wrap(value: str, size: float, limit: float) -> list[str]:
    lines: list[str] = []
    current = ""
    for char in str(value):
        if char == "\n":
            lines.append(current)
            current = ""
            continue
        if text_width(current + char, size) > limit and current:
            lines.append(current)
            current = char
        else:
            current += char
    if current or not lines:
        lines.append(current)
    return lines


# --- 图元构造 ---------------------------------------------------------------
def line(prims, x1, y1, x2, y2, weight=sym.W_EQUIP, layer="PFD_EQUIPMENT", ink=None):
    """线图元。``ink`` 只给「图层表」的色块用：预览里按院标色号显示该层的颜色。"""
    prims.append({"t": "line", "x1": x1, "y1": y1, "x2": x2, "y2": y2, "w": weight,
                  "layer": layer, "ink": ink})


def poly(prims, points, closed=False, weight=sym.W_EQUIP, layer="PFD_EQUIPMENT"):
    if len(points) < 2:
        return
    prims.append({"t": "poly", "pts": [(float(x), float(y)) for x, y in points], "closed": bool(closed),
                  "w": weight, "layer": layer})


def circle(prims, cx, cy, r, weight=sym.W_THIN, layer="PFD_EQUIPMENT"):
    prims.append({"t": "circle", "cx": cx, "cy": cy, "r": r, "w": weight, "layer": layer})


def rect(prims, x, y, w, h, weight=sym.W_THIN, layer="PFD_TABLE"):
    prims.append({"t": "rect", "x": x, "y": y, "w": w, "h": h, "lw": weight, "layer": layer})


def solid(prims, points, layer="PFD_ARROW"):
    prims.append({"t": "solid", "pts": [(float(x), float(y)) for x, y in points], "layer": layer})


def text(prims, x, y, size, value, layer="PFD_LABEL", anchor="start", weight="normal"):
    if value is None or str(value) == "":
        return
    prims.append({"t": "text", "x": float(x), "y": float(y), "h": float(size), "value": str(value),
                  "layer": layer, "anchor": anchor, "weight": weight})


def draw_shapes(prims, items, map_point, layer="PFD_EQUIPMENT"):
    for item in items:
        kind = item[0]
        if kind == "line":
            x1, y1 = map_point(item[1], item[2])
            x2, y2 = map_point(item[3], item[4])
            line(prims, x1, y1, x2, y2, item[5], layer)
        elif kind == "poly":
            poly(prims, [map_point(px, py) for px, py in item[1]], item[2], item[3], layer)
        elif kind == "rect":
            x1, y1 = map_point(item[1], item[2])
            x2, y2 = map_point(item[1] + item[3], item[2] + item[4])
            rect(prims, min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1), item[5], layer)
        elif kind == "circle":
            px, py = map_point(item[1], item[2])
            dot = map_point(item[1] + item[3], item[2])
            circle(prims, px, py, max(abs(dot[0] - px), abs(dot[1] - py)), item[4], layer)


def shape_bounds(items):
    """符号图元的外框，用于把符号在格子里居中。"""
    xs: list[float] = []
    ys: list[float] = []
    for item in items:
        if item[0] == "line":
            xs += [item[1], item[3]]
            ys += [item[2], item[4]]
        elif item[0] == "poly":
            xs += [px for px, _ in item[1]]
            ys += [py for _, py in item[1]]
        elif item[0] == "rect":
            xs += [item[1], item[1] + item[3]]
            ys += [item[2], item[2] + item[4]]
        elif item[0] == "circle":
            xs += [item[1] - item[3], item[1] + item[3]]
            ys += [item[2] - item[3], item[2] + item[3]]
    if not xs:
        return 0.0, 0.0, 0.0, 0.0
    return min(xs), min(ys), max(xs), max(ys)


def legend_size(kind: str) -> float:
    """图例里各类符号的基准尺寸：设备中等、阀门清晰可辨、仪表略小。

    与工段图一样乘上该类型的放大系数，图例与图面上的大小比例一一对应
    （塔在图例里同样是最高最大的那个），并限制在图例格子里放得下。
    """
    if kind.startswith("valve_"):
        return 4.4
    if kind.startswith("instrument_"):
        return 4.6
    return min(6.4 * sym.symbol_scale(kind), 8.0)


def translate(cx, cy):
    return lambda x, y: (cx + x, cy + y)


def rotate_map(cx, cy, direction):
    """把符号库的 +x 方向旋转到 direction（正交方向）。"""
    ux, uy = direction
    if abs(ux) >= abs(uy):
        sign = 1.0 if ux >= 0 else -1.0
        return lambda x, y: (cx + sign * x, cy + sign * y)
    sign = 1.0 if uy >= 0 else -1.0
    return lambda x, y: (cx - sign * y, cy + sign * x)


# --- 表格、图签栏、页眉 -----------------------------------------------------
def draw_table(prims, x, y, width, title, columns, weights, rows, empty_note, row_h,
               cell=sym.T_TABLE_CELL, head=sym.T_TABLE_HEAD):
    text(prims, x, y - 1.8, sym.T_TABLE_TITLE, title, "PFD_TABLE", weight="bold")
    body_h = head + 1.8 + row_h * max(1, len(rows))
    rect(prims, x, y, width, body_h, sym.W_EQUIP, "PFD_TABLE")
    widths = [width * weight for weight in weights]
    cursor = x
    for column, column_w in zip(columns, widths):
        text(prims, cursor + 1.4, y + head - 0.5, head * 0.82, column, "PFD_TABLE", weight="bold")
        cursor += column_w
    cursor = x
    for column_w in widths[:-1]:
        cursor += column_w
        line(prims, cursor, y, cursor, y + body_h, sym.W_THIN, "PFD_TABLE")
    head_y = y + head + 1.8
    line(prims, x, head_y, x + width, head_y, sym.W_EQUIP, "PFD_TABLE")
    if not rows:
        text(prims, x + 2.0, head_y + row_h * 0.72, cell, empty_note, "PFD_TABLE")
        return body_h
    for index, row in enumerate(rows):
        baseline = head_y + row_h * index + row_h * 0.72
        if index:
            line(prims, x, head_y + row_h * index, x + width, head_y + row_h * index, sym.W_THIN, "PFD_TABLE")
        cursor = x
        for value, column_w in zip(row, widths):
            text(prims, cursor + 1.4, baseline, cell, clip(value, cell, column_w - 3.0), "PFD_TABLE")
            cursor += column_w
    return body_h


def table_height(row_h, count, head=sym.T_TABLE_HEAD):
    return head + 1.8 + row_h * max(1, count)


def draw_title_block(prims, x, y, width, rows):
    height = TITLE_BLOCK_ROW * len(rows) + sym.T_TB_VALUE * 1.2
    rect(prims, x, y, width, height, sym.W_MAIN, "PFD_TITLEBLOCK")
    label_w = 34.0
    line(prims, x + label_w, y, x + label_w, y + height, sym.W_EQUIP, "PFD_TITLEBLOCK")
    for index, (label, value) in enumerate(rows):
        top = y + TITLE_BLOCK_ROW * index
        if index:
            line(prims, x, top, x + width, top, sym.W_THIN, "PFD_TITLEBLOCK")
        text(prims, x + 1.4, top + sym.T_TB_LABEL + 0.7, sym.T_TB_LABEL, label, "PFD_TITLEBLOCK")
        text(prims, x + label_w + 1.6, top + sym.T_TB_VALUE + 0.7, sym.T_TB_VALUE,
             clip(value, sym.T_TB_VALUE, width - label_w - 4.0), "PFD_TITLEBLOCK", weight="bold")
    return height


def draw_header(prims, x, y, width, title, sheet_name, sheet_index, meta, scale_text, format_text):
    text(prims, x + 1.0, y + sym.T_FIG_TITLE, sym.T_FIG_TITLE,
         clip(title, sym.T_FIG_TITLE, width - 110.0), "PFD_TITLE", weight="bold")
    right = x + width
    text(prims, right, y + 3.6, sym.T_TAG, sheet_index, "PFD_LABEL", anchor="end", weight="bold")
    text(prims, right, y + 8.4, sym.T_HEAD, sheet_name, "PFD_LABEL", anchor="end")
    text(prims, right, y + 12.8, sym.T_NOTE, f"比例 {scale_text}　幅面 {format_text}", "PFD_LABEL", anchor="end")
    text(prims, x + 1.0, y + sym.T_FIG_TITLE + 5.0, sym.T_NOTE,
         f'项目：{meta.get("project") or "未指定项目"}　产品：{meta.get("product") or "未指定产品"}　'
         f'版本 {meta.get("version") or "—"} / 修订 {meta.get("revision") or "—"}　'
         f'数据来源：{meta.get("source") or "手工绘制或演示草稿"}', "PFD_LABEL")
    line(prims, x, y + HEADER_H - 1.0, right, y + HEADER_H - 1.0, sym.W_MAIN, "PFD_FRAME")
    return HEADER_H


def draw_notes(prims, x, y, width, lines):
    height = 2.4 + 4.6 * len(lines)
    rect(prims, x, y, width, height, sym.W_MINOR, "PFD_NOTE")
    for index, value in enumerate(lines):
        text(prims, x + 2.0, y + 4.6 + 4.6 * index, sym.T_NOTE, clip(value, sym.T_NOTE, width - 4.0), "PFD_NOTE")
    return height


def notes_height(count):
    return 2.4 + 4.6 * count


def draw_warning(prims, x, y, width, headline, lines):
    height = warning_height(len(lines))
    rect(prims, x, y, width, height, sym.W_MAIN, "PFD_WARN")
    text(prims, x + 2.0, y + 6.2, sym.T_HEAD, clip(headline, sym.T_HEAD, width - 4.0), "PFD_WARN", weight="bold")
    for index, value in enumerate(lines):
        text(prims, x + 2.0, y + 11.4 + 5.0 * index, sym.T_NOTE, clip(value, sym.T_NOTE, width - 4.0), "PFD_WARN")
    return height


def warning_height(lines):
    return 12.0 + 5.0 * max(1, lines)


def draw_frame(prims, width, height):
    line(prims, sym.SHEET_MARGIN_LEFT, sym.SHEET_MARGIN, width - sym.SHEET_MARGIN, sym.SHEET_MARGIN, sym.W_MAIN, "PFD_FRAME")
    line(prims, sym.SHEET_MARGIN_LEFT, height - sym.SHEET_MARGIN, width - sym.SHEET_MARGIN, height - sym.SHEET_MARGIN, sym.W_MAIN, "PFD_FRAME")
    line(prims, sym.SHEET_MARGIN_LEFT, sym.SHEET_MARGIN, sym.SHEET_MARGIN_LEFT, height - sym.SHEET_MARGIN, sym.W_MAIN, "PFD_FRAME")
    line(prims, width - sym.SHEET_MARGIN, sym.SHEET_MARGIN, width - sym.SHEET_MARGIN, height - sym.SHEET_MARGIN, sym.W_MAIN, "PFD_FRAME")
    inner = 3.0
    line(prims, sym.SHEET_MARGIN_LEFT + inner, sym.SHEET_MARGIN + inner, width - sym.SHEET_MARGIN - inner, sym.SHEET_MARGIN + inner, sym.W_THIN, "PFD_FRAME")
    line(prims, sym.SHEET_MARGIN_LEFT + inner, height - sym.SHEET_MARGIN - inner, width - sym.SHEET_MARGIN - inner, height - sym.SHEET_MARGIN - inner, sym.W_THIN, "PFD_FRAME")
    line(prims, sym.SHEET_MARGIN_LEFT + inner, sym.SHEET_MARGIN + inner, sym.SHEET_MARGIN_LEFT + inner, height - sym.SHEET_MARGIN - inner, sym.W_THIN, "PFD_FRAME")
    line(prims, width - sym.SHEET_MARGIN - inner, sym.SHEET_MARGIN + inner, width - sym.SHEET_MARGIN - inner, height - sym.SHEET_MARGIN - inner, sym.W_THIN, "PFD_FRAME")


# --- 管线走线 ---------------------------------------------------------------
def _port_normal(key):
    """接口的朝外法线：出线/进线的方向，用来挑「朝向下游」的接口。"""
    if key in ("w", "in"):
        return -1.0, 0.0
    if key in ("e", "out"):
        return 1.0, 0.0
    if key == "n":
        return 0.0, -1.0
    if key == "s":
        return 0.0, 1.0
    return 0.0, 0.0


def _pick_ports(source, target, src_ports, tgt_ports):
    dx = target["cx"] - source["cx"]
    dy = target["cy"] - source["cy"]
    pairs = (("e", "w"), ("w", "e"), ("s", "n"), ("n", "s")) if abs(dx) >= abs(dy) \
        else (("s", "n"), ("n", "s"), ("e", "w"), ("w", "e"))
    for key_s, key_t in pairs:
        if key_s in src_ports and key_t in tgt_ports:
            # 回程管线（去向在自己的上游）不能用「上一台的下口直接进下一台的上口」：
            # 两台设备上下叠放时那条线会从两个符号身上穿过去，改从侧面绕回来。
            if dy < -1e-6 and dx <= 1e-6 and (key_s, key_t) in (("s", "n"), ("n", "s")):
                for side_s, side_t in (("e", "e"), ("w", "w")):
                    if side_s in src_ports and side_t in tgt_ports:
                        return side_s, side_t
            return key_s, key_t
    # 语义约束（例如「塔顶接口只能走 n」）把常规配对全部排除时，不能随手拿字典里的
    # 第一个接口：要挑「出线方向朝向下游、进线方向迎着上游」的那一对，否则回流线
    # 会从泵的进口出去，绕一大圈再从塔身里穿上去。
    best = None
    best_score = None
    for key_s, point_s in src_ports.items():
        sx, sy = _port_normal(key_s)
        for key_t, point_t in tgt_ports.items():
            tx, ty = _port_normal(key_t)
            score = (-(sx * dx + sy * dy) - (tx * dx + ty * dy),
                     math.hypot(point_t[0] - point_s[0], point_t[1] - point_s[1]))
            if best_score is None or score < best_score:
                best_score = score
                best = (key_s, key_t)
    if best is not None:
        return best
    return next(iter(src_ports)), next(iter(tgt_ports))


def _segment_hits(a, b, boxes):
    """线段是否穿过任一设备外框（正交线）。"""
    if abs(a[1] - b[1]) < 1e-6:
        low, high = sorted((a[0], b[0]))
        for box in boxes:
            if box["y1"] - 1.5 <= a[1] <= box["y2"] + 1.5 and low < box["x2"] - 1.0 and high > box["x1"] + 1.0:
                return True
        return False
    if abs(a[0] - b[0]) < 1e-6:
        low, high = sorted((a[1], b[1]))
        for box in boxes:
            if box["x1"] - 1.5 <= a[0] <= box["x2"] + 1.5 and low < box["y2"] - 1.0 and high > box["y1"] + 1.0:
                return True
        return False
    return False


def _overlap(a, b):
    return not (a["x2"] <= b["x1"] or a["x1"] >= b["x2"] or a["y2"] <= b["y1"] or a["y1"] >= b["y2"])


def _label_spot(a, b, boxes, width, height):
    """在最长线段附近找一个不压住设备与位号标注的位置。"""
    dx, dy = b[0] - a[0], b[1] - a[1]
    span = math.hypot(dx, dy) or 1.0
    px_unit, py_unit = -dy / span, dx / span
    best = None
    best_score = None
    for offset in (0.0, -11.0, 11.0, -19.0, 19.0, -27.0, 27.0, -35.0, 35.0, -43.0, 43.0,
                   -52.0, 52.0, -62.0, 62.0):
        for ratio in (0.5, 0.42, 0.58, 0.34, 0.66, 0.26, 0.74, 0.2, 0.8, 0.16, 0.84, 0.12, 0.88):
            px = a[0] + dx * ratio + px_unit * offset
            py = a[1] + dy * ratio + py_unit * offset
            box = {"x1": px - width / 2.0, "x2": px + width / 2.0, "y1": py - height - 2.0, "y2": py + 1.0}
            hits = sum(1 for other in boxes if _overlap(box, other))
            if hits == 0:
                return px, py
            score = (hits, abs(offset), abs(ratio - 0.5))
            if best_score is None or score < best_score:
                best_score = score
                best = (px, py)
    if best is not None:
        return best
    return (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0


def _label_spot_vertical(a, b, boxes, width):
    """竖管标注：沿管线上下找位置，文字排在管线右侧的两行里。

    占位框必须按「文字实际横排的宽×两行的高」来算；早先按 4 mm 宽×room 高
    去避让，等于把整条竖带当成禁区，标注才会压到旁边的管线上。
    """
    dx, dy = b[0] - a[0], b[1] - a[1]
    best = None
    best_score = None
    for push in (0.0, 5.0, 10.0, 16.0, 22.0, -5.0, -10.0):
        for ratio in (0.5, 0.42, 0.58, 0.34, 0.66, 0.26, 0.74, 0.2, 0.8, 0.14, 0.86):
            px = a[0] + dx * ratio + push
            py = a[1] + dy * ratio
            box = {"x1": px + 1.8, "x2": px + 1.8 + width, "y1": py - 7.5, "y2": py + 1.0}
            hits = sum(1 for other in boxes if _overlap(box, other))
            if hits == 0:
                return px, py
            score = (hits, abs(push), abs(ratio - 0.5))
            if best_score is None or score < best_score:
                best_score = score
                best = (px, py)
    if best is not None:
        return best
    return (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0


def _text_box(x, y, height, value, anchor="start"):
    """粗略的文字占位框，中文按 1.0em、西文按 0.62em 估算宽度。"""
    width = text_width(value, height)
    x1 = x if anchor == "start" else (x - width if anchor == "end" else x - width / 2.0)
    return {"x1": x1 - 0.6, "x2": x1 + width + 0.6, "y1": y - height - 0.8, "y2": y + height * 0.35 + 0.8}


def _free_text_spot(x, y, height, value, occupied, anchor="start", shifts=((0.0, 0.0),)):
    """位号挪到不压住已有文字的位置；挪不开就留在原位，绝不静默压字。"""
    for dx, dy in shifts:
        box = _text_box(x + dx, y + dy, height, value, anchor)
        if not any(_overlap(box, other) for other in occupied):
            return x + dx, y + dy, box
    box = _text_box(x, y, height, value, anchor)
    return x, y, box


def _place_caption(cx, bottom, tag_text, name_text, occupied, pipes, limit=None, above=None):
    """设备位号 / 名称：默认居中在符号下方，被管线或已放标注压住时就近挪开。

    图纸规范：位号与名称不得被管线穿过（不能当作下划线）。
    候选顺序：先右左平移（竖管），再逐步下移（横管）；
    图面下缘（表格顶线）放不下时，改排到符号上方。

    limit：图面可用下缘；above：符号上沿，用于向上排。
    """
    lines = [(tag_text, sym.T_TAG, 5.6), (name_text, sym.T_NAME, 9.6)]
    lines = [item for item in lines if item[0]]
    if not lines:
        return cx, bottom + 5.6, bottom + 5.6
    step = max(text_width(item[0], item[1]) for item in lines) / 2.0 + 2.2

    blocked = list(occupied) + list(pipes)
    if limit is not None:
        # 表格顶线以下不可占用：把那一片当成障碍，候选自然往别处走
        blocked.append({"x1": -1.0e6, "x2": 1.0e6, "y1": limit, "y2": 1.0e9})

    # 每种排法给出“位号行 / 名称行”相对符号下沿的额外偏移；
    # 默认向下，最后一种是排到符号上方（名称在上、位号在下）。
    layouts: list[tuple[float, float]] = [(dy, dy) for dy in (0.0, 4.4, 8.8)]
    if above is not None:
        layouts.append((above - bottom - 7.6, above - bottom - 15.6))

    best = None
    best_score = None
    for tag_dy, name_dy in layouts:
        for dx in (0.0, step, -step, step * 1.6, -step * 1.6):
            boxes = []
            for (value, height, offset), dy in zip(lines, (tag_dy, name_dy)):
                boxes.append(_text_box(cx + dx, bottom + offset + dy, height, value, "middle"))
            hits = 0
            area = 0.0
            for box in boxes:
                for other in blocked:
                    if not _overlap(box, other):
                        continue
                    hits += 1
                    area += max(0.0, min(box["x2"], other["x2"]) - max(box["x1"], other["x1"])) * \
                        max(0.0, min(box["y2"], other["y2"]) - max(box["y1"], other["y1"]))
            spot = (cx + dx, bottom + lines[0][2] + tag_dy, bottom + lines[-1][2] + name_dy)
            if hits == 0:
                return spot
            score = (round(area, 2), abs(dx) + abs(tag_dy) + abs(name_dy))
            if best_score is None or score < best_score:
                best_score = score
                best = spot
    return best

def _inside(points, bounds):
    x1, y1, x2, y2 = bounds
    for x, y in points:
        if x < x1 - 0.5 or x > x2 + 0.5 or y < y1 - 0.5 or y > y2 + 0.5:
            return False
    return True


def _dedupe(raw):
    points: list[tuple[float, float]] = []
    for point in raw:
        if not points or abs(point[0] - points[-1][0]) > 0.05 or abs(point[1] - points[-1][1]) > 0.05:
            points.append(point)
    return points


def _corridor_levels(obstacles, axis, lo, hi, pad=5.0):
    """候选通道位置：贴着设备外框两侧，外加图框内缩边界。"""
    values: list[float] = [lo, hi]
    for box in obstacles:
        low, high = (box["y1"], box["y2"]) if axis == "y" else (box["x1"], box["x2"])
        values.extend((low - pad, high + pad))
    return sorted({round(value, 3) for value in values if lo - 0.05 <= value <= hi + 0.05})


def _nearest(values, target, limit):
    return sorted(values, key=lambda value: abs(value - target))[:limit]


def _lane_free(lanes, span, obstacles, port, near_id):
    """竖管能落在哪几条通道上：贴着 near_id「顶接口」的那一小段不算障碍。"""
    def blocked(lane):
        band = {"x1": lane - 1.6, "x2": lane + 1.6, "y1": span[0], "y2": span[1]}
        for box in obstacles:
            probe = box
            if box["id"] == near_id:
                probe = {"id": box["id"], "x1": box["x1"], "x2": box["x2"],
                         "y1": max(box["y1"], port[1] + 2.0), "y2": box["y2"]}
                if probe["y1"] >= probe["y2"]:
                    continue
            if _overlap(band, probe):
                return True
        return False

    return [lane for lane in lanes if not blocked(lane)]


def _route_cost(points, obstacles, bounds):
    """先看是否出图框，再看压住几个设备框，最后比长度。"""
    hits = sum(1 for index in range(len(points) - 1)
               if any(_segment_hits(points[index], points[index + 1], (box,)) for box in obstacles))
    outside = 0 if bounds is None or _inside(points, bounds) else 1
    return outside, hits, polyline_length(points)


def _corridor_route(p1, a, c, p2, horizontal, obstacles, bounds):
    """贴边通道回退：走设备框之间的空档，避免管线横穿设备符号。"""
    if not obstacles:
        return None
    if bounds:
        x_lo, y_lo, x_hi, y_hi = bounds
    else:
        xs = [box["x1"] for box in obstacles] + [box["x2"] for box in obstacles] + [p1[0], p2[0]]
        ys = [box["y1"] for box in obstacles] + [box["y2"] for box in obstacles] + [p1[1], p2[1]]
        x_lo, x_hi = min(xs) - 8.0, max(xs) + 8.0
        y_lo, y_hi = min(ys) - 8.0, max(ys) + 8.0
    levels_y = _corridor_levels(obstacles, "y", y_lo, y_hi)
    levels_x = _corridor_levels(obstacles, "x", x_lo, x_hi)
    candidates: list[list[tuple[float, float]]] = []
    if horizontal:
        for level in _nearest(levels_y, (a[1] + c[1]) / 2.0, 6):
            candidates.append(_dedupe([p1, a, (a[0], level), (c[0], level), c, p2]))
        for trunk in _nearest(levels_x, (a[0] + c[0]) / 2.0, 6):
            for near_a in _nearest(levels_y, a[1], 3):
                for near_c in _nearest(levels_y, c[1], 3):
                    candidates.append(_dedupe([p1, a, (a[0], near_a), (trunk, near_a),
                                               (trunk, near_c), (c[0], near_c), c, p2]))
    else:
        for level in _nearest(levels_x, (a[0] + c[0]) / 2.0, 6):
            candidates.append(_dedupe([p1, a, (level, a[1]), (level, c[1]), c, p2]))
        for trunk in _nearest(levels_y, (a[1] + c[1]) / 2.0, 6):
            for near_a in _nearest(levels_x, a[0], 3):
                for near_c in _nearest(levels_x, c[0], 3):
                    candidates.append(_dedupe([p1, a, (near_a, a[1]), (near_a, trunk),
                                               (near_c, trunk), (near_c, c[1]), c, p2]))
    if not candidates:
        return None
    return min(candidates, key=lambda points: _route_cost(points, obstacles, bounds))


def route_ports(source, target, src_ports, tgt_ports, obstacles=(), bounds=None, leave=()):
    """正交走线：两端沿接口法线出线，中间走「通道」，绕开其它设备符号。

    管线只从接口点进出，既不穿进符号本体，也不会横穿相邻设备。
    """
    key_s, key_t = _pick_ports(source, target, src_ports, tgt_ports)
    p1 = src_ports[key_s]
    p2 = tgt_ports[key_t]
    stub = 4.0
    nx = -1.0 if key_s == "w" else (1.0 if key_s == "e" else 0.0)
    ny = -1.0 if key_s == "n" else (1.0 if key_s == "s" else 0.0)
    mx = -1.0 if key_t == "w" else (1.0 if key_t == "e" else 0.0)
    my = -1.0 if key_t == "n" else (1.0 if key_t == "s" else 0.0)
    a = (p1[0] + nx * stub, p1[1] + ny * stub)
    c = (p2[0] + mx * stub, p2[1] + my * stub)
    # 同一侧进出（上下叠放的两台设备之间绕回来）：两端贴到同一条竖线上，
    # 否则线头会先出去几毫米再折回来，图上看着像画粗了。
    if key_s == key_t and key_s in ("e", "w"):
        shared = max(a[0], c[0]) if key_s == "e" else min(a[0], c[0])
        a = (shared, a[1])
        c = (shared, c[1])
    src_leave, tgt_leave = (tuple(leave) + (None, None))[:2]

    # 出线短管必然贴着本设备本体（泵的出口就在本体框里），所以只放行「第一段贴着
    # 起点设备、最后一段贴着终点设备」；中间各段一律不许穿设备本体。
    def _blocked(candidate):
        last = len(candidate) - 2
        for index in range(len(candidate) - 1):
            for box in obstacles:
                if (index == 0 and box is src_leave) or (index == last and box is tgt_leave):
                    continue
                if _segment_hits(candidate[index], candidate[index + 1], (box,)):
                    return True
        return False

    horizontal = key_s in ("w", "e")
    offsets = (0.0, -13.0, 13.0, -21.0, 21.0, -30.0, 30.0, -40.0, 40.0, -52.0, 52.0)
    fallback = None
    for offset in offsets:
        if horizontal:
            level = a[1] + offset
            raw = [p1, a, (a[0], level), (c[0], level), c, p2]
        else:
            level = a[0] + offset
            raw = [p1, a, (level, a[1]), (level, c[1]), c, p2]
        points = _dedupe(raw)
        if fallback is None:
            fallback = points
        if bounds and not _inside(points, bounds):
            continue
        if _blocked(points):
            continue
        return points
    chosen = _corridor_route(p1, a, c, p2, horizontal, obstacles, bounds)
    if chosen is not None and _route_cost(chosen, obstacles, bounds) < _route_cost(fallback, obstacles, bounds):
        return chosen
    return fallback or _dedupe([p1, a, c, p2])


def polyline_length(points):
    return sum(math.hypot(points[i + 1][0] - points[i][0], points[i + 1][1] - points[i][1])
               for i in range(len(points) - 1))


def point_at(points, distance):
    remaining = max(0.0, distance)
    for index in range(len(points) - 1):
        x1, y1 = points[index]
        x2, y2 = points[index + 1]
        seg = math.hypot(x2 - x1, y2 - y1)
        if seg <= 1e-9:
            continue
        if remaining <= seg or index == len(points) - 2:
            ratio = min(1.0, remaining / seg)
            return (x1 + (x2 - x1) * ratio, y1 + (y2 - y1) * ratio), ((x2 - x1) / seg, (y2 - y1) / seg)
        remaining -= seg
    x1, y1 = points[-1]
    return (x1, y1), (1.0, 0.0)


def split_polyline(points, cuts):
    """按弧长区间切开折线：阀门占用的那一段不画线，管线终止在阀门口上。"""
    if not cuts:
        return [points]
    total = polyline_length(points)
    edges = [0.0]
    for index in range(len(points) - 1):
        edges.append(edges[-1] + math.hypot(points[index + 1][0] - points[index][0],
                                            points[index + 1][1] - points[index][1]))
    boundaries = sorted({0.0, total} | {max(0.0, min(total, value)) for cut in cuts for value in cut})
    spans: list[tuple[float, float]] = []
    for index in range(len(boundaries) - 1):
        start, end = boundaries[index], boundaries[index + 1]
        if end - start <= 1e-6:
            continue
        if any(cut[0] - 1e-6 <= start and end <= cut[1] + 1e-6 for cut in cuts):
            continue
        spans.append((start, end))
    parts: list[list[tuple[float, float]]] = []
    for start, end in spans:
        part: list[tuple[float, float]] = [point_at(points, start)[0]]
        for index in range(len(points)):
            if start + 1e-6 < edges[index] < end - 1e-6:
                part.append(points[index])
        part.append(point_at(points, end)[0])
        parts.append(part)
    return [part for part in parts if len(part) >= 2]


def arrow_solid(prims, points, size=3.2, layer="PFD_ARROW"):
    if len(points) < 2:
        return
    (px, py), (tx, ty) = points[-2], points[-1]
    length = math.hypot(tx - px, ty - py) or 1.0
    ux, uy = (tx - px) / length, (ty - py) / length
    bx, by = tx - ux * size, ty - uy * size
    solid(prims, [(tx, ty), (bx - uy * size * 0.34, by + ux * size * 0.34),
                  (bx + uy * size * 0.34, by - ux * size * 0.34)], layer)


# --- 进出车间（界区）流股旗标 -----------------------------------------------
def _boundary_lines(kind, entry, compact=False) -> list[str]:
    """旗标里的文字：进料端写来源、出料端写去向，排两行免得旗标拉得太长。"""
    if entry:
        return ["主物料入口", "（来源）"]
    if kind == "waste":
        return ["三废出口", "（去向）"]
    if compact:
        return ["物料出口"]
    return ["主物料出口", "（去向）"]


def _boundary_size(lines) -> tuple[float, float]:
    """旗标几何：(从平边到尖端的全长, 旗标高度)。"""
    line_h = FLAG_TEXT * 1.28
    body = max((text_width(value, FLAG_TEXT) for value in lines), default=0.0) + 2.0 * FLAG_PAD_X
    body = max(body, FLAG_MIN_BODY)
    height = line_h * len(lines) + 2.0 * FLAG_PAD_Y
    return body + height * FLAG_TIP_RATIO, height


def _boundary_box(edge_x, y, lines, entry) -> dict:
    """旗标的占位框：供其他标注与管线避让用。"""
    total, height = _boundary_size(lines)
    x1 = edge_x if entry else edge_x - total
    return {"x1": x1 - 0.8, "x2": x1 + total + 0.8,
            "y1": y - height / 2.0 - 0.8, "y2": y + height / 2.0 + 0.8}


def boundary_flag(prims, edge_x, y, lines, entry, layer="PFD_BOUNDARY", weight=None):
    """界区（进出车间）流股旗标：方框 + 尖端，尖端指向介质流动方向。

    ``entry=True`` 是进车间的流股（平边贴图框左边线、尖端朝车间内），
    ``entry=False`` 是出车间的流股（尖端贴图框右边线）；两者尖端都顺着流动方向，
    所以「从哪来、往哪去」在图面上一目了然。返回主线的端点（旗标的尖端 / 平边）。
    """
    weight = sym.W_MAIN_HEAVY if weight is None else weight
    line_h = FLAG_TEXT * 1.28
    total, height = _boundary_size(lines)
    tip = height * FLAG_TIP_RATIO
    if entry:
        edge, tip_x = edge_x, edge_x + total
    else:
        edge, tip_x = edge_x - total, edge_x
    body_right = tip_x - tip
    top = y - height / 2.0
    poly(prims, [(edge, top), (body_right, top), (tip_x, y),
                 (body_right, y + height / 2.0), (edge, y + height / 2.0)],
         closed=True, weight=weight, layer=layer)
    cx = (edge + body_right) / 2.0
    for index, value in enumerate(lines):
        text(prims, cx, top + FLAG_PAD_Y + line_h * (index + 0.5) + FLAG_TEXT * 0.36,
             FLAG_TEXT, value, layer, anchor="middle", weight="bold")


# --- 版面：选择图幅与比例 ---------------------------------------------------
def _layout_rows(ctx):
    equipment = ctx["equipment_rows"]
    streams = ctx["stream_rows"]
    assets = ctx.get("asset_rows") or []
    balance = ctx["balance_rows"]
    return equipment, streams, assets, balance


def _band_heights(ctx):
    equipment, streams, assets, balance = _layout_rows(ctx)
    eq_h = table_height(sym.ROW_EQUIPMENT, len(equipment))
    st_h = table_height(sym.ROW_STREAM, len(streams))
    asset_h = table_height(sym.ROW_STREAM, len(assets)) if assets else 0.0
    return max(eq_h, st_h, asset_h), table_height(sym.ROW_BALANCE, len(balance))


def _main_material_ends(ctx) -> tuple[str, str, str]:
    """主物料的进口端、出口端与进出口合计：说明文字要用的三段，尽量短。"""
    graph = ctx["graph"]
    entries, exits = _terminal_ports(graph)
    if not entries or not exits:
        return "", "", ""
    flow = ctx.get("flow_of_edge") or {}
    main_ids = _main_path(graph, ctx.get("flow_values") or {})
    entry = next((node for node in entries if str(node.get("kind")) == "feed"), entries[0])
    exit_node = next((node for node in exits if str(node.get("kind")) == "product"), None)
    if exit_node is None:
        exit_node = max(exits, key=lambda node: float(node.get("x") or 0.0))

    def edge_of(node, entry_side):
        for edge in graph["edges"]:
            if edge["id"] not in main_ids:
                continue
            if ((entry_side and edge["source"] == node["id"])
                    or (not entry_side and edge["target"] == node["id"])):
                return edge
        return None

    def describe(node, entry_side):
        edge = edge_of(node, entry_side)
        label = ((edge or {}).get("label") or "").strip()
        label = label or str(node.get("label") or node.get("tag") or "物流").strip()
        amount = (flow.get(edge["id"]) if edge else "") or ""
        return (label[:22] + " " + amount).strip()

    balance = ctx.get("balance") or {}
    totals = []
    for key, word in (("global_in", "进口合计"), ("global_out", "出口合计")):
        mass = balance.get(key) or {}
        if mass:
            totals.append(word + " " + "、".join(
                f"{value:g} {unit}" for unit, value in sorted(mass.items())))
    return describe(entry, True), describe(exit_node, False), "，".join(totals)


def _main_material_note(ctx) -> str:
    """主物料来源与去向的一句话说明（排在第 1 张图例图的说明栏里）。"""
    entry, exit_text, totals = _main_material_ends(ctx)
    if not entry and not exit_text:
        return ""
    tail = f"；{totals}" if totals else ""
    return (f"主物料流向：主物料从「{entry}」一侧进入本工段，"
            f"最后从「{exit_text}」一侧离开{tail}。")


def _main_material_row(ctx) -> str:
    """图签栏「主物料流向」一行：进出口各取一段短的，排不下就把合计省掉。"""
    entry, exit_text, totals = _main_material_ends(ctx)
    if not entry and not exit_text:
        return "—"
    row = f"{entry} → {exit_text}"
    if totals and len(row) + len(totals) <= 34:
        row = f"{row}（{totals}）"
    return row


def _notes_lines(ctx):
    return list(ctx["notes"])


def _title_block_rows(ctx, sheet_name, sheet_index, scale_text, format_text):
    balance = ctx["balance"]
    return [
        ("图名", sheet_name),
        ("图册（共 2 张）", sheet_index),
        ("项目", ctx["meta"].get("project") or "未指定项目"),
        ("产品", ctx["meta"].get("product") or "未指定产品"),
        ("版本 / 修订", f'{ctx["meta"].get("version") or "—"} / {ctx["meta"].get("revision") or "—"}'),
        ("数据来源", ctx["meta"].get("source") or "手工绘制或演示草稿"),
        ("主物料流向", _main_material_row(ctx)),
        ("阶段", ctx["stage"]),
        ("比例 / 幅面", f"{scale_text} / {format_text}"),
        ("绘制", "AI for Science 工艺工作区（平台自动生成）"),
        ("审核 / 批准", "待补（需合格工程人员）"),
        ("节点 / 连线", f'{len(ctx["graph"]["nodes"])} / {len(ctx["graph"]["edges"])}'),
        ("物料守恒校核", balance["status"]),
        ("日期", ctx["stamp"] or "—"),
        # 要求19①：顶部的说明框不再画在图面上，免责声明缩成图签栏里的一行，
        # 图面因此让给流程图本身，但图纸上仍保留「非施工图」的声明。
        ("注", "概念级流程图，仅供研发讨论，非施工图或已验证 P&ID。"),
    ]


def _title_block_height(ctx):
    # 行数跟着签名档实际内容走（加了「主物料流向」一行后不再写死 13 行）
    return TITLE_BLOCK_ROW * len(_title_block_rows(ctx, "", "", "", "")) + sym.T_TB_VALUE * 1.2


def _pick_format(ctx, content_h, fixed_h):
    for name, width, height in sym.SHEET_FORMATS:
        usable = height - 2.0 * sym.SHEET_MARGIN
        if usable - fixed_h >= max(DRAWING_MIN_H, content_h):
            return name, width, height
    name, width, height = sym.SHEET_FORMATS[-1]
    return name, width, height


def fit_scale(span_x, span_y, draw_w, band, pitch) -> float:
    """图面缩放系数：把布置缩放到刚好画满图面框，不套用标准比例。

    节点坐标是浏览器画布的排版单位，不是真实设备尺寸，所以图签栏一律标
    “不按比例（NTS）”。系数上限 1:1（不放大小图），并保证相邻设备中心距
    不超过 96 mm，避免两个节点的图被放大到占掉半张纸。
    """
    kx = (draw_w - 2.0 * EDGE_NAME_ROOM) / max(span_x, 1.0)
    ky = (band - 36.0) / max(span_y, 1.0)
    return max(0.02, min(kx, ky, 1.0, 96.0 / max(pitch, 1.0)))


def _centers(graph):
    centres: dict[str, tuple[float, float]] = {}
    for node in graph["nodes"]:
        centres[node["id"]] = (float(node["x"]) + float(node["w"]) / 2.0,
                               float(node["y"]) + float(node["h"]) / 2.0)
    return centres


def _min_pitch(centres):
    values = list(centres.values())
    if len(values) < 2:
        return 240.0
    best = None
    for index, (x1, y1) in enumerate(values):
        for x2, y2 in values[index + 1:]:
            distance = math.hypot(x2 - x1, y2 - y1)
            if best is None or distance < best:
                best = distance
    return best or 240.0


def _catalog_grid(ctx, columns, cell_h):
    rows = [max(1, math.ceil(len(items) / columns)) for _group, items in sym.CATALOG_GROUPS]
    ctx["catalog_rows"] = rows
    return sum(rows) * cell_h + len(sym.CATALOG_GROUPS) * 7.0


def catalog_columns(width):
    usable = width - 2.0
    for columns in (6, 5, 4, 3):
        cell = usable / columns
        if cell >= 46.0:
            return columns, cell
    return 3, usable / 3.0


def _symbol_frame(kind, s):
    """符号在图纸毫米下的外廓：半宽、上沿、下沿（相对符号中心）。"""
    x1, y1, x2, y2 = shape_bounds(sym.symbol_shape(kind, 0.0, 0.0, s))
    return {"hw": max(-x1, x2), "up": -y1, "down": y2, "w": x2 - x1, "h": y2 - y1}


def _symbol_sizes(nodes):
    """每个节点的图纸毫米基准尺寸：符号大小只由类型决定，不随节点框伸缩。"""
    sizes: dict[str, dict] = {}
    for node in nodes:
        base = sym.SYM_EQUIPMENT * sym.symbol_scale(node["kind"])
        sizes[node["id"]] = {"s": base, **_symbol_frame(node["kind"], base)}
    return sizes


def _axis_gaps(centres):
    """布置网格在 x / y 方向上的最小中心距（画布单位）。"""
    xs = sorted({round(value[0], 3) for value in centres.values()})
    ys = sorted({round(value[1], 3) for value in centres.values()})
    dx = min((b - a for a, b in zip(xs, xs[1:]) if b - a > 1e-6), default=0.0)
    dy = min((b - a for a, b in zip(ys, ys[1:]) if b - a > 1e-6), default=0.0)
    return dx, dy


def _material_edges(graph):
    """物料线（公用工程线不计入物料守恒，也不代表物料去向）。"""
    return [edge for edge in graph["edges"] if str(edge.get("media") or "material") != "utility"]


def _terminal_ports(graph):
    """主物料的进料端与出料端：有物料进没有物料出的就是进料端，反之是出料端。"""
    material = _material_edges(graph)
    has_in = {edge["target"] for edge in material}
    has_out = {edge["source"] for edge in material}
    entries = [node for node in graph["nodes"] if node["id"] in has_out and node["id"] not in has_in]
    exits = [node for node in graph["nodes"] if node["id"] in has_in and node["id"] not in has_out]
    return entries, exits


def _main_path(graph, values):
    """主物料线：从进料端沿物料线走到产品出料端，每步取流量最大的支路。

    流量还没填（一律“待补”）时，按“还能走到产品端”的支路走，所以主物料线
    不会误走成塔顶冷凝液或排污支线。返回主物料线上连线 id 的集合。
    """
    entries, exits = _terminal_ports(graph)
    if not entries or not exits:
        return set()
    entry = next((node for node in entries if node.get("kind") == "feed"), entries[0])
    exit_node = next((node for node in exits if node.get("kind") == "product"), None)
    if exit_node is None:
        exit_node = max(exits, key=lambda node: float(node.get("x") or 0.0))
    outgoing: dict[str, list[dict]] = {}
    incoming: dict[str, list[dict]] = {}
    for edge in _material_edges(graph):
        outgoing.setdefault(edge["source"], []).append(edge)
        incoming.setdefault(edge["target"], []).append(edge)
    reachable = {exit_node["id"]}
    queue = [exit_node["id"]]
    while queue:
        for edge in incoming.get(queue.pop(), []):
            if edge["source"] not in reachable:
                reachable.add(edge["source"])
                queue.append(edge["source"])

    def flow_of(edge):
        found = values.get(edge["id"])
        return found[1] if found else -1.0

    path: set[str] = set()
    node_id = entry["id"]
    while node_id != exit_node["id"]:
        options = [edge for edge in outgoing.get(node_id, []) if edge["id"] not in path]
        if not options:
            break
        preferred = [edge for edge in options if edge["target"] in reachable]
        chosen = max(preferred or options, key=flow_of)
        path.add(chosen["id"])
        node_id = chosen["target"]
    return path


_OVERHEAD_WORDS = ("汽相", "气相", "蒸汽", "塔顶", "冷凝", "回流", "不凝气")
_BOTTOMS_WORDS = ("排污", "排渣", "残液", "塔底采出")


def _semantic_port(node, edge):
    """塔类设备按物流名称取接口：塔顶汽相只能从塔顶接口出，塔底排放从塔底接口出。"""
    if not node or str(node.get("kind")) != "column":
        return None
    name = str(edge.get("label") or "")
    if any(word in name for word in _OVERHEAD_WORDS):
        return ("n",)
    if any(word in name for word in _BOTTOMS_WORDS):
        return ("s",)
    return None


# --- 工段图（第 2 张） -----------------------------------------------------
# --- 公用工程总管、安全阀组与仪表信号回路 ------------------------------------
def _utility_key(entry) -> str:
    medium = str(entry.get("medium") or "").strip()
    service = str(entry.get("service") or "").strip()
    return medium or service[:14]


def _utility_media(ctx) -> list[str]:
    """本图要用到哪几种公用工程介质（决定图面上沿要留几条总管）。"""
    seen: list[str] = []
    for node in ctx["graph"]["nodes"]:
        for entry in node.get("utilities") or []:
            key = _utility_key(entry)
            if key and key not in seen:
                seen.append(key)
    order = {key: index for index, key in enumerate(sym.MEDIA_ORDER)}
    return sorted(seen, key=lambda key: (order.get(key, 99), key))


def _utility_records(ctx, placed) -> list[dict]:
    """按介质归并公用工程接管：一种介质一条总管，每个接入点一条支管。"""
    buckets: dict[str, dict] = {}
    for node in ctx["graph"]["nodes"]:
        item = placed.get(node["id"])
        if item is None:
            continue
        for entry in node.get("utilities") or []:
            key = _utility_key(entry)
            if not key:
                continue
            service = str(entry.get("service") or "").strip()
            medium = str(entry.get("medium") or "").strip()
            name = sym.MEDIA_LABELS.get(medium) or medium or service[:14] or "公用工程"
            bucket = buckets.setdefault(key, {"key": key, "name": name, "services": [], "branches": []})
            if service and service not in bucket["services"]:
                bucket["services"].append(service)
            bucket["branches"].append({
                "node": node["id"],
                "kind": str(node.get("kind") or "other"),
                "port": str(entry.get("port") or ""),
                "amount": f'{entry.get("value") or ""}{entry.get("unit") or ""}'.strip(),
                "source": str(entry.get("source") or "").strip(),
                "cx": item["cx"], "cy": item["cy"], "s": item["s"],
            })
    order = {key: index for index, key in enumerate(sym.MEDIA_ORDER)}
    return [buckets[key] for key in sorted(buckets, key=lambda key: (order.get(key, 99), key))]


def _utility_drop(port, lane, obstacles, node_id, bounds, key, offset=0.0):
    """支管从总管竖直落到设备接口；半路压着别的设备就贴着它的外框绕一条通道。

    同一台设备上的第二条接管（例如蒸汽进、冷凝水出）用 offset 再让开几毫米，
    两条支管不会叠成一条线，箭头方向也能分清是进还是出。
    """
    side = (-6.0 if key == "w" else (6.0 if key == "e" else 0.0)) + offset
    lanes = [port[0] + side]
    for box in obstacles:
        lanes.extend((box["x1"] - 3.0, box["x2"] + 3.0))
    lanes = [value for value in lanes if bounds[0] + 1.5 <= value <= bounds[2] - 1.5]
    span = (min(lane, port[1]), max(lane, port[1]))
    free = _lane_free(lanes, span, obstacles, port, node_id)
    chosen = min(free, key=lambda value: abs(value - port[0] - side)) if free else port[0] + side
    if key in ("w", "e"):
        return _dedupe([(chosen, lane), (chosen, port[1]), port])
    if abs(chosen - port[0]) < 0.05:
        return _dedupe([(chosen, lane), port])
    lift = -6.0 if key == "n" else 6.0
    return _dedupe([(chosen, lane), (chosen, port[1] + lift), (port[0], port[1] + lift), port])


def draw_utility_header(prims, records, obstacles, bounds, header_base, occupied, pipe_boxes):
    """公用工程总管：每种介质一条横向总管，从图框左边进线，支管落到设备接口。"""
    left, right = bounds[0], bounds[2]
    for row, record in enumerate(records):
        lane = header_base - row * UTILITY_LANE_STEP
        spec = " / ".join(record["services"])[:36]
        # 规格文字常常以介质名开头（“循环冷却水 上水 32 ℃/回水 40 ℃”），
        # 总管名里已经写了介质，这里去掉重复的那一段。
        if spec.startswith(record["name"]):
            spec = spec[len(record["name"]):].strip(" 　·，,")
        head = "公用工程总管"
        if spec:
            head = f'{head}（{spec}）'
        # 总管从图框左边进车间：端头画界区旗标（尖端指向流动方向），介质名写进旗标，
        # 规格 / 温度跟在旗标后面，一眼看出这条总管是从界区外接进来的。
        flag_x = left + 1.0        # 图框内边线 = 界区线
        flag_total, _flag_h = _boundary_size([record["name"]])
        flag_end = flag_x + flag_total
        line(prims, flag_end, lane, right - 2.0, lane, sym.W_UTILITY, "PFD_UTILITY")
        boundary_flag(prims, flag_x, lane, [record["name"]], True, "PFD_BOUNDARY", sym.W_UTILITY)
        arrow_solid(prims, [(flag_end + 1.0, lane), (flag_end + 7.5, lane)], 2.6, "PFD_ARROW")
        text(prims, flag_end + 9.0, lane - 1.7, UTILITY_TEXT, head, "PFD_UTILITY")
        occupied.append({"x1": flag_x - 0.8, "x2": flag_end + 10.0 + text_width(head, UTILITY_TEXT),
                         "y1": lane - 3.2, "y2": lane + 1.2})
        used: dict[str, int] = {}
        for branch in record["branches"]:
            ports = sym.symbol_ports(branch["kind"], branch["cx"], branch["cy"], branch["s"])
            key = branch["port"] if branch["port"] in ports else ""
            if not key:
                key = next((name for name in ("n", "w", "e", "s") if name in ports), "")
            if not key:
                continue
            port = ports[key]
            offset = 5.0 * used.get(branch["node"], 0)
            used[branch["node"]] = used.get(branch["node"], 0) + 1
            out_flow = str(branch.get("direction") or "") == "out"
            path = _utility_drop(port, lane, obstacles, branch["node"], bounds, key, offset)
            if out_flow:
                path = list(reversed(path))
            poly(prims, path, False, sym.W_UTILITY, "PFD_UTILITY")
            if len(path) > 1:
                arrow_solid(prims, [path[-2], path[-1]], 2.4, "PFD_ARROW")
            drop_x = path[0][0]
            tag = record["name"] + (f' {branch["amount"]}' if branch["amount"] else "")
            if out_flow:
                tag = f'{tag}（回）' 
            box = _text_box(drop_x + 2.2, lane + 2.4, UTILITY_TEXT, tag, "start")
            if not any(_overlap(box, other) for other in occupied):
                text(prims, drop_x + 2.2, lane + 2.4, UTILITY_TEXT, tag, "PFD_UTILITY")
                occupied.append(box)
            for start, end in zip(path, path[1:]):
                pipe_boxes.append({"id": "utility", "x1": min(start[0], end[0]) - 0.9,
                                   "x2": max(start[0], end[0]) + 0.9,
                                   "y1": min(start[1], end[1]) - 0.9,
                                   "y2": max(start[1], end[1]) + 0.9})
            occupied.append({"x1": min(point[0] for point in path) - 1.0,
                             "x2": max(point[0] for point in path) + 1.0,
                             "y1": min(point[1] for point in path) - 1.0,
                             "y2": max(point[1] for point in path) + 1.0})


def draw_reliefs(prims, ctx, placed, occupied, pipe_boxes, jobs):
    """安全阀组 / 放空 / 排污：按位号画在单元的顶部（或底部），并标出泄放去向。"""
    for node in ctx["graph"]["nodes"]:
        item = placed.get(node["id"])
        if item is None:
            continue
        reliefs = [entry for entry in (node.get("reliefs") or []) if isinstance(entry, dict)]
        if not reliefs:
            continue
        kind = str(node.get("kind") or "other")
        ports = sym.symbol_ports(kind, item["cx"], item["cy"], item["s"])
        stack_up = 0.0
        stack_down = 0.0
        for entry in reliefs:
            rk = str(entry.get("kind") or "safety_valve")
            top = rk != "drain"
            port = ports.get("n" if top else "s")
            if port is None:
                continue
            sign = -1.0 if top else 1.0
            offset = stack_up if top else stack_down
            # 接管不再借用塔顶汽相那一条竖线：从封头顶面偏开 4 mm 起，阀组整体右移 9 mm，
            # 塔顶管线与安全阀组各走各的，画面上不会叠成一根。
            start = (port[0] + 4.0, port[1])
            base = port[1] + sign * (offset + 4.0)
            symbol = "valve_safety" if rk in ("safety_valve", "rupture_disc") else "valve_gate"
            centre_x = start[0] + sym.SYM_VALVE + 3.0
            poly(prims, _dedupe([start, (start[0], base), (centre_x - sym.SYM_VALVE, base)]),
                 False, sym.W_UTILITY, "PFD_MINOR")
            draw_shapes(prims, sym.symbol_shape(symbol, 0.0, 0.0, sym.SYM_VALVE),
                        translate(centre_x, base), "PFD_VALVE")
            tip_x = centre_x + sym.SYM_VALVE + 4.0
            tip_y = base + sign * 7.0
            poly(prims, _dedupe([(centre_x + sym.SYM_VALVE, base), (tip_x, base), (tip_x, tip_y)]),
                 False, sym.W_UTILITY, "PFD_MINOR")
            arrow_solid(prims, [(tip_x, base + sign * 2.0), (tip_x, tip_y)], 2.4, "PFD_ARROW")
            name = sym.RELIEF_LABELS.get(rk, "安全阀组")
            tag = str(entry.get("tag") or "").strip() or f'{name}-{node.get("tag") or ""}'.strip("-")
            # 一段标题 + 一段「定压 / 去向」+ 最多两行依据。原先整串挤在一个 46 mm 的
            # 单行里，「去向：放空总管」被截成「去向：放…」，恰好把最要紧的流向截掉了。
            head = clip(f"{tag}\u3000{name}", UTILITY_TEXT, 42.0)
            duty = "\u3000".join(value for value in (
                f'定压 {entry.get("value")}{entry.get("unit") or ""}'.strip() if entry.get("value") else "",
                f'去向：{entry.get("to")}'.strip() if entry.get("to") else "",
            ) if value) or "定压与去向待补"
            lines = [head, clip(duty, UTILITY_TEXT, 46.0)]
            basis = str(entry.get("source") or "").strip()
            if basis:
                # 依据行只留一行、超出用「…」收尾：按宽度硬换行会把「PSV-101」切成两半，
                # 图上反而更难读；完整依据在右侧检查器和图例的表格里。
                lines.append(clip(f"依据：{basis}", UTILITY_TEXT, 46.0))
            lead = -UTILITY_TEXT * 1.78 if top else UTILITY_TEXT * 1.78
            # 文字不当场落笔：先记下来，等物流标注与阀门位号排完再用避让器找空位，
            # 否则安全阀的文字会被后来的 S 编号压住。候选位置放宽到图面右侧的空白带。
            jobs.append((tip_x + 1.6, tip_y + sign * 1.4 - 1.0, lines, lead,
                         ((0.0, 0.0), (0.0, -11.0), (0.0, 11.0), (0.0, -22.0), (0.0, 22.0),
                          (0.0, -33.0), (0.0, 33.0), (0.0, -44.0), (0.0, 44.0),
                          (18.0, 0.0), (18.0, -11.0), (18.0, 11.0), (18.0, -22.0), (18.0, 22.0),
                          (36.0, 0.0), (36.0, -11.0), (36.0, 11.0), (36.0, -22.0), (36.0, 22.0),
                          (54.0, 0.0), (54.0, 11.0), (54.0, -11.0), (54.0, 22.0), (54.0, -22.0),
                          (-18.0, 0.0), (-18.0, 11.0), (-18.0, -11.0), (72.0, 0.0), (72.0, 11.0),
                          (72.0, -11.0), (90.0, 0.0), (90.0, 11.0), (90.0, -11.0))))
            pipe_boxes.append({"id": "relief", "x1": start[0] - sym.SYM_VALVE - 1.2,
                               "x2": tip_x + 1.2, "y1": min(base, tip_y) - 1.2,
                               "y2": max(base, tip_y) + 1.2})
            occupied.append({"x1": start[0] - sym.SYM_VALVE - 1.2, "x2": tip_x + 1.2,
                             "y1": min(base, tip_y) - 1.2, "y2": max(base, tip_y) + 1.2})
            # 同一台设备上的第二只安全阀/放空阀整组再错开 20 mm，不叠在第一只上
            if top:
                stack_up = offset + 20.0
            else:
                stack_down = offset + 20.0


def draw_signal_lines(prims, ctx, instrument_marks, valve_marks, occupied):
    """仪表信号回路：由仪表气泡引到对应的执行机构（调节阀），线端带小圆点与信号形式。"""
    if not instrument_marks:
        return
    valve_by_tag = {tag: point for tag, point in valve_marks if tag}
    spare: dict[str, tuple[str, tuple[float, float]]] = {}
    for tag, point in valve_marks:
        if tag:
            spare.setdefault(tag.split("-")[-1], (tag, point))
    explicit: dict[str, tuple[str, str]] = {}
    for edge in ctx["graph"]["edges"]:
        for signal in edge.get("signals") or []:
            tag = str(signal.get("tag") or "").strip()
            if tag:
                explicit[tag] = (str(signal.get("target") or "").strip(), str(signal.get("kind") or ""))
    drawn: set[str] = set()
    for tag, centre in instrument_marks:
        if not tag or tag in drawn:
            continue
        drawn.add(tag)
        target, kind = explicit.get(tag, ("", ""))
        point = valve_by_tag.get(target) if target else None
        if point is None and not target:
            found = spare.get(tag.split("-")[-1])
            point = found[1] if found else None
        if point is None:
            continue
        # 起点不再点一只小圆：仪表端本来就是气泡，圆点叠在气泡里会看成「双气泡」；
        # 引线也从圆周引出，不从圆心插出去，气泡里保持干净。
        span_x, span_y = point[0] - centre[0], point[1] - centre[1]
        span = math.hypot(span_x, span_y) or 1.0
        start = (centre[0] + span_x / span * sym.SYM_INSTRUMENT,
                 centre[1] + span_y / span * sym.SYM_INSTRUMENT)
        line(prims, start[0], start[1], point[0], point[1], sym.W_THIN, "PFD_SIGNAL")
        arrow_solid(prims, [(point[0] - (point[0] - centre[0]) * 0.22,
                             point[1] - (point[1] - centre[1]) * 0.22), point], 2.2, "PFD_ARROW")
        mid = ((centre[0] + point[0]) / 2.0, (centre[1] + point[1]) / 2.0)
        if kind == "electric":
            line(prims, mid[0] - 1.1, mid[1] + 1.1, mid[0] + 1.1, mid[1] - 1.1, sym.W_THIN, "PFD_SIGNAL")
        name = {"electric": "电信号", "pneumatic": "气信号", "interlock": "联锁"}.get(kind, "")
        if not name:
            continue
        box = _text_box(mid[0] + 1.8, mid[1] - 1.2, UTILITY_TEXT, name, "start")
        if not any(_overlap(box, other) for other in occupied):
            text(prims, mid[0] + 1.8, mid[1] - 1.2, UTILITY_TEXT, name, "PFD_SIGNAL")
            occupied.append(box)


def compose_section(ctx):
    prims: list[dict] = []
    graph = ctx["graph"]
    nodes = graph["nodes"]
    meta = ctx["meta"]
    equipment, streams, assets, balance_rows = _layout_rows(ctx)
    # 图面顶部的说明框整条撤掉：第 2 张只留图面与右下角签名档，
    # 说明性文字（含主物料流向）统一排到第 1 张图例图与签名档里。
    notes = _notes_lines(ctx)
    warn_lines = ctx.get("warning_lines") or []
    centres = _centers(graph)
    pitch = _min_pitch(centres)
    # 第 2 张只留图面与右下角图签栏：设备一览表、物流表、管道附件表与物料守恒
    # 校核表都不再画（数据由图上标注与图签栏承载），图面因此拿到整张纸的篇幅。
    tables_h = 0.0
    balance_h = 0.0
    tb_h = _title_block_height(ctx)
    warn_h = warning_height(len(warn_lines)) if warn_lines else 0.0
    notes_h = notes_height(len(notes)) if notes else 0.0

    xs = [value[0] for value in centres.values()] or [0.0]
    ys = [value[1] for value in centres.values()] or [0.0]
    span_x = max(xs) - min(xs)
    span_y = max(ys) - min(ys)

    # 符号是固定图纸尺寸、行距随图面缩放：行距必须先放得下最高的符号（塔），
    # 否则主设备会压到下一行的设备上。
    sizes = _symbol_sizes(nodes)
    tall = max((item["h"] for item in sizes.values()), default=0.0)
    wide = max((item["w"] for item in sizes.values()), default=0.0)
    pitch_x, pitch_y = _axis_gaps(centres)
    k_floor = (wide + 12.0) / pitch_x if pitch_x > 1e-6 else 0.0
    kv_floor = (tall + 12.0) / pitch_y if pitch_y > 1e-6 else 0.0

    # 图面的实际高度 = 首末两排中心距 + 首排符号的上外廓 + 末排符号的下外廓 + 标注，
    # 只按行距算会把最高的塔顶出图面上沿（管线也跟着跑到图面外）。
    rows: dict[float, dict] = {}
    for node in nodes:
        entry = rows.setdefault(round(centres[node["id"]][1], 3), {"up": 0.0, "down": 0.0})
        entry["up"] = max(entry["up"], sizes[node["id"]]["up"])
        entry["down"] = max(entry["down"], sizes[node["id"]]["down"])
    row_keys = sorted(rows)
    # 图面上沿除了走线空间，还要留给「公用工程总管」与「安全阀组/放空阀」：
    # 不留高度它们会顶到图框上边线外，或者压住塔顶的汽相管线。
    media_keys = _utility_media(ctx)
    utility_room = UTILITY_LANE_STEP * len(media_keys) + 12.0 if media_keys else 0.0
    relief_room = RELIEF_ROOM if any(node.get("reliefs") for node in nodes) else 0.0
    top_room = (rows[row_keys[0]]["up"] if row_keys else 0.0) + ROUTE_ROOM + relief_room + utility_room
    bottom_down = rows[row_keys[-1]]["down"] if row_keys else 0.0
    extent = top_room + bottom_down + CAPTION_ROOM

    def plan(sheet_w, sheet_h):
        usable = sheet_h - 2.0 * sym.SHEET_MARGIN
        wid = sheet_w - sym.SHEET_MARGIN_LEFT - sym.SHEET_MARGIN
        y0 = sym.SHEET_MARGIN + HEADER_H + GAP + notes_h + GAP
        bottom = max(tb_h, balance_h)
        tail = tables_h + GAP + bottom + GAP + (warn_h + GAP if warn_h else 0.0)
        band = usable - (y0 - sym.SHEET_MARGIN) - tail
        return wid, y0, band

    chosen = None
    # 工段图从 A3 起排：A4 放不开单元操作符号、位号与进出口标注，标准做法也是 A3 及以上。
    for name, sheet_w, sheet_h in sym.SHEET_FORMATS[1:]:
        draw_w, draw_y, band = plan(sheet_w, sheet_h)
        if band < DRAWING_MIN_H:
            continue
        k_fit = max(fit_scale(span_x, span_y, draw_w, band, pitch), k_floor)
        kv_fit = k_fit
        if span_y > 1e-6:
            room_y = (band - 2.0 * DRAWING_EDGE - extent) / span_y
            kv_fit = max(k_fit, min(room_y, k_fit * VERTICAL_FILL_MAX))
        kv_used = max(kv_fit, kv_floor)
        content_h = span_y * kv_used + extent
        if content_h <= band:
            chosen = (name, sheet_w, sheet_h, draw_w, draw_y,
                      max(content_h, DRAWING_MIN_H), k_fit, NTS, kv_used)
            break
    if chosen is None:
        name, sheet_w, sheet_h = sym.SHEET_FORMATS[-1]
        draw_w, draw_y, band = plan(sheet_w, sheet_h)
        k_fit = max(fit_scale(span_x, span_y, draw_w, band, pitch), k_floor)
        chosen = (name, sheet_w, sheet_h, draw_w, draw_y, max(band, DRAWING_MIN_H),
                  k_fit, NTS, max(k_fit, kv_floor))
    fmt_name, sheet_w, sheet_h, draw_w, draw_y, draw_h, k, scale_text, kv = chosen
    draw_x = sym.SHEET_MARGIN_LEFT
    # 行距在纵向摊开填满图面（符号尺寸不随行距变化，摊开的只是位置）；
    # 摊开的幅度已在选图幅时算好，保证放得下最高的设备符号。
    ox = draw_x + (draw_w - span_x * k) / 2.0 - min(xs) * k
    oy = draw_y + (band - (span_y * kv + extent)) / 2.0 + top_room - min(ys) * kv
    draw_h = band

    table_y = draw_y + draw_h + GAP

    def X(value):
        return ox + float(value) * k

    def Y(value):
        return oy + float(value) * kv

    by_id = {node["id"]: node for node in nodes}
    placed: dict[str, dict] = {}
    for node in nodes:
        cx, cy = centres[node["id"]]
        placed[node["id"]] = {"cx": X(cx), "cy": Y(cy), "kind": node["kind"],
                              "s": sizes[node["id"]]["s"]}

    # 设备占位框（含符号实际外廓），管线据此避让
    boxes = []
    for node_id, item in placed.items():
        size = sizes[node_id]
        boxes.append({"id": node_id, "x1": item["cx"] - size["hw"] - 3.0,
                      "x2": item["cx"] + size["hw"] + 3.0,
                      "y1": item["cy"] - size["up"] - 3.0,
                      "y2": item["cy"] + size["down"] + 3.0})
    bounds = (draw_x + 2.0, draw_y + 2.0, draw_x + draw_w - 2.0, draw_y + draw_h - 2.0)
    pipe_boxes: list[dict] = []
    label_jobs: list[tuple[int, dict, list]] = []
    tag_jobs: list[tuple] = []
    # 标注避让：符号本体、设备位号与名称、阀门位号、仪表、已排好的物流标注都不允许被压住。
    # 这里用逐条文字的实际占位框（而不是整块设备框），否则图面会被大框占满，
    # 标注找不到位置就又压回设备上。
    occupied: list[dict] = []
    for node in nodes:
        item = placed[node["id"]]
        size = sizes[node["id"]]
        occupied.append({"x1": item["cx"] - size["hw"], "x2": item["cx"] + size["hw"],
                         "y1": item["cy"] - size["up"], "y2": item["cy"] + size["down"]})
        node_tag = (node.get("tag") or "").strip()
        occupied.append(_text_box(item["cx"], item["cy"] + size["down"] + 5.6, sym.T_TAG,
                                  node_tag or "　", "middle"))
        if node_tag and (node.get("label") or ""):
            occupied.append(_text_box(item["cx"], item["cy"] + size["down"] + 9.6, sym.T_NAME,
                                      clip(node.get("label") or "", sym.T_NAME, 58.0), "middle"))

    # 管线：终止于接口点；公用工程等辅助管线画细实线（主物料线的 1/3 宽）；阀门所占线段断开
    flow_of_edge = ctx.get("flow_of_edge") or {}
    # 主物料线（由进料端贯通到产品端）单独加粗：一眼看出物料从哪来、最后到哪去
    main_ids = _main_path(graph, ctx.get("flow_values") or {})
    # 主物料的来源与去向：进出料端直接标在图面边缘
    entries, exits = _terminal_ports(graph)
    balance_info = ctx.get("balance") or {}

    def corridor_free(node_id, start, end, y):
        probe = {"x1": min(start, end) - 1.5, "x2": max(start, end) + 1.5,
                 "y1": y - 2.5, "y2": y + 2.5}
        return not any(_overlap(probe, box) for box in boxes if box["id"] != node_id)

    def terminal(node, entry, compact=False):
        item = placed.get(node["id"])
        if item is None:
            return
        kind = str(node.get("kind") or "other")
        size = sizes[node["id"]]
        ports = sym.symbol_ports(kind, item["cx"], item["cy"], size["s"])
        anchor_port = ports.get("w" if entry else "e")
        if anchor_port is None:
            return
        y = item["cy"]
        # 主物料进出车间：主线一律顶到图框的左右边线（左进右出），端头画设计院通用的
        # 「界区旗标」（方框 + 尖端，尖端顺着流动方向，框内写流股名称与来源 / 去向）。
        # 中间被别的设备挡住时走一个直角弯绕过去，不为了避让就把线缩回设备旁边。
        # 界区线取图框内边线（外框线往里 3 mm）：旗标的平边 / 尖端正好落在界区线上
        left_edge, right_edge = draw_x + 3.0, draw_x + draw_w - 3.0
        edge_x = left_edge if entry else right_edge
        head_lines = _boundary_lines(kind, entry, compact)
        total, flag_h = _boundary_size(head_lines)
        stub = edge_x + total if entry else edge_x - total
        level = y
        if not corridor_free(node["id"], min(stub, anchor_port[0]), max(stub, anchor_port[0]), y):
            probe = {"x1": min(stub, anchor_port[0]) - 1.0, "x2": max(stub, anchor_port[0]) + 1.0,
                     "y1": y - 2.5, "y2": y + 2.5}
            detours: list[float] = []
            for box in boxes:
                if box["id"] == node["id"] or not _overlap(probe, box):
                    continue
                detours.extend((box["y1"] - 5.0, box["y2"] + 5.0))
            detours = [value for value in detours if draw_y + 3.0 <= value <= draw_y + draw_h - 3.0]
            if detours:
                level = min(detours, key=lambda value: abs(value - y))
        # 旗标不能压住设备本体或别的标注：原位放不下就上下挪一档，挪不动就留在原位
        # （宁可旗标贴得近一点，也不把「从哪来、往哪去」丢掉）。
        for candidate in (level, level - (flag_h + 3.0), level + (flag_h + 3.0)):
            if candidate - flag_h / 2.0 < draw_y + 1.5 or candidate + flag_h / 2.0 > draw_y + draw_h - 1.5:
                continue
            flag_box = _boundary_box(edge_x, candidate, head_lines, entry)
            if any(_overlap(flag_box, other) for other in boxes):
                continue
            if any(_overlap(flag_box, other) for other in occupied):
                continue
            level = candidate
            break
        if abs(level - y) < 0.05:
            line(prims, stub, y, anchor_port[0], y, sym.W_MAIN_HEAVY, "PFD_MAIN")
            arrow_solid(prims, [(stub, y), anchor_port] if entry else [anchor_port, (stub, y)],
                        3.2, "PFD_ARROW")
        else:
            # 竖着走到接口同一高度，再横着进/出接口：侧接口不能被竖管从上往下戳。
            lane = anchor_port[0] - 6.0 if entry else anchor_port[0] + 6.0
            path = [(stub, level), (lane, level), (lane, y), anchor_port]
            if not entry:
                path = list(reversed(path))
            for index in range(len(path) - 1):
                line(prims, path[index][0], path[index][1],
                     path[index + 1][0], path[index + 1][1], sym.W_MAIN_HEAVY, "PFD_MAIN")
            arrow_solid(prims, path, 3.2, "PFD_ARROW")
        # 端头旗标：进出车间的流股方向一眼可辨
        boundary_flag(prims, edge_x, level, head_lines, entry)
        occupied.append(_boundary_box(edge_x, level, head_lines, entry))

    if entries and exits:
        terminal(next((node for node in entries if node.get("kind") == "feed"), entries[0]), True)
        product = next((node for node in exits if node.get("kind") == "product"), None)
        if product is None:
            product = max(exits, key=lambda node: float(node.get("x") or 0.0))
        terminal(product, False)
        for node in exits:
            if node["id"] != product["id"]:
                terminal(node, False, compact=True)

    losses = ctx.get("losses") or {}
    gaps = ctx.get("node_gaps") or {}
    # 仪表气泡与执行机构的位置先记下来，全部管线画完之后再连信号回路：
    # 一边画管线一边连信号线会被后画的管线横穿。
    instrument_marks: list[tuple[str, tuple[float, float]]] = []
    valve_marks: list[tuple[str, tuple[float, float]]] = []
    for index, edge in enumerate(graph["edges"], 1):
        source = placed.get(edge["source"])
        target = placed.get(edge["target"])
        if not source or not target:
            continue
        src_ports = sym.symbol_ports(source["kind"], source["cx"], source["cy"], source["s"])
        tgt_ports = sym.symbol_ports(target["kind"], target["cx"], target["cy"], target["s"])
        src_only = _semantic_port(by_id.get(edge["source"]), edge)
        tgt_only = _semantic_port(by_id.get(edge["target"]), edge)
        obstacles = [box for box in boxes if box["id"] not in (edge["source"], edge["target"])]
        # 起点 / 终点设备的本体（不含与相邻设备之间预留的 3 mm 间隙）同样参与避让：
        # 早先把两端设备整个排除在障碍之外，回流线就从泵出来绕到塔的中心线，
        # 再竖直向上从塔身里穿过去接塔顶接口。
        end_boxes: dict[str, dict] = {}
        for node_id in (edge["source"], edge["target"]):
            item = placed.get(node_id)
            if item is None or node_id in end_boxes:
                continue
            size = sizes[node_id]
            end_boxes[node_id] = {"id": node_id, "x1": item["cx"] - size["hw"],
                                  "x2": item["cx"] + size["hw"],
                                  "y1": item["cy"] - size["up"],
                                  "y2": item["cy"] + size["down"]}
        obstacles = obstacles + list(end_boxes.values())
        leave = (end_boxes.get(edge["source"]), end_boxes.get(edge["target"]))
        if src_only == ("n",) and "n" in tgt_ports:
            # 塔顶汽相：从塔顶接口向上出线，在图面上方横走，再下进冷凝器顶部。
            # 交给通用走线会先试「出口正下方」这条路，结果从塔身里穿过去。
            start, end = src_ports["n"], tgt_ports["n"]
            level = min(start[1], end[1]) - ROUTE_ROOM + 2.0
            # 竖直下行的那一段要自己挑通道：直接落在目标正上方会压住中间的设备
            # （例如冷凝器正好在结晶釜下方时，管会从结晶釜里穿过去）。
            lanes = [end[0]]
            for box in obstacles:
                lanes.append(box["x1"] - 3.0)
                lanes.append(box["x2"] + 3.0)
            span = (min(level, end[1]), max(level, end[1]))
            free = _lane_free(lanes, span, obstacles, end, edge["target"])
            lane = min(free, key=lambda value: abs(value - end[0])) if free else end[0]
            points = _dedupe([start, (start[0], level), (lane, level), (lane, end[1]), end])
        elif tgt_only == ("n",) and "n" in tgt_ports and src_ports:
            # 进塔顶接口（回流线）：来源在本行下方时不能直着往上顶——那条竖线会从塔身
            # 里穿过去。先抬到塔顶接口之上，在图面上方横走，最后从上往下接进塔顶。
            end = tgt_ports["n"]
            start = src_ports[_pick_ports(source, target, src_ports, {"n": end})[0]]
            if start[1] > end[1] + 4.0:
                base = min(start[1], end[1]) - ROUTE_ROOM + 2.0
                lanes = [start[0], end[0]]
                for box in obstacles:
                    lanes.append(box["x1"] - 3.0)
                    lanes.append(box["x2"] + 3.0)
                span = (min(base, start[1]), max(base, start[1]))
                free = _lane_free(lanes, span, obstacles, end, edge["target"])
                lane = min(free, key=lambda value: abs(value - start[0])) if free else start[0]
                # 塔顶汽相线也在同一个高度上横走：两条线要错开几毫米平行走，
                # 否则叠成一条粗线，图上分不出哪条是回流、哪条是塔顶汽相。
                level = base
                for shift in (0.0, 5.0, 10.0, -5.0, 15.0):
                    candidate = base + shift
                    if candidate > end[1] - 3.0:
                        continue
                    band = {"x1": min(lane, end[0]) - 1.6, "x2": max(lane, end[0]) + 1.6,
                            "y1": candidate - 1.6, "y2": candidate + 1.6}
                    if not any(_overlap(band, other) for other in pipe_boxes):
                        level = candidate
                        break
                points = _dedupe([start, (lane, start[1]), (lane, level), (end[0], level), end])
            else:
                if src_only:
                    src_ports = {key: value for key, value in src_ports.items() if key in src_only}
                points = route_ports(source, target, src_ports, {"n": end}, obstacles, bounds, leave)
        else:
            if src_only:
                src_ports = {key: value for key, value in src_ports.items() if key in src_only}
            if tgt_only:
                tgt_ports = {key: value for key, value in tgt_ports.items() if key in tgt_only}
            points = route_ports(source, target, src_ports, tgt_ports, obstacles, bounds, leave)
        utility = str(edge.get("media") or "material") == "utility"
        if utility:
            weight, layer = sym.W_UTILITY, "PFD_UTILITY"
        elif edge["id"] in main_ids:
            # 主物料线走「1-主物料线」层，其余工艺物流走「2-次物料线」层
            weight, layer = sym.W_MAIN_HEAVY, "PFD_MAIN"
        else:
            weight, layer = sym.W_MAIN, "PFD_SECONDARY"
        total = polyline_length(points)

        components = [item for item in (edge.get("components") or []) if item.get("kind") in sym.VALVE_LABELS]
        cuts: list[tuple[float, float]] = []
        placements: list[tuple[float, dict]] = []
        for position, component in enumerate(components):
            at = total * (0.30 + 0.46 * (position + 1) / (len(components) + 1))
            cuts.append((at - sym.SYM_VALVE * 1.35, at + sym.SYM_VALVE * 1.35))
            placements.append((at, component))
        for part in split_polyline(points, cuts):
            poly(prims, part, False, weight, layer)
        longest = max(split_polyline(points, cuts) or [points], key=polyline_length)
        arrow_solid(prims, longest, 3.0, "PFD_ARROW")

        for position, (at, component) in enumerate(placements):
            point, direction = point_at(points, at)
            map_point = rotate_map(point[0], point[1], direction)
            draw_shapes(prims, sym.symbol_shape(component["kind"], 0.0, 0.0, sym.SYM_VALVE), map_point, "PFD_VALVE")
            occupied.append({"x1": point[0] - sym.SYM_VALVE * 1.7, "x2": point[0] + sym.SYM_VALVE * 1.7,
                             "y1": point[1] - sym.SYM_VALVE * 1.7, "y2": point[1] + sym.SYM_VALVE * 1.7})
            tag = (component.get("tag") or "").strip() or sym.VALVE_LABELS[component["kind"]]
            valve_marks.append((tag, (point[0], point[1])))
            label = clip(tag, sym.T_TAG, 22.0)
            if abs(direction[0]) >= abs(direction[1]):
                tag_x, tag_y, tag_anchor = point[0], point[1] + 6.0 + (position % 2) * 4.2, "middle"
                tag_shifts = ((0.0, 0.0), (-6.0, 0.0), (6.0, 0.0), (0.0, 4.4), (0.0, -4.4),
                              (-12.0, 0.0), (12.0, 0.0), (0.0, 8.8), (0.0, -8.8), (-18.0, 0.0),
                              (18.0, 0.0), (0.0, 13.2), (0.0, -13.2), (-24.0, 0.0), (24.0, 0.0),
                              (-30.0, 0.0), (30.0, 0.0), (0.0, 17.6), (0.0, -17.6), (-36.0, 0.0),
                              (36.0, 0.0), (-24.0, 8.8), (24.0, 8.8), (-24.0, -8.8), (24.0, -8.8),
                              (0.0, 22.0), (0.0, -22.0), (-42.0, 0.0), (42.0, 0.0))
            else:
                tag_x, tag_y, tag_anchor = point[0] + 4.4, point[1] - 0.6, "start"
                tag_shifts = ((0.0, 0.0), (0.0, 4.2), (0.0, -4.2), (5.2, 0.0), (5.2, 4.2),
                              (5.2, -4.2), (10.4, 0.0), (-5.2, 0.0), (-10.4, 0.0), (10.4, 4.2),
                              (10.4, -4.2), (0.0, 8.4), (0.0, -8.4), (15.6, 0.0), (-15.6, 0.0),
                              (15.6, 4.2), (15.6, -4.2), (0.0, 12.6), (0.0, -12.6), (20.8, 0.0),
                              (-20.8, 0.0), (20.8, 4.2), (20.8, -4.2), (26.0, 0.0), (-26.0, 0.0),
                              (0.0, 16.8), (0.0, -16.8), (31.2, 0.0), (-31.2, 0.0))
            tag_jobs.append((tag_x, tag_y, tag_anchor, label, tag_shifts))

        for position, tag in enumerate(edge.get("instruments") or []):
            at = total * (0.72 + 0.10 * position)
            point, direction = point_at(points, at)
            kind = sym.instrument_kind(tag)
            bubble = sym.SYM_INSTRUMENT
            if abs(direction[0]) >= abs(direction[1]):
                centre = (point[0], point[1] - bubble - 4.0)
                bubble_shifts = ((0.0, 0.0), (0.0, -8.0), (0.0, 8.0), (0.0, -16.0), (0.0, 16.0))
            else:
                centre = (point[0] + bubble + 4.0, point[1])
                bubble_shifts = ((0.0, 0.0), (8.0, 0.0), (-8.0, 0.0), (16.0, 0.0), (-16.0, 0.0))
            for shift_x, shift_y in bubble_shifts:
                probe = {"x1": centre[0] + shift_x - bubble, "x2": centre[0] + shift_x + bubble,
                         "y1": centre[1] + shift_y - bubble, "y2": centre[1] + shift_y + bubble}
                if not any(_overlap(probe, other) for other in occupied):
                    centre = (centre[0] + shift_x, centre[1] + shift_y)
                    break
            # 引线止于气泡圆周：原来直插圆心，横穿下半格的回路号，放大看就是一团墨。
            span_x, span_y = centre[0] - point[0], centre[1] - point[1]
            span = math.hypot(span_x, span_y) or 1.0
            line(prims, point[0], point[1],
                 centre[0] - span_x / span * bubble, centre[1] - span_y / span * bubble,
                 sym.W_THIN, "PFD_SIGNAL")
            draw_shapes(prims, sym.symbol_shape(kind, 0.0, 0.0, bubble), translate(centre[0], centre[1]),
                        "PFD_INSTRUMENT")
            # ISA 两分格：功能字母在上半格、回路号在下半格；原来一组 3 个字母
            # 正压在中间那条横线上，放大看是一团墨，也读不出回路号。
            raw_tag = str(tag).strip()
            head = "".join(char for char in raw_tag.split("-")[0] if char.isalpha())[:3].upper() or "I"
            loop = "".join(char for char in raw_tag.rsplit("-", 1)[-1] if char.isdigit()) if "-" in raw_tag else ""
            if kind == "instrument_interlock":
                text(prims, centre[0], centre[1] + sym.T_SYMBOL * 0.30, sym.T_SYMBOL * 0.66,
                     head, "PFD_INSTRUMENT", anchor="middle")
            elif loop:
                text(prims, centre[0], centre[1] - bubble * 0.24, sym.T_SYMBOL * 0.80,
                     head, "PFD_INSTRUMENT", anchor="middle")
                text(prims, centre[0], centre[1] + bubble * 0.70, sym.T_SYMBOL * 0.74,
                     loop, "PFD_INSTRUMENT", anchor="middle")
            else:
                text(prims, centre[0], centre[1] + sym.T_SYMBOL * 0.30, sym.T_SYMBOL * 0.80,
                     head, "PFD_INSTRUMENT", anchor="middle")
            occupied.append({"x1": centre[0] - bubble, "x2": centre[0] + bubble,
                             "y1": centre[1] - bubble, "y2": centre[1] + bubble})
            instrument_marks.append((str(tag).strip(), (centre[0], centre[1])))

        # 管线占位：标注要避开管线本体，等全部管线排完再统一放标注
        for a, b in zip(points, points[1:]):
            pipe_boxes.append({"id": edge["id"], "x1": min(a[0], b[0]) - 0.9, "x2": max(a[0], b[0]) + 0.9,
                               "y1": min(a[1], b[1]) - 0.9, "y2": max(a[1], b[1]) + 0.9})
        label_jobs.append((index, edge, points))

    # 公用工程总管与支管、安全阀组与放空排污、仪表信号回路：
    # 都排在物流标注之前，标注会自动避开它们。
    records = _utility_records(ctx, placed)
    if records:
        top_edge = min((item["cy"] - sizes[node_id]["up"] for node_id, item in placed.items()),
                       default=draw_y + draw_h)
        header_base = max(top_edge - ROUTE_ROOM - relief_room - 6.0, draw_y + 4.0 + UTILITY_LANE_STEP * len(records))
        draw_utility_header(prims, records, boxes, bounds, header_base, occupied, pipe_boxes)
    relief_jobs: list[tuple[float, float, str, tuple]] = []
    draw_reliefs(prims, ctx, placed, occupied, pipe_boxes, relief_jobs)
    draw_signal_lines(prims, ctx, instrument_marks, valve_marks, occupied)

    # 物流标注：编号 + 名称与流量排在管线上方两行，把管线下方留给阀门位号。
    # 全部管线画完之后再放标注，否则后画的管线会横穿先放好的文字。
    for index, edge, points in label_jobs:
        segments = [(polyline_length([a, b]), a, b) for a, b in zip(points, points[1:])]
        seg_len, seg_a, seg_b = max(segments, key=lambda item: item[0]) if segments else (0.0, points[0], points[0])
        horizontal = abs(seg_b[0] - seg_a[0]) >= abs(seg_b[1] - seg_a[1])
        room = min(MAX_STREAM_LABEL_W, seg_len - 10.0)
        if room < MIN_STREAM_LABEL_ROOM:
            continue
        name = clip(f'S{index:02d}　{edge.get("label") or "物流"}', sym.T_FLOW, room)
        flow = clip(flow_of_edge.get(edge["id"], ""), sym.T_FLOW, room)
        text_w = max(text_width(name, sym.T_FLOW), text_width(flow, sym.T_FLOW))
        others = [box for box in pipe_boxes if box["id"] != edge["id"]]
        label_obstacles = occupied + others
        if horizontal:
            anchor_point = _label_spot(seg_a, seg_b, label_obstacles, text_w, 10.0)
        else:
            anchor_point = _label_spot_vertical(seg_a, seg_b, label_obstacles, text_w)
        if horizontal:
            text(prims, anchor_point[0], anchor_point[1] - 5.4, sym.T_FLOW, name, "PFD_FLOW", anchor="middle")
            text(prims, anchor_point[0], anchor_point[1] - 1.4, sym.T_FLOW, flow, "PFD_FLOW", anchor="middle")
            box = {"x1": anchor_point[0] - text_w / 2.0, "x2": anchor_point[0] + text_w / 2.0,
                   "y1": anchor_point[1] - 7.0, "y2": anchor_point[1] + 1.0}
        else:
            text(prims, anchor_point[0] + 2.4, anchor_point[1] - 4.4, sym.T_FLOW, name, "PFD_FLOW")
            text(prims, anchor_point[0] + 2.4, anchor_point[1] - 0.4, sym.T_FLOW, flow, "PFD_FLOW")
            box = {"x1": anchor_point[0] + 1.8, "x2": anchor_point[0] + 1.8 + text_w,
                   "y1": anchor_point[1] - 7.5, "y2": anchor_point[1] + 1.0}
        occupied.append(box)

    # 阀门位号最后放：位号文字小、可挪动，让它去避让已经排好的物流标注
    for tag_x, tag_y, tag_anchor, label, shifts in tag_jobs:
        tag_x, tag_y, tag_box = _free_text_spot(tag_x, tag_y, sym.T_TAG, label, occupied,
                                               tag_anchor, shifts)
        text(prims, tag_x, tag_y, sym.T_TAG, label, "PFD_TAG", anchor=tag_anchor)
        occupied.append(tag_box)

    # 安全阀组 / 放空 / 排污的文字：整块（名称 / 定压与去向 / 依据）一起找空位。
    # 块内相对位置固定，块整体挪动；「去向」一行不截断。实在找不到空位就留在原位，
    # 宁可叠字也不把泄放去向丢掉。
    for text_x, text_y, lines, lead, shifts in relief_jobs:
        block_w = max(text_width(value, UTILITY_TEXT) for value in lines)
        base_x = max(draw_x + 2.0, min(text_x, draw_x + draw_w - 2.0 - block_w))
        chosen = None
        for shift_x, shift_y in shifts:
            px, py = base_x + shift_x, text_y + shift_y
            last = py + lead * (len(lines) - 1)
            box = {"x1": px - 0.6, "x2": px + block_w + 0.6,
                   "y1": min(py, last) - UTILITY_TEXT * 1.05,
                   "y2": max(py, last) + UTILITY_TEXT * 0.45}
            if box["x1"] < draw_x + 0.5 or box["x2"] > draw_x + draw_w - 0.5:
                continue
            if box["y1"] < draw_y + 0.5 or box["y2"] > draw_y + draw_h - 0.5:
                continue
            if any(_overlap(box, other) for other in occupied):
                continue
            chosen = (px, py, box)
            break
        if chosen is None:
            last = text_y + lead * (len(lines) - 1)
            chosen = (base_x, text_y,
                      {"x1": base_x - 0.6, "x2": base_x + block_w + 0.6,
                       "y1": min(text_y, last) - UTILITY_TEXT * 1.05,
                       "y2": max(text_y, last) + UTILITY_TEXT * 0.45})
        px, py, box = chosen
        for index, value in enumerate(lines):
            text(prims, px, py + lead * index, UTILITY_TEXT, value, "PFD_MINOR")
        occupied.append(box)

    # 设备符号与位号
    for node in nodes:
        item = placed[node["id"]]
        draw_shapes(prims, sym.symbol_shape(item["kind"], 0.0, 0.0, item["s"]), translate(item["cx"], item["cy"]),
                    "PFD_EQUIPMENT")
    for node in nodes:
        item = placed[node["id"]]
        size = sizes[node["id"]]
        tag = (node.get("tag") or "").strip()
        caption = clip(f'{tag} {node.get("label") or ""}'.strip(), sym.T_NAME, 60.0)
        tag_text = tag or caption[:12]
        name_text = clip(node.get("label") or "", sym.T_NAME, 58.0) if tag else ""
        tag_x, tag_y, name_y = _place_caption(item["cx"], item["cy"] + size["down"], tag_text, name_text,
                                              occupied, pipe_boxes,
                                              limit=draw_y + draw_h - 2.0,
                                              above=item["cy"] - size["up"])
        # 位号与名称居中排：最左/最右的设备会把文字甩到图框外，按文字宽度夹回框内。
        cap_w = max(text_width(tag_text, sym.T_TAG),
                    text_width(name_text, sym.T_NAME) if name_text else 0.0)
        cap_x1 = draw_x + cap_w / 2.0 + 1.0
        cap_x2 = draw_x + draw_w - cap_w / 2.0 - 1.0
        if cap_x1 <= cap_x2:
            tag_x = min(max(tag_x, cap_x1), cap_x2)
        text(prims, tag_x, tag_y, sym.T_TAG, tag_text, "PFD_TAG", anchor="middle")
        occupied.append(_text_box(tag_x, tag_y, sym.T_TAG, tag_text, "middle"))
        if name_text:
            text(prims, tag_x, name_y, sym.T_NAME, name_text, "PFD_LABEL", anchor="middle")
            occupied.append(_text_box(tag_x, name_y, sym.T_NAME, name_text, "middle"))

    # 差额去向：必须画在图上（底部排出/放空/母液…），未标注的差额用红字提示
    for node in nodes:
        item = placed[node["id"]]
        entries = list(losses.get(node["id"]) or [])
        gap = gaps.get(node["id"])
        if not entries and not gap:
            continue
        origin = (item["cx"] + sizes[node["id"]]["hw"] + 2.0, item["cy"])
        tip = (origin[0] + 16.0, item["cy"])
        layer = "PFD_WARN" if not entries else "PFD_MINOR"
        arrow_layer = "PFD_WARN" if not entries else "PFD_ARROW"
        line(prims, origin[0], origin[1], tip[0], tip[1], sym.W_MINOR, layer)
        arrow_solid(prims, [origin, tip], 2.6, arrow_layer)
        if entries:
            for position, value in enumerate(entries):
                text(prims, tip[0] + 2.0, tip[1] - 1.0 + position * 4.2, sym.T_FLOW,
                     clip(f"去向：{value}", sym.T_FLOW, 52.0), "PFD_MINOR")
        else:
            text(prims, tip[0] + 2.0, tip[1] - 1.0, sym.T_FLOW,
                 clip(f"差额 {gap} 去向待补", sym.T_FLOW, 52.0), "PFD_WARN", weight="bold")

    # 右下角图签栏（签名档）：第 2 张只保留图面与签名档，
    # 设备一览表、物流表、管道附件表与物料守恒校核表都不再占据图面。
    if warn_lines:
        draw_warning(prims, draw_x, table_y, draw_w - TITLE_BLOCK_W - GAP,
                     ctx["warning_title"], warn_lines)
    draw_title_block(prims, sheet_w - sym.SHEET_MARGIN - TITLE_BLOCK_W, sheet_h - sym.SHEET_MARGIN - tb_h,
                     TITLE_BLOCK_W, _title_block_rows(ctx, ctx["section_name"], "第 2 张 · 共 2 张",
                                                      scale_text, fmt_name))

    draw_frame(prims, sheet_w, sheet_h)
    draw_header(prims, draw_x, sym.SHEET_MARGIN, draw_w, ctx["title"], ctx["section_name"],
                ctx["section_index"], meta, scale_text, f"{fmt_name} 横放")
    if notes:
        draw_notes(prims, draw_x, sym.SHEET_MARGIN + HEADER_H + GAP, draw_w, notes)
    return {"prims": prims, "sheet_w": sheet_w, "sheet_h": sheet_h, "view": "section",
            "scale": scale_text, "format": fmt_name}


# --- 图例图（第 1 张）：CAD 图层与颜色 --------------------------------------
ROW_LAYER_KEY = 5.0


def layer_key_rows() -> tuple:
    """图层表：按院标顺序列出这张图纸用到的图层。

    每行给 (院标图层名, 内部图元层, 色号, 颜色, 用途)；内部图元层用来把色块
    画在对应的图层上，这样 DXF 里那块色块本身就是该层的颜色。
    """
    used: dict = {}
    for internal, name in LAYER_MAP.items():
        used.setdefault(name, internal)
    return tuple((name, used[name], aci, LAYER_RGB[name], LAYER_USE[name])
                 for name, aci, _rgb, _lt, _use in CAD_LAYERS if name in used)


def draw_layer_key(prims, x, y, width, height, rows):
    """CAD 图层与颜色（院标图层表）。

    色号 7（白 / 黑随背景反色）在白纸上按黑线表示，与 CAD 里的显示一致；
    打印线宽按院标习惯由打印样式表（CTB）按色号控制。
    """
    text(prims, x, y - 1.8, sym.T_TABLE_TITLE, "CAD 图层与颜色（院标图层表）",
         "PFD_TABLE", weight="bold")
    rect(prims, x, y, width, height, sym.W_MINOR, "PFD_TABLE")
    name_x = x + 16.0
    aci_x = x + 48.0
    use_x = x + 68.0
    text(prims, name_x, y + 4.4, sym.T_TABLE_HEAD, "图层名", "PFD_TABLE", weight="bold")
    text(prims, aci_x, y + 4.4, sym.T_TABLE_HEAD, "色号", "PFD_TABLE", weight="bold")
    text(prims, use_x, y + 4.4, sym.T_TABLE_HEAD,
         "用途（选这一层画哪类内容）", "PFD_TABLE", weight="bold")
    line(prims, x, y + 6.2, x + width, y + 6.2, sym.W_EQUIP, "PFD_TABLE")
    for index, (name, internal, aci, rgb, use) in enumerate(rows):
        top = y + 6.2 + index * ROW_LAYER_KEY
        if index:
            line(prims, x, top, x + width, top, sym.W_THIN, "PFD_TABLE")
        mid = top + ROW_LAYER_KEY / 2.0
        line(prims, x + 2.0, mid, x + 13.0, mid, sym.W_MAIN, internal,
             INK if aci == 7 else rgb)
        text(prims, name_x, top + 3.5, sym.T_TABLE_CELL, name, "PFD_TABLE")
        text(prims, aci_x, top + 3.5, sym.T_TABLE_CELL, str(aci), "PFD_TABLE")
        text(prims, use_x, top + 3.5, sym.T_TABLE_CELL,
             clip(use, sym.T_TABLE_CELL, x + width - use_x - 2.0), "PFD_TABLE")


# --- 图例图（第 1 张） -----------------------------------------------------
def compose_legend(ctx):
    prims: list[dict] = []
    graph = ctx["graph"]
    meta = ctx["meta"]
    equipment, streams, assets, balance_rows = _layout_rows(ctx)
    notes = _notes_lines(ctx)
    path_note = _main_material_note(ctx)
    if path_note and path_note not in notes:
        notes = notes + [path_note]
    convention = list(ctx.get("convention_lines") or [])
    balance_h = table_height(sym.ROW_BALANCE, len(balance_rows))
    tb_h = _title_block_height(ctx)
    notes_h = notes_height(len(notes))
    # 线型说明按实际条数排：多一条说明就多一行，不会再压到下面的「流向」行上
    link_rows = list(ctx.get("linetype_rows") or [])
    line_rows_h = 4.0 + 6.2 * (len(link_rows) + 1)
    line_h = line_rows_h + 4.0
    # 图层表：每层一行，行高 5.0 mm，加表头
    key_rows = layer_key_rows()
    key_h = 6.2 + ROW_LAYER_KEY * len(key_rows) + 1.0
    conv_h = 12.0 + 4.6 * max(1, len(convention))
    # 表册（设备一览表 / 物流表 / 管道附件与仪表表）从第 2 张工段图挪到第 1 张图例图，
    # 图例图要多留一条表册带。
    eq_h = table_height(sym.ROW_EQUIPMENT, len(equipment))
    st_h = table_height(sym.ROW_STREAM, len(streams))
    asset_h = table_height(sym.ROW_STREAM, len(assets)) if assets else 0.0
    tables_h = max(eq_h, st_h, asset_h)
    # 公用工程条件表单独占一条：介质、规格/温度、用量、接入单元与依据逐条列出，
    # 让「哪一段要多少温度、走冷媒还是热媒」在图例上就说得清。
    util_rows = list(ctx.get("utility_rows") or [])
    util_h = table_height(sym.ROW_STREAM, max(1, len(util_rows)))

    # 右下角那两张表按图幅底边固定定位，表名画在框线上方 1.8 mm：
    # 这里必须按表名的高度留出余量，否则它会上移压住上面一张表的最后一行。
    rest_h = (GAP + tables_h + GAP + util_h + GAP + line_h + GAP + key_h + GAP + conv_h + GAP
              + max(balance_h, tb_h) + 12.0)

    def plan(name, sheet_w, sheet_h):
        usable = sheet_h - 2.0 * sym.SHEET_MARGIN
        draw_w = sheet_w - sym.SHEET_MARGIN_LEFT - sym.SHEET_MARGIN
        columns, _cell = catalog_columns(draw_w)
        grid_min = _catalog_grid(ctx, columns, LEGEND_CELL_MIN)
        grid_rows = sum(ctx["catalog_rows"])
        head = sym.SHEET_MARGIN + HEADER_H + GAP + notes_h + GAP
        slack = usable - (head - sym.SHEET_MARGIN) - rest_h - grid_min
        if slack < 0:
            return None
        # 富余高度摊到每行符号格上，图例图铺满幅面，不留大片空白
        cell_h = LEGEND_CELL_MIN + min(LEGEND_CELL_MAX - LEGEND_CELL_MIN, slack / max(1, grid_rows))
        grid_h = grid_rows * cell_h + len(sym.CATALOG_GROUPS) * 7.0
        return name, sheet_w, sheet_h, draw_w, columns, grid_h, head, cell_h

    chosen = None
    for name, sheet_w, sheet_h in sym.SHEET_FORMATS:
        chosen = plan(name, sheet_w, sheet_h)
        if chosen is not None:
            break
    if chosen is None:
        name, sheet_w, sheet_h = sym.SHEET_FORMATS[-1]
        draw_w = sheet_w - sym.SHEET_MARGIN_LEFT - sym.SHEET_MARGIN
        columns, _cell = catalog_columns(draw_w)
        grid_h = _catalog_grid(ctx, columns, LEGEND_CELL_MIN)
        chosen = (name, sheet_w, sheet_h, draw_w, columns, grid_h,
                  sym.SHEET_MARGIN + HEADER_H + GAP + notes_h + GAP, LEGEND_CELL_MIN)
    fmt_name, sheet_w, sheet_h, draw_w, columns, grid_h, head, cell_h = chosen
    draw_x = sym.SHEET_MARGIN_LEFT
    cursor = head

    # 符号库：分组成表；符号在格子内居中，名称与说明固定在格子底部（不会串位）
    cell_w = (draw_w - 2.0) / columns
    grow = max(1.0, min(1.5, cell_h / LEGEND_CELL_MIN))
    for group_name, items in sym.CATALOG_GROUPS:
        rows = max(1, math.ceil(len(items) / columns))
        block_h = rows * cell_h
        rect(prims, draw_x, cursor - 1.0, draw_w, block_h + 7.0, sym.W_MINOR, "PFD_TABLE")
        text(prims, draw_x + 1.6, cursor + 4.4, sym.T_TABLE_TITLE, group_name, "PFD_TABLE", weight="bold")
        for position, (kind, kind_name, kind_note) in enumerate(items):
            row = position // columns
            column = position % columns
            cx = draw_x + cell_w * column + cell_w / 2.0
            top = cursor + 7.0 + row * cell_h
            size = legend_size(kind) * grow
            shapes = sym.symbol_shape(kind, 0.0, 0.0, size)
            x1, y1, x2, y2 = shape_bounds(shapes)
            draw_shapes(prims, shapes,
                        translate(cx - (x1 + x2) / 2.0, top + (cell_h - 9.0) * 0.5 - (y1 + y2) / 2.0),
                        "PFD_EQUIPMENT")
            text(prims, cx, top + cell_h - 8.4, sym.T_SYMBOL, kind_name, "PFD_LABEL",
                 anchor="middle", weight="bold")
            text(prims, cx, top + cell_h - 3.6, sym.T_SYMBOL,
                 clip(kind_note, sym.T_SYMBOL, cell_w - 4.0), "PFD_NOTE", anchor="middle")
        cursor += block_h + 7.0 + GAP

    # 线型、介质与流向图例
    text(prims, draw_x, cursor - 1.8, sym.T_TABLE_TITLE, "线型、介质与流向图例", "PFD_TABLE", weight="bold")
    rect(prims, draw_x, cursor, draw_w, line_h, sym.W_MINOR, "PFD_TABLE")
    for index, (label, layer, weight) in enumerate(link_rows):
        top = cursor + 4.0 + index * 6.2
        line(prims, draw_x + 2.0, top, draw_x + 34.0, top, weight, layer)
        text(prims, draw_x + 37.0, top + 1.0, sym.T_NOTE,
             clip(label, sym.T_NOTE, draw_w - 40.0), "PFD_LABEL")
    flow_y = cursor + 4.0 + len(link_rows) * 6.2
    # 流向与进出车间（界区）画法：主线端头的旗标就是进 / 出车间的标记，尖端指向流向
    flag_len, _flag_h = _boundary_size(["界区"])
    line(prims, draw_x + 2.0, flow_y, draw_x + 34.0 - flag_len, flow_y, sym.W_MAIN_HEAVY, "PFD_MAIN")
    arrow_solid(prims, [(draw_x + 16.0 - flag_len, flow_y), (draw_x + 21.0 - flag_len, flow_y)],
                3.0, "PFD_ARROW")
    boundary_flag(prims, draw_x + 34.0 - flag_len, flow_y, ["界区"], True)
    text(prims, draw_x + 37.0, flow_y + 1.0, sym.T_NOTE,
         clip("流向与进出车间（界区）：箭头指向下游；进出车间的流股（主物料进出口、三废出口、"
              "公用工程总管）在图框边线处画界区旗标——方框加尖端、尖端顺着流动方向，"
              "框内写流股名称与流量", sym.T_NOTE, draw_w - 40.0),
         "PFD_LABEL")
    cursor += line_h + GAP

    # CAD 图层与颜色（院标图层表）：把「先建图层、再按图层画线」的习惯写进图纸
    draw_layer_key(prims, draw_x, cursor, draw_w, key_h, key_rows)
    cursor += key_h + GAP

    # 物料流向与守恒说明
    text(prims, draw_x, cursor - 1.8, sym.T_TABLE_TITLE, "物料流向与守恒说明", "PFD_TABLE", weight="bold")
    rect(prims, draw_x, cursor, draw_w, conv_h, sym.W_MINOR, "PFD_TABLE")
    verdict_from = int(ctx.get("convention_verdict_from") or 0)
    for index, value in enumerate(convention):
        layer = "PFD_WARN" if index >= verdict_from else "PFD_LABEL"
        text(prims, draw_x + 2.0, cursor + 6.4 + index * 4.6, sym.T_NOTE,
             clip(value, sym.T_NOTE, draw_w - 4.0), layer)
    cursor += conv_h + GAP

    # 表册：设备一览表 / 物流表 / 管道附件与仪表表（三张并排）。
    # 按设计院习惯，这些表跟在第 1 张图例图里，第 2 张工段图只留图面与右下角签名档。
    if assets:
        inner_w = draw_w - GAP * 2.0
        eq_w = inner_w * 0.40
        st_w = inner_w * 0.36
        asset_w = inner_w - eq_w - st_w
        draw_table(prims, draw_x, cursor, eq_w, "设备一览表", ctx["equipment_columns"],
                   ctx["equipment_weights"], equipment, ctx["empty_table"], sym.ROW_EQUIPMENT)
        draw_table(prims, draw_x + eq_w + GAP, cursor, st_w, "物流表", ctx["stream_columns"],
                   ctx["stream_weights"], streams, ctx["empty_table"], sym.ROW_STREAM)
        draw_table(prims, draw_x + eq_w + GAP + st_w + GAP, cursor, asset_w,
                   "管道附件与仪表（概念示意）", ctx["asset_columns"], ctx["asset_weights"],
                   assets, ctx["empty_table"], sym.ROW_STREAM)
    else:
        left_w = draw_w * 0.40
        draw_table(prims, draw_x, cursor, left_w, "设备一览表", ctx["equipment_columns"],
                   ctx["equipment_weights"], equipment, ctx["empty_table"], sym.ROW_EQUIPMENT)
        draw_table(prims, draw_x + left_w + GAP, cursor, draw_w - left_w - GAP, "物流表",
                   ctx["stream_columns"], ctx["stream_weights"], streams, ctx["empty_table"],
                   sym.ROW_STREAM)
    cursor += tables_h + GAP

    # 公用工程条件：资料里没有就画一行交代，避免设计人员以为漏画了
    draw_table(prims, draw_x, cursor, draw_w, ctx.get("utility_title") or "公用工程条件",
               ctx.get("utility_columns") or (), ctx.get("utility_weights") or (),
               util_rows, ctx.get("utility_empty") or "", sym.ROW_STREAM)
    cursor += util_h + GAP

    # 物料守恒校核表与签名档并排，排在图纸右下角
    bottom_h = max(tb_h, balance_h)
    bottom_y = sheet_h - sym.SHEET_MARGIN - bottom_h
    draw_table(prims, draw_x, bottom_y, draw_w - TITLE_BLOCK_W - GAP, ctx["balance_title"],
               ctx["balance_columns"], ctx["balance_weights"], balance_rows, ctx["empty_table"],
               sym.ROW_BALANCE)

    # 右下角图签栏（签名档）
    draw_title_block(prims, sheet_w - sym.SHEET_MARGIN - TITLE_BLOCK_W, sheet_h - sym.SHEET_MARGIN - tb_h,
                     TITLE_BLOCK_W, _title_block_rows(ctx, ctx["legend_name"], "第 1 张 · 共 2 张",
                                                      "不按比例（NTS）", fmt_name))

    draw_frame(prims, sheet_w, sheet_h)
    draw_header(prims, draw_x, sym.SHEET_MARGIN, draw_w, ctx["title"], ctx["legend_name"],
                ctx["legend_index"], meta, "不按比例（NTS）", f"{fmt_name} 横放")
    draw_notes(prims, draw_x, sym.SHEET_MARGIN + HEADER_H + GAP, draw_w, notes)
    return {"prims": prims, "sheet_w": sheet_w, "sheet_h": sheet_h, "view": "legend",
            "scale": "不按比例（NTS）", "format": fmt_name}


def compose(ctx) -> dict:
    return compose_legend(ctx) if ctx.get("view") == "legend" else compose_section(ctx)


# --- 输出：SVG 与 DXF -------------------------------------------------------
DASH = {"DASHED": "7,2.6", "DASHDOT": "9,2.4,1.4,2.4"}
PIPE_LAYERS = ("PFD_MAIN", "PFD_SECONDARY", "PFD_UTILITY", "PFD_RESERVED")


def _esc(value) -> str:
    return (str(value).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def emit_svg(sheet) -> str:
    """图纸输出：深色工作区里垫一张白纸，图元坐标始终相对纸面原点。"""
    paper_w = sheet["sheet_w"]
    paper_h = sheet["sheet_h"]
    width = paper_w + 2.0 * WORKSPACE_PAD
    height = paper_h + 2.0 * WORKSPACE_PAD
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.1f}mm" height="{height:.1f}mm" '
        f'viewBox="0 0 {width:.2f} {height:.2f}" role="img" aria-label="概念级工艺流程图 CAD 图纸">',
        f'<rect x="0" y="0" width="{width:.2f}" height="{height:.2f}" fill="{WORKSPACE}"/>',
        f'<rect x="{WORKSPACE_PAD:.2f}" y="{WORKSPACE_PAD:.2f}" width="{paper_w:.2f}" '
        f'height="{paper_h:.2f}" fill="{PAPER}"/>',
        f'<g transform="translate({WORKSPACE_PAD:.2f} {WORKSPACE_PAD:.2f})">',
    ]
    for layer in LAYER_ORDER:
        items = [item for item in sheet["prims"] if item["layer"] == layer]
        if not items:
            continue
        colour = SVG_COLOR.get(layer, INK)
        stroke = ' stroke-dasharray="%s"' % DASH[LAYER_LTYPE[layer]] if layer in LAYER_LTYPE else ""
        parts.append(f'<g id="{layer}" stroke="{colour}"{stroke}>')
        for item in items:
            if item["t"] == "line":
                ink = f' stroke="{item["ink"]}"' if item.get("ink") else ""
                parts.append(f'<line x1="{item["x1"]:.2f}" y1="{item["y1"]:.2f}" x2="{item["x2"]:.2f}" '
                             f'y2="{item["y2"]:.2f}" stroke-width="{item["w"]:.2f}"{ink}/>')
            elif item["t"] == "poly":
                points = " ".join(f"{x:.2f},{y:.2f}" for x, y in item["pts"])
                if layer in PIPE_LAYERS and not item["closed"]:
                    parts.append(f'<polyline points="{points}" fill="none" stroke-width="{item["w"]:.2f}"/>')
                else:
                    path = "M " + " L ".join(f"{x:.2f} {y:.2f}" for x, y in item["pts"]) + (" Z" if item["closed"] else "")
                    parts.append(f'<path d="{path}" fill="none" stroke-width="{item["w"]:.2f}"/>')
            elif item["t"] == "rect":
                parts.append(f'<rect x="{item["x"]:.2f}" y="{item["y"]:.2f}" width="{item["w"]:.2f}" '
                             f'height="{item["h"]:.2f}" fill="none" stroke-width="{item["lw"]:.2f}"/>')
            elif item["t"] == "circle":
                parts.append(f'<circle cx="{item["cx"]:.2f}" cy="{item["cy"]:.2f}" r="{item["r"]:.2f}" '
                             f'fill="none" stroke-width="{item["w"]:.2f}"/>')
            elif item["t"] == "solid":
                points = " ".join(f"{x:.2f},{y:.2f}" for x, y in item["pts"])
                parts.append(f'<polygon points="{points}" fill="{colour}" stroke="none"/>')
        parts.append("</g>")
    for item in sheet["prims"]:
        if item["t"] != "text":
            continue
        colour = SVG_COLOR.get(item["layer"], INK)
        anchor = {"start": "start", "middle": "middle", "end": "end"}[item["anchor"]]
        bold = ' font-weight="700"' if item["weight"] == "bold" else ""
        parts.append(f'<text x="{item["x"]:.2f}" y="{item["y"]:.2f}" font-size="{item["h"]:.2f}" '
                     f'fill="{colour}" text-anchor="{anchor}" font-family="{SVG_FONT}"{bold}>'
                     f'{_esc(item["value"])}</text>')
    parts.append("</g></svg>")
    return "".join(parts)


def emit_dxf(sheet) -> str:
    width = sheet["sheet_w"]
    height = sheet["sheet_h"]
    out: list[str] = []

    def put(code, value=""):
        out.append(str(code))
        out.append(f"{value}")

    def flip(value: float) -> float:
        return height - float(value)

    put(0, "SECTION")
    put(2, "HEADER")
    put(9, "$ACADVER")
    put(1, "AC1009")
    put(9, "$DWGCODEPAGE")
    put(3, "ANSI_936")
    put(9, "$MEASUREMENT")
    put(70, 1)
    put(9, "$EXTMIN")
    put(10, "0.0")
    put(20, "0.0")
    put(30, "0.0")
    put(9, "$EXTMAX")
    put(10, f"{width:.3f}")
    put(20, f"{height:.3f}")
    put(30, "0.0")
    put(0, "ENDSEC")

    put(0, "SECTION")
    put(2, "TABLES")
    put(0, "TABLE")
    put(2, "LTYPE")
    put(70, 5)
    for name, description, dashes in (
        ("CONTINUOUS", "Solid line", ()),
        ("DASHED", "Dashed __ __ __", (0.5, -0.25)),
        ("DASHDOT", "Dash dot __ . __ .", (0.5, -0.25, 0.0, -0.25)),
        ("ByBlock", "", ()),
        ("ByLayer", "", ()),
    ):
        put(0, "LTYPE")
        put(2, name)
        put(70, 0)
        put(3, description)
        put(72, 65)
        put(73, len(dashes))
        put(40, "0.75")
        for dash in dashes:
            put(49, f"{dash:.2f}")
    put(0, "ENDTAB")
    put(0, "TABLE")
    put(2, "LAYER")
    put(70, len(CAD_LAYERS))
    # 图层名、色号、线型都按院标预设：打开就是一套分好类的图层。
    for name, aci, _rgb, linetype, _use in CAD_LAYERS:
        put(0, "LAYER")
        put(2, name)
        put(70, 0)
        put(62, aci)
        put(6, linetype)
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
        put(71, "0")
        put(42, "2.5")
        put(3, font)
        put(4, big)
    put(0, "ENDTAB")
    put(0, "ENDSEC")

    put(0, "SECTION")
    put(2, "ENTITIES")

    def ent(kind, layer, ltype=None, colour=None):
        """图元头：图层写院标层名；预留管线补线型，警告与图名补颜色。"""
        put(0, kind)
        put(8, layer)
        if ltype:
            put(6, ltype)
        if colour:
            put(62, colour)

    for item in sheet["prims"]:
        source = item["layer"]
        layer = LAYER_MAP.get(source, source)
        ltype = ENTITY_LTYPE.get(source)
        colour = ENTITY_COLOR.get(source)
        if item["t"] == "line":
            ent("LINE", layer, ltype, colour)
            put(10, f'{item["x1"]:.4f}')
            put(20, f'{flip(item["y1"]):.4f}')
            put(30, "0.0")
            put(11, f'{item["x2"]:.4f}')
            put(21, f'{flip(item["y2"]):.4f}')
            put(31, "0.0")
        elif item["t"] == "poly":
            ent("POLYLINE", layer, ltype, colour)
            put(66, 1)
            put(70, 1 if item["closed"] else 0)
            for x, y in item["pts"]:
                put(0, "VERTEX")
                put(8, layer)
                put(10, f"{x:.4f}")
                put(20, f"{flip(y):.4f}")
                put(30, "0.0")
            put(0, "SEQEND")
            put(8, layer)
        elif item["t"] == "rect":
            x, y, w, h = item["x"], item["y"], item["w"], item["h"]
            corners = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
            ent("POLYLINE", layer, ltype, colour)
            put(66, 1)
            put(70, 1)
            for cx, cy in corners:
                put(0, "VERTEX")
                put(8, layer)
                put(10, f"{cx:.4f}")
                put(20, f"{flip(cy):.4f}")
                put(30, "0.0")
            put(0, "SEQEND")
            put(8, layer)
        elif item["t"] == "circle":
            ent("CIRCLE", layer, ltype, colour)
            put(10, f'{item["cx"]:.4f}')
            put(20, f'{flip(item["cy"]):.4f}')
            put(30, "0.0")
            put(40, f'{item["r"]:.4f}')
        elif item["t"] == "solid":
            points = list(item["pts"])
            while len(points) < 4:
                points.append(points[-1])
            ent("SOLID", layer, ltype, colour)
            for index, (x, y) in enumerate(points[:4]):
                code = 10 + index
                put(code, f"{x:.4f}")
                put(code + 10, f"{flip(y):.4f}")
                put(code + 20, "0.0")
        elif item["t"] == "text":
            x = item["x"]
            if item["anchor"] == "middle":
                x -= text_width(item["value"], item["h"]) / 2.0
            elif item["anchor"] == "end":
                x -= text_width(item["value"], item["h"])
            ent("TEXT", layer, ltype, colour)
            put(10, f"{x:.4f}")
            put(20, f'{flip(item["y"]):.4f}')
            put(30, "0.0")
            put(40, f'{item["h"]:.4f}')
            put(1, item["value"])
            put(7, "PFD_CN")
    put(0, "ENDSEC")
    put(0, "EOF")
    return "\n".join(out) + "\n"
