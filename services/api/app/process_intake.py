"""多模态工艺资料接入：工艺信息 -> AI 识别 -> 自动生成可编辑的概念 PFD 草稿。

设计人员不必再手工点节点：粘贴工艺描述、上传工艺文档（.txt/.md/.csv/.docx/.pdf）
或上传流程图照片 / 设备清单截图，由多模态模型识别出单元操作、物流、阀门仪表与参数，
自动排布成一张可以继续编辑的概念级流程图草稿。

诚实性约束（与 process_pfd 一致）：
* 只落资料里确实读到的值，每个参数都带来源片段；
* 读不到的内容进 unreadable 清单，绝不编造；
* 工程细节（管径、仪表回路定值、安全阀定压、设备强度、材质标准号）统一写 待补；
* 识别结果只是草稿，必须由设计人员复核后才能作为设计输入。

本模块只在本机副本运行；模型调用走环境变量里的密钥，不写入任何线上配置。
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from . import aspen_flowsheet
from . import process_pfd as _pfd
from .auth import UserContext, get_current_user, require_project_access
from .config import get_settings
from .database import get_db
from . import pfd_symbols as _symbols
from .models import AuditEvent, new_id

router = APIRouter(prefix="/api/v1/drug-discovery/projects/{project_id}/process/intake")

DISCLAIMER = _pfd.DISCLAIMER
PENDING = _pfd.PENDING
PENDING_SOURCE = _pfd.PENDING_SOURCE
AI_SOURCE = "AI 识别自上传资料，需人工核对原文"
NO_DATA_SOURCE = "资料中未读到，需人工补充"

MODEL_BASE = os.environ.get("INTAKE_MODEL_BASE", "https://api.siliconflow.cn/v1").rstrip("/")
MODEL_KEY_ENV = os.environ.get("INTAKE_MODEL_KEY_ENV", "SILICONFLOW_API_KEY")
TEXT_MODEL = os.environ.get("INTAKE_TEXT_MODEL", "deepseek-ai/DeepSeek-V4-Flash")
VISION_MODEL = os.environ.get("INTAKE_VL_MODEL", "deepseek-ai/DeepSeek-OCR")
OCR_MODEL = os.environ.get("INTAKE_OCR_MODEL", "deepseek-ai/DeepSeek-OCR")
VISION_MODE = os.environ.get("INTAKE_VISION_MODE", "auto").strip().lower()
MODEL_TIMEOUT = float(os.environ.get("INTAKE_TIMEOUT", "240"))

MAX_TEXT_CHARS = 24000
MAX_FILES = 12
MAX_IMAGES = 6
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_BYTES = 12 * 1024 * 1024
TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".log", ".yaml", ".yml", ".xml"}
DOC_SUFFIXES = {".docx", ".pdf"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
              ".webp": "image/webp", ".bmp": "image/bmp", ".gif": "image/gif"}

VALVE_KINDS = (
    "valve_gate", "valve_globe", "valve_ball", "valve_butterfly", "valve_check",
    "valve_control", "valve_electric", "valve_safety", "valve_regulator",
)
VALVE_TEXT = (
    "valve_gate=闸阀, valve_globe=截止阀, valve_ball=球阀, valve_butterfly=蝶阀, "
    "valve_check=止回阀, valve_control=气动调节阀, valve_electric=电动阀, "
    "valve_safety=安全阀, valve_regulator=减压/调压阀"
)
KIND_TEXT = "、".join(f"{key}={label}" for key, label in _pfd.KIND_LABELS.items())

KIND_ALIAS = {
    "reactor": "reactor", "反应器": "reactor", "反应釜": "reactor", "反应罐": "reactor", "cstr": "reactor",
    "pump": "pump", "泵": "pump", "离心泵": "pump", "计量泵": "pump", "真空泵": "pump",
    "column": "column", "塔": "column", "精馏塔": "column", "蒸馏塔": "column", "层析柱": "column", "填料塔": "column",
    "heat_exchanger": "heat_exchanger", "换热器": "heat_exchanger", "冷凝器": "heat_exchanger", "再沸器": "heat_exchanger",
    "vessel": "vessel", "储罐": "vessel", "缓冲罐": "vessel", "中间罐": "vessel", "罐": "vessel", "buffer tank": "vessel",
    "filter": "filter", "过滤器": "filter", "压滤机": "filter", "膜过滤": "filter",
    "dryer": "dryer", "干燥器": "dryer", "干燥机": "dryer", "真空干燥": "dryer",
    "compressor": "compressor", "压缩机": "compressor", "风机": "compressor", "鼓风机": "compressor",
    "feed": "feed", "进料": "feed", "计量罐": "feed", "投料": "feed", "原料罐": "feed",
    "product": "product", "产品": "product", "成品": "product", "灌装": "product",
    "waste": "waste", "废物": "waste", "母液": "waste", "废液": "waste", "残渣": "waste",
    "separation": "separation", "分离": "separation", "萃取": "separation", "结晶": "separation",
    "离心": "separation", "离心机": "separation", "浓缩": "separation", "提纯": "separation",
    "analysis": "analysis", "取样": "analysis", "分析": "analysis", "在线检测": "analysis",
    "other": "other", "其他": "other",
}

LEVEL_SPAN = 300.0
ROW_SPAN = 170.0
NODE_W = 180.0
NODE_H = 84.0
MAX_LEVELS = 12


def _clip(value: Any, limit: int, default: str = "") -> str:
    text = "" if value is None else str(value)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return default
    return text[:limit]


TAG_PLACEHOLDERS = {"未指定", "待补", "无", "未知", "不详", "空", "none", "n/a", "na", "-", "--", "—", "/"}


def _clean_tag(value: Any) -> str:
    """位号只保留真实位号；模型写的“未指定/无/N/A”这类占位词一律清空。"""
    text = _clip(value, 24)
    return "" if text.strip().lower() in TAG_PLACEHOLDERS else text


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "big5"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _docx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            target = "word/document.xml" if "word/document.xml" in names else next(
                (name for name in names if name.startswith("word/") and name.endswith(".xml")), None
            )
            if target is None:
                return ""
            xml = archive.read(target).decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, f"无法解析 Word 文档：{exc}") from exc
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:br[^>]*/>", "\n", xml)
    xml = re.sub(r"<w:tab[^>]*/>", "\t", xml)
    xml = re.sub(r"</w:tc>", "\t", xml)
    xml = re.sub(r"<[^>]+>", "", xml)
    return re.sub(r"\n{3,}", "\n\n", xml)


def _pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError:  # pragma: no cover - 本地副本已安装 pypdf
        raise HTTPException(503, "本机缺少 PDF 解析库 pypdf，无法读取 PDF 资料。")
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [(page.extract_text() or "") for page in reader.pages[:80]]
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, f"无法解析 PDF 文档：{exc}") from exc
    return "\n\n".join(pages)


def _extract_text(name: str, data: bytes) -> str:
    suffix = Path(name).suffix.lower()
    if suffix == ".docx":
        return _docx_text(data)
    if suffix == ".pdf":
        return _pdf_text(data)
    return _decode(data)


def _model_key() -> str:
    key = os.environ.get(MODEL_KEY_ENV, "").strip()
    if not key:
        raise HTTPException(503, f"本机未配置模型密钥（环境变量 {MODEL_KEY_ENV}），无法进行 AI 识别。")
    return key


RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
RETRY_DELAYS = (4.0, 10.0)


def _chat(messages: list[dict], model: str, max_tokens: int) -> tuple[str, dict]:
    """调用 OpenAI 兼容的 chat/completions；对限流与瞬时故障退避重试，避免识别白跑一趟。"""
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": max_tokens,
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {_model_key()}", "Content-Type": "application/json"}
    last_error = ""
    attempts = len(RETRY_DELAYS) + 1
    for attempt in range(attempts):
        if attempt:
            time.sleep(RETRY_DELAYS[attempt - 1])
        try:
            with httpx.Client(timeout=MODEL_TIMEOUT) as client:
                response = client.post(f"{MODEL_BASE}/chat/completions", json=payload, headers=headers)
        except httpx.HTTPError as exc:
            last_error = f"模型服务不可达（{MODEL_BASE}）：{exc}"
            continue
        if response.status_code in RETRY_STATUS and attempt < attempts - 1:
            last_error = f"模型服务暂时不可用（{response.status_code}）：{response.text[:200]}"
            continue
        if response.status_code >= 400:
            raise HTTPException(502, f"模型调用失败（{response.status_code}）：{response.text[:300]}")
        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise HTTPException(502, "模型返回结构异常，无法解析识别结果。") from exc
        return str(content or ""), dict(body.get("usage") or {})
    raise HTTPException(504, f"模型识别连续 {attempts} 次失败，请稍后重试。最后一次：{last_error}")


def _load_json(text: str) -> dict:
    raw = (text or "").strip()
    # 推理型模型（DeepSeek-V4 等）会把思维链混在 content 里，先切掉 </think 标记之前的部分。
    marker = raw.rfind("</think")
    if marker >= 0:
        close = raw.find(">", marker)
        if close >= 0:
            raw = raw[close + 1:].strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", raw, re.S)
    if fence:
        raw = fence.group(1).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        raw = raw[start:end + 1]
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(502, f"模型未返回可解析的 JSON 识别结果（{exc.msg}），请重试或换用更清晰的资料。") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(502, "模型返回的识别结果不是 JSON 对象。")
    return parsed


# 「一次问清」用的固定输入清单，分两级：
# key = 图上要用、缺了直接影响成图；detail = 详细设计资料，图上按「待补」占位，
# 缺了也照样出图，只在资料里确实看得出缺口时才列出来。资料里没给的要一次列全。
INPUT_CHECKLIST: tuple[dict[str, str], ...] = (
    {"key": "equipment_tags", "level": "key", "label": "单元位号",
     "need": "每台设备/单元的位号（如 R-101、T-102）", "how": "设备一览表，或带位号的流程图"},
    {"key": "topology", "level": "key", "label": "物流连接关系",
     "need": "每条管线从哪台设备接到哪台设备", "how": "带连线的 PFD/P&ID，或逐段写明起点终点的文字描述"},
    {"key": "flows", "level": "key", "label": "物料流量",
     "need": "每股物流的流量与单位（kg/h、t/a 等）", "how": "物料平衡表，或工艺描述里的流量数据"},
    {"key": "composition", "level": "detail", "label": "物料组成与浓度",
     "need": "关键物流的组成、浓度、纯度（wt%、mol%）", "how": "物料表，或化验/分析数据"},
    {"key": "conditions", "level": "key", "label": "操作温度与压力",
     "need": "各单元的操作温度与操作压力", "how": "工艺条件表，或操作规程里的操作条件"},
    {"key": "heat_media", "level": "detail", "label": "逐段温度与加热/冷却介质",
     "need": "每段物料要维持多少温度、该段走蒸汽/导热油还是冷却水/冷冻水、是否需要伴热保温",
     "how": "工艺条件表里的温度控制要求，或换热计算书/规程里的冷却或加热说明"},
    {"key": "signals", "level": "detail", "label": "仪表信号回路",
     "need": "每个调节阀由哪个仪表控制、信号是电信号/气信号还是联锁",
     "how": "仪表回路图、联锁逻辑说明或控制室位号表"},
    {"key": "valves", "level": "detail", "label": "阀门类型与位号",
     "need": "管线上阀门的类型（闸阀/球阀/调节阀…）与位号", "how": "带阀门符号的流程图，或阀门一览表"},
    {"key": "pipe_class", "level": "detail", "label": "管道等级与管径",
     "need": "管道等级代号、公称直径与材质", "how": "管道材料等级表（C1F/C2F/A2 这类代号要有定义）"},
    {"key": "instruments", "level": "detail", "label": "仪表位号与量程",
     "need": "仪表位号、测量范围，以及回路定值（PID 参数、联锁值）", "how": "仪表一览表、回路图或联锁逻辑说明"},
    {"key": "materials", "level": "detail", "label": "设备与管道材质",
     "need": "设备/管道材质标准号与腐蚀裕量", "how": "设备数据表，或材质选择与腐蚀评估说明"},
    {"key": "utilities", "level": "detail", "label": "公用工程条件",
     "need": "蒸汽/冷却水/氮气/冷冻水等公用工程的规格与接入点", "how": "公用工程条件表"},
    {"key": "thermo", "level": "detail", "label": "物性与反应热数据",
     "need": "反应热、比热、密度等物性数据（热量与换算要用）", "how": "物性手册数据，或实验测定报告"},
    {"key": "relief", "level": "detail", "label": "安全泄放与联锁",
     "need": "安全阀定压、泄放去向、联锁与紧急切断逻辑", "how": "安全阀数据表，或 HAZOP/联锁说明"},
    {"key": "waste", "level": "detail", "label": "三废与去向",
     "need": "废液/废气/残渣的去向与处理方式", "how": "三废处理说明"},
    {"key": "balance_gap", "level": "key", "label": "物料守恒差额去向",
     "need": "进出料对不上的差额由哪股物料带出", "how": "物料平衡表（含排污、放空、取样留样）"},
)
CHECKLIST_KEYS: dict[str, dict[str, str]] = {item["key"]: item for item in INPUT_CHECKLIST}
CHECKLIST_TEXT = "；".join(f"{item['key']}={item['label']}" for item in INPUT_CHECKLIST)
CHECKLIST_BRIEF = "、".join(item["label"] for item in INPUT_CHECKLIST)

SYSTEM_PROMPT = (
    "你是一名资深化工工艺设计工程师，负责把工艺资料整理成概念级工艺流程图（PFD）的结构化数据。\n"
    "你只输出一个 JSON 对象，不要输出解释、前后缀或 Markdown 说明。\n\n"
    "硬性规则：\n"
    "1. 只记录资料里确实写到的信息。资料没写的一律不要猜，写进 unreadable 数组。\n"
    "2. 每个参数都必须给 source 字段，写清依据（原文片段、表格名或图示位置），source 不得为空。\n"
    "3. 工程细节（管径、仪表回路定值、安全阀定压、设备强度、材质标准号）不要编造："
    "value 写 \"待补\"，source 写 \"需合格工程人员设计\"。\n"
    "4. 单位必须明确写出；数值保持资料原文的量级与单位，不要换算。\n"
    "5. 阀门、仪表画在管线上，不要建成节点：用 streams[].components 放阀门，streams[].instruments 放仪表位号。\n"
    "6. 某个单元进料与出料对不上时（物料不守恒），把差额的去向写进该单元的 outlets，"
    "例如底部排污 50 kg/h；资料没写去向就进 unreadable。\n"
    "7. 不要输出资料以外的设备、物流或数值。\n"
    "8. 除进料源与最终产品/废物外，每个单元都必须同时有进料连线和出料连线，不要让物料在半路断掉。\n"
    "9. 资料没写位号就留空 tag，不要填 \"未指定\"、\"无\"、\"N/A\" 这类占位词。\n"
    "10. value 只写数值本身，单位放 unit 字段，不要在 value 里重复单位。\n"
    "11. 终点单元的 kind 要按性质选：产品/成品/包装外送用 product；废液罐、母液罐、废水罐、"
    "放空/尾气、残渣用 waste。不要把这些出口写成 vessel，否则全流程物料衡算会算不出出口。\n"
    "12. 资料不全是常态，**有什么画什么**：只要读到单元名称就先建节点，"
    "读到先后顺序或连接关系就先连管线；缺位号、缺流量、缺操作条件都照画，"
    "绝不要因为「资料不全」就返回空的 units。\n"
    "14. 公用工程与工艺物料分开记：蒸汽、导热油、冷却水、冷冻水、载冷盐水、氮气、仪表空气、"
    "压缩空气、工艺水、电加热/电伴热这类介质，接在哪个单元上就写进该单元的 utilities，"
    "medium 用固定代号，service 写规格与温度（例：0.6 MPa 饱和蒸汽 158 ℃）；"
    "如果资料把它写成一整股物料，就照样建一条物流线，media 写 utility，service 写介质与规格。\n"
    "15. 逐段温度要求要看懂：资料说「某段要维持 xx ℃」、「用冷却水降温」、「用蒸汽加热」、"
    "「需要伴热/保温」，就把温度、介质与依据分别写进该单元的 utilities/参数里；"
    "资料没给的温度或介质用量一律不要猜，写进 required_inputs（key 用 heat_media）。\n"
    "16. 安全阀组、放空、排空、排污要有依据才画：写进该单元的 reliefs，"
    "kind 选 safety_valve / vent / drain / rupture_disc，to 写泄放或排放去向（放空、火炬、回收罐、"
    "地沟），定压或口径资料没给就 value 写 \"待补\"、source 写 \"需合格工程人员设计\"；"
    "资料里完全没提安全泄放就不要写 reliefs，并在 required_inputs（key 用 relief）里问一次。\n"
    "17. 调节阀由哪个仪表控制、走电信号还是气信号，写进该条物流的 signals"
    "（tag 写仪表位号，target 写执行机构位号，kind 写 electric / pneumatic / interlock）；"
    "资料没写信号形式就把 kind 留空，不要猜。\n"
    "13. required_inputs 只写资料里没给、而图上要用到的输入：status 写 \"需补充\"，"
    "detail 写清缺什么，impact 写清影响图上哪一处，how 写清建议补什么资料；"
    "资料里已经给全的不要写进来，也不必逐条汇报。"
    "能想到的缺口一次写完，不要留到下一轮再问。"
    "unreadable 只放读不清、有歧义、图片没转出来的内容，不要和 required_inputs 重复。\n"
    f"图纸常用输入的固定清单（key=名称，required_inputs[].key 只能从这里选）：{CHECKLIST_TEXT}\n"
)

SCHEMA_HINT = (
    "输出 JSON 结构：\n"
    "{\n"
    "  \"title\": \"图纸标题（例如：某产品 反应-精制工段 概念 PFD）\",\n"
    "  \"units\": [{\n"
    "    \"id\": \"U1\",\n"
    "    \"label\": \"反应器 R-101\",\n"
    "    \"kind\": \"reactor\",\n"
    "    \"tag\": \"R-101\",\n"
    "    \"params\": [{\"name\": \"进料流量\", \"value\": \"500\", \"unit\": \"kg/h\", \"source\": \"原文：进料流量 500 kg/h\"}],\n"
    "    \"notes\": [\"风险或待确认事项\"],\n"
    "    \"utilities\": [{\"medium\": \"cooling_water\", \"service\": \"32 ℃ 循环冷却水上水/回水\", "
    "\"value\": \"12\", \"unit\": \"t/h\", \"port\": \"e\", \"direction\": \"in\", "
    "\"source\": \"原文：塔顶全凝器用循环冷却水冷却\"}],\n"
    "    \"reliefs\": [{\"kind\": \"safety_valve\", \"tag\": \"PSV-101\", \"value\": \"0.6\", "
    "\"unit\": \"MPa\", \"to\": \"放空\", \"source\": \"原文：塔顶设安全阀，定压 0.6 MPa\"}],\n"
    "    \"outlets\": [{\"name\": \"底部排污\", \"value\": \"50\", \"unit\": \"kg/h\", \"source\": \"原文：底部排污 50 kg/h\"}]\n"
    "  }],\n"
    "  \"streams\": [{\n"
    "    \"id\": \"S1\",\n"
    "    \"source\": \"U1\",\n"
    "    \"target\": \"U2\",\n"
    "    \"label\": \"反应液\",\n"
    "    \"media\": \"material\",\n"
    "    \"service\": \"介质与规格/温度（公用工程线才填，例：0.6 MPa 饱和蒸汽 158 ℃）\",\n"
    "    \"signals\": [{\"tag\": \"FIC-101\", \"target\": \"FV-101\", \"kind\": \"electric\", "
    "\"source\": \"原文：进料流量由 FIC-101 控制 FV-101\"}],\n"
    "    \"params\": [{\"name\": \"流量\", \"value\": \"500\", \"unit\": \"kg/h\", \"source\": \"原文：进料 500 kg/h\"}],\n"
    "    \"components\": [{\"kind\": \"valve_control\", \"tag\": \"FV-101\"}],\n"
    "    \"instruments\": [\"PIC-101\"]\n"
    "  }],\n"
    "  \"unreadable\": [\"看不清或资料没有写明的信息\"],\n"
    "  \"required_inputs\": [{\"key\": \"pipe_class\", \"status\": \"需补充\", "
    "\"detail\": \"C1F/C2F/A2 只给了代号，没有给等级表定义\", \"impact\": \"管线上无法标管道等级\", "
    "\"how\": \"请提供本单位管道材料等级表\"}],\n"
    "  \"notes\": [\"图纸整体说明\"]\n"
    "}\n\n"
    f"units[].kind 只能从这个列表里选：{KIND_TEXT}。\n"
    f"streams[].components[].kind 只能从这个列表里选：{VALVE_TEXT}。\n"
    "streams[].source / streams[].target 必须等于某个 units[].id；"
    "media 为 material（物料）或 utility（公用工程/蒸汽/冷却水等）。\n"
    f"utilities[].medium 与 stream.media 的公用工程代号只能从这个列表里选：{_symbols.MEDIA_TEXT}。\n"
    "units[].reliefs[].kind 只能选 safety_valve（安全阀组）/ vent（放空）/ drain（排空排污）/ "
    "rupture_disc（爆破片）；reliefs 只写资料里有依据的，定压/口径没给就写 \"待补\"。\n"
    "streams[].signals[].kind 只能选 electric（电信号）/ pneumatic（气信号）/ interlock（联锁），"
    "资料没写就留空字符串。\n"
    "required_inputs 只写资料里没给、而图上要用到的输入（图上会按「待补」占位）；"
    f"图上已经有的不要再列。required_inputs[].key 只能从这个清单里选：{CHECKLIST_TEXT}。\n"
)


def _vision_mode() -> str:
    """图片怎么读：ocr = 先转文字再结构化；direct = 直接把图片交给视觉模型。"""
    mode = VISION_MODE if VISION_MODE in ("auto", "ocr", "direct") else "auto"
    if mode == "auto":
        return "ocr" if "ocr" in VISION_MODEL.lower() else "direct"
    return mode


def _merge_usage(*buckets: dict) -> dict:
    merged: dict[str, float] = {}
    for bucket in buckets:
        for key, value in (bucket or {}).items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                merged[key] = merged.get(key, 0) + value
    return merged


OCR_PROMPT = "请把这张图片里能看清的文字逐行转录出来，保持原有阅读顺序，表格用制表符分隔。不要解释，不要输出 JSON。"


def _usable_ocr(text: str) -> bool:
    """挡掉 OCR 模型的退化输出（整段只重复同一个符号）。

    DeepSeek-OCR 是纯转录模型：一旦要求它描述管线连接或输出结构化内容，
    就会退化成「}}}}}}」这类无意义字符串。这种结果直接丢掉，不往图里灌。
    """
    stripped = re.sub(r"\s+", "", text or "")
    if len(stripped) < 8:
        return False
    counts: dict[str, int] = {}
    for char in stripped:
        counts[char] = counts.get(char, 0) + 1
    if len(stripped) > 200 and max(counts.values()) / len(stripped) > 0.2:
        return False
    if len(stripped) > 200 and len(re.findall(r"[\u4e00-\u9fff]", stripped)) < 5:
        return False
    return True


def _ocr_images(picture_parts: list[dict], image_names: list[str]) -> tuple[list[str], dict]:
    """把图片转成文字。

    DeepSeek 系列的对话模型是纯文本的，读图要用 deepseek-ai/DeepSeek-OCR；
    转出的文字再交给文本模型做结构化，图片和文字最终合成同一张图。
    """
    blocks: list[str] = []
    usage: dict = {}
    for index, part in enumerate(picture_parts, 1):
        content, used = _chat(
            [{"role": "user", "content": [{"type": "text", "text": OCR_PROMPT}, part]}],
            OCR_MODEL,
            4096,
        )
        usage = _merge_usage(usage, used)
        text = str(content or "").strip()
        name = image_names[index - 1] if index - 1 < len(image_names) else f"图片{index}"
        if _usable_ocr(text):
            blocks.append(f"【图片 {index} {name}（OCR 转录）】\n{text}")
        else:
            blocks.append(
                f"【图片 {index} {name}】本机 OCR 没有转出可用文字（或输出已退化），"
                "这张图的内容视为读不清，请写进 unreadable。"
            )
    return blocks, usage


def _node_kind(raw: dict) -> str:
    kind = _clip(raw.get("kind"), 40).lower()
    if kind in _pfd.KIND_LABELS:
        return kind
    if kind in KIND_ALIAS:
        return KIND_ALIAS[kind]
    return _pfd._kind_for(_clip(raw.get("label") or raw.get("name"), 80))


def _build_param(raw: Any, fallback: str = AI_SOURCE) -> dict | None:
    if not isinstance(raw, dict):
        return None
    name = _clip(raw.get("name"), 40)
    if not name:
        return None
    value = _clip(raw.get("value"), 60, PENDING)
    unit = _clip(raw.get("unit"), 20)
    # 模型常把单位写进 value（例如 value=92% unit=%），这里去掉重复，避免图上出现“92% %”。
    if unit and value.endswith(unit) and value != unit:
        value = value[: -len(unit)].strip() or PENDING
    return {
        "name": name,
        "value": value,
        "unit": unit,
        "source": _clip(raw.get("source") or raw.get("evidence") or raw.get("quote"), 160) or fallback,
    }


def _graph_input_signals(graph: dict, balance: dict | None = None) -> tuple[dict[str, str], set[str]]:
    """从排好的图里确定性看得出来的缺口，以及图上已经用到的关键输入。

    返回 (缺口, 已覆盖)：缺口是要提示人工补的，已覆盖的说明图上已经用到了，
    不必再问一遍。
    """
    gaps: dict[str, str] = {}
    covered: set[str] = set()
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    if not nodes:
        return gaps, covered

    untagged = [node for node in nodes if not str(node.get("tag") or "").strip()]
    if untagged:
        gaps["equipment_tags"] = f"有 {len(untagged)} 个单元没有位号，图上只能显示名称。"
    else:
        covered.add("equipment_tags")

    in_degree = {node["id"]: 0 for node in nodes}
    out_degree = {node["id"]: 0 for node in nodes}
    for edge in edges:
        if edge.get("source") in out_degree:
            out_degree[edge["source"]] += 1
        if edge.get("target") in in_degree:
            in_degree[edge["target"]] += 1
    dangling = [node for node in nodes
                if (in_degree[node["id"]] == 0 and node.get("kind") != "feed")
                or (out_degree[node["id"]] == 0
                    and node.get("kind") not in ("product", "waste", "analysis"))]
    if edges and not dangling:
        covered.add("topology")
    elif dangling:
        gaps["topology"] = (f"有 {len(dangling)} 个单元的进出料管线没有接全，"
                            "图上按现有连接关系画，缺的那段要人工确认。")

    material = [edge for edge in edges if str(edge.get("media") or "material") != "utility"]
    if material:
        missing_flow = [edge for edge in material if _pfd._edge_flow(edge) is None]
        if missing_flow:
            gaps["flows"] = (f"有 {len(missing_flow)} 条物流在资料里没有流量数值，"
                             f"图上按「{PENDING}」占位。")
        else:
            covered.add("flows")

    conditions = [param for node in nodes for param in (node.get("params") or [])
                  if any(word in str(param.get("name") or "") for word in ("温度", "压力"))]
    if conditions:
        covered.add("conditions")
    else:
        gaps["conditions"] = f"资料里没有给出各单元的操作温度与压力，图上按「{PENDING}」占位。"

    if edges and not any(edge.get("components") for edge in edges):
        gaps["valves"] = "资料里没有给出管线上的阀门位置与类型，图上只有管线本身。"
    if edges and not any(edge.get("instruments") for edge in edges):
        gaps["instruments"] = "资料里没有给出仪表位号与量程，管线上没有仪表标注。"

    if any(node.get("utilities") for node in nodes) or any(
            str(edge.get("media") or "material") == "utility" for edge in edges):
        covered.add("utilities")
    if any(node.get("reliefs") for node in nodes):
        covered.add("relief")
    if any(str(edge.get("service") or "").strip() for edge in edges):
        covered.add("heat_media")
    if any(edge.get("signals") for edge in edges):
        covered.add("signals")

    status = (balance or {}).get("status")
    if status == "不平衡":
        gaps["balance_gap"] = ("全流程进出口对不上，差额的去向资料里没写，图上只能留空；"
                               "需要说明这部分是从底部排污、放空、母液还是取样带走的。")
    elif status == "闭合":
        covered.add("balance_gap")

    return gaps, covered


def _build_required_inputs(parsed: dict, graph: dict, balance: dict | None = None) -> list[dict]:
    """列出这份资料里没给、而图上要用到的输入，并按两级给出。

    key 级（位号 / 连接关系 / 流量 / 操作条件 / 差额去向）缺了会直接影响图面，一定提示；
    detail 级（管径、材质、仪表定值、安全阀、三废…）属于详细设计，图上已按「待补」占位，
    只在资料里确实看得出缺口、或模型明确说明缺的时候才列。这样缺什么都可以先出图，
    出图之后再告诉设计人员还缺哪些资料。
    """
    reported: dict[str, dict] = {}
    for item in (parsed.get("required_inputs") or []):
        if not isinstance(item, dict):
            continue
        key = _clip(item.get("key") or item.get("category"), 40).lower()
        if key not in CHECKLIST_KEYS:
            label = _clip(item.get("label") or item.get("category"), 40)
            key = next((k for k, spec in CHECKLIST_KEYS.items() if label and label == spec["label"]), "")
        if key:
            reported.setdefault(key, item)

    gaps, covered = _graph_input_signals(graph, balance)
    required: list[dict] = []
    for spec in INPUT_CHECKLIST:
        key = spec["key"]
        level = spec.get("level", "detail")
        raw = reported.get(key) or {}
        status = _clip(raw.get("status"), 12)
        detail = _clip(raw.get("detail"), 200)
        if key in gaps:
            state = "图上缺值"
            fallback = gaps[key]
        elif status in ("已给全", "已覆盖", "已齐全", "齐全"):
            continue
        elif key in covered:
            continue
        elif status:
            state = "资料未提供"
            fallback = f"这份资料里没有给出：{spec['need']}。"
        elif level == "key":
            state = "待确认"
            fallback = f"这份资料里没有明确给出：{spec['need']}；如果已经有，请一并提供。"
        else:
            # 详细设计资料：图上已按「待补」占位，不影响出图，不在这里打扰设计人员
            continue
        required.append({
            "key": key, "level": level,
            "category": spec["label"], "status": state,
            "detail": detail or fallback,
            "impact": _clip(raw.get("impact"), 120) or spec["need"],
            "how": _clip(raw.get("how"), 160) or spec["how"],
        })
    return required


def _build_graph(payload: dict) -> tuple[dict, list[str], list[str]]:
    """把模型返回的 JSON 规整成 process_pfd.PfdGraph 能接受的图，并自动排布坐标。"""
    warnings: list[str] = []
    raw_units = payload.get("units") or payload.get("nodes") or []
    raw_streams = payload.get("streams") or payload.get("edges") or []
    if not isinstance(raw_units, list):
        raw_units = []
    if not isinstance(raw_streams, list):
        raw_streams = []

    id_map: dict[str, str] = {}
    nodes: list[dict] = []
    for raw in raw_units[:120]:
        if not isinstance(raw, dict):
            continue
        label = _clip(raw.get("label") or raw.get("name"), 80)
        if not label:
            continue
        node_id = f"n{len(nodes) + 1}"
        for alias in (raw.get("id"), raw.get("tag"), raw.get("label")):
            key = _clip(alias, 60)
            if key:
                id_map.setdefault(key, node_id)
        params = [item for item in (_build_param(entry) for entry in (raw.get("params") or [])[:12]) if item]
        if not params:
            params = [{"name": "设备参数", "value": PENDING, "unit": "", "source": NO_DATA_SOURCE}]
        notes = [text for text in (_clip(entry, 80) for entry in (raw.get("notes") or [])[:6]) if text]
        outlets: list[dict] = []
        for entry in (raw.get("outlets") or [])[:6]:
            if not isinstance(entry, dict):
                continue
            name = _clip(entry.get("name"), 40)
            if not name:
                continue
            outlets.append({
                "name": name,
                "value": _clip(entry.get("value"), 40, PENDING),
                "unit": _clip(entry.get("unit"), 20, "kg/h"),
                "source": _clip(entry.get("source"), 160) or AI_SOURCE,
            })
        utilities: list[dict] = []
        for entry in (raw.get("utilities") or [])[:8]:
            if not isinstance(entry, dict):
                continue
            medium = _clip(entry.get("medium"), 40).lower()
            service = _clip(entry.get("service"), 80)
            if not medium and not service:
                continue
            port = _clip(entry.get("port"), 4).lower()
            direction = _clip(entry.get("direction"), 4).lower()
            utilities.append({
                "medium": medium if medium in _symbols.MEDIA_LABELS else "",
                "service": service,
                "value": _clip(entry.get("value"), 40, PENDING),
                "unit": _clip(entry.get("unit"), 20),
                "port": port if port in ("w", "e", "n", "s") else "",
                "direction": direction if direction in ("in", "out") else "",
                "source": _clip(entry.get("source") or entry.get("evidence"), 160) or AI_SOURCE,
            })
        reliefs: list[dict] = []
        for entry in (raw.get("reliefs") or [])[:6]:
            if not isinstance(entry, dict):
                continue
            kind = _clip(entry.get("kind"), 24).lower()
            if kind not in ("safety_valve", "vent", "drain", "rupture_disc"):
                kind = "safety_valve"
            tag = _clean_tag(entry.get("tag"))
            to = _clip(entry.get("to"), 40)
            source = _clip(entry.get("source") or entry.get("evidence"), 160)
            if not (tag or to or source):
                continue
            value = _clip(entry.get("value"), 40, PENDING)
            reliefs.append({
                "kind": kind,
                "tag": tag,
                "value": value,
                "unit": _clip(entry.get("unit"), 20) if value != PENDING else "",
                "to": to,
                "source": source or (PENDING_SOURCE if value == PENDING else AI_SOURCE),
            })
        nodes.append({
            "id": node_id,
            "kind": _node_kind(raw),
            "tag": _clean_tag(raw.get("tag")),
            "label": label,
            "x": 0.0,
            "y": 0.0,
            "w": NODE_W,
            "h": NODE_H,
            "params": params,
            "notes": notes,
            "outlets": outlets,
            "utilities": utilities,
            "reliefs": reliefs,
        })

    if not nodes:
        raise HTTPException(
            422,
            "这次资料里没有读到任何单元操作（设备/单元）。把资料再写具体一点就能出图，"
            "例如「原料经计量泵送反应器，反应液去精馏塔」；只要有单元名称，"
            "哪怕没有位号、流量和管线连接，也会先画一张草图，缺的部分在图上标「待补」。",
        )

    edges: list[dict] = []
    for raw in raw_streams[:400]:
        if not isinstance(raw, dict):
            continue
        source_key = _clip(raw.get("source") or raw.get("from"), 60)
        target_key = _clip(raw.get("target") or raw.get("to"), 60)
        flow_hint = f"{_clip(raw.get('label'), 80)} {_clip(raw.get('name'), 80)}".strip()
        source_id = id_map.get(source_key) or _placeholder(nodes, id_map, source_key, "起点", warnings, flow_hint)
        target_id = id_map.get(target_key) or _placeholder(nodes, id_map, target_key, "终点", warnings, flow_hint)
        if not source_id or not target_id:
            warnings.append(f"连线 {source_key or '?'} → {target_key or '?'} 缺少起止单元名称，已跳过。")
            continue
        if source_id == target_id:
            warnings.append(f"连线 {source_key} 起止为同一单元，已跳过。")
            continue
        params = [item for item in (_build_param(entry) for entry in (raw.get("params") or [])[:8]) if item]
        if not params:
            params = [{"name": "物流数据", "value": PENDING, "unit": "", "source": NO_DATA_SOURCE}]
        components: list[dict] = []
        for entry in (raw.get("components") or [])[:6]:
            kind = _clip(entry.get("kind") if isinstance(entry, dict) else entry, 40).lower()
            if kind not in VALVE_KINDS:
                continue
            tag = _clean_tag(entry.get("tag")) if isinstance(entry, dict) else ""
            components.append({"kind": kind, "tag": tag})
        instruments = [text for text in (_clip(entry, 24) for entry in (raw.get("instruments") or [])[:4]) if text]
        signals: list[dict] = []
        for entry in (raw.get("signals") or [])[:6]:
            if not isinstance(entry, dict):
                continue
            tag = _clip(entry.get("tag"), 24)
            if not tag:
                continue
            kind = _clip(entry.get("kind"), 20).lower()
            signals.append({
                "tag": tag,
                "target": _clean_tag(entry.get("target")),
                "kind": kind if kind in ("electric", "pneumatic", "interlock") else "",
                "source": _clip(entry.get("source") or entry.get("evidence"), 160) or AI_SOURCE,
            })
        label = _clip(raw.get("label"), 80) or params[0]["name"]
        edges.append({
            "id": f"e{len(edges) + 1}",
            "source": source_id,
            "target": target_id,
            "label": label,
            "params": params,
            "media": "utility" if _clip(raw.get("media"), 20).lower() == "utility" else "material",
            "components": components,
            "instruments": instruments,
            "service": _clip(raw.get("service"), 80),
            "signals": signals,
        })

    _dedupe_outlets(nodes, edges)
    _finalize_kinds(nodes, edges)
    warnings.extend(_structure_warnings(nodes, edges))
    _layout(nodes, edges)
    return {"nodes": nodes, "edges": edges}, warnings, [node["id"] for node in nodes]


def _dedupe_outlets(nodes: list[dict], edges: list[dict]) -> None:
    """差额去向只在“图上没有画这条物流线”时才计入，避免同一股物料被算两遍。

    模型经常既把底部排污写成单元 outlets，又把它画成一条去废水罐的物流线；
    process_pfd._material_balance 会把两者都算进该单元出料，全流程就凭空多出一份。
    这里把已经画成物流线、且流量完全对得上的 outlets 去掉。
    """
    drawn: dict[str, list[tuple[str, float]]] = {}
    for edge in edges:
        found = _pfd._edge_flow(edge)
        if found is not None:
            drawn.setdefault(edge["source"], []).append(found)
    for node in nodes:
        outlets = node.get("outlets") or []
        if not outlets:
            continue
        keep: list[dict] = []
        for outlet in outlets:
            found = _pfd._parse_flow(outlet)
            if found is not None and found in drawn.get(node["id"], []):
                continue
            keep.append(outlet)
        node["outlets"] = keep


SYNTHETIC_SOURCE = "资料未给出该单元，由连线反推生成，请人工确认"
PLACEHOLDER_MARKS = ("放空", "排空", "排污", "排放", "尾气", "废气", "废液", "废水", "废渣",
                     "残渣", "母液", "污泥", "火炬")


def _placeholder(nodes: list[dict], id_map: dict[str, str], key: str, role: str,
                 warnings: list[str], hint: str = "") -> str:
    """连线指向了资料里没有定义的单元时补一个占位单元，不要把这条线丢掉。

    丢掉一条线会连带把该单元的出料抹掉，物料守恒就会出现假差额；补成占位单元
    后，图面与物衡都能对上，人工一眼看到哪里没识别到。
    """
    if not key:
        return ""
    node_id = f"n{len(nodes) + 1}"
    text = f"{key} {hint}"
    if role == "起点":
        # 连线的起点在资料里没有定义，它必然是一股物料的来源，按进料源处理。
        kind = "feed"
    elif any(mark in text for mark in PLACEHOLDER_MARKS):
        # 终点看不出是什么时，用这股物流的名字判断它是排放口还是普通单元。
        kind = "waste"
    else:
        kind = "other"
    nodes.append({
        "id": node_id,
        "kind": kind,
        "tag": "",
        "label": f"未识别单元（{key}）"[:80],
        "x": 0.0,
        "y": 0.0,
        "w": NODE_W,
        "h": NODE_H,
        "params": [{"name": "设备参数", "value": PENDING, "unit": "", "source": SYNTHETIC_SOURCE}],
        "notes": [f"资料里没有“{key}”这个单元，是按连线反推补出来的占位，请人工补全或删除。"],
        "outlets": [],
        "synthetic": True,
    })
    id_map.setdefault(key, node_id)
    warnings.append(f"连线里的“{key}”在资料中没有对应单元，已按“未识别单元（{key}）”补成{role}，请人工确认。")
    return node_id


WASTE_MARKS = ("废", "母液", "残液", "排污", "排放", "放空", "尾气", "废渣", "污泥")
PRODUCT_MARKS = ("产品", "成品", "包装", "出厂", "罐装")


def _finalize_kinds(nodes: list[dict], edges: list[dict]) -> None:
    """终点单元按名称归到 产品/废物。

    物衡表的“全流程出口”只统计 product / waste 两类单元（见 process_pfd._material_balance），
    模型常把废液罐、母液罐、放空口写在 vessel 里，会让全流程校核出现假差额。
    这里只对“没有出料连线”的终点单元按名称纠正，不动流程中间的设备。
    """
    out_degree = {node["id"]: 0 for node in nodes}
    for edge in edges:
        if edge["source"] in out_degree:
            out_degree[edge["source"]] += 1
    for node in nodes:
        if out_degree[node["id"]] or node["kind"] in ("product", "waste", "analysis", "feed"):
            continue
        label = str(node.get("label") or "")
        if any(mark in label for mark in WASTE_MARKS):
            node["kind"] = "waste"
        elif any(mark in label for mark in PRODUCT_MARKS):
            node["kind"] = "product"


def _structure_warnings(nodes: list[dict], edges: list[dict]) -> list[str]:
    """指出识别结果里断掉的流程：单元缺进料或出料连线，物料守恒就不可能闭合。"""
    in_degree = {node["id"]: 0 for node in nodes}
    out_degree = {node["id"]: 0 for node in nodes}
    for edge in edges:
        if edge["source"] in out_degree:
            out_degree[edge["source"]] += 1
        if edge["target"] in in_degree:
            in_degree[edge["target"]] += 1
    problems: list[str] = []
    for node in nodes:
        name = f'{node.get("tag") or ""} {node.get("label") or ""}'.strip()
        if in_degree[node["id"]] == 0 and node["kind"] != "feed":
            problems.append(f"{name} 没有进料连线，请确认上游设备或物料来源是否漏识别。")
        if out_degree[node["id"]] == 0 and node["kind"] not in ("product", "waste", "analysis"):
            problems.append(f"{name} 没有出料连线，请确认下游去向（产品或废物）是否漏识别。")
    return problems[:8]


def _layout(nodes: list[dict], edges: list[dict]) -> None:
    """按连接关系做分层排布：主物料从左到右，废物/排放走在最下面一排。

    工艺流程里常有回路（例如“凝液后返回反应器”）。最长路径分层在有环时不会收敛，
    会一路把层级抬到上限，最后所有单元挤进同一列、图形重叠。这里先按拓扑序断掉回边再分层，
    并把空列压掉；同一列内再按上游行号对齐，让主物料线尽量走成一条直线。
    """
    known = [node["id"] for node in nodes]
    index = {node_id: position for position, node_id in enumerate(known)}
    outgoing: dict[str, list[str]] = {node_id: [] for node_id in known}
    seen: set[tuple[str, str]] = set()
    for edge in edges:
        source, target = edge.get("source"), edge.get("target")
        if source not in index or target not in index or source == target:
            continue
        if (source, target) in seen:
            continue
        seen.add((source, target))
        outgoing[source].append(target)

    indegree = {node_id: 0 for node_id in known}
    for targets in outgoing.values():
        for target in targets:
            indegree[target] += 1

    ready = [node_id for node_id in known if indegree[node_id] == 0]
    remaining = set(known)
    sequence: list[str] = []
    while remaining:
        if not ready:
            # 剩下的就是回路：先放行入度最小的单元，等于断掉它还没处理的入边。
            ready.append(min(remaining, key=lambda node_id: (indegree[node_id], index[node_id])))
        node_id = ready.pop(0)
        if node_id not in remaining:
            continue
        remaining.discard(node_id)
        sequence.append(node_id)
        for target in outgoing[node_id]:
            indegree[target] -= 1
            if indegree[target] == 0 and target in remaining:
                ready.append(target)

    rank = {node_id: position for position, node_id in enumerate(sequence)}
    # 互为进出料的两台设备（如反应器与顶置冷凝器）画在同一列、上下叠放：
    # 这种「去程 + 回程」的回路就近成对画，管线才不会绕到别的设备底下去。
    paired = {node_id: set() for node_id in known}
    for node_id, targets in outgoing.items():
        for target in targets:
            if node_id in outgoing.get(target, ()):
                paired[node_id].add(target)
                paired[target].add(node_id)
    level = {node_id: 0 for node_id in known}
    for node_id in sequence:
        for target in outgoing[node_id]:
            if rank[target] <= rank[node_id]:
                continue
            step = 0 if target in paired[node_id] else 1
            level[target] = max(level[target], level[node_id] + step)
    # 支线尽量靠下游：催化剂泵这类没有上游连线的支线往右挪到紧靠合流设备的那一列，
    # 支管短，也和主物料线上的同类设备对齐，不会从图纸最左边拉出一条长支线。
    for node_id in reversed(sequence):
        forward = [target for target in outgoing[node_id] if rank[target] > rank[node_id]]
        if forward:
            level[node_id] = max(level[node_id], min(level[target] for target in forward) - 1)
    squeeze = {value: position for position, value in enumerate(sorted(set(level.values())))}
    level = {node_id: squeeze[value] for node_id, value in level.items()}

    parents: dict[str, list[str]] = {node_id: [] for node_id in known}
    for source, targets in outgoing.items():
        for target in targets:
            if rank[target] > rank[source]:
                parents[target].append(source)

    # 从本单元出发还能向下游走多少步：越长越是主物料线。
    # 主干与支线落在同一列时，主干优先占“与上游同行”的位置，否则主线会被拉到下一行拐弯。
    depth = {node_id: 0 for node_id in known}
    for node_id in reversed(sequence):
        for target in outgoing[node_id]:
            if rank[target] > rank[node_id]:
                depth[node_id] = max(depth[node_id], depth[target] + 1)

    columns: dict[int, list[dict]] = {}
    for node in nodes:
        columns.setdefault(level[node["id"]], []).append(node)

    # 同一列内：先按上游行号的平均值找自己该在的行，再从上往下避开占用。
    rows: dict[str, int] = {}
    for column in sorted(columns):
        items = [node for node in columns[column] if node["kind"] != "waste"]
        plan: list[tuple[float, int, dict]] = []
        for node in items:
            ahead = [rows[item] for item in parents[node["id"]] if item in rows]
            preferred = sum(ahead) / len(ahead) if ahead else 0.0
            plan.append((preferred, index[node["id"]], node))
        plan.sort(key=lambda item: (-depth[item[2]["id"]], item[0], item[1]))
        taken: set[int] = set()
        for preferred, _order, node in plan:
            row = int(preferred)  # 向主物料线（行号小的一侧）对齐，避免主干被支线拉到下一行
            while row in taken:
                row += 1
            taken.add(row)
            rows[node["id"]] = row

    main_rows = max(rows.values(), default=0) + 1
    bottom = 60.0 + max(main_rows, 1) * ROW_SPAN + 40.0
    for column in sorted(columns):
        used = 0
        for node in columns[column]:
            if node["kind"] == "waste":
                node["x"] = 60.0 + column * LEVEL_SPAN
                node["y"] = bottom + used * ROW_SPAN
                used += 1
            else:
                node["x"] = 60.0 + column * LEVEL_SPAN
                node["y"] = 60.0 + rows[node["id"]] * ROW_SPAN

def _trim_graph(graph: dict) -> dict:
    """把越界字段裁到 schema 允许的范围，丢掉无法修复的连线。"""
    nodes, edges = graph["nodes"], graph["edges"]
    node_ids = {node["id"] for node in nodes}
    trimmed_edges = []
    for edge in edges:
        if edge["source"] not in node_ids or edge["target"] not in node_ids or edge["source"] == edge["target"]:
            continue
        edge["components"] = [item for item in edge.get("components", []) if item.get("kind") in VALVE_KINDS][:6]
        edge["instruments"] = edge.get("instruments", [])[:4]
        edge["service"] = edge.get("service") or ""
        edge["signals"] = [item for item in edge.get("signals", []) if isinstance(item, dict)][:6]
        trimmed_edges.append(edge)
    for node in nodes:
        node["utilities"] = [item for item in node.get("utilities", []) if isinstance(item, dict)][:8]
        node["reliefs"] = [item for item in node.get("reliefs", []) if isinstance(item, dict)][:6]
    return {"nodes": nodes, "edges": trimmed_edges}


def _num(value: Any, low: float, high: float, default: float) -> float:
    """把模型给的坐标/尺寸夹到图纸数据规范允许的范围。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number or number in (float("inf"), float("-inf")):
        return default
    return max(low, min(high, number))


def _repair_graph(graph: dict, warnings: list[str]) -> dict:
    """把内容修到图纸数据规范能接受的范围，修不了的连线丢掉，但不放弃整张图。

    以前这里校验不过就直接报错、整张图都出不来。工艺资料本来就不全，
    能画多少先画多少，剩下的在图上标「待补」交给设计人员复核。
    """
    nodes: list[dict] = []
    seen_ids: set[str] = set()
    for node in (graph.get("nodes") or [])[:200]:
        if not isinstance(node, dict):
            continue
        node_id = _clip(node.get("id"), 40)
        if not node_id or node_id in seen_ids:
            continue
        seen_ids.add(node_id)
        node["id"] = node_id
        node["label"] = _clip(node.get("label"), 80) or "未命名单元"
        node["tag"] = _clip(node.get("tag"), 24)
        node["params"] = [item for item in (node.get("params") or [])[:12] if isinstance(item, dict)]
        if not node["params"]:
            node["params"] = [{"name": "设备参数", "value": PENDING, "unit": "", "source": NO_DATA_SOURCE}]
        node["notes"] = [text for text in (_clip(item, 200) for item in (node.get("notes") or [])[:6]) if text]
        node["outlets"] = [item for item in (node.get("outlets") or [])[:6] if isinstance(item, dict)]
        node["utilities"] = [item for item in (node.get("utilities") or [])[:8] if isinstance(item, dict)]
        node["reliefs"] = [item for item in (node.get("reliefs") or [])[:6] if isinstance(item, dict)]
        node["x"] = _num(node.get("x"), -100000.0, 100000.0, 0.0)
        node["y"] = _num(node.get("y"), -100000.0, 100000.0, 0.0)
        node["w"] = _num(node.get("w"), 20.0, 1200.0, NODE_W)
        node["h"] = _num(node.get("h"), 20.0, 800.0, NODE_H)
        nodes.append(node)

    edges: list[dict] = []
    seen_edge_ids: set[str] = set()
    for edge in (graph.get("edges") or [])[:400]:
        if not isinstance(edge, dict):
            continue
        if edge.get("source") not in seen_ids or edge.get("target") not in seen_ids:
            continue
        if edge.get("source") == edge.get("target"):
            continue
        edge_id = _clip(edge.get("id"), 40)
        if not edge_id or edge_id in seen_edge_ids:
            edge_id = f"e{len(edges) + 1}"
        seen_edge_ids.add(edge_id)
        edge["id"] = edge_id
        edge["label"] = _clip(edge.get("label"), 80)
        edge["params"] = [item for item in (edge.get("params") or [])[:8] if isinstance(item, dict)]
        edge["components"] = [item for item in (edge.get("components") or [])[:6]
                              if isinstance(item, dict) and item.get("kind") in VALVE_KINDS]
        edge["instruments"] = [text for text in (_clip(item, 24)
                                                 for item in (edge.get("instruments") or [])[:4]) if text]
        edge["service"] = _clip(edge.get("service"), 80)
        edge["signals"] = [item for item in (edge.get("signals") or [])[:6] if isinstance(item, dict)]
        edges.append(edge)

    repaired = {"nodes": nodes, "edges": edges}
    try:
        _pfd.PfdGraph(**repaired)
    except Exception as exc:  # noqa: BLE001 - 规范检查不过也要出图，交给人工复核
        warnings.append(f"图面数据有一处不合规范（{_clip(exc, 120)}），已按能画的部分出图，请人工核对。")
    return repaired


def _key_coverage(graph: dict, balance: dict | None) -> list[dict]:
    """这次成图用到了哪几项关键输入、哪几项是缺的，给界面一句交代。"""
    gaps, covered = _graph_input_signals(graph, balance)
    rows: list[dict] = []
    for spec in INPUT_CHECKLIST:
        if spec.get("level") != "key":
            continue
        key = spec["key"]
        if key in gaps:
            state = "图上缺值"
        elif key in covered:
            state = "已用于成图"
        else:
            state = "资料未给"
        rows.append({"key": key, "label": spec["label"], "state": state})
    return rows


def _persist_originals(project_id: str, blobs: list[tuple[str, bytes]]) -> str | None:
    """把上传原件留档到本地上传目录，便于复核识别依据。"""
    if not blobs:
        return None
    try:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        root = Path(get_settings().upload_dir) / "intake" / project_id / stamp
        root.mkdir(parents=True, exist_ok=True)
        for name, data in blobs:
            safe = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]", "_", Path(name).name)[:80] or "upload.bin"
            (root / safe).write_bytes(data)
        return str(root)
    except OSError:
        return None


@router.get("/models")
def intake_models(project_id: str, user: UserContext = Depends(get_current_user)) -> dict:
    """返回本机当前使用的识别模型与可用性，便于界面提示。"""
    return {
        "configured": bool(os.environ.get(MODEL_KEY_ENV, "").strip()),
        "key_env": MODEL_KEY_ENV,
        "endpoint": MODEL_BASE,
        "text_model": TEXT_MODEL,
        "vision_model": VISION_MODEL,
        "ocr_model": OCR_MODEL,
        "vision_mode": _vision_mode(),
        "accepts": {
            "text": True,
            "documents": sorted(TEXT_SUFFIXES | DOC_SUFFIXES),
            "images": sorted(IMAGE_SUFFIXES),
        },
        "limits": {"max_text_chars": MAX_TEXT_CHARS, "max_files": MAX_FILES,
                   "max_images": MAX_IMAGES, "max_file_bytes": MAX_FILE_BYTES,
                   "max_image_bytes": MAX_IMAGE_BYTES},
    }


@router.post("/extract")
async def extract_process(
    project_id: str,
    text: str = Form(default=""),
    hint: str = Form(default=""),
    title: str = Form(default=""),
    files: list[UploadFile] = File(default=[]),
    images: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
) -> dict:
    """把工艺文字 / 文档 / 图片识别成一张可编辑的概念 PFD 草稿（不落库）。"""
    require_project_access(db, project_id, user, "member")

    blocks: list[str] = []
    originals: list[tuple[str, bytes]] = []
    sources: list[dict] = []
    unreadable: list[str] = []

    pasted = (text or "").strip()
    if pasted:
        blocks.append(f"【粘贴的工艺描述】\n{pasted[:MAX_TEXT_CHARS]}")
        sources.append({"kind": "text", "name": "粘贴的工艺描述", "chars": min(len(pasted), MAX_TEXT_CHARS),
                        "truncated": len(pasted) > MAX_TEXT_CHARS})

    incoming = [item for item in (files or []) if item is not None and item.filename]
    if len(incoming) > MAX_FILES:
        unreadable.append(f"一次最多处理 {MAX_FILES} 个文档，本次只读取前 {MAX_FILES} 个。")
        incoming = incoming[:MAX_FILES]
    for item in incoming:
        name = Path(item.filename or "upload").name
        suffix = Path(name).suffix.lower()
        data = await item.read()
        if len(data) > MAX_FILE_BYTES:
            unreadable.append(f"{name}：文件超过 {MAX_FILE_BYTES // (1024 * 1024)} MB，本次未读取。")
            continue
        if suffix not in TEXT_SUFFIXES | DOC_SUFFIXES:
            unreadable.append(f"{name}：仅支持 {', '.join(sorted(TEXT_SUFFIXES | DOC_SUFFIXES))} 文档，本次未读取。")
            continue
        body = _extract_text(name, data)
        body = body.strip()
        if not body:
            unreadable.append(f"{name}：没有抽取到可读文字（可能是扫描件，请改用图片上传）。")
            continue
        blocks.append(f"【文档 {name}】\n{body[:MAX_TEXT_CHARS]}")
        originals.append((name, data))
        sources.append({"kind": "document", "name": name, "chars": min(len(body), MAX_TEXT_CHARS),
                        "truncated": len(body) > MAX_TEXT_CHARS})

    picture_parts: list[dict] = []
    image_names: list[str] = []
    incoming_images = [item for item in (images or []) if item is not None and item.filename]
    if len(incoming_images) > MAX_IMAGES:
        unreadable.append(f"一次最多识别 {MAX_IMAGES} 张图片，本次只发送前 {MAX_IMAGES} 张。")
    for item in incoming_images[:MAX_IMAGES]:
        name = Path(item.filename or "image").name
        suffix = Path(name).suffix.lower()
        data = await item.read()
        if len(data) > MAX_IMAGE_BYTES:
            unreadable.append(f"{name}：图片超过 {MAX_IMAGE_BYTES // (1024 * 1024)} MB，本次未识别。")
            continue
        mime = IMAGE_MIME.get(suffix)
        if mime is None:
            unreadable.append(f"{name}：仅支持 {', '.join(sorted(IMAGE_SUFFIXES))} 图片，本次未识别。")
            continue
        picture_parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"},
        })
        originals.append((name, data))
        image_names.append(name)
        sources.append({"kind": "image", "name": name, "bytes": len(data)})

    if not blocks and not picture_parts:
        raise HTTPException(422, "请至少提供一种输入：粘贴工艺描述、上传工艺文档，或上传流程图/设备清单图片。")

    instruction = SCHEMA_HINT
    if hint.strip():
        instruction += f"\n补充说明（来自用户）：{_clip(hint, 500)}\n"
    payload_text = instruction
    if blocks:
        payload_text += "\n\n===== 待识别资料 =====\n" + "\n\n".join(blocks)

    vision_mode = _vision_mode()
    ocr_used = False
    usage: dict = {}
    if picture_parts and vision_mode == "ocr":
        # 纯文本对话模型（DeepSeek-V4 等）不能直接吃图片：先用 OCR 模型把图转成文字，
        # 转出的文字和用户粘贴/上传的文字资料一起交给文本模型做结构化。
        ocr_blocks, ocr_usage = _ocr_images(picture_parts, image_names)
        usage = _merge_usage(usage, ocr_usage)
        ocr_used = True
        if ocr_blocks:
            blocks.extend(ocr_blocks)
        else:
            unreadable.append("上传的图片没有转出可读文字，请确认图片清晰度，或改用文字、文档输入。")

    instruction = SCHEMA_HINT
    if hint.strip():
        instruction += f"\n补充说明（来自用户）：{_clip(hint, 500)}\n"
    if ocr_used:
        instruction += (
            "\n注意：上面的图片资料是 OCR 转录的文字，版面里的连线关系可能没有完整保留；"
            "只按转录出来的设备、物流与数值建图，连接关系不确定的写进 unreadable，不要凭常识补管线。\n"
        )
    payload_text = instruction
    if blocks:
        payload_text += "\n\n===== 待识别资料 =====\n" + "\n\n".join(blocks)

    user_content: list[dict] = [{"type": "text", "text": payload_text}]
    if picture_parts and not ocr_used:
        user_content.append({"type": "text", "text": f"下面是 {len(picture_parts)} 张工艺相关图片（流程图、设备清单或操作规程截图），请一并识别并与上面的文字资料合并成同一张图。"})
        user_content.extend(picture_parts)

    model = VISION_MODEL if (picture_parts and not ocr_used) else TEXT_MODEL
    content, model_usage = _chat(
        [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_content}],
        model,
        8192,
    )
    usage = _merge_usage(usage, model_usage)
    parsed = _load_json(content)

    graph, warnings, _order = _build_graph(parsed)
    graph = _trim_graph(graph)
    graph = _repair_graph(graph, warnings)

    for item in (parsed.get("unreadable") or [])[:20]:
        entry = _clip(item, 200)
        if entry:
            unreadable.append(entry)
    notes = [text for text in (_clip(entry, 200) for entry in (parsed.get("notes") or [])[:8]) if text]

    balance: dict | None = None
    balance_verdict = ""
    try:
        balance = _pfd._material_balance(graph)
        balance_verdict = _pfd._balance_verdict(balance)
    except Exception:  # noqa: BLE001 - 校核失败不影响出图
        balance = None
    if balance and balance.get("status") == "不平衡":
        names = "、".join(balance.get("imbalanced") or []) or "图中单元"
        warnings.append(
            f"物料守恒未闭合：{names} 的进出料对不上。差额必须有明确去向"
            "（底部排污、放空、母液、取样留样等），请人工确认后再作为设计输入。"
        )

    required_inputs = _build_required_inputs(parsed, graph, balance)
    key_inputs = [item for item in required_inputs if item.get("level") == "key"]
    detail_inputs = [item for item in required_inputs if item.get("level") != "key"]
    if key_inputs and detail_inputs:
        required_inputs_note = (
            f"图纸已按这份资料里能确定的内容生成，缺的地方图上标了「{PENDING}」。"
            f"要补的里面 {len(key_inputs)} 项影响图面，另有 {len(detail_inputs)} 项属于详细设计；"
            "不补也照样出图，补了图更细。"
        )
    elif key_inputs:
        required_inputs_note = (
            f"图纸已按这份资料里能确定的内容生成，缺的地方图上标了「{PENDING}」。"
            f"要补的里面 {len(key_inputs)} 项影响图面，补齐后重新识别即可。"
        )
    elif detail_inputs:
        required_inputs_note = (
            f"影响图面的输入这次都齐了，可以直接用；另有 {len(detail_inputs)} 项详细设计资料，"
            f"图上已按「{PENDING}」占位，补了可以让图更细。"
        )
    else:
        required_inputs_note = "这份资料已覆盖图上要用到的全部输入，没有需要补的。"


    stamped = _persist_originals(project_id, originals)
    draft_ref = f"draft-{new_id()}"
    db.add(AuditEvent(
        project_id=project_id, actor_id=user.id, action="process.pfd_ai_intake",
        entity_type="process_pfd_draft", entity_id=draft_ref,
        details={"model": model, "nodes": len(graph["nodes"]), "edges": len(graph["edges"]),
                 "documents": len(originals), "images": len(picture_parts),
                 "usage": usage, "stored": stamped},
    ))
    db.commit()

    return {
        "draft_ref": draft_ref,
        "title": _clip(title, 160) or _clip(parsed.get("title"), 140) or "AI 识别生成的概念 PFD",
        "graph": graph,
        "node_count": len(graph["nodes"]),
        "edge_count": len(graph["edges"]),
        "unit_labels": [node["label"] for node in graph["nodes"]],
        "stream_labels": [edge["label"] for edge in graph["edges"]],
        "unreadable": unreadable,
        "required_inputs": required_inputs,
        "required_inputs_note": required_inputs_note,
        "required_core": key_inputs,
        "required_detail": detail_inputs,
        "drawing_basis": _key_coverage(graph, balance),
        "notes": notes,
        "warnings": warnings,
        "sources": sources,
        "model": {"name": model, "vision": bool(picture_parts), "endpoint": MODEL_BASE,
                  "vision_mode": vision_mode if picture_parts else "text",
                  "ocr_model": OCR_MODEL if ocr_used else None},
        "usage": usage,
        "balance": {"status": (balance or {}).get("status"), "verdict": balance_verdict},
        "evidence_note": "每个参数都带着识别来源；来源为空的一律标为待补。识别结果是草稿，必须由设计人员复核后才能作为设计输入。",
        "pending_label": PENDING,
        "pending_source": PENDING_SOURCE,
        "disclaimer": DISCLAIMER,
        "stored_originals": stamped,
    }


@router.post("/aspen-draft")
def aspen_draft(project_id: str, payload: dict = Body(default={}), db: Session = Depends(get_db),
                user: UserContext = Depends(get_current_user)) -> dict:
    """把识别出来的单元操作排成 Aspen 风格「流程模拟图」+ 输入文件骨架（不落库）。

    用途是把「文本 → CAD」拆成「文本 → 流程模拟图 → CAD 图纸」两步：设计人员先在
    流程模拟图上核对拓扑与流量，再据此出 PFD 与图例两张 CAD 图。本机没有 Aspen
    授权，这里给的是模拟前的草稿与待补清单，不冒充模拟器运行结果。
    """
    require_project_access(db, project_id, user, "member")
    graph = payload.get("graph") if isinstance(payload, dict) else None
    if not isinstance(graph, dict) or not (graph.get("nodes") or []):
        raise HTTPException(422, "画布上还没有单元操作：先识别工艺信息或手工加节点，再生成流程模拟图。")
    title = str((payload or {}).get("title") or "概念 PFD").strip()[:200] or "概念 PFD"
    stamp = datetime.now(timezone.utc).astimezone().date().isoformat()
    stage = aspen_flowsheet.build_stage(title, graph, stamp)
    stage["disclaimer"] = (
        DISCLAIMER + " 本图是 Aspen 流程模拟的草稿：本机未运行 Aspen Plus，"
        "组分、物性方法、热力学数据与设备尺寸均为待补，须由工程师在 Aspen 中补全并运行。"
    )
    return stage
