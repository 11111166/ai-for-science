"""Evidence-bounded rapid order assessment backed by a local public-data catalog."""
import json
import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import ForeignKey, JSON, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .auth import UserContext, get_current_user, require_project_access
from .database import Base, get_db
from .models import AuditEvent, new_id

router = APIRouter(prefix="/api/v1/drug-discovery/projects/{project_id}/process/orders")


class OrderAssessment(Base):
    __tablename__ = "process_order_assessments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    data: Mapped[dict] = mapped_column(JSON)


class OrderInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=500)
    delivery_state: Literal["solid", "liquid", "gas"] = "solid"
    quantity_kg: float = Field(gt=0, le=1_000_000)
    purity_pct: float = Field(gt=0, le=100)
    max_steps: int = Field(default=6, ge=1, le=30)
    chromatography_allowed: bool = False
    material_cost_cny_per_kg: float | None = Field(default=None, gt=0, le=10_000_000)
    required_delivery_days: int = Field(default=30, ge=1, le=3650)
    labor_hours: float = Field(default=0, ge=0, le=1_000_000)
    labor_rate_cny_h: float = Field(default=0, ge=0, le=100_000)
    analytical_cost_cny: float = Field(default=0, ge=0, le=100_000_000)
    equipment_cost_cny: float = Field(default=0, ge=0, le=100_000_000)
    waste_cost_cny: float = Field(default=0, ge=0, le=100_000_000)
    contingency_pct: float = Field(default=20, ge=0, le=200)
    gross_margin_pct: float = Field(default=30, ge=0, lt=95)


@lru_cache(maxsize=1)
def catalog() -> dict:
    path = Path(__file__).with_name("data") / "order_assessment_catalog.json"
    return json.loads(path.read_text(encoding="utf-8"))


def normalized(value: str) -> str:
    return re.sub(r"[\s_·,，;；]+", "", value).casefold()


def find_record(query: str) -> dict | None:
    needle = normalized(query)
    for record in catalog()["records"]:
        aliases = record["aliases"] + [record["name"], record["english_name"], record["cas"], str(record["pubchem_cid"]), record["smiles"]]
        if needle in {normalized(value) for value in aliases}:
            return record
    return None


def assess(spec: dict) -> dict:
    record = find_record(spec["query"])
    patent_review = {
        "status": "需按法域和权利要求完成 FTO 复核",
        "conclusion": "化合物或原研专利到期不代表任一合成路线可自由实施；工艺、晶型、中间体、用途和改进专利可能仍有效。",
        "checklist": ["确认目标销售/生产法域", "检索化合物、关键中间体、工艺、晶型和用途专利族",
                      "核对优先权、法律状态、到期日和权利要求", "由专利人员形成书面 FTO 意见"],
    }
    if not record:
        return {
            "status": "evidence_gap", "identity": {"query": spec["query"], "matched": False}, "routes": [], "recommended_route": None,
            "summary": {"公开路线": "本地快照未命中", "路线成熟度": "待检索", "纯化难度": "待评估", "放大难度": "待评估", "成本水平": "待报价"},
            "decision": "先完成身份、文献和专利路线检索，再决定是否报价",
            "commercial_decision": {"grade": "暂缓报价", "confidence": "低", "why": "尚无可核对路线、收率、纯化和原料成本",
                                    "stop_conditions": ["身份无法确认", "没有可复现的公开或企业路线", "关键危险操作超出设备能力"]},
            "purification": {"expected_assay_range_pct": None, "recommendation": "待提取路线与杂质后选择纯化",
                             "improvement_actions": ["确认化合物物态、pKa、溶解度和热稳定性", "提取粗品纯度、杂质和原文纯化条件"]},
            "blockers": ["CAS、标准 SMILES 或明确名称尚未映射到本地资料", "缺少可核对的文献或专利实施例", "缺少原料报价与目标交付规格"],
            "next_actions": ["核对 CAS 与结构", "检索论文补充信息和专利实施例", "提取反应规模、收率与纯化方式", "向采购补齐关键原料报价"],
            "evidence_matrix": [{"item": "身份", "status": "缺失"}, {"item": "路线", "status": "缺失"}, {"item": "成本", "status": "缺失"}, {"item": "FTO", "status": "未复核"}],
            "patent_review": patent_review, "cost_estimate": None, "sources": [], "catalog_version": catalog()["version"],
        }

    routes = []
    for route in record["routes"]:
        penalties = route["steps"] * 4 + len(route["hazards"]) * 5
        if route["chromatography"] and not spec["chromatography_allowed"]:
            penalties += 30
        if route["steps"] > spec["max_steps"]:
            penalties += 20
        if spec["quantity_kg"] >= 10 and route["scale_evidence"] not in {"工业实施公开案例", "成熟工业化学转化", "历史工业路线"}:
            penalties += 15
        if spec["purity_pct"] >= 99:
            penalties += 6
        if spec.get("required_delivery_days", 30) < route["steps"] * 5:
            penalties += 10
        score = max(0, 100 - penalties)
        overall_yield = max(.35, .88 ** route["steps"])
        purification_text = "、".join(route["purification"])
        if "结晶" in purification_text:
            assay_range = [95, 99.5]
        elif "蒸馏" in purification_text:
            assay_range = [95, 99.5]
        elif "柱" in purification_text:
            assay_range = [97, 99.9]
        elif "萃取" in purification_text or "相分离" in purification_text:
            assay_range = [80, 97]
        else:
            assay_range = [85, 98]
        routes.append({
            **route, "fit_score": score, "fit": "优先" if score >= 78 else "可评估" if score >= 60 else "不优先",
            "reason": "无需柱层析且步骤数在约束内" if not route["chromatography"] and route["steps"] <= spec["max_steps"] else "存在步骤数或纯化约束",
            "estimated_overall_yield_pct": round(overall_yield * 100, 1),
            "expected_assay_range_pct": assay_range,
            "evidence_gap": "需回原始实施例提取各步规模、收率、当量、纯化与分析方法",
        })
    routes.sort(key=lambda item: (-item["fit_score"], item["steps"]))
    best = routes[0]
    difficulty = "低" if best["steps"] <= 2 and not best["chromatography"] else "中" if best["steps"] <= 5 and not best["chromatography"] else "高"
    scale = "低" if best["scale_evidence"] in {"工业实施公开案例", "成熟工业化学转化", "历史工业路线"} and not best["chromatography"] else "中"
    cost_level = "低" if best["steps"] <= 2 and not best["chromatography"] else "中" if best["steps"] <= 5 else "高"

    estimate = None
    if spec.get("material_cost_cny_per_kg"):
        overall_yield = best["estimated_overall_yield_pct"] / 100
        material_cost = spec["quantity_kg"] / overall_yield * spec["material_cost_cny_per_kg"]
        labor_cost = spec.get("labor_hours", 0) * spec.get("labor_rate_cny_h", 0)
        direct = material_cost + labor_cost + spec.get("analytical_cost_cny", 0) + spec.get("equipment_cost_cny", 0) + spec.get("waste_cost_cny", 0)
        risk_adjusted = direct * (1 + spec.get("contingency_pct", 20) / 100)
        quote = risk_adjusted / (1 - spec.get("gross_margin_pct", 30) / 100)
        estimate = {
            "material_cost_cny": round(material_cost, 2), "labor_cost_cny": round(labor_cost, 2),
            "analytical_cost_cny": round(spec.get("analytical_cost_cny", 0), 2),
            "equipment_cost_cny": round(spec.get("equipment_cost_cny", 0), 2),
            "waste_cost_cny": round(spec.get("waste_cost_cny", 0), 2),
            "direct_cost_cny": round(direct, 2), "risk_adjusted_cost_cny": round(risk_adjusted, 2),
            "suggested_quote_cny": round(quote, 2), "quote_range_cny": [round(quote * .9, 2), round(quote * 1.2, 2)],
            "per_product_kg_cny": round(quote / spec["quantity_kg"], 2),
            "basis": f"按路线经验总收率 {best['estimated_overall_yield_pct']}%、用户成本、{spec.get('contingency_pct',20)}% 风险准备和 {spec.get('gross_margin_pct',30)}% 毛利率估算",
            "limitations": "未获得逐步实测收率、采购正式报价、设备工时和失败批概率前仅作接单区间。",
        }

    blockers = ["公开路线摘要不能替代原始文献或专利实施例复核", "危险性、杂质谱和分析方法尚需项目数据"]
    if spec["purity_pct"] >= 99:
        blockers.append("高纯度目标需建立杂质去向和重结晶/打浆筛选")
    if spec.get("required_delivery_days", 30) < best["steps"] * 5:
        blockers.append("要求交期短于路线步骤的常规初评周期，需并行采购与分析并预留失败重做")
    if spec.get("delivery_state") == "gas":
        blockers.append("气体交付需另行确认组成、温压基准、容器、充装许可、泄漏与运输条件")

    improvement = ["复核原始实施例并提取逐步收率/纯度", "用溶解度、pKa 和杂质数据筛选结晶/打浆",
                   "对关键步骤做小规模时间序列与终点方法", "建立母液、洗液和返工物流的产品/杂质衡算"]
    if best["chromatography"]:
        improvement.insert(1, "优先验证结晶、萃取或选择性吸附替代柱层析")
    confidence = "中" if best["route_evidence"] == "高" and len(record["sources"]) >= 2 else "较低"
    grade = "可进入有条件报价" if best["fit_score"] >= 78 and estimate else "先预实验再报价" if best["fit_score"] >= 60 else "暂缓接单"
    return {
        "status": "matched",
        "identity": {k: record[k] for k in ("name", "english_name", "cas", "pubchem_cid", "formula", "molecular_weight", "smiles")} | {"matched": True},
        "routes": routes, "recommended_route": best,
        "summary": {"公开路线": f"{len(routes)} 条", "路线成熟度": best["route_evidence"], "纯化难度": difficulty, "放大难度": scale, "成本水平": cost_level},
        "decision": "值得进入路线复核与小试评估" if best["fit_score"] >= 70 else "补充路线证据后再决定是否报价",
        "commercial_decision": {
            "grade": grade, "confidence": confidence,
            "why": f"优先路线匹配分 {best['fit_score']}，经验总收率约 {best['estimated_overall_yield_pct']}%，交付 {spec['quantity_kg']} kg / {spec.get('required_delivery_days',30)} 天",
            "stop_conditions": ["原始实施例无法复现", "关键原料交期超过交付窗口", "安全或设备缺口无法在报价内解决"],
        },
        "purification": {
            "expected_assay_range_pct": best["expected_assay_range_pct"],
            "recommendation": " → ".join(best["purification"]),
            "target_purity_pct": spec["purity_pct"], "improvement_actions": improvement,
            "basis": "按公开路线的单元操作给出经验范围，不是该项目保证值；需用粗品杂质和实验数据校正。",
        },
        "blockers": blockers,
        "next_actions": ["打开来源并复核原始路线", "建立逐步物料衡算和杂质清单", "验证非柱层析纯化方案", "补齐原料询价和工时后冻结报价区间"],
        "evidence_matrix": [
            {"item": "化合物身份", "status": "已匹配"}, {"item": "公开路线", "status": f"{len(routes)} 条，证据 {best['route_evidence']}"},
            {"item": "放大证据", "status": best["scale_evidence"]}, {"item": "成本", "status": "已形成区间" if estimate else "缺少单位物料成本"},
            {"item": "专利/FTO", "status": "未完成法律复核"},
        ],
        "patent_review": patent_review, "cost_estimate": estimate, "sources": record["sources"], "catalog_version": catalog()["version"],
    }

def output(item: OrderAssessment) -> dict:
    spec = OrderInput(**item.data["spec"]).model_dump()
    return {"id": item.id, **item.data, "spec": spec, "result": assess(spec)}


@router.get("/catalog")
def list_catalog(project_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_project_access(db, project_id, user)
    return [{"name": item["name"], "english_name": item["english_name"], "cas": item["cas"]} for item in catalog()["records"]]


@router.get("/assessments")
def list_assessments(project_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_project_access(db, project_id, user)
    return [output(item) for item in db.scalars(select(OrderAssessment).where(OrderAssessment.project_id == project_id)).all()]


@router.post("/assessments")
def create_assessment(project_id: str, payload: OrderInput, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    require_project_access(db, project_id, user, "member")
    if len(db.scalars(select(OrderAssessment.id).where(OrderAssessment.project_id == project_id)).all()) >= 100:
        raise HTTPException(422, "每个项目最多保留 100 份订单评估")
    spec = payload.model_dump()
    data = {"spec": spec, "result": assess(spec), "created_at": datetime.now(timezone.utc).isoformat()}
    item = OrderAssessment(project_id=project_id, data=data)
    db.add(item); db.flush()
    db.add(AuditEvent(project_id=project_id, actor_id=user.id, action="process.order_assessed", entity_type="process_order_assessment", entity_id=item.id, details=data))
    db.commit(); db.refresh(item)
    return output(item)
