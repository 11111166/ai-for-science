"""Read-only product adapter for the transparent Novel Target Discovery v1 snapshot."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .open_targets import OpenTargetsError, normalize_disease_text
from .snapshot_index import SnapshotHeader, read_snapshot_header


SCHEMA_VERSION = "novel-target-discovery-product-snapshot-v1"


class NovelTargetSnapshotAdapter:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self.adapters: list[NovelTargetSnapshotAdapter] = []
        self.snapshot_headers: list[SnapshotHeader] = []
        if self.path.is_dir():
            paths = sorted(self.path.rglob("novel-target-discovery-v1.json"))
            if not paths:
                raise OpenTargetsError("NOVEL_TARGET_SNAPSHOT_INVALID", "新靶点快照目录中没有可用结果。")
            self.snapshot_headers = [read_snapshot_header(
                item, schema_version=SCHEMA_VERSION, error_code="NOVEL_TARGET_SNAPSHOT_INVALID",
                error_message="无法读取已配置的新靶点发现快照。", require_quality_passed=False,
            ) for item in paths]
            self.labels_by_path = {header.path: {normalize_disease_text(value) for value in [
                header.disease.get("id"), header.disease.get("name"), header.disease.get("name_zh"),
                *(header.disease.get("aliases") or []),
            ] if value} for header in self.snapshot_headers}
            self.labels = set().union(*self.labels_by_path.values())
            self.disease = {}
            return
        try:
            encoded = self.path.read_bytes()
            self.payload = json.loads(encoded)
        except (OSError, json.JSONDecodeError) as exc:
            raise OpenTargetsError(
                "NOVEL_TARGET_SNAPSHOT_INVALID",
                "无法读取已配置的新靶点发现快照。",
                {"cause": type(exc).__name__},
            ) from exc
        if self.payload.get("schema_version") != SCHEMA_VERSION:
            raise OpenTargetsError("NOVEL_TARGET_SNAPSHOT_SCHEMA_MISMATCH", "新靶点发现快照的格式版本不受支持。")
        self.digest = hashlib.sha256(encoded).hexdigest()
        disease = self.payload.get("disease") or {}
        self.disease = disease
        labels = [disease.get("id"), disease.get("name"), disease.get("name_zh"), *(disease.get("aliases") or [])]
        self.labels = {normalize_disease_text(label) for label in labels if label}

    def _select(self, query_text: str) -> NovelTargetSnapshotAdapter:
        matches = [header for header in self.snapshot_headers if normalize_disease_text(query_text) in self.labels_by_path[header.path]]
        if len(matches) == 1:
            return NovelTargetSnapshotAdapter(matches[0].path)
        if len(matches) > 1:
            raise OpenTargetsError("DISEASE_AMBIGUOUS", "疾病同时匹配多个新靶点快照，请使用标准疾病本体 ID。")
        available = [header.disease.get("name_zh") or header.disease.get("name") for header in self.snapshot_headers]
        raise OpenTargetsError("NOVEL_TARGET_DISEASE_NOT_FOUND", "该疾病尚无通过质量门的新靶点快照。", {"query": query_text, "available_diseases": available})

    def resolve_disease(self, query_text: str) -> dict[str, Any]:
        if self.snapshot_headers:
            return self._select(query_text).resolve_disease(query_text)
        if normalize_disease_text(query_text) not in self.labels:
            raise OpenTargetsError(
                "NOVEL_TARGET_DISEASE_NOT_FOUND",
                "当前配置的新靶点快照尚未验证该疾病。",
                {"query": query_text, "available_diseases": [self.disease.get("name"), self.disease.get("name_zh")]},
            )
        disease_id = self.disease["id"]
        return {
            "query": query_text,
            "canonical_name": self.disease["name"],
            "disease_id": disease_id,
            "ontology": disease_id.split("_", 1)[0],
            "synonyms": [label for label in [self.disease.get("name_zh"), *(self.disease.get("aliases") or [])] if label],
            "source": "AI for Science 新靶点发现快照",
            "resolution_status": "resolved",
            "resolution_confidence": 1.0,
            "resolution_method": "versioned_novel_target_snapshot_exact_alias_v1",
            "raw_reference": f"file://{self.path}#disease={disease_id}",
            "cache_hit": True,
        }

    @staticmethod
    def _category(score: float | None, datatypes: list[str]) -> dict[str, Any]:
        return {"status": "available" if score is not None and score > 0 else "unavailable", "score": score, "datatypes": datatypes}

    def discover(self, query_text: str, top_n: int = 50, include_indirect: bool = False, include_evidence_records: bool = True) -> dict[str, Any]:
        if self.snapshot_headers:
            return self._select(query_text).discover(query_text, top_n, include_indirect, include_evidence_records)
        if not 1 <= top_n <= 200:
            raise OpenTargetsError("INVALID_TOP_N", "候选靶点数量必须在 1 到 200 之间。")
        resolution = self.resolve_disease(query_text)
        selected = sorted(self.payload.get("targets", []), key=lambda item: (item["rank"], item["target_id"]))[:top_n]
        try:
            retrieved_at = datetime.fromisoformat(self.payload["created_at"])
        except (KeyError, ValueError):
            retrieved_at = datetime.now(timezone.utc)
        if retrieved_at.tzinfo is None:
            retrieved_at = retrieved_at.replace(tzinfo=timezone.utc)

        modality_contract = {
            "gwas_score": ("遗传学", "gwas", "GWAS", "GWAS 关联与精细定位证据"),
            "eqtl_score": ("遗传学", "eqtl", "eQTL 共定位", "疾病位点与表达数量性状位点共定位证据"),
            "single_cell_score": ("表达", "single_cell", "疾病单细胞", "供体×细胞类型伪汇总的病例/对照差异信号"),
            "network_score": ("网络", "network", "蛋白互作网络", "由直接证据种子产生的有界网络传播信号"),
        }
        targets: list[dict[str, Any]] = []
        evidence: list[dict[str, Any]] = []
        disease_id = resolution["disease_id"]
        score_contract_version = str((self.payload.get("score_contract") or {}).get("version") or "novel-target-score-v1")
        score_origin = "ai4science_novel_target_score_v2" if score_contract_version.endswith("v2") else "ai4science_novel_target_score_v1"
        for item in selected:
            direct_count = int(item.get("direct_modality_count") or 0)
            categories = {
                "gwas": self._category(item.get("gwas_score"), ["gwas"]),
                "eqtl": self._category(item.get("eqtl_score"), ["eqtl"]),
                "single_cell": self._category(item.get("single_cell_score"), ["single_cell"]),
                "network": self._category(item.get("network_score"), ["network"]),
                "novelty": self._category(item.get("novelty_score"), ["novelty"]),
                "development_feasibility": self._category(item.get("development_feasibility"), ["tractability"]),
            }
            breakdown = {
                "registry_version": self.payload["source_registry_version"],
                "score_contract_version": self.payload["score_contract"]["version"],
                "discovery_strategy": "novel",
                "categories": categories,
                "axes": {
                    "evidence_strength": item["evidence_strength"],
                    "novelty": item["novelty_score"],
                    "development_feasibility": item["development_feasibility"],
                },
                "features": {
                    **{key: item.get(key) for key in modality_contract},
                    "eqtl_h4": item.get("eqtl_h4"), "eqtl_clpp": item.get("eqtl_clpp"),
                    "fine_mapped_loci": item.get("fine_mapped_loci"),
                    "gwas_catalog_studies": item.get("gwas_catalog_studies"),
                },
                "candidate_origin": item.get("candidate_origin"),
                "novelty_class": item.get("novelty_class"),
                "single_cell_context": {
                    "cell_type": item.get("best_cell_type"),
                    "log2_fold_change": item.get("single_cell_log2fc"),
                    "fdr": item.get("single_cell_fdr"),
                    "case_donors": item.get("case_donors"),
                    "control_donors": item.get("control_donors"),
                },
                "held_out": {"known_drug_label": bool(item.get("known_drug_label"))},
            }
            targets.append({
                "external_id": item["target_id"], "symbol": item["symbol"], "name": item["approved_name"], "biotype": "protein_coding",
                "rank": int(item["rank"]), "score": float(item["novel_target_score"]), "score_origin": score_origin,
                "derived_score": float(item["novel_target_score"]), "causal_confidence": float(item["evidence_strength"]),
                "disease_context": float(item["novelty_score"]), "development_feasibility": float(item["development_feasibility"]),
                "evidence_coverage": direct_count / 3.0, "evidence_count": direct_count, "source_count": direct_count,
                "evidence_breakdown": breakdown, "direction": item.get("suggested_direction") or "unknown",
                "raw_reference": resolution["raw_reference"] + f"&target={item['target_id']}",
            })
            for score_key, (category, datatype, source, summary) in modality_contract.items():
                score = float(item.get(score_key) or 0.0)
                if score <= 0:
                    continue
                details = {"snapshot_id": self.payload["snapshot_id"], "score_key": score_key}
                if score_key == "gwas_score":
                    details.update({"fine_mapped_loci": item.get("fine_mapped_loci"), "gwas_catalog_studies": item.get("gwas_catalog_studies")})
                elif score_key == "eqtl_score":
                    details.update({
                        "colocalised_loci": item.get("colocalised_loci"), "biosamples": item.get("eqtl_biosamples"),
                        "h4": item.get("eqtl_h4"), "clpp": item.get("eqtl_clpp"),
                        "calibration": "0.60*H4 + 0.40*CLPP/(CLPP+0.02)",
                    })
                elif score_key == "single_cell_score":
                    details.update(breakdown["single_cell_context"])
                evidence.append({
                    "target_external_id": item["target_id"], "external_evidence_id": f"{disease_id}:{item['target_id']}:{datatype}",
                    "disease_id": disease_id, "evidence_type": "novel_target_modality_score_v1", "datatype": datatype,
                    "datasource": datatype, "category": category, "source": source, "summary": summary, "score": score,
                    "direction_on_target": item.get("suggested_direction") if score_key == "single_cell_score" else None,
                    "direction_on_trait": None, "evidence_count": 1, "confidence": item.get("novelty_class"),
                    "raw_reference": resolution["raw_reference"] + f"&target={item['target_id']}&modality={datatype}", "details": details,
                })

        quality = self.payload.get("quality", {})
        benchmark = quality.get("benchmark", {})
        build = quality.get("build", {})
        return {
            "metadata": {"source": "AI for Science 新靶点发现快照", "source_version": self.payload["snapshot_id"], "api_version": "offline-novel-v1", "endpoint": f"file://{self.path}"},
            "resolution": resolution,
            "parameters": {"top_n": top_n, "include_indirect": False, "requested_include_indirect": include_indirect, "discovery_strategy": "novel"},
            "targets": targets, "evidence": evidence,
            "data_quality": {
                "disease_resolution_status": "resolved", "canonical_disease_id": disease_id, "targets_retrieved": len(targets),
                "normalized_evidence_records": len(evidence), "evidence_materialization": "transparent_multimodal_aggregates_v1",
                "targets_with_genetics": sum(float(item.get("gwas_score") or 0) > 0 or float(item.get("eqtl_score") or 0) > 0 for item in selected),
                "targets_with_known_drug": sum(bool(item.get("known_drug_label")) for item in selected),
                "coverage": {"mean": round(sum(int(item.get("direct_modality_count") or 0) / 3 for item in selected) / len(selected), 6) if selected else None},
                "source": "AI for Science 新靶点发现快照", "source_version": self.payload["snapshot_id"],
                "source_registry_version": self.payload["source_registry_version"], "score_contract_version": self.payload["score_contract"]["version"],
                "snapshot_sha256": self.digest, "discovery_strategy": "novel", "scientific_status": self.payload.get("scientific_status"),
                "benchmark_summary": benchmark, "build_counts": build.get("counts", {}), "cache_hit": True,
                "published_target_count": len(self.payload.get("targets", [])),
                "warnings": ["COMPUTATIONAL_HYPOTHESES_REQUIRE_EXTERNAL_AND_EXPERIMENTAL_VALIDATION"], "errors": [],
            },
            "benchmark": benchmark, "warnings": ["COMPUTATIONAL_HYPOTHESES_REQUIRE_EXTERNAL_AND_EXPERIMENTAL_VALIDATION"],
            "raw_reference": resolution["raw_reference"], "retrieved_at": retrieved_at,
        }


def novel_target_availability(path: str | Path | None, query: str) -> dict[str, Any]:
    """Use the deployed snapshot registry, not the marketing disease catalogue."""
    result: dict[str, Any] = {"query": query, "available": False, "diseases": []}
    if not path:
        return {**result, "code": "NOVEL_TARGET_NOT_CONFIGURED", "message": "新靶点探索数据尚未配置，可先分析已有证据靶点。"}
    try:
        adapter = NovelTargetSnapshotAdapter(path)
        diseases = [header.disease for header in adapter.snapshot_headers] or [adapter.disease]
        result["diseases"] = [{"id": d.get("id"), "name": d.get("name_zh") or d.get("name")} for d in diseases]
        selected = adapter._select(query) if adapter.snapshot_headers else adapter
        resolution = selected.resolve_disease(query)
        if not selected.payload.get("targets"):
            return {**result, "code": "NOVEL_TARGET_EMPTY", "message": "该疾病的新靶点数据没有可用候选，请先分析已有证据靶点。"}
        return {**result, "available": True, "code": "AVAILABLE", "disease_id": resolution["disease_id"], "message": "疾病专属新靶点数据已就绪。"}
    except OpenTargetsError as exc:
        message = "该疾病尚未接入新靶点探索所需的多组学数据，可先分析已有证据靶点。" if exc.code == "NOVEL_TARGET_DISEASE_NOT_FOUND" else "新靶点数据暂不可用，请稍后重新检查。"
        return {**result, "code": exc.code, "message": message}
