"""Read-only adapter for the compact RCEES Target Discovery v1 release."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .open_targets import OpenTargetsError, normalize_disease_text


SCHEMA_VERSION = "target-discovery-product-snapshot-v1"


def _route_status(score: float) -> str:
    if score >= 0.60:
        return "supported"
    if score >= 0.35:
        return "possible"
    return "needs_evidence"


def build_target_to_hit_v3(item: dict[str, Any]) -> dict[str, Any]:
    """Build a decision-oriented v3 view from traceable v2 snapshot facts.

    No external structure, essentiality or safety record is inferred here. Those
    missing checks remain explicit so the product never turns absence into proof.
    """
    chemistry = float(item.get("chemistry_start_score") or 0)
    biologic = float(item.get("biologic_start_score") or 0)
    tissue = float(item.get("tissue_selectivity_score") or 0)
    disease = float(item.get("disease_evidence_score") or 0)
    score = float(item.get("target_to_hit_score") or 0)
    pocket, ligand = bool(item.get("hasPocket")), bool(item.get("hasLigand"))
    binder, probe = bool(item.get("hasSmallMoleculeBinder")), bool(item.get("hasHighQualityChemicalProbes"))
    membrane, secreted = bool(item.get("isInMembrane")), bool(item.get("isSecreted"))
    safety_signal = bool(item.get("known_safety_signal"))

    gaps: list[str] = []
    if chemistry < 0.45:
        gaps.append("缺少充分的小分子配体、结合口袋或高质量探针证据")
    if biologic < 0.45:
        gaps.append("膜蛋白或分泌蛋白可及性证据不足")
    if tissue < 0.40:
        gaps.append("正常组织表达较广或选择性证据不足，需要评估系统暴露风险")
    if disease < 0.40:
        gaps.append("疾病遗传或情境证据仍需独立数据复核")
    if safety_signal:
        gaps.append("公共数据中存在已知安全性信号，需要优先复核具体事件")

    structure_basis = [label for present, label in ((pocket, "已有结合口袋线索"), (ligand, "已有配体线索"), (probe, "已有高质量化学探针")) if present]
    structure_level = "strong" if pocket and ligand else "partial" if pocket or ligand or probe else "missing"
    modality = str(item.get("recommended_modality") or "evidence_generation")
    level = str(item.get("target_to_hit_level") or "evidence_gap")
    source_count = sum(float(item.get(key) or 0) > 0 for key in (
        "genetic_association", "genetic_literature", "somatic_mutation", "rna_expression", "affected_pathway", "animal_model"
    ))
    replication_level = "strong" if source_count >= 4 else "moderate" if source_count >= 2 else "limited"

    degradation_score = round(min(1.0, 0.65 * chemistry + 0.20 * disease + 0.15 * tissue), 6)
    gene_medicine_score = round(min(1.0, 0.60 * disease + 0.40 * tissue), 6)
    routes = [
        {
            "id": "small_molecule", "label": "小分子",
            "support_score": round(chemistry, 6), "status": _route_status(chemistry),
            "basis": [label for present, label in ((binder, "已有小分子结合物"), (pocket, "已有口袋线索"), (ligand, "已有配体"), (probe, "已有化学探针")) if present],
            "gaps": [] if chemistry >= 0.45 else ["补充结构检索、口袋评估或结合实验"],
        },
        {
            "id": "antibody_or_protein", "label": "抗体或蛋白药",
            "support_score": round(biologic, 6), "status": _route_status(biologic),
            "basis": [label for present, label in ((membrane, "膜蛋白定位"), (secreted, "分泌蛋白定位")) if present],
            "gaps": [] if biologic >= 0.45 else ["确认细胞外可及性、转运与组织分布"],
        },
        {
            "id": "targeted_degradation", "label": "靶向降解",
            "support_score": degradation_score,
            "status": "not_preferred" if membrane or secreted else "possible" if chemistry >= 0.45 else "needs_evidence",
            "basis": (["具备小分子起点"] if chemistry >= 0.45 else []),
            "gaps": ["尚未接入亚细胞定位、E3 连接酶和降解实验数据"],
        },
        {
            "id": "rna_or_gene_medicine", "label": "RNA 或基因药物",
            "support_score": gene_medicine_score,
            "status": "possible" if disease >= 0.50 and tissue >= 0.35 else "needs_evidence",
            "basis": (["疾病证据较强"] if disease >= 0.50 else []) + (["具有一定组织选择性"] if tissue >= 0.40 else []),
            "gaps": ["尚未评估致病方向、递送组织和长期表达风险"],
        },
    ]

    if level == "ready_for_hit_search":
        decision = "start_hit_search"
    elif max(chemistry, biologic) >= 0.35:
        decision = "validate_structure_or_assay"
    elif disease >= 0.35:
        decision = "validate_disease_biology"
    else:
        decision = "hold_for_evidence"

    next_steps: list[str] = []
    if chemistry < 0.45:
        next_steps.append("检索实验结构或预测结构，并完成口袋与可结合性复核")
    if biologic < 0.45:
        next_steps.append("确认靶点在疾病细胞表面或分泌环境中的可及性")
    if disease < 0.50 or source_count < 2:
        next_steps.append("用独立遗传、患者样本或功能实验复核疾病作用方向")
    if tissue < 0.45:
        next_steps.append("检查关键正常组织表达和潜在剂量限制毒性")
    if safety_signal:
        next_steps.insert(0, "优先复核已登记安全性事件及其可逆性")
    if not next_steps:
        next_steps.append("进入代表性化合物检索，并建立靶点结合与细胞功能实验")

    return {
        "contract_version": "target-to-hit-v3",
        "score": score, "level": level, "recommended_modality": modality,
        "chemistry_start_score": chemistry, "biologic_start_score": biologic,
        "tissue_selectivity_score": tissue, "disease_evidence_score": disease,
        "has_small_molecule_binder": binder, "has_pocket": pocket, "has_ligand": ligand,
        "has_high_quality_probe": probe, "is_membrane": membrane, "is_secreted": secreted,
        "known_safety_signal": safety_signal, "gaps": gaps,
        "safety_policy": "未发现公开安全信号不等于安全；安全和必需性风险不进入准备度奖励分。",
        "decision": decision,
        "evidence_replication": {"source_count": source_count, "level": replication_level},
        "structure_readiness": {"level": structure_level, "basis": structure_basis},
        "safety_review": {"status": "known_signal" if safety_signal else "unresolved", "essentiality_status": "not_assessed_at_target_stage"},
        "routes": routes,
        "next_steps": next_steps[:4],
    }

VALIDATED_DISEASE_LABELS_ZH: dict[str, tuple[str, ...]] = {
    "EFO_0000768": ("特发性肺纤维化", "肺纤维化"),
    "MONDO_0004975": ("阿尔茨海默病", "阿尔兹海默病"),
    "MONDO_0005180": ("帕金森病",),
    "MONDO_0008383": ("类风湿关节炎",),
    "MONDO_0005011": ("克罗恩病",),
    "MONDO_0005148": ("2型糖尿病", "2 型糖尿病"),
    "HP_0001513": ("肥胖症", "肥胖"),
    "MONDO_0005010": ("冠状动脉疾病", "冠心病"),
    "MONDO_0005233": ("非小细胞肺癌",),
    "MONDO_0007254": ("乳腺癌",),
    "MONDO_0007915": ("系统性红斑狼疮", "红斑狼疮"),
    "MONDO_0010679": ("杜氏肌营养不良", "杜氏肌营养不良症"),
    "MONDO_0005083": ("银屑病",),
    "MONDO_0005101": ("溃疡性结肠炎",),
    "MONDO_0004979": ("哮喘",),
    "MONDO_0005300": ("慢性肾病", "慢性肾脏病"),
    "MONDO_0005002": ("慢性阻塞性肺疾病", "慢阻肺"),
    "MONDO_0005575": ("结直肠癌",),
    "MONDO_0007256": ("肝细胞癌", "肝癌"),
    "MONDO_0005105": ("黑色素瘤",),
    "MONDO_0018874": ("急性髓系白血病",),
    "MONDO_0018177": ("胶质母细胞瘤",),
    "MONDO_0009831": ("胰腺癌",),
    "MONDO_0009061": ("囊性纤维化",),
    "MONDO_0001516": ("脊髓性肌萎缩症",),
    "MONDO_0010526": ("法布里病",),
    "MONDO_0018150": ("戈谢病",),
}


class TargetSnapshotAdapter:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        try:
            encoded = self.path.read_bytes()
            self.payload = json.loads(encoded)
        except (OSError, json.JSONDecodeError) as exc:
            raise OpenTargetsError(
                "TARGET_SNAPSHOT_INVALID",
                "The configured Target Discovery snapshot could not be read.",
                {"cause": type(exc).__name__},
            ) from exc
        if self.payload.get("schema_version") != SCHEMA_VERSION:
            raise OpenTargetsError(
                "TARGET_SNAPSHOT_SCHEMA_MISMATCH",
                "The configured Target Discovery snapshot has an unsupported schema.",
            )
        self.digest = hashlib.sha256(encoded).hexdigest()
        self._diseases = {item["id"]: item for item in self.payload.get("diseases", [])}
        self._lookup: dict[str, list[str]] = {}
        for disease in self._diseases.values():
            labels = [
                disease["id"],
                disease["name"],
                disease.get("name_zh"),
                *(disease.get("aliases") or []),
                *VALIDATED_DISEASE_LABELS_ZH.get(disease["id"], ()),
            ]
            for label in filter(None, labels):
                self._lookup.setdefault(normalize_disease_text(label), []).append(disease["id"])

    def resolve_disease(self, query_text: str) -> dict[str, Any]:
        matches = sorted(set(self._lookup.get(normalize_disease_text(query_text), [])))
        if not matches:
            raise OpenTargetsError(
                "TARGET_SNAPSHOT_DISEASE_NOT_FOUND",
                "Disease is not included in the validated Target Discovery v1 snapshot.",
                {"query": query_text, "available_diseases": [item["name"] for item in self._diseases.values()]},
            )
        if len(matches) != 1:
            raise OpenTargetsError("DISEASE_AMBIGUOUS", "Disease query matched more than one snapshot disease.")
        disease = self._diseases[matches[0]]
        return {
            "query": query_text,
            "canonical_name": disease["name"],
            "disease_id": disease["id"],
            "ontology": disease["id"].split("_", 1)[0],
            "synonyms": disease.get("aliases") or [],
            "source": "AI for Science public-data snapshot",
            "resolution_status": "resolved",
            "resolution_confidence": 1.0,
            "resolution_method": "versioned_snapshot_exact_alias_v1",
            "raw_reference": f"file://{self.path}#disease={disease['id']}",
            "cache_hit": True,
        }

    @staticmethod
    def _category(score: float | None, configured: bool = True) -> dict[str, Any]:
        return {
            "status": "available" if score is not None else "unavailable" if configured else "not_configured",
            "score": score,
            "datatypes": [],
        }

    def discover(self, query_text: str, top_n: int = 50, include_indirect: bool = True, include_evidence_records: bool = False) -> dict[str, Any]:
        if not 1 <= top_n <= 100:
            raise OpenTargetsError("INVALID_TOP_N", "top_n must be between 1 and 100.")
        resolution = self.resolve_disease(query_text)
        disease_id = resolution["disease_id"]
        selected = sorted(
            (item for item in self.payload.get("targets", []) if item["disease_id"] == disease_id),
            key=lambda item: (item["rank"], item["target_id"]),
        )[:top_n]
        try:
            retrieved_at = datetime.fromisoformat(self.payload["created_at"])
        except (KeyError, ValueError):
            retrieved_at = datetime.now(timezone.utc)
        if retrieved_at.tzinfo is None:
            retrieved_at = retrieved_at.replace(tzinfo=timezone.utc)

        targets = []
        evidence = []
        evidence_sources = {
            "genetic_association": ("genetics", "Open Targets genetics"),
            "genetic_literature": ("genetics", "Open Targets curated genetics"),
            "somatic_mutation": ("genetics", "Open Targets somatic mutation"),
            "rna_expression": ("expression", "Open Targets expression evidence"),
            "affected_pathway": ("pathway", "Open Targets pathway evidence"),
            "animal_model": ("functional", "Open Targets animal model evidence"),
        }
        for item in selected:
            chemistry_score = float(item.get("chemistry_start_score") or 0)
            biologic_score = float(item.get("biologic_start_score") or 0)
            target_to_hit_score = float(item.get("target_to_hit_score") or 0)
            target_to_hit_level = str(item.get("target_to_hit_level") or "evidence_gap")
            recommended_modality = str(item.get("recommended_modality") or "evidence_generation")
            target_to_hit = build_target_to_hit_v3(item)
            breakdown = {
                "registry_version": self.payload["source_registry_version"],
                "score_contract_version": "three-axis-score-v1",
                "target_to_hit_contract_version": "target-to-hit-v3",
                "categories": {
                    "causal_confidence": self._category(item["causal_confidence"]),
                    "disease_context": self._category(item["disease_context"]),
                    "development_feasibility": self._category(item["development_feasibility"]),
                    "hgnc_identity": self._category(1.0 if item.get("hgnc_status") == "matched" else None),
                    "hpa_context": self._category(1.0 if item.get("hpa_status") == "matched" else None),
                    "known_drug_label": self._category(float(item["known_drug_label"])),
                },
                "axes": {
                    "causal_confidence": item["causal_confidence"],
                    "disease_context": item["disease_context"],
                    "development_feasibility": item["development_feasibility"],
                },
                "features": {key: item.get(key) for key in evidence_sources},
                "identity": {
                    "hgnc_id": item.get("hgnc_id"),
                    "uniprot_ids": item.get("uniprot_ids"),
                    "hgnc_status": item.get("hgnc_status"),
                    "hpa_status": item.get("hpa_status"),
                },
                "held_out": {"known_drug_label": bool(item["known_drug_label"])},
            }
            if "target_to_hit_score" in item:
                breakdown["target_to_hit"] = target_to_hit
            targets.append(
                {
                    "external_id": item["target_id"],
                    "symbol": item["symbol"],
                    "name": item["approved_name"],
                    "biotype": item.get("biotype"),
                    "rank": int(item["rank"]),
                    "score": float(item["discovery_score"]),
                    "score_origin": "ai4science_three_axis_score_v1",
                    "derived_score": float(item["discovery_score"]),
                    "causal_confidence": float(item["causal_confidence"]),
                    "disease_context": float(item["disease_context"]),
                    "development_feasibility": float(item["development_feasibility"]),
                    "evidence_coverage": float(item["evidence_coverage"]),
                    "evidence_count": int(item.get("nonclinical_evidence_count") or 0),
                    "source_count": int(item.get("nonclinical_datatype_count") or 0),
                    "evidence_breakdown": breakdown,
                    "direction": "not_available",
                    "raw_reference": resolution["raw_reference"] + f"&target={item['target_id']}",
                }
            )
            for datatype, (category, source) in evidence_sources.items():
                value = float(item.get(datatype) or 0.0)
                if value <= 0:
                    continue
                evidence.append(
                    {
                        "target_external_id": item["target_id"],
                        "external_evidence_id": f"{disease_id}:{item['target_id']}:{datatype}",
                        "disease_id": disease_id,
                        "evidence_type": "versioned_datatype_aggregate",
                        "datatype": datatype,
                        "datasource": "open_targets",
                        "category": category,
                        "source": source,
                        "summary": f"Versioned direct {datatype} association aggregate.",
                        "score": value,
                        "direction_on_target": None,
                        "direction_on_trait": None,
                        "evidence_count": 1,
                        "confidence": None,
                        "raw_reference": resolution["raw_reference"] + f"&target={item['target_id']}&datatype={datatype}",
                        "details": {"aggregation_level": "datatype", "snapshot_id": self.payload["snapshot_id"]},
                    }
                )

        benchmark = self.payload.get("quality", {}).get("benchmark", {})
        return {
            "metadata": {
                "source": "AI for Science public-data snapshot",
                "source_version": self.payload["snapshot_id"],
                "api_version": "offline-v1",
                "endpoint": f"file://{self.path}",
            },
            "resolution": resolution,
            "parameters": {"top_n": top_n, "include_indirect": False, "requested_include_indirect": include_indirect},
            "targets": targets,
            "evidence": evidence,
            "data_quality": {
                "disease_resolution_status": "resolved",
                "canonical_disease_id": disease_id,
                "targets_retrieved": len(targets),
                "normalized_evidence_records": len(evidence),
                "evidence_materialization": "versioned_datatype_aggregates",
                "targets_with_genetics": sum(item["causal_confidence"] > 0 for item in selected),
                "targets_with_known_drug": sum(bool(item["known_drug_label"]) for item in selected),
                "coverage": {"mean": round(sum(item["evidence_coverage"] for item in selected) / len(selected), 6) if selected else None},
                "source": "AI for Science public-data snapshot",
                "source_version": self.payload["snapshot_id"],
                "source_registry_version": self.payload["source_registry_version"],
                "score_contract_version": "three-axis-score-v1",
                "target_to_hit_contract_version": "target-to-hit-v3",
                "snapshot_sha256": self.digest,
                "benchmark_summary": benchmark.get("macro", {}).get("discovery_score"),
                "cache_hit": True,
                "warnings": ["COMPUTATIONAL_HYPOTHESES_NOT_EXPERIMENTAL_VALIDATION"],
                "errors": [],
            },
            "benchmark": benchmark,
            "warnings": ["COMPUTATIONAL_HYPOTHESES_NOT_EXPERIMENTAL_VALIDATION"],
            "raw_reference": resolution["raw_reference"],
            "retrieved_at": retrieved_at,
        }
