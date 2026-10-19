"""Add immutable process product and route portfolios.

Revision ID: 0006_process_portfolios
Revises: 0005_project_profiles
"""

import sqlalchemy as sa
from alembic import op


revision = "0006_process_portfolios"
down_revision = "0005_project_profiles"
branch_labels = None
depends_on = None


def upgrade():
    if "process_portfolios" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "process_portfolios",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("project_id", sa.String(length=36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column("data", sa.JSON(), nullable=False),
        )
        op.create_index("ix_process_portfolios_project_id", "process_portfolios", ["project_id"])


def downgrade():
    # Retain user-authored portfolios on application rollback. A deliberate
    # data-retention operation is required before physically dropping the table.
    pass
