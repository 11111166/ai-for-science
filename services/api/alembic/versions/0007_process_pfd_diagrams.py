"""Add versioned browser-editable concept PFD/CAD diagrams.

Revision ID: 0007_process_pfd_diagrams
Revises: 0006_process_portfolios
"""

import sqlalchemy as sa
from alembic import op


revision = "0007_process_pfd_diagrams"
down_revision = "0006_process_portfolios"
branch_labels = None
depends_on = None


def upgrade():
    if "process_pfd_diagrams" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "process_pfd_diagrams",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("project_id", sa.String(length=36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column("data", sa.JSON(), nullable=False),
        )
        op.create_index("ix_process_pfd_diagrams_project_id", "process_pfd_diagrams", ["project_id"])


def downgrade():
    # Retain user-authored concept diagrams on application rollback. Dropping
    # the table needs a deliberate data-retention decision first.
    pass