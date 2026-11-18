"""narrow question_bank.scope to global/org, dropping the unusable 'branch' value (report item 10)

Revision ID: 0023
Revises: 0022
Create Date: 2026-08-19

Not a doc 03 §9 step — see revision 0020's docstring. This implements the confirmed decision
on report item 10, and it is the only revision in the chain that **deliberately contradicts
doc 03** rather than interpreting it.

What this closes
----------------
**Report item 10: ``scope`` admitted ``'branch'`` and the table has no ``branch_id``.**
doc 03 §7.3 line 1301 lists three values for ``question_bank.scope`` — ``global``, ``org``,
``branch`` — and §5's column list (lines 613-644) has no ``branch_id``. It also gives
conditional rules for two of the three values and not the third:

    - if scope is global, org_id must be null      (line 663)
    - if scope is org, org_id must be present      (line 664)

So a row with ``scope = 'branch'`` was accepted, unconstrained in either direction, and
recorded *that* it was branch-scoped without recording **which branch**. It is
indistinguishable from an org-scoped row at query time, which means the value could not
filter anything: the ten-column retrieval index has ``scope`` as its second column, and a
predicate of ``scope = 'branch'`` narrows to "some branch, somewhere in this org". Revision
0006 shipped the value anyway and flagged it, on the standing instruction not to resolve a
doc 03 ambiguity silently. This is that resolution, now confirmed.

Dropping the value rather than adding the column
------------------------------------------------
Adding ``branch_id`` was the alternative and it is the larger change by a wide margin: a
nullable FK to ``branches`` with ``ON DELETE`` semantics nobody has specified, a third
conditional CHECK (``scope <> 'branch' OR branch_id IS NOT NULL``), a revision to the
retrieval index to make branch filtering selective, and a decision about whether a
branch-scoped question is visible to the parent organization — which is an RLS predicate,
and RLS belongs to P1-T5. Branch-level scoping is a feature, not a column, and the
confirmed decision is to drop the value **until it is actually designed**. Re-adding it
later is this revision's ``downgrade`` plus the column work; nothing here is a one-way door.

The deviation from doc 03, stated plainly
-----------------------------------------
doc 03 §7.3 is a normative table and this revision makes the database disagree with it. Two
places therefore need amending and are not amended by this migration, because a migration
cannot edit a document: **doc 03 §7.3's row for ``question_bank.scope`` should become
``global``, ``org``**, and §5's constraint list should keep both conditional rules, which
now cover the whole vocabulary rather than two thirds of it. Recorded in the P1-T4 report's
decisions log alongside the ``QuestionEmbedding`` amendment.

A consequence worth knowing before P2
-------------------------------------
With ``branch`` gone, the two conditional constraints make ``scope`` **completely derivable
from ``org_id``**: ``global`` requires ``org_id IS NULL``, ``org`` requires
``org_id IS NOT NULL``, and those are the only two values. The column is now a denormalized
restatement of one nullness test, and the CHECK pair guarantees they can never disagree.

That is not a defect and the column is kept, for two reasons: it reads clearly at call sites
(``scope = 'global'`` says what it means; ``org_id IS NULL`` makes the reader reconstruct
it), and it is the second column of ``ix_question_bank_retrieval``, where a text equality is
a better index prefix than a nullness test. But it does mean the column earns its place back
only if ``branch`` — or another non-org scope such as a marketplace or publisher tier —
eventually returns. If P2 concludes it never will, dropping ``scope`` and reading ``org_id``
is the simplification, and it should be a deliberate decision rather than something noticed
in year two.

The pre-flight count, and why it is not defensive clutter
--------------------------------------------------------
``ALTER TABLE ... ADD CONSTRAINT`` validates against existing rows, so if any row has
``scope = 'branch'`` this revision cannot succeed. PostgreSQL's own error for that is
``check constraint "ck_question_bank_scope" of relation "question_bank" is violated by some
row`` — which does not say how many rows, does not say which value offends, and does not say
what to do. Against a development database seeded before this revision existed, that message
sends the reader to the wrong place.

The count runs **before** the ``DROP``, so a failure leaves the old three-value constraint
in place rather than leaving the column unconstrained. Offline (``--sql``) mode skips it
entirely, for the reason revision 0019 documents at length: ``op.get_bind()`` returns a
``MockConnection`` whose ``execute()`` returns ``None``, so any revision that *reads* dies
in offline rendering while passing perfectly online. A DBA applying the rendered script gets
PostgreSQL's own error, which is the best available outcome when there is no process to
raise a better one.
"""
import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "0023"
down_revision: Union[str, Sequence[str], None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_log = logging.getLogger("alembic.runtime.migration")

_CONSTRAINT_NAME = "ck_question_bank_scope"

#: Both vocabularies as frozen literals, rendered exactly as revision 0006 rendered the
#: original (``IN ('a', 'b')``, space after each comma) so that the constraint definition
#: PostgreSQL reads back is comparable across revisions.
_SCOPES_AFTER = "scope IN ('global', 'org')"
_SCOPES_BEFORE = "scope IN ('global', 'org', 'branch')"

_COUNT_BRANCH_SCOPED = sa.text(
    "SELECT count(*) FROM question_bank WHERE scope = 'branch'"
)


def upgrade() -> None:
    """Upgrade schema.

    Count, drop, recreate. The count is skipped offline; see the module docstring.
    """

    if not context.is_offline_mode():
        branch_scoped = op.get_bind().execute(_COUNT_BRANCH_SCOPED).scalar_one()
        if branch_scoped:
            raise RuntimeError(
                f"0023 cannot narrow question_bank.scope: {branch_scoped} row(s) still "
                "have scope = 'branch', and the new CHECK constraint would reject them. "
                "These rows record that they are branch-scoped without recording which "
                "branch (question_bank has no branch_id), so this migration will not "
                "guess a value for them. Decide per row whether each is org-scoped or "
                "global, update it, and re-run:\n"
                "    SELECT id, org_id, source, status, created_at\n"
                "      FROM question_bank\n"
                "     WHERE scope = 'branch'\n"
                "     ORDER BY created_at;\n"
                "A row with a non-null org_id is almost certainly scope = 'org'."
            )
        _log.info("0023: no branch-scoped question_bank rows, narrowing the vocabulary")

    op.drop_constraint(op.f(_CONSTRAINT_NAME), "question_bank", type_="check")
    op.create_check_constraint(op.f(_CONSTRAINT_NAME), "question_bank", _SCOPES_AFTER)


def downgrade() -> None:
    """Downgrade schema.

    Restores the three-value vocabulary exactly as revision 0006 wrote it. Always succeeds:
    widening a CHECK cannot be violated by rows that satisfied the narrower one.

    No data is restored, and none was lost — the upgrade refuses to run while any
    branch-scoped row exists, so there is no state in which this revision destroyed a value.
    """

    op.drop_constraint(op.f(_CONSTRAINT_NAME), "question_bank", type_="check")
    op.create_check_constraint(op.f(_CONSTRAINT_NAME), "question_bank", _SCOPES_BEFORE)
