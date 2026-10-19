"""Read-only adapter for ChEMBL/RDKit/TDC Compound Evaluation v1."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .open_targets import OpenTargetsError, normalize_disease_text
from .snapshot_index import SnapshotHeader, read_snapshot_header


SCHEMA_VERSION = "compound-evaluation-product-snapshot-v1"


class CompoundEvaluationSnapshotAdapter:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self.adapters: list[CompoundEvaluationSnapshotAdapter] = []
        self.snapshot_headers: list[SnapshotHeader] = []
        self.selected: CompoundEvaluationSnapshotAdapter | None = None
        if self.path.is_dir():
            paths = sorted(self.path.rglob("compound-evaluation-v1.json"))
            if not paths:
                raise OpenTargetsError("COMPOUND_SNAPSHOT_INVALID", "化合物快照目录中没有可用结果。")
            self.snapshot_headers = [read_snapshot_header(
                item, schema_version=SCHEMA_VERSION, error_code="COMPOUND_SNAPSHOT_INVALID",
                error_message="无法读取已配置的化合物评价快照。",
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
            raise OpenTargetsError("COMPOUND_SNAPSHOT_INVALID", "无法读取已配置的化合物评价快照。", {"cause": type(exc).__name__}) from exc
        if self.payload.get("schema_version") != SCHEMA_VERSION:
            raise OpenTargetsError("COMPOUND_SNAPSHOT_SCHEMA_MISMATCH", "化合物评价快照格式不受支持。")
        if not self.payload.get("quality_passed"):
            raise OpenTargetsError("COMPOUND_QUALITY_FAILED", "化合物评价快照未通过质量门。")
        self.digest = hashlib.sha256(encoded).hexdigest()
        disease = self.payload.get("disease") or {}
        labels = [disease.get("id"), disease.get("name"), disease.get("name_zh"), *(disease.get("aliases") or [])]
        self.labels = {normalize_disease_text(value) for value in labels if value}

    def validate_disease(self, query: str) -> None:
        if self.snapshot_headers:
            matches = [header for header in self.snapshot_headers if normalize_disease_text(query) in self.labels_by_path[header.path]]
            if len(matches) == 1:
                self.selected = CompoundEvaluationSnapshotAdapter(matches[0].path)
                self.payload = self.selected.payload
                self.digest = self.selected.digest
                return
            if len(matches) > 1:
                raise OpenTargetsError("DISEASE_AMBIGUOUS", "疾病同时匹配多个化合物快照，请使用标准疾病本体 ID。")
            raise OpenTargetsError("COMPOUND_DISEASE_NOT_FOUND", "该疾病尚无通过质量门的化合物评价快照。", {"query": query})
        if normalize_disease_text(query) not in self.labels:
            raise OpenTargetsError("COMPOUND_DISEASE_NOT_FOUND", "该疾病尚无通过质量门的化合物评价快照。", {"query": query})

    def compounds(self) -> list[dict[str, Any]]:
        if self.snapshot_headers:
            if not self.selected:
                raise OpenTargetsError("COMPOUND_DISEASE_REQUIRED", "请先确认本次任务的疾病实体。")
            return self.selected.compounds()
        value = self.payload.get("compounds")
        if not isinstance(value, list):
            raise OpenTargetsError("COMPOUND_RESULTS_INVALID", "化合物评价快照缺少候选结果。")
        return value

    def available_symbols(self) -> set[str]:
        if self.snapshot_headers:
            if not self.selected:
                raise OpenTargetsError("COMPOUND_DISEASE_REQUIRED", "请先确认本次任务的疾病实体。")
            return self.selected.available_symbols()
        return {
            str(match.get("target_symbol") or "").upper()
            for compound in self.compounds()
            for match in compound.get("target_matches") or []
            if match.get("target_symbol")
        }

    def pooled_compounds(self) -> list[dict[str, Any]]:
        """Return the gene/molecule-level union across release packs.

        ChEMBL binding measurements and molecule properties are not disease
        specific.  Disease-specific intervention direction is deliberately
        recalculated by the runtime job instead of reusing a pack's label.
        """
        sources = [header.path for header in self.snapshot_headers] if self.snapshot_headers else [self.path]
        by_molecule: dict[str, dict[str, Any]] = {}
        for path in sources:
            adapter = self if path == self.path and not self.snapshot_headers else CompoundEvaluationSnapshotAdapter(path)
            for compound in adapter.compounds():
                molecule_id = str(compound.get("molecule_chembl_id") or "")
                if not molecule_id:
                    continue
                stored = by_molecule.setdefault(molecule_id, {**compound, "target_matches": []})
                existing = {
                    (str(item.get("target_symbol") or "").upper(), str(item.get("target_chembl_id") or ""))
                    for item in stored.get("target_matches") or []
                }
                for match in compound.get("target_matches") or []:
                    key = (str(match.get("target_symbol") or "").upper(), str(match.get("target_chembl_id") or ""))
                    if key not in existing:
                        stored["target_matches"].append(dict(match))
                        existing.add(key)
        return list(by_molecule.values())

    def pooled_available_symbols(self) -> set[str]:
        return {
            str(match.get("target_symbol") or "").upper()
            for compound in self.pooled_compounds()
            for match in compound.get("target_matches") or []
            if match.get("target_symbol")
        }

    def pooled_provenance(self) -> dict[str, Any]:
        sources = [header.path for header in self.snapshot_headers] if self.snapshot_headers else [self.path]
        snapshot_ids = [header.snapshot_id for header in self.snapshot_headers] if self.snapshot_headers else [self.payload.get("snapshot_id")]
        return {
            "source": "ChEMBL 实验活性 + RDKit/ChEMBL 结构性质 + TDC ADMET 基线",
            "snapshot_id": "pooled-compound-evaluation-v1",
            "model_version": "compound-gene-level-union-v1",
            "scientific_status": "experimental_activity_plus_computational_property_baseline",
            "coverage_scope": "gene_and_molecule_level_global",
            "pooled_snapshot_count": len(sources),
            "pooled_compound_count": len(self.pooled_compounds()),
            "pooled_target_count": len(self.pooled_available_symbols()),
            "snapshot_ids": snapshot_ids,
            "data_mode": "real",
            "scientific_conclusion": False,
        }

    def provenance(self) -> dict[str, Any]:
        if self.snapshot_headers:
            if not self.selected:
                raise OpenTargetsError("COMPOUND_DISEASE_REQUIRED", "请先确认本次任务的疾病实体。")
            return self.selected.provenance()
        return {
            "source": "ChEMBL 实验活性 + RDKit 结构性质 + TDC ADMET 基线",
            "snapshot_id": self.payload["snapshot_id"],
            "snapshot_sha256": self.digest,
            "model_version": self.payload.get("model_version"),
            "scientific_status": self.payload.get("scientific_status"),
            "sources": self.payload.get("sources"),
            "score_contract": self.payload.get("score_contract"),
            "benchmark": self.payload.get("benchmark"),
            "data_mode": "real",
            "scientific_conclusion": False,
        }
