from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ProjectCreate(BaseModel):
    name: str = Field(min_length=2, max_length=180)
    disease: str = Field(min_length=2, max_length=180)
    disease_id: str | None = Field(default=None, max_length=80)
    objective: str = Field(default="", max_length=3000)
    project_type: Literal["research", "validation"] = "research"
    research_track: Literal["full", "target_discovery", "novel_discovery", "compound_prioritization"] = "target_discovery"


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=180)
    disease: str | None = Field(default=None, min_length=2, max_length=180)
    disease_id: str | None = Field(default=None, max_length=80)
    objective: str | None = Field(default=None, max_length=3000)
    status: Literal["active", "archived"] | None = None
    research_track: Literal["full", "target_discovery", "novel_discovery", "compound_prioritization"] | None = None


class ProjectOut(ORMModel):
    id: str
    name: str
    disease: str
    disease_id: str | None
    objective: str
    status: str
    project_type: str
    research_track: str
    owner_id: str
    created_at: datetime
    updated_at: datetime


class MemberCreate(BaseModel):
    user_id: str = Field(min_length=2, max_length=120)
    role: Literal["owner", "member", "viewer"]


class MemberOut(ORMModel):
    id: str
    project_id: str
    user_id: str
    role: str
    created_at: datetime


class DatasetCreate(BaseModel):
    name: str
    source: str
    license: str = "unspecified"
    asset_ids: list[str] = Field(default_factory=list)
    demo: bool = False


class AnalysisRequest(BaseModel):
    adapter_mode: Literal["not_configured", "precomputed", "real", "runtime"] = "precomputed"
    input_version: str = "v1"
    parameters: dict[str, Any] = Field(default_factory=dict)


class JobAccepted(BaseModel):
    job_id: str
    status: str
    status_url: str
    cache_hit: bool = False
    reused_from_job_id: str | None = None


class JobOut(ORMModel):
    id: str
    project_id: str
    type: str
    status: str
    stage: str
    progress: int
    adapter_mode: str
    parameters: dict[str, Any]
    input_version: str
    output_ref: str | None
    result_status: str | None
    error_code: str | None
    error_message: str | None
    retry_of: str | None
    requested_by: str
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class TargetUpdate(BaseModel):
    selected: bool | None = None
    note: str | None = Field(default=None, max_length=3000)


class TargetSelectionUpdate(BaseModel):
    job_id: str = Field(min_length=36, max_length=36)
    mode: Literal["top_n", "all", "clear"]
    top_n: int = Field(default=10, ge=1, le=200)


class RankingUpdate(BaseModel):
    decision: Literal["pending", "selected", "excluded"]
    rationale: str = Field(default="", max_length=3000)


class ReportCreate(BaseModel):
    title: str | None = Field(default=None, max_length=255)
