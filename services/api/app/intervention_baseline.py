"""Transparent fallback for diseases without a dedicated perturbation snapshot.

The baseline intentionally ranks *what to validate next*.  It does not infer a
causal inhibition/activation direction and it is not a disease-state reversal
model.  All inputs are already persisted on the upstream target candidate.
"""

from __future__ import annotations

from typing import Any


MODEL_VERSION = "evidence-intervention-direction-v2"


def _clamp(value: float | int | None) -> float:
    if value is None:
        return 0.0
    return min(1.0, max(0.0, float(value)))


def _text(value: Any) -> str:
    return str(value or "").strip().lower().replace("_", " ").replace("-", " ")


def _record_value(record: Any, field: str) -> Any:
    return record.get(field) if isinstance(record, dict) else getattr(record, field, None)


def _direction_from_evidence(target_effect: Any, trait_effect: Any) -> str | None:
    """Translate directional human evidence into a therapeutic hypothesis."""
    target_text = _text(target_effect)
    trait_text = _text(trait_effect)
    loss = "lof" in target_text or "loss of function" in target_text
    gain = "gof" in target_text or "gain of function" in target_text
    risk = "risk" in trait_text or "increase" in trait_text
    protect = "protect" in trait_text or "decrease" in trait_text
    if (loss and protect) or (gain and risk):
        return "inhibit"
    if (loss and risk) or (gain and protect):
        return "activate"
    return None


def _infer_direction(
    target: Any,
    evidence_records: list[Any] | None,
    knockout_profile: dict[str, Any] | None,
) -> dict[str, Any]:
    raw_direction = _text(getattr(target, "direction", ""))
    if raw_direction in {"inhibit", "activate"}:
        opposite = "activate" if raw_direction == "inhibit" else "inhibit"
        return {
            "direction": raw_direction,
            "direction_status": "upstream_supported",
            "direction_confidence": 0.86,
            "direction_support": {raw_direction: 0.90, opposite: 0.10},
            "direction_margin": 0.80,
            "direction_evidence": [{
                "source": "上游新靶点或疾病专属分析",
                "signal": "已有明确的建议干预方向",
                "conclusion": "支持抑制" if raw_direction == "inhibit" else "支持激活",
                "strength": "较强",
                "limitation": "仍需功能实验确认剂量、细胞类型和时序",
            }],
            "counter_signals": [],
        }

    weighted = {"inhibit": 0.0, "activate": 0.0}
    directional_rows: list[dict[str, Any]] = []
    for record in evidence_records or []:
        target_effect = _record_value(record, "direction_on_target")
        trait_effect = _record_value(record, "direction_on_trait")
        inferred = _direction_from_evidence(target_effect, trait_effect)
        if not inferred:
            continue
        score = _clamp(_record_value(record, "score"))
        datasource = str(_record_value(record, "datasource") or _record_value(record, "source") or "公开方向证据")
        reliability = 1.0 if datasource in {"clinical_precedence", "chembl"} else 0.9
        contribution = max(0.15, score) * reliability
        weighted[inferred] += contribution
        directional_rows.append({
            "source": datasource,
            "signal": f"靶点效应 {target_effect or '—'}；疾病效应 {trait_effect or '—'}",
            "conclusion": "支持抑制" if inferred == "inhibit" else "支持激活",
            "strength": "较强" if contribution >= 0.7 else "中等" if contribution >= 0.4 else "较弱",
            "weight": round(contribution, 3),
            "limitation": "数据库方向字段是人群或临床证据，不等同于当前疾病模型中的实测药效",
        })
    total = weighted["inhibit"] + weighted["activate"]
    if total > 0:
        inhibit_support = weighted["inhibit"] / total
        activate_support = weighted["activate"] / total
        direction = "inhibit" if inhibit_support >= activate_support else "activate"
        consensus = max(inhibit_support, activate_support)
        margin = abs(inhibit_support - activate_support)
        if margin >= 0.12:
            return {
                "direction": direction,
                "direction_status": "directional_evidence_supported" if len(directional_rows) >= 2 and consensus >= 0.67 else "directional_evidence_limited",
                "direction_confidence": round(min(0.90, 0.58 + 0.28 * consensus + min(0.04, len(directional_rows) * 0.01)), 3),
                "direction_support": {"inhibit": round(inhibit_support, 3), "activate": round(activate_support, 3)},
                "direction_margin": round(margin, 3),
                "direction_evidence": directional_rows[:8],
                "counter_signals": [item for item in directional_rows if item["conclusion"] != ("支持抑制" if direction == "inhibit" else "支持激活")][:4],
            }

    # Product-grade fallback: when no signed disease evidence is available, use
    # a transparent, conservative first-experiment prior instead of returning a
    # blank result.  Inhibition is the default because loss-of-function assays
    # and inhibitors are the most direct validation route; negative regulators
    # receive an activation/restoration hypothesis.  DepMap modifies safety and
    # confidence, never masquerading as disease efficacy evidence.
    name = _text(getattr(target, "name", ""))
    activation_terms = ("negative regulator", "suppressor", "inhibitor of", "anti inflammatory", "protective")
    direction = "activate" if any(term in name for term in activation_terms) else "inhibit"
    breakdown = getattr(target, "evidence_breakdown", None) or {}
    held_out = breakdown.get("held_out") or {}
    target_to_hit = breakdown.get("target_to_hit") or {}
    known_drug = bool(held_out.get("known_drug_label"))
    tractable = bool(target_to_hit.get("has_small_molecule_binder") or target_to_hit.get("has_high_quality_probe"))
    tolerance = _clamp((knockout_profile or {}).get("knockout_tolerability")) if knockout_profile else None
    base = 0.58 + (0.05 if known_drug else 0.0) + (0.04 if tractable else 0.0)
    if tolerance is not None:
        # High tolerance makes an inhibition experiment easier to interpret;
        # low tolerance is a counter-signal rather than proof of activation.
        base += 0.06 * (tolerance - 0.5)
    base = min(0.74, max(0.54, base))
    preferred = base
    other = 1.0 - preferred
    support = {direction: round(preferred, 3), "activate" if direction == "inhibit" else "inhibit": round(other, 3)}
    causal = _clamp(getattr(target, "causal_confidence", None))
    context = _clamp(getattr(target, "disease_context", None))
    coverage = _clamp(getattr(target, "evidence_coverage", None))
    evidence = [{
        "source": "上游疾病—靶点证据",
        "signal": f"因果线索 {causal:.3f}；疾病情境 {context:.3f}；证据覆盖 {coverage:.3f}",
        "conclusion": "支持把该靶点纳入干预验证",
        "strength": "中等" if max(causal, context) >= 0.4 else "较弱",
        "limitation": "关联强度说明靶点相关，但单独不能决定抑制或激活",
    }, {
        "source": "可解释方向先验 v2",
        "signal": "缺少带正负号的疾病证据，先选择最直接的功能验证路线",
        "conclusion": "优先做抑制实验" if direction == "inhibit" else "优先做激活/恢复实验",
        "strength": "中等偏弱",
        "weight": round(preferred, 3),
        "limitation": "这是首轮实验假设，不是已证实的治疗机制",
    }]
    if known_drug:
        evidence.append({"source": "已知药物标签", "signal": "公开数据中已有药物研究记录", "conclusion": "提高方向假设的可执行性", "strength": "中等", "limitation": "标签本身不证明对当前疾病有效"})
    if tractable:
        evidence.append({"source": "Target-to-Hit", "signal": "已有结合分子、口袋或化学探针线索", "conclusion": "更容易开展首轮功能降低实验", "strength": "中等", "limitation": "可成药性不等于疾病方向正确"})
    counter_signals: list[dict[str, Any]] = []
    if tolerance is not None:
        risk = 1.0 - tolerance
        evidence.append({"source": "DepMap 24Q4", "signal": f"敲除耐受度 {tolerance:.3f}", "conclusion": "抑制实验安全窗口相对可评估" if risk < 0.5 else "完全抑制可能影响细胞存活", "strength": "中等" if abs(tolerance - 0.5) >= 0.2 else "较弱", "limitation": "以癌细胞系为主，只用于广泛必需性风险参考"})
        if risk >= 0.5:
            counter_signals.append({"source": "DepMap 24Q4", "signal": f"广泛依赖风险约 {risk:.3f}", "conclusion": "优先测试部分抑制、可逆抑制或组织选择性方案"})
    return {
        "direction": direction,
        "direction_status": "model_hypothesis",
        "direction_confidence": round(preferred, 3),
        "direction_support": support,
        "direction_margin": round(preferred - other, 3),
        "direction_evidence": evidence,
        "counter_signals": counter_signals,
    }


def build_intervention_baseline(
    target: Any,
    disease_name: str,
    evidence_records: list[Any] | None = None,
    knockout_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a bounded, explainable validation-priority profile.

    ``reversal_score`` remains populated by the caller for API compatibility,
    but the metric is explicitly named ``intervention_validation_priority`` in
    the result payload and product UI.
    """
    causal = _clamp(getattr(target, "causal_confidence", None))
    context = _clamp(getattr(target, "disease_context", None))
    coverage = _clamp(getattr(target, "evidence_coverage", None))
    source_count = max(0, int(getattr(target, "source_count", None) or 0))
    source_diversity = min(1.0, source_count / 6.0)
    target_score = _clamp(getattr(target, "score", None))

    # Disease evidence dominates; feasibility is deliberately excluded because
    # it answers developability, not whether a biological intervention works.
    priority = round(
        0.35 * causal
        + 0.35 * context
        + 0.15 * coverage
        + 0.10 * source_diversity
        + 0.05 * target_score,
        6,
    )
    # Missing direction-specific and perturbational data caps confidence.  This
    # prevents a strong association score from looking like experimental proof.
    confidence = round(min(0.74, 0.12 + 0.30 * coverage + 0.22 * source_diversity + 0.10 * max(causal, context)), 6)

    direction_result = _infer_direction(target, evidence_records, knockout_profile)
    direction = direction_result["direction"]
    direction_zh = {"inhibit": "优先验证抑制", "activate": "优先验证激活"}.get(direction, "抑制或激活方向待确认")
    direction_prefix = "证据建议" if direction_result["direction_status"] in {"upstream_supported", "directional_evidence_supported"} else "模型推测"
    target_to_hit = (getattr(target, "evidence_breakdown", None) or {}).get("target_to_hit") or {}

    return {
        "symbol": target.symbol,
        "analysis_tier": "evidence_baseline",
        "metric_type": "intervention_validation_priority",
        "model_version": MODEL_VERSION,
        "direction": direction,
        **direction_result,
        "direction_label": f"{direction_prefix}：{direction_zh}",
        "direction_component": None,
        "single_cell_fdr": None,
        "genetic_support": causal,
        "network_module_reach": context,
        "evidence_coverage": coverage,
        "source_count": source_count,
        "source_diversity": round(source_diversity, 6),
        "target_score": target_score,
        "virtual_intervention_score": priority,
        "confidence": confidence,
        "evidence_grade": "moderate" if confidence >= 0.55 else "preliminary",
        "effect_interpretation": "direction_requires_disease_specific_validation",
        "decision_summary": f"{direction_prefix}{direction_zh}；建议先做剂量梯度和反向救援实验，再决定是否进入 {disease_name} 的化合物路线。",
        "basis": [
            "疾病—靶点因果线索",
            "疾病情境证据",
            "证据覆盖率",
            "独立数据类型数量",
        ],
        "missing_evidence": [
            "疾病专属病例/对照单细胞方向",
            "该靶点的扰动转录组或功能实验",
        ],
        "recommended_experiments": [
            "CRISPRi/CRISPRa 或 siRNA/过表达做双向验证",
            "设置至少三个剂量或干预强度并观察疾病相关表型",
            "用反向救援实验确认表型确由该靶点引起",
        ],
        "development_route": {
            "readiness": target_to_hit.get("level"),
            "recommended_modality": target_to_hit.get("recommended_modality"),
            "chemistry_start_score": target_to_hit.get("chemistry_start_score"),
            "biologic_start_score": target_to_hit.get("biologic_start_score"),
        },
        "boundary": "这是产品级首轮干预假设和验证优先级，不是疾病状态逆转实测或临床疗效结论；“模型推测”必须通过双向功能实验确认。",
        "provenance": {
            "source": "上游版本化疾病—靶点公开数据快照",
            "snapshot_id": getattr(target, "source_version", None) or "upstream-target-job",
            "model_version": MODEL_VERSION,
            "data_mode": "real",
            "scientific_conclusion": False,
        },
    }
