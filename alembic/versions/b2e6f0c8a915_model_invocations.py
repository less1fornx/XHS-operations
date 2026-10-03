"""add model invocation records

Revision ID: b2e6f0c8a915
Revises: 9a4d7e2c1b30
Create Date: 2026-10-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "b2e6f0c8a915"
down_revision: Union[str, None] = "9a4d7e2c1b30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "model_invocations",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("workflow_run_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("shop_lead_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("source", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("case_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("provider", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("model", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("prompt_version", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("prompt_sha256", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("input_json", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("evidence_ids_json", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("output_json", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("error_type", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("error_message", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("request_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("usage_json", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("model_invocations", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_model_invocations_workflow_run_id"),
            ["workflow_run_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_model_invocations_shop_lead_id"),
            ["shop_lead_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("model_invocations", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_model_invocations_shop_lead_id"))
        batch_op.drop_index(batch_op.f("ix_model_invocations_workflow_run_id"))
    op.drop_table("model_invocations")
