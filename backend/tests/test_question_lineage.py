"""BR-8 / ADR-003 question versioning, proved against a real PostgreSQL database.

The claim under test is the one the whole question bank rests on: **an approved
question is never edited in place — editing forks a new version, and the old version
stays queryable forever.** A model file cannot demonstrate that. Only a live database
can show that the partial unique index really rejects a second current version, that
the forked row really points back at its parent, and that the text printed on last
term's paper is still there afterwards.

Requirements are the same as ``test_models_roundtrip.py``: a reachable PostgreSQL with
``alembic upgrade head`` applied. The fixtures fail with instructions rather than
skipping, because a skipped database test looks like a pass.

What the schema does and does not guarantee
-------------------------------------------
Worth stating precisely, because the gap is reported in the P1-T4 hand-off notes:

* **Guaranteed by the database** — at most one ``is_current`` row per rooted lineage,
  version numbers unique within a lineage, ``version_number > 0``, and the
  ``scope``/``org_id`` agreement rules. Each has a test below.
* **Also guaranteed, as of revision 0020** — that an ``approved`` row is never edited in
  place. This bullet previously said the opposite, and the correction is the point: doc
  03 specifies no trigger and §9 step 18 lists "indexes, constraints, RLS policies,
  partitions" without mentioning them, so immutability was an application invariant with
  nothing behind it. Revision 0020 adds ``trg_question_bank_reject_approved_edit``, and
  ``tests/test_question_immutability.py`` asserts it actually refuses the UPDATE.

  The tests in *this* file still assert only that forking **preserves** history, which
  is the right division: this file's subject is the lineage the fork creates, and the
  trigger's subject is the edit the fork replaces. What matters here is that
  :func:`test_editing_an_approved_question_forks_a_new_version` is now also a positive
  control for the trigger — it demotes an approved row's ``is_current``, which 0020 must
  permit or BR-8 becomes unimplementable.
* **Inert until a lineage is rooted** — ``root_question_id`` is nullable (doc 03 §5)
  and PostgreSQL unique indexes are NULLS DISTINCT, so both lineage indexes ignore
  unrooted rows entirely. That is measured explicitly in
  :func:`test_lineage_constraints_are_inert_until_a_lineage_is_rooted` rather than
  left as a comment, because it is the difference between BR-8 being enforced and
  BR-8 looking enforced.

  Note the interaction with revision 0020, which :func:`_insert_rooted` depends on
  without saying so: rooting happens in a second statement after the INSERT, so a
  helper that inserted a row *already* ``approved`` could not then root it. Every
  caller below leaves ``status`` at its ``draft`` default for that reason.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.models import QuestionBank

pytestmark = pytest.mark.db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _question(tenant, curriculum, **overrides) -> QuestionBank:
    """A valid org-scoped question, with any field overridable.

    ``language`` and ``content_hash`` are supplied explicitly because doc 03 §5 gives
    neither a default — an omission reported as an open item, and one this helper makes
    concrete every time it is called.
    """

    values = dict(
        org_id=tenant.org_id,
        scope="org",
        source="institute_bank",
        board_id=curriculum.board_id,
        class_id=curriculum.class_id,
        subject_id=curriculum.subject_id,
        chapter_id=curriculum.chapter_id,
        qtype="mcq",
        marks=Decimal("1.5"),
        difficulty="medium",
        bloom="understand",
        language="en",
        stem="Which of the following is an irrational number?",
        options=["2/3", "0.75", "sqrt(2)", "-4"],
        answer={"correct_index": 2},
        content_hash="c0ffee" + "0" * 58,
        created_by=tenant.user_id,
    )
    values.update(overrides)
    return QuestionBank(**values)


async def _insert_rooted(db_session, tenant, curriculum, **overrides) -> QuestionBank:
    """Insert version 1 of a lineage and root it on itself.

    ``root_question_id = id`` is exactly what P1-T5's repository will do on insert, and
    it has to happen in two steps because ``id`` comes from a server-side
    ``gen_random_uuid()`` — there is no id to point at until PostgreSQL has returned
    one. doc 03 §9 step 19's "backfill current pointers" is the alternative for rows
    that predate that behaviour.
    """

    question = _question(tenant, curriculum, **overrides)
    db_session.add(question)
    await db_session.flush()
    assert question.id is not None, "gen_random_uuid() server default did not populate id"
    question.root_question_id = question.id
    await db_session.commit()
    return question


# ---------------------------------------------------------------------------
# The required round trip: insert → approve → edit → fork
# ---------------------------------------------------------------------------


async def test_editing_an_approved_question_forks_a_new_version(
    db_session, tenant, curriculum
):
    """The BR-8 lifecycle, end to end, with every assertion read back from the server.

    One implementation detail this test pins down, and it is a real constraint on
    P1-T5's fork operation rather than a quirk of the test: **the demotion must be
    flushed before the new version is inserted.** SQLAlchemy's unit of work emits
    INSERTs for a mapper before UPDATEs, so adding v2 with ``is_current = True`` and
    demoting v1 in the same flush would hit
    ``uq_question_bank_root_question_id_current`` mid-flush. PostgreSQL cannot defer a
    unique *index* the way it can defer a unique *constraint*, so ordering is the only
    remedy — two flushes, demote first.
    """

    original = await _insert_rooted(db_session, tenant, curriculum)
    root_id = original.root_question_id
    v1_id = original.id

    # --- server defaults on the first version --------------------------------
    assert original.version_number == 1        # DEFAULT 1
    assert original.is_current is True         # DEFAULT true
    assert original.status == "draft"          # DEFAULT 'draft' (doc 03's two-way default)
    assert original.meta == {}                 # DEFAULT '{}'::jsonb
    assert original.learning_outcome_ids == []  # DEFAULT '{}'::uuid[]
    assert original.competency_ids == []
    assert original.row_version == 1
    assert original.deleted_at is None

    # --- approve it ----------------------------------------------------------
    original.status = "approved"
    await db_session.commit()

    # --- edit it, which means fork it ---------------------------------------
    # Step 1: retire the current pointer. Must be its own flush; see the docstring.
    original.is_current = False
    await db_session.flush()

    # Step 2: the new version. `previous_version_id` records the parent; `root_id` is
    # unchanged, which is what makes the two rows one lineage.
    forked = _question(
        tenant,
        curriculum,
        root_question_id=root_id,
        previous_version_id=v1_id,
        version_number=2,
        is_current=True,
        status="approved",
        stem="Which of the following numbers is irrational?",
        content_hash="deadbeef" + "0" * 56,
    )
    db_session.add(forked)
    await db_session.commit()
    v2_id = forked.id

    # --- read everything back from the database ------------------------------
    # expunge_all() first: the objects asserted on below must be reconstructed from
    # what PostgreSQL stored, not handed back out of the identity map.
    db_session.expunge_all()

    lineage = (
        (
            await db_session.execute(
                sa.select(QuestionBank)
                .where(QuestionBank.root_question_id == root_id)
                .order_by(QuestionBank.version_number)
            )
        )
        .scalars()
        .all()
    )

    assert [q.version_number for q in lineage] == [1, 2], "the fork did not create v2"
    old, new = lineage

    # The old version is still there, still readable, still saying what it said.
    assert old.id == v1_id
    assert old.is_current is False
    assert old.status == "approved"
    assert old.stem == "Which of the following is an irrational number?"
    assert old.deleted_at is None, "forking must not tombstone the previous version"

    # The new one is current and knows where it came from.
    assert new.id == v2_id
    assert new.is_current is True
    assert new.previous_version_id == v1_id
    assert new.root_question_id == root_id
    assert new.stem == "Which of the following numbers is irrational?"
    assert new.content_hash != old.content_hash, "a fork should re-hash its content"

    # BR-8 in one query: exactly one current version per lineage.
    current_count = (
        await db_session.execute(
            sa.select(sa.func.count())
            .select_from(QuestionBank)
            .where(
                QuestionBank.root_question_id == root_id,
                QuestionBank.is_current.is_(True),
            )
        )
    ).scalar_one()
    assert current_count == 1

    # And the relationships wire the chain in both directions, through real FKs.
    walked = (
        await db_session.execute(
            sa.select(QuestionBank).where(QuestionBank.id == v2_id)
        )
    ).scalar_one()
    parent = await db_session.get(QuestionBank, walked.previous_version_id)
    assert parent is not None and parent.version_number == 1


# ---------------------------------------------------------------------------
# The two lineage indexes actually bite
# ---------------------------------------------------------------------------


async def test_a_second_current_version_is_rejected(db_session, tenant, curriculum):
    """``uq_question_bank_root_question_id_current`` is what makes BR-8 a DB fact."""

    original = await _insert_rooted(db_session, tenant, curriculum)

    db_session.add(
        _question(
            tenant,
            curriculum,
            root_question_id=original.root_question_id,
            previous_version_id=original.id,
            version_number=2,
            is_current=True,  # without demoting version 1 first
            content_hash="second" + "0" * 58,
        )
    )

    with pytest.raises(IntegrityError) as exc_info:
        await db_session.flush()
    assert "uq_question_bank_root_question_id_current" in str(exc_info.value)
    await db_session.rollback()


async def test_a_duplicate_version_number_is_rejected(db_session, tenant, curriculum):
    """Version numbers are lineage identity, so they cannot repeat.

    Note this index is *not* partial on ``deleted_at``: soft-deleting version 2 must
    not free the number 2, or a later fork could mint a second version 2 and every
    ``previous_version_id`` chain through the lineage would become ambiguous. The
    second half of this test is what proves the index is non-partial.
    """

    original = await _insert_rooted(db_session, tenant, curriculum)
    root_id = original.root_question_id

    original.is_current = False
    await db_session.flush()
    second = _question(
        tenant,
        curriculum,
        root_question_id=root_id,
        previous_version_id=original.id,
        version_number=2,
        content_hash="v2" + "0" * 62,
    )
    db_session.add(second)
    await db_session.commit()
    second_id = second.id

    # Same version number again, while v2 is alive: rejected.
    second.is_current = False
    await db_session.flush()
    db_session.add(
        _question(
            tenant,
            curriculum,
            root_question_id=root_id,
            previous_version_id=second_id,
            version_number=2,
            content_hash="clash" + "0" * 59,
        )
    )
    with pytest.raises(IntegrityError) as exc_info:
        await db_session.flush()
    assert "uq_question_bank_root_question_id_version_number" in str(exc_info.value)
    await db_session.rollback()

    # Now tombstone v2 and try again. Still rejected — unlike every *name* uniqueness
    # rule in this schema, this index has no `WHERE deleted_at IS NULL` predicate.
    db_session.expunge_all()
    second = (
        await db_session.execute(
            sa.select(QuestionBank).where(QuestionBank.id == second_id)
        )
    ).scalar_one()
    second.deleted_at = dt.datetime.now(dt.timezone.utc)
    await db_session.commit()

    db_session.add(
        _question(
            tenant,
            curriculum,
            root_question_id=root_id,
            previous_version_id=second_id,
            version_number=2,
            is_current=False,
            content_hash="clash2" + "0" * 58,
        )
    )
    with pytest.raises(IntegrityError) as exc_info:
        await db_session.flush()
    assert "uq_question_bank_root_question_id_version_number" in str(exc_info.value)
    await db_session.rollback()


async def test_lineage_constraints_are_inert_until_a_lineage_is_rooted(
    db_session, tenant, curriculum
):
    """**Measuring the known gap, not asserting a desired behaviour.**

    doc 03 §5 declares ``root_question_id`` nullable, and PostgreSQL unique indexes
    are NULLS DISTINCT: a NULL never equals another NULL, so neither lineage index sees
    an unrooted row. The two INSERTs below are therefore both accepted even though they
    claim the same version number *and* both claim to be current.

    If this test ever starts failing, that is good news — it means ``root_question_id``
    became NOT NULL (doc 03 §9 step 19) or the repository layer started rooting rows on
    insert (P1-T5). Delete the test at that point; do not "fix" it.
    """

    db_session.add_all(
        [
            _question(tenant, curriculum, content_hash="unrooted-a" + "0" * 54),
            _question(tenant, curriculum, content_hash="unrooted-b" + "0" * 54),
        ]
    )
    await db_session.commit()  # must not raise, and that is the finding

    unrooted = (
        await db_session.execute(
            sa.select(sa.func.count())
            .select_from(QuestionBank)
            .where(
                QuestionBank.root_question_id.is_(None),
                QuestionBank.is_current.is_(True),
                QuestionBank.version_number == 1,
            )
        )
    ).scalar_one()
    assert unrooted == 2, (
        "two unrooted current v1 rows should coexist: the lineage indexes are "
        "NULLS DISTINCT, so BR-8 binds only once root_question_id is populated"
    )


# ---------------------------------------------------------------------------
# CHECK constraints (ADR-011) and the conditional scope rules (doc 03 §5)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"source": "crowdsourced"}, "ck_question_bank_source"),
        ({"scope": "district"}, "ck_question_bank_scope"),
        ({"qtype": "essay"}, "ck_question_bank_qtype"),
        ({"difficulty": "brutal"}, "ck_question_bank_difficulty"),
        ({"bloom": "memorize"}, "ck_question_bank_bloom"),
        ({"status": "published"}, "ck_question_bank_status"),
        ({"version_number": 0}, "ck_question_bank_version_number_positive"),
        ({"row_version": 0}, "ck_question_bank_row_version_positive"),
        # doc 03 §5's two conditional rules. A global question belongs to nobody...
        (
            {"scope": "global"},
            "ck_question_bank_scope_global_requires_null_org",
        ),
        # ...and an org-scoped one cannot be ownerless.
        (
            {"scope": "org", "org_id": None},
            "ck_question_bank_scope_org_requires_org",
        ),
    ],
)
async def test_check_constraints_reject_invalid_rows(
    db_session, tenant, curriculum, overrides, constraint
):
    """The server refuses these, not just the application.

    ``{"scope": "global"}`` on its own is the interesting case: the helper supplies a
    real ``org_id``, so this row is rejected for *disagreeing with itself* rather than
    for using a bad value — which is exactly the protection that makes ``'global'`` a
    safe server default on a tenant-scoped table.
    """

    db_session.add(_question(tenant, curriculum, **overrides))

    with pytest.raises(IntegrityError) as exc_info:
        await db_session.flush()
    assert constraint in str(exc_info.value)
    await db_session.rollback()


async def test_null_bloom_passes_its_check_constraint(db_session, tenant, curriculum):
    """A CHECK rejects a row only when it evaluates to FALSE.

    ``bloom`` is nullable (doc 03 §5) and its CHECK is an ``IN`` list, so a NULL makes
    the predicate NULL rather than FALSE and the row is accepted. Worth an explicit
    test because the whole ``check_in`` helper in ``app/db/base.py`` depends on this
    three-valued-logic detail being true, on every nullable vocabulary column in the
    schema.
    """

    question = _question(tenant, curriculum, bloom=None)
    db_session.add(question)
    await db_session.commit()

    db_session.expunge_all()
    loaded = (
        await db_session.execute(
            sa.select(QuestionBank).where(QuestionBank.id == question.id)
        )
    ).scalar_one()
    assert loaded.bloom is None


# ---------------------------------------------------------------------------
# Column types that have to be exact
# ---------------------------------------------------------------------------


async def test_marks_round_trip_as_exact_decimals(db_session, tenant, curriculum):
    """``numeric(4,1)``, not float — doc 03 §5, and it matters.

    A paper's marks must sum to the blueprint total exactly. 0.1 has no exact binary
    representation, so a float column would make "twenty questions of 1.5 marks" sum to
    something that is not 30 often enough to matter. The second half of the test proves
    the declared precision is real rather than advisory.
    """

    question = _question(tenant, curriculum, marks=Decimal("1.5"))
    db_session.add(question)
    await db_session.commit()
    db_session.expunge_all()

    loaded = (
        await db_session.execute(
            sa.select(QuestionBank).where(QuestionBank.id == question.id)
        )
    ).scalar_one()
    assert isinstance(loaded.marks, Decimal), f"got {type(loaded.marks).__name__}"
    assert loaded.marks == Decimal("1.5")

    # precision 4, scale 1 → 999.9 is the ceiling. A DataError, not an IntegrityError:
    # this is the type refusing the value, not a constraint.
    db_session.add(_question(tenant, curriculum, marks=Decimal("1000.0")))
    with pytest.raises(DBAPIError):
        await db_session.flush()
    await db_session.rollback()


async def test_jsonb_and_array_columns_round_trip(db_session, tenant, curriculum):
    """``options``/``answer``/``rubric``/``meta`` are JSONB; the id columns are uuid[].

    doc 03 §3's normalization rule is visible here: the *searchable* content lives in
    ``stem``, a plain text column, and JSONB holds only the structured extras. That is
    what lets §9 step 18 build a ``tsvector`` over real columns instead of digging
    through JSON.
    """

    outcome_ids = [uuid.uuid4(), uuid.uuid4()]
    question = _question(
        tenant,
        curriculum,
        qtype="match",
        options={"left": ["a", "b"], "right": ["1", "2"]},
        answer={"pairs": [["a", "2"], ["b", "1"]]},
        rubric={"partial_credit": True, "per_pair_marks": 0.5},
        meta={"origin": "P1-T4 round trip", "reviewed": False},
        learning_outcome_ids=outcome_ids,
    )
    db_session.add(question)
    await db_session.commit()
    db_session.expunge_all()

    loaded = (
        await db_session.execute(
            sa.select(QuestionBank).where(QuestionBank.id == question.id)
        )
    ).scalar_one()
    assert loaded.options == {"left": ["a", "b"], "right": ["1", "2"]}
    assert loaded.answer["pairs"][0] == ["a", "2"]
    assert loaded.rubric["partial_credit"] is True
    assert loaded.meta["origin"] == "P1-T4 round trip"
    assert loaded.learning_outcome_ids == outcome_ids
    assert all(isinstance(value, uuid.UUID) for value in loaded.learning_outcome_ids)
    # No FK is possible on an array element in PostgreSQL (doc 03 §3 accepts this), so
    # these UUIDs reference nothing. Asserted so the limitation is recorded in a test
    # rather than only in a comment: referential integrity here is the application's.
    assert loaded.competency_ids == []


# ---------------------------------------------------------------------------
# Referential actions (doc 03 §5)
# ---------------------------------------------------------------------------


async def test_hard_deleting_an_author_keeps_their_questions(
    db_session, tenant, curriculum
):
    """``created_by`` is ON DELETE SET NULL: content outlives its author.

    This is the schema-level reason ADR-012 §2 added ``users.status`` — an identity has
    to be *disableable* precisely because rows like this one reference it. The DELETE
    is the only way to observe an ``ON DELETE`` rule; it runs against Core rather than
    the ORM so what is tested is PostgreSQL's behaviour and not SQLAlchemy's, and the
    enclosing transaction is rolled back. Application code never issues one (ADR-004).
    """

    from app.models import User

    question = await _insert_rooted(db_session, tenant, curriculum)
    question_id = question.id
    db_session.expunge_all()

    await db_session.execute(
        sa.delete(User.__table__).where(User.__table__.c.id == tenant.user_id)
    )
    await db_session.commit()

    survivor = (
        await db_session.execute(
            sa.select(QuestionBank).where(QuestionBank.id == question_id)
        )
    ).scalar_one()
    assert survivor.created_by is None, "ON DELETE SET NULL did not fire"
    assert survivor.stem, "the question content must survive its author"


async def test_a_referenced_chapter_cannot_be_hard_deleted(
    db_session, tenant, curriculum
):
    """``chapter_id`` is ON DELETE RESTRICT — doc 03 §5, chapters.

    "Restrict deletion if outcomes, questions, or textbook assets still reference the
    chapter." Curriculum is shared reference data; a chapter disappearing under a
    question would leave it unclassifiable and silently drop it out of every
    chapter-filtered retrieval.
    """

    from app.models import Chapter

    await _insert_rooted(db_session, tenant, curriculum)
    db_session.expunge_all()

    with pytest.raises(IntegrityError) as exc_info:
        await db_session.execute(
            sa.delete(Chapter.__table__).where(
                Chapter.__table__.c.id == curriculum.chapter_id
            )
        )
    assert "fk_question_bank_chapter_id_chapters" in str(exc_info.value)
    await db_session.rollback()
