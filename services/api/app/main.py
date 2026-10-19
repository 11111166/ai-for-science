from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import get_settings
from .auth import UserContext, get_current_user
from .cache_middleware import PersistentResponseCacheMiddleware
from .persistent_cache import cache_health, cache_stats, init_cache_schema, purge_expired
from sqlalchemy import select

from .database import Base, SessionLocal, business_store_health, engine
from .models import Job, JobEvent, JobStatus
from .molecule_structure import router as structure_router
from .routes import router
from .auth_routes import router as auth_router
from .molecule_workbench import router as workbench_router
from .process_development import router as process_router
from .hte_demo import router as hte_router
from .process_scaleup import router as scale_up_router
from .order_assessment import router as order_router
from .process_knowledge import router as process_knowledge_router
from .process_engineering import router as process_engineering_router
from .process_retrosynthesis import router as process_retrosynthesis_router
from .process_portfolio import router as process_portfolio_router
from .process_pfd import router as process_pfd_router
from .process_demo import router as process_demo_router
from .process_intake import router as process_intake_router
from .protein_structure import router as protein_structure_router
from .omics_analysis import router as omics_analysis_router
from .fungal_compound import router as fungal_compound_router
from .bacterial_compound import router as bacterial_compound_router
from .fungal_target import router as fungal_target_router
from .admin_routes import router as admin_router


settings = get_settings()


def recover_interrupted_inline_jobs() -> int:
    """Close jobs whose in-process worker disappeared with a prior API process."""
    if settings.job_execution_mode == "celery":
        return 0
    db = SessionLocal()
    try:
        jobs = db.scalars(select(Job).where(Job.status.in_([JobStatus.queued.value, JobStatus.running.value]))).all()
        completed_at = datetime.now(timezone.utc)
        for job in jobs:
            job.status = JobStatus.failed.value
            job.stage = "interrupted"
            job.error_code = "API_RESTART_INTERRUPTED"
            job.error_message = "服务重启中断了本次计算，请重新运行。"
            job.completed_at = completed_at
            db.add(JobEvent(job_id=job.id, status=JobStatus.failed.value, stage="interrupted", progress=job.progress, message=job.error_message))
        db.commit()
        return len(jobs)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.database_url.startswith("sqlite"):
        Base.metadata.create_all(engine)
    recover_interrupted_inline_jobs()
    init_cache_schema()
    purge_expired()
    yield


app = FastAPI(
    title="AI Drug Discovery API",
    version="0.2.0",
    description="Traceable disease resolution, target discovery and drug-effect workflow API.",
    lifespan=lifespan,
)
app.add_middleware(PersistentResponseCacheMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
app.include_router(structure_router, prefix="/api/v1/drug-discovery")
app.include_router(auth_router)
app.include_router(workbench_router)
app.include_router(process_router)
app.include_router(hte_router)
app.include_router(scale_up_router)
app.include_router(order_router)
app.include_router(process_knowledge_router)
app.include_router(process_engineering_router)
app.include_router(process_retrosynthesis_router)
app.include_router(process_portfolio_router)
app.include_router(process_pfd_router)
app.include_router(process_demo_router)
app.include_router(process_intake_router)
app.include_router(protein_structure_router)
app.include_router(omics_analysis_router)
app.include_router(fungal_target_router)
app.include_router(fungal_compound_router)
app.include_router(bacterial_compound_router)
app.include_router(admin_router)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "drug-discovery-api",
        "environment": settings.app_env,
        "business_store": business_store_health(),
        "persistent_cache": cache_health(),
    }


@app.get("/api/v1/drug-discovery/cache/status")
def persistent_cache_status(user: UserContext = Depends(get_current_user)):
    return {
        "status": "ok",
        "user": user.id,
        "architecture": {
            "primary": business_store_health(),
            "cache": cache_health(),
            "rules": [
                "SQLite is authoritative for projects, jobs, result references and audit events.",
                "PostgreSQL contains rebuildable reference/response caches only.",
                "Project and job endpoints always bypass the shared cache and re-check access.",
                "Cache failure is fail-open and never blocks the primary business workflow.",
            ],
        },
        **cache_stats(),
    }


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"error": {"code": "INTERNAL_ERROR", "message": "Unexpected server error"}, "path": request.url.path})
