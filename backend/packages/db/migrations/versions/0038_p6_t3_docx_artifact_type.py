"""P6-T3 distinguish paper and answer-key export artifacts.

Revision ID: 0038
Revises: 0037
Create Date: 2026-09-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0038"
down_revision: Union[str, Sequence[str], None] = "0037"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add nullable first, then backfill explicitly so every P6-T2 row is
    # unambiguously a student-facing paper before the NOT NULL guard is applied.
    op.add_column(
        "export_artifacts",
        sa.Column("artifact_type", sa.Text(), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE public.export_artifacts "
            "SET artifact_type = 'paper' WHERE artifact_type IS NULL"
        )
    )
    op.alter_column(
        "export_artifacts",
        "artifact_type",
        nullable=False,
        server_default=sa.text("'paper'"),
    )
    op.create_check_constraint(
        op.f("ck_export_artifacts_artifact_type"),
        "export_artifacts",
        "artifact_type IN ('paper','answer_key')",
    )
    op.create_index(
        "ix_export_artifacts_version_artifact_type",
        "export_artifacts",
        ["paper_version_id", "artifact_type", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_export_artifacts_version_artifact_type",
        table_name="export_artifacts",
    )
    op.drop_constraint(
        op.f("ck_export_artifacts_artifact_type"),
        "export_artifacts",
        type_="check",
    )
    op.drop_column("export_artifacts", "artifact_type")
