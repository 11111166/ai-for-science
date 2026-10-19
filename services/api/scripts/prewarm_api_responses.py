#!/usr/bin/env python3
"""Prewarm persistent API responses from the current production dataset."""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from typing import Any
from urllib.parse import urlencode

os.environ["RECOVER_INTERRUPTED_JOBS"] = "false"

from fastapi.testclient import TestClient
from app.main import app
from app.persistent_cache import invalidate_response_cache

BASE = "/api/v1/drug-discovery"
HEADERS = {"x-dev-user": "cache-prewarm", "x-dev-role": "admin", "accept": "application/json"}
results: list[dict[str, Any]] = []


def fetch(client: TestClient, path: str, *, required: bool = False) -> Any:
    response = client.get(BASE + path, headers=HEADERS)
    results.append({"path": path, "status": response.status_code, "cache": response.headers.get("x-ai4science-cache", "")})
    if required and response.status_code != 200:
        raise RuntimeError(f"required prewarm request failed: {path} -> {response.status_code}")
    if response.status_code != 200 or "application/json" not in response.headers.get("content-type", ""):
        return None
    try:
        return response.json()
    except Exception:
        return None


def item_id(value: Any, *keys: str) -> str | None:
    if not isinstance(value, dict):
        return None
    for key in keys:
        raw = value.get(key)
        if raw is not None and str(raw):
            return str(raw)
    return None


def rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    if isinstance(value, dict):
        for key in ("items", "results", "jobs", "projects", "data"):
            candidate = value.get(key)
            if isinstance(candidate, list):
                return [x for x in candidate if isinstance(x, dict)]
    return []


def main() -> int:
    invalidate_response_cache()
    with TestClient(app) as client:
        for path in (
            "/methodology",
            "/data-sources",
            "/disease-cache-index",
            "/omics/examples",
            "/fungal-discovery/organisms",
            "/fungal-compounds/targets",
            "/fungal-targets/data-status",
            "/fungal-targets/examples/sc5314",
            "/bacterial-discovery/organisms",
            "/bacterial-compounds/targets",
        ):
            fetch(client, path, required=path in {"/methodology", "/data-sources"})
        projects = rows(fetch(client, "/projects", required=True))
        for project in projects:
            project_id = item_id(project, "id", "project_id")
            if not project_id:
                continue
            root = f"/projects/{project_id}"
            # Project/job/result endpoints intentionally bypass PostgreSQL and
            # are read from the authoritative SQLite store after access checks.
            fetch(client, root)
            disease_query = project.get("disease_id") or project.get("disease")
            if disease_query:
                fetch(client, "/target-discovery-availability?" + urlencode({"disease_query": disease_query}))
            fetch(client, root + "/members")
            jobs_payload = fetch(client, root + "/jobs")
            for suffix in ("/assets", "/datasets", "/compounds", "/qsar", "/candidates", "/reports", "/audit", "/omics/results"):
                fetch(client, root + suffix)
            for module in ("virtual_intervention", "gene_knockout", "virtual_cell"):
                fetch(client, root + "/perturbations?" + urlencode({"module": module}))
            for suffix in ("/process/knowledge/summary", "/process/knowledge/materials", "/process/knowledge/reaction-templates", "/process/knowledge/principles", "/process/knowledge/topics", "/process/demo/cases", "/process/studies", "/process/scale-up/assessments", "/process/pfd/diagrams", "/process/portfolios", "/workbench/molecules", "/workbench/tasks", "/workbench/task-results-template"):
                fetch(client, root + suffix)
            completed = [job for job in rows(jobs_payload) if str(job.get("status", "")).lower() == "completed"]
            target_jobs = [job for job in completed if "target" in str(job.get("job_type") or job.get("type") or job.get("module") or "").lower() and "fungal" not in str(job.get("job_type") or job.get("type") or job.get("module") or "").lower()]
            if target_jobs:
                job_id = item_id(target_jobs[-1], "id", "job_id")
                if job_id:
                    fetch(client, root + "/targets?" + urlencode({"job_id": job_id, "limit": 200}))
                    fetch(client, f"/jobs/{job_id}/target-discovery-result")
                    for module in ("virtual_intervention", "gene_knockout", "virtual_cell", "compound_matching"):
                        fetch(client, root + "/target-compatibility?" + urlencode({"target_job_id": job_id, "module": module, "top_n": 10}))
    statuses = Counter(item["status"] for item in results)
    caches = Counter(item["cache"] for item in results)
    summary = {"requests": len(results), "statuses": dict(sorted(statuses.items())), "cache_headers": dict(sorted(caches.items())), "projects": len(projects), "failed_paths": [item for item in results if item["status"] >= 500], "not_found_count": sum(1 for item in results if item["status"] == 404)}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if summary["failed_paths"] else 0


if __name__ == "__main__":
    sys.exit(main())
