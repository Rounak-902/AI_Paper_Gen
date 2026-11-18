"""add question_bank.prompt_version_id, the AI provenance link (report item 7)

Revision ID: 0022
Revises: 0021
Create Date: 2026-08-19

Not a doc 03 §9 step — see revision 0020's docstring. This implements the confirmed decision
on report item 7.

What this closes
----------------
**Report item 7: ``prompt_versions`` existed with nothing pointing at it.** Revision 0016
created the table doc 03 §5 specifies — task, version, template_ref, model, eval_score,
status, lineage — and doc 03 gives no column anywhere that references it. So the schema
could record *that* prompt v7 of the ``generate_questions`` task existed, and could not
record that any particular question came out of it.

That is the gap this closes, and it is worth being precise about what it buys, because the
column is small and the consequence is not: ``question_bank.source`` already admits
``ai_generated``, so before this revision the schema could say "this question came from a
model" and could not say **which prompt, which model, or which template**. Every question
the AI pipeline produces is therefore unattributable in exactly the situation where
attribution matters — a bad prompt version ships, produces questions that are subtly wrong,
and nothing in the database can answer "which questions came from that prompt". With this
column that is one indexed query.

Nullable, and it has to be
--------------------------
Three of the five ``source`` values — ``institute_bank``, ``textbook``, ``previous_year`` —
describe questions no prompt produced. A NOT NULL column would require inventing a
sentinel prompt version for human-authored content, which is a worse lie than a NULL. So:
``uuid NULL``, with the honest reading that **NULL means "not attributed to a prompt"**, not
"unknown".

Deliberately *not* added: a CHECK tying the column to ``source``. The tempting constraint is
``source <> 'ai_generated' OR prompt_version_id IS NOT NULL``, and it is wrong in both
directions. Questions generated before this column existed have ``source = 'ai_generated'``
and no prompt to point at, so the constraint could not be added to a populated table without
a backfill that has nothing to backfill from. And a curated question *edited* with AI
assistance is legitimately both human-sourced and prompt-attributed. Flagged as a question
for the AI pipeline task (P4) rather than answered here.

``ON DELETE SET NULL``, as confirmed
-----------------------------------
Confirmed in the decision, and it is the right action for a reason worth recording: this is
a provenance pointer, not an ownership edge. ``CASCADE`` would delete real questions because
an old prompt row was cleaned up — catastrophic and obviously wrong. ``RESTRICT`` would make
``prompt_versions`` rows undeletable forever, which sounds appealing for an audit trail but
puts the audit requirement in the wrong place: if prompt versions must be immutable, that
is a rule about ``prompt_versions`` (a trigger like revision 0020's, or ADR-004's
``deleted_at``, which revision 0016 already gave it), not a side effect of a foreign key on
another table. ``SET NULL`` says what actually happened: the question is still real, its
prompt record is gone.

The index is not optional either
--------------------------------
PostgreSQL does not index the referencing side of a foreign key automatically, and this
column has two independent needs for one:

* **The query the column exists for.** "Which questions came from prompt version X" is the
  entire point, and it is a sequential scan over the largest table in the schema without an
  index.
* **``ON DELETE SET NULL`` itself.** Deleting a ``prompt_versions`` row makes PostgreSQL
  find every referencing row in ``question_bank``. Unindexed, every prompt deletion scans
  the whole question bank while holding locks.

Inherits revision 0020's protection for free
--------------------------------------------
``prompt_version_id`` is not in revision 0020's mutable set, so from the moment this column
exists an approved question's prompt attribution cannot be rewritten. That is the payoff of
writing 0020's comparison as a whole-row jsonb difference rather than a list of named
columns: a revision two steps later adds a column and the immutability rule extends to it
with no edit and no chance of forgetting. It also means backfilling this column on
*approved* rows requires the maintenance-window path in 0020's docstring — worth knowing
before a P4 backfill is written.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "0022"
down_revision: Union[str, Sequence[str], None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Explicit names on both objects. The ``MetaData`` naming convention in ``app/db/base.py``
#: generates these for model-driven DDL, but ``op.create_foreign_key`` and
#: ``op.create_index`` take whatever name they are given — so the names are written out here
#: to match what the convention would produce
#: (``fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s`` and
#: ``ix_%(table_name)s_%(column_0_N_name)s``). A mismatch would not fail anything at upgrade
#: time; it would fail the downgrade's drop-by-name, months later.
_FK_NAME = "fk_question_bank_prompt_version_id_prompt_versions"
_INDEX_NAME = "ix_question_bank_prompt_version_id"


def upgrade() -> None:
    """Upgrade schema.

    Column, then foreign key, then index. ``add_column`` on a nullable column with no
    default takes ``ACCESS EXCLUSIVE`` only briefly and does not rewrite the table
    (PostgreSQL 11+), so this is cheap even against a populated question bank — unlike
    revision 0018's generated column, which did rewrite it.
    """

    op.add_column(
        "question_bank",
        sa.Column("prompt_version_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        _FK_NAME,
        "question_bank",
        "prompt_versions",
        ["prompt_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(_INDEX_NAME, "question_bank", ["prompt_version_id"])


def downgrade() -> None:
    """Downgrade schema.

    Reverse order, and the column drop would take the other two with it anyway — dropping a
    column drops the constraints and indexes that depend on it. They are dropped explicitly
    regardless, so that a downgrade run against a database where one of them was removed by
    hand fails on the object that is actually missing rather than succeeding quietly.

    Data loss on downgrade is real and unavoidable: every prompt attribution recorded while
    this revision was applied is gone. That is inherent to dropping a column and is noted
    rather than worked around — a downgrade that preserved the values would have to write
    them somewhere this schema has no place for.
    """

    op.drop_index(_INDEX_NAME, table_name="question_bank")
    op.drop_constraint(_FK_NAME, "question_bank", type_="foreignkey")
    op.drop_column("question_bank", "prompt_version_id")
