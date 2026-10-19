"""Precompute established target discovery for every disease in the catalogue.

The script is idempotent: a disease is skipped when any active project already
has a completed established target-discovery job with at least one candidate.
System-created cache projects are hidden from the ordinary project list but are
included in the disease cache index.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from sqlalchemy import func, select

from app.database import SessionLocal
from app.jobs import execute_job
from app.models import Job, JobEvent, JobStatus, Project, ProjectMember, TargetCandidate


SYSTEM_CACHE_OWNER = "system-disease-cache"
REQUESTED_BY = "system-disease-target-prewarm"


def catalogue(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("diseases") or []
    if len(rows) != 201 or any(not row.get("id") or not row.get("name_zh") for row in rows):
        raise RuntimeError(f"Unexpected disease catalogue: {len(rows)} rows")
    return rows


def completed_target_job(db, disease_id: str):
    rows = db.execute(
        select(Job, func.count(TargetCandidate.id))
        .join(Project, Project.id == Job.project_id)
        .outerjoin(TargetCandidate, TargetCandidate.job_id == Job.id)
        .where(
            Project.disease_id == disease_id,
            Project.status == "active",
            Job.type == "target_discovery",
            Job.status == JobStatus.completed.value,
        )
        .group_by(Job.id)
        .order_by(Job.completed_at.desc(), Job.created_at.desc())
    ).all()
    for job, count in rows:
        strategy = str((job.parameters or {}).get("discovery_strategy") or "established")
        if strategy == "established" and int(count) > 0:
            return job, int(count)
    return None


def cache_project(db, row: dict) -> Project:
    project = db.scalar(
        select(Project).where(
            Project.disease_id == row["id"],
            Project.owner_id == SYSTEM_CACHE_OWNER,
            Project.status == "active",
        ).order_by(Project.updated_at.desc())
    )
    if project:
        return project
    project = Project(
        name=f"{row['name_zh']}靶点数据库",
        disease=row["name_zh"],
        disease_id=row["id"],
        objective="全疾病目录靶点缓存：使用版本化公共证据预计算，供搜索后直接加载。",
        status="active",
        project_type="research",
        research_track="target_discovery",
        owner_id=SYSTEM_CACHE_OWNER,
    )
    db.add(project)
    db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=SYSTEM_CACHE_OWNER, role="owner"))
    db.commit()
    return project


def create_job(db, project: Project, row: dict) -> Job:
    job = Job(
        project_id=project.id,
        type="target_discovery",
        adapter_mode="real",
        parameters={
            "disease_query": row["id"],
            "top_n": 100,
            "include_indirect": True,
            # Aggregated datatype evidence is complete for the target cards and
            # avoids downloading thousands of individual literature records.
            "include_evidence_records": False,
            "discovery_strategy": "established",
        },
        input_version="open-targets-live",
        requested_by=REQUESTED_BY,
    )
    db.add(job)
    db.flush()
    db.add(JobEvent(job_id=job.id, status=JobStatus.queued.value, stage="queued", progress=0, message="Catalogue target cache queued"))
    db.commit()
    return job


def run_one(row: dict, retries: int) -> dict:
    with SessionLocal() as db:
        existing = completed_target_job(db, row["id"])
        if existing:
            job, count = existing
            return {"status": "existing", "job_id": job.id, "targets": count}
        project = cache_project(db, row)

    last = None
    for attempt in range(1, retries + 1):
        with SessionLocal() as db:
            project = db.scalar(select(Project).where(Project.disease_id == row["id"], Project.owner_id == SYSTEM_CACHE_OWNER))
            job = create_job(db, project, row)
            job_id = job.id
        started = time.monotonic()
        execute_job(job_id)
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            count = int(db.scalar(select(func.count(TargetCandidate.id)).where(TargetCandidate.job_id == job_id)) or 0)
            last = {
                "status": job.status,
                "job_id": job_id,
                "targets": count,
                "error_code": job.error_code,
                "error_message": job.error_message,
                "elapsed_seconds": round(time.monotonic() - started, 2),
                "attempt": attempt,
            }
        if last["status"] == JobStatus.completed.value and count > 0:
            return last
        time.sleep(min(2 ** attempt, 8))
    return last or {"status": "failed", "error_code": "NO_ATTEMPT"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalogue", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--retries", type=int, default=3)
    args = parser.parse_args()
    rows = catalogue(args.catalogue)
    state = {"total": len(rows), "processed": 0, "completed": 0, "existing": 0, "failed": 0, "results": {}}
    for index, row in enumerate(rows, 1):
        outcome = run_one(row, max(1, args.retries))
        state["processed"] = index
        state["results"][row["id"]] = {"name_zh": row["name_zh"], **outcome}
        state["completed"] = sum(item["status"] == "completed" for item in state["results"].values())
        state["existing"] = sum(item["status"] == "existing" for item in state["results"].values())
        state["failed"] = sum(item["status"] not in {"completed", "existing"} for item in state["results"].values())
        args.state.parent.mkdir(parents=True, exist_ok=True)
        args.state.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"index": index, "total": len(rows), "id": row["id"], "name": row["name_zh"], **outcome}, ensure_ascii=False), flush=True)
    return 0 if state["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
