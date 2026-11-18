"""Revision 0021: BR-6 separation of duties, enforced in the database.

BR-6 says a paper's author must not be the person who decides on it. Until revision 0021
that was unenforceable at schema level and the suite said so out loud —
``test_operations_schema.py`` carried a test named
``test_the_database_permits_self_approval`` whose docstring read *"If a future migration
adds the trigger, this test will fail — and that failure is the correct signal, not a
regression."* Revision 0021 is that migration, and this file is what replaced that test.

Why a trigger at all
--------------------
The author is ``generated_papers.created_by``; the decider is ``approvals.reviewer_id``.
A CHECK constraint may only reference columns of its own row, so ``reviewer_id <>
created_by`` is not expressible as one — it spans two tables. The alternatives were a
trigger or denormalizing ``created_by`` onto ``approvals``, which would put the same fact
in two places and then need a trigger of its own to keep them agreeing.

Why the rule is narrowed to ``approve`` and ``reject``
-----------------------------------------------------
``approvals`` is a workflow *trail*, not a decision table: ``action`` admits ``submit``,
``approve``, ``reject`` and ``comment``. The confirmed decision named no action, and read
literally it would have blocked all four — which would make it **impossible for an author
to submit their own paper**, since the author is precisely the person who submits. That
would not enforce separation of duties; it would break submission and leave approval
working by accident.

So the trigger's ``WHEN`` clause carries the narrowing, and the tests below are paired
accordingly: the author is refused on ``approve`` and ``reject``, and permitted on
``submit`` and ``comment``. Two of these four tests exist specifically so that a future
widening of the rule fails loudly instead of quietly breaking the submit step.

The gaps are measured, not hidden
---------------------------------
Three tests here assert that PostgreSQL **accepts** something BR-6 arguably should
forbid — a NULL ``reviewer_id``, an ``UPDATE`` that rewrites a decision's reviewer after
the fact, and an approval by someone with no role or membership anywhere near the paper.
They are the same species as the test this file replaced: known limits, pinned in
executable form so that nobody reads doc 03's prose and assumes the database is doing more
than it is. Each says so in its own docstring and each is reported in the P1-T4 decisions
log.

Requirements are the same as every other database module here: PostgreSQL reachable at
``TEST_DATABASE_URL`` with ``alembic upgrade head`` applied. The fixtures fail with
instructions rather than skipping, because a skipped database test looks like a pass.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.models import Approval, GeneratedPaper, User

pytestmark = pytest.mark.db

#: Revision 0021 raises ``23514`` (``check_violation``) for the same reason revision 0020
#: does: it makes SQLAlchemy classify the failure as :class:`IntegrityError`, so a service
#: layer handles it beside every other constraint violation instead of special-casing the
#: generic ``DBAPIError`` that plpgsql's default ``P0001`` would produce.
BR6_SQLSTATE = "23514"

#: ``foreign_key_violation`` — expected from the one test that inserts an approval
#: pointing at no paper. See :func:`test_a_missing_paper_fails_on_the_foreign_key`.
FK_SQLSTATE = "23503"

TRIGGER_NAME = "trg_approvals_reject_self_approval"
FUNCTION_NAME = "fn_approvals_reject_self_approval"
PAPER_FK_NAME = "fk_approvals_paper_id_generated_papers"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _paper(tenant, curriculum, **overrides) -> GeneratedPaper:
    """A valid paper owned by ``tenant``'s organization and authored by its user.

    A local copy rather than an import from ``test_operations_schema.py``, matching the
    convention across the database modules: each must be runnable on its own.
    """

    values = dict(
        org_id=tenant.org_id,
        created_by=tenant.user_id,
        curriculum_version_id=curriculum.curriculum_version_id,
        title="Unit Test 2 - Polynomials",
        blueprint={
            "total_marks": 20,
            "sections": [{"name": "A", "qtype": "mcq", "count": 5, "marks_each": 1}],
        },
        total_marks=Decimal("20.0"),
        duration_min=45,
    )
    values.update(overrides)
    return GeneratedPaper(**values)


async def _committed_paper(db_session, tenant, curriculum) -> uuid.UUID:
    """A paper committed inside the test's transaction, authored by ``tenant.user_id``.

    Committed rather than merely flushed because the trigger reads ``generated_papers``
    with its own ``SELECT``. A flush would be enough — the statement runs in the same
    transaction and sees uncommitted rows — but committing means the assertions below are
    about a row that survived a savepoint release, which removes one way for a passing
    test to be measuring nothing.
    """

    paper = _paper(tenant, curriculum)
    db_session.add(paper)
    await db_session.flush()
    paper_id = paper.id
    await db_session.commit()
    return paper_id


async def _committed_reviewer(db_session, email: str) -> uuid.UUID:
    """A second user, so "someone other than the author" is an actual person."""

    reviewer = User(email=email, full_name="Head of Mathematics")
    db_session.add(reviewer)
    await db_session.flush()
    reviewer_id = reviewer.id
    await db_session.commit()
    return reviewer_id


async def _insert_approval(
    db_session,
    paper_id: uuid.UUID,
    action: str,
    reviewer_id: uuid.UUID | None,
) -> None:
    """Insert one trail row through raw SQL.

    Raw SQL for the same reason ``test_question_immutability.py`` uses it: the rule has to
    hold against every writer, not just SQLAlchemy. A trigger tested only through the ORM
    proves the weaker claim, and BR-6 is a control whose whole value is that it also
    applies to a ``psql`` session and to whatever the P1-T5 repository layer turns out to
    emit.
    """

    reviewer = "NULL" if reviewer_id is None else f"CAST('{reviewer_id}' AS uuid)"
    await db_session.execute(
        sa.text(
            "INSERT INTO approvals (paper_id, reviewer_id, action) "
            f"VALUES (CAST(:pid AS uuid), {reviewer}, :action)"
        ),
        {"pid": str(paper_id), "action": action},
    )


async def _self_approval_count(db_session, paper_id: uuid.UUID) -> int:
    """How many decisions on this paper were made by its own author.

    The join BR-6 is about, expressed once. Every test that claims a self-approval was
    prevented — or, in the gap tests, was *not* prevented — asserts on this number rather
    than inferring it from whether an exception was raised, because "the INSERT failed"
    and "no self-approval exists" are different statements and only the second is BR-6.
    """

    result = await db_session.execute(
        sa.text(
            "SELECT count(*) FROM approvals AS a "
            "  JOIN generated_papers AS p ON p.id = a.paper_id "
            " WHERE a.paper_id = CAST(:pid AS uuid) "
            "   AND a.reviewer_id = p.created_by "
            "   AND a.action IN ('approve', 'reject')"
        ),
        {"pid": str(paper_id)},
    )
    return int(result.scalar_one())


def _error_message(error) -> str:
    """asyncpg's own rendering of the failure — ``MESSAGE``, ``DETAIL``, ``HINT``.

    **Deliberately ``str(error.orig)`` and not ``str(error)``.** SQLAlchemy's
    ``DBAPIError.__str__`` appends the failing statement, so an assertion made against it
    can be satisfied by the *statement* rather than by anything the server said — a test
    checking that ``DETAIL`` names a particular user id would pass merely because the
    INSERT mentioned that id. ``error.orig`` carries only the server's message, which is
    the layer these assertions are actually about.
    """

    return str(error.orig)


def _assert_br6_violation(exc_info, paper_id, author_id, action: str) -> None:
    """Assert this is revision 0021's rejection, and that it identifies the parties.

    The SQLSTATE is checked separately from the message because they fail independently:
    a rule that raised ``P0001`` would still block the INSERT while giving callers an
    unclassifiable error, and a rule that raised ``23514`` from the wrong place would be
    classified correctly while meaning something else. The paper id, the author id and the
    action are asserted because an error naming none of them tells an operator that *a*
    self-approval was refused without saying whose — which for an audit control is most of
    the value gone.
    """

    error = exc_info.value
    assert error.orig.sqlstate == BR6_SQLSTATE, (
        f"expected SQLSTATE {BR6_SQLSTATE} (check_violation) so that callers see an "
        f"IntegrityError; got {error.orig.sqlstate!r}"
    )

    message = _error_message(error)
    assert "may not decide on their own paper" in message, (
        "the rejection did not come from revision 0021's trigger: " + message
    )
    assert "BR-6" in message, message

    assert "DETAIL:" in message, (
        "no DETAIL section, so the error does not say which paper or which user: "
        + message
    )
    detail = message.split("DETAIL:", 1)[1].split("HINT:", 1)[0]
    assert str(paper_id) in detail, detail
    assert str(author_id) in detail, detail
    assert f"'{action}'" in detail, detail


# ---------------------------------------------------------------------------
# The rule bites
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("action", ["approve", "reject"])
async def test_the_author_cannot_decide_on_their_own_paper(
    db_session, tenant, curriculum, action
):
    """The headline claim, for both decision actions.

    ``approve`` is the case BR-6 is written about. ``reject`` is included on the
    conservative reading that a *decision* on one's own work is what separation of duties
    excludes, in either direction — and it is worth being explicit that this is a judgment
    call rather than something doc 03 settles. Self-rejection is not a privilege
    escalation, and an author withdrawing their own paper is a plausible flow, so if this
    parametrisation is ever removed the change belongs in the ``WHEN`` clause of a new
    revision and in the decisions log, not in a quiet edit to this list.

    The second assertion is the one that makes the test about BR-6 rather than about
    exceptions: after the rollback, the join that defines a self-approval finds nothing.
    """

    paper_id = await _committed_paper(db_session, tenant, curriculum)

    with pytest.raises(IntegrityError) as exc_info:
        await _insert_approval(db_session, paper_id, action, tenant.user_id)
    _assert_br6_violation(exc_info, paper_id, tenant.user_id, action)
    await db_session.rollback()

    assert await _self_approval_count(db_session, paper_id) == 0


async def test_the_orm_cannot_route_around_the_rule(db_session, tenant, curriculum):
    """The same rejection through SQLAlchemy, which is not a redundant restatement.

    It settles the question P1-T5 will actually ask: can a repository method
    ``session.add`` an approval without going through whatever service-layer check gets
    written, and have it land? It cannot. The database is the backstop, so the service
    check becomes a source of better error messages rather than the only thing standing
    between an author and their own approval.
    """

    paper_id = await _committed_paper(db_session, tenant, curriculum)

    db_session.add(
        Approval(paper_id=paper_id, reviewer_id=tenant.user_id, action="approve")
    )
    with pytest.raises(IntegrityError) as exc_info:
        await db_session.flush()
    _assert_br6_violation(exc_info, paper_id, tenant.user_id, "approve")
    await db_session.rollback()

    assert await _self_approval_count(db_session, paper_id) == 0


# ---------------------------------------------------------------------------
# The rule does not over-reach
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("action", ["submit", "comment"])
async def test_the_author_can_still_submit_and_comment_on_their_own_paper(
    db_session, tenant, curriculum, action
):
    """The narrowing, asserted — and this is the test that protects the product.

    A blanket rule on all four actions would have made ``submit`` impossible for the one
    person who is always the submitter, so a paper could never reach
    ``status = 'submitted'`` at all. Commenting on one's own draft is ordinary in the same
    way.

    If someone later widens the ``WHEN`` clause to "any action", these two parametrisations
    are what fail, immediately and with an obvious cause, instead of the review workflow
    breaking in staging a week later.
    """

    paper_id = await _committed_paper(db_session, tenant, curriculum)

    await _insert_approval(db_session, paper_id, action, tenant.user_id)
    await db_session.commit()

    written = await db_session.execute(
        sa.text(
            "SELECT count(*) FROM approvals "
            " WHERE paper_id = CAST(:pid AS uuid) "
            "   AND reviewer_id = CAST(:uid AS uuid) "
            "   AND action = :action"
        ),
        {"pid": str(paper_id), "uid": str(tenant.user_id), "action": action},
    )
    assert written.scalar_one() == 1


async def test_a_different_reviewer_can_approve(db_session, tenant, curriculum):
    """The workflow BR-6 exists to require actually works end to end.

    Author submits, someone else approves. Without this test the suite could pass with a
    trigger that rejected *every* approval — the negative tests would be satisfied and
    nothing would notice that approval had become impossible, which is the failure mode a
    security control reaches for on its own.
    """

    paper_id = await _committed_paper(db_session, tenant, curriculum)
    reviewer_id = await _committed_reviewer(db_session, "hod.maths@example.com")

    await _insert_approval(db_session, paper_id, "submit", tenant.user_id)
    await _insert_approval(db_session, paper_id, "approve", reviewer_id)
    await db_session.commit()

    trail = await db_session.execute(
        sa.text(
            "SELECT action, reviewer_id FROM approvals "
            " WHERE paper_id = CAST(:pid AS uuid) ORDER BY action"
        ),
        {"pid": str(paper_id)},
    )
    rows = [(action, str(reviewer)) for action, reviewer in trail]
    assert rows == [
        ("approve", str(reviewer_id)),
        ("submit", str(tenant.user_id)),
    ]
    assert await _self_approval_count(db_session, paper_id) == 0


async def test_a_missing_paper_fails_on_the_foreign_key(db_session, tenant, curriculum):
    """"No paper found" is handed to the foreign key, not claimed by the trigger.

    This looks like laxity in the function and is not. ``approvals.paper_id`` has a
    foreign key to ``generated_papers.id``, and PostgreSQL checks foreign keys **after**
    ``BEFORE`` row triggers run — so when the function executes, a nonexistent
    ``paper_id`` has not been rejected yet and its lookup legitimately finds nothing.
    Raising a separation-of-duties error there would replace the FK's precise complaint
    with a confusing one about BR-6.

    Both halves are asserted: the FK's SQLSTATE and constraint name are present, and the
    BR-6 wording is **absent**. The second assertion is the one that would catch someone
    "hardening" the function by raising on ``NOT FOUND``.
    """

    orphan_paper_id = uuid.uuid4()
    reviewer_id = await _committed_reviewer(db_session, "orphan.reviewer@example.com")

    with pytest.raises(IntegrityError) as exc_info:
        await _insert_approval(db_session, orphan_paper_id, "approve", reviewer_id)

    message = _error_message(exc_info.value)
    assert exc_info.value.orig.sqlstate == FK_SQLSTATE, (
        f"expected the foreign key to own this failure ({FK_SQLSTATE}); got "
        f"{exc_info.value.orig.sqlstate!r}: {message}"
    )
    assert PAPER_FK_NAME in message, message
    assert "BR-6" not in message, (
        "the trigger claimed a failure that belongs to the foreign key, which makes a "
        "bad paper_id look like a separation-of-duties problem: " + message
    )
    await db_session.rollback()


# ---------------------------------------------------------------------------
# Known gaps, measured rather than assumed
# ---------------------------------------------------------------------------


async def test_a_null_reviewer_id_bypasses_the_rule(db_session, tenant, curriculum):
    """**Measuring a gap.** An unattributed decision is accepted.

    doc 03 §5 line 946 makes ``reviewer_id`` nullable, so an approval that names nobody is
    a legal row and BR-6 simply cannot be evaluated for it. The trigger's ``WHEN`` clause
    states this out loud (``NEW.reviewer_id IS NOT NULL``) rather than leaving it to
    three-valued logic, because a rule that silently no-ops on NULL is indistinguishable
    from a rule that is broken.

    The fix is not a trigger. It is a CHECK constraint requiring ``reviewer_id IS NOT
    NULL`` when ``action IN ('approve', 'reject')`` — expressible, because both columns
    are on the same row — and it is not added because nobody has confirmed it. Reported in
    the decisions log; if it is added later, this test should fail and be replaced.
    """

    paper_id = await _committed_paper(db_session, tenant, curriculum)

    await _insert_approval(db_session, paper_id, "approve", None)
    await db_session.commit()

    unattributed = await db_session.execute(
        sa.text(
            "SELECT count(*) FROM approvals "
            " WHERE paper_id = CAST(:pid AS uuid) "
            "   AND action = 'approve' AND reviewer_id IS NULL"
        ),
        {"pid": str(paper_id)},
    )
    assert unattributed.scalar_one() == 1, (
        "a NULL reviewer_id was rejected, so either the WHEN clause changed or this test "
        "is no longer measuring the gap it claims to measure"
    )


async def test_an_update_can_still_rewrite_a_decision_reviewer(
    db_session, tenant, curriculum
):
    """**Measuring a gap.** The trigger is ``BEFORE INSERT``, so ``UPDATE`` defeats it.

    A different reviewer approves, and then one ``UPDATE approvals SET reviewer_id =
    <author>`` produces exactly the state BR-6 forbids. ``approvals`` is append-only only
    in the weak sense that it carries no ``row_version`` and no ``deleted_at``; nothing
    actually prevents the statement.

    Closing this is the two-word change ``BEFORE INSERT OR UPDATE`` in revision 0021's
    trigger definition. It is not made because whether ``approvals`` should be *genuinely*
    append-only — a trigger rejecting all UPDATEs and DELETEs, per CN-22 — is a broader
    decision than BR-6 and belongs with the append-only question. This test is the
    executable record of what is still open, and it is the direct successor to the
    ``test_the_database_permits_self_approval`` test it replaced: same shape, narrower gap.
    """

    paper_id = await _committed_paper(db_session, tenant, curriculum)
    reviewer_id = await _committed_reviewer(db_session, "rewritable@example.com")

    await _insert_approval(db_session, paper_id, "approve", reviewer_id)
    await db_session.commit()
    assert await _self_approval_count(db_session, paper_id) == 0

    await db_session.execute(
        sa.text(
            "UPDATE approvals SET reviewer_id = CAST(:author AS uuid) "
            " WHERE paper_id = CAST(:pid AS uuid) AND action = 'approve'"
        ),
        {"author": str(tenant.user_id), "pid": str(paper_id)},
    )
    await db_session.commit()

    assert await _self_approval_count(db_session, paper_id) == 1, (
        "the UPDATE was blocked, so revision 0021's trigger now covers UPDATE and this "
        "gap test should be replaced with one asserting the rejection"
    )


async def test_any_other_user_can_approve_regardless_of_role_or_membership(
    db_session, tenant, curriculum
):
    """**Measuring a gap.** BR-6 is about one person, not about roles or membership.

    The rule as implemented says the author is not the approver. It does not say the
    approver is *entitled* to approve — so the user below, who holds no membership in the
    paper's organization at all, let alone a reviewing role, approves it without
    complaint. Anything stronger needs the RBAC vocabulary from doc 03 §7.2, which is
    P1-T7's, plus an approval policy nobody has specified.

    Worth being precise about why this is reported rather than fixed: expressing "an
    approver must hold role X in org Y" would mean consulting ``memberships`` from inside
    the trigger, which makes every approval INSERT depend on the RBAC tables and buries an
    authorization policy where no one will look for it. The ``LEFT JOIN`` below is the
    assertion's whole point — it proves the approver's membership row is *absent*, so the
    test measures the gap rather than assuming it.
    """

    paper_id = await _committed_paper(db_session, tenant, curriculum)
    outsider_id = await _committed_reviewer(db_session, "no.membership@example.com")

    await _insert_approval(db_session, paper_id, "approve", outsider_id)
    await db_session.commit()

    approved_by_outsider = await db_session.execute(
        sa.text(
            "SELECT count(*) FROM approvals AS a "
            "  JOIN generated_papers AS p ON p.id = a.paper_id "
            "  LEFT JOIN memberships AS m "
            "    ON m.user_id = a.reviewer_id AND m.org_id = p.org_id "
            " WHERE a.paper_id = CAST(:pid AS uuid) "
            "   AND a.reviewer_id = CAST(:uid AS uuid) "
            "   AND m.id IS NULL"
        ),
        {"pid": str(paper_id), "uid": str(outsider_id)},
    )
    assert approved_by_outsider.scalar_one() == 1, (
        "either the approval was rejected or the approver now has a membership in the "
        "paper's organization; in both cases this gap test is stale"
    )


# ---------------------------------------------------------------------------
# The objects are actually there, enabled, and privileged as intended
# ---------------------------------------------------------------------------


async def test_the_trigger_exists_enabled_and_scoped_to_decisions(db_session):
    """Read the trigger back out of ``pg_trigger``, including ``tgenabled``.

    Duplicated from ``scripts/verify-p1-t4.ps1`` on purpose: the script runs at
    checkpoints, this runs on every commit, and triggers are the one kind of object
    ``alembic check`` is blind to in **both** directions — a dropped trigger is not
    reported as drift, so BR-6 could quietly stop being enforced with every other test
    still green.

    ``tgenabled`` matters as much as existence. ``ALTER TABLE ... DISABLE TRIGGER`` leaves
    the definition in the catalog and completely inert, so a test that checked only for
    presence would be satisfied by a permanently disabled control.

    The ``WHEN`` clause is asserted for both what it contains and what it does **not**:
    ``submit`` and ``comment`` must be absent, because their presence would mean the rule
    has been widened to break the submission step. PostgreSQL normalises ``IN (...)`` to
    ``= ANY (ARRAY[...])``, so the assertions are on the literals rather than the operator.
    """

    row = (
        await db_session.execute(
            sa.text(
                "SELECT t.tgenabled::text, pg_get_triggerdef(t.oid) "
                "  FROM pg_trigger AS t "
                "  JOIN pg_class AS c ON c.oid = t.tgrelid "
                " WHERE NOT t.tgisinternal "
                "   AND c.relname = 'approvals' "
                f"   AND t.tgname = '{TRIGGER_NAME}'"
            )
        )
    ).one_or_none()

    assert row is not None, (
        f"{TRIGGER_NAME} is missing from approvals. alembic check cannot see this, so "
        "nothing else in the suite would have noticed: BR-6 is currently unenforced."
    )

    enabled, definition = row
    assert enabled == "O", (
        f"{TRIGGER_NAME} exists but tgenabled is {enabled!r}, not 'O' — it is in the "
        "catalog and not firing, which is the worst of both states"
    )

    lowered = definition.lower()
    assert "before insert" in lowered, definition
    assert "for each row" in lowered, definition
    assert "new.reviewer_id is not null" in lowered, (
        "the NULL bypass is no longer stated in the WHEN clause; if it was closed by a "
        "CHECK constraint, test_a_null_reviewer_id_bypasses_the_rule should have failed "
        "first: " + definition
    )
    assert "'approve'" in lowered and "'reject'" in lowered, definition
    assert "'submit'" not in lowered and "'comment'" not in lowered, (
        "the rule has been widened past the two decision actions, which makes it "
        "impossible for an author to submit their own paper: " + definition
    )
    assert FUNCTION_NAME in lowered, definition


async def test_the_trigger_function_is_security_definer_with_a_pinned_search_path(
    db_session,
):
    """``prosecdef`` and ``proconfig``, asserted from ``pg_proc``. Both are load-bearing.

    **``SECURITY DEFINER`` is what makes this rule survive P1-T5.** The function reads
    ``generated_papers`` to find the author. Running as the *invoking* user under
    row-level security, it would see only the rows that user's policies admit — and a
    subselect that finds no row yields NULL, ``NEW.reviewer_id = NULL`` is NULL, the ``IF``
    does not fire, and **the check passes silently**. A control that quietly stops working
    when RLS is switched on is worse than no control, because the tests written today
    would still be green. Running as the owner, which bypasses that table's policies,
    keeps the lookup honest.

    **The pinned ``search_path`` is what stops that becoming a vulnerability.** A
    ``SECURITY DEFINER`` function with a mutable search path is the textbook PostgreSQL
    privilege-escalation vector: a caller who can create a schema earlier in the path can
    shadow ``generated_papers`` with a table of their own and have it read with the
    owner's privileges. The reference inside the function is schema-qualified as well, so
    the protection is doubled rather than resting on one line — but this assertion is what
    keeps the second layer from being removed as redundant.

    One consequence to carry into P1-T5, and the reason this test's failure message says
    so: ``FORCE ROW LEVEL SECURITY`` on ``generated_papers`` would apply RLS to the owner
    too and reintroduce the silent-pass hole. If ``FORCE`` is wanted, this function needs
    an owner with ``BYPASSRLS``.
    """

    row = (
        await db_session.execute(
            sa.text(
                "SELECT p.prosecdef, array_to_string(p.proconfig, '|') "
                "  FROM pg_proc AS p "
                "  JOIN pg_namespace AS n ON n.oid = p.pronamespace "
                f" WHERE n.nspname = 'public' AND p.proname = '{FUNCTION_NAME}'"
            )
        )
    ).one_or_none()

    assert row is not None, f"public.{FUNCTION_NAME} is missing"
    security_definer, config = row

    assert security_definer is True, (
        f"public.{FUNCTION_NAME} is SECURITY INVOKER. Its lookup into generated_papers "
        "will return no row under P1-T5's RLS, NEW.reviewer_id = NULL is NULL, and BR-6 "
        "will pass silently for every self-approval."
    )
    assert config is not None, (
        f"public.{FUNCTION_NAME} is SECURITY DEFINER with no pinned search_path, which "
        "is a privilege-escalation vector: a caller who can create a schema earlier in "
        "the path can shadow generated_papers and have it read as the owner."
    )
    assert "search_path=" in config, config
    assert "pg_catalog" in config and "public" in config, config
