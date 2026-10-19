"""Add Target Discovery v1 three-axis scores.

Revision ID: 0003_target_discovery_three_axes
Revises: 0002_generic_target_discovery
"""

import sqlalchemy as sa
from alembic import op


revision = "0003_target_discovery_three_axes"
down_revision = "0002_generic_target_discovery"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    existing = {item["name"] for item in inspector.get_columns("target_candidates")}
    additions = [
        ("causal_confidence", sa.Float()),
        ("disease_context", sa.Float()),
        ("development_feasibility", sa.Float()),
    ]
    with op.batch_alter_table("target_candidates") as batch:
        for name, column_type in additions:
            if name not in existing:
                batch.add_column(sa.Column(name, column_type, nullable=True))


def downgrade():
    inspector = sa.inspect(op.get_bind())
    existing = {item["name"] for item in inspector.get_columns("target_candidates")}
    with op.batch_alter_table("target_candidates") as batch:
        for name in ("development_feasibility", "disease_context", "causal_confidence"):
            if name in existing:
                batch.drop_column(name)

