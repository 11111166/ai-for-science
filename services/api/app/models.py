import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class AdapterMode(str, enum.Enum):
    not_configured = "not_configured"
    precomputed = "precomputed"
    real = "real"
    # Accepted for compatibility with jobs created before REAL became the
    # user-facing runtime mode. New submissions are normalized to ``real``.
    runtime = "runtime"


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(180))
    disease: Mapped[str] = mapped_column(String(180))
    disease_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    objective: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="active")
    project_type: Mapped[str] = mapped_column(String(30), default="research", index=True)
    research_track: Mapped[str] = mapped_column(String(40), default="target_discovery")
    owner_id: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ProjectMember(Base):
    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(20), default="viewer")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Asset(Base):
    __tablename__ = "assets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(80), default="file")
    storage_uri: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    format: Mapped[str] = mapped_column(String(80), default="unknown")
    version: Mapped[int] = mapped_column(Integer, default=1)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Dataset(Base):
    __tablename__ = "datasets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(180))
    source: Mapped[str] = mapped_column(String(255))
    license: Mapped[str] = mapped_column(String(120), default="unspecified")
    asset_ids: Mapped[list] = mapped_column(JSON, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)
    demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(20), default=JobStatus.queued.value, index=True)
    stage: Mapped[str] = mapped_column(String(100), default="queued")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    adapter_mode: Mapped[str] = mapped_column(String(30), default=AdapterMode.precomputed.value)
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    input_version: Mapped[str] = mapped_column(String(120), default="v1")
    output_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_of: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    requested_by: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class JobEvent(Base):
    __tablename__ = "job_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20))
    stage: Mapped[str] = mapped_column(String(100))
    progress: Mapped[int] = mapped_column(Integer)
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ModelRun(Base):
    __tablename__ = "model_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    adapter: Mapped[str] = mapped_column(String(120))
    adapter_mode: Mapped[str] = mapped_column(String(30))
    model_name: Mapped[str] = mapped_column(String(160))
    model_version: Mapped[str] = mapped_column(String(80))
    input_hash: Mapped[str] = mapped_column(String(64))
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)
    demo: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TargetDiscoveryRun(Base):
    __tablename__ = "target_discovery_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), unique=True, index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    query: Mapped[str] = mapped_column(String(255))
    resolution_status: Mapped[str] = mapped_column(String(40))
    canonical_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    disease_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    ontology: Mapped[str | None] = mapped_column(String(40), nullable=True)
    synonyms: Mapped[list] = mapped_column(JSON, default=list)
    resolution_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    resolution_method: Mapped[str] = mapped_column(String(80), default="open_targets_search")
    source: Mapped[str] = mapped_column(String(120))
    source_version: Mapped[str] = mapped_column(String(80))
    source_api_version: Mapped[str] = mapped_column(String(80))
    evidence_registry_version: Mapped[str] = mapped_column(String(40), default="v1")
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    raw_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    data_quality: Mapped[dict] = mapped_column(JSON, default=dict)
    benchmark: Mapped[dict] = mapped_column(JSON, default=dict)
    benchmark_status: Mapped[str] = mapped_column(String(40), default="not_yet_validated")
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    errors: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ExternalDataCache(Base):
    __tablename__ = "external_data_cache"
    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    source: Mapped[str] = mapped_column(String(120), index=True)
    source_version: Mapped[str] = mapped_column(String(80), index=True)
    api_version: Mapped[str] = mapped_column(String(80))
    operation: Mapped[str] = mapped_column(String(80), index=True)
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    storage_uri: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TargetCandidate(Base):
    __tablename__ = "target_candidates"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    target_discovery_run_id: Mapped[str | None] = mapped_column(ForeignKey("target_discovery_runs.id", ondelete="CASCADE"), nullable=True, index=True)
    external_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(180))
    biotype: Mapped[str | None] = mapped_column(String(80), nullable=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score: Mapped[float] = mapped_column(Float)
    score_origin: Mapped[str] = mapped_column(String(80), default="precomputed_demo")
    derived_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    causal_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    disease_context: Mapped[float | None] = mapped_column(Float, nullable=True)
    development_feasibility: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence_coverage: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evidence_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    data_mode: Mapped[str] = mapped_column(String(30), default=AdapterMode.precomputed.value)
    source: Mapped[str] = mapped_column(String(120), default="precomputed_demo")
    source_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    direction: Mapped[str] = mapped_column(String(40))
    selected: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str] = mapped_column(Text, default="")
    demo: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TargetEvidence(Base):
    __tablename__ = "target_evidence"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    target_id: Mapped[str] = mapped_column(ForeignKey("target_candidates.id", ondelete="CASCADE"), index=True)
    external_evidence_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    disease_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    evidence_type: Mapped[str] = mapped_column(String(80))
    datatype: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    datasource: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    category: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(255))
    summary: Mapped[str] = mapped_column(Text)
    score: Mapped[float] = mapped_column(Float)
    direction_on_target: Mapped[str | None] = mapped_column(String(80), nullable=True)
    direction_on_trait: Mapped[str | None] = mapped_column(String(80), nullable=True)
    evidence_count: Mapped[int] = mapped_column(Integer, default=1)
    source_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_mode: Mapped[str] = mapped_column(String(30), default=AdapterMode.precomputed.value)
    confidence: Mapped[str | None] = mapped_column(String(120), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)


class PerturbationResult(Base):
    __tablename__ = "perturbation_results"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    target_id: Mapped[str | None] = mapped_column(ForeignKey("target_candidates.id"), nullable=True)
    intervention: Mapped[str] = mapped_column(String(80))
    cell_context: Mapped[str] = mapped_column(String(160))
    reversal_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    demo: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Compound(Base):
    __tablename__ = "compounds"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(180))
    identifier: Mapped[str] = mapped_column(String(120))
    mechanism: Mapped[str] = mapped_column(Text, default="")
    match_score: Mapped[float] = mapped_column(Float)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    demo: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CompoundEvaluation(Base):
    __tablename__ = "compound_evaluations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    compound_id: Mapped[str] = mapped_column(ForeignKey("compounds.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    efficacy: Mapped[float | None] = mapped_column(Float, nullable=True)
    safety: Mapped[float | None] = mapped_column(Float, nullable=True)
    developability: Mapped[float | None] = mapped_column(Float, nullable=True)
    admet: Mapped[dict] = mapped_column(JSON, default=dict)
    demo: Mapped[bool] = mapped_column(Boolean, default=True)


class Ranking(Base):
    __tablename__ = "rankings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    compound_id: Mapped[str] = mapped_column(ForeignKey("compounds.id", ondelete="CASCADE"), index=True)
    rank: Mapped[int] = mapped_column(Integer)
    total_score: Mapped[float] = mapped_column(Float)
    decision: Mapped[str] = mapped_column(String(40), default="pending")
    rationale: Mapped[str] = mapped_column(Text, default="")
    weights: Mapped[dict] = mapped_column(JSON, default=dict)


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(30), default="ready")
    html: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True)
    actor_id: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(100))
    entity_type: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[str] = mapped_column(String(120))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
