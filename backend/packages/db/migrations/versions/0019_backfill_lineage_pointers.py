"""backfill current pointers and derived search artifacts (doc 03 §9 step 19)

Revision ID: 0019
Revises: 0018
Create Date: 2026-08-17

doc 03 §9 step 19, the last step in the migration order: *"Backfill current pointers and
derived search artifacts."*

**This revision issues no DDL.** It is the only revision in the chain that changes data
and not structure, which has a useful consequence for verification: a catalog dump taken
at 0018 and one taken at 0019 are byte-identical, and ``alembic check`` reports no drift
at either, because ``Base.metadata`` describes structure and there is none to add here.

Two halves, and only one of them has any work in it
---------------------------------------------------
**"Derived search artifacts" is vacuous as written, and that is a doc 03 finding rather
than something worked around.** doc 03 §8 asks for the search document as a *generated*
column, and revision 0018 creates it that way — ``GENERATED ALWAYS AS (...) STORED``.
PostgreSQL evaluates a stored generated column's expression for every existing row as part
of the ``ADD COLUMN`` table rewrite, so there is no interval in which the column exists
and is unpopulated. There is nothing here to backfill, and any ``UPDATE`` written to do it
would be a no-op that rewrites the whole table for nothing.

The two halves of §8 and §9 are describing different designs. Step 19 presupposes a
*trigger-maintained* ``tsvector``, which genuinely does need a one-time
``UPDATE question_bank SET stem = stem`` to fire the trigger over pre-existing rows;
§8 mandates a *generated* column, which does not. Both cannot be the intent. §8 wins
because it specifies the mechanism and §9 only specifies an order, and the residue is that
step 19 keeps a clause with no implementation. Reported, not silently dropped.

**"Current pointers" is the half with real work**, and the pointer in question is
``question_bank.root_question_id``. Three columns in the schema could answer to that name,
so the reading is stated explicitly:

* ``question_bank.is_current`` — nothing to backfill. It is ``NOT NULL DEFAULT true``, so
  every row that exists already has a value, and every row that will exist gets one.
* ``organizations.subscription_id`` — cannot be backfilled, and not for want of trying.
  Choosing which of an organization's subscriptions is "current" needs a rule
  (``status = 'active'``? latest ``period_end``? both, and what about two actives?) that
  doc 03 states nowhere, and the constraint that makes the column trustworthy only arrived
  one revision ago. Left to the Billing module.
* ``question_bank.root_question_id`` — nullable, and the lineage identity that ADR-003's
  BR-8 index is built on. This is the one, and it is where the value is: **a row with
  ``root_question_id IS NULL`` is not constrained by
  ``uq_question_bank_root_question_id_current`` at all.** That index is partial-unique on
  ``root_question_id WHERE is_current``, and PostgreSQL's default NULLS DISTINCT means
  every NULL is unique to itself — so unrooted rows can accumulate as an unbounded set of
  simultaneously-"current" questions, which is the exact state BR-8 exists to forbid.
  Rooting them is what puts them back under the constraint.

Why the ``previous_version_id IS NULL`` guard, and why it is not caution
-----------------------------------------------------------------------
The obvious backfill is ``UPDATE question_bank SET root_question_id = id WHERE
root_question_id IS NULL``. This revision adds a second predicate — ``AND
previous_version_id IS NULL`` — and the reason is that the unguarded form can silently
corrupt a lineage.

Consider a fork ``A → B`` where ``A`` is correctly rooted (``root = A``) and ``B`` was
inserted with ``previous_version_id = A`` but no root:

* **Unguarded**, ``B`` becomes its own root. It is now a separate one-row lineage. Both
  ``A`` and ``B`` can be ``is_current`` because they answer to different roots, so BR-8's
  index is satisfied, ``alembic check`` is clean, and every catalog assertion passes.
  Nothing anywhere reports that one question's history was split in two.
* **Guarded**, ``B`` is left NULL. That is a visible anomaly — one query finds it, and the
  query is in this docstring — and it is *the same* anomaly the row already had, not a new
  one.

So the guard trades an undetectable wrong answer for a detectable missing one. A row with
a predecessor and no root is a repository-layer bug, and a backfill that papers over it
destroys the evidence. Rows in that state are counted and logged at WARNING rather than
raised on: a migration that aborts over pre-existing data leaves the chain half-applied,
which is a worse operational position than an upgrade that completes and tells you what it
found.

The detection query, for whoever reads that warning::

    SELECT id, previous_version_id, version_number, is_current, status
    FROM question_bank
    WHERE root_question_id IS NULL
    ORDER BY created_at;

What ``--sql`` (offline) mode can and cannot render
--------------------------------------------------
This is the only revision in the chain that has to know whether it is running offline,
and the reason is structural rather than incidental: it is the only one that *reads*.

In offline mode Alembic never opens a connection. ``op.get_bind()`` hands back a
SQLAlchemy ``MockConnection`` whose ``execute()`` forwards to a writer callable and
returns whatever that callable returns, which is ``None`` — its whole job is to serialize
the statement into the output script. So offline:

* ``result.rowcount`` is not merely unknown, there is no ``result``. The statement has not
  run and cannot have affected a number of rows.
* ``bind.execute(_COUNT_UNRESOLVED).scalar_one()`` cannot work either, and would be the
  next failure behind the first. A ``SELECT`` has nowhere to send its answer.

The split below therefore emits the ``UPDATE`` and stops:

* **The backfill is rendered.** It is a real change to real data, and a DBA handed the
  output of ``alembic upgrade head --sql`` must apply it or their database ends at a
  different state than an online run would produce. Dropping it to keep this function
  read-free would make the generated script a quietly incomplete copy of the chain.
* **The diagnostic count is not rendered.** It changes nothing, and a bare
  ``SELECT count(*)`` sitting in a DDL script does nothing when psql runs it except print
  a number into a migration log that no code is reading. Its value is the WARNING it
  raises in-process, and offline there is no process to warn.
* **Neither log line fires.** Reporting "rooted 0 row(s)" from a run that rooted no rows
  because it touched no database would be a fabricated number in an operator-facing log.
  Silence is the honest output.

Found by the verification script rather than by reading: the online upgrade succeeded, all
~38 content assertions against the rendered DDL passed, and the *only* signal was
``alembic upgrade head --sql`` exiting 1 with the rendered file stopping after this
revision's ``UPDATE`` — no ``UPDATE alembic_version``, no ``COMMIT``. The script now
asserts on that tail as well, so a truncated render fails on what is missing and not just
on an exit code.

Idempotent, and safe against every uniqueness rule on the table
---------------------------------------------------------------
Re-running it changes nothing: after the first pass no row matches ``root_question_id IS
NULL``, so the second pass updates zero rows. This matters more than it looks — Alembic
will re-run this revision on every ``downgrade``/``upgrade`` cycle the verification script
performs, and a backfill that is not idempotent turns that cycle into a data-corruption
test.

It also cannot violate either uniqueness rule on ``question_bank``, and the argument is
short: the value assigned is ``id``, which is the primary key. ``(root_question_id,
version_number)`` becomes ``(id, version_number)`` and ``root_question_id WHERE
is_current`` becomes ``id``, both unique because ``id`` is. No ``ON CONFLICT`` clause is
needed, and one would only hide a failure that cannot happen.

Why NOT NULL is deliberately *not* added
----------------------------------------
Rooting every existing row invites the follow-up ``ALTER COLUMN root_question_id SET NOT
NULL``, which would make the BR-8 index total rather than partial-in-effect and close the
NULLS DISTINCT hole described above for good. It is not done, and the reason is mechanical
rather than a change of posture:

**A column default cannot reference another column.** ``root_question_id`` can only be set
to ``id`` after ``id`` is known, so there is no ``DEFAULT`` that self-roots a new row.
NOT NULL would therefore force every INSERT to supply both columns, which means generating
the UUID in Python instead of letting ``gen_random_uuid()`` do it, and it would break every
plain ``INSERT INTO question_bank (...)`` — including the fixtures in
``tests/conftest.py``, the seed script, and any ``psql`` session. The alternatives are a
``BEFORE INSERT`` trigger (real DDL that doc 03 does not specify, and invisible to
``alembic check``) or an identity-generating default, neither of which is P1-T4's call to
make.

So the invariant stays where ADR-003 puts it: **the repository layer roots new rows**
(P1-T5). What this revision guarantees is narrower and still worth having — that no row
predating that layer is left outside BR-8's reach. ``tests/test_question_lineage.py``
roots its lineages explicitly and asserts the constraint bites, so the gap is measured
rather than assumed.
"""
import logging
from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0019"
down_revision: Union[str, Sequence[str], None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Alembic configures the ``alembic`` logger hierarchy in ``alembic.ini``, so this name
#: is guaranteed to reach the console during a migration run. A module-named logger would
#: not be — revision modules are loaded under their filename, outside that hierarchy.
_log = logging.getLogger("alembic.runtime.migration")

#: Roots every row that has no predecessor to itself. See the module docstring for why the
#: second predicate is not optional.
_BACKFILL_ROOTS = sa.text(
    """
    UPDATE question_bank
       SET root_question_id = id
     WHERE root_question_id IS NULL
       AND previous_version_id IS NULL
    """
)

#: Whatever the backfill could not resolve: a forked row whose lineage identity was never
#: recorded. Reported, never guessed at.
_COUNT_UNRESOLVED = sa.text(
    """
    SELECT count(*)
      FROM question_bank
     WHERE root_question_id IS NULL
    """
)


def upgrade() -> None:
    """Upgrade schema.

    No DDL. Two statements online: one idempotent backfill, one diagnostic count.
    Offline, only the backfill — see the module docstring for why the count cannot be
    rendered and why the backfill must be.
    """

    # Checked before op.get_bind() rather than after, because what get_bind() returns
    # offline is a MockConnection that answers execute() with None. Every line below the
    # branch assumes a result object exists.
    if context.is_offline_mode():
        op.execute(_BACKFILL_ROOTS)
        return

    bind = op.get_bind()

    result = bind.execute(_BACKFILL_ROOTS)
    rooted = result.rowcount if result.rowcount is not None else -1

    unresolved = bind.execute(_COUNT_UNRESOLVED).scalar_one()

    # On a greenfield database both numbers are zero and neither line is interesting.
    # Against real data they are the only record of what this revision touched, so they
    # are logged rather than left to be reconstructed from row counts afterwards.
    _log.info("0019: rooted %s question_bank row(s) to themselves", rooted)

    if unresolved:
        _log.warning(
            "0019: %s question_bank row(s) still have root_question_id IS NULL. Each has "
            "a previous_version_id, so its lineage identity cannot be inferred without "
            "guessing, and self-rooting it would split a lineage in a way nothing "
            "detects. These rows are NOT constrained by "
            "uq_question_bank_root_question_id_current. See revision 0019's docstring for "
            "the query that lists them.",
            unresolved,
        )


def downgrade() -> None:
    """Downgrade schema.

    Deliberately empty, and this is the one place in the chain where "reversible" needs
    qualifying.

    Structurally there is nothing to reverse: this revision issues no DDL, so the schema
    at 0018 and at 0019 is identical and every catalog assertion, index definition and
    ``alembic check`` result is the same at both. The upgrade/downgrade/upgrade cycle is
    therefore fully reversible in the sense the verification script tests.

    The *data* change is not undone, and must not be. The only available inverse is
    ``UPDATE question_bank SET root_question_id = NULL WHERE root_question_id = id``,
    which cannot distinguish a row this revision rooted from one the application rooted
    correctly on insert — every genuine single-version lineage in the bank matches that
    predicate. It would un-root the entire table to undo a backfill that may have touched
    nothing, and it would push all of those rows back outside BR-8's partial unique index.

    Leaving a completed backfill in place across a downgrade is the standard posture for
    data migrations and the safe one here: the rooted state is valid at every revision
    from 0006 onward, since ``root_question_id`` has been nullable-but-populatable since
    the column was created.
    """
