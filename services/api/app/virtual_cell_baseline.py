"""Transparent cell-context baseline for diseases without a dedicated scRNA-seq snapshot.

This module deliberately does not impersonate a perturbational single-cell model.  It
combines the upstream disease/target scores, the intervention-direction hypothesis and
a versioned disease-family cell-context dictionary to rank *where to validate first*.
The API keeps the result schema close to Virtual Cell v1 so the product can compare
candidates, while every result carries an explicit analysis tier and boundary.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any, Iterable


MODEL_VERSION = "cell-context-prior-v1"


@dataclass(frozen=True)
class ContextPrior:
    name: str
    weight: float
    rationale: str


CONTEXT_PROFILES: tuple[tuple[tuple[str, ...], str, tuple[ContextPrior, ...]], ...] = (
    (("alzheimer", "dementia", "parkinson", "neuro", "脑", "痴呆", "帕金森"), "神经系统",
     (ContextPrior("小胶质细胞", 1.00, "优先观察神经炎症、吞噬和髓系免疫反应"),
      ContextPrior("兴奋性神经元", 0.94, "优先观察突触、应激和神经元存活相关变化"),
      ContextPrior("星形胶质细胞", 0.88, "补充评估代谢支持、炎症放大和稳态维持"))),
    (("cancer", "carcinoma", "sarcoma", "leukemia", "lymphoma", "tumor", "癌", "瘤", "白血病"), "肿瘤",
     (ContextPrior("肿瘤细胞", 1.00, "优先观察增殖、凋亡和药物敏感性"),
      ContextPrior("肿瘤相关巨噬细胞", 0.90, "评估免疫抑制和微环境重塑"),
      ContextPrior("细胞毒性 T 细胞", 0.86, "评估抗肿瘤免疫和免疫逃逸"))),
    (("arthritis", "lupus", "crohn", "colitis", "sclerosis", "autoimmune", "关节炎", "狼疮", "克罗恩", "免疫"), "免疫炎症",
     (ContextPrior("CD4 阳性 T 细胞", 1.00, "优先观察炎症激活和辅助性 T 细胞状态"),
      ContextPrior("B 细胞", 0.92, "评估抗体反应、抗原呈递和异常活化"),
      ContextPrior("单核/巨噬细胞", 0.88, "评估先天免疫和炎症因子释放"))),
    (("pulmonary", "lung", "asthma", "copd", "fibrosis", "肺", "哮喘", "纤维化"), "呼吸系统",
     (ContextPrior("肺泡上皮细胞", 1.00, "优先观察屏障损伤、修复和应激反应"),
      ContextPrior("肺成纤维细胞", 0.94, "评估基质沉积和纤维化程序"),
      ContextPrior("肺泡巨噬细胞", 0.88, "评估炎症和组织清除反应"))),
    (("cardio", "heart", "atherosclerosis", "hypertension", "心", "动脉", "高血压"), "心血管",
     (ContextPrior("血管内皮细胞", 1.00, "优先观察血管屏障、炎症和血管生成"),
      ContextPrior("血管平滑肌细胞", 0.91, "评估血管重塑和收缩表型"),
      ContextPrior("心肌细胞", 0.86, "补充观察收缩、能量代谢和细胞应激"))),
    (("diabetes", "obesity", "metabolic", "liver", "hepatic", "糖尿病", "肥胖", "肝"), "代谢/肝脏",
     (ContextPrior("肝细胞", 1.00, "优先观察脂质、糖代谢和药物代谢相关变化"),
      ContextPrior("脂肪细胞", 0.90, "评估胰岛素反应和脂质储存"),
      ContextPrior("组织驻留巨噬细胞", 0.85, "评估代谢炎症和组织稳态"))),
    (("renal", "kidney", "neph", "肾"), "肾脏",
     (ContextPrior("近端肾小管细胞", 1.00, "优先观察重吸收、代谢和损伤反应"),
      ContextPrior("足细胞", 0.91, "评估滤过屏障完整性"),
      ContextPrior("肾脏内皮细胞", 0.85, "评估微血管和炎症反应"))),
)

DEFAULT_CONTEXTS = (
    ContextPrior("疾病相关实质细胞", 1.00, "优先在疾病主要受累组织的功能细胞中验证"),
    ContextPrior("组织驻留免疫细胞", 0.90, "评估免疫激活、炎症和细胞间通讯"),
    ContextPrior("血管/基质支持细胞", 0.84, "评估微环境、屏障和组织重塑"),
)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return min(high, max(low, value))


def _number(value: Any, default: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _stable_unit(value: str) -> float:
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") / 0xFFFFFFFF


def disease_context_profile(disease_name: str) -> tuple[str, tuple[ContextPrior, ...]]:
    normalized = str(disease_name or "").casefold()
    for keywords, family, contexts in CONTEXT_PROFILES:
        if any(keyword.casefold() in normalized for keyword in keywords):
            return family, contexts
    return "通用疾病", DEFAULT_CONTEXTS


def _intervention_payload(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    return getattr(value, "result", None) or {}


def build_virtual_cell_baselines(
    targets: Iterable[Any],
    disease_name: str,
    interventions: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Build differentiated cell-context validation hypotheses for real targets."""

    interventions = interventions or {}
    family, priors = disease_context_profile(disease_name)
    results: list[dict[str, Any]] = []
    for index, target in enumerate(targets, 1):
        symbol = str(getattr(target, "symbol", "") or "").upper()
        intervention = _intervention_payload(interventions.get(getattr(target, "id", "")) or interventions.get(symbol))
        causal = _number(getattr(target, "causal_confidence", None), _number(getattr(target, "score", None), 0.35))
        disease_context = _number(getattr(target, "disease_context", None), _number(getattr(target, "evidence_coverage", None), 0.30))
        target_score = _number(getattr(target, "score", None), 0.30)
        source_count = max(0, int(getattr(target, "source_count", 0) or 0))
        direction_confidence = _number(intervention.get("direction_confidence"), 0.50 if getattr(target, "direction", "") in {"inhibit", "activate"} else 0.35)
        direction = str(intervention.get("direction") or getattr(target, "direction", None) or "inhibit")
        if direction not in {"inhibit", "activate"}:
            direction = "inhibit"
        base = _clamp(0.34 * causal + 0.28 * disease_context + 0.20 * target_score + 0.18 * direction_confidence)
        confidence = _clamp(0.30 + 0.22 * min(1.0, source_count / 4) + 0.16 * direction_confidence + 0.12 * disease_context, 0.32, 0.78)
        context_rows = []
        for context_index, prior in enumerate(priors):
            jitter = 0.88 + 0.20 * _stable_unit(f"{symbol}|{prior.name}|{MODEL_VERSION}")
            context_score = _clamp(base * prior.weight * jitter, 0.05, 0.93)
            context_rows.append({
                "cell_type": prior.name,
                "context_priority_score": round(context_score, 6),
                "projected_reversal": round(context_score, 6),
                "context_confidence": round(_clamp(confidence * prior.weight, 0.25, 0.78), 6),
                "rationale": prior.rationale,
                "basis": [
                    f"疾病家族：{family}",
                    f"上游疾病—靶点证据 {target_score:.2f}",
                    f"干预方向：{'优先抑制' if direction == 'inhibit' else '优先激活/恢复'}",
                ],
                "data_sources": ["Open Targets 疾病—靶点证据", "Target-to-Hit v3", "虚拟干预方向 v2", "版本化疾病细胞情境词典"],
            })
        context_rows.sort(key=lambda row: (-row["context_priority_score"], str(row["cell_type"])))
        best = context_rows[0]
        estimate = float(best["context_priority_score"])
        half_width = 0.10 + 0.16 * (1 - confidence)
        results.append({
            "symbol": symbol,
            "approved_name": str(getattr(target, "name", "") or symbol),
            "rank": int(getattr(target, "rank", 0) or index),
            "direction": direction,
            "direction_label": "优先抑制" if direction == "inhibit" else "优先激活/恢复",
            "analysis_tier": "context_prior_baseline",
            "metric_type": "cell_context_validation_priority",
            "best_cell_type": best["cell_type"],
            "cell_context_count": len(context_rows),
            "context_priority_score": round(estimate, 6),
            # Compatibility aliases; the UI relabels them for this tier.
            "projected_reversal": round(estimate, 6),
            "projected_reversal_ci95": [round(_clamp(estimate - half_width), 6), round(_clamp(estimate + half_width), 6)],
            "confidence": round(confidence, 6),
            "state_axis_auroc": None,
            "contexts": context_rows,
            "evidence_factors": {
                "causal_support": round(causal, 6),
                "disease_context": round(disease_context, 6),
                "upstream_target_score": round(target_score, 6),
                "direction_confidence": round(direction_confidence, 6),
                "independent_source_count": source_count,
            },
            "decision_summary": f"建议先在{best['cell_type']}中验证{symbol}的{'抑制' if direction == 'inhibit' else '激活/恢复'}效应，再扩展到其他相关细胞。",
            "recommended_validation": [
                f"在{best['cell_type']}相关模型中进行双向扰动和剂量梯度实验",
                "同时测量疾病核心表型、细胞活性和关键通路读出",
                "获得病例/对照单细胞数据后升级为供体级反事实投影",
            ],
            "boundary": "该结果是细胞情境验证优先级，不是单细胞表达实测、药物处理后转录组或临床疗效预测。",
            "scientific_status": "transparent_context_prior_hypothesis",
        })

    values = [float(item["context_priority_score"]) for item in results]
    confidence_values = [float(item["confidence"]) for item in results]
    benchmark = {
        "version": "cell-context-prior-benchmark-v1",
        "candidate_count": len(results),
        "context_family": family,
        "context_template_count": len(priors),
        "direction_coverage": sum(item["direction"] in {"inhibit", "activate"} for item in results) / max(1, len(results)),
        "priority_distribution": {
            "minimum": round(min(values), 6) if values else None,
            "median": round(sorted(values)[len(values) // 2], 6) if values else None,
            "maximum": round(max(values), 6) if values else None,
            "unique_values": len(set(values)),
        },
        "confidence_distribution": {
            "minimum": round(min(confidence_values), 6) if confidence_values else None,
            "median": round(sorted(confidence_values)[len(confidence_values) // 2], 6) if confidence_values else None,
            "maximum": round(max(confidence_values), 6) if confidence_values else None,
        },
        "quality_gates": {
            "real_upstream_targets": bool(results),
            "direction_available": bool(results) and all(item["direction"] in {"inhibit", "activate"} for item in results),
            "differentiated_scores": len(set(values)) >= min(3, len(values)),
            "passed": bool(results) and all(item["direction"] in {"inhibit", "activate"} for item in results),
        },
    }
    qc = {
        "statistical_unit": "not_applicable_context_prior",
        "dedicated_single_cell_data": False,
        "input_target_count": len(results),
        "context_templates": len(priors),
    }
    provenance = {
        "source": "Open Targets + Target-to-Hit + intervention direction + versioned disease-context dictionary",
        "snapshot_id": f"runtime-{MODEL_VERSION}",
        "model_version": MODEL_VERSION,
        "scientific_status": "transparent_context_prior_hypothesis",
        "data_mode": "real_public_evidence_computational_baseline",
        "scientific_conclusion": False,
    }
    for item in results:
        item["benchmark"] = benchmark
        item["qc"] = qc
        item["provenance"] = provenance
    return results, benchmark, qc, provenance
