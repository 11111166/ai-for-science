"""Read-only adapter for the independently evaluated perturbation/knockout snapshot."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .open_targets import OpenTargetsError, normalize_disease_text
from .snapshot_index import SnapshotHeader, read_snapshot_header, sha256_file


SCHEMA_VERSION = "perturbation-knockout-product-snapshot-v1"


class PerturbationKnockoutSnapshotAdapter:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self.adapters: list[PerturbationKnockoutSnapshotAdapter] = []
        self.snapshot_headers: list[SnapshotHeader] = []
        self.selected: PerturbationKnockoutSnapshotAdapter | None = None
        if self.path.is_dir():
            paths = sorted(self.path.rglob("perturbation-knockout-v1.json"))
            if not paths:
                raise OpenTargetsError("PERTURBATION_KNOCKOUT_SNAPSHOT_INVALID", "虚拟干预快照目录中没有可用结果。")
            self.snapshot_headers = [read_snapshot_header(
                item, schema_version=SCHEMA_VERSION,
                error_code="PERTURBATION_KNOCKOUT_SNAPSHOT_INVALID",
                error_message="无法读取已配置的虚拟干预与基因敲除快照。",
            ) for item in paths]
            self.labels_by_path = {
                header.path: {normalize_disease_text(value) for value in [
                    header.disease.get("id"), header.disease.get("name"), header.disease.get("name_zh"),
                    *(header.disease.get("aliases") or []),
                ] if value}
                for header in self.snapshot_headers
            }
            self.labels = set().union(*self.labels_by_path.values())
            self.payload = {"disease": {"name_zh": "已通过质量门的完整验证疾病"}}
            return
        try:
            encoded = self.path.read_bytes()
            self.payload = json.loads(encoded)
        except (OSError, json.JSONDecodeError) as exc:
            raise OpenTargetsError(
                "PERTURBATION_KNOCKOUT_SNAPSHOT_INVALID",
                "无法读取已配置的虚拟干预与基因敲除快照。",
                {"cause": type(exc).__name__},
            ) from exc
        if self.payload.get("schema_version") != SCHEMA_VERSION:
            raise OpenTargetsError("PERTURBATION_KNOCKOUT_SNAPSHOT_SCHEMA_MISMATCH", "虚拟干预与基因敲除快照格式不受支持。")
        if not self.payload.get("quality_passed"):
            raise OpenTargetsError("PERTURBATION_KNOCKOUT_QUALITY_FAILED", "虚拟干预与基因敲除快照未通过质量门。")
        self.digest = hashlib.sha256(encoded).hexdigest()
        disease = self.payload.get("disease") or {}
        labels = [disease.get("id"), disease.get("name"), disease.get("name_zh"), *(disease.get("aliases") or [])]
        self.labels = {normalize_disease_text(value) for value in labels if value}

    def validate_disease(self, query: str) -> None:
        if self.snapshot_headers:
            matches = [header for header in self.snapshot_headers if normalize_disease_text(query) in self.labels_by_path[header.path]]
            if len(matches) == 1:
                self.selected = PerturbationKnockoutSnapshotAdapter(matches[0].path)
                self.payload = self.selected.payload
                self.digest = self.selected.digest
                return
            if len(matches) > 1:
                raise OpenTargetsError("DISEASE_AMBIGUOUS", "疾病同时匹配多个虚拟干预快照，请使用标准疾病本体 ID。")
            raise OpenTargetsError("PERTURBATION_KNOCKOUT_DISEASE_NOT_FOUND", "该疾病尚无通过质量门的虚拟干预与基因敲除快照。", {"query": query})
        if normalize_disease_text(query) not in self.labels:
            raise OpenTargetsError(
                "PERTURBATION_KNOCKOUT_DISEASE_NOT_FOUND",
                "该疾病尚无通过质量门的虚拟干预与基因敲除快照。",
                {"query": query},
            )

    def module(self, module: str) -> dict[str, Any]:
        if self.snapshot_headers:
            if not self.selected:
                raise OpenTargetsError("PERTURBATION_KNOCKOUT_DISEASE_REQUIRED", "请先确认本次任务的疾病实体。")
            return self.selected.module(module)
        value = self.payload.get(module)
        if not isinstance(value, dict) or not isinstance(value.get("results"), list):
            raise OpenTargetsError("PERTURBATION_KNOCKOUT_MODULE_INVALID", "快照缺少请求的科研模块。")
        return value

    def pooled_gene_knockout_module(self) -> dict[str, Any]:
        """Return the union of all DepMap-covered genes across disease packs.

        The perturbation part of each product snapshot is disease-specific, but
        the bundled DepMap Chronos measurements are not.  A gene can therefore
        be evaluated whenever it is present in *any* quality-passed pack.  This
        method deliberately does not mutate ``selected`` or the disease payload.
        """
        sources = [header.path for header in self.snapshot_headers] if self.snapshot_headers else [self.path]
        by_symbol: dict[str, dict[str, Any]] = {}
        benchmarks: list[dict[str, Any]] = []
        model_versions: set[str] = set()
        for path in sources:
            adapter = self if path == self.path and not self.snapshot_headers else PerturbationKnockoutSnapshotAdapter(path)
            module = adapter.module("gene_knockout")
            benchmark = module.get("benchmark")
            if isinstance(benchmark, dict):
                benchmarks.append(benchmark)
            if module.get("model_version"):
                model_versions.add(str(module["model_version"]))
            for item in module["results"]:
                symbol = str(item.get("symbol") or "").upper()
                if not symbol:
                    continue
                current = by_symbol.get(symbol)
                # Prefer an actually screened result over a placeholder.  When
                # duplicates are equivalent, retain the first deterministic row.
                if current is None or (not current.get("screened") and item.get("screened")):
                    by_symbol[symbol] = dict(item)
        screened_count = sum(1 for item in by_symbol.values() if item.get("screened"))
        benchmark = dict(benchmarks[0]) if benchmarks else {}
        benchmark.update({
            "pooled_snapshot_count": len(sources),
            "pooled_unique_gene_count": len(by_symbol),
            "pooled_screened_gene_count": screened_count,
            "coverage_scope": "DepMap 24Q4 跨疾病基因级覆盖",
        })
        return {
            "scientific_status": "measured_cell_fitness_out_of_domain_proxy",
            "model_version": sorted(model_versions)[0] if len(model_versions) == 1 else "depmap-24q4-pooled-v1",
            "benchmark": benchmark,
            "results": sorted(by_symbol.values(), key=lambda item: str(item.get("symbol") or "")),
        }

    def pooled_gene_knockout_provenance(self) -> dict[str, Any]:
        sources = [header.path for header in self.snapshot_headers] if self.snapshot_headers else [self.path]
        digests = [sha256_file(path) for path in sources]
        snapshot_ids = [header.snapshot_id for header in self.snapshot_headers] if self.snapshot_headers else [str(self.payload.get("snapshot_id") or self.path.name)]
        combined_digest = hashlib.sha256("\n".join(digests).encode("utf-8")).hexdigest()
        module = self.pooled_gene_knockout_module()
        return {
            "source": "DepMap 24Q4 Chronos · AI for Science 版本化公开数据快照",
            "snapshot_id": "depmap-24q4-pooled",
            "snapshot_ids": snapshot_ids,
            "snapshot_sha256": combined_digest,
            "module": "gene_knockout",
            "model_version": module.get("model_version"),
            "scientific_status": module.get("scientific_status"),
            "benchmark": module.get("benchmark"),
            "coverage_scope": "gene_level_global",
            "data_mode": "real",
            "scientific_conclusion": False,
        }

    def provenance(self, module: str) -> dict[str, Any]:
        if self.snapshot_headers:
            if not self.selected:
                raise OpenTargetsError("PERTURBATION_KNOCKOUT_DISEASE_REQUIRED", "请先确认本次任务的疾病实体。")
            return self.selected.provenance(module)
        value = self.module(module)
        return {
            "source": "AI for Science 版本化公开数据快照",
            "snapshot_id": self.payload["snapshot_id"],
            "snapshot_sha256": self.digest,
            "source_registry_version": self.payload.get("source_registry_version"),
            "upstream_target_snapshot": self.payload.get("upstream_target_snapshot"),
            "module": module,
            "model_version": value.get("model_version"),
            "scientific_status": value.get("scientific_status"),
            "benchmark": value.get("benchmark"),
            "data_mode": "real",
            "scientific_conclusion": False,
        }
