"""track one-shot scheduled task reminders

Revision ID: 5e7c1a9d2f44
Revises: 3c4a8f7b2d11
Create Date: 2026-10-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "5e7c1a9d2f44"
down_revision: Union[str, None] = "3c4a8f7b2d11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("outreach_tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("reminder_sent_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("outreach_tasks", schema=None) as batch_op:
        batch_op.drop_column("reminder_sent_at")
