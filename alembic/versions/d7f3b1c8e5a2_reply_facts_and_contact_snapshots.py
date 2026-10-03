"""add reply facts and contact-time snapshots

Revision ID: d7f3b1c8e5a2
Revises: c41d7a9e2f60
Create Date: 2026-10-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "d7f3b1c8e5a2"
down_revision: Union[str, None] = "c41d7a9e2f60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("outreach_outcomes", schema=None) as batch_op:
        batch_op.add_column(sa.Column("replied_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True))
        batch_op.add_column(sa.Column("reply_timing", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column("reply_text", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(
            sa.Column("ask_tags_json", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default="[]")
        )
        batch_op.add_column(sa.Column("stage", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.create_table(
        "contact_snapshots",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("run_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("shop_lead_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("sent_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column("channel", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("final_text", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("tier", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("shop_type", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("features_json", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("versions_json", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("contact_snapshots", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_contact_snapshots_run_id"), ["run_id"], unique=True)
        batch_op.create_index(batch_op.f("ix_contact_snapshots_shop_lead_id"), ["shop_lead_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("contact_snapshots", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_contact_snapshots_shop_lead_id"))
        batch_op.drop_index(batch_op.f("ix_contact_snapshots_run_id"))
    op.drop_table("contact_snapshots")
    with op.batch_alter_table("outreach_outcomes", schema=None) as batch_op:
        for column in ("stage", "ask_tags_json", "reply_text", "reply_timing", "replied_at"):
            batch_op.drop_column(column)
