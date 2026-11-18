"""enforce BR-6 separation of duties on approvals with a BEFORE INSERT trigger (report item 5)

Revision ID: 0021
Revises: 0020
Create Date: 2026-08-19

Not a doc 03 §9 step — see revision 0020's docstring for why the chain stops mapping onto
§9 here. This implements the confirmed decision on report item 5.

What this closes
----------------
**BR-6, separation of duties: a paper's author must not be its approver.** Report item 5
recorded that this was unenforceable at schema level as doc 03 specifies the tables, and the
reason is the same shape as item 1's but across tables rather than across row versions: the
author is ``generated_papers.created_by`` and the approver is ``approvals.reviewer_id``, and
a CHECK constraint may only reference columns of its own row. ``reviewer_id <> created_by``
spans two tables, so CHECK cannot express it. The alternatives are a trigger or
denormalizing ``created_by`` onto ``approvals`` — which would put the same fact in two
places and need its own trigger to keep them agreeing. Trigger it is.

``tests/test_operations_schema.py::test_the_database_permits_self_approval`` measured this
gap deliberately and said so: *"If a future migration adds the trigger, this test will fail
— and that failure is the correct signal, not a regression."* This is that migration. That
test is replaced in this revision's companion commit by
``tests/test_approval_separation_of_duties.py``, which asserts the opposite outcome.

The column is ``reviewer_id``, not ``actor_id``
----------------------------------------------
The confirmed decision was written against ``NEW.actor_id``. There is no such column:
doc 03 §5 line 946 specifies ``reviewer_id UUID, nullable`` and revision 0011 created it
under that name. Implemented against ``reviewer_id``. Nothing else about the rule changes,
and no column is renamed — a rename would break revision 0011's DDL, the ``Approval`` model,
two existing indexes (``ix_approvals_reviewer_id_created_at``) and every test that already
inserts a trail.

The rule applies to decisions only, not to the whole trail
---------------------------------------------------------
``approvals`` is a *workflow trail*, and ``action`` admits four values (revision 0011,
``ck_approvals_action``): ``submit``, ``approve``, ``reject``, ``comment``. The confirmed
decision named no action and would, read literally, block every one of them.

**That would make it impossible for an author to submit their own paper.** The author is
precisely the person who submits — a paper reaching ``status = 'submitted'`` with a trail
row written by anyone else describes a workflow the product does not have — and commenting
on one's own draft is ordinary. A blanket rule would not enforce separation of duties; it
would break the submission step and leave the approval step as the only thing that still
worked by accident.

BR-6 is about the *decision*, so the trigger fires on ``action IN ('approve', 'reject')``
and nothing else. ``reject`` is included on the conservative reading that a decision on
one's own work is what "separation of duties" excludes, in either direction. If it should
be narrowed to self-*approval* alone — self-rejection is not a privilege escalation, and an
author withdrawing their own paper is a plausible flow — that is a one-value edit to the
``WHEN`` clause in :data:`_CREATE_TRIGGER` and a new revision. Flagged rather than assumed.

Why SECURITY DEFINER, and why this is not the usual overreach
------------------------------------------------------------
The function reads ``public.generated_papers`` to find the author. Under the row-level
security P1-T5 will add, a trigger function running as the *invoking* user sees only rows
that user's policies admit — and a subselect that finds no row returns NULL, so
``NEW.reviewer_id = NULL`` evaluates to NULL, the ``IF`` does not fire, and **the check
passes silently**. A security control that quietly stops working when RLS is switched on is
worse than no control, because the tests written today would still pass.

``SECURITY DEFINER`` makes the function run as its owner — the role that ran this migration,
which owns ``generated_papers`` — and a table's owner bypasses that table's RLS policies. So
the lookup keeps working after P1-T5, and the check cannot be defeated by narrowing the
caller's visibility.

Two consequences to carry forward, both real:

* **P1-T5 must not set ``FORCE ROW LEVEL SECURITY`` on ``generated_papers``** without
  revisiting this function. ``FORCE`` is the one setting that makes RLS apply to the owner
  as well, which would reintroduce the silent-pass hole. If ``FORCE`` is wanted, this
  function needs an owner with ``BYPASSRLS`` instead.
* ``SET search_path = pg_catalog, public`` is **mandatory**, not tidiness. A
  ``SECURITY DEFINER`` function with a mutable search path is the textbook PostgreSQL
  privilege-escalation vector: any caller who can create a schema earlier in the path can
  shadow ``generated_papers`` with their own table and have it read as the owner. The table
  reference below is schema-qualified as well, so the protection is doubled rather than
  load-bearing in one place.

The lookup is deliberately not ``STRICT`` about a missing paper
--------------------------------------------------------------
``PERFORM``-style "no row found" is treated as *not a violation* and the trigger returns
``NEW``. That looks lax and is not: ``approvals.paper_id`` has a foreign key to
``generated_papers.id``, and PostgreSQL checks foreign keys *after* ``BEFORE`` row triggers
run. So at the moment this function executes, a nonexistent ``paper_id`` has not been
rejected yet. Raising here would replace the FK's precise error
(``violates foreign key constraint "fk_approvals_paper_id_generated_papers"``) with a
confusing complaint about separation of duties. Letting it through hands the row to the
constraint that actually owns that failure.

Known residue, reported rather than resolved
--------------------------------------------
* **A NULL ``reviewer_id`` bypasses the rule entirely**, and the ``WHEN`` clause says so out
  loud (``NEW.reviewer_id IS NOT NULL``) rather than leaving it to three-valued logic. doc
  03 makes ``reviewer_id`` nullable, so an unattributed approval is a legal row and BR-6
  cannot be evaluated for it. The fix is not a trigger — it is a CHECK constraint requiring
  ``reviewer_id IS NOT NULL`` when ``action IN ('approve','reject')``, which is expressible
  because both columns are on the same row. That is a new constraint nobody has confirmed,
  so it is flagged here and not added.
* **``UPDATE`` is not covered.** This is ``BEFORE INSERT``, as specified. ``approvals`` is
  append-only "by construction" only in the weak sense that it has no ``row_version`` or
  ``deleted_at`` — nothing actually prevents ``UPDATE approvals SET reviewer_id = <author>``
  from defeating the rule in one statement after the fact. Closing it is the two-word change
  ``BEFORE INSERT OR UPDATE`` in :data:`_CREATE_TRIGGER`; it is not made here because
  whether ``approvals`` should be genuinely append-only (a trigger that rejects all UPDATEs
  and DELETEs, per CN-22) is a broader decision than BR-6 and belongs with the append-only
  question, not this one.
* **One person can still be the entire workflow minus one step.** BR-6 as implemented says
  the author is not the approver. It does not say two *different* approvals must come from
  two different people, nor does it know about roles — a second author in the same
  organization can approve everything. Anything stronger needs the RBAC vocabulary from
  doc 03 §7.2, which is P1-T7's.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0021"
down_revision: Union[str, Sequence[str], None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: The two actions BR-6 governs, as a frozen literal. ``submit`` and ``comment`` are
#: deliberately absent — see the module docstring.
_DECISION_ACTIONS: tuple[str, ...] = ("approve", "reject")

_DECISION_ACTIONS_SQL = ", ".join(f"'{action}'" for action in _DECISION_ACTIONS)

_FUNCTION_NAME = "fn_approvals_reject_self_approval"
_TRIGGER_NAME = "trg_approvals_reject_self_approval"

_CREATE_FUNCTION = f"""
CREATE OR REPLACE FUNCTION public.{_FUNCTION_NAME}()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $function$
DECLARE
    _author uuid;
BEGIN
    SELECT p.created_by
      INTO _author
      FROM public.generated_papers AS p
     WHERE p.id = NEW.paper_id;

    -- No paper: let the foreign key on paper_id produce the error that belongs to it.
    IF NOT FOUND THEN
        RETURN NEW;
    END IF;

    IF NEW.reviewer_id = _author THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'approvals: a paper''s author may not decide on their own paper (BR-6)',
            DETAIL  = 'paper ' || NEW.paper_id::text || ' was created by user '
                      || _author::text || ', which is the reviewer_id on this '
                      || quote_literal(NEW.action) || ' row.',
            HINT    = 'Separation of duties requires a different reviewer for approve and '
                      || 'reject. The author may still submit and comment on their own '
                      || 'paper.';
    END IF;

    RETURN NEW;
END;
$function$
"""

#: The ``WHEN`` clause carries both narrowings, so ``pg_get_triggerdef`` states the rule's
#: exact scope — including the NULL bypass, which is a documented gap rather than an
#: accident of three-valued logic.
_CREATE_TRIGGER = f"""
CREATE TRIGGER {_TRIGGER_NAME}
BEFORE INSERT ON public.approvals
FOR EACH ROW
WHEN (NEW.reviewer_id IS NOT NULL AND NEW.action IN ({_DECISION_ACTIONS_SQL}))
EXECUTE FUNCTION public.{_FUNCTION_NAME}()
"""


def upgrade() -> None:
    """Upgrade schema.

    Function before trigger; ``EXECUTE FUNCTION`` is resolved at ``CREATE TRIGGER`` time.

    Invisible to ``alembic check`` in both directions, exactly as revision 0020's trigger
    is — which is why ``tests/test_approval_separation_of_duties.py`` and the catalog
    assertions in ``scripts/verify-p1-t4.ps1`` are the only things that can notice this
    object going missing.
    """

    op.execute(_CREATE_FUNCTION)
    op.execute(_CREATE_TRIGGER)


def downgrade() -> None:
    """Downgrade schema.

    Trigger before function, ``IF EXISTS`` on both, no ``CASCADE`` — see revision 0020's
    downgrade for the reasoning. Rows this trigger rejected were never written, so there is
    no data change to reverse.
    """

    op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER_NAME} ON public.approvals")
    op.execute(f"DROP FUNCTION IF EXISTS public.{_FUNCTION_NAME}()")
