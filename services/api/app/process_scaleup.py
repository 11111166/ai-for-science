"""Transparent material, energy, equipment, cost and transferable process-design calculations."""
import csv
import html
import io
import json
import math
import zipfile
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import ForeignKey, JSON, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .auth import UserContext, get_current_user, require_project_access
from .database import Base, get_db
from .models import AuditEvent, new_id

router = APIRouter(prefix="/api/v1/drug-discovery/projects/{project_id}/process/scale-up")


class ScaleAssessment(Base):
    __tablename__ = "process_scale_assessments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    data: Mapped[dict] = mapped_column(JSON)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)


class Material(Strict):
    name: str = Field(min_length=1, max_length=80)
    role: Literal["limiting", "reagent", "catalyst"]
    molecular_weight: float = Field(gt=0, le=5000)
    equivalents: float = Field(gt=0, le=100)
    phase: Literal["solid", "liquid", "gas", "resin"] = "solid"
    unit_cost_cny_per_kg: float | None = Field(default=None, gt=0, le=10_000_000)


class ScaleInput(Strict):
    name: str = Field(min_length=1, max_length=160)
    product: str = Field(min_length=1, max_length=160)
    product_state: Literal["solid", "liquid", "gas"] = "solid"
    process_system: Literal["homogeneous_liquid", "solid_liquid", "liquid_resin", "gas_liquid"] = "homogeneous_liquid"
    product_molecular_weight: float = Field(gt=0, le=5000)
    product_density_kg_l: float | None = Field(default=None, gt=0, le=5)
    target_purity_pct: float = Field(default=98, gt=0, le=100)
    target_product_kg: float = Field(gt=0, le=1_000_000)
    conversion_pct: float = Field(gt=0, le=100)
    isolation_yield_pct: float = Field(gt=0, le=100)
    product_concentration_kg_l: float = Field(gt=0, le=10)
    temperature_c: float = Field(ge=-100, le=500)
    pressure_bar: float = Field(gt=0, le=500)
    reaction_time_h: float = Field(gt=0, le=1000)
    separation: Literal["萃取", "过滤", "结晶", "蒸馏", "气体净化", "柱层析"]
    solvent: str = Field(min_length=1, max_length=80)
    solvent_density_kg_l: float = Field(gt=0, le=5)
    heat_capacity_kj_kg_k: float = Field(gt=0, le=20, default=2.0)
    reaction_enthalpy_kj_mol: float | None = Field(default=None, ge=-5000, le=5000)
    separation_energy_kwh: float = Field(default=0, ge=0, le=100_000_000)
    solvent_unit_cost_cny_per_kg: float | None = Field(default=None, gt=0, le=10_000_000)
    utility_cost_cny_per_kwh: float = Field(default=0.8, ge=0, le=1000)
    waste_cost_cny_per_kg: float = Field(default=3, ge=0, le=100_000)
    fill_fraction: float = Field(gt=.3, le=.9, default=.7)
    corrosive: bool = False
    materials: list[Material] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_materials(self):
        if sum(item.role == "limiting" for item in self.materials) != 1:
            raise ValueError("须且只能有一个限量原料")
        if len({item.name for item in self.materials}) != len(self.materials):
            raise ValueError("物料名称不能重复")
        return self


STANDARD_VESSELS = [10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000]


def round_value(value: float, digits: int = 3) -> float:
    return round(float(value), digits)


def calculate(spec: dict) -> dict:
    conversion = spec["conversion_pct"] / 100
    isolation = spec["isolation_yield_pct"] / 100
    product_kmol = spec["target_product_kg"] / spec["product_molecular_weight"]
    limiting_kmol = product_kmol / (conversion * isolation)
    material_rows = []
    total_raw_kg = 0.0
    known_raw_cost = 0.0
    missing_costs = []
    for item in spec["materials"]:
        kg = limiting_kmol * item["equivalents"] * item["molecular_weight"]
        total_raw_kg += kg
        cost = kg * item["unit_cost_cny_per_kg"] if item.get("unit_cost_cny_per_kg") else None
        known_raw_cost += cost or 0
        if cost is None:
            missing_costs.append(item["name"])
        material_rows.append({
            **item,
            "amount_kmol": round_value(limiting_kmol * item["equivalents"], 4),
            "amount_kg": round_value(kg),
            "estimated_cost_cny": round_value(cost, 2) if cost is not None else None,
        })

    reaction_volume_l = spec["target_product_kg"] / spec["product_concentration_kg_l"]
    nominal_l = reaction_volume_l / spec["fill_fraction"]
    vessel_l = next((size for size in STANDARD_VESSELS if size >= nominal_l), math.ceil(nominal_l / 10000) * 10000)
    solvent_l = reaction_volume_l
    solvent_kg = solvent_l * spec["solvent_density_kg_l"]
    solvent_cost = solvent_kg * spec["solvent_unit_cost_cny_per_kg"] if spec.get("solvent_unit_cost_cny_per_kg") else None
    charge_kg = total_raw_kg + solvent_kg
    delta_t = abs(spec["temperature_c"] - 25)
    sensible_kwh = charge_kg * spec["heat_capacity_kj_kg_k"] * delta_t / 3600
    reaction_kwh = abs(spec["reaction_enthalpy_kj_mol"]) * limiting_kmol * 1000 / 3600 if spec.get("reaction_enthalpy_kj_mol") is not None else None
    total_energy_kwh = sensible_kwh + (reaction_kwh or 0) + spec.get("separation_energy_kwh", 0)
    unreacted_limiting_kg = next(row["amount_kg"] for row in material_rows if row["role"] == "limiting") * (1 - conversion)
    reaction_product_kg = spec["target_product_kg"] / isolation
    isolation_loss_kg = reaction_product_kg - spec["target_product_kg"]
    estimated_waste_kg = max(0, charge_kg - spec["target_product_kg"])
    pmi = charge_kg / spec["target_product_kg"]
    e_factor = estimated_waste_kg / spec["target_product_kg"]

    pressure_service = spec["pressure_bar"] > 1.2 or spec["process_system"] == "gas_liquid"
    reactor_by_system = {
        "homogeneous_liquid": "带搅拌间歇反应釜",
        "solid_liquid": "高扭矩浆态反应釜",
        "liquid_resin": "低剪切树脂浆态反应釜",
        "gas_liquid": "带气体分布器的压力反应釜",
    }
    reactor_type = reactor_by_system[spec["process_system"]]
    if pressure_service and spec["process_system"] != "gas_liquid":
        reactor_type = "带搅拌压力反应釜"
    construction = "搪玻璃或经相容性确认的耐蚀合金" if spec["corrosive"] else "316L 不锈钢（须做介质相容性复核）"
    downstream = {
        "萃取": "液液分离罐", "过滤": "密闭过滤器", "结晶": "结晶釜与过滤器",
        "蒸馏": "蒸馏釜与冷凝器", "气体净化": "气体洗涤、干燥与收集系统", "柱层析": "制备色谱系统",
    }[spec["separation"]]
    mixing_basis = {
        "homogeneous_liquid": "确认混合时间、传热系数和加料点局部浓度",
        "solid_liquid": "确认最小悬浮转速、扭矩、粒径和过滤性能",
        "liquid_resin": "确认树脂溶胀、低剪切悬浮、磨损、过滤和树脂回收",
        "gas_liquid": "确认 kLa、气体消耗、搅拌/压力敏感性和尾气组成",
    }[spec["process_system"]]
    equipment = [
        {"tag": "R-101", "type": reactor_type, "suggested_size_l": vessel_l, "working_volume_l": round_value(reaction_volume_l),
         "fill_pct": round_value(reaction_volume_l / vessel_l * 100, 1), "material": construction, "purpose": "反应与温度控制",
         "selection_basis": mixing_basis, "lab_data_needed": ["实测密度、黏度和相态变化", "量热/热稳定性", "混合或传质敏感性"]},
        {"tag": "S-101", "type": downstream, "suggested_size_l": vessel_l, "working_volume_l": round_value(reaction_volume_l),
         "fill_pct": round_value(reaction_volume_l / vessel_l * 100, 1), "material": construction, "purpose": spec["separation"],
         "selection_basis": "按目标物态、主要纯化方式和工作体积初选", "lab_data_needed": ["分离收率和物料闭合", "周期、持液与清洗", "产品/杂质在各物流中的归趋"]},
    ]
    pfd = ["原料暂存", "计量投料", reactor_type, downstream]
    if spec["product_state"] == "gas":
        pfd += ["气体分析", "压缩或充装", "产品暂存"]
    elif spec["product_state"] == "liquid":
        if spec["separation"] != "蒸馏":
            pfd.append("浓缩或溶剂置换")
        pfd += ["精制液体暂存", "产品灌装"]
    else:
        if spec["separation"] not in {"结晶", "蒸馏"}:
            pfd.append("浓缩")
        if spec["separation"] != "结晶":
            pfd.append("结晶或成盐（待路线确认）")
        pfd += ["过滤", "干燥", "产品暂存"]

    purity_ranges = {
        "萃取": (80, 97), "过滤": (85, 98), "结晶": (95, 99.5),
        "蒸馏": (95, 99.5), "气体净化": (90, 99.9), "柱层析": (97, 99.9),
    }
    purity_low, purity_high = purity_ranges[spec["separation"]]
    improvement_actions = {
        "萃取": ["测 pH—分配比并优化相比", "用多次小体积萃取比较总回收", "验证乳化、第三相和目标物持液"],
        "过滤": ["测滤液—时间曲线和滤饼比阻", "优化粒径、温度、压差和分段洗涤", "核对滤饼含湿与产品损失"],
        "结晶": ["建立溶解度和亚稳区数据", "优化晶种、过饱和生成和熟化", "联动验证晶型、过滤、洗涤和干燥"],
        "蒸馏": ["测压力下汽液平衡或关键馏分组成", "优化真空、回流和切割点", "核对热稳定性、起泡和冷凝能力"],
        "气体净化": ["测原气和尾气组成", "筛选吸收、吸附、干燥或膜分离组合", "核对压降、泄漏、尾气和充装规格"],
        "柱层析": ["比较结晶、萃取、打浆或吸附替代", "降低上样负荷并回收溶剂", "仅在规模和周期可接受时保留"],
    }[spec["separation"]]

    flags = []
    if pressure_service:
        flags.append("压力操作需完成压力设备、泄放、惰化与联锁设计")
    if spec["temperature_c"] > 120 or spec["temperature_c"] < 0:
        flags.append("温度超出常规区间，需补充热安全与传热验证")
    if spec["process_system"] == "liquid_resin":
        flags.append("树脂体系需确认负载、溶胀、磨损、传质、过滤和批间再用性能")
    if spec["process_system"] == "solid_liquid":
        flags.append("浆料体系需确认最差固含下的悬浮、扭矩、传热和转料")
    if spec["process_system"] == "gas_liquid" or spec["product_state"] == "gas":
        flags.append("气体体系需确认组成、可燃/毒性、气液传质、尾气和压力边界")
    if spec["separation"] == "柱层析":
        flags.append("柱层析通常限制规模化，应优先筛选结晶、萃取或打浆替代方案")
    if spec["target_purity_pct"] > purity_high:
        flags.append(f"目标纯度 {spec['target_purity_pct']}% 高于该单元操作的通用经验上界，需组合纯化或实验证明")
    if spec["isolation_yield_pct"] < 70:
        flags.append("分离收率偏低，放大前应优先优化后处理和纯化")
    if vessel_l > 20000:
        flags.append("单批名义容积较大，需比较分批生产与设备利用率")
    if reaction_kwh is None:
        flags.append("尚未提供反应焓；总热负荷当前只包含显热与用户填写的分离能耗")
    flags.append("尚未纳入完整相平衡、放气、粉尘、腐蚀和清洗验证数据")

    known_material_cost = known_raw_cost + (solvent_cost or 0)
    utility_cost = total_energy_kwh * spec["utility_cost_cny_per_kwh"]
    waste_cost = estimated_waste_kg * spec["waste_cost_cny_per_kg"]
    known_cost = known_material_cost + utility_cost + waste_cost
    cost_complete = not missing_costs and solvent_cost is not None
    cost = {
        "known_material_cny": round_value(known_material_cost, 2),
        "utility_cny": round_value(utility_cost, 2),
        "waste_cny": round_value(waste_cost, 2),
        "known_total_cny": round_value(known_cost, 2),
        "range_cny": [round_value(known_cost * 0.85, 2), round_value(known_cost * 1.35, 2)],
        "per_product_kg_cny": round_value(known_cost / spec["target_product_kg"], 2),
        "complete": cost_complete,
        "missing_prices": missing_costs + ([] if solvent_cost is not None else [spec["solvent"]]),
        "scope": "只含已填写物料、显热/反应热/分离能耗和废物单价；未含人工、折旧、分析、质量、返工、税和利润",
    }
    streams = [
        {"id": "S01", "name": "原料与试剂", "phase": "mixed", "temperature_c": 25, "pressure_bar": 1, "mass_kg_batch": round_value(total_raw_kg), "purpose": "反应进料"},
        {"id": "S02", "name": spec["solvent"], "phase": "liquid", "temperature_c": 25, "pressure_bar": 1, "mass_kg_batch": round_value(solvent_kg), "purpose": "反应溶剂"},
        {"id": "S03", "name": "反应混合物", "phase": spec["process_system"], "temperature_c": spec["temperature_c"], "pressure_bar": spec["pressure_bar"], "mass_kg_batch": round_value(charge_kg), "purpose": "进入分离"},
        {"id": "S04", "name": spec["product"], "phase": spec["product_state"], "temperature_c": 25, "pressure_bar": 1, "mass_kg_batch": round_value(spec["target_product_kg"]), "purpose": "合格产品"},
        {"id": "S05", "name": "母液/废物流", "phase": "mixed", "temperature_c": 25, "pressure_bar": 1, "mass_kg_batch": round_value(estimated_waste_kg), "purpose": "回收或处理"},
    ]
    product_state_info = {"state": spec["product_state"]}
    if spec["product_state"] == "gas":
        product_state_info["estimated_standard_volume_nm3"] = round_value(product_kmol * 22.414, 3)
        product_state_info["note"] = "按 0℃、1 atm 理想气体数量级；实际充装需按组成、温压和状态方程复核"
    elif spec["product_state"] == "liquid" and spec.get("product_density_kg_l"):
        product_state_info["estimated_volume_l"] = round_value(spec["target_product_kg"] / spec["product_density_kg_l"], 3)
        product_state_info["note"] = "按用户填写密度估算"
    else:
        product_state_info["note"] = "以质量交付为基准"

    lab_to_plant = [
        {"dimension": "混合/传质", "lab": "记录桨型、尺寸、转速、加料点和相态", "plant": mixing_basis, "acceptance": "关键转化/杂质对转速或供气不敏感，或已定义控制范围"},
        {"dimension": "热量与温控", "lab": "反应/淬灭量热和失冷情景", "plant": "用实测 UA、冷却能力和加料速率核算", "acceptance": "最大释热速率低于可移热能力并留有工程裕量"},
        {"dimension": "分离与持液", "lab": "闭合产品、杂质、母液和洗液衡算", "plant": "核对过滤面积、分相时间、传料和设备持液", "acceptance": "收率、纯度和周期在设备限制内可重复"},
        {"dimension": "分析与终点", "lab": "验证取样、淬灭和方法专属性", "plant": "定义取样频率、行动限和偏差处理", "acceptance": "终点判定可执行且误差小于工艺差异"},
    ]
    risk = "高" if pressure_service or spec["temperature_c"] > 180 or spec["product_state"] == "gas" else "中" if len(flags) > 2 else "低"
    return {
        "basis": {"isolated_product_kg": spec["target_product_kg"], "product_kmol": round_value(product_kmol, 4),
                  "conversion_pct": spec["conversion_pct"], "isolation_yield_pct": spec["isolation_yield_pct"],
                  "target_purity_pct": spec["target_purity_pct"], "process_system": spec["process_system"]},
        "product_state": product_state_info,
        "materials": material_rows,
        "solvent": {"name": spec["solvent"], "estimated_l": round_value(solvent_l), "estimated_kg": round_value(solvent_kg)},
        "mass_metrics": {"total_input_kg": round_value(charge_kg), "estimated_waste_kg": round_value(estimated_waste_kg),
                         "pmi": round_value(pmi, 2), "e_factor": round_value(e_factor, 2)},
        "losses": {"unreacted_limiting_material_kg": round_value(unreacted_limiting_kg), "isolation_loss_product_equivalent_kg": round_value(isolation_loss_kg)},
        "utilities": {"sensible_kwh": round_value(sensible_kwh, 2), "reaction_kwh": round_value(reaction_kwh, 2) if reaction_kwh is not None else None,
                      "separation_kwh": round_value(spec.get("separation_energy_kwh", 0), 2), "total_kwh": round_value(total_energy_kwh, 2),
                      "temperature_change_c": round_value(delta_t, 1), "scope": "显热由质量、比热和温差计算；反应热和分离能耗仅在用户提供时计入"},
        "purification": {"method": spec["separation"], "expected_assay_range_pct": [purity_low, purity_high],
                         "target_purity_pct": spec["target_purity_pct"], "actions": improvement_actions,
                         "basis": "通用单元操作经验范围，不是对该化合物的保证值；需以溶解度、相平衡、杂质和实验结果校正"},
        "equipment": equipment,
        "materials_of_construction": {"recommended": construction, "basis": "按腐蚀标记、压力与反应体系做概念初选",
                                      "verify": ["介质/清洗剂腐蚀与应力腐蚀", "温压和机械载荷", "密封、衬里、金属离子与产品相容性"]},
        "pfd": pfd,
        "aspen_streams": streams,
        "lab_to_plant": lab_to_plant,
        "cost": cost,
        "risk_level": risk,
        "flags": flags,
        "decision": "补齐工程数据后进入概念放大评审" if risk != "高" else "先完成关键安全、传质与设备可行性评估",
        "next_experiments": ["量热或热稳定性筛查", "混合/传质敏感性试验", "分离操作的小试物料闭合", "至少一次工程尺度确认"],
        "method": "确定性化学计量 + 物料/能量衡算 + 物态/反应体系规则 + 设备材质初选 + 成本缺口分析",
        "version": "process-engineering-package-v2",
    }

def record_output(item: ScaleAssessment) -> dict:
    spec = ScaleInput(**item.data["spec"]).model_dump()
    return {"id": item.id, **item.data, "spec": spec, "result": calculate(spec)}


def _safe_output(item: ScaleAssessment) -> dict:
    """Recompute one assessment, falling back to its stored payload.

    A row written by an earlier release may no longer satisfy the current input
    schema. Degrading that single row keeps the project list usable instead of
    failing the whole request.
    """
    try:
        return record_output(item)
    except Exception:
        data = dict(item.data or {})
        data.setdefault("spec", {})
        data.setdefault("result", {})
        return {"id": item.id, **data}


def get_assessment(db: Session, project_id: str, assessment_id: str, user) -> ScaleAssessment:
    require_project_access(db, project_id, user)
    item = db.get(ScaleAssessment, assessment_id)
    if not item or item.project_id != project_id:
        raise HTTPException(404, "放大评估不存在")
    return item


@router.get("/assessments")
def list_assessments(project_id: str, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    require_project_access(db, project_id, user)
    rows = db.scalars(select(ScaleAssessment).where(ScaleAssessment.project_id == project_id)).all()
    return [_safe_output(item) for item in rows]


@router.post("/assessments")
def create_assessment(project_id: str, payload: ScaleInput, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    require_project_access(db, project_id, user, "member")
    if len(db.scalars(select(ScaleAssessment.id).where(ScaleAssessment.project_id == project_id)).all()) >= 50:
        raise HTTPException(422, "每个项目最多保留 50 份放大评估")
    spec = payload.model_dump()
    data = {"spec": spec, "result": calculate(spec), "created_at": datetime.now(timezone.utc).isoformat()}
    item = ScaleAssessment(project_id=project_id, data=data)
    db.add(item); db.flush()
    db.add(AuditEvent(project_id=project_id, actor_id=user.id, action="process.scale_assessed", entity_type="process_scale_assessment", entity_id=item.id, details=data))
    db.commit(); db.refresh(item)
    return record_output(item)


@router.get("/assessments/{assessment_id}/streams.csv")
def export_streams(project_id: str, assessment_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    item = get_assessment(db, project_id, assessment_id, user)
    output = record_output(item)
    columns = ["id", "name", "phase", "temperature_c", "pressure_bar", "mass_kg_batch", "purpose"]
    buffer = io.StringIO(); writer = csv.DictWriter(buffer, fieldnames=columns)
    writer.writeheader(); writer.writerows(output["result"]["aspen_streams"])
    return Response("\ufeff" + buffer.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="aspen-stream-table.csv"'})


@router.get("/assessments/{assessment_id}/pfd")
def export_pfd_sheet(project_id: str, assessment_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    item = get_assessment(db, project_id, assessment_id, user)
    output = record_output(item)
    steps = output["result"]["pfd"]
    width = max(900, 190 * len(steps)); height = 190
    nodes = []
    for index, step in enumerate(steps):
        x = 25 + index * 185
        nodes.append(f'<rect x="{x}" y="55" width="145" height="60" rx="10" fill="#eef8f4" stroke="#087e6b"/>')
        nodes.append(f'<text x="{x + 72.5}" y="90" text-anchor="middle" font-size="14" fill="#123d35">{html.escape(step)}</text>')
        if index < len(steps) - 1:
            nodes.append(f'<line x1="{x + 145}" y1="85" x2="{x + 180}" y2="85" stroke="#087e6b" marker-end="url(#arrow)"/>')
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto"><path d="M0,0 L0,6 L8,3 z" fill="#087e6b"/></marker></defs>
<rect width="100%" height="100%" fill="white"/><text x="25" y="28" font-size="18" font-weight="700" fill="#123d35">{html.escape(output['spec']['name'])} · 概念 PFD</text>{''.join(nodes)}
<text x="25" y="155" font-size="12" fill="#607c74">概念流程图；设备编号、管径、仪表、联锁和安全系统需由合格工程人员完成。</text></svg>'''
    return Response(svg, media_type="image/svg+xml", headers={"Content-Disposition": 'attachment; filename="concept-pfd.svg"'})


@router.get("/assessments/{assessment_id}/pfd.dxf")
def export_pfd_dxf(project_id: str, assessment_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    item = get_assessment(db, project_id, assessment_id, user)
    output = record_output(item)
    steps = output["result"]["pfd"]
    entities = []
    for index, step in enumerate(steps):
        x = index * 55.0
        for x1, y1, x2, y2 in ((x, 0, x+42, 0), (x+42, 0, x+42, 20), (x+42, 20, x, 20), (x, 20, x, 0)):
            entities.append(f"0\nLINE\n8\nPFD\n10\n{x1}\n20\n{y1}\n11\n{x2}\n21\n{y2}\n")
        label = step.encode("ascii", "replace").decode("ascii")
        entities.append(f"0\nTEXT\n8\nLABELS\n10\n{x+3}\n20\n8\n40\n2.5\n1\n{label}\n")
        if index < len(steps)-1:
            entities.append(f"0\nLINE\n8\nFLOW\n10\n{x+42}\n20\n10\n11\n{x+55}\n21\n10\n")
    dxf = "0\nSECTION\n2\nENTITIES\n" + "".join(entities) + "0\nENDSEC\n0\nEOF\n"
    return Response(dxf, media_type="application/dxf", headers={"Content-Disposition": 'attachment; filename="concept-pfd.dxf"'})


@router.get("/assessments/{assessment_id}/pfd.svg")
def export_pfd_svg_legacy(project_id: str, assessment_id: str, user=Depends(get_current_user)):
    """旧扩展名链接：303 跳转到 /pfd，避免 .svg 被门户按 URL 缓存绕过鉴权。"""
    return RedirectResponse(url="pfd", status_code=303)


@router.get("/assessments/{assessment_id}/aspen-package.zip")
def export_aspen_package(project_id: str, assessment_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Export a license-neutral package plus an opt-in Windows Aspen COM bridge."""
    item = get_assessment(db, project_id, assessment_id, user)
    output = record_output(item)
    streams = output["result"]["aspen_streams"]
    csv_buffer = io.StringIO(); columns = ["id", "name", "phase", "temperature_c", "pressure_bar", "mass_kg_batch", "purpose"]
    writer = csv.DictWriter(csv_buffer, fieldnames=columns); writer.writeheader(); writer.writerows(streams)
    operations = [{"sequence": i + 1, "operation": name, "status": "conceptual", "mapping_required": True}
                  for i, name in enumerate(output["result"]["pfd"])]
    bridge = '''"""Run on a licensed Windows Aspen Plus workstation after reviewing mappings."""
import csv, os
DRY_RUN = os.environ.get("ASPEN_WRITE", "0") != "1"
ARCHIVE = os.environ.get("ASPEN_TEMPLATE", "")
if not ARCHIVE:
    raise SystemExit("Set ASPEN_TEMPLATE to a reviewed .bkp/.apw template")
import win32com.client
document = win32com.client.Dispatch("Apwn.Document")
document.InitFromArchive2(os.path.abspath(ARCHIVE))
with open("streams.csv", encoding="utf-8-sig") as handle:
    for row in csv.DictReader(handle):
        stream = row["id"]
        mappings = {
            "TEMP": float(row["temperature_c"]),
            "PRES": float(row["pressure_bar"]),
            "TOTFLOW": float(row["mass_kg_batch"]),
        }
        for field, value in mappings.items():
            path = rf"\\Data\\Streams\\{stream}\\Input\\{field}\\MIXED"
            node = document.Tree.FindNode(path)
            if node is None:
                print("REVIEW MAPPING", path, value)
            elif DRY_RUN:
                print("DRY RUN", path, value)
            else:
                node.Value = value
if not DRY_RUN:
    document.Engine.Run2()
    document.SaveAs(os.path.abspath("generated-case.apw"))
else:
    print("No Aspen values were written. Set ASPEN_WRITE=1 only after reviewing stream IDs, units and components.")
document.Close(False)
'''
    readme = f"""Aspen 概念工程包：{output['spec']['name']}

1. streams.csv 是带单位的概念物流表。
2. unit-operations.json 是流程步骤，所有单元操作都需在 Aspen 模板中人工映射。
3. assumptions.json 保留本次输入、计算结果和缺失数据。
4. aspen_bridge.py 默认只做映射预览；需 Windows、pywin32、Aspen Plus 许可证和已复核模板。
5. 执行前必须核对物性方法、组分定义、物流 ID、单位、相态、反应和单元操作。

该包不包含 Aspen 许可证，也不代表模拟已收敛或设计已完成。
"""
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("streams.csv", "\ufeff" + csv_buffer.getvalue())
        archive.writestr("unit-operations.json", json.dumps(operations, ensure_ascii=False, indent=2))
        archive.writestr("assumptions.json", json.dumps(output, ensure_ascii=False, indent=2))
        archive.writestr("aspen_bridge.py", bridge)
        archive.writestr("README_CN.txt", readme)
    return Response(payload.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="aspen-concept-package.zip"'})
