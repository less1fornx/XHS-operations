"""add human outcome feedback channel

Revision ID: 9a4d7e2c1b30
Revises: 5e7c1a9d2f44
Create Date: 2026-10-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "9a4d7e2c1b30"
down_revision: Union[str, None] = "5e7c1a9d2f44"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "outreach_outcomes",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("outreach_task_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("shop_lead_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("reply_status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("intent_stage", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("conversion_status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("converted_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True),
        sa.Column("order_value_note", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("need_tags_json", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("attraction_tags_json", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("objection_tags_json", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("human_notes", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("source", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("confirmed_by", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column("updated_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("outreach_outcomes", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_outreach_outcomes_outreach_task_id"),
            ["outreach_task_id"],
            unique=True,
        )
        batch_op.create_index(
            batch_op.f("ix_outreach_outcomes_shop_lead_id"),
            ["shop_lead_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("outreach_outcomes", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_outreach_outcomes_shop_lead_id"))
        batch_op.drop_index(batch_op.f("ix_outreach_outcomes_outreach_task_id"))
    op.drop_table("outreach_outcomes")
