"""标准化工艺流程图（PFD）图形符号库与图纸制图基准。

把「图形符号」和「版面基准」从渲染代码里独立出来，供 SVG 预览、DXF 导出与
浏览器画布共用同一套几何，保证同一张图上符号大小、比例与线宽一致：

* 符号按 **图纸毫米** 定义。设备基准半尺寸 ``SYM_EQUIPMENT``、阀门 ``SYM_VALVE``、
  仪表 ``SYM_INSTRUMENT`` 固定，符号之间的大小比例不再随节点框漂移，
  也不会出现「阀门画得矮胖」这类比例失调。
* 每个符号给出 **接口点（port）**：方框类设备取轮廓边中点，圆形设备取 **圆周（外径）**，
  管线必须终止在接口点上，不得穿过符号本体。
* 线宽与字高分档：粗实线 0.70（主要物料流）、中实线 0.50（设备轮廓）、
  次要线 0.35（公用工程/预留）、细线 0.25（仪表信号与标注引线）。

所有角度在屏幕坐标（y 轴向下）下定义：0° 为 +x，90° 为 +y（向下），
因此 180°→360° 的圆弧向上凸起（用于椭圆封头与气动薄膜头）。
"""
from __future__ import annotations

import math

# --- 线宽（mm） -------------------------------------------------------------
W_MAIN = 0.70
W_MAIN_HEAVY = 0.90
W_EQUIP = 0.50
W_MINOR = 0.35
W_THIN = 0.25
# 辅助物料线（公用工程总管与支管、安全阀组 / 放空 / 排污支管）的线宽：
# 按设计口径取主物料线的 1/3（主物料线粗实线 0.90 mm → 辅助线 0.30 mm），并且一律画实线。
# 虚线与点划线只留给「预留管线 / 备用接口」和「仪表信号」。
W_UTILITY = round(W_MAIN_HEAVY / 3.0, 2)

# --- 符号基准尺寸（mm） -----------------------------------------------------
SYM_EQUIPMENT = 8.0
SYM_VALVE = 2.6
SYM_INSTRUMENT = 2.6

# 各单元符号相对基准半尺寸的放大系数：一张图上「塔最高最大、反应器次之、
# 罐与换热器更小」。主设备与辅助设备的比例由符号本身决定，不再出现
# 「精馏塔还没换热器个头大」这种失调。
# 塔与储罐再放大一档：塔要压得住全场（比换热器直径高一截），立式 / 卧式储罐
# 也不能比换热器的圆壳还小。
SYM_KIND_SCALE: dict[str, float] = {
    "column": 2.30,
    "reactor": 1.45,
    "vessel": 1.45,
    "tank_h": 1.40,
    "separation": 0.95,
    "dryer": 0.95,
    "filter": 0.95,
    "feed": 0.92,
    "product": 0.90,
    "waste": 0.90,
    "analysis": 0.80,
    "heat_exchanger": 0.92,
    "condenser": 0.92,
    "pump": 0.92,
    "compressor": 0.92,
    "other": 0.95,
}


# 公用工程介质名称：图面上的总管标注与第 1 张图例的公用工程条件表都用这张表，
# 保证「冷却水」「冷冻水」「蒸汽」这类叫法在两张图上完全一致。
MEDIA_LABELS: dict[str, str] = {
    "cooling_water": "循环冷却水",
    "chilled_water": "冷冻水",
    "brine": "载冷盐水",
    "steam": "蒸汽",
    "heat_oil": "导热油",
    "electricity": "电（电加热/电伴热）",
    "nitrogen": "氮气",
    "instrument_air": "仪表空气",
    "compressed_air": "压缩空气",
    "condensate": "蒸汽冷凝水",
    "process_water": "工艺水",
    "vacuum": "真空",
    "utility": "公用工程（介质待补）",
    "material": "工艺物料",
}
MEDIA_TEXT = "、".join(f"{key}={value}" for key, value in MEDIA_LABELS.items())
# 总管自上而下的排列顺序：热的、冷的、惰性气体、水，和设计院画公用工程站的次序一致
MEDIA_ORDER: tuple[str, ...] = (
    "steam", "heat_oil", "condensate", "cooling_water", "chilled_water", "brine",
    "process_water", "nitrogen", "instrument_air", "compressed_air", "electricity", "vacuum",
)
# 排放类附件的名称（安全阀组 / 放空 / 排污），图例与管道附件表共用
RELIEF_LABELS: dict[str, str] = {
    "safety_valve": "安全阀组",
    "vent": "放空",
    "drain": "排污/排空",
    "rupture_disc": "爆破片",
}


def symbol_scale(kind: str) -> float:
    """该单元符号的放大系数；表里没有的类型按 1.0 处理。"""
    return SYM_KIND_SCALE.get(str(kind), 1.0)

# --- 字高（mm） -------------------------------------------------------------
T_FIG_TITLE = 7.0
T_HEAD = 4.2
T_NOTE = 3.2
T_TAG = 3.5
T_NAME = 3.0
T_PARAM = 2.6
T_FLOW = 2.6
T_TABLE_TITLE = 3.5
T_TABLE_HEAD = 3.0
T_TABLE_CELL = 2.6
T_TB_LABEL = 2.6
T_TB_VALUE = 3.0
T_LEGEND = 2.6
T_SYMBOL = 2.4

# --- 表格行高（mm） ---------------------------------------------------------
ROW_EQUIPMENT = 6.0
ROW_STREAM = 5.6
ROW_BALANCE = 5.6
ROW_HEAD = 7.0
ROW_TITLE_BLOCK = 4.6

# --- 图幅（mm，横放） -------------------------------------------------------
SHEET_FORMATS: tuple[tuple[str, float, float], ...] = (
    ("A4", 297.0, 210.0),
    ("A3", 420.0, 297.0),
    ("A2", 594.0, 420.0),
    ("A1", 841.0, 594.0),
    ("A0", 1189.0, 841.0),
)
# 装订边（左边）25，其余 10
SHEET_MARGIN_LEFT = 25.0
SHEET_MARGIN = 10.0

# 标准比例系列（图纸 : 实际布置模型）
STANDARD_SCALES: tuple[int, ...] = (1, 2, 5, 10, 20, 25, 50, 100, 200, 500)


def arc_points(cx: float, cy: float, r: float, start: float, end: float, steps: int = 14) -> list[tuple[float, float]]:
    """采样一段圆弧为折线点（角度制，屏幕坐标 y 向下）。"""
    span = end - start
    return [
        (cx + r * math.cos(math.radians(start + span * index / steps)),
         cy + r * math.sin(math.radians(start + span * index / steps)))
        for index in range(steps + 1)
    ]


def _rect_ports(cx: float, cy: float, hw: float, hh: float) -> dict[str, tuple[float, float]]:
    return {"w": (cx - hw, cy), "e": (cx + hw, cy), "n": (cx, cy - hh), "s": (cx, cy + hh)}


def _circle_ports(cx: float, cy: float, r: float) -> dict[str, tuple[float, float]]:
    """圆周接口：接管落在圆的外径上，不再穿进圆内。"""
    return {"w": (cx - r, cy), "e": (cx + r, cy), "n": (cx, cy - r), "s": (cx, cy + r)}


def symbol_ports(kind: str, cx: float, cy: float, s: float) -> dict[str, tuple[float, float]]:
    """返回符号可用接口点；管线只能从这里进出符号。"""
    if kind in ("heat_exchanger", "condenser"):
        return _circle_ports(cx, cy, s)
    if kind == "pump":
        return {"w": (cx - s, cy), "n": (cx, cy - s), "e": (cx + s * 0.7, cy + s * 0.75)}
    if kind == "compressor":
        return {"w": (cx - s * 0.7, cy + s * 0.75), "n": (cx, cy - s), "e": (cx + s, cy)}
    if kind in ("vessel", "reactor", "tank_h"):
        return _rect_ports(cx, cy, s * 0.62, s * 0.95)
    if kind == "column":
        return {"w": (cx - s * 0.40, cy), "e": (cx + s * 0.40, cy),
                "n": (cx, cy - s * 1.70), "s": (cx, cy + s * 1.75)}
    if kind == "feed":
        return _rect_ports(cx, cy, s * 0.95, s * 0.9)
    if kind == "dryer":
        return _rect_ports(cx, cy, s, s * 0.62)
    if kind == "separation":
        return _rect_ports(cx, cy, s * 0.8, s * 0.9)
    if kind == "filter":
        return _rect_ports(cx, cy, s * 0.5, s * 0.9)
    if kind.startswith("valve_"):
        return {"in": (cx - s, cy), "out": (cx + s, cy), "w": (cx - s, cy), "e": (cx + s, cy),
                "n": (cx, cy - s * 1.5), "s": (cx, cy + s)}
    if kind.startswith("instrument_"):
        return {"w": (cx - s, cy), "e": (cx + s, cy), "n": (cx, cy - s), "s": (cx, cy + s)}
    return _rect_ports(cx, cy, s, s)


def symbol_shape(kind: str, cx: float, cy: float, s: float) -> list[tuple]:
    """标准图形符号。s 为基准半尺寸（mm），比例由基准决定，不随节点框伸缩。"""
    hw = s * 0.95

    if kind == "feed":  # 固体投料/计量料斗
        return [
            ("poly", [(cx - hw, cy - s * 0.9), (cx + hw, cy - s * 0.9),
                      (cx + s * 0.34, cy + s * 0.3), (cx - s * 0.34, cy + s * 0.3)], True, W_EQUIP),
            ("line", cx - s * 0.34, cy + s * 0.3, cx - s * 0.34, cy + s * 0.9, W_EQUIP),
            ("line", cx + s * 0.34, cy + s * 0.3, cx + s * 0.34, cy + s * 0.9, W_EQUIP),
        ]
    if kind == "vessel":  # 立式储罐/暂存罐（带上下椭圆封头）
        return [
            ("line", cx - s * 0.62, cy - s * 0.72, cx - s * 0.62, cy + s * 0.72, W_EQUIP),
            ("line", cx + s * 0.62, cy - s * 0.72, cx + s * 0.62, cy + s * 0.72, W_EQUIP),
            ("poly", arc_points(cx, cy - s * 0.72, s * 0.62, 180.0, 360.0), False, W_EQUIP),
            ("poly", arc_points(cx, cy + s * 0.72, s * 0.62, 0.0, 180.0), False, W_EQUIP),
        ]
    if kind == "tank_h":  # 卧式储罐
        return [
            ("line", cx - s * 0.7, cy - s * 0.5, cx + s * 0.7, cy - s * 0.5, W_EQUIP),
            ("line", cx - s * 0.7, cy + s * 0.5, cx + s * 0.7, cy + s * 0.5, W_EQUIP),
            ("poly", arc_points(cx - s * 0.7, cy, s * 0.5, 90.0, 270.0), False, W_EQUIP),
            ("poly", arc_points(cx + s * 0.7, cy, s * 0.5, -90.0, 90.0), False, W_EQUIP),
        ]
    if kind == "reactor":  # 釜式反应器（带搅拌与电机）
        return [
            ("line", cx - s * 0.62, cy - s * 0.62, cx - s * 0.62, cy + s * 0.62, W_EQUIP),
            ("line", cx + s * 0.62, cy - s * 0.62, cx + s * 0.62, cy + s * 0.62, W_EQUIP),
            ("poly", arc_points(cx, cy - s * 0.62, s * 0.62, 180.0, 360.0), False, W_EQUIP),
            ("poly", arc_points(cx, cy + s * 0.62, s * 0.62, 0.0, 180.0), False, W_EQUIP),
            ("line", cx, cy - s * 0.62, cx, cy + s * 0.48, W_EQUIP),          # 搅拌轴
            ("line", cx - s * 0.34, cy + s * 0.48, cx + s * 0.34, cy + s * 0.48, W_EQUIP),  # 桨叶
            ("line", cx, cy - s * 1.05, cx, cy - s * 1.24, W_THIN),           # 轴伸出封头
            ("circle", cx, cy - s * 1.42, s * 0.2, W_EQUIP),                  # 电机
            ("line", cx - s * 0.5, cy + s * 0.9, cx + s * 0.5, cy + s * 0.9, W_THIN),  # 支腿
        ]
    if kind == "column":  # 板式精馏塔：上下封头 + 六层塔板 + 裙座（全图最高的主设备）
        left, right = cx - s * 0.40, cx + s * 0.40
        top, bottom = cy - s * 1.30, cy + s * 1.30
        return [
            ("line", left, top, left, bottom, W_EQUIP),
            ("line", right, top, right, bottom, W_EQUIP),
            ("poly", arc_points(cx, top, s * 0.40, 180.0, 360.0), False, W_EQUIP),
            ("poly", arc_points(cx, bottom, s * 0.40, 0.0, 180.0), False, W_EQUIP),
            ("line", left, cy - s * 1.05, cx + s * 0.12, cy - s * 1.05, W_THIN),
            ("line", cx - s * 0.12, cy - s * 0.72, right, cy - s * 0.72, W_THIN),
            ("line", left, cy - s * 0.39, cx + s * 0.12, cy - s * 0.39, W_THIN),
            ("line", cx - s * 0.12, cy - s * 0.06, right, cy - s * 0.06, W_THIN),
            ("line", left, cy + s * 0.27, cx + s * 0.12, cy + s * 0.27, W_THIN),
            ("line", cx - s * 0.12, cy + s * 0.60, right, cy + s * 0.60, W_THIN),
            ("line", left, cy + s * 0.93, cx + s * 0.12, cy + s * 0.93, W_THIN),
            ("line", left, bottom, left, bottom + s * 0.45, W_EQUIP),
            ("line", right, bottom, right, bottom + s * 0.45, W_EQUIP),
            ("line", left, bottom + s * 0.45, right, bottom + s * 0.45, W_EQUIP),
        ]
    if kind in ("heat_exchanger", "condenser"):  # 列管式换热器/冷凝器（圆形壳体）
        return [
            ("circle", cx, cy, s, W_EQUIP),
            ("line", cx - s * 0.55, cy - s * 0.78, cx - s * 0.55, cy + s * 0.78, W_EQUIP),  # 管板
            ("line", cx + s * 0.55, cy - s * 0.78, cx + s * 0.55, cy + s * 0.78, W_EQUIP),
            ("line", cx - s * 0.55, cy - s * 0.3, cx + s * 0.55, cy - s * 0.3, W_THIN),    # 管束
            ("line", cx - s * 0.55, cy, cx + s * 0.55, cy, W_THIN),
            ("line", cx - s * 0.55, cy + s * 0.3, cx + s * 0.55, cy + s * 0.3, W_THIN),
        ]
    if kind == "pump":  # 离心泵（圆形泵体 + 叶轮 + 吸入/排出接管）
        return [
            ("circle", cx, cy, s * 0.62, W_EQUIP),
            ("poly", [(cx - s * 0.28, cy - s * 0.3), (cx + s * 0.3, cy), (cx - s * 0.28, cy + s * 0.3)], True, W_THIN),
            ("line", cx - s, cy, cx - s * 0.62, cy, W_MAIN),
            ("line", cx, cy - s, cx, cy - s * 0.62, W_MAIN),
            ("line", cx - s * 0.5, cy + s * 0.78, cx + s * 0.5, cy + s * 0.78, W_EQUIP),
            ("line", cx, cy + s * 0.62, cx, cy + s * 0.78, W_THIN),
        ]
    if kind == "compressor":  # 压缩机/风机
        return [
            ("circle", cx, cy, s * 0.62, W_EQUIP),
            ("poly", [(cx + s * 0.28, cy - s * 0.3), (cx - s * 0.3, cy), (cx + s * 0.28, cy + s * 0.3)], True, W_THIN),
            ("line", cx + s, cy, cx + s * 0.62, cy, W_MAIN),
            ("line", cx, cy - s, cx, cy - s * 0.62, W_MAIN),
            ("line", cx - s * 0.5, cy + s * 0.78, cx + s * 0.5, cy + s * 0.78, W_EQUIP),
        ]
    if kind == "filter":  # 过滤器（滤网斜线）
        return [
            ("line", cx - s * 0.5, cy - s * 0.86, cx + s * 0.5, cy - s * 0.86, W_EQUIP),
            ("line", cx - s * 0.5, cy + s * 0.86, cx + s * 0.5, cy + s * 0.86, W_EQUIP),
            ("line", cx - s * 0.5, cy - s * 0.86, cx - s * 0.5, cy + s * 0.86, W_EQUIP),
            ("line", cx + s * 0.5, cy - s * 0.86, cx + s * 0.5, cy + s * 0.86, W_EQUIP),
            ("line", cx - s * 0.5, cy - s * 0.5, cx + s * 0.5, cy - s * 0.14, W_THIN),
            ("line", cx - s * 0.5, cy - s * 0.14, cx + s * 0.5, cy + s * 0.22, W_THIN),
            ("line", cx - s * 0.5, cy + s * 0.22, cx + s * 0.5, cy + s * 0.58, W_THIN),
        ]
    if kind == "dryer":  # 干燥器
        return [
            ("line", cx - s, cy - s * 0.55, cx + s, cy - s * 0.55, W_EQUIP),
            ("line", cx - s, cy + s * 0.55, cx + s, cy + s * 0.55, W_EQUIP),
            ("line", cx - s, cy - s * 0.55, cx - s, cy + s * 0.55, W_EQUIP),
            ("line", cx + s, cy - s * 0.55, cx + s, cy + s * 0.55, W_EQUIP),
            ("line", cx - s * 0.55, cy - s * 0.55, cx - s * 0.55, cy + s * 0.55, W_THIN),
            ("line", cx + s * 0.55, cy - s * 0.55, cx + s * 0.55, cy + s * 0.55, W_THIN),
            ("line", cx - s * 0.3, cy + s * 0.55, cx - s * 0.3, cy + s * 0.9, W_MINOR),
            ("line", cx + s * 0.3, cy + s * 0.55, cx + s * 0.3, cy + s * 0.9, W_MINOR),
        ]
    if kind == "separation":  # 结晶/离心分离设备（锥底）
        return [
            ("poly", [(cx - s * 0.8, cy - s * 0.8), (cx + s * 0.8, cy - s * 0.8),
                      (cx + s * 0.8, cy + s * 0.25), (cx, cy + s * 0.9), (cx - s * 0.8, cy + s * 0.25)],
             True, W_EQUIP),
            ("line", cx, cy - s * 0.8, cx, cy + s * 0.1, W_THIN),
            ("line", cx - s * 0.55, cy - s * 0.35, cx + s * 0.55, cy - s * 0.35, W_THIN),
        ]
    if kind == "product":  # 产品/包装
        return [
            ("line", cx - s * 0.8, cy - s * 0.7, cx + s * 0.8, cy - s * 0.7, W_EQUIP),
            ("line", cx - s * 0.8, cy + s * 0.7, cx + s * 0.8, cy + s * 0.7, W_EQUIP),
            ("line", cx - s * 0.8, cy - s * 0.7, cx - s * 0.8, cy + s * 0.7, W_EQUIP),
            ("line", cx + s * 0.8, cy - s * 0.7, cx + s * 0.8, cy + s * 0.7, W_EQUIP),
            ("line", cx - s * 0.8, cy - s * 0.7, cx, cy - s * 0.2, W_THIN),
            ("line", cx + s * 0.8, cy - s * 0.7, cx, cy - s * 0.2, W_THIN),
        ]
    if kind == "waste":  # 废物/母液收集
        return [
            ("poly", [(cx - s * 0.8, cy - s * 0.7), (cx + s * 0.8, cy - s * 0.7),
                      (cx + s * 0.8, cy + s * 0.35), (cx + s * 0.45, cy + s * 0.9),
                      (cx - s * 0.45, cy + s * 0.9), (cx - s * 0.8, cy + s * 0.35)], True, W_EQUIP),
            ("line", cx - s * 0.5, cy - s * 0.25, cx + s * 0.5, cy - s * 0.25, W_THIN),
        ]
    if kind == "analysis":  # 取样/分析点
        return [
            ("poly", [(cx, cy - s), (cx + s, cy), (cx, cy + s), (cx - s, cy)], True, W_EQUIP),
            ("circle", cx, cy, s * 0.3, W_THIN),
        ]
    if kind == "other":  # 其他单元（通用）
        return [("rect", cx - s, cy - s, 2 * s, 2 * s, W_EQUIP)]

    # --- 管道附件：阀门 -----------------------------------------------------
    # 阀体为标准「蝶形」：两个三角形在中心对顶，外接正方形 2s×2s（不矮胖）。
    if kind.startswith("valve_"):
        body = [
            ("poly", [(cx - s, cy - s), (cx - s, cy + s), (cx, cy)], True, W_MAIN),
            ("poly", [(cx + s, cy - s), (cx + s, cy + s), (cx, cy)], True, W_MAIN),
        ]
        if kind == "valve_gate":            # 闸阀：两端阀座竖线，图形闭合
            return body + [
                ("line", cx - s, cy - s, cx - s, cy + s, W_MAIN),
                ("line", cx + s, cy - s, cx + s, cy + s, W_MAIN),
            ]
        if kind == "valve_globe":           # 截止阀：阀体 + 中心圆
            return body + [("circle", cx, cy, s * 0.3, W_THIN)]
        if kind == "valve_ball":            # 球阀：阀体 + 中心圆 + 阀杆
            return body + [
                ("circle", cx, cy, s * 0.42, W_THIN),
                ("line", cx, cy - s * 0.42, cx, cy - s * 1.5, W_MINOR),
            ]
        if kind == "valve_butterfly":       # 蝶阀：阀体 + 阀板竖线
            return body + [
                ("line", cx, cy - s, cx, cy + s, W_THIN),
                ("line", cx, cy - s, cx, cy - s * 1.5, W_MINOR),
            ]
        if kind == "valve_check":           # 止回阀：阀体 + 阀座线 + 流向箭头
            return body + [
                ("line", cx + s, cy - s, cx + s, cy + s, W_MAIN),
                ("poly", [(cx - s * 0.4, cy - s * 0.34), (cx + s * 0.34, cy), (cx - s * 0.4, cy + s * 0.34)], True, W_THIN),
            ]
        if kind == "valve_control":         # 气动薄膜调节阀：膜头闭合、阀杆两端相连
            head = cy - s * 1.55
            return body + [
                ("line", cx, cy, cx, head, W_MINOR),                     # 阀杆（连到阀体顶边与膜头底边）
                ("poly", arc_points(cx, head, s * 0.95, 180.0, 360.0), False, W_EQUIP),  # 膜头（上半圆）
                ("line", cx - s * 0.95, head, cx + s * 0.95, head, W_EQUIP),  # 膜头底边：闭合
            ]
        if kind == "valve_electric":        # 电动阀：阀体 + 方框执行机构
            return body + [
                ("line", cx, cy, cx, cy - s * 1.45, W_MINOR),
                ("rect", cx - s * 0.6, cy - s * 2.35, s * 1.2, s * 0.9, W_EQUIP),
            ]
        if kind == "valve_safety":          # 安全阀：阀体 + 阀杆 + 弹簧
            return body + [
                ("line", cx, cy, cx, cy - s * 1.5, W_MINOR),
                ("poly", [(cx - s * 0.7, cy - s * 1.5), (cx - s * 0.35, cy - s * 1.75),
                          (cx, cy - s * 1.5), (cx + s * 0.35, cy - s * 1.75),
                          (cx + s * 0.7, cy - s * 1.5)], False, W_EQUIP),
                ("line", cx - s * 0.7, cy - s * 1.5, cx + s * 0.7, cy - s * 1.5, W_EQUIP),
            ]
        if kind == "valve_regulator":       # 减压/调压阀
            return body + [
                ("line", cx, cy, cx, cy - s * 1.4, W_MINOR),
                ("rect", cx - s * 0.8, cy - s * 2.2, s * 1.6, s * 0.8, W_EQUIP),
            ]
        return body + [("line", cx, cy, cx, cy - s * 1.5, W_MINOR)]

    # --- 仪表 ---------------------------------------------------------------
    if kind == "instrument_local":          # 就地指示仪表
        return [("circle", cx, cy, s, W_THIN)]
    if kind == "instrument_board":          # 集中（DCS）指示仪表
        return [("circle", cx, cy, s, W_THIN), ("line", cx - s, cy, cx + s, cy, W_THIN)]
    if kind == "instrument_shared":         # 共用显示
        return [("circle", cx, cy, s, W_THIN),
                ("line", cx - s, cy - s * 0.35, cx + s, cy - s * 0.35, W_THIN),
                ("line", cx - s, cy + s * 0.35, cx + s, cy + s * 0.35, W_THIN)]
    if kind == "instrument_interlock":      # 联锁（方框内圆）
        return [("rect", cx - s, cy - s, 2 * s, 2 * s, W_THIN), ("circle", cx, cy, s * 0.78, W_THIN)]
    return [("circle", cx, cy, s, W_THIN)]


# 图例目录：第 1 张图纸（图例与符号说明）按此顺序成表。
CATALOG_GROUPS: tuple[tuple[str, tuple[tuple[str, str, str], ...]], ...] = (
    ("通用设备图形符号", (
        ("feed", "投料/计量", "固体或液体投料、计量单元"),
        ("vessel", "立式储罐", "暂存、缓冲、计量罐"),
        ("tank_h", "卧式储罐", "卧式罐、受液罐"),
        ("reactor", "釜式反应器", "带搅拌与电机"),
        ("column", "板式塔", "精馏、吸收、层析塔"),
        ("heat_exchanger", "列管式换热器", "圆形壳体，接管接圆外径"),
        ("condenser", "冷凝器", "同列管式换热器画法"),
        ("pump", "离心泵", "吸入左侧、排出顶部"),
        ("compressor", "压缩机/风机", "气体增压、输送"),
        ("filter", "过滤器", "膜过滤、精密过滤"),
        ("dryer", "干燥器", "干燥、蒸发、结晶后处理"),
        ("separation", "结晶/分离设备", "锥底结晶釜、离心机"),
        ("product", "产品/包装", "流程终点"),
        ("waste", "废物/母液", "排放与收集终点"),
        ("analysis", "取样/分析点", "取样口、在线分析"),
        ("other", "其他单元", "通用单元操作"),
    )),
    ("管道附件（阀门）图形符号", (
        ("valve_gate", "闸阀", "两端阀座竖线，阀体闭合"),
        ("valve_globe", "截止阀", "阀体加中心圆"),
        ("valve_ball", "球阀", "阀体加中心圆与阀杆"),
        ("valve_butterfly", "蝶阀", "阀体加阀板竖线"),
        ("valve_check", "止回阀", "阀座线加流向箭头"),
        ("valve_control", "气动薄膜调节阀", "膜头闭合、阀杆与阀体相连"),
        ("valve_electric", "电动阀", "阀体加方框执行机构"),
        ("valve_safety", "安全阀", "阀体加弹簧与阀杆"),
        ("valve_regulator", "调压阀", "阀体加调压机构"),
    )),
    ("仪表图形符号", (
        ("instrument_local", "就地指示仪表", "现场压力表、温度计"),
        ("instrument_board", "集中指示仪表", "DCS/控制室显示"),
        ("instrument_shared", "共用显示仪表", "两地显示"),
        ("instrument_interlock", "联锁（SIS）", "方框内圆，联锁回路"),
    )),
)

CATALOG_NOTES: dict[str, str] = {
    kind: note for _group, items in CATALOG_GROUPS for kind, _name, note in items
}


def catalog_name(kind: str) -> str:
    for _group, items in CATALOG_GROUPS:
        for item_kind, name, _note in items:
            if item_kind == kind:
                return name
    return "其他单元"


INSTRUMENT_LETTERS: tuple[tuple[str, str, str], ...] = (
    ("P", "", "压力"), ("T", "", "温度"), ("F", "", "流量"), ("L", "", "液位"),
    ("A", "", "分析"), ("W", "", "重量"), ("S", "", "转速"), ("H", "", "手动"),
)

VALVE_LABELS: dict[str, str] = {
    "valve_gate": "闸阀", "valve_globe": "截止阀", "valve_ball": "球阀",
    "valve_butterfly": "蝶阀", "valve_check": "止回阀", "valve_control": "气动薄膜调节阀",
    "valve_electric": "电动阀", "valve_safety": "安全阀", "valve_regulator": "调压阀",
}

LEGEND_KINDS: tuple[str, ...] = tuple(
    kind for _group, items in CATALOG_GROUPS for kind, _name, _note in items
)


def instrument_kind(tag: str) -> str:
    """按位号前缀判定仪表符号类型（就地/集中/共用/联锁）。"""
    text = (tag or "").strip().upper()
    if "SIS" in text or "IL" in text or "联锁" in tag:
        return "instrument_interlock"
    if "DCS" in text or "C" in text[:3]:
        return "instrument_board"
    return "instrument_local"
