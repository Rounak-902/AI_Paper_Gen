"""enforce one active curriculum version per board

Revision ID: 0029
Revises: 0028
Create Date: 2026-08-29

The curriculum lifecycle treats ``active`` as the board's one canonical
published syllabus. Superseded versions remain available for historical and
explicitly pinned Program/question-paper references, but two live active rows
would make an unqualified current-version lookup ambiguous. The partial unique
index is the final database-level invariant; the service's board advisory lock
serializes the publish race before this constraint is evaluated.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0029"
down_revision: Union[str, Sequence[str], None] = "0028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "uq_curriculum_versions_one_active_per_board",
        "curriculum_versions",
        ["board_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active' AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_curriculum_versions_one_active_per_board",
        table_name="curriculum_versions",
    )
