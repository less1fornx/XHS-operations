"""store the basis for a DM or comment decision

Revision ID: 3c4a8f7b2d11
Revises: 74d098d94e9f
Create Date: 2026-10-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "3c4a8f7b2d11"
down_revision: Union[str, None] = "74d098d94e9f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("outreach_tasks", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "contact_basis",
                sqlmodel.sql.sqltypes.AutoString(),
                nullable=False,
                server_default="cold_outreach",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("outreach_tasks", schema=None) as batch_op:
        batch_op.drop_column("contact_basis")
