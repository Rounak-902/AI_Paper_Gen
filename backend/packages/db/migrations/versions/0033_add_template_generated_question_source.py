"""admit deterministic template generation as question provenance

Revision ID: 0033
Revises: 0032
Create Date: 2026-09-11

Doc 06 section 9.1 requires ``source = 'template_generated'`` so deterministic
questions remain distinguishable from both bank content and paid AI generation.
Revision 0006 predates that execution path and therefore did not include the value.
This revision widens only the existing controlled vocabulary; it does not alter old
rows or relax any other question-bank invariant.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0033"
down_revision: Union[str, Sequence[str], None] = "0032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CONSTRAINT_NAME = "ck_question_bank_source"
_SOURCES_BEFORE = (
    "source IN ('institute_bank', 'textbook', 'intext', 'previous_year', "
    "'ai_generated')"
)
_SOURCES_AFTER = (
    "source IN ('institute_bank', 'textbook', 'intext', 'previous_year', "
    "'ai_generated', 'template_generated')"
)


def upgrade() -> None:
    op.drop_constraint(op.f(_CONSTRAINT_NAME), "question_bank", type_="check")
    op.create_check_constraint(
        op.f(_CONSTRAINT_NAME), "question_bank", _SOURCES_AFTER
    )


def downgrade() -> None:
    # PostgreSQL itself refuses the downgrade while template-generated rows exist.
    # Silently rewriting provenance would make cost and audit reporting incorrect.
    op.drop_constraint(op.f(_CONSTRAINT_NAME), "question_bank", type_="check")
    op.create_check_constraint(
        op.f(_CONSTRAINT_NAME), "question_bank", _SOURCES_BEFORE
    )
