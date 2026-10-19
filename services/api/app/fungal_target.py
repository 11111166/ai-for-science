"""Project-scoped fungal target prioritisation from user-supplied evidence matrices.

The module deliberately ranks *candidate* targets.  It does not claim target
validation and it does not silently substitute missing BLAST, essentiality,
virulence, structure, or compound-protein evidence with synthetic values.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .auth import UserContext, get_current_user, require_project_access
from .database import get_db
from .models import Asset, Job, JobStatus
from .storage import read_storage_bytes, save_asset


router = APIRouter(prefix="/api/v1/drug-discovery")
MODEL_VERSION = "fungal-target-prioritisation-v1"
MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_ROWS = 20_000
FUNGAL_TARGET_DATA_DIR = Path(os.environ.get("FUNGAL_TARGET_DATA_DIR", "./data/shared/fungal-target-data/current"))

Workflow = Literal["prioritization", "target_fishing"]

IDENTITY_COLUMNS = ("protein_id", "gene_symbol")
COMMON_COLUMNS = (
    "protein_name", "pathway", "human_similarity", "pathogen_conservation",
    "pocket_score", "structure_evidence", "known_ligand_evidence", "notes",
)
PRIORITIZATION_METRICS = {
    "essentiality": 0.22,
    "virulence": 0.16,
    "infection_expression": 0.12,
    "resistance_relevance": 0.06,
    "druggability": 0.18,
    "pathogen_conservation": 0.14,
    "pocket_score": 0.12,
}
FISHING_METRICS = {
    "ligand_similarity": 0.22,
    "ai_prediction": 0.26,
    "inverse_docking": 0.22,
    "pathway_evidence": 0.12,
    "resistance_evidence": 0.12,
    "genetic_evidence": 0.06,
}
WORKFLOW_COLUMNS = {
    "prioritization": (*IDENTITY_COLUMNS, *COMMON_COLUMNS, *PRIORITIZATION_METRICS),
    "target_fishing": (*IDENTITY_COLUMNS, *COMMON_COLUMNS, *FISHING_METRICS),
}


class FungalTargetError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(code)
        self.code = code
        self.message = message


class PreviewRequest(BaseModel):
    asset_id: str
    workflow: Workflow


SC5314_SOURCES = [
    {
        "id": "ncbi-bioproject",
        "title": "NCBI BioProject PRJNA10701 · C. albicans SC5314 reference genome",
        "url": "https://www.ncbi.nlm.nih.gov/bioproject/10701",
        "supports": "参考基因组、染色体水平组装、8 条染色体与 6,030 条蛋白序列",
    },
    {
        "id": "genome-history",
        "title": "Genome sequence and annotation of Candida albicans SC5314",
        "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC5794938/",
        "supports": "1984 年念珠菌血症分离株、参考菌株地位与 Assembly 22 注释背景",
    },
    {
        "id": "rob1-caveat",
        "title": "A rare ROB1 allele makes SC5314 unusually filamentous and invasive",
        "url": "https://pubmed.ncbi.nlm.nih.gov/37737633/",
        "supports": "SC5314 的强侵袭、成丝与生物膜表型，以及参考菌株外推时的 ROB1 等位基因注意事项",
    },
    {
        "id": "erg11-ncbi",
        "title": "NCBI Gene 3641571 · ERG11",
        "url": "https://www.ncbi.nlm.nih.gov/gene/3641571",
        "supports": "ERG11 身份、甾醇 14α-去甲基酶功能与唑类作用机制",
    },
    {
        "id": "erg11-resistance",
        "title": "ERG11 substitutions and azole resistance in SC5314",
        "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC4291385/",
        "supports": "ERG11 临床突变导入 SC5314 后的唑类耐药证据",
    },
    {
        "id": "fks1-resistance",
        "title": "FKS1 mutations and caspofungin resistance",
        "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC1479158/",
        "supports": "β-1,3-葡聚糖合酶、FKS1 与棘白菌素耐药",
    },
    {
        "id": "infection-transcriptome",
        "title": "SC5314 transcriptional response during renal infection",
        "url": "https://pubmed.ncbi.nlm.nih.gov/19032986/",
        "supports": "感染期表达与毒力表型联合分析的研究背景",
    },
]


SC5314_PRIORITIZATION_CSV = """protein_id,gene_symbol,protein_name,pathway,essentiality,virulence,infection_expression,resistance_relevance,human_similarity,druggability,pathogen_conservation,pocket_score,structure_evidence,known_ligand_evidence,notes
P10613,ERG11,Sterol 14-alpha demethylase,Ergosterol biosynthesis,0.98,0.84,0.74,0.98,0.22,0.98,0.91,0.94,0.98,1.00,"Known azole target; sources: erg11-ncbi, erg11-resistance"
SC5314:FKS1,FKS1,Beta-1 3-glucan synthase catalytic subunit,Cell-wall beta-glucan synthesis,0.99,0.93,0.77,0.96,0.08,0.91,0.89,0.75,0.90,1.00,"Known echinocandin target; source: fks1-resistance"
SC5314:ERG1,ERG1,Squalene monooxygenase,Ergosterol biosynthesis,0.96,0.66,0.69,0.86,0.18,0.94,0.88,0.91,0.92,1.00,"Established antifungal pathway; terbinafine mechanism context"
SC5314:CHS1,CHS1,Chitin synthase 1,Cell-wall chitin synthesis,0.94,0.81,0.73,0.54,0.12,0.78,0.82,0.67,0.77,0.62,"Cell-wall candidate; direct target validation still required"
SC5314:FUR1,FUR1,Uracil phosphoribosyltransferase,Pyrimidine salvage,0.91,0.59,0.62,0.85,0.24,0.87,0.80,0.86,0.85,0.92,"Flucytosine activation/resistance mechanism context"
SC5314:CNA1,CNA1,Calcineurin catalytic subunit,Stress adaptation and drug tolerance,0.78,0.88,0.82,0.86,0.64,0.83,0.84,0.72,0.91,0.73,"Strong biology but host similarity lowers selectivity priority"
SC5314:HSP90,HSP90,Heat shock protein 90,Stress adaptation and drug tolerance,0.97,0.89,0.91,0.92,0.87,0.90,0.93,0.81,0.98,0.88,"Mechanistically important; high human similarity is a safety liability"
SC5314:HOG1,HOG1,Mitogen-activated protein kinase HOG1,Osmotic and oxidative stress,0.67,0.86,0.90,0.58,0.55,0.72,0.86,0.70,0.90,0.54,"Virulence/stress node; selectivity requires structural review"
SC5314:MKC1,MKC1,Mitogen-activated protein kinase MKC1,Cell-wall integrity signalling,0.65,0.83,0.79,0.61,0.58,0.74,0.84,0.69,0.88,0.49,"Cell-wall signalling candidate; human kinase selectivity risk"
SC5314:ROB1,ROB1,Biofilm transcription factor ROB1,Biofilm and filamentation,0.42,0.96,0.91,0.35,0.11,0.31,0.66,0.22,0.61,0.12,"High strain-specific virulence relevance; rare SC5314 allele limits generalisation"
SC5314:EFG1,EFG1,Transcriptional regulator EFG1,Morphogenesis and virulence,0.58,0.94,0.88,0.41,0.16,0.36,0.79,0.28,0.68,0.17,"Strong virulence biology but difficult small-molecule target class"
SC5314:CDC28,CDC28,Cyclin-dependent kinase,Cell cycle,0.99,0.72,0.70,0.45,0.82,0.86,0.91,0.89,0.96,0.71,"Essential and pocketed but human kinase homology is a major liability"
""".encode()


SC5314_FISHING_CSV = """protein_id,gene_symbol,protein_name,pathway,human_similarity,pathogen_conservation,pocket_score,structure_evidence,known_ligand_evidence,ligand_similarity,ai_prediction,inverse_docking,pathway_evidence,resistance_evidence,genetic_evidence,notes
P10613,ERG11,Sterol 14-alpha demethylase,Ergosterol biosynthesis,0.22,0.91,0.94,0.98,1.00,0.99,0.98,0.95,1.00,0.98,0.96,"Primary fluconazole target; sources: erg11-ncbi, erg11-resistance"
SC5314:ERG3,ERG3,C-5 sterol desaturase,Ergosterol biosynthesis,0.19,0.86,0.80,0.86,0.70,0.61,0.72,0.65,0.94,0.88,0.78,"Downstream sterol-pathway and resistance-context candidate, not established as direct fluconazole target"
SC5314:UPC2,UPC2,Sterol uptake and biosynthesis regulator,Ergosterol regulation,0.14,0.75,0.38,0.68,0.25,0.28,0.63,0.31,0.88,0.84,0.70,"Mechanism-context regulator; direct binding evidence is weak"
SC5314:CDR1,CDR1,ABC multidrug transporter,Drug efflux,0.31,0.80,0.58,0.79,0.42,0.23,0.56,0.35,0.73,0.96,0.77,"Resistance mechanism candidate; likely modulates exposure rather than being the primary target"
SC5314:MDR1,MDR1,Major facilitator multidrug transporter,Drug efflux,0.17,0.72,0.52,0.71,0.31,0.19,0.49,0.29,0.70,0.91,0.68,"Resistance mechanism candidate; not evidence of direct fluconazole binding"
SC5314:TAC1,TAC1,Transcriptional activator of CDR genes,Drug-efflux regulation,0.12,0.67,0.30,0.62,0.18,0.14,0.43,0.21,0.65,0.89,0.64,"Resistance regulator; included to show target-vs-mechanism discrimination"
SC5314:HSP90,HSP90,Heat shock protein 90,Stress adaptation and drug tolerance,0.87,0.93,0.81,0.98,0.88,0.32,0.66,0.47,0.66,0.83,0.72,"Tolerance network candidate; high host similarity and no claim of direct fluconazole targeting"
SC5314:CNA1,CNA1,Calcineurin catalytic subunit,Stress adaptation and drug tolerance,0.64,0.84,0.72,0.91,0.73,0.27,0.62,0.41,0.63,0.79,0.69,"Tolerance network candidate; host similarity limits selectivity"
""".encode()


def _float(value: str | None, column: str, row_number: int) -> float | None:
    value = (value or "").strip()
    if not value or value.lower() in {"na", "nan", "null", "none", "."}:
        return None
    try:
        number = float(value)
    except ValueError as exc:
        raise FungalTargetError("FUNGAL_TARGET_NON_NUMERIC", f"第 {row_number} 行的 {column} 不是数值。") from exc
    if not math.isfinite(number) or not 0 <= number <= 1:
        raise FungalTargetError("FUNGAL_TARGET_RANGE", f"第 {row_number} 行的 {column} 必须在 0–1 之间。")
    return number


def _decode(data: bytes) -> str:
    if b"\x00" in data[:4096]:
        raise FungalTargetError("FUNGAL_TARGET_BINARY_INPUT", "证据矩阵须为 UTF-8 CSV/TSV，不能直接上传 FASTA、结构文件或二进制结果。")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise FungalTargetError("FUNGAL_TARGET_ENCODING", "证据矩阵须使用 UTF-8 编码。") from exc


def _parse_table(data: bytes, filename: str, workflow: Workflow) -> tuple[list[dict], list[str]]:
    text = _decode(data)
    delimiter = "\t" if filename.lower().endswith((".tsv", ".txt")) else ","
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",\t")
        delimiter = dialect.delimiter
    except csv.Error:
        pass
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    header = [str(item or "").strip() for item in (reader.fieldnames or [])]
    if not header:
        raise FungalTargetError("FUNGAL_TARGET_HEADER", "文件为空或缺少表头。")
    if len(header) != len(set(header)):
        raise FungalTargetError("FUNGAL_TARGET_DUPLICATE_COLUMNS", "表头存在重复列名。")
    missing = [column for column in IDENTITY_COLUMNS if column not in header]
    if missing:
        raise FungalTargetError("FUNGAL_TARGET_REQUIRED_COLUMNS", f"缺少必填列：{'、'.join(missing)}。")
    metric_columns = PRIORITIZATION_METRICS if workflow == "prioritization" else FISHING_METRICS
    if not any(column in header for column in metric_columns):
        raise FungalTargetError("FUNGAL_TARGET_NO_EVIDENCE", f"至少需要一个证据列：{'、'.join(metric_columns)}。")

    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    numeric_columns = set(metric_columns) | {"human_similarity", "pathogen_conservation", "pocket_score", "structure_evidence", "known_ligand_evidence", "druggability"}
    for row_number, raw in enumerate(reader, start=2):
        if not any(str(value or "").strip() for value in raw.values()):
            continue
        if len(rows) >= MAX_ROWS:
            raise FungalTargetError("FUNGAL_TARGET_TOO_MANY_ROWS", f"在线分析最多处理 {MAX_ROWS:,} 个蛋白；完整蛋白组请先在计算节点汇总证据。")
        protein_id = str(raw.get("protein_id") or "").strip()
        gene_symbol = str(raw.get("gene_symbol") or "").strip()
        if not protein_id or not gene_symbol:
            raise FungalTargetError("FUNGAL_TARGET_IDENTITY", f"第 {row_number} 行缺少 protein_id 或 gene_symbol。")
        key = (protein_id.upper(), gene_symbol.upper())
        if key in seen:
            raise FungalTargetError("FUNGAL_TARGET_DUPLICATE_PROTEIN", f"第 {row_number} 行的蛋白/基因组合重复。")
        seen.add(key)
        row: dict = {
            "protein_id": protein_id,
            "gene_symbol": gene_symbol,
            "protein_name": str(raw.get("protein_name") or "").strip(),
            "pathway": str(raw.get("pathway") or "").strip(),
            "notes": str(raw.get("notes") or "").strip(),
        }
        for column in numeric_columns:
            if column in header:
                row[column] = _float(raw.get(column), column, row_number)
        rows.append(row)
    if not rows:
        raise FungalTargetError("FUNGAL_TARGET_EMPTY", "没有可分析的蛋白记录。")
    return rows, header


def _load_asset(db: Session, project_id: str, asset_id: str) -> tuple[Asset, bytes]:
    asset = db.get(Asset, asset_id)
    if not asset or asset.project_id != project_id:
        raise FungalTargetError("FUNGAL_TARGET_ASSET_NOT_FOUND", "未找到当前项目中的证据矩阵。")
    if asset.size > MAX_INPUT_BYTES:
        raise FungalTargetError("FUNGAL_TARGET_FILE_TOO_LARGE", "证据矩阵超过在线分析 8 MB 上限。")
    try:
        return asset, read_storage_bytes(asset.storage_uri, MAX_INPUT_BYTES)
    except (ValueError, OSError) as exc:
        raise FungalTargetError("FUNGAL_TARGET_ASSET_READ_FAILED", "证据矩阵无法读取，请重新上传。") from exc


def preview_payload(asset: Asset, data: bytes, workflow: Workflow) -> dict:
    rows, header = _parse_table(data, asset.name, workflow)
    metrics = PRIORITIZATION_METRICS if workflow == "prioritization" else FISHING_METRICS
    coverage = {metric: sum(row.get(metric) is not None for row in rows) for metric in metrics}
    optional = {column: sum(row.get(column) is not None for row in rows) for column in ("human_similarity", "pathogen_conservation", "pocket_score", "structure_evidence", "known_ligand_evidence") if column in header}
    warnings: list[str] = []
    if "human_similarity" not in header:
        warnings.append("缺少 human_similarity：不能执行宿主同源性排除，只能把该项标为待补。")
    if workflow == "prioritization" and "pathogen_conservation" not in header:
        warnings.append("缺少 pathogen_conservation：不能判断广谱致病真菌保守性。")
    if not ({"druggability", "pocket_score", "known_ligand_evidence"} & set(header)):
        warnings.append("缺少可成药性、口袋或已知配体证据，不能完成结构可开发性筛选。")
    return {
        "asset_id": asset.id,
        "asset_name": asset.name,
        "workflow": workflow,
        "row_count": len(rows),
        "columns": header,
        "metric_coverage": coverage,
        "optional_coverage": optional,
        "warnings": warnings,
        "ready": True,
    }


def _score_rows(rows: list[dict], workflow: Workflow, parameters: dict) -> list[dict]:
    weights = PRIORITIZATION_METRICS if workflow == "prioritization" else FISHING_METRICS
    human_penalty = 0.20 if workflow == "prioritization" else 0.10
    scored: list[dict] = []
    for row in rows:
        available = [(metric, weight, row.get(metric)) for metric, weight in weights.items() if row.get(metric) is not None]
        available_weight = sum(weight for _, weight, _ in available)
        evidence_score = sum(weight * float(value) for _, weight, value in available) / available_weight if available_weight else 0.0
        coverage = available_weight / sum(weights.values())
        # Coverage is reported independently and only mildly shrinks an otherwise
        # strong score; missing evidence is never silently interpreted as zero.
        coverage_adjusted = evidence_score * (0.75 + 0.25 * coverage)
        human_similarity = row.get("human_similarity")
        score = coverage_adjusted - (human_penalty * float(human_similarity) if human_similarity is not None else 0.0)
        score = max(0.0, min(1.0, score))
        signals = sorted(
            ({"metric": metric, "value": float(value), "contribution": weight * float(value)} for metric, weight, value in available),
            key=lambda item: (-item["contribution"], item["metric"]),
        )
        missing = [metric for metric in weights if row.get(metric) is None]
        if human_similarity is None:
            missing.append("human_similarity")
        tier = "high_priority" if score >= 0.75 and coverage >= 0.60 and human_similarity is not None else "review" if score >= 0.55 else "evidence_gap"
        scored.append({
            **row,
            "candidate_score": round(score, 4),
            "evidence_score": round(evidence_score, 4),
            "evidence_coverage": round(coverage, 4),
            "priority_tier": tier,
            "top_signals": signals[:3],
            "missing_evidence": missing,
            "candidate_label": "计算候选靶标（待实验验证）",
        })
    scored.sort(key=lambda item: (-item["candidate_score"], -item["evidence_coverage"], item["gene_symbol"]))
    for rank, row in enumerate(scored, start=1):
        row["rank"] = rank
    return scored


def analyse_payload(asset: Asset, data: bytes, workflow: Workflow, parameters: dict) -> dict:
    rows, header = _parse_table(data, asset.name, workflow)
    candidates = _score_rows(rows, workflow, parameters)
    human_penalty = 0.20 if workflow == "prioritization" else 0.10
    top_n = int(parameters.get("top_n", 50))
    human_threshold = float(parameters.get("human_similarity_threshold", 0.35))
    evidence_threshold = float(parameters.get("evidence_threshold", 0.50))
    if not 1 <= top_n <= 200:
        raise FungalTargetError("FUNGAL_TARGET_TOP_N", "候选数量须在 1–200 之间。")
    if not 0 <= human_threshold <= 1 or not 0 <= evidence_threshold <= 1:
        raise FungalTargetError("FUNGAL_TARGET_THRESHOLD", "阈值须在 0–1 之间。")

    if workflow == "prioritization":
        biological = [row for row in candidates if max(row.get("essentiality") or 0, row.get("virulence") or 0, row.get("infection_expression") or 0, row.get("resistance_relevance") or 0) >= evidence_threshold]
        host_divergent = [row for row in biological if row.get("human_similarity") is not None and row["human_similarity"] <= human_threshold]
        druggable = [row for row in host_divergent if max(row.get("druggability") or 0, row.get("pocket_score") or 0, row.get("known_ligand_evidence") or 0) >= evidence_threshold]
        funnel = [
            {"id": "proteome", "label": "输入蛋白", "count": len(candidates)},
            {"id": "biology", "label": "必需 / 毒力 / 感染 / 耐药证据", "count": len(biological)},
            {"id": "host_divergent", "label": "低宿主同源", "count": len(host_divergent)},
            {"id": "druggable", "label": "可成药性证据", "count": len(druggable)},
            {"id": "ranked", "label": "进入候选榜", "count": min(top_n, len(druggable))},
        ]
        final = druggable[:top_n]
    else:
        evidence_supported = [row for row in candidates if row["evidence_coverage"] >= 0.35]
        convergent = [row for row in evidence_supported if sum((row.get(metric) or 0) >= evidence_threshold for metric in FISHING_METRICS) >= 2]
        host_divergent = [row for row in convergent if row.get("human_similarity") is None or row["human_similarity"] <= human_threshold]
        funnel = [
            {"id": "proteome", "label": "候选蛋白", "count": len(candidates)},
            {"id": "evidence", "label": "预测证据可用", "count": len(evidence_supported)},
            {"id": "convergent", "label": "至少两类证据收敛", "count": len(convergent)},
            {"id": "host_divergent", "label": "宿主差异可接受 / 待补", "count": len(host_divergent)},
            {"id": "ranked", "label": "进入候选榜", "count": min(top_n, len(host_divergent))},
        ]
        final = host_divergent[:top_n]

    # Re-rank the visible shortlist after biological/selectivity filters so the
    # UI never shows confusing gaps inherited from the pre-filter score order.
    final = [{**row, "rank": rank} for rank, row in enumerate(final, start=1)]
    preview = preview_payload(asset, data, workflow)
    return {
        "schema_version": "1.0",
        "model_version": MODEL_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "workflow": workflow,
        "organism": str(parameters.get("organism") or "").strip(),
        "compound": {
            "name": str(parameters.get("compound_name") or "").strip(),
            "structure": str(parameters.get("compound_structure") or "").strip(),
        } if workflow == "target_fishing" else None,
        "input": {"asset_id": asset.id, "asset_name": asset.name, "sha256": asset.content_hash, "row_count": len(rows), "columns": header},
        "thresholds": {"evidence": evidence_threshold, "human_similarity": human_threshold, "top_n": top_n},
        "weights": PRIORITIZATION_METRICS if workflow == "prioritization" else FISHING_METRICS,
        "human_similarity_penalty": human_penalty,
        "funnel": funnel,
        "candidates": final,
        "all_candidate_count": len(candidates),
        "warnings": preview["warnings"],
        "boundary": "本结果仅为计算候选靶标优先级，不等于已发现或已确认靶标；需通过基因敲除/过表达、耐药突变、结合实验或化学蛋白组学验证。",
    }


def _example_asset(name: str, data: bytes) -> Asset:
    """Build an in-memory asset so the example uses the production scoring path."""
    return Asset(
        id=f"example-sc5314-{name}",
        project_id="reference-example",
        name=name,
        kind="fungal-target-reference-example",
        storage_uri="embedded://sc5314",
        content_hash=hashlib.sha256(data).hexdigest(),
        size=len(data),
        format="csv",
        created_by="curated-reference-example",
    )


def fungal_data_status_payload() -> dict:
    manifest_path = FUNGAL_TARGET_DATA_DIR / "manifest.json"
    if not manifest_path.is_file():
        return {
            "configured": False,
            "data_dir": str(FUNGAL_TARGET_DATA_DIR),
            "message": "服务器证据库尚未配置；案例将使用内置的小型教学数据。",
            "sources": [],
            "algorithms": [],
            "counts": {},
        }
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "configured": False,
            "data_dir": str(FUNGAL_TARGET_DATA_DIR),
            "message": "服务器证据库清单无法读取；案例将使用内置的小型教学数据。",
            "sources": [],
            "algorithms": [],
            "counts": {},
        }
    return {
        "configured": bool(manifest.get("configured")),
        "bundle_version": manifest.get("bundle_version"),
        "generated_at": manifest.get("generated_at"),
        "organism": manifest.get("organism"),
        "sources": manifest.get("sources", []),
        "algorithms": manifest.get("algorithms", []),
        "counts": manifest.get("counts", {}),
        "message": "核心证据库已在 jingmiao 本地配置。" if manifest.get("configured") else "证据库清单存在，但尚未完成配置。",
    }


def _server_sc5314_matrix() -> tuple[bytes, dict] | None:
    manifest_path = FUNGAL_TARGET_DATA_DIR / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        relative = str(manifest.get("case_files", {}).get("sc5314_prioritization") or "")
        if not relative:
            return None
        root = FUNGAL_TARGET_DATA_DIR.resolve()
        matrix_path = (root / relative).resolve()
        matrix_path.relative_to(root)
        data = matrix_path.read_bytes()
        if len(data) > MAX_INPUT_BYTES:
            raise OSError("SC5314 evidence matrix exceeds online analysis limit")
        return data, manifest
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def sc5314_example_payload() -> dict:
    server_matrix = _server_sc5314_matrix()
    prioritization_data = server_matrix[0] if server_matrix else SC5314_PRIORITIZATION_CSV
    prioritization_name = "sc5314-server-evidence.csv" if server_matrix else "sc5314-target-prioritization.csv"
    prioritization = analyse_payload(
        _example_asset(prioritization_name, prioritization_data),
        prioritization_data,
        "prioritization",
        {
            "organism": "Candida albicans SC5314",
            "top_n": 50,
            "evidence_threshold": 0.50,
            "human_similarity_threshold": 0.35,
        },
    )
    fishing = analyse_payload(
        _example_asset("sc5314-fluconazole-target-fishing.csv", SC5314_FISHING_CSV),
        SC5314_FISHING_CSV,
        "target_fishing",
        {
            "organism": "Candida albicans SC5314",
            "compound_name": "Fluconazole / 氟康唑",
            "compound_structure": "InChIKey=RFHAOTPXVQNOHP-UHFFFAOYSA-N",
            "top_n": 20,
            "evidence_threshold": 0.50,
            "human_similarity_threshold": 0.35,
        },
    )
    for result, workflow in ((prioritization, "prioritization"), (fishing, "target_fishing")):
        result.update({
            "job_id": f"example-sc5314-{workflow}",
            "generated_at": "2026-09-24T00:00:00+00:00",
            "example_mode": True,
            "score_interpretation": "0–1 分数是依据公开研究方向整理的演示性标准化编码，用于展示筛选逻辑；不是数据库原始测量值、实验定量结果或临床结论。",
        })
    if server_matrix:
        manifest = server_matrix[1]
        prioritization.update({
            "data_mode": "server_evidence_bundle",
            "bundle_version": manifest.get("bundle_version"),
            "score_interpretation": "优选分数由本地版本化数据库中的序列比对、精确 ID 表型证据和确定性规则归一化得到；不是原始实验测量值，也不是经前瞻验证的成功概率。",
        })
    data_status = fungal_data_status_payload()
    bundled_sources = [
        {"id": f"bundle-{item.get('id')}", "title": f"{item.get('name')} · {item.get('version')}", "url": item.get("url"), "supports": "jingmiao 本地版本化证据库"}
        for item in data_status.get("sources", []) if item.get("url")
    ]
    return {
        "schema_version": "1.0",
        "case_id": "candida-albicans-sc5314",
        "case_version": "2026-09-24",
        "title": "Candida albicans SC5314 参考菌株完整案例",
        "species": "Candida albicans",
        "strain": "SC5314",
        "organism": "Candida albicans SC5314",
        "summary": "经典且资料丰富的白色念珠菌参考菌株，可同时演示从蛋白组证据优选候选靶标，以及以氟康唑为例的候选靶标去卷积。",
        "identifiers": {
            "bioproject": "PRJNA10701",
            "assembly": "GCA_000182965.3",
            "assembly_name": "ASM18296v3",
        },
        "genome": {
            "assembly_level": "Chromosome",
            "chromosome_count": 8,
            "protein_sequence_count": 6030,
            "analysis_protein_count": prioritization["all_candidate_count"],
            "annotation_context": "Assembly 22 / reference genome context",
        },
        "isolation": {
            "year": 1984,
            "source": "念珠菌血症患者血液分离株",
        },
        "why_selected": [
            "有染色体水平参考组装与系统注释，适合展示蛋白组到候选靶标的完整漏斗。",
            "ERG11、FKS1 等经典抗真菌靶点及耐药研究丰富，结果易于解释和核查。",
            "感染、毒力、生物膜、形态转换和药物耐受研究积累充分，可同时展示证据整合与局限性。",
        ],
        "caveats": [
            "SC5314 的 ROB1 等位基因与较强成丝、生物膜及侵袭表型有关，不能把所有菌株层面的结论直接外推到全部 C. albicans 临床分离株。",
            (
                "完整蛋白组优选使用服务器版本化数据库与确定性规则；缺少直接感染表达或口袋计算的蛋白仍明确保留为待补，不把缺失值当成阴性。"
                if server_matrix else
                "内置小型优选案例的 0–1 分数是用于产品演示的人工标准化编码，不是文献中的原始实验数值。"
            ),
            "氟康唑 target fishing 是可核查的预计算示范，不代表系统已经对任意新化合物实时完成反向对接或 AI 结合预测。",
            "靶标优选与 target fishing 均只产生待验证候选，不能替代遗传学、耐药突变、直接结合或化学蛋白组学验证。",
        ],
        "sources": [*SC5314_SOURCES, *bundled_sources],
        "evidence_bundle": data_status,
        "workflows": {
            "prioritization": prioritization,
            "target_fishing": fishing,
        },
    }


def process_fungal_target(db: Session, job: Job) -> dict:
    workflow = str(job.parameters.get("workflow") or "")
    if workflow not in {"prioritization", "target_fishing"}:
        raise FungalTargetError("FUNGAL_TARGET_WORKFLOW", "请选择靶标优选或化合物靶标预测。")
    asset, data = _load_asset(db, job.project_id, str(job.parameters.get("asset_id") or ""))
    result = analyse_payload(asset, data, workflow, job.parameters)
    result.update({"job_id": job.id, "project_id": job.project_id})
    payload = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    storage_uri, digest = save_asset(job.project_id, f"fungal-target-{job.id}.json", payload)
    job.output_ref = storage_uri
    return {
        "adapter": "UploadedFungalTargetEvidenceAdapter",
        "model_name": "真菌候选靶标透明加权优选" if workflow == "prioritization" else "化合物–真菌蛋白多证据靶标预测",
        "model_version": MODEL_VERSION,
        "provenance": {"data_mode": "real", "input_asset_id": asset.id, "input_sha256": asset.content_hash, "output_sha256": digest, "workflow": workflow, "scientific_conclusion": False},
        "demo": False,
    }


def _template(workflow: Workflow) -> str:
    columns = WORKFLOW_COLUMNS[workflow]
    return ",".join(columns) + "\n"


@router.get("/fungal-targets/templates/{workflow}")
def download_template(workflow: Workflow, user: UserContext = Depends(get_current_user)):
    filename = "fungal-target-prioritization.csv" if workflow == "prioritization" else "fungal-compound-target-fishing.csv"
    return Response(_template(workflow), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/fungal-targets/examples/sc5314")
def get_sc5314_example(user: UserContext = Depends(get_current_user)):
    return sc5314_example_payload()


@router.get("/fungal-targets/data-status")
def get_fungal_target_data_status(user: UserContext = Depends(get_current_user)):
    return fungal_data_status_payload()


@router.post("/projects/{project_id}/fungal-targets/preview")
def preview_fungal_targets(project_id: str, payload: PreviewRequest, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    require_project_access(db, project_id, user)
    try:
        asset, data = _load_asset(db, project_id, payload.asset_id)
        return preview_payload(asset, data, payload.workflow)
    except FungalTargetError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from exc


@router.get("/jobs/{job_id}/fungal-target-result")
def get_fungal_target_result(job_id: str, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    job = db.get(Job, job_id)
    if not job or job.type != "fungal_target":
        raise HTTPException(status_code=404, detail="未找到真菌靶标任务。")
    require_project_access(db, job.project_id, user)
    if job.status != JobStatus.completed.value or not job.output_ref:
        raise HTTPException(status_code=409, detail="真菌靶标任务尚未完成。")
    try:
        return json.loads(read_storage_bytes(job.output_ref, MAX_INPUT_BYTES))
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="真菌靶标结果文件无法读取。") from exc
