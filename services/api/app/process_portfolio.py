"""Versioned product and route portfolios for the nine-module process workflow.

The first slice accepts user-supplied evidence. It does not fetch suppliers,
establish regulatory limits, infer patent clearance, or invent reaction routes.
"""
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import ForeignKey, JSON, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .auth import UserContext, get_current_user, require_project_access
from .database import Base, get_db
from .models import AuditEvent, new_id

router = APIRouter(prefix="/api/v1/drug-discovery/projects/{project_id}/process/portfolios")


class ProcessPortfolio(Base):
    __tablename__ = "process_portfolios"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    data: Mapped[dict] = mapped_column(JSON)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class RouteStep(Strict):
    transformation: str = Field(min_length=2, max_length=240)
    intermediate: str = Field(default="", max_length=180)
    yield_pct: float | None = Field(default=None, ge=0, le=100)
    purification: str = Field(default="", max_length=180)
    source_reference: str = Field(default="", max_length=500)


class CandidateRoute(Strict):
    name: str = Field(min_length=2, max_length=160)
    kind: Literal["literature", "patent", "user", "hypothesis"]
    source_reference: str = Field(default="", max_length=500)
    steps: list[RouteStep] = Field(min_length=1, max_length=15)

    @model_validator(mode="after")
    def require_public_reference(self):
        if self.kind in {"literature", "patent"} and not self.source_reference:
            raise ValueError("公开文献或专利路线必须填写可核对的来源编号或链接")
        return self


class PortfolioInput(Strict):
    target_name: str = Field(min_length=2, max_length=200)
    cas: str = Field(default="", max_length=40)
    smiles: str = Field(default="", max_length=1000)
    product_class: Literal["human_api", "veterinary_api", "intermediate", "other_chemical"]
    intended_use: str = Field(min_length=2, max_length=240)
    jurisdiction: Literal["CN", "US", "EP", "JP", "KR", "other"]
    target_purity_pct: float | None = Field(default=None, gt=0, le=100)
    target_annual_kg: float | None = Field(default=None, gt=0, le=1_000_000_000)
    routes: list[CandidateRoute] = Field(default_factory=list, max_length=5)


def summarize(payload: PortfolioInput) -> dict:
    routes = []
    for route in payload.routes:
        yields = [step.yield_pct for step in route.steps]
        overall = None
        if all(value is not None for value in yields):
            product = 1.0
            for value in yields:
                if value is not None:
                    product *= value / 100
            overall = round(product * 100, 2)
        routes.append({
            "name": route.name,
            "kind": route.kind,
            "step_count": len(route.steps),
            "overall_yield_pct": overall,
            "missing_yield_steps": [index + 1 for index, value in enumerate(yields) if value is None],
            "source_status": "用户提供，待原文核对" if route.kind in {"literature", "patent"} else "用户自拟，待实验" if route.kind == "user" else "规则假设，待实验",
        })
    comparable = [route for route in routes if route["overall_yield_pct"] is not None]
    strongest_yield = max(comparable, key=lambda route: route["overall_yield_pct"]) if comparable else None
    gaps = []
    if not payload.cas and not payload.smiles:
        gaps.append("缺少 CAS 或结构，先核对目标身份及盐型/立体化学")
    if not routes:
        gaps.append("尚无候选路线；添加公开来源或自拟路线")
    elif len(comparable) < 2:
        gaps.append("不足两条具有完整逐步收率的路线，暂不能按总收率比较")
    if not payload.target_purity_pct:
        gaps.append("尚未提供产品质量目标；需按用途与法域拟定并审核")
    gaps.extend(["真实生产商、产能与报价待采购核实", "专利权利要求和当地法律状态待人工复核"])
    if payload.product_class in {"human_api", "veterinary_api"}:
        gaps.append("杂质、残留溶剂、元素杂质等质量限度待法规与分析人员确认")
    return {
        "route_summaries": routes,
        "published_route_count": sum(route.kind in {"literature", "patent"} for route in payload.routes),
        "user_route_count": sum(route.kind == "user" for route in payload.routes),
        "best_reported_yield_route": strongest_yield,
        "scorecard": [
            {"module": "产品/中间体筛选", "status": "需补证", "finding": "目标已登记；供应商与生产商待核实"},
            {"module": "多路线工艺开发", "status": "可比较" if len(comparable) >= 2 else "需补证", "finding": f"已录入 {len(routes)} 条候选路线，{len(comparable)} 条有完整逐步收率"},
            {"module": "法规与质量目标", "status": "需审核" if payload.target_purity_pct else "需补证", "finding": f"用户目标纯度 {payload.target_purity_pct}%（非法规限度）" if payload.target_purity_pct else "目标纯度与适用限度待确定"},
            {"module": "全球专利与 FTO", "status": "需补证", "finding": f"{payload.jurisdiction} 法域权利要求与法律状态待核查"},
            {"module": "实验开发", "status": "需补证", "finding": "逐步确认实验与备料量待路线条件和分析方法补齐"},
            {"module": "连续流/微通道", "status": "需补证", "finding": "需逐步取得反应热、动力学及堵塞风险数据"},
            {"module": "Aspen + 工程放大", "status": "需补证", "finding": "需物性、热数据和验证后路线"},
            {"module": "CAD/PFD", "status": "需补证", "finding": "需冻结物流与设备初选后生成图纸"},
            {"module": "技术经济与最终决策", "status": "需补证", "finding": "采购报价、回收率、人工及公用工程成本待补齐"},
        ],
        "gaps": gaps,
        "decision": "先复核来源、质量目标和关键步骤，再开展实验与报价",
    }


def output(item: ProcessPortfolio) -> dict:
    payload = PortfolioInput(**item.data["input"])
    return {"id": item.id, "created_at": item.data["created_at"], "input": payload.model_dump(), "result": summarize(payload)}


@router.get("/public-case/fluralaner")
def fluralaner_public_case(project_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_project_access(db, project_id, user)
    from .fluralaner_public_case import build_public_case
    return build_public_case()


@router.get("")
def list_portfolios(project_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_project_access(db, project_id, user)
    rows = db.scalars(select(ProcessPortfolio).where(ProcessPortfolio.project_id == project_id)).all()
    return [output(item) for item in rows]


@router.post("")
def create_portfolio(project_id: str, payload: PortfolioInput, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    require_project_access(db, project_id, user, "member")
    if len(db.scalars(select(ProcessPortfolio.id).where(ProcessPortfolio.project_id == project_id)).all()) >= 100:
        raise HTTPException(422, "每个项目最多保留 100 份产品工艺档案")
    data = {"input": payload.model_dump(), "created_at": datetime.now(timezone.utc).isoformat()}
    item = ProcessPortfolio(project_id=project_id, data=data)
    db.add(item)
    db.flush()
    db.add(AuditEvent(project_id=project_id, actor_id=user.id, action="process.portfolio_created", entity_type="process_portfolio", entity_id=item.id, details={"target_name": payload.target_name, "routes": len(payload.routes)}))
    db.commit()
    db.refresh(item)
    return output(item)
