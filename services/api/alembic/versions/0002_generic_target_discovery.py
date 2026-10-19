"""Generic disease-target discovery runs and Open Targets provenance.

Revision ID: 0002_generic_target_discovery
Revises: 0001_initial
"""

import sqlalchemy as sa
from alembic import op


revision = "0002_generic_target_discovery"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    # 0001 was originally implemented with ``Base.metadata.create_all``. On a
    # brand-new install it therefore sees the current model metadata, while an
    # existing 0001 database contains the historical schema. Avoid recreating
    # the additive v2 objects on fresh installs, but still migrate real v1 DBs.
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    candidate_columns = {item["name"] for item in inspector.get_columns("target_candidates")} if "target_candidates" in tables else set()
    evidence_columns = {item["name"] for item in inspector.get_columns("target_evidence")} if "target_evidence" in tables else set()
    if (
        {"target_discovery_runs", "external_data_cache"}.issubset(tables)
        and {"target_discovery_run_id", "external_id", "score_origin", "evidence_breakdown"}.issubset(candidate_columns)
        and {"external_evidence_id", "datatype", "category", "details"}.issubset(evidence_columns)
    ):
        return

    op.create_table(
        "target_discovery_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("query", sa.String(255), nullable=False),
        sa.Column("resolution_status", sa.String(40), nullable=False),
        sa.Column("canonical_name", sa.String(255), nullable=True),
        sa.Column("disease_id", sa.String(80), nullable=True),
        sa.Column("ontology", sa.String(40), nullable=True),
        sa.Column("synonyms", sa.JSON(), nullable=False),
        sa.Column("resolution_confidence", sa.Float(), nullable=True),
        sa.Column("resolution_method", sa.String(80), nullable=False),
        sa.Column("source", sa.String(120), nullable=False),
        sa.Column("source_version", sa.String(80), nullable=False),
        sa.Column("source_api_version", sa.String(80), nullable=False),
        sa.Column("evidence_registry_version", sa.String(40), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_reference", sa.Text(), nullable=True),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("data_quality", sa.JSON(), nullable=False),
        sa.Column("benchmark", sa.JSON(), nullable=False),
        sa.Column("benchmark_status", sa.String(40), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("errors", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_target_discovery_runs_job_id", "target_discovery_runs", ["job_id"], unique=True)
    op.create_index("ix_target_discovery_runs_project_id", "target_discovery_runs", ["project_id"])
    op.create_index("ix_target_discovery_runs_disease_id", "target_discovery_runs", ["disease_id"])

    op.create_table(
        "external_data_cache",
        sa.Column("cache_key", sa.String(64), primary_key=True),
        sa.Column("source", sa.String(120), nullable=False),
        sa.Column("source_version", sa.String(80), nullable=False),
        sa.Column("api_version", sa.String(80), nullable=False),
        sa.Column("operation", sa.String(80), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("storage_uri", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_external_data_cache_source", "external_data_cache", ["source"])
    op.create_index("ix_external_data_cache_source_version", "external_data_cache", ["source_version"])
    op.create_index("ix_external_data_cache_operation", "external_data_cache", ["operation"])

    with op.batch_alter_table("target_candidates") as batch:
        batch.add_column(sa.Column("target_discovery_run_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("external_id", sa.String(80), nullable=True))
        batch.add_column(sa.Column("biotype", sa.String(80), nullable=True))
        batch.add_column(sa.Column("rank", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("score_origin", sa.String(80), nullable=False, server_default="precomputed_demo"))
        batch.add_column(sa.Column("derived_score", sa.Float(), nullable=True))
        batch.add_column(sa.Column("evidence_coverage", sa.Float(), nullable=True))
        batch.add_column(sa.Column("evidence_count", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("source_count", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("evidence_breakdown", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("data_mode", sa.String(30), nullable=False, server_default="precomputed"))
        batch.add_column(sa.Column("source", sa.String(120), nullable=False, server_default="precomputed_demo"))
        batch.add_column(sa.Column("source_version", sa.String(80), nullable=True))
        batch.add_column(sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("raw_reference", sa.Text(), nullable=True))
        batch.create_foreign_key(
            "fk_target_candidates_target_discovery_run",
            "target_discovery_runs",
            ["target_discovery_run_id"],
            ["id"],
            ondelete="CASCADE",
        )
    op.create_index("ix_target_candidates_target_discovery_run_id", "target_candidates", ["target_discovery_run_id"])
    op.create_index("ix_target_candidates_external_id", "target_candidates", ["external_id"])

    with op.batch_alter_table("target_evidence") as batch:
        batch.add_column(sa.Column("external_evidence_id", sa.String(80), nullable=True))
        batch.add_column(sa.Column("disease_id", sa.String(80), nullable=True))
        batch.add_column(sa.Column("datatype", sa.String(80), nullable=True))
        batch.add_column(sa.Column("datasource", sa.String(120), nullable=True))
        batch.add_column(sa.Column("category", sa.String(80), nullable=True))
        batch.add_column(sa.Column("direction_on_target", sa.String(80), nullable=True))
        batch.add_column(sa.Column("direction_on_trait", sa.String(80), nullable=True))
        batch.add_column(sa.Column("evidence_count", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("source_version", sa.String(80), nullable=True))
        batch.add_column(sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("raw_reference", sa.Text(), nullable=True))
        batch.add_column(sa.Column("data_mode", sa.String(30), nullable=False, server_default="precomputed"))
        batch.add_column(sa.Column("confidence", sa.String(120), nullable=True))
        batch.add_column(sa.Column("details", sa.JSON(), nullable=True))
    op.create_index("ix_target_evidence_external_evidence_id", "target_evidence", ["external_evidence_id"])
    op.create_index("ix_target_evidence_disease_id", "target_evidence", ["disease_id"])
    op.create_index("ix_target_evidence_datatype", "target_evidence", ["datatype"])
    op.create_index("ix_target_evidence_datasource", "target_evidence", ["datasource"])
    op.create_index("ix_target_evidence_category", "target_evidence", ["category"])


def downgrade():
    for name in (
        "ix_target_evidence_category",
        "ix_target_evidence_datasource",
        "ix_target_evidence_datatype",
        "ix_target_evidence_disease_id",
        "ix_target_evidence_external_evidence_id",
    ):
        op.drop_index(name, table_name="target_evidence")
    with op.batch_alter_table("target_evidence") as batch:
        for column in (
            "details", "confidence", "data_mode", "raw_reference", "retrieved_at", "source_version",
            "evidence_count", "direction_on_trait", "direction_on_target", "category", "datasource",
            "datatype", "disease_id", "external_evidence_id",
        ):
            batch.drop_column(column)

    op.drop_index("ix_target_candidates_external_id", table_name="target_candidates")
    op.drop_index("ix_target_candidates_target_discovery_run_id", table_name="target_candidates")
    with op.batch_alter_table("target_candidates") as batch:
        batch.drop_constraint("fk_target_candidates_target_discovery_run", type_="foreignkey")
        for column in (
            "raw_reference", "retrieved_at", "source_version", "source", "data_mode", "evidence_breakdown",
            "source_count", "evidence_count", "evidence_coverage", "derived_score", "score_origin", "rank",
            "biotype", "external_id", "target_discovery_run_id",
        ):
            batch.drop_column(column)

    op.drop_table("external_data_cache")
    op.drop_table("target_discovery_runs")
