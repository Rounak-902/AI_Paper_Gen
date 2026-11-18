"""enforce approved-question immutability with a BEFORE UPDATE trigger (report item 1)

Revision ID: 0020
Revises: 0019
Create Date: 2026-08-19

Not a doc 03 §9 step. Revisions 0001-0019 map 1:1 onto §9's nineteen steps; **0020 onward
are amendments** made after the P1-T4 report, each one implementing a confirmed decision
from that report's Tier A list. The revision number no longer carries a step number, and
that break is deliberate — renumbering the chain to keep the mapping would mean editing
applied revisions.

What this closes
----------------
Report item 1, the largest open item in P1-T4: **ADR-003's "an approved question is
immutable" had no enforcement anywhere in the database.** A CHECK constraint cannot see the
previous state of the row it is validating, so ``status = 'approved' AND content unchanged``
is not expressible as a CHECK — the comparison needs ``OLD``, and only a trigger has one.
Until now the invariant lived entirely in a repository layer that does not exist yet
(P1-T5), which meant any ``psql`` session, seed script, admin tool or ORM call could edit
the text of a question that had already been printed on a paper.

The rule as implemented
----------------------
``BEFORE UPDATE ... FOR EACH ROW WHEN (OLD.status = 'approved')``, raising if the UPDATE
changes any column outside :data:`_MUTABLE_ON_APPROVED`.

The ``WHEN`` clause and the function body split the rule in two on purpose. ``WHEN`` decides
*which rows are protected* and is visible in ``pg_get_triggerdef`` output, so the protected
set is a catalog fact that verification can read back. The function decides *what is
forbidden* on those rows. Neither duplicates the other, so there is one place to change if
either half is revised.

Two columns beyond the confirmed three, and why they are not optional
--------------------------------------------------------------------
The confirmed decision named ``status``, ``updated_at`` and ``row_version`` as the mutable
set. Implemented with **five**, and the two additions are load-bearing rather than
defensive:

* ``is_current`` — **without it, fork-on-edit is impossible.** doc 03 §5 puts a partial
  unique index on ``root_question_id WHERE is_current`` (revision 0006,
  ``uq_question_bank_root_question_id_current``), so creating version 2 of a lineage
  *requires* setting version 1's ``is_current`` to false in the same transaction. Version 1
  is the approved row. A trigger that blocks that UPDATE does not enforce ADR-003's
  versioning model, it forbids it: approved questions could never be superseded, and the
  only way to revise one would be to edit it in place, which is the exact act this
  revision exists to prevent. Blocking ``is_current`` would invert the requirement it
  implements.
* ``deleted_at`` — **without it, an approved question can never be retired.** ADR-004
  forbids hard deletes from application code and doc 03 §5 says "prefer soft delete" for
  this table, so ``deleted_at`` is the only sanctioned removal path. This trigger is
  ``BEFORE UPDATE`` and does not fire on ``DELETE``, so blocking ``deleted_at`` would leave
  hard ``DELETE`` as the *only* way to remove an approved row — pushing every retirement
  onto the one operation ADR-004 prohibits and destroying the lineage history that
  ``ON DELETE RESTRICT`` on the self-references exists to protect. Nothing is gained
  either: ``status`` is mutable, so ``status = 'archived'`` is already an available
  soft-retirement, and refusing ``deleted_at`` would leave two retirement mechanisms
  disagreeing about which one works.

``search_document`` is also excluded, for a third and purely mechanical reason: it is a
``GENERATED ALWAYS ... STORED`` column, and PostgreSQL computes generated columns *after*
``BEFORE`` row triggers run. Its value in ``NEW`` is therefore not the value the row will
end up with, and comparing it would make every UPDATE on an approved row — including a
bare status transition — look like a content change. Excluding it costs no coverage,
because the column is a pure function of ``stem``, ``answer`` and ``rubric`` and all three
of those are protected: no edit can move the search vector without tripping the trigger on
its inputs first.

Everything else is protected, and that includes columns added later
------------------------------------------------------------------
The comparison is written as a **jsonb difference over the whole row** rather than a list of
``OLD.col IS DISTINCT FROM NEW.col`` tests. That choice matters more than it looks: an
enumerated trigger silently stops protecting any column added after it was written, and the
failure is invisible — the trigger still exists, still fires, and quietly permits edits to
the new column. With ``to_jsonb(OLD) - _ignored``, a column added by a future revision is
protected the moment it exists, and un-protecting one is an explicit edit to the ignore
list. Revision 0022 adds ``prompt_version_id`` two steps later and inherits protection
without touching this function.

The error the caller sees
------------------------
``ERRCODE = '23514'`` — ``check_violation``, the SQLSTATE a CHECK constraint raises. This is
a deliberate small lie about the mechanism in exchange for a true statement about the
meaning: the rule *is* a check, implemented as a trigger only because CHECK constraints
cannot read ``OLD``. Callers get ``IntegrityError`` from SQLAlchemy rather than the generic
``DBAPIError`` that ``P0001`` (the default ``raise_exception``) produces, so a service layer
handles this failure with the same code path as every other constraint violation, and
nothing has to special-case "the trigger ones". ``DETAIL`` names the columns that tripped
it, which is the part that makes the failure debuggable from a log line.

Ordering against revision 0019, which is safe by construction
-------------------------------------------------------------
Revision 0019 runs ``UPDATE question_bank SET root_question_id = id WHERE root_question_id
IS NULL`` — an UPDATE of a protected column, which this trigger would block on any approved
row. It cannot collide, and the reason is the chain order rather than luck: Alembic
downgrades in reverse, so a downgrade past 0019 drops this trigger first (0020's
``downgrade`` runs before 0019's), and the matching upgrade re-runs 0019's backfill before
0020 recreates the trigger. There is no reachable state in which 0019's DML and this
trigger are both live.

The general form of that hazard is worth stating for whoever writes revision 0024 or later:
**any future data migration that touches a protected column on approved rows must either
sort before 0020 — which is impossible — or drop and recreate this trigger around its own
work.** ``ALTER TABLE question_bank DISABLE TRIGGER
trg_question_bank_reject_approved_edit`` is the maintenance-window escape hatch; it requires
table ownership, which is the point.

Known residue, reported rather than resolved
--------------------------------------------
* **A wrongly-unrooted approved row can no longer be repaired by a backfill.**
  ``root_question_id`` is protected, so if a repository bug ever commits an approved row
  with a NULL root, no ``UPDATE`` can fix it while the trigger is live. That is the
  intended trade — allowing ``root_question_id`` to change on approved rows would permit
  re-parenting history, which is worse than needing a maintenance window to fix a bug.
  The escape hatch above is the documented repair path.
* **Nothing prevents an approved row from being reverted to ``draft`` and then edited.**
  ``status`` is mutable by construction (the confirmed decision explicitly allows status
  transitions), so ``UPDATE ... SET status = 'draft'`` followed by an edit defeats the
  rule in two statements. Closing that needs a state-machine trigger on ``status``
  transitions — approved may go to ``archived`` or ``rejected`` but never back to
  ``draft`` — which is a separate decision and is not made here. The trigger raises the
  cost of an in-place edit from "one UPDATE" to "a deliberate two-step downgrade of the
  row's status, in an audit trail"; it does not make it impossible.
* **UPDATEs are the whole scope.** ``TRUNCATE question_bank`` is unaffected (a statement-
  level trigger would be needed) and so is ``DELETE``. Both are already governed by
  ADR-004 and by ``ON DELETE RESTRICT`` on the lineage self-references.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0020"
down_revision: Union[str, Sequence[str], None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: The columns an approved question may still change, as a frozen literal. Frozen, not
#: imported from ``app.models.question``, for the same reason revision 0018 repeats the
#: search-document expression: a migration that imports application code stops describing
#: the schema as it was when the migration was written, which is the one thing a migration
#: is for.
#:
#: ``search_document`` is in the list for a different reason than the other five — see the
#: module docstring. It is a generated column, not a mutable one.
_MUTABLE_ON_APPROVED: tuple[str, ...] = (
    "status",
    "updated_at",
    "row_version",
    "is_current",
    "deleted_at",
    "search_document",
)

_IGNORED_ARRAY_SQL = ", ".join(f"'{column}'" for column in _MUTABLE_ON_APPROVED)

_FUNCTION_NAME = "fn_question_bank_reject_approved_edit"
_TRIGGER_NAME = "trg_question_bank_reject_approved_edit"

#: ``SET search_path`` is pinned even though this function is SECURITY INVOKER and touches
#: no tables: ``to_jsonb``, ``jsonb_each`` and ``string_agg`` are resolved through the path,
#: and pinning it means a caller cannot shadow them with a same-named function in a schema
#: earlier in their own path. Cheap, and it makes the function's behaviour independent of
#: who calls it.
_CREATE_FUNCTION = f"""
CREATE OR REPLACE FUNCTION public.{_FUNCTION_NAME}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $function$
DECLARE
    _ignored text[] := ARRAY[{_IGNORED_ARRAY_SQL}];
    _old     jsonb;
    _new     jsonb;
    _changed text;
BEGIN
    -- Whole-row comparison minus the mutable set, so a column added by a later
    -- revision is protected without editing this function.
    _old := to_jsonb(OLD) - _ignored;
    _new := to_jsonb(NEW) - _ignored;

    -- Collected rather than short-circuited: the caller needs to know which columns
    -- tripped the rule, and one pass gives both the decision and the message.
    -- `IS DISTINCT FROM` because a column set to NULL is jsonb 'null' on both sides,
    -- which must compare equal, while NULL -> value must not.
    SELECT string_agg(o.key, ', ' ORDER BY o.key)
      INTO _changed
      FROM jsonb_each(_old) AS o
     WHERE o.value IS DISTINCT FROM _new -> o.key;

    IF _changed IS NOT NULL THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'question_bank: an approved question is immutable (ADR-003, BR-8)',
            DETAIL  = 'row ' || OLD.id::text || ' has status=approved and this UPDATE '
                      || 'changes: ' || _changed,
            HINT    = 'Do not edit an approved question. Insert a new row sharing its '
                      || 'root_question_id with version_number + 1 and '
                      || 'previous_version_id set to this row, then set is_current=false '
                      || 'on this row (which is permitted).';
    END IF;

    RETURN NEW;
END;
$function$
"""

_CREATE_TRIGGER = f"""
CREATE TRIGGER {_TRIGGER_NAME}
BEFORE UPDATE ON public.question_bank
FOR EACH ROW
WHEN (OLD.status = 'approved')
EXECUTE FUNCTION public.{_FUNCTION_NAME}()
"""


def upgrade() -> None:
    """Upgrade schema.

    Function first, then trigger — ``CREATE TRIGGER`` resolves ``EXECUTE FUNCTION`` at
    creation time and fails if the function does not yet exist.

    Both statements go through ``op.execute`` because neither has an Alembic operation:
    triggers and functions are outside ``Base.metadata`` entirely. That has one useful
    consequence and one cost. The consequence is that ``alembic check`` stays clean —
    autogenerate cannot see these objects, so it cannot report them as drift in either
    direction. The cost is that ``alembic check`` also cannot *protect* them: if someone
    drops this trigger by hand, no drift check notices, and only the behavioural tests in
    ``tests/test_question_immutability.py`` and the catalog assertions in
    ``scripts/verify-p1-t4.ps1`` will catch it. That is why both exist.
    """

    op.execute(_CREATE_FUNCTION)
    op.execute(_CREATE_TRIGGER)


def downgrade() -> None:
    """Downgrade schema.

    Trigger first, then function: dropping the function while a trigger depends on it fails
    without ``CASCADE``, and ``CASCADE`` here would be a way of not knowing what else got
    dropped.

    ``IF EXISTS`` on both, so a downgrade from a partially-applied upgrade completes. There
    is no data to reverse — the trigger has no state of its own, and rows it rejected were
    never written.
    """

    op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER_NAME} ON public.question_bank")
    op.execute(f"DROP FUNCTION IF EXISTS public.{_FUNCTION_NAME}()")
