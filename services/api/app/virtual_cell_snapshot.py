"""Read-only adapter for the independently evaluated Virtual Cell v1 snapshot."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .open_targets import OpenTargetsError, normalize_disease_text
from .snapshot_index import SnapshotHeader, read_snapshot_header


SCHEMA_VERSION = "virtual-cell-product-snapshot-v1"


class VirtualCellSnapshotAdapter:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self.adapters: list[VirtualCellSnapshotAdapter] = []
        self.snapshot_headers: list[SnapshotHeader] = []
        self.selected: VirtualCellSnapshotAdapter | None = None
        if self.path.is_dir():
            paths = sorted(self.path.rglob("virtual-cell-v1.json"))
            if not paths:
                raise OpenTargetsError("VIRTUAL_CELL_SNAPSHOT_INVALID", "虚拟细胞快照目录中没有可用结果。")
            self.snapshot_headers = [read_snapshot_header(
                item, schema_version=SCHEMA_VERSION, error_code="VIRTUAL_CELL_SNAPSHOT_INVALID",
                error_message="无法读取已配置的虚拟细胞快照。",
            ) for item in paths]
            self.labels_by_path = {header.path: {normalize_disease_text(value) for value in [
                header.disease.get("id"), header.disease.get("name"), header.disease.get("name_zh"),
                *(header.disease.get("aliases") or []),
            ] if value} for header in self.snapshot_headers}
            self.labels = set().union(*self.labels_by_path.values())
            self.payload = {"disease": {"name_zh": "已通过质量门的完整验证疾病"}}
            return
        try:
            encoded = self.path.read_bytes()
            self.payload = json.loads(encoded)
        except (OSError, json.JSONDecodeError) as exc:
            raise OpenTargetsError("VIRTUAL_CELL_SNAPSHOT_INVALID", "无法读取已配置的虚拟细胞快照。", {"cause": type(exc).__name__}) from exc
        if self.payload.get("schema_version") != SCHEMA_VERSION:
            raise OpenTargetsError("VIRTUAL_CELL_SNAPSHOT_SCHEMA_MISMATCH", "虚拟细胞快照格式不受支持。")
        if not self.payload.get("quality_passed"):
            raise OpenTargetsError("VIRTUAL_CELL_QUALITY_FAILED", "虚拟细胞快照未通过独立质量门。")
        self.digest = hashlib.sha256(encoded).hexdigest()
        disease = self.payload.get("disease") or {}
        labels = [disease.get("id"), disease.get("name"), disease.get("name_zh"), *(disease.get("aliases") or [])]
        self.labels = {normalize_disease_text(value) for value in labels if value}

    def validate_disease(self, query: str) -> None:
        if self.snapshot_headers:
            matches = [header for header in self.snapshot_headers if normalize_disease_text(query) in self.labels_by_path[header.path]]
            if len(matches) == 1:
                self.selected = VirtualCellSnapshotAdapter(matches[0].path)
                self.payload = self.selected.payload
                self.digest = self.selected.digest
                return
            if len(matches) > 1:
                raise OpenTargetsError("DISEASE_AMBIGUOUS", "疾病同时匹配多个虚拟细胞快照，请使用标准疾病本体 ID。")
            raise OpenTargetsError("VIRTUAL_CELL_DISEASE_NOT_FOUND", "该疾病尚无通过质量门的虚拟细胞快照。", {"query": query})
        if normalize_disease_text(query) not in self.labels:
            raise OpenTargetsError("VIRTUAL_CELL_DISEASE_NOT_FOUND", "该疾病尚无通过质量门的虚拟细胞快照。", {"query": query})

    def results(self) -> list[dict[str, Any]]:
        if self.snapshot_headers:
            if not self.selected:
                raise OpenTargetsError("VIRTUAL_CELL_DISEASE_REQUIRED", "请先确认本次任务的疾病实体。")
            return self.selected.results()
        value = self.payload.get("results")
        if not isinstance(value, list):
            raise OpenTargetsError("VIRTUAL_CELL_RESULTS_INVALID", "虚拟细胞快照缺少结果。")
        return value

    def provenance(self) -> dict[str, Any]:
        if self.snapshot_headers:
            if not self.selected:
                raise OpenTargetsError("VIRTUAL_CELL_DISEASE_REQUIRED", "请先确认本次任务的疾病实体。")
            return self.selected.provenance()
        return {
            "source": "AI for Science 版本化虚拟细胞快照",
            "snapshot_id": self.payload["snapshot_id"],
            "snapshot_sha256": self.digest,
            "model_version": self.payload.get("model_version"),
            "scientific_status": self.payload.get("scientific_status"),
            "benchmark": self.payload.get("benchmark"),
            "qc": self.payload.get("qc"),
            "data_mode": "real",
            "scientific_conclusion": False,
        }
