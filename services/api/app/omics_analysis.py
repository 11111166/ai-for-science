"""Lightweight, traceable analysis for uploaded omics matrices.

The production API is a control plane with a strict memory budget.  This
module therefore analyses bounded CSV/TSV matrices only.  Raw FASTQ, H5AD and
vendor binary files remain valid project assets, but must be processed on the
dedicated compute tier before they can enter this workflow.
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import shutil
import statistics
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import UserContext, get_current_user, require_project_access
from .config import get_settings
from .database import get_db
from .models import Asset, AuditEvent, Job, JobStatus
from .storage import read_storage_bytes, save_asset, sha256_bytes


router = APIRouter(prefix="/api/v1/drug-discovery")
MODEL_VERSION = "omics-analysis-v1.3.1"
MAX_ANALYSIS_BYTES = 25 * 1024 * 1024
MAX_FEATURES = 60_000
MAX_SAMPLES = 250
MAX_SINGLE_CELL_GENES = 5_000
MAX_CELLS = 100_000
MAX_ADVANCED_BYTES = 10 * 1024 * 1024
MAX_MULTIMODAL_BYTES = 10 * 1024 * 1024

Modality = Literal["bulk_transcriptomics", "single_cell", "proteomics", "metabolomics"]
MATRIX_MODALITIES = {"bulk_transcriptomics", "proteomics", "metabolomics"}
EXAMPLE_ROOT = Path(__file__).resolve().parent.parent / "data" / "omics_examples"


def _normalise_legacy_r_rows(value: object) -> object:
    """Flatten one-row data.frame wrappers from older R JSON results.

    jsonlite writes a list of one-row data.frames as [[{...}], ...].  The
    browser expects [{...}, ...].  Apply this only to the advanced R payload,
    preserving the stored result file and all scalar/matrix values.
    """
    if isinstance(value, dict):
        return {key: _normalise_legacy_r_rows(item) for key, item in value.items()}
    if isinstance(value, list):
        if value and all(isinstance(item, list) and len(item) == 1 and isinstance(item[0], dict) for item in value):
            value = [item[0] for item in value]
        return [_normalise_legacy_r_rows(item) for item in value]
    return value

OMICS_EXAMPLES: dict[str, dict] = {
    "bulk_transcriptomics": {
        "title": "地塞米松处理气道平滑肌",
        "accession": "GSE52778",
        "filename": "bulk_airway_gse52778.csv",
        "design": "4 个细胞系配对的未处理 / 地塞米松处理样本，共 8 个样本",
        "contents": "1,500 个高表达基因的原始 read counts",
        "expected_signal": "可观察糖皮质激素应答相关表达变化，如 DUSP1、MT2A 等",
        "curation": "保留至少 4 个样本计数 ≥10 的基因，再按全体样本平均 CPM 选取前 1,500 个；保留全部 8 个样本。",
        "source_name": "Bioconductor airway / NCBI GEO",
        "source_url": "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE52778",
        "article_url": "https://doi.org/10.1371/journal.pone.0099625",
        "recommended_scale": "counts",
        "zero_is_missing": False,
        "reference_label": "未处理",
        "comparison_label": "地塞米松",
    },
    "single_cell": {
        "title": "IFN-β 刺激人 PBMC 单核细胞",
        "accession": "GSE96583",
        "filename": "single_cell_kang_gse96583.csv",
        "design": "4 位供体的 control / IFN-β stimulated CD14+ 单核细胞，每个供体-条件 20 个细胞",
        "contents": "160 个细胞 × 54 个干扰素应答、单核细胞与质控基因的原始 UMI counts",
        "expected_signal": "可观察 ISG15、IFIT、OAS、MX 等干扰素应答基因上调",
        "curation": "每个供体-条件按 GEO 原始顺序取前 20 个 singlet CD14+ monocytes；使用预先固定的 IFN/QC/单核细胞基因面板。",
        "source_name": "Kang et al. 2018 / NCBI GEO",
        "source_url": "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE96583",
        "article_url": "https://doi.org/10.1038/nbt.4042",
        "recommended_scale": "counts",
        "zero_is_missing": False,
        "reference_label": "control",
        "comparison_label": "stimulated",
    },
    "proteomics": {
        "title": "不同泛素链长度的相互作用蛋白",
        "accession": "DEP UbiLength",
        "filename": "proteomics_ubilength_ctrl_ubi4.csv",
        "design": "Ctrl 与 Ubi4 各 3 个 MaxQuant LFQ 重复，共 6 个样本",
        "contents": "2,907 个蛋白组的 LFQ intensity；保留真实缺失值（0）",
        "expected_signal": "可观察 SQSTM1、RPS27A、HUWE1 等 Ubi4 条件富集信号",
        "curation": "从 Bioconductor DEP 的 UbiLength 数据移除 reverse、contaminant 与 site-only 行，保留 Ctrl / Ubi4 六列 LFQ intensity。",
        "source_name": "Bioconductor DEP UbiLength",
        "source_url": "https://rdrr.io/bioc/DEP/man/UbiLength.html",
        "article_url": "https://doi.org/10.1016/j.molcel.2017.01.004",
        "recommended_scale": "raw",
        "zero_is_missing": True,
        "reference_label": "Ctrl",
        "comparison_label": "Ubi4",
    },
    "metabolomics": {
        "title": "癌症恶病质尿液代谢谱",
        "accession": "MetaboAnalyst human_cachexia",
        "filename": "metabolomics_human_cachexia.csv",
        "design": "10 个 control 与 10 个 cachectic 尿液样本",
        "contents": "20 个样本 × 63 个 1H-NMR 代谢物浓度",
        "expected_signal": "可观察氨基酸、葡萄糖与能量代谢相关差异",
        "curation": "按 MetaboAnalyst 源文件顺序分别取前 10 个 control / cachectic 样本，保留全部 63 个代谢物并转置为特征 × 样本。",
        "source_name": "MetaboAnalyst human_cachexia",
        "source_url": "https://new.metaboanalyst.ca/MetaboAnalyst/resources/vignettes/Enrichment_Analysis.html",
        "article_url": "https://doi.org/10.1007/s11306-010-0232-9",
        "recommended_scale": "raw",
        "zero_is_missing": True,
        "reference_label": "Control",
        "comparison_label": "Cachectic",
    },
}


class OmicsAnalysisError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(code)
        self.code = code
        self.message = message


class OmicsPreviewRequest(BaseModel):
    asset_id: str
    modality: Modality


def _normalise_name(value: str) -> str:
    return "".join(ch for ch in value.strip().lower() if ch.isalnum())


def _decode_table(data: bytes, filename: str) -> tuple[list[str], list[list[str]], str]:
    if b"\x00" in data[:4096]:
        raise OmicsAnalysisError("OMICS_BINARY_INPUT", "当前一键分析只读取 CSV/TSV 数值矩阵；H5AD、FASTQ 和厂商二进制文件需先在计算节点预处理。")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise OmicsAnalysisError("OMICS_ENCODING", "文件须使用 UTF-8 编码。") from exc
    sample = text[:8192]
    delimiter = "\t" if filename.lower().endswith((".tsv", ".txt")) else ","
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=",\t").delimiter
    except csv.Error:
        pass
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        header = [item.strip() for item in next(reader)]
    except StopIteration as exc:
        raise OmicsAnalysisError("OMICS_EMPTY_FILE", "文件为空。") from exc
    if len(header) < 3 or any(not item for item in header):
        raise OmicsAnalysisError("OMICS_HEADER", "表头至少需要标识列和两个数据列，且列名不能为空。")
    if len(set(header)) != len(header):
        raise OmicsAnalysisError("OMICS_DUPLICATE_COLUMNS", "表头存在重复列名，请先去重。")
    rows = [row for _, row in zip(range(80), reader) if any(cell.strip() for cell in row)]
    return header, rows, delimiter


def _load_asset(db: Session, project_id: str, asset_id: str) -> tuple[Asset, bytes]:
    asset = db.get(Asset, asset_id)
    if not asset or asset.project_id != project_id:
        raise OmicsAnalysisError("OMICS_ASSET_NOT_FOUND", "未找到该项目文件。")
    if asset.size > MAX_ANALYSIS_BYTES:
        raise OmicsAnalysisError(
            "OMICS_FILE_TOO_LARGE",
            "该文件已登记，但超过在线分析 25 MB 上限。请在 RCEES/专用计算节点生成紧凑矩阵后再分析。",
        )
    try:
        return asset, read_storage_bytes(asset.storage_uri, MAX_ANALYSIS_BYTES)
    except ValueError as exc:
        raise OmicsAnalysisError("OMICS_ASSET_READ_FAILED", "项目文件无法读取，请重新上传或联系管理员检查存储。") from exc


def _float(value: str) -> float | None:
    value = value.strip()
    if not value or value.lower() in {"na", "nan", "null", "none", "."}:
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _numeric_columns(header: list[str], rows: list[list[str]], excluded: set[int] | None = None) -> list[str]:
    result: list[str] = []
    excluded = excluded or set()
    for index, name in enumerate(header):
        if index in excluded:
            continue
        values = [row[index] for row in rows if index < len(row) and row[index].strip()]
        if values and sum(_float(value) is not None for value in values) / len(values) >= 0.85:
            result.append(name)
    return result


def _suggest_group(name: str) -> str:
    token = _normalise_name(name)
    if any(term in token for term in ("control", "ctrl", "normal", "healthy", "reference", "ref", "vehicle")):
        return "reference"
    if any(term in token for term in ("case", "disease", "tumor", "treated", "treatment", "patient", "stim")):
        return "comparison"
    return "ignore"


def _detect_scale(rows: list[list[str]], header: list[str], numeric_names: list[str], modality: str) -> str:
    indices = [header.index(name) for name in numeric_names[:20]]
    values = [_float(row[index]) for row in rows[:40] for index in indices if index < len(row)]
    numbers = [value for value in values if value is not None]
    if not numbers:
        return "counts" if modality in {"bulk_transcriptomics", "single_cell"} else "raw"
    integer_fraction = sum(abs(value - round(value)) < 1e-9 and value >= 0 for value in numbers) / len(numbers)
    if modality in {"bulk_transcriptomics", "single_cell"} and integer_fraction >= 0.95 and max(numbers) >= 20:
        return "counts"
    if modality in {"proteomics", "metabolomics"} and max(numbers) > 50:
        return "raw"
    return "log2"


def preview_payload(asset: Asset, data: bytes, modality: Modality) -> dict:
    header, rows, delimiter = _decode_table(data, asset.name)
    if modality in MATRIX_MODALITIES:
        numeric = _numeric_columns(header, rows, {0})
        if len(numeric) < 4:
            raise OmicsAnalysisError("OMICS_SAMPLE_COLUMNS", "至少需要 4 个可识别的数值样本列（参考组和比较组各不少于 2 个）。")
        return {
            "asset_id": asset.id,
            "asset_name": asset.name,
            "modality": modality,
            "format": "tsv" if delimiter == "\t" else "csv",
            "orientation": "features_by_samples",
            "identifier_column": header[0],
            "sample_columns": [{"name": name, "suggested_group": _suggest_group(name)} for name in numeric[:MAX_SAMPLES]],
            "detected_scale": _detect_scale(rows, header, numeric, modality),
            "preview_rows": len(rows),
            "warnings": (["样本列超过在线上限，只展示前 250 列。"] if len(numeric) > MAX_SAMPLES else []),
        }

    aliases = {_normalise_name(name): index for index, name in enumerate(header)}
    required_aliases = {
        "cell_id": ("cellid", "cell", "barcode"),
        "sample_id": ("sampleid", "sample", "donor", "donorid"),
        "group": ("group", "condition", "status", "phenotype"),
    }
    columns: dict[str, str] = {}
    excluded: set[int] = set()
    for canonical, choices in required_aliases.items():
        index = next((aliases[name] for name in choices if name in aliases), None)
        if index is None:
            raise OmicsAnalysisError("OMICS_SINGLE_CELL_METADATA", "单细胞表必须包含 cell_id、sample_id、group 三列（可使用常见英文同义列名）。")
        columns[canonical] = header[index]
        excluded.add(index)
    cell_type_index = next((aliases[name] for name in ("celltype", "cellannotation", "cluster") if name in aliases), None)
    if cell_type_index is not None:
        columns["cell_type"] = header[cell_type_index]
        excluded.add(cell_type_index)
    genes = _numeric_columns(header, rows, excluded)
    if len(genes) < 10:
        raise OmicsAnalysisError("OMICS_SINGLE_CELL_GENES", "未识别到足够的数值基因列；首版需要“每行一个细胞、每列一个基因”的 CSV/TSV。")
    group_index = header.index(columns["group"])
    groups = sorted({row[group_index].strip() for row in rows if group_index < len(row) and row[group_index].strip()})
    return {
        "asset_id": asset.id,
        "asset_name": asset.name,
        "modality": modality,
        "format": "tsv" if delimiter == "\t" else "csv",
        "orientation": "cells_by_genes",
        "metadata_columns": columns,
        "feature_count_detected": len(genes),
        "group_values": groups,
        "detected_scale": "counts",
        "preview_rows": len(rows),
        "warnings": (["基因列超过在线上限；请先在计算节点完成高变基因筛选并导出不超过 5,000 列的紧凑矩阵。"] if len(genes) > MAX_SINGLE_CELL_GENES else []),
    }


@router.post("/projects/{project_id}/omics/preview")
def preview_omics(
    project_id: str,
    payload: OmicsPreviewRequest,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    require_project_access(db, project_id, user)
    try:
        asset, data = _load_asset(db, project_id, payload.asset_id)
        return preview_payload(asset, data, payload.modality)
    except OmicsAnalysisError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from exc


def _example_payload(modality: str) -> dict:
    example = OMICS_EXAMPLES[modality]
    path = EXAMPLE_ROOT / example["filename"]
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise HTTPException(status_code=503, detail="内置案例文件暂不可用，请联系管理员检查部署包。") from exc
    return {
        "modality": modality,
        **example,
        "size": len(data),
        "sha256": sha256_bytes(data),
        "scope_note": "教学用真实数据子集；用于验证分析流程，不替代完整队列的科研复现。",
    }


def _asset_payload(asset: Asset) -> dict:
    return {
        "id": asset.id,
        "project_id": asset.project_id,
        "name": asset.name,
        "kind": asset.kind,
        "content_hash": asset.content_hash,
        "size": asset.size,
        "format": asset.format,
        "version": asset.version,
        "metadata_json": asset.metadata_json,
        "created_by": asset.created_by,
        "created_at": asset.created_at,
    }


@router.get("/omics/examples")
def list_omics_examples(user: UserContext = Depends(get_current_user)):
    return [_example_payload(modality) for modality in OMICS_EXAMPLES]


@router.post("/projects/{project_id}/omics/examples/{modality}", status_code=201)
def install_omics_example(
    project_id: str,
    modality: Modality,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    require_project_access(db, project_id, user, "member")
    example = _example_payload(modality)
    path = EXAMPLE_ROOT / example["filename"]
    data = path.read_bytes()
    kind = f"omics-example-{modality}"
    existing = db.scalar(
        select(Asset).where(
            Asset.project_id == project_id,
            Asset.kind == kind,
            Asset.content_hash == example["sha256"],
        ).order_by(Asset.created_at.desc())
    )
    if existing:
        return {"asset": _asset_payload(existing), "example": example, "reused": True}

    storage_uri, digest = save_asset(project_id, example["filename"], data)
    asset = Asset(
        project_id=project_id,
        name=example["filename"],
        kind=kind,
        storage_uri=storage_uri,
        content_hash=digest,
        size=len(data),
        format="csv",
        metadata_json={
            "content_type": "text/csv",
            "built_in_example": True,
            "modality": modality,
            "title": example["title"],
            "accession": example["accession"],
            "source_name": example["source_name"],
            "source_url": example["source_url"],
            "article_url": example["article_url"],
            "curation": example["curation"],
            "scope_note": example["scope_note"],
        },
        created_by=user.id,
    )
    db.add(asset)
    db.flush()
    db.add(AuditEvent(
        project_id=project_id,
        actor_id=user.id,
        action="omics.example_installed",
        entity_type="asset",
        entity_id=asset.id,
        details={"modality": modality, "accession": example["accession"], "hash": digest, "size": len(data)},
    ))
    db.commit()
    db.refresh(asset)
    return {"asset": _asset_payload(asset), "example": example, "reused": False}


TEMPLATES = {
    "bulk_transcriptomics": "feature_id,control_1,control_2,case_1,case_2\nGENE_A,120,135,280,310\nGENE_B,82,76,40,35\n",
    "proteomics": "feature_id,control_1,control_2,case_1,case_2\nP001,12450,11980,22100,21880\nP002,8400,8100,3900,4100\n",
    "metabolomics": "feature_id,control_1,control_2,case_1,case_2\nM001,10500,9900,18800,18100\nM002,7200,7500,3600,3400\n",
    "single_cell": "cell_id,sample_id,group,cell_type,GENE_A,GENE_B,GENE_C,GENE_D,GENE_E,GENE_F,GENE_G,GENE_H,GENE_I,MT-ND1\nc1,donor_1,control,T cell,3,0,1,2,0,1,0,2,1,1\nc2,donor_1,control,T cell,1,2,0,1,1,0,2,1,0,0\nc3,donor_2,control,T cell,2,1,0,2,0,1,1,2,0,0\nc4,donor_2,control,T cell,2,0,1,1,1,0,2,0,1,1\nc5,donor_3,case,T cell,7,0,1,2,0,1,0,2,1,1\nc6,donor_3,case,T cell,5,1,0,1,1,0,2,1,0,0\nc7,donor_4,case,T cell,6,0,1,2,0,1,1,2,0,1\nc8,donor_4,case,T cell,8,1,0,1,1,0,2,0,1,0\n",
}


@router.get("/omics/templates/{modality}")
def omics_template(modality: Modality, user: UserContext = Depends(get_current_user)):
    return Response(
        TEMPLATES[modality],
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{modality}-template.csv"'},
    )


def _betacf(a: float, b: float, x: float) -> float:
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1e-300 if abs(d) < 1e-300 else d
    d = 1.0 / d
    h = d
    for m in range(1, 201):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1e-300 if abs(d) < 1e-300 else d
        c = 1.0 + aa / c
        c = 1e-300 if abs(c) < 1e-300 else c
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1e-300 if abs(d) < 1e-300 else d
        c = 1.0 + aa / c
        c = 1e-300 if abs(c) < 1e-300 else c
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-12:
            break
    return h


def _regularized_beta(x: float, a: float, b: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    bt = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def _welch_test(reference: list[float], comparison: list[float]) -> tuple[float, float]:
    mean_a, mean_b = statistics.fmean(reference), statistics.fmean(comparison)
    var_a = statistics.variance(reference) if len(reference) > 1 else 0.0
    var_b = statistics.variance(comparison) if len(comparison) > 1 else 0.0
    term_a, term_b = var_a / len(reference), var_b / len(comparison)
    denominator = math.sqrt(term_a + term_b)
    if denominator == 0:
        return (0.0, 1.0) if mean_a == mean_b else (math.copysign(math.inf, mean_b - mean_a), 0.0)
    statistic = (mean_b - mean_a) / denominator
    df_denominator = (term_a * term_a / max(1, len(reference) - 1)) + (term_b * term_b / max(1, len(comparison) - 1))
    degrees = ((term_a + term_b) ** 2 / df_denominator) if df_denominator else 1_000_000.0
    x = degrees / (degrees + statistic * statistic)
    return statistic, max(0.0, min(1.0, _regularized_beta(x, degrees / 2.0, 0.5)))


def _bh_adjust(rows: list[dict]) -> None:
    ordered = sorted(enumerate(rows), key=lambda item: item[1]["p_value"])
    adjusted = [1.0] * len(rows)
    running = 1.0
    for reverse_rank, (index, item) in enumerate(reversed(ordered), start=1):
        rank = len(rows) - reverse_rank + 1
        running = min(running, item["p_value"] * len(rows) / rank)
        adjusted[index] = max(0.0, min(1.0, running))
    for item, value in zip(rows, adjusted):
        item["adjusted_p_value"] = value


def _dot(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def _sample_structure(samples: list[str], groups: list[str], feature_rows: list[list[float | None]]) -> tuple[dict, dict]:
    """Return bounded sample PCA and correlation summaries without heavy numeric dependencies."""
    usable: list[list[float]] = []
    for values in feature_rows[:200]:
        finite = [value for value in values if value is not None]
        if len(finite) < 2:
            continue
        mean = statistics.fmean(finite)
        filled = [mean if value is None else value for value in values]
        sd = statistics.stdev(filled) if len(filled) > 1 else 0.0
        if sd > 1e-12:
            usable.append([(value - mean) / sd for value in filled])

    sample_count = len(samples)
    if not usable:
        empty_points = [{"sample": sample, "group": groups[index], "pc1": 0.0, "pc2": 0.0} for index, sample in enumerate(samples)]
        empty_matrix = [[1.0 if row == column else 0.0 for column in range(sample_count)] for row in range(sample_count)]
        return ({"features_used": 0, "explained_variance": [0.0, 0.0], "points": empty_points}, {"features_used": 0, "samples": samples, "matrix": empty_matrix})

    sample_vectors = [[row[index] for row in usable] for index in range(sample_count)]
    centered_vectors = [[value - statistics.fmean(vector) for value in vector] for vector in sample_vectors]
    correlation: list[list[float]] = []
    for row_index, left in enumerate(centered_vectors):
        line: list[float] = []
        for column_index, right in enumerate(centered_vectors):
            if row_index == column_index:
                line.append(1.0)
                continue
            denominator = math.sqrt(_dot(left, left) * _dot(right, right))
            line.append(max(-1.0, min(1.0, _dot(left, right) / denominator if denominator else 0.0)))
        correlation.append(line)

    gram = [[_dot(sample_vectors[row], sample_vectors[column]) / max(1, len(usable) - 1) for column in range(sample_count)] for row in range(sample_count)]
    components: list[list[float]] = []
    eigenvalues: list[float] = []
    for component_index in range(min(2, sample_count)):
        vector = [1.0 + ((index * 17 + component_index * 7) % 11) / 11.0 for index in range(sample_count)]
        for previous in components:
            projection = _dot(vector, previous)
            vector = [value - projection * basis for value, basis in zip(vector, previous)]
        norm = math.sqrt(_dot(vector, vector))
        if norm <= 1e-12:
            break
        vector = [value / norm for value in vector]
        for _ in range(100):
            next_vector = [_dot(row, vector) for row in gram]
            for previous in components:
                projection = _dot(next_vector, previous)
                next_vector = [value - projection * basis for value, basis in zip(next_vector, previous)]
            norm = math.sqrt(_dot(next_vector, next_vector))
            if norm <= 1e-12:
                break
            next_vector = [value / norm for value in next_vector]
            if sum(abs(a - b) for a, b in zip(next_vector, vector)) < 1e-9:
                vector = next_vector
                break
            vector = next_vector
        eigenvalue = max(0.0, _dot(vector, [_dot(row, vector) for row in gram]))
        if eigenvalue <= 1e-12:
            break
        components.append(vector)
        eigenvalues.append(eigenvalue)

    while len(components) < 2:
        components.append([0.0] * sample_count)
        eigenvalues.append(0.0)
    total_variance = max(1e-12, sum(gram[index][index] for index in range(sample_count)))
    coordinates = [[math.sqrt(eigenvalues[axis]) * components[axis][index] for axis in range(2)] for index in range(sample_count)]
    reference = [coordinates[index][0] for index, group in enumerate(groups) if group == "reference"]
    comparison = [coordinates[index][0] for index, group in enumerate(groups) if group == "comparison"]
    if reference and comparison and statistics.fmean(comparison) < statistics.fmean(reference):
        for point in coordinates:
            point[0] *= -1
    return (
        {"features_used": len(usable), "explained_variance": [value / total_variance for value in eigenvalues], "points": [{"sample": sample, "group": groups[index], "pc1": coordinates[index][0], "pc2": coordinates[index][1]} for index, sample in enumerate(samples)]},
        {"features_used": len(usable), "samples": samples, "matrix": correlation},
    )


def _reader(data: bytes, filename: str):
    header, _, delimiter = _decode_table(data, filename)
    reader = csv.reader(io.StringIO(data.decode("utf-8-sig")), delimiter=delimiter)
    next(reader)
    return header, reader


def _matrix_analysis(asset: Asset, data: bytes, modality: str, parameters: dict) -> dict:
    header, reader = _reader(data, asset.name)
    reference_names = [str(value) for value in parameters.get("reference_samples", [])]
    comparison_names = [str(value) for value in parameters.get("comparison_samples", [])]
    if len(reference_names) < 2 or len(comparison_names) < 2 or set(reference_names) & set(comparison_names):
        raise OmicsAnalysisError("OMICS_GROUPS", "参考组和比较组各需至少 2 个不同样本。")
    selected = reference_names + comparison_names
    if len(selected) > MAX_SAMPLES or any(name not in header for name in selected):
        raise OmicsAnalysisError("OMICS_SAMPLE_COLUMNS", "样本列选择无效或超过 250 列上限。")
    indices = [header.index(name) for name in selected]
    scale = str(parameters.get("scale") or "auto")
    if scale == "auto":
        scale = preview_payload(asset, data, modality)["detected_scale"]
    if scale not in {"counts", "raw", "log2"}:
        raise OmicsAnalysisError("OMICS_SCALE", "不支持的数据尺度。")
    zero_missing = bool(parameters.get("zero_is_missing", modality in {"proteomics", "metabolomics"}))
    sample_totals = [0.0] * len(selected)
    sample_detected = [0] * len(selected)
    sample_missing = [0] * len(selected)
    smallest_positive: float | None = None
    feature_count = 0
    for row in reader:
        if not any(cell.strip() for cell in row):
            continue
        feature_count += 1
        if feature_count > MAX_FEATURES:
            raise OmicsAnalysisError("OMICS_TOO_MANY_FEATURES", "特征数超过在线分析 60,000 行上限。")
        for offset, index in enumerate(indices):
            value = _float(row[index]) if index < len(row) else None
            if value is None or (zero_missing and value == 0):
                sample_missing[offset] += 1
                continue
            if value < 0 and scale in {"counts", "raw"}:
                raise OmicsAnalysisError("OMICS_NEGATIVE_VALUE", "计数/原始强度中不能包含负值；若数据已做 log2，请选择“已 log2”。")
            sample_totals[offset] += value
            sample_detected[offset] += 1
            if value > 0 and (smallest_positive is None or value < smallest_positive):
                smallest_positive = value
    if feature_count < 2:
        raise OmicsAnalysisError("OMICS_NO_FEATURES", "没有足够的特征行可供分析。")
    if scale == "counts" and any(total <= 0 for total in sample_totals):
        raise OmicsAnalysisError("OMICS_EMPTY_LIBRARY", "至少一个样本的计数总和为 0。")

    def transform(value: float, offset: int) -> float:
        if scale == "counts":
            return math.log2(value / sample_totals[offset] * 1_000_000 + 1.0)
        if scale == "raw":
            return math.log2(value + (smallest_positive or 1.0) / 2.0)
        return value

    header, reader = _reader(data, asset.name)
    rows: list[dict] = []
    for row_number, row in enumerate(reader, start=2):
        if not any(cell.strip() for cell in row):
            continue
        feature = row[0].strip() if row else ""
        if not feature:
            feature = f"feature_{row_number}"
        values: list[float | None] = []
        for offset, index in enumerate(indices):
            value = _float(row[index]) if index < len(row) else None
            if value is None or (zero_missing and value == 0):
                values.append(None)
            else:
                values.append(transform(value, offset))
        reference = [value for value in values[: len(reference_names)] if value is not None]
        comparison = [value for value in values[len(reference_names) :] if value is not None]
        if len(reference) < 2 or len(comparison) < 2:
            continue
        statistic, p_value = _welch_test(reference, comparison)
        effect = statistics.fmean(comparison) - statistics.fmean(reference)
        rows.append({
            "feature": feature,
            "_row_number": row_number,
            "_variance": statistics.variance([value for value in values if value is not None]) if sum(value is not None for value in values) > 1 else 0.0,
            "effect": effect,
            "statistic": statistic if math.isfinite(statistic) else (999.0 if statistic > 0 else -999.0),
            "p_value": p_value,
            "reference_mean": statistics.fmean(reference),
            "comparison_mean": statistics.fmean(comparison),
            "missing_fraction": 1.0 - (len(reference) + len(comparison)) / len(selected),
        })
    if not rows:
        raise OmicsAnalysisError("OMICS_NO_TESTABLE_FEATURES", "没有特征同时满足两组各至少 2 个有效值。")
    _bh_adjust(rows)
    rows.sort(key=lambda item: (item["adjusted_p_value"], -abs(item["effect"]), item["feature"]))
    fdr = float(parameters.get("fdr_threshold", 0.05))
    effect_threshold = float(parameters.get("effect_threshold", 1.0 if modality != "metabolomics" else 0.585))
    if not 0 < fdr <= 1 or effect_threshold < 0:
        raise OmicsAnalysisError("OMICS_THRESHOLDS", "FDR 阈值须在 0 到 1 之间，效应阈值不能为负数。")
    top = [{key: value for key, value in item.items() if not key.startswith("_")} for item in rows[:100]]
    heatmap_items = sorted(rows, key=lambda item: (-abs(item["effect"]), item["adjusted_p_value"]))[:20]
    ordination_items = sorted(rows, key=lambda item: (-item["_variance"], item["feature"]))[:200]
    wanted_rows = {item["_row_number"] for item in heatmap_items + ordination_items}
    _, reader = _reader(data, asset.name)
    heatmap_values: dict[int, list[float | None]] = {}
    for row_number, row in enumerate(reader, start=2):
        if row_number not in wanted_rows:
            continue
        values: list[float | None] = []
        for offset, index in enumerate(indices):
            value = _float(row[index]) if index < len(row) else None
            values.append(None if value is None or (zero_missing and value == 0) else transform(value, offset))
        heatmap_values[row_number] = values
    heatmap = []
    for item in heatmap_items:
        row_number = item["_row_number"]
        feature = item["feature"]
        values = heatmap_values[row_number]
        finite = [value for value in values if value is not None]
        mean = statistics.fmean(finite)
        sd = statistics.stdev(finite) if len(finite) > 1 else 0.0
        heatmap.append({"feature": feature, "values": [None if value is None else (value - mean) / sd if sd else 0.0 for value in values]})
    groups = ["reference"] * len(reference_names) + ["comparison"] * len(comparison_names)
    sample_pca, sample_correlation = _sample_structure(selected, groups, [heatmap_values[item["_row_number"]] for item in ordination_items])
    significant_up = sum(item["adjusted_p_value"] <= fdr and item["effect"] >= effect_threshold for item in rows)
    significant_down = sum(item["adjusted_p_value"] <= fdr and item["effect"] <= -effect_threshold for item in rows)
    return {
        "modality": modality,
        "input": {"asset_id": asset.id, "asset_name": asset.name, "sha256": asset.content_hash, "scale": scale, "zero_is_missing": zero_missing},
        "comparison": {"reference_label": str(parameters.get("reference_label") or "参考组"), "comparison_label": str(parameters.get("comparison_label") or "比较组"), "reference_samples": reference_names, "comparison_samples": comparison_names},
        "summary": {
            "feature_count": feature_count,
            "tested_feature_count": len(rows),
            "sample_count": len(selected),
            "significant_count": sum(item["adjusted_p_value"] <= fdr and abs(item["effect"]) >= effect_threshold for item in rows),
            "significant_up_count": significant_up,
            "significant_down_count": significant_down,
            "fdr_threshold": fdr,
            "effect_threshold": effect_threshold,
        },
        "sample_qc": [{"sample": name, "group": "reference" if index < len(reference_names) else "comparison", "total_signal": sample_totals[index], "detected_features": sample_detected[index], "missing_fraction": sample_missing[index] / feature_count} for index, name in enumerate(selected)],
        "top_features": top,
        "heatmap": {"samples": selected, "rows": heatmap},
        "sample_pca": sample_pca,
        "sample_correlation": sample_correlation,
        "warnings": ["显著性来自 Welch t 检验与 BH-FDR；少量重复时统计功效有限。", "结果用于探索与实验优先级，不等于生物学或临床验证。"],
    }


def _median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def _advanced_transcriptomics(asset: Asset, data: bytes, parameters: dict, quick_result: dict) -> dict:
    if quick_result["input"]["scale"] != "counts":
        raise OmicsAnalysisError(
            "OMICS_ADVANCED_COUNTS_REQUIRED",
            "DESeq2 和 edgeR 只能读取原始非负整数计数；TPM、CPM、FPKM、已标准化或 log2 数据不能运行该步骤。",
        )
    if len(data) > MAX_ADVANCED_BYTES:
        raise OmicsAnalysisError(
            "OMICS_ADVANCED_FILE_TOO_LARGE",
            "DESeq2/edgeR 在线任务当前限 10 MB；请在计算节点完成完整模型后导入结果。",
        )
    species = str(parameters.get("species") or "human")
    gene_id_type = str(parameters.get("gene_id_type") or "auto")
    if species not in {"human", "mouse"}:
        raise OmicsAnalysisError("OMICS_ADVANCED_SPECIES", "高级转录组分析当前支持人和小鼠。")
    if gene_id_type not in {"auto", "symbol", "ensembl", "entrez"}:
        raise OmicsAnalysisError("OMICS_ADVANCED_GENE_ID", "基因 ID 类型须为自动识别、Symbol、Ensembl 或 Entrez。")

    settings = get_settings()
    rscript = settings.omics_rscript or shutil.which("Rscript") or ""
    script = Path(__file__).resolve().parent.parent / "scripts" / "omics_advanced.R"
    if not rscript or not Path(rscript).is_file() or not script.is_file():
        raise OmicsAnalysisError("OMICS_ADVANCED_RUNTIME", "DESeq2/edgeR 计算环境尚未配置，请联系管理员检查组学 R 运行时。")
    metadata = {
        "reference_samples": quick_result["comparison"]["reference_samples"],
        "comparison_samples": quick_result["comparison"]["comparison_samples"],
        "reference_label": quick_result["comparison"]["reference_label"],
        "comparison_label": quick_result["comparison"]["comparison_label"],
        "species": species,
        "gene_id_type": gene_id_type,
        "fdr_threshold": quick_result["summary"]["fdr_threshold"],
        "effect_threshold": quick_result["summary"]["effect_threshold"],
    }
    suffix = Path(asset.name).suffix.lower() if Path(asset.name).suffix.lower() in {".csv", ".tsv", ".txt"} else ".csv"
    with tempfile.TemporaryDirectory(prefix="ai4science-omics-") as directory:
        input_path = Path(directory) / f"input{suffix}"
        output_path = Path(directory) / "advanced.json"
        input_path.write_bytes(data)
        environment = os.environ.copy()
        environment.update({"OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "LANG": "en_US.utf8", "LC_ALL": "en_US.utf8"})
        try:
            completed = subprocess.run(
                [rscript, "--vanilla", str(script), str(input_path), json.dumps(metadata, ensure_ascii=False), str(output_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=settings.omics_advanced_timeout,
                check=False,
                env=environment,
            )
        except subprocess.TimeoutExpired as exc:
            raise OmicsAnalysisError("OMICS_ADVANCED_TIMEOUT", "DESeq2/edgeR 与富集分析超过运行时限，请缩小矩阵或转到计算节点。") from exc
        if completed.returncode != 0 or not output_path.is_file():
            detail = " ".join(completed.stderr.strip().splitlines()[-3:])[:500]
            raise OmicsAnalysisError("OMICS_ADVANCED_FAILED", f"高级转录组分析未完成：{detail or 'R 任务未生成结果'}")
        try:
            result = json.loads(output_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise OmicsAnalysisError("OMICS_ADVANCED_OUTPUT", "高级转录组结果文件无法读取。") from exc
    result = _normalise_legacy_r_rows(result)
    result["runtime"] = {"rscript": rscript, "script": script.name, "execution": "local_isolated_subprocess", "threads": 1}
    return result


def _advanced_multimodal(asset: Asset, data: bytes, modality: str, parameters: dict, quick_result: dict) -> dict:
    if modality not in {"single_cell", "proteomics", "metabolomics"}:
        raise OmicsAnalysisError("OMICS_MULTIMODALITY", "不支持的完整组学分析类型。")
    if len(data) > MAX_MULTIMODAL_BYTES:
        raise OmicsAnalysisError(
            "OMICS_MULTIMODAL_FILE_TOO_LARGE",
            "完整在线组学任务当前限 10 MB；请在计算节点完成大型矩阵处理后导入紧凑矩阵。",
        )
    species = str(parameters.get("species") or "human")
    gene_id_type = str(parameters.get("gene_id_type") or "auto")
    if species not in {"human", "mouse"}:
        raise OmicsAnalysisError("OMICS_MULTIMODAL_SPECIES", "单细胞和蛋白组功能解释当前支持人和小鼠。")
    if gene_id_type not in {"auto", "symbol", "ensembl", "entrez", "uniprot"}:
        raise OmicsAnalysisError("OMICS_MULTIMODAL_GENE_ID", "基因/蛋白 ID 类型不受支持。")
    metabolite_id_type = str(parameters.get("metabolite_id_type") or "auto")
    if metabolite_id_type not in {"auto", "name", "kegg"}:
        raise OmicsAnalysisError("OMICS_MULTIMODAL_METABOLITE_ID", "代谢物 ID 类型须为自动识别、英文名称或 KEGG Compound ID。")
    scale = str(quick_result["input"].get("scale") or "counts")
    if modality in {"proteomics", "metabolomics"} and scale not in {"raw", "log2"}:
        raise OmicsAnalysisError(
            "OMICS_MULTIMODAL_SCALE",
            "蛋白组和代谢组完整流程当前支持原始强度或已 log2 矩阵；计数型输入需要专用模型。",
        )

    settings = get_settings()
    rscript = settings.omics_rscript or shutil.which("Rscript") or ""
    script = Path(__file__).resolve().parent.parent / "scripts" / "omics_multimodal.R"
    if not rscript or not Path(rscript).is_file() or not script.is_file():
        raise OmicsAnalysisError("OMICS_MULTIMODAL_RUNTIME", "完整组学计算环境尚未配置，请联系管理员检查 AI 专属 R 运行时。")

    metadata: dict = {
        "modality": modality,
        "species": species,
        "gene_id_type": gene_id_type,
        "metabolite_id_type": metabolite_id_type,
        "fdr_threshold": quick_result["summary"]["fdr_threshold"],
        "effect_threshold": quick_result["summary"]["effect_threshold"],
        "scale": scale,
        "zero_is_missing": bool(quick_result["input"].get("zero_is_missing", False)),
        "reference_samples": quick_result["comparison"]["reference_samples"],
        "comparison_samples": quick_result["comparison"]["comparison_samples"],
        "reference_label": quick_result["comparison"]["reference_label"],
        "comparison_label": quick_result["comparison"]["comparison_label"],
    }
    if modality == "single_cell":
        preview = preview_payload(asset, data, "single_cell")
        metadata.update({
            "metadata_columns": preview["metadata_columns"],
            "reference_group": quick_result["comparison"]["reference_label"],
            "comparison_group": quick_result["comparison"]["comparison_label"],
        })

    suffix = Path(asset.name).suffix.lower() if Path(asset.name).suffix.lower() in {".csv", ".tsv", ".txt"} else ".csv"
    with tempfile.TemporaryDirectory(prefix=f"ai4science-{modality}-") as directory:
        input_path = Path(directory) / f"input{suffix}"
        output_path = Path(directory) / "advanced.json"
        input_path.write_bytes(data)
        environment = os.environ.copy()
        environment.update({"OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "LANG": "en_US.utf8", "LC_ALL": "en_US.utf8"})
        try:
            completed = subprocess.run(
                [rscript, "--vanilla", str(script), str(input_path), json.dumps(metadata, ensure_ascii=False), str(output_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=settings.omics_advanced_timeout,
                check=False,
                env=environment,
            )
        except subprocess.TimeoutExpired as exc:
            raise OmicsAnalysisError("OMICS_MULTIMODAL_TIMEOUT", "完整组学分析超过运行时限，请缩小矩阵或转到计算节点。") from exc
        if completed.returncode != 0 or not output_path.is_file():
            detail = " ".join(completed.stderr.strip().splitlines()[-4:])[:700]
            raise OmicsAnalysisError("OMICS_MULTIMODAL_FAILED", f"完整{modality}分析未完成：{detail or 'R 任务未生成结果'}")
        try:
            result = json.loads(output_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise OmicsAnalysisError("OMICS_MULTIMODAL_OUTPUT", "完整组学结果文件无法读取。") from exc
    result = _normalise_legacy_r_rows(result)
    result["runtime"] = {"rscript": rscript, "script": script.name, "execution": "local_isolated_subprocess", "threads": 1}
    return result


def _single_cell_analysis(asset: Asset, data: bytes, parameters: dict) -> dict:
    preview = preview_payload(asset, data, "single_cell")
    header, reader = _reader(data, asset.name)
    metadata = preview["metadata_columns"]
    indices = {name: header.index(column) for name, column in metadata.items()}
    excluded = set(indices.values())
    gene_indices = [index for index in range(len(header)) if index not in excluded]
    if len(gene_indices) > MAX_SINGLE_CELL_GENES:
        raise OmicsAnalysisError("OMICS_TOO_MANY_GENES", "单细胞矩阵超过在线分析 5,000 个基因列；请先生成高变基因/伪汇总紧凑矩阵。")
    reference_group = str(parameters.get("reference_group") or "").strip()
    comparison_group = str(parameters.get("comparison_group") or "").strip()
    if not reference_group or not comparison_group or reference_group == comparison_group:
        raise OmicsAnalysisError("OMICS_GROUPS", "请选择两个不同的单细胞分组。")
    genes = [header[index] for index in gene_indices]
    mito = [gene.upper().startswith(("MT-", "MT.")) for gene in genes]
    sample_sums: dict[str, list[float]] = {}
    sample_cells: dict[str, int] = {}
    sample_groups: dict[str, str] = {}
    sample_counts: dict[str, list[float]] = {}
    sample_detected: dict[str, list[float]] = {}
    sample_mito: dict[str, list[float]] = {}
    cell_types: dict[str, int] = {}
    total_cells = 0
    filtered_other_group = 0
    for row in reader:
        if not any(cell.strip() for cell in row):
            continue
        group = row[indices["group"]].strip() if indices["group"] < len(row) else ""
        if group not in {reference_group, comparison_group}:
            filtered_other_group += 1
            continue
        sample = row[indices["sample_id"]].strip() if indices["sample_id"] < len(row) else ""
        if not sample:
            raise OmicsAnalysisError("OMICS_SINGLE_CELL_SAMPLE", "发现缺少 sample_id 的细胞。")
        if sample in sample_groups and sample_groups[sample] != group:
            raise OmicsAnalysisError("OMICS_SINGLE_CELL_GROUP_CONFLICT", f"样本 {sample} 同时出现在两个分组中。")
        sample_groups[sample] = group
        values: list[float] = []
        for index in gene_indices:
            value = _float(row[index]) if index < len(row) else None
            if value is None or value < 0:
                raise OmicsAnalysisError("OMICS_SINGLE_CELL_COUNTS", "单细胞基因列必须是非负数值计数，且不能有缺失值。")
            values.append(value)
        total = sum(values)
        detected = sum(value > 0 for value in values)
        mito_total = sum(value for value, is_mito in zip(values, mito) if is_mito)
        if sample not in sample_sums:
            sample_sums[sample] = [0.0] * len(genes)
            sample_cells[sample] = 0
            sample_counts[sample] = []
            sample_detected[sample] = []
            sample_mito[sample] = []
        for index, value in enumerate(values):
            sample_sums[sample][index] += value
        sample_cells[sample] += 1
        sample_counts[sample].append(total)
        sample_detected[sample].append(float(detected))
        sample_mito[sample].append(mito_total / total if total else 0.0)
        if "cell_type" in indices:
            cell_type = row[indices["cell_type"]].strip() or "未标注"
            cell_types[cell_type] = cell_types.get(cell_type, 0) + 1
        total_cells += 1
        if total_cells > MAX_CELLS:
            raise OmicsAnalysisError("OMICS_TOO_MANY_CELLS", "单细胞矩阵超过在线分析 100,000 个细胞上限。")
    reference_samples = sorted(sample for sample, group in sample_groups.items() if group == reference_group)
    comparison_samples = sorted(sample for sample, group in sample_groups.items() if group == comparison_group)
    if len(reference_samples) < 2 or len(comparison_samples) < 2:
        raise OmicsAnalysisError("OMICS_SINGLE_CELL_REPLICATES", "单细胞差异分析按 sample_id 做伪汇总，两组各需至少 2 个独立样本。")
    samples = reference_samples + comparison_samples
    log_cpm: dict[str, list[float]] = {}
    for sample in samples:
        library = sum(sample_sums[sample])
        if library <= 0:
            raise OmicsAnalysisError("OMICS_EMPTY_LIBRARY", f"样本 {sample} 的伪汇总计数为 0。")
        log_cpm[sample] = [math.log2(value / library * 1_000_000 + 1.0) for value in sample_sums[sample]]
    rows: list[dict] = []
    for gene_index, gene in enumerate(genes):
        reference = [log_cpm[sample][gene_index] for sample in reference_samples]
        comparison = [log_cpm[sample][gene_index] for sample in comparison_samples]
        statistic, p_value = _welch_test(reference, comparison)
        rows.append({"feature": gene, "_gene_index": gene_index, "_variance": statistics.variance(reference + comparison), "effect": statistics.fmean(comparison) - statistics.fmean(reference), "statistic": statistic if math.isfinite(statistic) else (999.0 if statistic > 0 else -999.0), "p_value": p_value, "reference_mean": statistics.fmean(reference), "comparison_mean": statistics.fmean(comparison), "missing_fraction": 0.0})
    _bh_adjust(rows)
    rows.sort(key=lambda item: (item["adjusted_p_value"], -abs(item["effect"]), item["feature"]))
    fdr = float(parameters.get("fdr_threshold", 0.05))
    effect_threshold = float(parameters.get("effect_threshold", 1.0))
    if not 0 < fdr <= 1 or effect_threshold < 0:
        raise OmicsAnalysisError("OMICS_THRESHOLDS", "FDR 阈值须在 0 到 1 之间，效应阈值不能为负数。")
    heatmap_features = [item["feature"] for item in sorted(rows, key=lambda item: (-abs(item["effect"]), item["adjusted_p_value"]))[:20]]
    gene_lookup = {gene: index for index, gene in enumerate(genes)}
    heatmap = []
    for gene in heatmap_features:
        values = [log_cpm[sample][gene_lookup[gene]] for sample in samples]
        mean = statistics.fmean(values)
        sd = statistics.stdev(values) if len(values) > 1 else 0.0
        heatmap.append({"feature": gene, "values": [(value - mean) / sd if sd else 0.0 for value in values]})
    ordination_rows = sorted(rows, key=lambda item: (-item["_variance"], item["feature"]))[:200]
    groups = ["reference"] * len(reference_samples) + ["comparison"] * len(comparison_samples)
    sample_pca, sample_correlation = _sample_structure(samples, groups, [[log_cpm[sample][item["_gene_index"]] for sample in samples] for item in ordination_rows])
    significant_up = sum(item["adjusted_p_value"] <= fdr and item["effect"] >= effect_threshold for item in rows)
    significant_down = sum(item["adjusted_p_value"] <= fdr and item["effect"] <= -effect_threshold for item in rows)
    return {
        "modality": "single_cell",
        "input": {"asset_id": asset.id, "asset_name": asset.name, "sha256": asset.content_hash, "scale": "counts_to_pseudobulk_log2_cpm"},
        "comparison": {"reference_label": reference_group, "comparison_label": comparison_group, "reference_samples": reference_samples, "comparison_samples": comparison_samples},
        "summary": {"feature_count": len(genes), "tested_feature_count": len(rows), "sample_count": len(samples), "cell_count": total_cells, "significant_count": significant_up + significant_down, "significant_up_count": significant_up, "significant_down_count": significant_down, "fdr_threshold": fdr, "effect_threshold": effect_threshold, "cell_type_count": len(cell_types)},
        "sample_qc": [{"sample": sample, "group": sample_groups[sample], "cell_count": sample_cells[sample], "median_counts_per_cell": _median(sample_counts[sample]), "median_genes_per_cell": _median(sample_detected[sample]), "median_mito_fraction": _median(sample_mito[sample])} for sample in samples],
        "cell_types": [{"name": name, "cell_count": count} for name, count in sorted(cell_types.items(), key=lambda item: (-item[1], item[0]))[:30]],
        "top_features": [{key: value for key, value in item.items() if not key.startswith("_")} for item in rows[:100]],
        "heatmap": {"samples": samples, "rows": heatmap},
        "sample_pca": sample_pca,
        "sample_correlation": sample_correlation,
        "warnings": ["差异检验以 sample_id 伪汇总为统计单位，不把单个细胞当作生物学重复。", "当前首版不执行聚类、细胞类型自动注释或批次整合。", *([f"另有 {filtered_other_group} 个其他分组细胞未进入本次比较。"] if filtered_other_group else [])],
    }


def process_omics_analysis(db: Session, job: Job) -> dict:
    modality = str(job.parameters.get("modality") or "")
    if modality not in MATRIX_MODALITIES | {"single_cell"}:
        raise OmicsAnalysisError("OMICS_MODALITY", "不支持的组学类型。")
    asset_id = str(job.parameters.get("asset_id") or "")
    asset, data = _load_asset(db, job.project_id, asset_id)
    result = _single_cell_analysis(asset, data, job.parameters) if modality == "single_cell" else _matrix_analysis(asset, data, modality, job.parameters)
    if modality == "bulk_transcriptomics" and bool(job.parameters.get("advanced_transcriptomics", False)):
        result["advanced_transcriptomics"] = _advanced_transcriptomics(asset, data, job.parameters, result)
    if modality in {"single_cell", "proteomics", "metabolomics"} and bool(job.parameters.get("advanced_multimodal", False)):
        result[f"advanced_{modality}"] = _advanced_multimodal(asset, data, modality, job.parameters, result)
    result.update({
        "schema_version": "1.0",
        "model_version": MODEL_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "job_id": job.id,
        "project_id": job.project_id,
    })
    payload = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    storage_uri, digest = save_asset(job.project_id, f"omics-{job.id}.json", payload)
    job.output_ref = storage_uri
    return {
        "adapter": "UploadedOmicsMatrixAdapter",
        "model_name": "上传组学矩阵质控与双组比较",
        "model_version": MODEL_VERSION,
        "provenance": {"data_mode": "real", "input_asset_id": asset.id, "input_sha256": asset.content_hash, "output_sha256": digest, "modality": modality, "scientific_conclusion": False},
        "demo": False,
    }


@router.get("/projects/{project_id}/omics/results")
def list_omics_results(project_id: str, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    require_project_access(db, project_id, user)
    jobs = db.scalars(select(Job).where(Job.project_id == project_id, Job.type == "omics_analysis").order_by(Job.created_at.desc())).all()
    return [{"job_id": job.id, "status": job.status, "stage": job.stage, "progress": job.progress, "modality": job.parameters.get("modality"), "asset_id": job.parameters.get("asset_id"), "created_at": job.created_at, "completed_at": job.completed_at, "error_code": job.error_code, "error_message": job.error_message} for job in jobs]


@router.get("/jobs/{job_id}/omics-result")
def get_omics_result(job_id: str, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    job = db.get(Job, job_id)
    if not job or job.type != "omics_analysis":
        raise HTTPException(status_code=404, detail="未找到组学分析任务。")
    require_project_access(db, job.project_id, user)
    if job.status != JobStatus.completed.value or not job.output_ref:
        raise HTTPException(status_code=409, detail="该组学分析尚未完成。")
    try:
        result = json.loads(read_storage_bytes(job.output_ref, 8 * 1024 * 1024))
        for key in ("advanced_transcriptomics", "advanced_single_cell", "advanced_proteomics", "advanced_metabolomics"):
            if key in result:
                result[key] = _normalise_legacy_r_rows(result[key])
        return result
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="分析结果文件无法读取。") from exc
