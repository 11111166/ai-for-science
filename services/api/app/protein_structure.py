"""Project-scoped protein structure evidence, comparison and validation records."""
from __future__ import annotations

import csv
import hashlib
import io
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import quote_plus, urlparse

# Release-local scientific dependencies keep the shared API runtime unchanged.
_chemdeps = Path(__file__).resolve().parent.parent / "_chemdeps"
if _chemdeps.is_dir() and str(_chemdeps) not in sys.path:
    sys.path.insert(0, str(_chemdeps))

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import ForeignKey, Integer, JSON, String, select, update
from sqlalchemy.orm import Mapped, Session, mapped_column

from .auth import UserContext, get_current_user, require_project_access
from .config import get_settings
from .database import Base, get_db
from .models import AuditEvent, new_id

router = APIRouter(prefix="/api/v1/drug-discovery/projects/{project_id}/protein-structures")
MAX_PDB_BYTES = 4_000_000
MAX_SEARCH_BYTES = 1_000_000
MAX_RESIDUES = 2_000
AA3 = {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C", "GLN": "Q",
    "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I", "LEU": "L", "LYS": "K",
    "MET": "M", "PHE": "F", "PRO": "P", "SER": "S", "THR": "T", "TRP": "W",
    "TYR": "Y", "VAL": "V", "SEC": "U", "PYL": "O", "MSE": "M",
}


class ProteinStructureStudy(Base):
    __tablename__ = "protein_structure_studies"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    data: Mapped[dict] = mapped_column(JSON)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class StudyInput(Strict):
    name: str = Field(min_length=1, max_length=160)
    target: str = Field(min_length=1, max_length=160)
    uniprot_accession: str = Field(default="", max_length=16)
    sequence: str = Field(default="", max_length=4_000)
    assembly: Literal["monomer", "multimer"] = "monomer"
    research_question: str = Field(min_length=1, max_length=2_000)

    @field_validator("uniprot_accession")
    @classmethod
    def validate_uniprot(cls, value: str) -> str:
        value = value.upper()
        if value and not re.fullmatch(r"[A-Z0-9]{6,10}(?:-\d+)?", value):
            raise ValueError("UniProt accession 格式不正确")
        return value

    @field_validator("sequence")
    @classmethod
    def validate_sequence(cls, value: str) -> str:
        sequence = re.sub(r"\s+", "", value).upper()
        if sequence and (len(sequence) < 10 or len(sequence) > MAX_RESIDUES or not re.fullmatch(r"[ACDEFGHIKLMNPQRSTVWYOUXBZJ]+", sequence)):
            raise ValueError(f"序列须为 10–{MAX_RESIDUES} 个氨基酸字母")
        return sequence


class RevisionRequest(Strict):
    revision: int = Field(ge=1)


class PdbReferenceRequest(RevisionRequest):
    pdb_id: str = Field(min_length=4, max_length=4)
    chain: str = Field(default="", max_length=4)

    @field_validator("pdb_id")
    @classmethod
    def validate_pdb(cls, value: str) -> str:
        value = value.upper()
        if not re.fullmatch(r"[0-9][A-Z0-9]{3}", value):
            raise ValueError("PDB ID 格式不正确")
        return value

    @field_validator("chain")
    @classmethod
    def validate_chain(cls, value: str) -> str:
        if value and not re.fullmatch(r"[A-Za-z0-9_]", value):
            raise ValueError("参考链须为一个字母或数字")
        return value


class ValidationInput(Strict):
    revision: int = Field(ge=1)
    method: Literal[
        "expression_purification", "xray", "cryo_em", "nmr", "cd", "dsf", "sec_mals",
        "hdx_ms", "crosslink_ms", "binding", "activity", "other",
    ]
    status: Literal["planned", "completed", "failed"] = "planned"
    criterion: str = Field(min_length=1, max_length=1_000)
    result: str = Field(default="", max_length=2_000)
    conclusion: Literal["pending", "supports", "inconclusive", "contradicts"] = "pending"
    source: str = Field(default="", max_length=1_000)
    notes: str = Field(default="", max_length=2_000)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_study(db: Session, project_id: str, study_id: str, user: UserContext, write: bool = False):
    require_project_access(db, project_id, user, "member" if write else "viewer")
    study = db.get(ProteinStructureStudy, study_id)
    if not study or study.project_id != project_id:
        raise HTTPException(404, "蛋白质结构研究不存在")
    return study


def save_data(db: Session, study: ProteinStructureStudy, data: dict, revision: int, user: UserContext, action: str):
    data = {**data, "updated_at": utcnow()}
    changed = db.execute(
        update(ProteinStructureStudy)
        .where(ProteinStructureStudy.id == study.id, ProteinStructureStudy.revision == revision)
        .values(data=data, revision=revision + 1)
    )
    if changed.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "记录已更新，请刷新后重新检查")
    db.add(AuditEvent(
        project_id=study.project_id, actor_id=user.id, action=action,
        entity_type="protein_structure_study", entity_id=study.id,
        details={"revision": revision + 1, "target": data["spec"]["target"]},
    ))
    db.commit()
    db.refresh(study)


def public_structure(structure: dict | None):
    if not structure:
        return None
    result = {key: value for key, value in structure.items() if not key.startswith("_")}
    chains = structure.get("_chains") or {}
    if chains:
        result["viewer"] = viewer_geometry(chains)
    return result


def viewer_geometry(chains: dict[str, list[dict]], budget: int = 900):
    """Return a bounded, read-only C-alpha trace for the authorized Web viewer."""
    total = sum(len(rows) for rows in chains.values())
    sampled: dict[str, list[list[float]]] = {}
    for chain, rows in chains.items():
        if not rows:
            continue
        allowance = min(len(rows), max(2, round(budget * len(rows) / max(total, 1))))
        if allowance >= len(rows):
            selected = rows
        else:
            indices = sorted({round(index * (len(rows) - 1) / (allowance - 1)) for index in range(allowance)})
            selected = [rows[index] for index in indices]
        sampled[chain] = [
            [round(float(row["xyz"][0]), 3), round(float(row["xyz"][1]), 3), round(float(row["xyz"][2]), 3), round(float(row["bfactor"]), 2)]
            for row in selected
        ]
    return {
        "kind": "ca_trace_3d",
        "chains": sampled,
        "sampled_residues": sum(len(rows) for rows in sampled.values()),
        "total_residues": total,
        "boundary": "基于 PDB Cα 坐标的三维主链浏览；不是二级结构丝带，也不替代专业结构叠合。",
    }


def output(study: ProteinStructureStudy):
    data = study.data
    return {
        "id": study.id,
        "revision": study.revision,
        "spec": data["spec"],
        "model": public_structure(data.get("model")),
        "comparisons": data.get("comparisons", []),
        "validations": data.get("validations", []),
        "prediction_task": data.get("prediction_task"),
        "created_at": data["created_at"],
        "updated_at": data.get("updated_at", data["created_at"]),
    }


def parse_pdb(raw: bytes):
    if not raw or len(raw) > MAX_PDB_BYTES:
        raise HTTPException(413, "PDB 文件须大于 0 且不超过 4 MB")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(422, "PDB 文件须为 UTF-8/ASCII 文本") from exc
    chains: dict[str, list[dict]] = {}
    seen = set()
    in_first_model = True
    saw_model = False
    for line in text.splitlines():
        if line.startswith("MODEL"):
            if saw_model:
                break
            saw_model = True
            in_first_model = True
            continue
        if line.startswith("ENDMDL") and in_first_model:
            break
        if not in_first_model or not line.startswith("ATOM  ") or len(line) < 66:
            continue
        if line[12:16].strip() != "CA" or line[16:17] not in (" ", "A"):
            continue
        residue_name = line[17:20].strip().upper()
        if residue_name not in AA3:
            continue
        chain = line[21:22].strip() or "_"
        residue_id = f"{line[22:26].strip()}{line[26:27].strip()}"
        key = (chain, residue_id)
        if key in seen:
            continue
        try:
            x, y, z = float(line[30:38]), float(line[38:46]), float(line[46:54])
            bfactor = float(line[60:66])
        except ValueError:
            continue
        if not all(math.isfinite(v) and abs(v) <= 1_000_000 for v in (x, y, z, bfactor)):
            continue
        seen.add(key)
        chains.setdefault(chain, []).append({"aa": AA3[residue_name], "residue": residue_id, "xyz": [x, y, z], "bfactor": bfactor})
        if len(seen) > MAX_RESIDUES:
            raise HTTPException(422, f"首版最多处理 {MAX_RESIDUES} 个 Cα 残基")
    if len(seen) < 10:
        raise HTTPException(422, "未在 PDB 首个模型中找到至少 10 个标准氨基酸 Cα 原子")
    if len(chains) > 20:
        raise HTTPException(422, "首版最多处理 20 条蛋白链")
    return chains


def projection(chains: dict[str, list[dict]]):
    points = [(chain, row["xyz"]) for chain, rows in chains.items() for row in rows]
    try:
        import numpy as np
        matrix = np.asarray([point for _, point in points], dtype=float)
        centered = matrix - matrix.mean(axis=0)
        _, _, axes = np.linalg.svd(centered, full_matrices=False)
        plane = centered @ axes[:2].T
        span = np.ptp(plane, axis=0)
        scale = float(max(span.max(), 1e-9))
        plane = plane / scale
    except (ImportError, ValueError):
        xs = [point[0] for _, point in points]
        ys = [point[1] for _, point in points]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        scale = max(max(xs) - min(xs), max(ys) - min(ys), 1e-9)
        plane = [[(x - mx) / scale, (y - my) / scale] for x, y in zip(xs, ys)]
    result: dict[str, list[list[float]]] = {}
    for index, (chain, _) in enumerate(points):
        result.setdefault(chain, []).append([round(float(plane[index][0]), 5), round(float(plane[index][1]), 5)])
    for chain, values in result.items():
        if len(values) > 400:
            result[chain] = [values[round(index * (len(values) - 1) / 399)] for index in range(400)]
    return {"method": "主成分二维投影（仅用于浏览，不代表三维叠合）", "chains": result}


def structure_summary(raw: bytes, source: str, source_id: str, file_name: str, provenance: dict):
    chains = parse_pdb(raw)
    rows = [row for values in chains.values() for row in values]
    sequence = "/".join("".join(row["aa"] for row in values) for values in chains.values())
    confidence = None
    if source in {"alphafold_db", "alphafold", "colabfold"}:
        values = [row["bfactor"] for row in rows]
        confidence = {
            "metric": "pLDDT", "mean": round(sum(values) / len(values), 2),
            "very_high_fraction": round(sum(v >= 90 for v in values) / len(values), 4),
            "confident_fraction": round(sum(v >= 70 for v in values) / len(values), 4),
            "low_fraction": round(sum(v < 50 for v in values) / len(values), 4),
            "boundary": "pLDDT 仅表示局部模型置信度，不验证结构域排布、功能或疗效。",
        }
    return {
        "source": source, "source_id": source_id, "file_name": file_name,
        "sha256": hashlib.sha256(raw).hexdigest(), "residue_count": len(rows),
        "chain_count": len(chains), "chain_lengths": {key: len(value) for key, value in chains.items()},
        "observed_sequence": sequence, "confidence": confidence, "projection": projection(chains),
        "provenance": provenance, "_chains": chains,
    }


def add_sequence_check(structure: dict, expected_sequence: str):
    if not expected_sequence:
        structure["sequence_check"] = None
        return structure
    best = None
    for chain, rows in structure["_chains"].items():
        observed = "".join(row["aa"] for row in rows)
        pairs = align_sequences(expected_sequence, observed)
        if not pairs:
            continue
        identity = sum(expected_sequence[i] == observed[j] for i, j in pairs) / len(pairs)
        candidate = {
            "chain": chain, "aligned_residues": len(pairs), "identity": round(identity, 4),
            "input_coverage": round(len(pairs) / len(expected_sequence), 4),
            "coordinate_coverage": round(len(pairs) / len(observed), 4),
            "exact": expected_sequence == observed,
        }
        if best is None or (candidate["aligned_residues"], candidate["identity"]) > (best["aligned_residues"], best["identity"]):
            best = candidate
    structure["sequence_check"] = best
    return structure


def align_sequences(left: str, right: str):
    try:
        import numpy as np
    except ImportError as exc:
        raise HTTPException(503, "结构比较运行库尚未配置") from exc
    rows, cols = len(left) + 1, len(right) + 1
    score = np.empty((rows, cols), dtype=np.int32)
    trace = np.zeros((rows, cols), dtype=np.int8)
    score[:, 0] = np.arange(rows) * -2
    score[0, :] = np.arange(cols) * -2
    for i in range(1, rows):
        for j in range(1, cols):
            choices = (score[i - 1, j - 1] + (2 if left[i - 1] == right[j - 1] else -1), score[i - 1, j] - 2, score[i, j - 1] - 2)
            move = int(max(range(3), key=lambda index: choices[index]))
            score[i, j], trace[i, j] = choices[move], move
    pairs = []
    i, j = len(left), len(right)
    while i and j:
        move = int(trace[i, j])
        if move == 0:
            pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif move == 1:
            i -= 1
        else:
            j -= 1
    return list(reversed(pairs))


def compare_chains(model_chains: dict, reference_chains: dict, requested_chain: str = ""):
    try:
        import numpy as np
    except ImportError as exc:
        raise HTTPException(503, "结构比较运行库尚未配置") from exc
    candidates = []
    for model_chain, model_rows in model_chains.items():
        for reference_chain, reference_rows in reference_chains.items():
            if requested_chain and reference_chain != requested_chain:
                continue
            pairs = align_sequences("".join(row["aa"] for row in model_rows), "".join(row["aa"] for row in reference_rows))
            if len(pairs) < 10:
                continue
            identity = sum(model_rows[i]["aa"] == reference_rows[j]["aa"] for i, j in pairs) / len(pairs)
            candidates.append((len(pairs), identity, model_chain, reference_chain, pairs, model_rows, reference_rows))
    if not candidates:
        raise HTTPException(422, "模型与参考结构之间没有可比较的至少 10 个配准残基")
    aligned_count, identity, model_chain, reference_chain, pairs, model_rows, reference_rows = max(candidates, key=lambda row: (row[0], row[1]))
    moving = np.asarray([model_rows[i]["xyz"] for i, _ in pairs], dtype=float)
    fixed = np.asarray([reference_rows[j]["xyz"] for _, j in pairs], dtype=float)
    moving -= moving.mean(axis=0)
    fixed -= fixed.mean(axis=0)
    u, _, vt = np.linalg.svd(moving.T @ fixed)
    rotation = u @ vt
    if np.linalg.det(rotation) < 0:
        u[:, -1] *= -1
        rotation = u @ vt
    distances = np.linalg.norm(moving @ rotation - fixed, axis=1)
    rmsd = float(np.sqrt(np.mean(distances ** 2)))
    reference_length = len(reference_rows)
    d0 = max(0.5, 1.24 * max(reference_length - 15, 1) ** (1 / 3) - 1.8)
    tm_like = float(np.sum(1 / (1 + (distances / d0) ** 2)) / reference_length)
    return {
        "model_chain": model_chain, "reference_chain": reference_chain,
        "aligned_residues": aligned_count, "sequence_identity": round(identity, 4),
        "model_coverage": round(aligned_count / len(model_rows), 4),
        "reference_coverage": round(aligned_count / reference_length, 4),
        "ca_rmsd_angstrom": round(rmsd, 3), "sequence_registered_tm_like": round(tm_like, 4),
        "metric_boundary": "按序列配准后做 Cα 刚体叠合；TM 样式一致度不是完整 TM-align/Foldseek 搜索，也不能证明功能或疗效。",
    }


def fetch_bytes(url: str):
    try:
        response = httpx.get(url, timeout=20, follow_redirects=True, headers={"User-Agent": "AI4Science-ProteinStructure/1.0"})
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(502, "上游结构库暂时不可用，可改用人工上传 PDB") from exc
    if len(response.content) > MAX_PDB_BYTES:
        raise HTTPException(502, "上游结构文件超过首版 4 MB 限制")
    return response.content


def coordinate_root() -> Path:
    return Path(get_settings().upload_dir)


def coordinate_relative_path(project_id: str, study_id: str, role: str, digest: str) -> Path:
    project_key = hashlib.sha256(project_id.encode()).hexdigest()[:20]
    study_key = hashlib.sha256(study_id.encode()).hexdigest()[:20]
    return Path("protein-structures") / project_key / study_key / f"{role}-{digest[:20]}.pdb"


def persist_coordinates(project_id: str, study_id: str, role: str, raw: bytes) -> str:
    digest = hashlib.sha256(raw).hexdigest()
    relative = coordinate_relative_path(project_id, study_id, role, digest)
    destination = coordinate_root() / relative
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = destination.with_name(destination.name + f".{new_id()}.tmp")
    temporary.write_bytes(raw)
    os.chmod(temporary, 0o600)
    os.replace(temporary, destination)
    return relative.as_posix()


def model_coordinates(study: ProteinStructureStudy) -> bytes:
    model = study.data.get("model")
    if not model:
        raise HTTPException(404, "该研究尚未导入结构模型")
    raw = None
    digest = str(model.get("sha256") or "")
    relative = coordinate_relative_path(study.project_id, study.id, "model", digest)
    root = coordinate_root().resolve()
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root):
        raise HTTPException(500, "结构坐标存储路径无效")
    if resolved.is_file():
        try:
            raw = resolved.read_bytes()
        except OSError as exc:
            raise HTTPException(404, "结构坐标文件暂不可用，请重新导入模型") from exc
    else:
        coordinate_url = str(model.get("provenance", {}).get("coordinate_url") or "")
        parsed = urlparse(coordinate_url)
        if parsed.scheme == "https" and parsed.hostname in {"alphafold.ebi.ac.uk", "files.rcsb.org"}:
            raw = fetch_bytes(coordinate_url)
    if raw is None:
        raise HTTPException(404, "早期人工上传记录未保留完整坐标，请重新上传 PDB 后使用丝带图")
    if hashlib.sha256(raw).hexdigest() != model.get("sha256"):
        raise HTTPException(502, "坐标内容与研究记录哈希不一致，已停止显示")
    if not resolved.is_file():
        persist_coordinates(study.project_id, study.id, "model", raw)
    return raw


def search_uniprot(term: str, field: str = "all", organism_id: int | None = 9606, reviewed_only: bool = False, size: int = 10):
    escaped = term.replace("\\", "\\\\").replace('"', '\\"')
    query_by_field = {
        "all": f'"{escaped}"',
        "gene": f'gene_exact:"{escaped}"',
        "protein": f'protein_name:"{escaped}"',
        "accession": f'accession:"{escaped.upper()}"',
    }
    clauses = [query_by_field[field]]
    if organism_id:
        clauses.append(f"organism_id:{organism_id}")
    if reviewed_only:
        clauses.append("reviewed:true")
    upstream_query = " AND ".join(clauses)
    params = {
        "query": upstream_query,
        "fields": "accession,id,reviewed,protein_name,gene_names,organism_name,length,xref_pdb",
        "format": "tsv",
        "size": str(size),
    }
    try:
        response = httpx.get(
            "https://rest.uniprot.org/uniprotkb/search", params=params, timeout=15,
            follow_redirects=True, headers={"User-Agent": "AI4Science-ProteinStructure/1.1"},
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(502, "UniProt 暂时不可用，请稍后重试；已有结构研究不受影响") from exc
    if len(response.content) > MAX_SEARCH_BYTES:
        raise HTTPException(502, "UniProt 返回内容超过当前检索限制，请缩小关键词范围")
    try:
        reader = csv.DictReader(io.StringIO(response.text), delimiter="\t")
        items = []
        for row in reader:
            accession = (row.get("Entry") or "").strip()
            if not accession:
                continue
            pdb_ids = [value for value in (row.get("PDB") or "").split(";") if value]
            try:
                length = int(row.get("Length") or 0)
            except ValueError:
                length = 0
            genes = (row.get("Gene Names") or "").split()
            items.append({
                "accession": accession,
                "entry_name": (row.get("Entry Name") or "").strip(),
                "reviewed": (row.get("Reviewed") or "").strip().lower() == "reviewed",
                "protein_name": (row.get("Protein names") or "").strip(),
                "gene": genes[0] if genes else "",
                "gene_names": genes,
                "organism": (row.get("Organism") or "").strip(),
                "length": length,
                "pdb_count": len(pdb_ids),
                "pdb_examples": pdb_ids[:5],
                "entry_url": f"https://www.uniprot.org/uniprotkb/{accession}/entry",
            })
    except (csv.Error, UnicodeError) as exc:
        raise HTTPException(502, "UniProt 返回格式暂时无法解析，请稍后重试") from exc
    total_header = response.headers.get("x-total-results", "")
    total = int(total_header) if total_header.isdigit() else len(items)
    return {
        "query": term,
        "items": items,
        "returned": len(items),
        "total": total,
        "source": {
            "name": "UniProtKB",
            "url": f"https://www.uniprot.org/uniprotkb?query={quote_plus(upstream_query)}",
            "retrieved_at": utcnow(),
            "boundary": "检索结果来自 UniProtKB 当前在线记录；数据库收录、审阅状态和 PDB 交叉引用不代表功能或疗效已验证。",
        },
    }


def fetch_alphafold(accession: str):
    api_url = f"https://alphafold.ebi.ac.uk/api/prediction/{accession}"
    try:
        response = httpx.get(api_url, timeout=15, follow_redirects=True, headers={"User-Agent": "AI4Science-ProteinStructure/1.0"})
        response.raise_for_status()
        rows = response.json()
        row = rows[0]
        entry_id, version = str(row["entryId"]), int(row["latestVersion"])
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise HTTPException(502, "AlphaFold DB 暂时不可用或未收录该 accession，可改用人工上传 PDB") from exc
    pdb_url = f"https://alphafold.ebi.ac.uk/files/{entry_id}-model_v{version}.pdb"
    def approved_url(value):
        parsed = urlparse(str(value or ""))
        return str(value) if parsed.scheme == "https" and parsed.hostname == "alphafold.ebi.ac.uk" else ""
    metadata = {
        "global_metric_value": row.get("globalMetricValue"),
        "model_created_date": row.get("modelCreatedDate"),
        "uniprot_description": row.get("uniprotDescription"),
        "pae_image_url": approved_url(row.get("paeImageUrl")),
        "pae_doc_url": approved_url(row.get("paeDocUrl")),
    }
    return fetch_bytes(pdb_url), entry_id, version, api_url, pdb_url, metadata


def fetch_rcsb(pdb_id: str):
    pdb_url = f"https://files.rcsb.org/download/{pdb_id}.pdb"
    metadata_url = f"https://data.rcsb.org/rest/v1/core/entry/{pdb_id}"
    raw = fetch_bytes(pdb_url)
    metadata = {"method": "", "resolution_angstrom": None, "title": "", "ligand_count": None}
    try:
        response = httpx.get(metadata_url, timeout=12, follow_redirects=True, headers={"User-Agent": "AI4Science-ProteinStructure/1.0"})
        response.raise_for_status()
        row = response.json()
        metadata = {
            "method": (row.get("exptl") or [{}])[0].get("method", ""),
            "resolution_angstrom": (row.get("rcsb_entry_info", {}).get("resolution_combined") or [None])[0],
            "title": row.get("struct", {}).get("title", ""),
            "ligand_count": row.get("rcsb_entry_info", {}).get("nonpolymer_entity_count"),
        }
    except (httpx.HTTPError, ValueError, TypeError):
        metadata["metadata_warning"] = "RCSB 元数据暂不可用，坐标比较仍已完成。"
    return raw, metadata, pdb_url, metadata_url


def prediction_task(spec: dict):
    sequence = spec.get("sequence", "")
    return {
        "status": "not_configured", "runner": "RCEES protein-structure worker",
        "task_type": "alphafold_multimer" if spec["assembly"] == "multimer" else "monomer_prediction",
        "sequence_sha256": hashlib.sha256(sequence.encode()).hexdigest() if sequence else "",
        "sequence_length": len(sequence),
        "required_provenance": ["container_digest", "model_weights", "database_versions", "random_seed", "hardware", "output_sha256"],
        "message": "重计算尚未接入；可先从 AlphaFold DB 获取已知蛋白，或上传外部计算所得 PDB。",
    }


@router.get("/search")
def search_proteins(
    project_id: str,
    q: str = Query(min_length=2, max_length=80),
    field: Literal["all", "gene", "protein", "accession"] = "all",
    organism_id: int = Query(default=9606, ge=0, le=10_000_000),
    reviewed_only: bool = False,
    size: int = Query(default=10, ge=1, le=20),
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    require_project_access(db, project_id, user)
    return search_uniprot(q, field, organism_id or None, reviewed_only, size)


@router.get("/studies")
def list_studies(project_id: str, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    require_project_access(db, project_id, user)
    return [output(study) for study in db.scalars(select(ProteinStructureStudy).where(ProteinStructureStudy.project_id == project_id)).all()]


@router.get("/studies/{study_id}/model/coordinates")
def get_model_coordinates(project_id: str, study_id: str, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    study = get_study(db, project_id, study_id, user)
    raw = model_coordinates(study)
    return Response(
        content=raw,
        media_type="chemical/x-pdb",
        headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"},
    )


@router.post("/studies")
def create_study(project_id: str, payload: StudyInput, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    require_project_access(db, project_id, user, "member")
    if len(db.scalars(select(ProteinStructureStudy.id).where(ProteinStructureStudy.project_id == project_id)).all()) >= 50:
        raise HTTPException(422, "每个项目最多 50 个蛋白质结构研究")
    spec = payload.model_dump()
    created = utcnow()
    study = ProteinStructureStudy(project_id=project_id, data={
        "spec": spec, "model": None, "comparisons": [], "validations": [],
        "prediction_task": prediction_task(spec), "created_at": created, "updated_at": created,
    })
    db.add(study)
    db.flush()
    db.add(AuditEvent(project_id=project_id, actor_id=user.id, action="protein_structure.created", entity_type="protein_structure_study", entity_id=study.id, details={"target": spec["target"]}))
    db.commit()
    db.refresh(study)
    return output(study)


@router.post("/studies/{study_id}/model/upload")
def upload_model(project_id: str, study_id: str, revision: int, source: Literal["alphafold", "colabfold", "rosettafold", "experimental", "other"] = "other", file: UploadFile = File(...), db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    study = get_study(db, project_id, study_id, user, True)
    if revision != study.revision:
        raise HTTPException(409, "记录已更新，请刷新后重新上传")
    raw = file.file.read(MAX_PDB_BYTES + 1)
    structure = add_sequence_check(structure_summary(raw, source, "manual-upload", file.filename or "model.pdb", {"retrieved_at": utcnow(), "mode": "manual_upload"}), study.data["spec"].get("sequence", ""))
    persist_coordinates(project_id, study.id, "model", raw)
    save_data(db, study, {**study.data, "model": structure, "comparisons": []}, revision, user, "protein_structure.model_uploaded")
    return output(study)


@router.post("/studies/{study_id}/model/alphafold")
def load_alphafold(project_id: str, study_id: str, payload: RevisionRequest, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    study = get_study(db, project_id, study_id, user, True)
    if payload.revision != study.revision:
        raise HTTPException(409, "记录已更新，请刷新后重新获取")
    accession = study.data["spec"].get("uniprot_accession", "")
    if not accession:
        raise HTTPException(422, "请在新建研究时填写 UniProt accession")
    raw, entry_id, version, api_url, pdb_url, metadata = fetch_alphafold(accession)
    structure = add_sequence_check(structure_summary(raw, "alphafold_db", entry_id, f"{entry_id}-model_v{version}.pdb", {
        "retrieved_at": utcnow(), "model_version": version, "metadata_url": api_url, "coordinate_url": pdb_url, **metadata,
    }), study.data["spec"].get("sequence", ""))
    persist_coordinates(project_id, study.id, "model", raw)
    save_data(db, study, {**study.data, "model": structure, "comparisons": []}, payload.revision, user, "protein_structure.alphafold_loaded")
    return output(study)


def add_comparison(db: Session, study: ProteinStructureStudy, reference: dict, revision: int, user: UserContext):
    model = study.data.get("model")
    if not model:
        raise HTTPException(422, "请先导入预测或模拟结构")
    metrics = compare_chains(model["_chains"], reference["_chains"], reference.get("requested_chain", ""))
    comparison = {
        "id": new_id(), "created_at": utcnow(), "reference": public_structure(reference),
        "metrics": metrics, "interpretation": "该结果只描述当前模型与参考坐标的一致性；功能、结合和效果须由独立实验记录支持。",
    }
    comparisons = study.data.get("comparisons", []) + [comparison]
    save_data(db, study, {**study.data, "comparisons": comparisons}, revision, user, "protein_structure.reference_compared")
    return output(study)


@router.post("/studies/{study_id}/references/pdb")
def compare_pdb(project_id: str, study_id: str, payload: PdbReferenceRequest, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    study = get_study(db, project_id, study_id, user, True)
    if payload.revision != study.revision:
        raise HTTPException(409, "记录已更新，请刷新后重新比较")
    raw, metadata, pdb_url, metadata_url = fetch_rcsb(payload.pdb_id)
    reference = structure_summary(raw, "pdb_experimental", payload.pdb_id, f"{payload.pdb_id}.pdb", {
        "retrieved_at": utcnow(), "coordinate_url": pdb_url, "metadata_url": metadata_url, **metadata,
    })
    reference["requested_chain"] = payload.chain
    return add_comparison(db, study, reference, payload.revision, user)


@router.post("/studies/{study_id}/references/upload")
def compare_upload(project_id: str, study_id: str, revision: int, reference_kind: Literal["experimental", "predicted"] = "experimental", chain: str = "", file: UploadFile = File(...), db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    study = get_study(db, project_id, study_id, user, True)
    if revision != study.revision:
        raise HTTPException(409, "记录已更新，请刷新后重新上传")
    if chain and not re.fullmatch(r"[A-Za-z0-9_]", chain):
        raise HTTPException(422, "参考链须为一个字母或数字")
    raw = file.file.read(MAX_PDB_BYTES + 1)
    reference = structure_summary(raw, "manual_experimental" if reference_kind == "experimental" else "manual_predicted", "manual-upload", file.filename or "reference.pdb", {"retrieved_at": utcnow(), "mode": "manual_upload", "reference_kind": reference_kind})
    reference["requested_chain"] = chain
    return add_comparison(db, study, reference, revision, user)


@router.post("/studies/{study_id}/validations")
def add_validation(project_id: str, study_id: str, payload: ValidationInput, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    study = get_study(db, project_id, study_id, user, True)
    if payload.revision != study.revision:
        raise HTTPException(409, "记录已更新，请刷新后重新保存")
    if payload.status == "completed" and not payload.result:
        raise HTTPException(422, "已完成验证须填写实测结果")
    if payload.status == "planned" and payload.conclusion != "pending":
        raise HTTPException(422, "待执行验证不能预先填写结论")
    validation = {"id": new_id(), "created_at": utcnow(), **payload.model_dump(exclude={"revision"})}
    save_data(db, study, {**study.data, "validations": study.data.get("validations", []) + [validation]}, payload.revision, user, "protein_structure.validation_saved")
    return output(study)
