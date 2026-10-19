"""Add structured compound evidence payload.

Revision ID: 0004_vcell_compound
Revises: 0003_target_discovery_three_axes
"""

import sqlalchemy as sa
from alembic import op


revision = "0004_vcell_compound"
down_revision = "0003_target_discovery_three_axes"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    existing = {item["name"] for item in inspector.get_columns("compounds")}
    if "details" not in existing:
        with op.batch_alter_table("compounds") as batch:
            batch.add_column(sa.Column("details", sa.JSON(), nullable=False, server_default="{}"))


def downgrade():
    inspector = sa.inspect(op.get_bind())
    existing = {item["name"] for item in inspector.get_columns("compounds")}
    if "details" in existing:
        with op.batch_alter_table("compounds") as batch:
            batch.drop_column("details")
