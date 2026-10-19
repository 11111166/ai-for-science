import html
import io
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from .auth import UserContext, get_current_user, require_project_access
from .config import get_settings
from .database import get_db
from .jobs import execute_job
from .models import (
    Asset,
    AuditEvent,
    Compound,
    CompoundEvaluation,
    Dataset,
    Job,
    JobEvent,
    JobStatus,
    PerturbationResult,
    Project,
    ProjectMember,
    Ranking,
    Report,
    TargetCandidate,
    TargetDiscoveryRun,
    TargetEvidence,
)
from .schemas import (
    AnalysisRequest,
    DatasetCreate,
    JobAccepted,
    JobOut,
    MemberCreate,
    MemberOut,
    ProjectCreate,
    ProjectOut,
    ProjectUpdate,
    RankingUpdate,
    ReportCreate,
    TargetSelectionUpdate,
    TargetUpdate,
)
from .novel_catalog import novel_availability
from .open_targets import OpenTargetsError
from .perturbation_snapshot import PerturbationKnockoutSnapshotAdapter
from .virtual_cell_snapshot import VirtualCellSnapshotAdapter
from .compound_snapshot import CompoundEvaluationSnapshotAdapter
from .storage import save_asset
from .methodology import METHODOLOGY
from .source_catalog import DATA_SOURCES
from .protein_structure import ProteinStructureStudy
from .process_development import ProcessStudy
from .process_scaleup import ScaleAssessment
from .process_pfd import PfdDiagram
from .process_portfolio import ProcessPortfolio
from .hte_demo import HteSession
from .order_assessment import OrderAssessment
from .molecule_workbench import LibraryMolecule, ValidationTask


router = APIRouter(prefix="/api/v1/drug-discovery")
SYSTEM_CACHE_OWNER = "system-disease-cache"

REUSABLE_ANALYSIS_JOB_TYPES = {
    "target_discovery", "perturbation", "gene_knockout", "virtual_cell",
    "compound_matching", "admet", "ranking", "omics_analysis",
    "fungal_target", "fungal_compound", "bacterial_compound",
}

CACHE_MODULE_ORDER = [
    "data", "omics_analysis", "target_discovery", "fungal_target", "fungal_compound",
    "bacterial_compound",
    "perturbation", "gene_knockout", "virtual_cell", "protein_structure",
    "compound_matching", "admet", "process", "ranking", "report",
]

PERSISTED_RESULT_MODELS = {
    "data": (Asset, Dataset),
    "protein_structure": (ProteinStructureStudy,),
    "process": (ProcessStudy, ScaleAssessment, PfdDiagram, ProcessPortfolio, HteSession, OrderAssessment),
    "compound_matching": (LibraryMolecule,),
    "ranking": (ValidationTask,),
    "report": (Report,),
}


@router.get("/methodology")
def get_methodology(user: UserContext = Depends(get_current_user)):
    return METHODOLOGY


@router.get("/data-sources")
def get_data_sources(user: UserContext = Depends(get_current_user)):
    return DATA_SOURCES


def as_dict(row):
    return {column.key: getattr(row, column.key) for column in inspect(row).mapper.column_attrs}


def audit(db: Session, user: UserContext, action: str, entity_type: str, entity_id: str, project_id: str | None = None, details: dict | None = None):
    db.add(AuditEvent(project_id=project_id, actor_id=user.id, action=action, entity_type=entity_type, entity_id=entity_id, details=details or {}))


class ProjectActivityRequest(BaseModel):
    view: str = Field(default="overview", max_length=80)


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    project_type: Literal["research", "validation"] = Query("research"),
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    # 要求 27：权限全面放开，所有账号都能列出全部项目（不再按成员身份过滤）。
    query = select(Project).where(Project.project_type == project_type, Project.owner_id != SYSTEM_CACHE_OWNER).order_by(Project.updated_at.desc()).limit(limit).offset(offset)
    return db.scalars(query).all()


@router.get("/disease-cache-index")
def disease_cache_index(db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    projects = db.scalars(
        select(Project)
        .where(Project.project_type == "research", Project.status == "active")
        .order_by(Project.updated_at.desc())
    ).all()
    project_ids = [project.id for project in projects]
    jobs_by_project: dict[str, list[Job]] = {project_id: [] for project_id in project_ids}
    target_counts: dict[str, int] = {}
    persisted_by_project: dict[str, dict[str, int]] = {project_id: {} for project_id in project_ids}
    if project_ids:
        completed_jobs = db.scalars(
            select(Job)
            .where(Job.project_id.in_(project_ids), Job.status == JobStatus.completed.value)
            .order_by(Job.completed_at.desc(), Job.created_at.desc())
        ).all()
        for job in completed_jobs:
            jobs_by_project.setdefault(job.project_id, []).append(job)
        target_counts = {
            job_id: int(count)
            for job_id, count in db.execute(
                select(TargetCandidate.job_id, func.count(TargetCandidate.id))
                .where(TargetCandidate.project_id.in_(project_ids))
                .group_by(TargetCandidate.job_id)
            ).all()
        }
        for module, models in PERSISTED_RESULT_MODELS.items():
            for model in models:
                for project_id, count in db.execute(
                    select(model.project_id, func.count(model.id))
                    .where(model.project_id.in_(project_ids))
                    .group_by(model.project_id)
                ).all():
                    persisted_by_project.setdefault(project_id, {})[module] = (
                        persisted_by_project.get(project_id, {}).get(module, 0) + int(count)
                    )
    result = []
    for project in projects:
        completed = jobs_by_project.get(project.id, [])
        target_jobs = [job for job in completed if job.type == "target_discovery"]
        best_target_count = max((target_counts.get(job.id, 0) for job in target_jobs), default=0)
        persisted = persisted_by_project.get(project.id, {})
        cached_module_set = {job.type for job in completed} | {module for module, count in persisted.items() if count > 0}
        cached_modules = [module for module in CACHE_MODULE_ORDER if module in cached_module_set]
        cached_modules.extend(sorted(cached_module_set - set(CACHE_MODULE_ORDER)))
        cached_result_count = len(completed) + sum(persisted.values())
        completed_modules = len(cached_modules)
        latest = max((job.completed_at or job.created_at for job in completed), default=None)
        strategies = sorted({str((job.parameters or {}).get("discovery_strategy") or "established") for job in target_jobs})
        result.append({
            "project_id": project.id,
            "project_name": project.name,
            "disease": project.disease,
            "disease_id": project.disease_id,
            "owner_id": project.owner_id,
            "has_cached_results": cached_result_count > 0,
            "completed_modules": completed_modules,
            "completed_jobs": len(completed),
            "cached_modules": cached_modules,
            "cached_result_count": cached_result_count,
            "target_count": best_target_count,
            "target_analyses": len(target_jobs),
            "target_strategies": strategies,
            "latest_cached_at": latest,
        })
    return result


@router.post("/projects", response_model=ProjectOut, status_code=201)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    if payload.project_type == "validation" and user.system_role != "admin":
        raise HTTPException(status_code=403, detail="Only administrators can create internal validation projects.")
    project = Project(**payload.model_dump(), owner_id=user.id)
    db.add(project)
    db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner"))
    audit(db, user, "project.created", "project", project.id, project.id)
    db.commit()
    db.refresh(project)
    return project


@router.get("/projects/{project_id}", response_model=ProjectOut)
def get_project(project_id: str, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    return require_project_access(db, project_id, user)


@router.patch("/projects/{project_id}", response_model=ProjectOut)
def update_project(project_id: str, payload: ProjectUpdate, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    project = require_project_access(db, project_id, user, "owner")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(project, key, value)
    audit(db, user, "project.updated", "project", project.id, project.id, payload.model_dump(exclude_unset=True))
    db.commit()
    db.refresh(project)
    return project


@router.get("/projects/{project_id}/members", response_model=list[MemberOut])
def list_members(project_id: str, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    require_project_access(db, project_id, user)
    return db.scalars(select(ProjectMember).where(ProjectMember.project_id == project_id)).all()


@router.post("/projects/{project_id}/members", response_model=MemberOut, status_code=201)
def add_member(project_id: str, payload: MemberCreate, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    require_project_access(db, project_id, user, "owner")
    existing = db.scalar(select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id == payload.user_id))
    if existing:
        existing.role = payload.role
        member = existing
    else:
        member = ProjectMember(project_id=project_id, **payload.model_dump())
        db.add(member)
    audit(db, user, "member.upserted", "project_member", payload.user_id, project_id, {"role": payload.role})
    db.commit()
    db.refresh(member)
    return member


@router.post("/projects/{project_id}/assets", status_code=201)
async def upload_asset(
    project_id: str,
    file: UploadFile = File(...),
    kind: str = "file",
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    require_project_access(db, project_id, user, "member")
    data = await file.read(get_settings().max_upload_bytes + 1)
    if len(data) > get_settings().max_upload_bytes:
        raise HTTPException(status_code=413, detail="File exceeds upload limit")
    storage_uri, digest = save_asset(project_id, file.filename or "upload.bin", data)
    asset = Asset(
        project_id=project_id,
        name=file.filename or "upload.bin",
        kind=kind,
        storage_uri=storage_uri,
        content_hash=digest,
        size=len(data),
        format=Path(file.filename or "").suffix.lstrip(".") or "unknown",
        metadata_json={"content_type": file.content_type},
        created_by=user.id,
    )
    db.add(asset)
    db.flush()
    audit(db, user, "asset.uploaded", "asset", asset.id, project_id, {"hash": digest, "size": len(data)})
    db.commit()
    db.refresh(asset)
    return as_dict(asset)


@router.get("/projects/{project_id}/assets")
def list_assets(project_id: str, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    require_project_access(db, project_id, user)
    return [as_dict(item) for item in db.scalars(select(Asset).where(Asset.project_id == project_id).order_by(Asset.created_at.desc())).all()]


@router.post("/projects/{project_id}/datasets", status_code=201)
def create_dataset(project_id: str, payload: DatasetCreate, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    require_project_access(db, project_id, user, "member")
    dataset = Dataset(project_id=project_id, **payload.model_dump())
    db.add(dataset)
    db.flush()
    audit(db, user, "dataset.created", "dataset", dataset.id, project_id)
    db.commit()
    db.refresh(dataset)
    return as_dict(dataset)


@router.get("/projects/{project_id}/datasets")
def list_datasets(project_id: str, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    require_project_access(db, project_id, user)
    return [as_dict(item) for item in db.scalars(select(Dataset).where(Dataset.project_id == project_id)).all()]


@router.get("/target-discovery-availability")
def target_discovery_availability(disease_query: str = Query(min_length=1, max_length=300), user: UserContext = Depends(get_current_user)):
    return novel_availability(get_settings(), disease_query.strip())


def submit_job(project_id: str, job_type: str, payload: AnalysisRequest, background_tasks: BackgroundTasks, db: Session, user: UserContext, retry_of: str | None = None, response: Response | None = None):
    project = require_project_access(db, project_id, user, "member")
    adapter_mode = "real" if payload.adapter_mode == "runtime" else payload.adapter_mode
    parameters = dict(payload.parameters)
    if job_type == "target_discovery" and adapter_mode == "real":
        top_n = parameters.get("top_n", 50)
        strategy = str(parameters.get("discovery_strategy") or "established")
        if strategy not in {"established", "novel"}:
            raise HTTPException(status_code=422, detail="不支持的靶点发现策略。")
        if strategy == "novel":
            availability = novel_availability(get_settings(), str(parameters.get("disease_query") or project.disease_id or project.disease))
            if not availability["available"]:
                raise HTTPException(status_code=409, detail=availability["message"])
        maximum = 200 if strategy == "novel" else 100
        if isinstance(top_n, bool) or not isinstance(top_n, int) or not 1 <= top_n <= maximum:
            raise HTTPException(status_code=422, detail=f"top_n must be an integer between 1 and {maximum}")
        parameters["top_n"] = top_n
        parameters["include_indirect"] = bool(parameters.get("include_indirect", True))
        if "disease_query" in parameters and not str(parameters["disease_query"]).strip():
            raise HTTPException(status_code=422, detail="disease_query cannot be empty")
    if job_type in {"perturbation", "gene_knockout", "virtual_cell", "compound_matching"} and adapter_mode == "real":
        top_n = parameters.get("top_n", 50)
        if isinstance(top_n, bool) or not isinstance(top_n, int) or not 1 <= top_n <= 200:
            raise HTTPException(status_code=422, detail="top_n must be an integer between 1 and 200")
        parameters["top_n"] = top_n
    if job_type == "omics_analysis":
        if adapter_mode != "real":
            raise HTTPException(status_code=422, detail="上传组学文件只能使用真实数据分析模式。")
        if parameters.get("modality") not in {"bulk_transcriptomics", "single_cell", "proteomics", "metabolomics"}:
            raise HTTPException(status_code=422, detail="请选择转录组、单细胞、蛋白组或代谢组。")
        asset = db.get(Asset, str(parameters.get("asset_id") or ""))
        if not asset or asset.project_id != project_id:
            raise HTTPException(status_code=422, detail="请选择当前项目中已经上传的文件。")
    if job_type == "fungal_target":
        if adapter_mode != "real":
            raise HTTPException(status_code=422, detail="真菌靶标模块只处理项目上传的真实证据矩阵。")
        workflow = str(parameters.get("workflow") or "")
        if workflow not in {"prioritization", "target_fishing"}:
            raise HTTPException(status_code=422, detail="请选择靶标优选或化合物靶标预测。")
        asset = db.get(Asset, str(parameters.get("asset_id") or ""))
        if not asset or asset.project_id != project_id:
            raise HTTPException(status_code=422, detail="请选择当前项目中已经上传的真菌证据矩阵。")
        organism = str(parameters.get("organism") or "").strip()
        if len(organism) < 2 or len(organism) > 180:
            raise HTTPException(status_code=422, detail="请填写本次分析的真菌物种或菌株。")
        if workflow == "target_fishing" and not (str(parameters.get("compound_name") or "").strip() or str(parameters.get("compound_structure") or "").strip()):
            raise HTTPException(status_code=422, detail="化合物靶标预测至少需要化合物名称或 SMILES / InChIKey。")
        top_n = parameters.get("top_n", 50)
        if isinstance(top_n, bool) or not isinstance(top_n, int) or not 1 <= top_n <= 200:
            raise HTTPException(status_code=422, detail="候选数量须为 1–200 的整数。")
    if job_type == "fungal_compound":
        if adapter_mode != "real":
            raise HTTPException(status_code=422, detail="真菌化合物探索只使用真实证据与真实结构计算。")
        target_ids = parameters.get("target_ids")
        if not isinstance(target_ids, list) or not [item for item in target_ids if str(item).strip()]:
            raise HTTPException(status_code=422, detail="请选择至少一个靶标。")
        if len(target_ids) > 12:
            raise HTTPException(status_code=422, detail="单次最多分析 12 个靶标。")
        threshold = parameters.get("similarity_threshold", 70)
        if isinstance(threshold, bool) or not isinstance(threshold, int) or not 40 <= threshold <= 100:
            raise HTTPException(status_code=422, detail="相似性阈值须为 40–100 的整数。")
        max_generated = parameters.get("max_generated", 25)
        if isinstance(max_generated, bool) or not isinstance(max_generated, int) or not 1 <= max_generated <= 60:
            raise HTTPException(status_code=422, detail="生成数量须为 1–60 的整数。")
        organism = str(parameters.get("organism") or "").strip()
        if len(organism) > 180:
            raise HTTPException(status_code=422, detail="物种名称过长。")
    if job_type == "bacterial_compound":
        if adapter_mode != "real":
            raise HTTPException(status_code=422, detail="细菌化合物探索只使用真实证据与真实结构计算。")
        target_ids = parameters.get("target_ids")
        if not isinstance(target_ids, list) or not [item for item in target_ids if str(item).strip()]:
            raise HTTPException(status_code=422, detail="请选择至少一个靶标。")
        if len(target_ids) > 12:
            raise HTTPException(status_code=422, detail="单次最多分析 12 个靶标。")
        threshold = parameters.get("similarity_threshold", 70)
        if isinstance(threshold, bool) or not isinstance(threshold, int) or not 40 <= threshold <= 100:
            raise HTTPException(status_code=422, detail="相似性阈值须为 40–100 的整数。")
        max_generated = parameters.get("max_generated", 25)
        if isinstance(max_generated, bool) or not isinstance(max_generated, int) or not 1 <= max_generated <= 60:
            raise HTTPException(status_code=422, detail="生成数量须为 1–60 的整数。")
        organism = str(parameters.get("organism") or "").strip()
        if len(organism) > 180:
            raise HTTPException(status_code=422, detail="物种名称过长。")
    if job_type in REUSABLE_ANALYSIS_JOB_TYPES and retry_of is None:
        cached_jobs = db.scalars(
            select(Job)
            .where(
                Job.project_id == project_id,
                Job.type == job_type,
                Job.status == JobStatus.completed.value,
                Job.adapter_mode == adapter_mode,
                Job.input_version == payload.input_version,
            )
            .order_by(Job.completed_at.desc(), Job.created_at.desc())
        ).all()
        for cached_job in cached_jobs:
            if (cached_job.parameters or {}) != parameters:
                continue
            if job_type == "target_discovery":
                target_count = db.scalar(
                    select(func.count(TargetCandidate.id)).where(TargetCandidate.job_id == cached_job.id)
                ) or 0
                if target_count <= 0:
                    continue
            elif not cached_job.output_ref:
                continue
            if response is not None:
                response.headers["X-AI4Science-Cache-Reused"] = "1"
            return JobAccepted(
                job_id=cached_job.id,
                status=cached_job.status,
                status_url=f"/api/v1/drug-discovery/jobs/{cached_job.id}",
                cache_hit=True,
                reused_from_job_id=cached_job.id,
            )

    job = Job(
        project_id=project_id,
        type=job_type,
        adapter_mode=adapter_mode,
        parameters=parameters,
        input_version=payload.input_version,
        retry_of=retry_of,
        requested_by=user.id,
    )
    db.add(job)
    db.flush()
    db.add(JobEvent(job_id=job.id, status=JobStatus.queued.value, stage="queued", progress=0, message="Task queued"))
    audit(db, user, "job.created", "job", job.id, project_id, {"type": job_type, "mode": adapter_mode})
    db.commit()
    if get_settings().job_execution_mode == "celery":
        from .worker import run_job
        run_job.delay(job.id)
    else:
        background_tasks.add_task(execute_job, job.id)
    return JobAccepted(job_id=job.id, status=job.status, status_url=f"/api/v1/drug-discovery/jobs/{job.id}")


def analysis_endpoint(job_type: str):
    def endpoint(project_id: str, payload: AnalysisRequest, background_tasks: BackgroundTasks, response: Response, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
        return submit_job(project_id, job_type, payload, background_tasks, db, user, response=response)
    return endpoint


router.add_api_route("/projects/{project_id}/target-discovery-jobs", analysis_endpoint("target_discovery"), methods=["POST"], response_model=JobAccepted, status_code=202)
router.add_api_route("/projects/{project_id}/perturbation-jobs", analysis_endpoint("perturbation"), methods=["POST"], response_model=JobAccepted, status_code=202)
router.add_api_route("/projects/{project_id}/gene-knockout-jobs", analysis_endpoint("gene_knockout"), methods=["POST"], response_model=JobAccepted, status_code=202)
router.add_api_route("/projects/{project_id}/virtual-cell-jobs", analysis_endpoint("virtual_cell"), methods=["POST"], response_model=JobAccepted, status_code=202)
router.add_api_route("/projects/{project_id}/compound-matching-jobs", analysis_endpoint("compound_matching"), methods=["POST"], response_model=JobAccepted, status_code=202)
router.add_api_route("/projects/{project_id}/admet-jobs", analysis_endpoint("admet"), methods=["POST"], response_model=JobAccepted, status_code=202)
router.add_api_route("/projects/{project_id}/ranking-jobs", analysis_endpoint("ranking"), methods=["POST"], response_model=JobAccepted, status_code=202)
router.add_api_route("/projects/{project_id}/omics-analysis-jobs", analysis_endpoint("omics_analysis"), methods=["POST"], response_model=JobAccepted, status_code=202)
router.add_api_route("/projects/{project_id}/fungal-target-jobs", analysis_endpoint("fungal_target"), methods=["POST"], response_model=JobAccepted, status_code=202)
router.add_api_route("/projects/{project_id}/fungal-compound-jobs", analysis_endpoint("fungal_compound"), methods=["POST"], response_model=JobAccepted, status_code=202)
router.add_api_route("/projects/{project_id}/bacterial-compound-jobs", analysis_endpoint("bacterial_compound"), methods=["POST"], response_model=JobAccepted, status_code=202)


@router.get("/projects/{project_id}/jobs", response_model=list[JobOut])
def list_jobs(project_id: str, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    require_project_access(db, project_id, user)
    return db.scalars(select(Job).where(Job.project_id == project_id).order_by(Job.created_at.desc())).all()


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    require_project_access(db, job.project_id, user)
    return job


@router.get("/jobs/{job_id}/events")
def get_job_events(job_id: str, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    require_project_access(db, job.project_id, user)
    return [as_dict(item) for item in db.scalars(select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.created_at)).all()]


@router.post("/jobs/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: str, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    require_project_access(db, job.project_id, user, "member")
    if job.status not in {JobStatus.queued.value, JobStatus.running.value}:
        raise HTTPException(status_code=409, detail="Job cannot be cancelled")
    job.status = JobStatus.cancelled.value
    job.stage = "cancelled"
    db.add(JobEvent(job_id=job.id, status=job.status, stage=job.stage, progress=job.progress, message="Cancelled by user"))
    audit(db, user, "job.cancelled", "job", job.id, job.project_id)
    db.commit()
    db.refresh(job)
    return job


@router.post("/jobs/{job_id}/retry", response_model=JobAccepted, status_code=202)
def retry_job(job_id: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    original = db.get(Job, job_id)
    if not original:
        raise HTTPException(status_code=404, detail="Job not found")
    if original.status not in {JobStatus.failed.value, JobStatus.cancelled.value, JobStatus.completed.value}:
        raise HTTPException(status_code=409, detail="Job is not retryable")
    payload = AnalysisRequest(adapter_mode=original.adapter_mode, input_version=original.input_version, parameters=original.parameters)
    return submit_job(original.project_id, original.type, payload, background_tasks, db, user, retry_of=original.id)


@router.get("/projects/{project_id}/targets")
def list_targets(
    project_id: str,
    job_id: str | None = None,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    require_project_access(db, project_id, user)
    selected_job_id = job_id or db.scalar(
        select(Job.id)
        .where(Job.project_id == project_id, Job.type == "target_discovery", Job.status == JobStatus.completed.value)
        .order_by(Job.completed_at.desc(), Job.created_at.desc())
        .limit(1)
    )
    if not selected_job_id:
        return []
    job = db.get(Job, selected_job_id)
    if not job or job.project_id != project_id or job.type != "target_discovery":
        raise HTTPException(status_code=404, detail="Target discovery job not found")
    targets = db.scalars(
        select(TargetCandidate)
        .where(TargetCandidate.project_id == project_id, TargetCandidate.job_id == selected_job_id)
        .order_by(TargetCandidate.rank.asc().nullslast(), TargetCandidate.score.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return [as_dict(item) for item in targets]


@router.get("/jobs/{job_id}/target-discovery-result")
def get_target_discovery_result(job_id: str, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    job = db.get(Job, job_id)
    if not job or job.type != "target_discovery":
        raise HTTPException(status_code=404, detail="Target discovery job not found")
    require_project_access(db, job.project_id, user)
    run = db.scalar(select(TargetDiscoveryRun).where(TargetDiscoveryRun.job_id == job_id))
    if not run:
        raise HTTPException(status_code=404, detail="Target discovery result not found")
    return as_dict(run)


@router.get("/targets/{target_id}")
def get_target(target_id: str, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    target = db.get(TargetCandidate, target_id)
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")
    require_project_access(db, target.project_id, user)
    value = as_dict(target)
    value["evidence_total"] = db.scalar(select(func.count(TargetEvidence.id)).where(TargetEvidence.target_id == target.id)) or 0
    value["evidence"] = [
        as_dict(item)
        for item in db.scalars(
            select(TargetEvidence).where(TargetEvidence.target_id == target.id).order_by(TargetEvidence.score.desc()).limit(25)
        ).all()
    ]
    return value


@router.get("/targets/{target_id}/evidence")
def list_target_evidence(
    target_id: str,
    category: str | None = None,
    datatype: str | None = None,
    datasource: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    target = db.get(TargetCandidate, target_id)
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")
    require_project_access(db, target.project_id, user)
    conditions = [TargetEvidence.target_id == target_id]
    if category:
        conditions.append(TargetEvidence.category == category)
    if datatype:
        conditions.append(TargetEvidence.datatype == datatype)
    if datasource:
        conditions.append(TargetEvidence.datasource == datasource)
    total = db.scalar(select(func.count(TargetEvidence.id)).where(*conditions)) or 0
    items = db.scalars(
        select(TargetEvidence)
        .where(*conditions)
        .order_by(TargetEvidence.score.desc(), TargetEvidence.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return {"items": [as_dict(item) for item in items], "total": total, "limit": limit, "offset": offset}


@router.get("/targets/{target_id}/molecules")
def target_molecules(target_id: str, refresh: bool = False, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    target = db.get(TargetCandidate, target_id)
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")
    require_project_access(db, target.project_id, user)
    from .drug_catalog import lookup
    if target.demo:
        return {"status": "available", "items": [], "warnings": ["演示靶点不混入真实药物检索。"]}
    return lookup("target", (target.symbol.upper(), target.external_id), refresh=refresh)


@router.get("/molecules/{identifier}/activities")
def molecule_activities(identifier: str, target: str, user: UserContext = Depends(get_current_user)):
    import re
    if not all(re.fullmatch(r"CHEMBL\d{1,12}", value.upper()) for value in (identifier, target)):
        raise HTTPException(status_code=422, detail="Invalid ChEMBL identifier")
    from .drug_catalog import lookup
    return lookup("activities", (identifier.upper(), target.upper()))


@router.get("/molecules/{identifier}/identity")
def molecule_identity(identifier: str, user: UserContext = Depends(get_current_user)):
    import re
    if not re.fullmatch(r"CHEMBL\d{1,12}", identifier.upper()):
        raise HTTPException(status_code=422, detail="Invalid ChEMBL molecule identifier")
    from .compound_identity import identity
    return identity(identifier.upper())


@router.get("/molecules/{identifier}/dossier")
def molecule_dossier(identifier: str, user: UserContext = Depends(get_current_user)):
    import re
    if not re.fullmatch(r"CHEMBL\d{1,12}", identifier.upper()):
        raise HTTPException(status_code=422, detail="Invalid ChEMBL molecule identifier")
    from .drug_catalog import lookup
    return lookup("molecule", identifier.upper())


@router.patch("/targets/{target_id}")
def update_target(target_id: str, payload: TargetUpdate, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    target = db.get(TargetCandidate, target_id)
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")
    require_project_access(db, target.project_id, user, "member")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(target, key, value)
    audit(db, user, "target.updated", "target", target.id, target.project_id, payload.model_dump(exclude_unset=True))
    db.commit()
    db.refresh(target)
    return as_dict(target)


def _target_job(db: Session, project_id: str, job_id: str) -> tuple[Job, TargetDiscoveryRun]:
    job = db.get(Job, job_id)
    if not job or job.project_id != project_id or job.type != "target_discovery" or job.status != JobStatus.completed.value:
        raise HTTPException(status_code=404, detail="没有找到指定的已完成靶点分析。")
    run = db.scalar(select(TargetDiscoveryRun).where(TargetDiscoveryRun.job_id == job_id))
    if not run:
        raise HTTPException(status_code=404, detail="指定靶点分析缺少可追溯的疾病结果。")
    return job, run


@router.patch("/projects/{project_id}/targets/selection")
def update_target_selection(
    project_id: str,
    payload: TargetSelectionUpdate,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    require_project_access(db, project_id, user, "member")
    _target_job(db, project_id, payload.job_id)
    targets = db.scalars(
        select(TargetCandidate)
        .where(TargetCandidate.project_id == project_id, TargetCandidate.job_id == payload.job_id)
        .order_by(TargetCandidate.rank.asc().nullslast(), TargetCandidate.score.desc())
    ).all()
    selected_ids = {item.id for item in targets[:payload.top_n]} if payload.mode == "top_n" else {item.id for item in targets} if payload.mode == "all" else set()
    for target in targets:
        target.selected = target.id in selected_ids
    audit(db, user, "targets.selection_updated", "target_discovery_job", payload.job_id, project_id, {
        "mode": payload.mode, "top_n": payload.top_n, "selected_count": len(selected_ids),
    })
    db.commit()
    return {"job_id": payload.job_id, "selected_count": len(selected_ids), "total_count": len(targets)}


@router.get("/projects/{project_id}/target-compatibility")
def get_target_compatibility(
    project_id: str,
    target_job_id: str,
    module: str = Query(pattern="^(virtual_intervention|gene_knockout|virtual_cell|compound_matching)$"),
    top_n: int = Query(10, ge=1, le=200),
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    require_project_access(db, project_id, user)
    target_job, run = _target_job(db, project_id, target_job_id)
    targets = db.scalars(
        select(TargetCandidate)
        .where(TargetCandidate.project_id == project_id, TargetCandidate.job_id == target_job_id)
        .order_by(TargetCandidate.rank.asc().nullslast(), TargetCandidate.score.desc())
    ).all()
    selected = [item for item in targets if item.selected]
    # An explicit selection is the user's complete requested set. ``top_n``
    # only supplies the default when nothing is selected.
    requested = selected if selected else targets[:top_n]
    baseline_candidates = [item for item in requested if target_job.adapter_mode in {"real", "runtime"} and item.data_mode == "real" and not item.demo]
    base = {
        "target_job_id": target_job_id,
        "module": module,
        "upstream_disease_id": run.disease_id,
        "upstream_disease_name": run.canonical_name or run.query,
        "strategy": str((run.parameters or {}).get("discovery_strategy") or "established"),
        "selection_mode": "selected" if selected else "top_n_default",
        "selected_count": len(selected),
        "requested_count": len(requested),
    }
    settings = get_settings()
    if module in {"virtual_intervention", "gene_knockout"}:
        snapshot_path = settings.perturbation_knockout_snapshot_path
        adapter_type = PerturbationKnockoutSnapshotAdapter
        missing_code = "PERTURBATION_KNOCKOUT_NOT_CONFIGURED"
        missing_message = "真实虚拟干预与基因敲除数据尚未配置。"
    elif module == "virtual_cell":
        snapshot_path = settings.virtual_cell_snapshot_path
        adapter_type = VirtualCellSnapshotAdapter
        missing_code = "VIRTUAL_CELL_NOT_CONFIGURED"
        missing_message = "虚拟细胞 v1 数据尚未配置。"
    else:
        snapshot_path = settings.compound_evaluation_snapshot_path
        adapter_type = CompoundEvaluationSnapshotAdapter
        missing_code = "COMPOUND_EVALUATION_NOT_CONFIGURED"
        missing_message = "化合物评价 v1 数据尚未配置。"
    if not snapshot_path:
        if module == "virtual_intervention" and baseline_candidates:
            symbols = [item.symbol for item in baseline_candidates]
            unavailable = [item.symbol for item in requested if item not in baseline_candidates]
            return {**base, "compatible": True, "computable_count": len(symbols), "unavailable_count": len(unavailable),
                    "computable_symbols": symbols, "unavailable_symbols": unavailable,
                    "analysis_tier": "evidence_baseline", "disease_specific_count": 0,
                    "baseline_count": len(symbols), "reason_code": None,
                    "message": "可运行公开证据方向推测；系统会给出优先抑制/激活、双边支持分和验证建议，但不直接判断药效。"}
        if module == "virtual_cell" and baseline_candidates:
            symbols = [item.symbol for item in baseline_candidates]
            unavailable = [item.symbol for item in requested if item not in baseline_candidates]
            return {**base, "compatible": True, "computable_count": len(symbols), "unavailable_count": len(unavailable),
                    "computable_symbols": symbols, "unavailable_symbols": unavailable,
                    "analysis_tier": "context_prior_baseline", "coverage_scope": "cross_disease_context_prior",
                    "disease_specific_count": 0, "baseline_count": len(symbols), "reason_code": None,
                    "message": "该疾病尚无专属单细胞快照，将运行细胞情境优先级基线：给出优先验证的细胞类型、方向、分数区间、依据和实验建议。"}
        if module == "compound_matching" and baseline_candidates:
            symbols = [item.symbol for item in baseline_candidates[:20]]
            unavailable = [item.symbol for item in requested if item.symbol not in symbols]
            return {**base, "compatible": True, "computable_count": len(symbols), "unavailable_count": len(unavailable),
                    "computable_symbols": symbols, "unavailable_symbols": unavailable,
                    "analysis_tier": "live_database_lookup", "coverage_scope": "gene_and_molecule_level_global",
                    "snapshot_covered_count": 0, "live_lookup_count": len(symbols), "reason_code": None,
                    "message": f"将按成药准备度优先检索 {len(symbols)} 个靶点的 ChEMBL 人源单蛋白活性；无命中的靶点会明确保留为‘未找到’，不会把空结果记为任务失败。"}
        return {**base, "compatible": False, "computable_count": 0, "unavailable_count": len(requested),
                "computable_symbols": [], "unavailable_symbols": [item.symbol for item in requested],
                "reason_code": missing_code, "message": missing_message}
    adapter = adapter_type(snapshot_path)
    if module == "gene_knockout" and baseline_candidates:
        # DepMap Chronos is a cross-disease cell-fitness resource.  Disease
        # snapshots package subsets for release convenience, but eligibility is
        # determined by gene coverage across the quality-passed union.
        pooled_module = adapter.pooled_gene_knockout_module()
        available = {
            str(item.get("symbol") or "").upper()
            for item in pooled_module["results"]
            if bool(item.get("screened"))
        }
        computable = [item.symbol for item in baseline_candidates if item.symbol.upper() in available]
        unavailable = [item.symbol for item in requested if item.symbol.upper() not in available]
        benchmark = pooled_module.get("benchmark") or {}
        return {
            **base,
            "compatible": bool(computable),
            "computable_count": len(computable),
            "unavailable_count": len(unavailable),
            "computable_symbols": computable,
            "unavailable_symbols": unavailable,
            "analysis_tier": "gene_level_global",
            "coverage_scope": "gene_level_global",
            "pooled_snapshot_count": benchmark.get("pooled_snapshot_count"),
            "pooled_screened_gene_count": benchmark.get("pooled_screened_gene_count"),
            "reason_code": None if computable else "TARGETS_NOT_COVERED",
            "message": (
                f"DepMap 按基因而不是按疾病覆盖：已选 {len(requested)} 个靶点中，{len(computable)} 个有真实 CRISPR 数据，"
                f"{len(unavailable)} 个当前版本未覆盖。可直接运行已覆盖部分。"
            ),
        }
    if module == "compound_matching" and baseline_candidates:
        available = adapter.pooled_available_symbols()
        snapshot_symbols = [item.symbol for item in baseline_candidates if item.symbol.upper() in available]
        live_symbols = [item.symbol for item in baseline_candidates if item.symbol.upper() not in available][:20]
        searchable = snapshot_symbols + live_symbols
        unavailable = [item.symbol for item in requested if item.symbol not in searchable]
        return {
            **base,
            "compatible": bool(searchable),
            "computable_count": len(searchable),
            "unavailable_count": len(unavailable),
            "computable_symbols": searchable,
            "unavailable_symbols": unavailable,
            "analysis_tier": "snapshot_plus_live_lookup",
            "coverage_scope": "gene_and_molecule_level_global",
            "snapshot_covered_count": len(snapshot_symbols),
            "live_lookup_count": len(live_symbols),
            "reason_code": None if searchable else "TARGETS_REQUIRED",
            "message": (
                f"ChEMBL 活性按靶点基因匹配，不受疾病快照限制：{len(snapshot_symbols)} 个靶点已有锁定版本数据，"
                f"{len(live_symbols)} 个将进入有边界的在线检索。每个化合物都会展示活性、重复实验、结构、机制方向和适用边界。"
            ),
        }
    try:
        adapter.validate_disease(run.disease_id or run.canonical_name or run.query)
    except OpenTargetsError:
        if module == "virtual_intervention" and baseline_candidates:
            symbols = [item.symbol for item in baseline_candidates]
            unavailable = [item.symbol for item in requested if item not in baseline_candidates]
            return {**base, "compatible": True, "computable_count": len(symbols), "unavailable_count": len(unavailable),
                    "computable_symbols": symbols, "unavailable_symbols": unavailable,
                    "analysis_tier": "evidence_baseline", "disease_specific_count": 0,
                    "baseline_count": len(symbols), "reason_code": None,
                    "message": "该疾病尚无专属单细胞扰动快照，将运行公开证据方向推测。系统会尽量给出优先抑制/激活，并标明推测强度和需要补充的实验。"}
        if module == "virtual_cell" and baseline_candidates:
            symbols = [item.symbol for item in baseline_candidates]
            unavailable = [item.symbol for item in requested if item not in baseline_candidates]
            return {**base, "compatible": True, "computable_count": len(symbols), "unavailable_count": len(unavailable),
                    "computable_symbols": symbols, "unavailable_symbols": unavailable,
                    "analysis_tier": "context_prior_baseline", "coverage_scope": "cross_disease_context_prior",
                    "disease_specific_count": 0, "baseline_count": len(symbols), "reason_code": None,
                    "message": "该疾病尚无专属单细胞快照，将运行透明的细胞情境优先级基线。结果会给出优先细胞类型、方向、依据、宽不确定范围和建议实验，并明确不等同于单细胞实测药效。"}
        disease = adapter.payload.get("disease") or {}
        supported = disease.get("name_zh") or disease.get("name") or disease.get("id") or "已验证疾病"
        return {**base, "compatible": False, "computable_count": 0, "unavailable_count": len(requested),
                "computable_symbols": [], "unavailable_symbols": [item.symbol for item in requested],
                "reason_code": "UPSTREAM_DISEASE_MISMATCH",
                "message": f"当前靶点来自“{base['upstream_disease_name']}”，本模块 v1 仅验证了“{supported}”。请切换到对应靶点结果后再运行。"}
    if module in {"virtual_intervention", "gene_knockout"}:
        available = {
            str(item.get("symbol") or "").upper()
            for item in adapter.module(module)["results"]
            if module != "gene_knockout" or bool(item.get("screened"))
        }
    elif module == "virtual_cell":
        available = {str(item.get("symbol") or "").upper() for item in adapter.results()}
    else:
        available = adapter.available_symbols()
    computable = [item.symbol for item in requested if item.symbol.upper() in available]
    unavailable = [item.symbol for item in requested if item.symbol.upper() not in available]
    if module == "virtual_intervention":
        # Targets missing from the disease-specific snapshot remain computable
        # through the clearly labelled evidence baseline.
        baseline_symbols = [item.symbol for item in baseline_candidates if item.symbol.upper() not in available]
        all_symbols = computable + baseline_symbols
        truly_unavailable = [item.symbol for item in requested if item.symbol not in all_symbols]
        return {**base, "compatible": bool(all_symbols), "computable_count": len(all_symbols),
                "unavailable_count": len(truly_unavailable), "computable_symbols": all_symbols,
                "unavailable_symbols": truly_unavailable, "analysis_tier": "mixed" if baseline_symbols else "disease_specific",
                "disease_specific_count": len(computable), "baseline_count": len(baseline_symbols),
                "reason_code": None if all_symbols else "TARGETS_REQUIRED",
                "message": f"{len(computable)} 个靶点使用疾病专属模型；{len(baseline_symbols)} 个使用公开证据基线。基线结果只用于安排验证顺序。"}
    return {**base, "compatible": bool(computable), "computable_count": len(computable),
            "unavailable_count": len(unavailable), "computable_symbols": computable,
            "unavailable_symbols": unavailable, "reason_code": None if computable else "TARGETS_NOT_COVERED",
            "message": f"可计算 {len(computable)} 个靶点；{len(unavailable)} 个暂未被当前快照覆盖。"}


@router.get("/projects/{project_id}/perturbations")
def list_perturbations(
    project_id: str,
    module: str | None = Query(default=None),
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    require_project_access(db, project_id, user)
    query = select(PerturbationResult).where(PerturbationResult.project_id == project_id)
    if module is not None:
        job_type = {"virtual_intervention": "perturbation", "gene_knockout": "gene_knockout", "virtual_cell": "virtual_cell"}.get(module)
        if not job_type:
            raise HTTPException(status_code=422, detail="module must be virtual_intervention, gene_knockout or virtual_cell")
        latest_job_id = db.scalar(
            select(Job.id)
            .where(Job.project_id == project_id, Job.type == job_type, Job.status == JobStatus.completed.value)
            .order_by(Job.completed_at.desc(), Job.created_at.desc())
            .limit(1)
        )
        if not latest_job_id:
            return []
        query = query.where(PerturbationResult.job_id == latest_job_id)
    return [as_dict(item) for item in db.scalars(query.order_by(PerturbationResult.reversal_score.desc())).all()]


@router.get("/projects/{project_id}/compounds")
def list_compounds(project_id: str, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    require_project_access(db, project_id, user)
    compounds = db.scalars(select(Compound).where(Compound.project_id == project_id).order_by(Compound.match_score.desc())).all()
    output = []
    for compound in compounds:
        value = as_dict(compound)
        evaluation = db.scalar(select(CompoundEvaluation).where(CompoundEvaluation.compound_id == compound.id).order_by(CompoundEvaluation.id.desc()))
        value["evaluation"] = as_dict(evaluation) if evaluation else None
        output.append(value)
    return output


@router.get("/projects/{project_id}/qsar")
def project_qsar(project_id: str, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    require_project_access(db, project_id, user)
    from .qsar import project_results
    compounds = db.scalars(select(Compound).where(Compound.project_id == project_id)).all()
    return project_results(compounds)


@router.get("/projects/{project_id}/candidates")
def list_candidates(project_id: str, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    require_project_access(db, project_id, user)
    rankings = db.scalars(select(Ranking).where(Ranking.project_id == project_id).order_by(Ranking.rank)).all()
    output = []
    for ranking in rankings:
        value = as_dict(ranking)
        compound = db.get(Compound, ranking.compound_id)
        if compound:
            compound_value = as_dict(compound)
            evaluation = db.scalar(
                select(CompoundEvaluation)
                .where(CompoundEvaluation.compound_id == compound.id)
                .order_by(CompoundEvaluation.id.desc())
            )
            compound_value["evaluation"] = as_dict(evaluation) if evaluation else None
            value["compound"] = compound_value
        else:
            value["compound"] = None
        output.append(value)
    return output


@router.patch("/rankings/{ranking_id}")
def update_ranking(ranking_id: str, payload: RankingUpdate, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    ranking = db.get(Ranking, ranking_id)
    if not ranking:
        raise HTTPException(status_code=404, detail="Ranking not found")
    require_project_access(db, ranking.project_id, user, "member")
    ranking.decision = payload.decision
    ranking.rationale = payload.rationale
    audit(db, user, "ranking.decided", "ranking", ranking.id, ranking.project_id, payload.model_dump())
    db.commit()
    db.refresh(ranking)
    return as_dict(ranking)


def build_report_html(db: Session, project: Project, title: str) -> str:
    def latest_job(job_type: str):
        return db.scalar(
            select(Job)
            .where(Job.project_id == project.id, Job.type == job_type, Job.status == JobStatus.completed.value)
            .order_by(Job.completed_at.desc(), Job.created_at.desc())
            .limit(1)
        )

    def esc(value) -> str:
        return html.escape(str(value if value is not None else "—"))

    def decimal(value, digits=3) -> str:
        return "—" if value is None else f"{float(value):.{digits}f}"

    stage_labels = {
        "target_discovery": "靶点发现",
        "perturbation": "虚拟干预",
        "gene_knockout": "基因敲除",
        "virtual_cell": "虚拟细胞",
        "compound_matching": "化合物匹配",
        "admet": "ADMET 评价",
        "ranking": "候选排序",
    }
    latest_jobs = {job_type: latest_job(job_type) for job_type in stage_labels}
    target_job = latest_jobs["target_discovery"]
    latest_target_job = target_job.id if target_job else None
    targets = db.scalars(
        select(TargetCandidate)
        .where(TargetCandidate.project_id == project.id, TargetCandidate.job_id == latest_target_job)
        .order_by(TargetCandidate.rank.asc().nullslast(), TargetCandidate.score.desc())
    ).all() if latest_target_job else []

    def module_results(job_type: str):
        job = latest_jobs[job_type]
        if not job:
            return []
        return db.scalars(
            select(PerturbationResult)
            .where(PerturbationResult.project_id == project.id, PerturbationResult.job_id == job.id)
            .order_by(PerturbationResult.reversal_score.desc())
        ).all()

    perturbations = module_results("perturbation")
    knockouts = module_results("gene_knockout")
    virtual_cells = module_results("virtual_cell")
    compounds = db.scalars(
        select(Compound).where(Compound.project_id == project.id).order_by(Compound.match_score.desc())
    ).all()
    rankings = db.scalars(select(Ranking).where(Ranking.project_id == project.id).order_by(Ranking.rank)).all()

    target_rows = "".join(
        f"<tr><td>{item.rank or '—'}</td><td><strong>{esc(item.symbol)}</strong><small>{esc(item.name)}</small></td>"
        f"<td>{decimal(item.score)}</td><td>{decimal(item.causal_confidence)}</td>"
        f"<td>{decimal(item.disease_context)}</td><td>{decimal(item.development_feasibility)}</td>"
        f"<td>{esc(item.source_version)}</td></tr>" for item in targets[:20]
    ) or "<tr><td colspan='7' class='empty'>尚无靶点结果。</td></tr>"

    def perturbation_rows(items, metric_label: str):
        return "".join(
            f"<tr><td><strong>{esc(item.intervention)}</strong><small>{esc(item.cell_context)}</small></td>"
            f"<td>{decimal(item.reversal_score)}</td><td>{decimal(item.confidence)}</td>"
            f"<td>{esc((item.result or {}).get('evidence_grade') or (item.result or {}).get('best_cell_type') or metric_label)}</td></tr>"
            for item in items[:20]
        ) or "<tr><td colspan='4' class='empty'>本模块尚无结果。</td></tr>"

    compound_rows = []
    evaluations = {}
    if compounds:
        evaluations = {
            item.compound_id: item for item in db.scalars(
                select(CompoundEvaluation).where(CompoundEvaluation.compound_id.in_([compound.id for compound in compounds]))
            ).all()
        }
    for compound in compounds[:25]:
        evaluation = evaluations.get(compound.id)
        details = compound.details or {}
        matches = details.get("target_matches") or []
        target_symbol = matches[0].get("target_symbol") if matches else "—"
        predictions = (evaluation.admet or {}).get("predictions") if evaluation else {}
        domains = [value.get("domain") for value in (predictions or {}).values() if isinstance(value, dict)]
        domain = "适用域外" if "outside" in domains else "适用域边缘" if "borderline" in domains else "适用域内" if domains else "未评估"
        alerts = (details.get("descriptors") or {}).get("structural_alert_count")
        compound_rows.append(
            f"<tr><td><strong>{esc(compound.name)}</strong><small>{esc(compound.identifier)}</small></td><td>{esc(target_symbol)}</td>"
            f"<td>{decimal(compound.match_score)}</td><td>{decimal(evaluation.efficacy if evaluation else None)}</td>"
            f"<td>{decimal(evaluation.safety if evaluation else None)}</td><td>{decimal(evaluation.developability if evaluation else None)}</td>"
            f"<td>{esc(domain)}</td><td>{esc(alerts)}</td></tr>"
        )
    compound_rows_html = "".join(compound_rows) or "<tr><td colspan='8' class='empty'>尚无化合物评价。</td></tr>"

    decision_labels = {"pending": "待决策", "selected": "已入选", "excluded": "已排除"}
    ranking_rows = []
    for item in rankings[:30]:
        compound = db.get(Compound, item.compound_id)
        ranking_rows.append(
            f"<tr><td>{item.rank}</td><td><strong>{esc(compound.name if compound else item.compound_id)}</strong>"
            f"<small>{esc(compound.identifier if compound else '')}</small></td><td>{decimal(item.total_score)}</td>"
            f"<td>{esc(decision_labels.get(item.decision, item.decision))}</td><td>{esc(item.rationale)}</td></tr>"
        )
    ranking_rows_html = "".join(ranking_rows) or "<tr><td colspan='5' class='empty'>尚无综合候选排序。</td></tr>"

    selected_count = sum(item.decision == "selected" for item in rankings)
    excluded_count = sum(item.decision == "excluded" for item in rankings)
    evaluated_count = len(evaluations)
    decided_count = selected_count + excluded_count
    decision_gates = [
        ("靶点候选", bool(targets), f"{len(targets)} 个候选", "运行靶点发现并复核成药路线"),
        ("干预与风险", bool(perturbations) and bool(knockouts), f"{len(perturbations)} 条干预 · {len(knockouts)} 条敲除", "补齐干预方向和广泛必需性检查"),
        ("细胞情境", bool(virtual_cells), f"{len(virtual_cells)} 条通过质量门", "运行虚拟细胞或检查质量门"),
        ("化合物起点", bool(compounds), f"{len(compounds)} 个化合物", "运行化合物匹配或更换靶点"),
        ("早期性质", evaluated_count > 0, f"{evaluated_count} 个已评价", "补齐 ADMET 与结构风险检查"),
        ("人工决定", bool(rankings) and decided_count > 0, f"{len(rankings)} 个排序 · {decided_count} 个已决定", "记录入选、暂缓或排除理由"),
    ]
    decision_readiness = round(sum(done for _, done, _, _ in decision_gates) / len(decision_gates) * 100)
    blocker_text = "；".join(f"{label}：{action}" for label, done, _, action in decision_gates if not done) or "系统内阶段门均已完成，可进入线下实验、预算与项目评审。"
    gate_rows = "".join(
        f"<div><b>{esc(label)} · {'已具备' if done else '待处理'}</b><span>{esc(evidence)}</span></div>"
        for label, done, evidence, _ in decision_gates
    )
    real_jobs = sum(job is not None and job.adapter_mode == "real" for job in latest_jobs.values())
    provenance_rows = "".join(
        f"<tr><td>{esc(stage_labels[job_type])}</td><td>{esc(job.adapter_mode if job else '未运行')}</td>"
        f"<td>{esc(job.input_version if job else '—')}</td><td><code>{esc(job.id if job else '—')}</code></td>"
        f"<td>{esc(job.completed_at.isoformat() if job and job.completed_at else '—')}</td></tr>"
        for job_type, job in latest_jobs.items()
    )
    method_rows = "".join(
        f"<tr><td>{esc(item['name'])}</td><td>{esc(item['version'])}</td><td>{esc('、'.join(item['inputs'][:4]))}</td>"
        f"<td>{esc(item['boundary'])}</td></tr>" for item in METHODOLOGY if item["maturity"] != "planned"
    )
    target_strategy = (target_job.parameters or {}).get("discovery_strategy") if target_job else "未运行"
    generated_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    disclaimer = "本报告用于公司内部候选分流、证据复核和实验优先级管理，不构成实验、临床、医疗或投资结论，也不替代预算审批或正式项目立项。综合分不是研发成功概率。"
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
    <title>{esc(title)}</title><style>
    :root{{--ink:#173f37;--green:#087a63;--mint:#e7f5f0;--line:#d8e4df;--muted:#657a73;--amber:#fff5dc}}
    *{{box-sizing:border-box}}body{{margin:0;color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;background:#f4f8f6;line-height:1.55}}
    main{{max-width:1180px;margin:0 auto;padding:42px 24px 72px}}header.hero{{padding:32px;border-radius:22px;color:white;background:linear-gradient(135deg,#075847,#09826a)}}
    .eyebrow{{font-size:12px;font-weight:800;letter-spacing:.14em}}h1{{margin:10px 0 8px;font-size:34px}}.hero p{{margin:4px 0;color:#d7eee8}}.notice{{margin:18px 0;padding:14px 16px;border:1px solid #ead39e;border-radius:12px;background:var(--amber)}}
    .metrics{{display:grid;grid-template-columns:repeat(6,1fr);gap:10px;margin:18px 0}}.metric{{padding:16px;border:1px solid var(--line);border-radius:13px;background:white}}.metric small,.muted,td small{{display:block;color:var(--muted);font-size:11px}}.metric strong{{display:block;margin-top:5px;font-size:25px}}
    section{{margin-top:20px;padding:24px;border:1px solid var(--line);border-radius:16px;background:white}}h2{{margin:0 0 6px;font-size:21px}}section>p{{margin:0 0 16px;color:var(--muted);font-size:13px}}.table-wrap{{overflow:auto}}table{{width:100%;border-collapse:collapse;font-size:12px}}th{{padding:10px;text-align:left;color:var(--muted);background:#f5f8f6}}td{{padding:11px 10px;border-top:1px solid var(--line);vertical-align:top}}td small{{margin-top:3px}}code{{font-size:10px}}.empty{{padding:25px;text-align:center;color:var(--muted)}}
    .two{{display:grid;grid-template-columns:1fr 1fr;gap:20px}}.facts{{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}}.facts div{{padding:12px;background:#f6faf8;border-radius:10px}}.facts b,.facts span{{display:block}}.facts span{{color:var(--muted);font-size:12px}}footer{{margin-top:22px;color:var(--muted);font-size:11px;text-align:center}}
    @media(max-width:850px){{.metrics{{grid-template-columns:repeat(2,1fr)}}.two{{grid-template-columns:1fr}}.facts{{grid-template-columns:1fr}}main{{padding:20px 12px}}}}
    @media print{{body{{background:white}}main{{max-width:none;padding:0}}section{{break-inside:avoid}}.hero{{print-color-adjust:exact}}}}
    </style></head><body><main>
    <header class='hero'><span class='eyebrow'>AI FOR SCIENCE · 企业研发决策快照</span><h1>{esc(title)}</h1><p>{esc(project.name)} · 疾病：{esc(project.disease)}</p><p>项目目标：{esc(project.objective)}</p><p>生成时间：{esc(generated_at)}</p></header>
    <div class='notice'><strong>使用边界：</strong>{esc(disclaimer)}</div>
    <div class='metrics'><div class='metric'><small>靶点候选</small><strong>{len(targets)}</strong></div><div class='metric'><small>虚拟干预</small><strong>{len(perturbations)}</strong></div><div class='metric'><small>基因敲除</small><strong>{len(knockouts)}</strong></div><div class='metric'><small>虚拟细胞</small><strong>{len(virtual_cells)}</strong></div><div class='metric'><small>已评价化合物</small><strong>{evaluated_count}</strong></div><div class='metric'><small>人工入选</small><strong>{selected_count}</strong></div></div>
    <section><h2>一、项目推进与决策摘要</h2><p>该快照汇总生成时各模块的最近完成结果；旧版本不会随之后的重跑改变。</p><div class='facts'><div><b>决策就绪度</b><span>{decision_readiness}% · 六个阶段门</span></div><div><b>候选决策</b><span>入选 {selected_count} · 排除 {excluded_count} · 待决策 {max(0,len(rankings)-selected_count-excluded_count)}</span></div><div><b>靶点策略</b><span>{esc(target_strategy)}</span></div><div><b>真实数据任务</b><span>{real_jobs} / {len(stage_labels)} 个最近任务</span></div>{gate_rows}</div><div class='notice'><strong>当前阻塞项：</strong>{esc(blocker_text)}</div></section>
    <section><h2>二、靶点发现</h2><p>数据库原始关联分与平台派生分分开保存；下表最多展示最近一次运行的前 20 个候选。</p><div class='table-wrap'><table><thead><tr><th>名次</th><th>靶点</th><th>来源/综合分</th><th>因果证据</th><th>疾病情境</th><th>开发可行性</th><th>数据版本</th></tr></thead><tbody>{target_rows}</tbody></table></div></section>
    <div class='two'><section><h2>三、虚拟干预</h2><p>模块衰减潜力与置信度为连续计算值。</p><div class='table-wrap'><table><thead><tr><th>干预</th><th>衰减潜力</th><th>置信度</th><th>证据/情境</th></tr></thead><tbody>{perturbation_rows(perturbations,'方向性假设')}</tbody></table></div></section><section><h2>四、基因敲除</h2><p>DepMap 癌细胞系适应度仅作广泛必需性风险代理。</p><div class='table-wrap'><table><thead><tr><th>干预</th><th>敲除耐受</th><th>数据置信</th><th>证据/情境</th></tr></thead><tbody>{perturbation_rows(knockouts,'DepMap 实测')}</tbody></table></div></section></div>
    <section><h2>五、虚拟细胞</h2><p>只纳入通过留一供体状态轴质量门的细胞情境；投影不是临床疗效预测。</p><div class='table-wrap'><table><thead><tr><th>反事实干预</th><th>投影逆转</th><th>综合置信度</th><th>最佳情境</th></tr></thead><tbody>{perturbation_rows(virtual_cells,'反事实投影')}</tbody></table></div></section>
    <section><h2>六、化合物匹配与 ADMET</h2><p>ChEMBL 实验活性、RDKit 结构规则和 TDC 模型预测分开计算；下表最多展示 25 个候选。</p><div class='table-wrap'><table><thead><tr><th>化合物</th><th>靶点</th><th>匹配</th><th>药效</th><th>安全</th><th>可开发</th><th>适用域</th><th>结构警示</th></tr></thead><tbody>{compound_rows_html}</tbody></table></div></section>
    <section><h2>七、综合候选排序与人工决策（候选组合 v2）</h2><p>透明加权综合分用于项目内相对比较，不是成功概率；人工决定和理由单独保存。下表最多展示前 30 名。</p><div class='table-wrap'><table><thead><tr><th>名次</th><th>化合物</th><th>综合分</th><th>人工决策</th><th>决策理由</th></tr></thead><tbody>{ranking_rows_html}</tbody></table></div></section>
    <section><h2>八、方法与适用边界</h2><p>页面展示的数据库、算法版本、主要输入和不能外推的边界。</p><div class='table-wrap'><table><thead><tr><th>模块</th><th>版本</th><th>主要数据库/输入</th><th>边界</th></tr></thead><tbody>{method_rows}</tbody></table></div></section>
    <section><h2>九、任务与版本追溯</h2><p>每一行指向该模块在报告生成时最近一次完成任务，可用于回查参数、事件、模型运行和输入版本。</p><div class='table-wrap'><table><thead><tr><th>模块</th><th>数据模式</th><th>输入版本</th><th>任务 ID</th><th>完成时间</th></tr></thead><tbody>{provenance_rows}</tbody></table></div></section>
    <footer>AI for Science · 报告为不可变项目快照 · Project ID {esc(project.id)}</footer></main></body></html>"""


@router.post("/projects/{project_id}/reports", status_code=201)
def create_report(project_id: str, payload: ReportCreate, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    project = require_project_access(db, project_id, user, "member")
    version = (db.scalar(select(func.max(Report.version)).where(Report.project_id == project_id)) or 0) + 1
    title = payload.title or f"{project.name} — AI for Science 决策报告"
    report = Report(project_id=project_id, version=version, title=title, html=build_report_html(db, project, title), created_by=user.id)
    db.add(report)
    db.flush()
    audit(db, user, "report.created", "report", report.id, project_id, {"version": version})
    db.commit()
    db.refresh(report)
    return as_dict(report)


@router.get("/projects/{project_id}/reports")
def list_reports(project_id: str, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    require_project_access(db, project_id, user)
    return [as_dict(item) for item in db.scalars(select(Report).where(Report.project_id == project_id).order_by(Report.version.desc())).all()]


@router.get("/reports/{report_id}", response_class=HTMLResponse)
def view_report(report_id: str, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    report = db.get(Report, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    require_project_access(db, report.project_id, user)
    return HTMLResponse(report.html)


@router.get("/reports/{report_id}/pdf")
def report_pdf(report_id: str, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    report = db.get(Report, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    project = require_project_access(db, report.project_id, user)
    target_job = db.scalar(
        select(Job)
        .where(Job.project_id == project.id, Job.type == "target_discovery", Job.status == JobStatus.completed.value)
        .order_by(Job.completed_at.desc(), Job.created_at.desc())
        .limit(1)
    )
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    pdf.setTitle(report.title)
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(54, 790, report.title[:70])
    pdf.setFont("Helvetica", 10)
    lines = [
        f"Project: {project.name}",
        f"Disease: {project.disease}",
        f"Version: {report.version}",
        (
            "Target discovery: versioned Open Targets public data; downstream stages may remain demo."
            if target_job and target_job.adapter_mode == "real"
            else "Demonstration workflow output only; not a scientific or clinical conclusion."
        ),
        "Detailed structured results and provenance are available through the API.",
    ]
    for index, line in enumerate(lines):
        pdf.drawString(54, 755 - index * 20, line[:100])
    pdf.save()
    return Response(buffer.getvalue(), media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="report-{report.id}.pdf"'})


@router.get("/projects/{project_id}/audit")
def list_audit(project_id: str, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    require_project_access(db, project_id, user, "owner")
    return [as_dict(item) for item in db.scalars(select(AuditEvent).where(AuditEvent.project_id == project_id).order_by(AuditEvent.created_at.desc()).limit(200)).all()]


@router.post("/projects/{project_id}/activity")
def record_project_activity(project_id: str, payload: ProjectActivityRequest | None = None,
                            db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    """工作台打开项目 / 切换模块时留痕；10 分钟内同一页面重复打开只记一次。"""
    project = require_project_access(db, project_id, user, "viewer")
    view = ((payload.view if payload else "overview") or "overview").strip()[:80] or "overview"
    action = "project.entered" if view == "overview" else "project.module_viewed"
    entity_id = project.id if action == "project.entered" else f"{project.id}:{view}"
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
    existing = db.scalar(
        select(AuditEvent.id).where(
            AuditEvent.actor_id == user.id,
            AuditEvent.project_id == project.id,
            AuditEvent.action == action,
            AuditEvent.entity_id == entity_id,
            AuditEvent.created_at >= cutoff,
        ).limit(1)
    )
    if existing:
        return {"recorded": False, "action": action, "view": view}
    db.add(AuditEvent(project_id=project.id, actor_id=user.id, action=action,
                      entity_type="project_activity", entity_id=entity_id,
                      details={"view": view, "project_name": project.name}))
    db.commit()
    return {"recorded": True, "action": action, "view": view}
