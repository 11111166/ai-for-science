"""Bounded, structure-based retrosynthesis for process route triage.

Rules propose precursor equivalents only. They do not retrieve patent examples,
prove forward feasibility, establish procurement availability, or determine FTO.
"""
from functools import lru_cache
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, File, Form, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from .auth import get_current_user, require_project_access
from .database import get_db
from .order_assessment import find_record
from .process_knowledge import materials

router = APIRouter(prefix="/api/v1/drug-discovery/projects/{project_id}/process/retrosynthesis")


class RetrosynthesisInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    target: str = Field(min_length=1, max_length=1000)
    patent_publication: str = Field(default="", max_length=80)
    jurisdiction: Literal["CN", "US", "EP", "WO", "其他"] = "CN"


class PatentReadInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    publication: str = Field(min_length=7, max_length=40)
    target: str = Field(default="", max_length=1000)
    structure_overrides: dict[str, str] = Field(default_factory=dict)


TEMPLATES = (
    {
        "id": "ester",
        "name": "酯键断裂",
        "reaction": "酯化或酰基化",
        "smarts": "[C:1](=[O:2])[O:3][*:4]>>[C:1](=[O:2])O.[O:3][*:4]",
        "forward": "将羧酸片段与醇/酚片段作为合成等价物；可比较活化酸、酸酐等方法。这里的酸片段不是已证实可直接反应的投料形式。",
        "process": "先核对选择性、水分、反应/淬灭热，再比较结晶、萃取和蒸馏的分离可行性。",
    },
    {
        "id": "amide",
        "name": "酰胺键断裂",
        "reaction": "酰胺化",
        "smarts": "[C:1](=[O:2])[N:3]>>[C:1](=[O:2])O.[N:3]",
        "forward": "将羧酸与胺作为合成等价物；需筛选活化方式、偶联试剂或酰氯路线，并核对手性和副产物。",
        "process": "重点核查活化剂残留、消旋、加料/淬灭放热及副产物去除。",
    },
    {
        "id": "sulfonamide",
        "name": "磺酰胺键断裂",
        "reaction": "磺酰化",
        "smarts": "[S:1](=[O:2])(=[O:3])[N:4]>>[S:1](=[O:2])(=[O:3])Cl.[N:4]",
        "forward": "以磺酰氯与胺作为可考察的前体组合；磺酰氯只是规则建议，并非已核实的专利投料。",
        "process": "核查磺酰氯稳定性、放热与酸性尾气、选择性和残留活性试剂。",
    },
)


def _chemistry():
    try:
        from rdkit import Chem
        from rdkit.Chem import rdChemReactions, rdMolDescriptors
    except ImportError as exc:
        raise HTTPException(503, "结构分析组件暂不可用，请稍后重试") from exc
    return Chem, rdChemReactions, rdMolDescriptors


@lru_cache(maxsize=1)
def _known_materials():
    Chem, _, _ = _chemistry()
    indexed = {}
    for item in materials():
        mol = Chem.MolFromSmiles(item["smiles"])
        if mol is not None:
            indexed[Chem.MolToSmiles(mol, isomericSmiles=True)] = item
    return indexed


def analyze(payload: RetrosynthesisInput) -> dict:
    Chem, reactions, descriptors = _chemistry()
    record = find_record(payload.target)
    submitted_smiles = record["smiles"] if record else payload.target
    target = Chem.MolFromSmiles(submitted_smiles)
    if target is None or not 1 <= target.GetNumHeavyAtoms() <= 100 or len(Chem.GetMolFrags(target)) != 1:
        raise HTTPException(422, "目标未命中本地名称/CAS；请提供单一、有效且不超过100个重原子的SMILES")
    canonical = Chem.MolToSmiles(target, isomericSmiles=True)
    known = _known_materials()
    candidates = []
    seen = set()
    for template in TEMPLATES:
        reaction = reactions.ReactionFromSmarts(template["smarts"])
        for products in reaction.RunReactants((target,), maxProducts=20):
            if len(products) != 2:
                continue
            precursors = []
            try:
                for molecule in products:
                    Chem.SanitizeMol(molecule)
                    if len(Chem.GetMolFrags(molecule)) != 1:
                        raise ValueError("fragmented precursor")
                    smiles = Chem.MolToSmiles(molecule, isomericSmiles=True)
                    match = known.get(smiles)
                    precursors.append({
                        "smiles": smiles,
                        "formula": descriptors.CalcMolFormula(molecule),
                        "name": match["name"] if match else "待核对前体",
                        "cas": match["cas"] if match else None,
                        "catalog_status": "本地原料库已识别；采购可得性未核实" if match else "本地原料库未识别",
                        "process_caution": match["process_caution"] if match else None,
                    })
            except (ValueError, RuntimeError):
                continue
            key = (template["id"], tuple(sorted(p["smiles"] for p in precursors)))
            if key in seen:
                continue
            seen.add(key)
            candidates.append({
                "id": f"{template['id']}-{len(candidates) + 1}",
                "disconnection": template["name"],
                "reaction_class": template["reaction"],
                "precursors": precursors,
                "forward_hypothesis": template["forward"],
                "process_checks": template["process"],
                "evidence_level": "结构规则推断；未核对专利实施例或实验收率",
                "catalog_hits": sum(p["cas"] is not None for p in precursors),
            })
    candidates.sort(key=lambda row: (-row["catalog_hits"], row["disconnection"], row["id"]))
    candidates = candidates[:8]
    known_routes = []
    if record:
        known_routes = [{"name": route["name"], "starting_materials": route["starting_materials"],
                         "transformations": route["transformations"], "purification": route["purification"],
                         "scale_evidence": route["scale_evidence"]} for route in record["routes"]]
    patent_query = payload.patent_publication.strip()
    return {
        "target": {"name": record["name"] if record else "用户输入结构", "cas": record["cas"] if record else None,
                   "smiles": canonical, "formula": descriptors.CalcMolFormula(target),
                   "identity_status": "本地目录已识别" if record else "仅通过结构格式校验；身份未与外部登记号核对"},
        "patent": {"publication": patent_query or None, "jurisdiction": payload.jurisdiction,
                   "status": "未自动核对专利原文、权利要求或法律状态",
                   "search_url": "https://patentscope.wipo.int/search/en/structuredSearch.jsf" if patent_query else None},
        "candidates": candidates,
        "known_route_summaries": known_routes,
        "known_route_sources": record["sources"] if record else [],
        "decision": "先复核规则拆解与原始实施例，再安排小试" if candidates else "当前规则库没有可信的一步拆解；需人工提取专利实施例或扩展反应模板",
        "next_checks": [
            "核对目标结构、立体化学、盐型与专利实施例中的实际中间体",
            "从原始实施例提取起始物、试剂、规模、逐步收率、纯度及分析方法",
            "核对关键前体的供应、价格、交期与杂质规格",
            "按目标法域复核专利族、权利要求、法律状态与工艺改进专利",
        ],
        "scope": "仅作一步逆合成候选生成；不能证明路线可行、前体可采购、成本或专利自由实施。",
        "template_version": "bounded-disconnections-v1",
    }


@router.post("/analyze")
def analyze_route(project_id: str, payload: RetrosynthesisInput,
                  db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_project_access(db, project_id, user, "viewer")
    return analyze(payload)


@router.post("/patent/read")
def read_patent(project_id: str, payload: PatentReadInput,
                db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_project_access(db, project_id, user, "viewer")
    from .process_patent_extraction import extract_examples, fetch_publication
    paragraphs, source = fetch_publication(payload.publication)
    return extract_examples(paragraphs, source, payload.target, payload.structure_overrides)


@router.post("/patent/upload")
async def upload_patent(project_id: str, file: UploadFile = File(...),
                        publication: str = Form(default=""), target: str = Form(default=""),
                        structure_overrides: str = Form(default="{}"),
                        db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_project_access(db, project_id, user, "viewer")
    from .process_patent_extraction import extract_examples, uploaded_paragraphs, normalize_publication, MAX_SOURCE_BYTES
    import json
    if len(target) > 1000:
        raise HTTPException(422, "目标结构输入过长")
    name = normalize_publication(publication) if publication.strip() else None
    try:
        overrides = json.loads(structure_overrides)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "结构补录格式错误") from exc
    if not isinstance(overrides, dict):
        raise HTTPException(422, "结构补录格式错误")
    data = await file.read(MAX_SOURCE_BYTES + 1)
    paragraphs, kind = uploaded_paragraphs(data, file.filename or "")
    return extract_examples(paragraphs, {"kind": kind, "url": None, "publication": name, "original": True}, target, overrides)
