"""Separate research projects from validation runs and add disease profiles.

Revision ID: 0005_project_profiles
Revises: 0004_vcell_compound
"""

import sqlalchemy as sa
from alembic import op


revision = "0005_project_profiles"
down_revision = "0004_vcell_compound"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    existing = {item["name"] for item in inspector.get_columns("projects")}
    with op.batch_alter_table("projects") as batch:
        if "disease_id" not in existing:
            batch.add_column(sa.Column("disease_id", sa.String(length=80), nullable=True))
        if "project_type" not in existing:
            batch.add_column(sa.Column("project_type", sa.String(length=30), nullable=False, server_default="research"))
        if "research_track" not in existing:
            batch.add_column(sa.Column("research_track", sa.String(length=40), nullable=False, server_default="target_discovery"))

    # Existing smoke and release-verification records remain auditable but no
    # longer appear in the default user project list.
    op.execute(sa.text("""
        UPDATE projects
        SET project_type = 'validation'
        WHERE name LIKE '%生产验证%'
           OR name LIKE '%上线验证%'
           OR owner_id LIKE '%smoke%'
    """))

    inspector = sa.inspect(op.get_bind())
    indexes = {item["name"] for item in inspector.get_indexes("projects")}
    if "ix_projects_project_type" not in indexes:
        op.create_index("ix_projects_project_type", "projects", ["project_type"], unique=False)


def downgrade():
    inspector = sa.inspect(op.get_bind())
    indexes = {item["name"] for item in inspector.get_indexes("projects")}
    if "ix_projects_project_type" in indexes:
        op.drop_index("ix_projects_project_type", table_name="projects")
    existing = {item["name"] for item in inspector.get_columns("projects")}
    with op.batch_alter_table("projects") as batch:
        if "research_track" in existing:
            batch.drop_column("research_track")
        if "project_type" in existing:
            batch.drop_column("project_type")
        if "disease_id" in existing:
            batch.drop_column("disease_id")
