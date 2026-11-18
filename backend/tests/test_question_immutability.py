"""Revision 0020: an approved question is immutable, and now the database says so.

The claim under test is ADR-003's, and until revision 0020 nothing in PostgreSQL
enforced it. ``tests/test_question_lineage.py`` proves that *forking* preserves history;
this file proves the other half — that the UPDATE the fork exists to replace is actually
**refused**. The two are not the same claim, and the second one is the one that survives
a ``psql`` session, a seed script, an admin panel, or a repository method someone forgot
to route through the fork path.

Why the negative tests are the point
------------------------------------
A trigger is the one kind of schema object that ``alembic check`` cannot see. Autogenerate
does not model triggers or functions, so it reports no drift when one is present and no
drift when one is **missing** — which means if
``trg_question_bank_reject_approved_edit`` were dropped by hand, or never created because
a revision was skipped, every other test in this suite would still pass. The behavioural
assertions below and the catalog assertions in ``scripts/verify-p1-t4.ps1`` are the only
things standing between "the rule exists" and "the rule is believed to exist".

The positive tests are equally load-bearing
-------------------------------------------
Over-blocking is the failure mode a trigger like this reaches for on its own. Revision
0020's mutable set is ``status``, ``updated_at``, ``row_version``, ``is_current``,
``deleted_at`` and ``search_document``, and three of those exist because blocking them
would break something the rule is *for*:

* ``is_current`` — forking requires demoting the approved row (the partial unique index
  ``uq_question_bank_root_question_id_current`` permits exactly one current version per
  lineage). Block it and BR-8 versioning becomes unimplementable.
* ``deleted_at`` — ADR-004 forbids hard deletes, and this trigger does not fire on
  ``DELETE``. Block ``deleted_at`` and hard ``DELETE`` becomes the only way to retire an
  approved question.
* ``search_document`` — a ``GENERATED ALWAYS ... STORED`` column, and PostgreSQL computes
  generated columns *after* ``BEFORE`` row triggers. ``NEW.search_document`` is therefore
  not the value the row will end up with, so comparing it would make every UPDATE on an
  approved row look like a content edit, including a bare status transition.

So the tests are paired throughout: for each thing the trigger must refuse there is a
neighbouring thing it must permit.

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

from app.models import PromptVersion, QuestionBank

pytestmark = pytest.mark.db

#: The SQLSTATE revision 0020 raises. ``23514`` is ``check_violation`` — the code a CHECK
#: constraint raises — chosen over plpgsql's default ``P0001`` (``raise_exception``) so
#: that SQLAlchemy classifies the failure as :class:`IntegrityError` rather than a bare
#: ``DBAPIError``. Every ``pytest.raises(IntegrityError)`` below is therefore an assertion
#: about the SQLSTATE as much as about the rejection: ``P0001`` maps to asyncpg's
#: ``RaiseError``, which is *not* an ``IntegrityConstraintViolationError``, so these tests
#: would not catch it.
IMMUTABILITY_SQLSTATE = "23514"

TRIGGER_NAME = "trg_question_bank_reject_approved_edit"
FUNCTION_NAME = "fn_question_bank_reject_approved_edit"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _question(tenant, curriculum, **overrides) -> QuestionBank:
    """A valid org-scoped question, with any field overridable.

    A local copy rather than an import from ``test_question_lineage.py``, matching the
    convention in ``test_operations_schema.py``: each database module must be runnable on
    its own, and a shared helper would couple an immutability test to a lineage test's
    idea of a valid row.
    """

    values = dict(
        org_id=tenant.org_id,
        scope="org",
        source="institute_bank",
        board_id=curriculum.board_id,
        class_id=curriculum.class_id,
        subject_id=curriculum.subject_id,
        chapter_id=curriculum.chapter_id,
        qtype="short_answer",
        marks=Decimal("3.0"),
        difficulty="medium",
        bloom="understand",
        language="en",
        stem="State and prove the fundamental theorem of arithmetic.",
        answer={"text": "Every integer above one factors uniquely into primes."},
        content_hash="a11ce" + "0" * 59,
        created_by=tenant.user_id,
    )
    values.update(overrides)
    return QuestionBank(**values)


async def _approved_question(db_session, tenant, curriculum, **overrides) -> uuid.UUID:
    """Insert a question, root it, approve it, and commit. Returns its id.

    The ordering here is not incidental and is worth reading once: ``root_question_id``
    and ``status`` are set in the **same** flush as each other but in a *later* statement
    than the INSERT, because ``id`` comes from a server-side ``gen_random_uuid()`` and
    there is nothing to point at until PostgreSQL has returned one. That UPDATE is safe
    because the trigger's ``WHEN`` clause tests ``OLD.status``, which is still ``'draft'``
    at that moment — approving a question is itself an UPDATE, and the rule deliberately
    lets the transition *into* ``approved`` through.

    The consequence, which P1-T5 must know: a repository that inserted a row already
    ``approved`` could never then root it. Rooting has to happen while the row is a draft.

    ``expunge_all`` at the end is not tidiness either. Every test below edits through raw
    SQL, so any object left in the identity map would be stale, and an assertion read off
    a stale object is an assertion about Python rather than about PostgreSQL.
    """

    question = _question(tenant, curriculum, **overrides)
    db_session.add(question)
    await db_session.flush()
    assert question.id is not None, "gen_random_uuid() server default did not populate id"

    question.root_question_id = question.id
    question.status = "approved"
    await db_session.commit()

    question_id = question.id
    db_session.expunge_all()
    return question_id


async def _update(db_session, question_id: uuid.UUID, assignment: str) -> None:
    """Run ``UPDATE question_bank SET <assignment> WHERE id = <question_id>``.

    Raw SQL on purpose, and this is the single most important design decision in the
    file. The rule exists to defend against writers that are *not* the ORM — a migration,
    a seed script, an operator at a ``psql`` prompt — so a test that only ever edits
    through SQLAlchemy would prove the weaker claim. ``sa.text`` sends the statement to
    the server as written, with no unit of work deciding which columns to include.

    The assignment is interpolated rather than bound because a bound parameter cannot be
    an arbitrary SQL fragment; the values come from literals in this file only.
    """

    await db_session.execute(
        sa.text(
            f"UPDATE question_bank SET {assignment} WHERE id = CAST(:qid AS uuid)"
        ),
        {"qid": str(question_id)},
    )


async def _read(db_session, question_id: uuid.UUID, column: str):
    """One column of one row, straight from the server."""

    result = await db_session.execute(
        sa.text(f"SELECT {column} FROM question_bank WHERE id = CAST(:qid AS uuid)"),
        {"qid": str(question_id)},
    )
    return result.scalar_one()


def _rejected_columns(error) -> list[str]:
    """The exact column list revision 0020 reported, parsed out of ``DETAIL``.

    **This parses ``str(error.orig)`` rather than ``str(error)``, and that distinction is
    the whole reason this function exists.** SQLAlchemy's ``DBAPIError.__str__`` appends
    the failing statement — ``[SQL: UPDATE question_bank SET stem = ...]`` — so a naive
    ``assert "stem" in str(error)`` passes whenever the *statement* mentions ``stem``,
    entirely regardless of what the trigger reported. Every column assertion in this file
    would have been a false positive, including on a trigger that named no columns at all.

    ``error.orig`` is the translated DBAPI error, whose single argument is asyncpg's own
    rendering: the ``MESSAGE``, then ``DETAIL``, then ``HINT``, and no statement echo. So
    slicing between those markers yields a list that can only have come from
    ``string_agg`` inside the trigger function.

    Returned as a list rather than a set because the order is a guarantee — the function
    sorts with ``ORDER BY o.key``, so the same rejection always produces the same log
    line.
    """

    message = str(error.orig)

    assert "DETAIL:" in message, (
        "the error carries no DETAIL section, so the trigger did not report which "
        "columns it rejected: " + message
    )
    detail = message.split("DETAIL:", 1)[1].split("HINT:", 1)[0]

    assert "changes: " in detail, (
        "DETAIL does not have revision 0020's shape ('... this UPDATE changes: a, b'), "
        "so this helper is parsing something else: " + detail
    )
    listed = detail.split("changes: ", 1)[1].strip()
    return [column.strip() for column in listed.split(",")]


def _assert_immutability_violation(exc_info, *expected_columns: str) -> None:
    """Assert the failure is revision 0020's, and that it named exactly these columns.

    Three separate claims, because any one of them can hold while another fails:

    1. **The SQLSTATE is ``23514``.** This is what makes the failure an
       :class:`IntegrityError` a service layer can handle beside every other constraint
       violation, instead of the generic ``DBAPIError`` that plpgsql's default ``P0001``
       produces. Asserted explicitly rather than relying on the ``pytest.raises`` type,
       because a *different* check constraint on the table would also raise ``23514``.
    2. **The message identifies the rule**, which is what turns a log line into a
       diagnosis and distinguishes this from the ``ck_question_bank_*`` constraints.
    3. **``DETAIL`` names exactly the expected columns — no more, no fewer.** Equality
       rather than containment: a trigger that reported every column on every UPDATE
       would satisfy a containment check while being useless, and one that had silently
       started including ``search_document`` would pass it while being actively broken.
    """

    error = exc_info.value
    assert error.orig.sqlstate == IMMUTABILITY_SQLSTATE, (
        f"expected SQLSTATE {IMMUTABILITY_SQLSTATE} (check_violation) so that callers "
        f"see an IntegrityError; got {error.orig.sqlstate!r}"
    )

    message = str(error.orig)
    assert "an approved question is immutable" in message, (
        "the rejection did not come from revision 0020's trigger: " + message
    )
    assert "ADR-003" in message and "BR-8" in message

    assert _rejected_columns(error) == list(expected_columns), (
        f"expected the trigger to report exactly {list(expected_columns)}, got "
        f"{_rejected_columns(error)}: " + message
    )


# ---------------------------------------------------------------------------
# The rule bites
# ---------------------------------------------------------------------------


async def test_editing_an_approved_question_is_rejected(db_session, tenant, curriculum):
    """The headline claim: a plain UPDATE of the text of an approved question fails.

    This is the exact statement that was possible before revision 0020 and that ADR-003
    forbids — rewriting a question that may already have been printed on a paper. The
    second half of the test matters as much as the first: the row is read back to prove
    the UPDATE left **nothing** behind. A ``BEFORE`` trigger that raised after partially
    applying a change would be worse than none, and "the exception was raised" does not
    by itself establish that it did not.
    """

    question_id = await _approved_question(db_session, tenant, curriculum)
    original_stem = await _read(db_session, question_id, "stem")

    with pytest.raises(IntegrityError) as exc_info:
        await _update(db_session, question_id, "stem = 'Rewritten after printing.'")
    _assert_immutability_violation(exc_info, "stem")
    await db_session.rollback()

    assert await _read(db_session, question_id, "stem") == original_stem


async def test_the_orm_cannot_edit_an_approved_question_either(
    db_session, tenant, curriculum
):
    """The same rule through SQLAlchemy, which is not a redundant restatement.

    The ORM emits a different UPDATE from the raw one above: it includes only the changed
    attributes *plus* ``updated_at``, which ``TimestampMixin`` wires an ``onupdate`` onto.
    So this statement carries one protected column and one mutable one, and it must still
    be refused — an implementation that checked "did this UPDATE touch only mutable
    columns" as a set-membership test on the SET list rather than comparing values could
    pass the raw test and fail here, or vice versa.

    It also settles the practical question P1-T5 will ask: does the fork path have to be
    used, or can a repository method quietly ``session.add`` its way around it? It cannot.
    """

    question_id = await _approved_question(db_session, tenant, curriculum)

    question = (
        await db_session.execute(
            sa.select(QuestionBank).where(QuestionBank.id == question_id)
        )
    ).scalar_one()
    question.stem = "Rewritten through the ORM."

    with pytest.raises(IntegrityError) as exc_info:
        await db_session.flush()
    _assert_immutability_violation(exc_info, "stem")
    await db_session.rollback()


@pytest.mark.parametrize(
    ("column", "assignment"),
    [
        ("stem", "stem = 'A different question entirely.'"),
        ("answer", """answer = '{"text": "Something else."}'::jsonb"""),
        ("marks", "marks = 9.5"),
        ("difficulty", "difficulty = 'hard'"),
        ("qtype", "qtype = 'long_answer'"),
        ("bloom", "bloom = NULL"),
        ("language", "language = 'hi'"),
        ("source", "source = 'ai_generated'"),
        ("content_hash", f"content_hash = '{'f00d' + '0' * 60}'"),
        ("version_number", "version_number = 7"),
        ("root_question_id", "root_question_id = NULL"),
        ("chapter_id", "chapter_id = NULL"),
        ("created_by", "created_by = NULL"),
        ("created_at", "created_at = '2000-01-01 00:00:00+00'::timestamptz"),
        ("meta", """meta = '{"note": "tampered"}'::jsonb"""),
    ],
)
async def test_every_content_column_is_protected(
    db_session, tenant, curriculum, column, assignment
):
    """Fifteen columns, one at a time, each refused and each named in the error.

    This is the test that proves the *shape* of revision 0020's comparison rather than
    one instance of it. The function diffs ``to_jsonb(OLD) - _ignored`` against
    ``to_jsonb(NEW) - _ignored`` instead of enumerating ``OLD.col IS DISTINCT FROM
    NEW.col`` tests, and the difference between those two implementations is invisible
    until a column is added: an enumerated trigger silently stops protecting anything
    declared after it was written, while still existing and still firing. A whole-row diff
    cannot have that bug.

    Two entries earn specific mention:

    * ``bloom = NULL`` exercises the ``IS DISTINCT FROM`` path. ``to_jsonb`` renders a
      NULL column as jsonb ``null``, not as a missing key, so a plain ``<>`` would return
      NULL here, the ``WHERE`` would drop the row, and **clearing a column would be
      permitted** while setting one was refused. That is the kind of hole that never
      shows up in the happy path.
    * ``created_at`` is the audit timestamp, protected while ``updated_at`` is
      deliberately not. The pair is the clearest statement of what the mutable set means:
      bookkeeping about *when the row was touched* may change; the record of when the
      question came into existence may not.

      It is written as a **fixed literal** rather than ``now()``, and that is a bug fix
      rather than a style choice. ``created_at = now()`` failed this test on a correct
      database: ``now()`` is ``transaction_timestamp()``, the row's ``created_at`` server
      default was evaluated from the same ``now()``, and
      :func:`~tests.conftest.db_session` joins the enclosing transaction with
      ``create_savepoint`` — so the ``commit()`` in :func:`_approved_question` releases a
      savepoint without ending the real transaction, and insert and UPDATE share one
      timestamp. The statement rewrote the identical value, the trigger's diff correctly
      found nothing changed, and nothing was raised. See
      :func:`test_setting_created_at_to_the_transaction_timestamp_is_a_no_op`, which pins
      that behaviour deliberately instead of tripping over it.
    """

    question_id = await _approved_question(db_session, tenant, curriculum)
    before = await _read(db_session, question_id, column)

    with pytest.raises(IntegrityError) as exc_info:
        await _update(db_session, question_id, assignment)
    _assert_immutability_violation(exc_info, column)
    await db_session.rollback()

    assert await _read(db_session, question_id, column) == before


async def test_a_column_added_by_a_later_revision_is_protected_too(
    db_session, tenant, curriculum
):
    """``prompt_version_id`` arrives in revision 0022 and inherits 0020's protection.

    The payoff of the whole-row diff, demonstrated rather than asserted in a comment.
    Nothing in revision 0022 mentions the trigger and nothing in revision 0020 mentions
    ``prompt_version_id``, yet an approved question's AI provenance cannot be rewritten —
    which is the correct outcome, because provenance that can be edited after approval is
    not provenance.

    The flip side is a real operational constraint on P4 and is recorded in revision
    0022's docstring: backfilling this column on rows that are *already* approved needs
    the ``ALTER TABLE ... DISABLE TRIGGER`` maintenance path. This test is what makes that
    constraint discoverable before someone writes the backfill.
    """

    question_id = await _approved_question(db_session, tenant, curriculum)

    prompt = PromptVersion(
        task="generate_questions",
        version=7,
        template_ref="packages/prompts/generate_questions/v7.md",
        model="test-model-generate-v1",
    )
    db_session.add(prompt)
    await db_session.flush()
    prompt_id = prompt.id
    await db_session.commit()

    with pytest.raises(IntegrityError) as exc_info:
        await _update(
            db_session,
            question_id,
            f"prompt_version_id = CAST('{prompt_id}' AS uuid)",
        )
    _assert_immutability_violation(exc_info, "prompt_version_id")
    await db_session.rollback()

    assert await _read(db_session, question_id, "prompt_version_id") is None


async def test_a_multi_column_edit_names_every_column_it_rejected(
    db_session, tenant, curriculum
):
    """``DETAIL`` lists all offending columns, alphabetically, not just the first.

    The function collects with ``string_agg(... ORDER BY o.key)`` rather than raising on
    the first mismatch, and the reason is diagnostic: an ORM flush or a hand-written
    UPDATE that changes six things gets one error naming six columns instead of six
    round trips discovering them one at a time.

    The ordering is asserted because it is deterministic and free — ``jsonb_each`` has no
    guaranteed order of its own, so without the ``ORDER BY`` the same failure would
    produce a different message each time, which is miserable to grep for in logs.
    """

    question_id = await _approved_question(db_session, tenant, curriculum)

    with pytest.raises(IntegrityError) as exc_info:
        await _update(
            db_session,
            question_id,
            "stem = 'Three at once.', difficulty = 'easy', marks = 1.0",
        )
    # Ordered equality, so this single call asserts both halves of the claim: all three
    # columns were reported, and they came back alphabetically rather than in whatever
    # order `jsonb_each` happened to walk the row.
    _assert_immutability_violation(exc_info, "difficulty", "marks", "stem")
    await db_session.rollback()


async def test_the_generated_search_column_is_excluded_without_losing_coverage(
    db_session, tenant, curriculum
):
    """``search_document`` never appears in ``DETAIL``, and nothing is unprotected by that.

    Excluding a column from an immutability rule normally *is* a hole, and this one is
    not, for a reason worth pinning to an executable assertion: ``search_document`` is a
    pure function of ``stem``, ``answer`` and ``rubric``, and all three of those are
    protected. No edit can move the search vector without tripping the trigger on its
    inputs first — which is precisely what this test observes. The rejection names
    ``stem`` and says nothing about the tsvector, and the tsvector is unchanged
    afterwards because the whole statement was refused.

    Why it has to be excluded at all is mechanical, not a judgement call: the column is
    ``GENERATED ALWAYS ... STORED`` and PostgreSQL computes generated columns after
    ``BEFORE`` row triggers run, so ``NEW.search_document`` still holds the *old* vector
    while ``NEW.stem`` holds the new stem. Comparing it would report a spurious change on
    every UPDATE — see :func:`test_status_transitions_on_an_approved_question_are_allowed`,
    which is the test that would fail if the exclusion were removed.
    """

    question_id = await _approved_question(db_session, tenant, curriculum)
    before = await _read(db_session, question_id, "search_document")
    assert before, "revision 0018's generated column is empty, so this test is vacuous"

    with pytest.raises(IntegrityError) as exc_info:
        await _update(db_session, question_id, "stem = 'Define a prime number.'")
    _assert_immutability_violation(exc_info, "stem")

    assert "search_document" not in _rejected_columns(exc_info.value), (
        "search_document was reported as changed, which means it is no longer in the "
        "trigger's ignore list and every status transition is about to start failing"
    )
    await db_session.rollback()

    assert await _read(db_session, question_id, "search_document") == before


# ---------------------------------------------------------------------------
# The rule does not over-reach
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("new_status", ["archived", "rejected", "draft"])
async def test_status_transitions_on_an_approved_question_are_allowed(
    db_session, tenant, curriculum, new_status
):
    """Status is mutable on approved rows — including, deliberately, back to ``draft``.

    The confirmed decision was explicit that status transitions themselves must be
    permitted, and the first two values are unremarkable: retiring or reversing a
    decision is a workflow action, not a content edit.

    ``draft`` is included because it is the **known residue**, and a test that quietly
    omitted it would hide the gap rather than record it. ``UPDATE ... SET status =
    'draft'`` followed by an edit defeats this trigger in two statements. That is
    reported, not fixed: closing it needs a state-machine trigger on ``status``
    transitions (approved may go to ``archived`` or ``rejected``, never back to
    ``draft``), which is a separate decision nobody has made. What revision 0020 buys is
    that an in-place edit now costs a deliberate, auditable status downgrade instead of
    one silent UPDATE.

    This test is also the canary for the ``search_document`` exclusion: if that column
    were ever compared, a bare status change would look like a content edit and every
    parametrisation here would fail.
    """

    question_id = await _approved_question(db_session, tenant, curriculum)

    await _update(db_session, question_id, f"status = '{new_status}'")
    await db_session.commit()

    assert await _read(db_session, question_id, "status") == new_status


async def test_demoting_is_current_on_an_approved_question_is_allowed(
    db_session, tenant, curriculum
):
    """Without this, BR-8 forking is impossible — so this is a requirement, not a leniency.

    ``uq_question_bank_root_question_id_current`` permits exactly one row per lineage with
    ``is_current``, so creating version 2 of an approved question *requires* clearing
    version 1's flag. A trigger that refused it would not enforce ADR-003's versioning
    model; it would forbid it, leaving in-place editing as the only way to revise an
    approved question — the exact act the trigger exists to prevent.

    The test goes all the way to a committed version 2 rather than stopping at the
    demotion, because the demotion in isolation proves only that one statement is allowed.
    What matters is that the *whole* fork completes with the trigger live.
    """

    question_id = await _approved_question(db_session, tenant, curriculum)

    await _update(db_session, question_id, "is_current = false")
    await db_session.commit()
    assert await _read(db_session, question_id, "is_current") is False

    forked = _question(
        tenant,
        curriculum,
        root_question_id=question_id,
        previous_version_id=question_id,
        version_number=2,
        is_current=True,
        status="approved",
        stem="State the fundamental theorem of arithmetic.",
        content_hash="beef" + "0" * 60,
    )
    db_session.add(forked)
    await db_session.commit()

    current = await db_session.execute(
        sa.text(
            "SELECT count(*) FROM question_bank "
            "WHERE root_question_id = CAST(:qid AS uuid) AND is_current"
        ),
        {"qid": str(question_id)},
    )
    assert current.scalar_one() == 1, (
        "the fork completed but left the wrong number of current versions, so this test "
        "is no longer demonstrating a working fork"
    )


async def test_soft_deleting_an_approved_question_is_allowed(
    db_session, tenant, curriculum
):
    """``deleted_at`` is mutable, because ADR-004 leaves no other way to retire the row.

    This trigger fires on ``UPDATE`` only. Blocking ``deleted_at`` would therefore leave
    hard ``DELETE`` as the single remaining removal path for an approved question —
    pushing every retirement onto the one operation ADR-004 prohibits, and destroying the
    lineage history that ``ON DELETE RESTRICT`` on the self-references exists to protect.

    Nothing is given up by allowing it: a tombstoned row's content is still immutable,
    since every content column remains protected whether ``deleted_at`` is set or not.
    """

    question_id = await _approved_question(db_session, tenant, curriculum)

    await _update(db_session, question_id, "deleted_at = now()")
    await db_session.commit()
    assert await _read(db_session, question_id, "deleted_at") is not None

    # Still immutable after the tombstone.
    with pytest.raises(IntegrityError) as exc_info:
        await _update(db_session, question_id, "stem = 'Edited after deletion.'")
    _assert_immutability_violation(exc_info, "stem")
    await db_session.rollback()


async def test_a_draft_question_can_still_be_edited_freely(
    db_session, tenant, curriculum
):
    """The ``WHEN (OLD.status = 'approved')`` clause is what scopes the rule.

    Authoring has to work. A question is edited many times before anyone approves it, and
    if the trigger fired on every row the table would be append-only from the first
    INSERT — which is not what ADR-003 says and would make the draft workflow impossible.

    Asserted with a *content* column rather than a status change so that the test would
    fail if the ``WHEN`` clause were ever dropped and the function left to decide for
    itself.
    """

    question = _question(tenant, curriculum)
    db_session.add(question)
    await db_session.flush()
    question_id = question.id
    await db_session.commit()
    db_session.expunge_all()

    assert await _read(db_session, question_id, "status") == "draft"

    await _update(db_session, question_id, "stem = 'Edited while still a draft.'")
    await db_session.commit()

    assert await _read(db_session, question_id, "stem") == "Edited while still a draft."


async def test_a_no_op_update_of_an_approved_question_is_allowed(
    db_session, tenant, curriculum
):
    """``SET stem = stem`` is permitted, which proves the trigger compares values.

    A weaker implementation — one that inspected which columns appeared in the SET list,
    or used ``to_jsonb(OLD) <> to_jsonb(NEW)`` on the whole row without excluding the
    generated column — would reject this. Neither behaviour is wrong in any dangerous
    way, but the distinction is worth pinning down, because it is what makes the rule
    predictable: **the trigger objects to a changed value, never to a statement.**

    That has a practical consequence for P1-T5. An idempotent writer that re-applies the
    values it already read — a retry, a reconciliation pass, an ``ON CONFLICT DO UPDATE``
    that lands on identical data — does not have to special-case approved rows.
    """

    question_id = await _approved_question(db_session, tenant, curriculum)

    await _update(db_session, question_id, "stem = stem, marks = marks")
    await db_session.commit()

    assert await _read(db_session, question_id, "status") == "approved"


async def test_setting_created_at_to_the_transaction_timestamp_is_a_no_op(
    db_session, tenant, curriculum
):
    """``created_at = now()`` is permitted *here*, and only because of ``now()``.

    This test exists because the parametrised case above was originally written as
    ``created_at = now()`` and failed on a correct database — so the behaviour is pinned
    deliberately rather than left as a trap for the next person.

    The mechanism, in order: ``now()`` is ``transaction_timestamp()``, not wall clock;
    ``created_at``'s server default is that same ``now()``; and the ``db_session`` fixture
    binds the session to :func:`~tests.conftest.db_connection`'s open transaction with
    ``join_transaction_mode="create_savepoint"``, so the ``commit()`` inside
    :func:`_approved_question` releases a savepoint without ending the real transaction.
    Insert and UPDATE therefore share one transaction timestamp, the assignment writes the
    byte-identical value, and the trigger's ``IS DISTINCT FROM`` diff correctly finds
    nothing changed. The first assertion states that premise as an equality rather than
    assuming it, so if the fixture ever stops joining the outer transaction this test
    fails *there* — on the reason — instead of at the ``pytest.raises`` below.

    **This is not a hole in the rule, and the contrast in the second half is the proof.**
    ``clock_timestamp()`` reads the wall clock, differs from the stored value, and is
    refused with ``created_at`` named. In production the row was created in an earlier
    transaction, so ``now()`` differs too and is refused identically; the permitted case is
    reachable only from inside the creating transaction, where there is nothing to falsify
    yet.
    """

    question_id = await _approved_question(db_session, tenant, curriculum)
    before = await _read(db_session, question_id, "created_at")

    same = await db_session.execute(
        sa.text(
            "SELECT now() = created_at FROM question_bank WHERE id = CAST(:qid AS uuid)"
        ),
        {"qid": str(question_id)},
    )
    assert same.scalar_one() is True, (
        "premise failed: now() no longer equals this row's created_at, so the fixture is "
        "not sharing one transaction with the insert and the rest of this test is "
        "asserting nothing"
    )

    await _update(db_session, question_id, "created_at = now()")
    assert await _read(db_session, question_id, "created_at") == before

    with pytest.raises(IntegrityError) as exc_info:
        await _update(db_session, question_id, "created_at = clock_timestamp()")
    _assert_immutability_violation(exc_info, "created_at")
    await db_session.rollback()

    assert await _read(db_session, question_id, "created_at") == before


# ---------------------------------------------------------------------------
# The objects are actually there, and actually enabled
# ---------------------------------------------------------------------------


async def test_the_trigger_exists_enabled_and_scoped_as_written(db_session):
    """Read the trigger back out of ``pg_trigger``, including whether it is *enabled*.

    Duplicated from ``scripts/verify-p1-t4.ps1`` on purpose. The script runs at
    checkpoints; this runs in CI on every commit, and the object it is guarding is the one
    kind ``alembic check`` is blind to in **both** directions — a dropped trigger is not
    reported as drift, so without this assertion the schema could lose its only
    enforcement of ADR-003 and every other test would still pass.

    ``tgenabled`` is the subtle half. ``ALTER TABLE ... DISABLE TRIGGER`` leaves the
    trigger in the catalog, fully defined and completely inert; a test that only checked
    for its *existence* would be satisfied by a permanently disabled one. ``'O'`` means it
    fires for origin and local sessions, which is the normal state.

    The ``WHEN`` clause is asserted rather than the function body because that is the half
    of the rule PostgreSQL normalises into readable catalog text — it states which rows
    are protected, so the protected set is a fact verification can read rather than infer.
    """

    row = (
        await db_session.execute(
            sa.text(
                "SELECT t.tgenabled::text, pg_get_triggerdef(t.oid) "
                "  FROM pg_trigger AS t "
                "  JOIN pg_class AS c ON c.oid = t.tgrelid "
                " WHERE NOT t.tgisinternal "
                "   AND c.relname = 'question_bank' "
                f"   AND t.tgname = '{TRIGGER_NAME}'"
            )
        )
    ).one_or_none()

    assert row is not None, (
        f"{TRIGGER_NAME} is missing from question_bank. alembic check cannot see this, "
        "so nothing else in the suite would have noticed: ADR-003 immutability is "
        "currently unenforced."
    )

    enabled, definition = row
    assert enabled == "O", (
        f"{TRIGGER_NAME} exists but tgenabled is {enabled!r}, not 'O' — it is in the "
        "catalog and not firing, which is the worst of both states"
    )

    lowered = definition.lower()
    assert "before update" in lowered, definition
    assert "for each row" in lowered, definition
    assert "old.status = 'approved'" in lowered, (
        "the WHEN clause no longer scopes the rule to approved rows: " + definition
    )
    assert FUNCTION_NAME in lowered, definition


async def test_the_trigger_function_has_a_pinned_search_path(db_session):
    """``SET search_path = pg_catalog, public`` on the function, asserted from ``pg_proc``.

    The function is ``SECURITY INVOKER`` and touches no tables, so this is not the
    privilege-escalation defence it is on revision 0021's function — but ``to_jsonb``,
    ``jsonb_each`` and ``string_agg`` are all resolved through the search path, and a
    caller who can create a schema earlier in their own path could otherwise shadow one
    of them and change what the rule computes. Pinning it makes the function's behaviour
    independent of who calls it, which for a security control is the whole requirement.

    ``proconfig`` is a text array of ``key=value`` settings, so this reads it as one
    string rather than guessing at PostgreSQL's spacing.
    """

    config = (
        await db_session.execute(
            sa.text(
                "SELECT array_to_string(p.proconfig, '|') "
                "  FROM pg_proc AS p "
                "  JOIN pg_namespace AS n ON n.oid = p.pronamespace "
                f" WHERE n.nspname = 'public' AND p.proname = '{FUNCTION_NAME}'"
            )
        )
    ).scalar_one_or_none()

    assert config is not None, f"public.{FUNCTION_NAME} is missing or has no proconfig"
    assert "search_path=" in config, config
    assert "pg_catalog" in config and "public" in config, config
