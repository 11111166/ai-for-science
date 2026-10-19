"""Evidence-bound patent example extraction and comparison with rule retrosynthesis.

No chemical structure is inferred from a drawing, name alone, or legal status.
"""
from __future__ import annotations

from html.parser import HTMLParser
from io import BytesIO
import re
from urllib.parse import quote
import xml.etree.ElementTree as ET

import httpx
from fastapi import HTTPException

from .order_assessment import catalog
from .process_knowledge import materials
from .process_retrosynthesis import RetrosynthesisInput, analyze, _chemistry

MAX_SOURCE_BYTES = 8_000_000
MAX_TEXT_CHARS = 400_000
PUBLICATION = re.compile(r"^(CN|EP|US|WO)[0-9]{5,13}[A-C][0-9]?$", re.I)
YIELD = re.compile(r"(?:收率|产率|yield)\s*[:：=]?\s*(\d{1,3}(?:\.\d+)?)\s*[%％]|(\d{1,3}(?:\.\d+)?)\s*[%％]\s*(?:yield|收率|产率)", re.I)
PURITY = re.compile(r"(?:纯度|purity)\s*[:：=]?\s*(\d{1,3}(?:\.\d+)?)\s*[%％]|(\d{1,3}(?:\.\d+)?)\s*[%％]\s*(?:purity|纯度)", re.I)
CAS = re.compile(r"\b\d{2,7}-\d{2}-\d\b")
EXAMPLE = re.compile(r"^(?:实施例|合成例|制备例|Example|Preparation)\s*([0-9一二三四五六七八九十]+)\b", re.I)
STEP = re.compile(r"^[（(]\s*([0-9]+)\s*[）)]\s*(.{0,60})")
PROCESS = re.compile(r"加入|滴加|搅拌|反应|过滤|干燥|重结晶|萃取|浓缩|蒸馏|制得|得到|获得|yield|added|stirred|heated|filtered|purified|obtained|isolated", re.I)
PURIFICATION = re.compile(r"重结晶|过滤|萃取|蒸馏|柱层析|干燥|recrystalli|filtrat|extract|chromatograph|distill", re.I)


def normalize_publication(value: str) -> str:
    publication = re.sub(r"[\s./_-]", "", value).upper()
    if not PUBLICATION.fullmatch(publication):
        raise HTTPException(422, "请填写 CN、EP、US 或 WO 的完整专利公开号（含公开类型字母）")
    return publication


def _fetch(url: str) -> bytes:
    try:
        with httpx.Client(timeout=httpx.Timeout(15, connect=5), follow_redirects=False,
                          headers={"User-Agent": "AIforScience/1.0 patent-example-reader"}) as client:
            response = client.get(url)
        if response.status_code != 200:
            raise HTTPException(502, f"专利公开文本暂不可读取（来源返回 {response.status_code}）；可上传原始 PDF 或文本")
        if len(response.content) > MAX_SOURCE_BYTES:
            raise HTTPException(413, "专利文本超过 8 MB，请上传精简的实施例原文")
        return response.content
    except httpx.HTTPError as exc:
        raise HTTPException(502, "专利公开文本连接失败；请上传原始 PDF 或文本") from exc


class PatentHtml(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.in_p = False
        self.source_depth = 0
        self.text: list[str] = []
        self.source: list[str] = []
        self.locator = ""
        self.paragraphs: list[tuple[str, str]] = []
        self.div_depth = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "div" and "description-paragraph" in attrs.get("class", "").split() and not self.in_p:
            self.in_p = True
            self.div_depth = 1
            self.text, self.source = [], []
            self.locator = attrs.get("id", "")
            return
        if self.in_p and self.div_depth:
            if tag == "div":
                self.div_depth += 1
            return
        if tag == "section" and attrs.get("itemprop") == "description" and not self.depth:
            self.depth = 1
        elif self.depth:
            if tag == "section":
                self.depth += 1
            if tag == "p":
                self.in_p = True
                self.text, self.source = [], []
                self.locator = attrs.get("id", "")
            if self.in_p and tag == "span" and "google-src-text" in attrs.get("class", "").split():
                self.source_depth = 1
            elif self.source_depth:
                self.source_depth += 1

    def handle_endtag(self, tag):
        if self.in_p and self.div_depth:
            if tag == "div":
                self.div_depth -= 1
                if not self.div_depth:
                    value = " ".join("".join(self.text).split())
                    if value:
                        self.paragraphs.append((value, self.locator))
                    self.in_p = False
            return
        if self.source_depth:
            self.source_depth -= 1
        if tag == "p" and self.in_p:
            value = " ".join("".join(self.source or self.text).split())
            if value:
                self.paragraphs.append((value, self.locator))
            self.in_p = False
        if tag == "section" and self.depth:
            self.depth -= 1

    def handle_data(self, data):
        if self.in_p:
            self.text.append(data)
            if self.source_depth:
                self.source.append(data)


def _google_paragraphs(raw: bytes) -> list[tuple[str, str]]:
    parser = PatentHtml()
    parser.feed(raw.decode("utf-8", errors="replace"))
    return parser.paragraphs


def _epo_paragraphs(raw: bytes) -> list[tuple[str, str]]:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise HTTPException(502, "EPO 返回的专利 XML 无法解析") from exc
    result = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1].lower() in {"p", "heading", "h", "title"}:
            value = " ".join("".join(element.itertext()).split())
            if value:
                result.append((value, element.attrib.get("id") or element.attrib.get("num") or ""))
    return result


def fetch_publication(publication: str):
    publication = normalize_publication(publication)
    if publication.startswith("EP"):
        # EPO publication-server format includes the NW correction code.
        number = publication[:2] + publication[2:-2].zfill(7) + "NW" + publication[-2:]
        url = f"https://data.epo.org/publication-server/rest/v1.2/patents/{number}/document.xml"
        paragraphs = _epo_paragraphs(_fetch(url))
        source = {"kind": "EPO 官方公开文本 XML", "url": url, "publication": publication, "original": True}
    else:
        url = f"https://patents.google.com/patent/{quote(publication)}/en"
        paragraphs = _google_paragraphs(_fetch(url))
        source = {"kind": "Google Patents 公开文本镜像", "url": url, "publication": publication,
                  "original": False}
    if not paragraphs:
        raise HTTPException(422, "未从该公开号读取到可解析的说明书文本；请上传原始 PDF 或文本")
    return paragraphs, source


def uploaded_paragraphs(data: bytes, filename: str) -> tuple[list[tuple[str, str]], str]:
    if not data or len(data) > MAX_SOURCE_BYTES:
        raise HTTPException(413, "上传文件须为 1 字节至 8 MB")
    filename = filename.lower()
    if filename.endswith(".pdf") and data.startswith(b"%PDF"):
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise HTTPException(503, "PDF 文本解析组件暂不可用，请上传 TXT") from exc
        try:
            reader = PdfReader(BytesIO(data), strict=False)
            if len(reader.pages) > 300:
                raise HTTPException(413, "PDF 超过 300 页，请截取实施例部分")
            pages = [(page.extract_text() or "", f"第 {index + 1} 页") for index, page in enumerate(reader.pages)]
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(422, "PDF 无法提取文本，扫描件请先 OCR 后上传 TXT") from exc
        kind = "用户上传 PDF 原文"
    elif filename.endswith(".txt"):
        try:
            pages = [(data.decode("utf-8-sig"), "上传文本")]
        except UnicodeDecodeError:
            pages = [(data.decode("gb18030", errors="replace"), "上传文本")]
        kind = "用户上传 TXT 原文"
    else:
        raise HTTPException(422, "仅支持 PDF 或 TXT 原文")
    paragraphs = []
    for content, page in pages:
        content = re.sub(r"(?im)^(?=(?:实施例|合成例|制备例|Example|Preparation)\s*[0-9]+\b)", "\n\n", content)
        for line in re.split(r"\n\s*\n|(?<=。)\s*\n|(?<=\.)\s*\n", content):
            value = " ".join(line.split())
            if value:
                paragraphs.append((value, page))
    if sum(len(value) for value, _ in paragraphs) < 30:
        raise HTTPException(422, "没有提取到足够的可检索文本；扫描 PDF 请先 OCR，图片结构需人工核对")
    return paragraphs, kind


def _molecule_index() -> list[dict]:
    records = []
    for item in materials():
        records.append({"name": item["name"], "aliases": [item["name"], item.get("english_name", ""), item["cas"]],
                        "smiles": item["smiles"], "cas": item["cas"], "source": "本地原料知识库"})
    for item in catalog()["records"]:
        records.append({"name": item["name"], "aliases": [item["name"], item["english_name"], item["cas"], *item["aliases"]],
                        "smiles": item["smiles"], "cas": item["cas"], "source": "本地化合物目录"})
    # Fixed identity references for the regression example, with public PubChem CIDs.
    records += [
        {"name": "水杨酸", "aliases": ["水杨酸", "salicylic acid", "2-hydroxybenzoic acid", "69-72-7"],
         "smiles": "O=C(O)c1ccccc1O", "cas": "69-72-7", "source": "PubChem CID 338 身份对照"},
        {"name": "苯酚", "aliases": ["苯酚", "phenol", "108-95-2"],
         "smiles": "Oc1ccccc1", "cas": "108-95-2", "source": "PubChem CID 996 身份对照"},
    ]
    return records


def _mentions(text: str, index: list[dict]) -> list[dict]:
    found = []
    lower = text.casefold()
    hits = []
    for item in index:
        aliases = [alias for alias in item["aliases"] if alias and len(alias) >= 2]
        for alias in aliases:
            for match in re.finditer(r"(?<![a-z0-9])" + re.escape(alias.casefold()) + r"(?![a-z0-9])", lower):
                hits.append((match.start(), match.end(), item))
    # A short name embedded in a longer reagent name is not a second molecule:
    # 乙酸酐 does not establish that free 乙酸 was used in the patent example.
    accepted = []
    for start, end, item in sorted(hits, key=lambda hit: (-(hit[1] - hit[0]), hit[0])):
        if not any(start < other_end and end > other_start for other_start, other_end, _ in accepted):
            accepted.append((start, end, item))
    for _, _, item in sorted(accepted):
        if item["cas"] not in {entry["cas"] for entry in found}:
            found.append(item)
    return found


def _pct(regex, text):
    match = regex.search(text)
    if not match:
        return None
    value = float(next(part for part in match.groups() if part is not None))
    return value if 0 <= value <= 100 else None


def extract_examples(paragraphs: list[tuple[str, str]], source: dict, target: str = "", overrides: dict | None = None) -> dict:
    if sum(len(text) for text, _ in paragraphs) > MAX_TEXT_CHARS:
        raise HTTPException(413, "原文超过 40 万字，请截取实施例部分")
    index = _molecule_index()
    overrides = overrides or {}
    if len(overrides) > 30:
        raise HTTPException(422, "最多补录 30 个结构")
    Chem, _, _ = _chemistry()
    manual = {}
    for name, smiles in overrides.items():
        if not isinstance(name, str) or not isinstance(smiles, str) or not 2 <= len(name) <= 100 or len(smiles) > 500:
            raise HTTPException(422, "结构补录需要名称和有效 SMILES")
        mol = Chem.MolFromSmiles(smiles)
        if mol is None or len(Chem.GetMolFrags(mol)) != 1 or mol.GetNumHeavyAtoms() > 100:
            raise HTTPException(422, f"{name} 的 SMILES 无效或含多个片段")
        manual[name] = Chem.MolToSmiles(mol, isomericSmiles=True)
    examples: list[dict] = []
    current = None
    step_label = ""
    molecules: dict[str, dict] = {}
    for text, locator in paragraphs:
        match = EXAMPLE.match(text)
        if match:
            current = {"label": match.group(0), "steps": []}
            examples.append(current)
            step_label = ""
            if len(examples) >= 40:
                break
            text = text[match.end():].strip(" \t:：—-。")
            if not text:
                continue
        if current is None or not PROCESS.search(text):
            continue
        if len(current["steps"]) >= 30:
            continue
        heading = STEP.match(text)
        if heading:
            step_label = heading.group(0)[:70]
        mentioned = _mentions(text, index)
        if len(text) < 15 and not heading:
            continue
        for item in mentioned:
            molecules.setdefault(item["cas"], {"name": item["name"], "cas": item["cas"], "smiles": item["smiles"],
                                               "identity_source": item["source"], "structure_status": "名称/CAS 与已收录结构映射；需核对专利实际盐型和立体化学"})
        for cas in CAS.findall(text):
            if cas not in molecules:
                molecules[cas] = {"name": f"CAS {cas}", "cas": cas, "smiles": None,
                                  "identity_source": "专利原文提及 CAS；结构未验证", "structure_status": "结构未解析"}
        for label in re.findall(r"(?:中间体|化合物|Intermediate|Compound)\s*[-#:：]?[A-Za-z0-9-]{1,12}", text, re.I):
            molecules.setdefault(label, {"name": label, "cas": None, "smiles": None,
                                         "identity_source": "专利原文名称线索", "structure_status": "结构未解析；检查图示或手动补录 SMILES"})
        for name, smiles in manual.items():
            if name.casefold() in text.casefold():
                molecules[name] = {"name": name, "cas": None, "smiles": smiles,
                                   "identity_source": "用户手动补录；非专利自动识别", "structure_status": "待与专利图示/分析数据核对"}
        yield_pct, purity_pct = _pct(YIELD, text), _pct(PURITY, text)
        if not mentioned and yield_pct is None and not heading and not any(name.casefold() in text.casefold() for name in manual):
            continue
        product = None
        for word in re.finditer(r"(?:得到|获得|制得|干燥得|过滤得|obtained|afforded|gave)\s*([^，,；;.（(]{1,70})", text, re.I):
            tail = word.group(1)
            product = next((item["name"] for item in reversed(mentioned) if any(alias and alias.casefold() in tail.casefold() for alias in item["aliases"])), product)
        step = {"index": len(current["steps"]) + 1, "heading": step_label,
                "kind": "分离/纯化" if PURIFICATION.search(text) and not re.search(r"羧化反应|酰化反应|coupling reaction", text, re.I) else "反应/操作",
                "product": product, "mentioned_molecules": [item["name"] for item in mentioned],
                "yield_pct": yield_pct, "purity_pct": purity_pct,
                "source_locator": locator, "source_url": (source.get("url") or "") + (f"#{locator}" if locator and source.get("url") and not locator.startswith("第 ") else ""),
                "evidence_excerpt": text[:220] + ("…" if len(text) > 220 else ""),
                "extraction_note": "产物/角色与跨段连接需核对原文"}
        current["steps"].append(step)
    examples = [item for item in examples if item["steps"]]
    if not examples:
        return {"source": source, "examples": [], "molecules": [], "comparison": [],
                "summary": "已读取文本，但没有识别到可核查的实施例步骤；请检查原文语言或上传实施例 TXT。",
                "limitations": ["未识别的图片结构、化学式和图示路线需要人工确认"]}
    result = None
    unresolved_target = False
    if target.strip():
        try:
            result = analyze(RetrosynthesisInput(target=target))
        except HTTPException as exc:
            if exc.status_code != 422:
                raise
            unresolved_target = True
    comparisons = []
    if result:
        present = {Chem.MolToSmiles(Chem.MolFromSmiles(item["smiles"]), isomericSmiles=True): item for item in molecules.values() if item["smiles"] and Chem.MolFromSmiles(item["smiles"])}
        for candidate in result["candidates"]:
            matches = []
            for precursor in candidate["precursors"]:
                item = present.get(precursor["smiles"])
                matches.append({"name": precursor["name"], "smiles": precursor["smiles"],
                                "status": "专利实施例提及同一已映射结构" if item else "原文未识别到同一结构",
                                "patent_name": item["name"] if item else None})
            comparisons.append({"candidate_id": candidate["id"], "disconnection": candidate["disconnection"],
                                "matches": matches, "matched_count": sum(item["patent_name"] is not None for item in matches),
                                "status": "仅为结构/名称对照；不证明同一步反应或专利覆盖"})
    limitations = ["逐段收率只在原文明确标注时显示；不得将纯化收率当作反应收率或相乘得总收率",
                   "名称映射不是从专利图像解析结构；盐型、异构体、保护基和图示结构需人工核对",
                   "原文段落顺序不等于已验证的物料流；法律状态及自由实施需另行核查"]
    if unresolved_target:
        limitations.append("目标名称未命中本地目录/SMILES，已保留实施例提取，但无法生成结构逆合成对照")
    return {"source": source, "examples": examples, "molecules": list(molecules.values()), "comparison": comparisons,
            "summary": f"读取 {len(examples)} 个实施例、{sum(len(item['steps']) for item in examples)} 条操作/反应记录；识别 {len(molecules)} 个有身份映射的物质。",
            "limitations": limitations}
