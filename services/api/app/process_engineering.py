"""Project-scoped, transparent process-engineering calculators.

The calculations are screening/design-basis calculations.  Every response keeps
its equation, assumptions and the measurements required before scale-up.
"""
import math
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from .auth import UserContext, get_current_user, require_project_access
from .database import get_db

router = APIRouter(prefix="/api/v1/drug-discovery/projects/{project_id}/process/engineering")
R = 8.314462618


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)


def rounded(value: float | None, digits: int = 4):
    return None if value is None else round(value, digits)


def access(project_id: str, db: Session, user: UserContext):
    require_project_access(db, project_id, user)


class AntoineComponent(Strict):
    name: str = Field(min_length=1, max_length=80)
    mole_fraction: float = Field(ge=0, le=1)
    antoine_a: float = Field(ge=-100, le=100)
    antoine_b: float = Field(gt=0, le=10000)
    antoine_c: float = Field(ge=-500, le=1000)


class ThermodynamicsInput(Strict):
    temperature_c: float = Field(ge=-100, le=400)
    pressure_kpa: float = Field(gt=0, le=50_000)
    model: Literal["ideal", "nrtl"] = "ideal"
    nrtl_tau12: float = Field(default=0, ge=-20, le=20)
    nrtl_tau21: float = Field(default=0, ge=-20, le=20)
    nrtl_alpha: float = Field(default=.3, gt=0, le=1)
    components: list[AntoineComponent] = Field(min_length=2, max_length=2)

    @model_validator(mode="after")
    def fractions(self):
        if abs(sum(x.mole_fraction for x in self.components) - 1) > .001:
            raise ValueError("两个组分的液相摩尔分数之和必须为 1")
        return self


def psat_kpa(component: AntoineComponent, temperature_c: float) -> float:
    denominator = component.antoine_c + temperature_c
    if abs(denominator) < 1e-9:
        raise ValueError("Antoine 参数在该温度下无定义")
    return 10 ** (component.antoine_a - component.antoine_b / denominator) * .133322368


def nrtl_gammas(spec: ThermodynamicsInput) -> tuple[float, float]:
    if spec.model == "ideal":
        return 1.0, 1.0
    x1, x2 = (x.mole_fraction for x in spec.components)
    t12, t21, alpha = spec.nrtl_tau12, spec.nrtl_tau21, spec.nrtl_alpha
    g12, g21 = math.exp(-alpha * t12), math.exp(-alpha * t21)
    d12, d21 = x2 + x1 * g12, x1 + x2 * g21
    ln1 = x2 * x2 * (t21 * (g21 / d21) ** 2 + t12 * g12 / d12**2)
    ln2 = x1 * x1 * (t12 * (g12 / d12) ** 2 + t21 * g21 / d21**2)
    return math.exp(ln1), math.exp(ln2)


def thermodynamics(spec: ThermodynamicsInput) -> dict:
    gammas = nrtl_gammas(spec)
    p_sats = [psat_kpa(c, spec.temperature_c) for c in spec.components]
    partial = [c.mole_fraction * g * p for c, g, p in zip(spec.components, gammas, p_sats)]
    bubble = sum(partial)
    vapor = [p / bubble for p in partial]

    def residual(temp: float) -> float:
        return sum(c.mole_fraction * g * psat_kpa(c, temp) for c, g in zip(spec.components, gammas)) - spec.pressure_kpa

    low, high = -80.0, 300.0
    bubble_temperature = None
    try:
        if residual(low) * residual(high) <= 0:
            for _ in range(80):
                mid = (low + high) / 2
                if residual(low) * residual(mid) <= 0:
                    high = mid
                else:
                    low = mid
            bubble_temperature = (low + high) / 2
    except (OverflowError, ValueError):
        bubble_temperature = None
    rows = [{"name": c.name, "x": rounded(c.mole_fraction), "gamma": rounded(g),
             "psat_kpa": rounded(p), "partial_pressure_kpa": rounded(pp), "y": rounded(y)}
            for c, g, p, pp, y in zip(spec.components, gammas, p_sats, partial, vapor)]
    return {
        "decision": "操作压力下预计沸腾" if bubble >= spec.pressure_kpa else "操作压力下预计仍为液相",
        "bubble_pressure_kpa": rounded(bubble), "bubble_temperature_c": rounded(bubble_temperature, 2),
        "relative_volatility_1_to_2": rounded((gammas[0] * p_sats[0]) / (gammas[1] * p_sats[1])),
        "components": rows, "model": "Raoult" if spec.model == "ideal" else "NRTL + modified Raoult",
        "equation": "P = Σ xᵢγᵢPᵢˢᵃᵗ；yᵢ = xᵢγᵢPᵢˢᵃᵗ/P",
        "assumptions": ["Antoine 参数必须适用于所算温度范围", "气相按理想气体处理", "NRTL 参数必须与组分顺序、温度和单位一致"],
        "required_validation": ["核对组分物性与 Antoine 适用范围", "用实测 VLE/LLE 或可追溯参数回归", "对共沸、电解质、缔合和高压体系使用专用模型"],
        "version": "thermodynamics-screen-v1",
    }


class KineticObservation(Strict):
    temperature_c: float = Field(ge=-100, le=500)
    time_min: float = Field(gt=0, le=1_000_000)
    conversion_pct: float = Field(gt=0, lt=100)


class GasComponent(Strict):
    name: str = Field(min_length=1, max_length=80)
    mole_fraction: float = Field(ge=0, le=1)
    critical_temperature_k: float = Field(gt=0, le=5000)
    critical_pressure_bar: float = Field(gt=0, le=10_000)
    acentric_factor: float = Field(ge=-1, le=5)
    molecular_weight_g_mol: float = Field(gt=0, le=5000)


class GasEosInput(Strict):
    temperature_c: float = Field(ge=-200, le=1000)
    pressure_bar: float = Field(gt=0, le=2000)
    components: list[GasComponent] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def fractions(self):
        if abs(sum(x.mole_fraction for x in self.components) - 1) > .001:
            raise ValueError("气体组分摩尔分数之和必须为 1")
        return self


def cbrt(value: float) -> float:
    return math.copysign(abs(value) ** (1 / 3), value)


def cubic_real_roots(a: float, b: float, c: float) -> list[float]:
    """Real roots of z^3 + a*z^2 + b*z + c = 0."""
    p = b - a * a / 3
    q = 2 * a**3 / 27 - a * b / 3 + c
    discriminant = (q / 2) ** 2 + (p / 3) ** 3
    if discriminant >= 0:
        root = cbrt(-q / 2 + math.sqrt(discriminant)) + cbrt(-q / 2 - math.sqrt(discriminant)) - a / 3
        return [root]
    radius = 2 * math.sqrt(-p / 3)
    angle = math.acos(max(-1.0, min(1.0, (3 * q / (2 * p)) * math.sqrt(-3 / p)))) / 3
    return sorted(radius * math.cos(angle - 2 * math.pi * k / 3) - a / 3 for k in range(3))


def gas_eos(spec: GasEosInput) -> dict:
    temperature = spec.temperature_c + 273.15; pressure = spec.pressure_bar * 100_000
    a_values = []; b_values = []
    for component in spec.components:
        pc = component.critical_pressure_bar * 100_000
        kappa = .37464 + 1.54226 * component.acentric_factor - .26992 * component.acentric_factor**2
        alpha = (1 + kappa * (1 - math.sqrt(temperature / component.critical_temperature_k))) ** 2
        a_values.append(.45724 * R**2 * component.critical_temperature_k**2 / pc * alpha)
        b_values.append(.07780 * R * component.critical_temperature_k / pc)
    am = sum(ci.mole_fraction * cj.mole_fraction * math.sqrt(a_values[i] * a_values[j])
             for i, ci in enumerate(spec.components) for j, cj in enumerate(spec.components))
    bm = sum(c.mole_fraction * b for c, b in zip(spec.components, b_values))
    big_a = am * pressure / (R**2 * temperature**2); big_b = bm * pressure / (R * temperature)
    roots = cubic_real_roots(-(1 - big_b), big_a - 3 * big_b**2 - 2 * big_b, -(big_a * big_b - big_b**2 - big_b**3))
    valid = [z for z in roots if z > big_b and z > 0]
    z = max(valid or roots)
    mw = sum(c.mole_fraction * c.molecular_weight_g_mol for c in spec.components) / 1000
    density = pressure * mw / (z * R * temperature)
    return {
        "decision": "可用于高压气体密度和压缩性初筛，两相区需做闪蒸计算",
        "compressibility_factor_z": rounded(z, 5), "gas_density_kg_m3": rounded(density, 4),
        "mixture_molecular_weight_g_mol": rounded(mw * 1000, 4), "real_roots": [rounded(x, 6) for x in roots],
        "reduced_parameters": {"A": rounded(big_a, 6), "B": rounded(big_b, 6)},
        "equation": "Peng–Robinson: Z³-(1-B)Z²+(A-3B²-2B)Z-(AB-B²-B³)=0",
        "assumptions": ["混合规则的二元交互参数 kij 默认为 0", "返回最大实根作为气相根", "未执行稳定性判定和气液闪蒸"],
        "required_validation": ["从可追溯物性库核对 Tc、Pc 和偏心因子", "含强极性、缔合或电解质体系需更合适的物性方法", "高压设备设计应用认可软件和实测 PVT 数据复核"],
        "version": "peng-robinson-screen-v1",
    }


class KineticsInput(Strict):
    model: Literal["zero", "first", "second"] = "first"
    initial_concentration_mol_l: float = Field(default=1, gt=0, le=100)
    target_temperature_c: float = Field(ge=-100, le=500)
    target_conversion_pct: float = Field(default=95, gt=0, lt=100)
    observations: list[KineticObservation] = Field(min_length=3, max_length=30)

    @model_validator(mode="after")
    def temperatures(self):
        if len({x.temperature_c for x in self.observations}) < 3:
            raise ValueError("激活能拟合至少需要 3 个不同温度")
        return self


def kinetic_constant(model: str, c0: float, conversion: float, time: float) -> float:
    x = conversion / 100
    if model == "zero":
        return c0 * x / time
    if model == "second":
        return x / (c0 * (1 - x) * time)
    return -math.log(1 - x) / time


def kinetics(spec: KineticsInput) -> dict:
    points = []
    for row in spec.observations:
        k = kinetic_constant(spec.model, spec.initial_concentration_mol_l, row.conversion_pct, row.time_min)
        points.append((1 / (row.temperature_c + 273.15), math.log(k), k, row))
    xbar = sum(x for x, *_ in points) / len(points); ybar = sum(y for _, y, *_ in points) / len(points)
    denominator = sum((x - xbar) ** 2 for x, *_ in points)
    slope = sum((x - xbar) * (y - ybar) for x, y, *_ in points) / denominator if denominator else 0
    intercept = ybar - slope * xbar
    predictions = [intercept + slope * x for x, *_ in points]
    ss_res = sum((y - yp) ** 2 for (_, y, *_), yp in zip(points, predictions))
    ss_tot = sum((y - ybar) ** 2 for _, y, *_ in points)
    r2 = 1 - ss_res / ss_tot if ss_tot else 0
    ea = -slope * R / 1000; factor = math.exp(intercept)
    target_k = math.exp(intercept + slope / (spec.target_temperature_c + 273.15))
    xt = spec.target_conversion_pct / 100
    if spec.model == "zero": target_time = spec.initial_concentration_mol_l * xt / target_k
    elif spec.model == "second": target_time = xt / (target_k * spec.initial_concentration_mol_l * (1 - xt))
    else: target_time = -math.log(1 - xt) / target_k
    units = {"zero": "mol/L/min", "first": "1/min", "second": "L/mol/min"}[spec.model]
    return {
        "decision": "可用于下一轮温度/时间设计" if r2 >= .9 else "拟合离散，先补动力学实验",
        "reaction_order_model": spec.model, "activation_energy_kj_mol": rounded(ea, 2),
        "pre_exponential_factor": rounded(factor, 6), "arrhenius_r2": rounded(r2, 4),
        "target": {"temperature_c": spec.target_temperature_c, "conversion_pct": spec.target_conversion_pct,
                   "predicted_time_min": rounded(target_time, 2), "predicted_k": rounded(target_k, 8), "k_unit": units},
        "observations": [{"temperature_c": p[3].temperature_c, "time_min": p[3].time_min,
                          "conversion_pct": p[3].conversion_pct, "k": rounded(p[2], 8)} for p in points],
        "equation": "ln(k)=ln(A)-Ea/(RT)",
        "assumptions": ["每条观测可用同一级数模型表示", "温度和组成在单次实验内近似恒定", "未单独拟合诱导期、可逆反应和传质控制"],
        "required_validation": ["在至少 3 个温度下补时间序列", "比较零/一/二级和机理模型的残差", "用独立批次验证预测时间"],
        "version": "kinetics-arrhenius-v1",
    }


class EquipmentInput(Strict):
    batch_mass_kg: float = Field(gt=0, le=10_000_000)
    mixture_density_kg_l: float = Field(gt=0, le=5)
    fill_fraction: float = Field(default=.7, gt=.3, le=.9)
    agitator_power_kw_m3: float = Field(default=1.5, gt=0, le=100)
    heat_duty_kw: float = Field(gt=0, le=1_000_000)
    overall_u_w_m2_k: float = Field(gt=0, le=10000)
    lmtd_k: float = Field(gt=0, le=500)
    filtration_volume_l: float = Field(gt=0, le=100_000_000)
    filtration_flux_l_m2_h: float = Field(gt=0, le=100_000)
    filtration_time_h: float = Field(gt=0, le=10_000)
    liquid_flow_m3_h: float = Field(gt=0, le=1_000_000)
    liquid_velocity_m_s: float = Field(default=1.5, gt=0, le=20)
    gas_flow_nm3_h: float = Field(default=0, ge=0, le=10_000_000)
    gas_velocity_m_s: float = Field(default=15, gt=0, le=200)
    relative_volatility: float | None = Field(default=None, gt=1, le=1000)
    distillate_light_fraction: float | None = Field(default=None, gt=0, lt=1)
    bottoms_light_fraction: float | None = Field(default=None, gt=0, lt=1)


def equipment(spec: EquipmentInput) -> dict:
    working_m3 = spec.batch_mass_kg / spec.mixture_density_kg_l / 1000
    vessel_m3 = working_m3 / spec.fill_fraction
    heat_area = spec.heat_duty_kw * 1000 / (spec.overall_u_w_m2_k * spec.lmtd_k)
    filter_area = spec.filtration_volume_l / (spec.filtration_flux_l_m2_h * spec.filtration_time_h)
    liquid_d = math.sqrt(4 * spec.liquid_flow_m3_h / 3600 / (math.pi * spec.liquid_velocity_m_s))
    gas_d = math.sqrt(4 * spec.gas_flow_nm3_h / 3600 / (math.pi * spec.gas_velocity_m_s)) if spec.gas_flow_nm3_h else None
    stages = None
    if spec.relative_volatility and spec.distillate_light_fraction and spec.bottoms_light_fraction:
        ratio = (spec.distillate_light_fraction / (1 - spec.distillate_light_fraction)) * ((1 - spec.bottoms_light_fraction) / spec.bottoms_light_fraction)
        stages = math.log(ratio) / math.log(spec.relative_volatility)
    return {
        "decision": "已形成设备初选尺寸，进入供应商/详细设计复核",
        "reactor": {"working_volume_m3": rounded(working_m3, 3), "minimum_geometric_volume_m3": rounded(vessel_m3, 3),
                    "agitator_power_kw": rounded(working_m3 * spec.agitator_power_kw_m3, 2)},
        "heat_transfer": {"required_area_m2": rounded(heat_area, 2), "duty_kw": spec.heat_duty_kw},
        "filtration": {"required_area_m2": rounded(filter_area, 2), "design_area_m2": rounded(filter_area * 1.25, 2)},
        "piping": {"liquid_internal_diameter_mm": rounded(liquid_d * 1000, 1), "gas_internal_diameter_mm": rounded(gas_d * 1000, 1) if gas_d else None},
        "distillation": {"fenske_minimum_theoretical_stages": rounded(stages, 2), "scope": "全回流最小理论级数，实际塔需回流比、效率、压降和热衡算"},
        "equations": ["V设备=V工作/装料率", "A换热=Q/(U·LMTD)", "A过滤=V/(通量·时间)", "D管径=√(4Q/πv)", "Nmin=ln[(xD/(1-xD))((1-xB)/xB)]/lnα"],
        "required_validation": ["实测浆料流变性和混合时间", "用物料体系实测 U 值或做传热保守校核", "用实际滤饼重复测试通量", "按压降、气蚀、噪声和材质复核管径"],
        "version": "equipment-sizing-v1",
    }


class SafetyInput(Strict):
    reaction_enthalpy_kj_mol: float = Field(lt=0, ge=-5000)
    reactive_moles: float = Field(gt=0, le=1_000_000_000)
    reaction_mass_kg: float = Field(gt=0, le=10_000_000)
    heat_capacity_kj_kg_k: float = Field(gt=0, le=20)
    start_temperature_c: float = Field(ge=-100, le=500)
    maximum_safe_temperature_c: float = Field(ge=-100, le=1000)
    decomposition_onset_c: float = Field(ge=-100, le=1000)
    accumulated_fraction: float = Field(default=1, gt=0, le=1)
    maximum_dosing_rate_mol_min: float = Field(gt=0, le=100_000_000)
    available_cooling_kw: float = Field(ge=0, le=10_000_000)
    generated_gas_nm3: float = Field(default=0, ge=0, le=100_000_000)


def safety(spec: SafetyInput) -> dict:
    total_heat = abs(spec.reaction_enthalpy_kj_mol) * spec.reactive_moles
    delta_t = total_heat / (spec.reaction_mass_kg * spec.heat_capacity_kj_kg_k)
    mtsr = spec.start_temperature_c + delta_t * spec.accumulated_fraction
    limit = min(spec.maximum_safe_temperature_c, spec.decomposition_onset_c)
    temperature_margin = limit - mtsr
    release_kw = abs(spec.reaction_enthalpy_kj_mol) * spec.maximum_dosing_rate_mol_min / 60
    cooling_margin = spec.available_cooling_kw - release_kw
    risk = "高" if temperature_margin <= 0 or cooling_margin < 0 else ("中" if temperature_margin < 20 or cooling_margin < release_kw * .3 else "低")
    flags = []
    if temperature_margin <= 0: flags.append("MTSR 达到或超过安全/分解温度上限")
    if cooling_margin < 0: flags.append("最大加料放热速率超过可用冷却能力")
    if spec.generated_gas_nm3 > 0: flags.append("存在产气，需用实验泄放数据完成 DIERS/API 520/521 设计")
    return {
        "decision": "暂停放大，先降低蓄积/加料速率并完成量热" if risk == "高" else "可进入下一轮安全验证",
        "risk_level": risk, "adiabatic_temperature_rise_c": rounded(delta_t, 2), "mtsr_c": rounded(mtsr, 2),
        "temperature_margin_c": rounded(temperature_margin, 2), "maximum_heat_release_kw": rounded(release_kw, 2),
        "cooling_margin_kw": rounded(cooling_margin, 2), "flags": flags or ["本轮筛查未触发红线，仍需实测验证"],
        "equations": ["ΔTad=|ΔH|·n/(m·Cp)", "MTSR=Tprocess+ΔTad·蓄积分数", "Q̇max=|ΔH|·最大加料速率"],
        "required_validation": ["DSC 筛查原料、反应液和中间体分解", "RC1/反应量热获得放热速率和蓄积", "ARC 或等效方法核对绝热分解", "产气工况做两相泄放专业设计", "由工艺安全人员完成 HAZOP/LOPA"],
        "boundary": "本计算不给出泄压口尺寸、SIL 等级或防爆设计结论。",
        "version": "reaction-safety-screen-v1",
    }


class SolidsInput(Strict):
    solvent_mass_kg: float = Field(gt=0, le=10_000_000)
    hot_solubility_g_100g_solvent: float = Field(gt=0, le=10000)
    cold_solubility_g_100g_solvent: float = Field(ge=0, le=10000)
    seed_loading_pct: float = Field(default=.5, ge=0, le=20)
    target_d50_um: float = Field(gt=0, le=100_000)
    slurry_volume_l: float = Field(gt=0, le=100_000_000)
    measured_filter_flux_l_m2_h: float = Field(gt=0, le=100_000)
    target_filtration_time_h: float = Field(gt=0, le=10_000)
    wet_cake_mass_kg: float = Field(gt=0, le=10_000_000)
    initial_moisture_pct: float = Field(gt=0, lt=100)
    final_moisture_pct: float = Field(ge=0, lt=100)
    latent_heat_kj_kg: float = Field(default=2257, gt=0, le=10000)
    dryer_efficiency_pct: float = Field(default=50, gt=0, le=100)

    @model_validator(mode="after")
    def ranges(self):
        if self.cold_solubility_g_100g_solvent >= self.hot_solubility_g_100g_solvent:
            raise ValueError("冷端溶解度必须低于热端溶解度")
        if self.final_moisture_pct >= self.initial_moisture_pct:
            raise ValueError("终点水分必须低于初始水分")
        return self


def solids(spec: SolidsInput) -> dict:
    dissolved_hot = spec.solvent_mass_kg * spec.hot_solubility_g_100g_solvent / 100
    dissolved_cold = spec.solvent_mass_kg * spec.cold_solubility_g_100g_solvent / 100
    crystals = dissolved_hot - dissolved_cold
    recovery = crystals / dissolved_hot * 100
    filter_area = spec.slurry_volume_l / (spec.measured_filter_flux_l_m2_h * spec.target_filtration_time_h) * 1.25
    dry_solids = spec.wet_cake_mass_kg * (1 - spec.initial_moisture_pct / 100)
    final_mass = dry_solids / (1 - spec.final_moisture_pct / 100)
    removed = spec.wet_cake_mass_kg - final_mass
    drying_kwh = removed * spec.latent_heat_kj_kg / 3600 / (spec.dryer_efficiency_pct / 100)
    actions = [f"种晶起始量可从溶质理论质量的 {spec.seed_loading_pct}% 起步，并记录加种时过饱和度", f"目标 D50 {spec.target_d50_um} µm：用降温/滴加速率、搅拌剪切和熟化时间建立设计空间", "分别检测母液和滤饼含量，区分溶解损失与机械损失"]
    return {
        "decision": "先用溶解度曲线和小型过滤/干燥实验锁定操作窗口",
        "crystallization": {"theoretical_crystal_kg": rounded(crystals, 3), "mother_liquor_loss_kg": rounded(dissolved_cold, 3), "theoretical_recovery_pct": rounded(recovery, 2)},
        "filtration": {"minimum_area_m2": rounded(filter_area / 1.25, 2), "design_area_m2": rounded(filter_area, 2), "scale_factor": 1.25},
        "drying": {"solvent_to_remove_kg": rounded(removed, 3), "minimum_thermal_energy_kwh": rounded(drying_kwh, 2)},
        "actions": actions,
        "equations": ["m晶体=m溶剂(S热-S冷)/100", "A过滤=1.25V/(实测通量·目标时间)", "Q干燥=m挥发物·汽化潜热/效率"],
        "required_validation": ["完整溶解度-温度曲线与溶剂化物检查", "FBRM/PVM 或等效方法观察成核和破碎", "晶型、粒径分布、滤饼比阻和洗涤效率", "干燥曲线、残留溶剂、热稳定性和粉体流动性"],
        "version": "solids-engineering-v1",
    }


@router.post("/thermodynamics")
def run_thermodynamics(project_id: str, payload: ThermodynamicsInput, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    access(project_id, db, user); return thermodynamics(payload)


@router.post("/kinetics")
def run_kinetics(project_id: str, payload: KineticsInput, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    access(project_id, db, user); return kinetics(payload)


@router.post("/gas-eos")
def run_gas_eos(project_id: str, payload: GasEosInput, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    access(project_id, db, user); return gas_eos(payload)


@router.post("/equipment")
def run_equipment(project_id: str, payload: EquipmentInput, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    access(project_id, db, user); return equipment(payload)


@router.post("/safety")
def run_safety(project_id: str, payload: SafetyInput, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    access(project_id, db, user); return safety(payload)


@router.post("/solids")
def run_solids(project_id: str, payload: SolidsInput, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    access(project_id, db, user); return solids(payload)
