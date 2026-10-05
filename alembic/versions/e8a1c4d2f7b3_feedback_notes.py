"""add feedback notes

Revision ID: e8a1c4d2f7b3
Revises: d7f3b1c8e5a2
Create Date: 2026-10-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "e8a1c4d2f7b3"
down_revision: Union[str, None] = "d7f3b1c8e5a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "feedback_notes",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("area", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("text", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("feedback_notes", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_feedback_notes_area"), ["area"], unique=False)
        batch_op.create_index(batch_op.f("ix_feedback_notes_status"), ["status"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("feedback_notes", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_feedback_notes_status"))
        batch_op.drop_index(batch_op.f("ix_feedback_notes_area"))
    op.drop_table("feedback_notes")
