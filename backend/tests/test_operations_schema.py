"""The operations half of the schema (revisions 0010-0019), proved against real rows.

``test_models_roundtrip.py`` covers tenancy and ``test_question_lineage.py`` covers BR-8
versioning. This file covers what P1-T4 round 2 added: the paper workflow, the billing
ledgers, the audit trail, daily analytics, and the two artifacts revision 0018 bolts onto
tables that already existed — ``question_bank.search_document`` and the
``organizations.subscription_id`` foreign key that revision 0002 deliberately shipped
without.

Why these tests exist when the verification script already dumps the catalog
---------------------------------------------------------------------------
``scripts/verify-p1-t4.ps1`` reads ``pg_constraint``, ``pg_index`` and ``pg_attribute``, so
it proves the DDL *is there*. It cannot prove the DDL *does anything*, and the two are not
the same claim: a generated column can exist and be empty, a foreign key can exist and
point at the wrong table, an identity column can exist without being ``GENERATED ALWAYS``.
Every test below writes or reads a real row to close that gap.

Two of them assert that PostgreSQL **accepts** something a reader might expect it to
reject. Those are not oversights: they are the schema's known limits, pinned down in
executable form so that nobody later reads doc 03's prose and assumes the database is
enforcing an audit vocabulary or an ordering the columns cannot express. Each says so in
its own docstring and each is reported in the P1-T4 hand-off notes.

There were three. The third asserted that the database permits a paper's author to approve
their own paper, and its docstring said that if a future migration added the trigger,
"this test will fail — and that failure is the correct signal, not a regression". Revision
0021 added the trigger, so the test was removed rather than adjusted: BR-6 is now a
database rule and belongs with the rest of its evidence in
``test_approval_separation_of_duties.py``. What is left of the gap — a NULL
``reviewer_id``, an ``UPDATE`` that rewrites a decision's reviewer, and the absence of any
role check on the approver — is measured there, in the same style.

One test below is now also a positive control for that trigger:
``test_paper_version_and_approval_round_trip`` has the author submit and a *different*
reviewer approve, which is exactly the workflow BR-6 requires. It needed no change when
0021 landed, and if it ever starts failing the cause is more likely to be the trigger than
the test.

Requirements are the same as the other two database modules: PostgreSQL reachable at
``TEST_DATABASE_URL`` with ``alembic upgrade head`` applied. The fixtures fail with
instructions rather than skipping.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.models import (
    AnalyticsDaily,
    Approval,
    AuditLog,
    GeneratedPaper,
    Organization,
    PaperVersion,
    QuestionBank,
    Subscription,
    Usage,
    User,
)

pytestmark = pytest.mark.db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _paper(tenant, curriculum, **overrides) -> GeneratedPaper:
    """A valid paper owned by ``tenant``'s organization and authored by its user.

    ``blueprint`` and ``title`` are the only NOT NULL columns beyond ``org_id`` and
    ``created_by``; ``status`` is left out on purpose so the server default proves itself.
    """

    values = dict(
        org_id=tenant.org_id,
        created_by=tenant.user_id,
        curriculum_version_id=curriculum.curriculum_version_id,
        title="Unit Test 1 - Real Numbers",
        blueprint={
            "total_marks": 20,
            "sections": [{"name": "A", "qtype": "mcq", "count": 5, "marks_each": 1}],
        },
        total_marks=Decimal("20.0"),
        duration_min=45,
    )
    values.update(overrides)
    return GeneratedPaper(**values)


def _question(tenant, curriculum, **overrides) -> QuestionBank:
    """A valid org-scoped question.

    Deliberately a local copy of the helper in ``test_question_lineage.py`` rather than an
    import: the two files must be independently runnable, and a shared helper would couple
    a search-artifact test to a lineage test's idea of a valid row.
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
        marks=Decimal("2.0"),
        difficulty="medium",
        bloom="understand",
        language="en",
        stem="Prove that the square root of two is irrational.",
        answer={"text": "Assume a rational form in lowest terms and derive a parity clash."},
        content_hash="d15c0" + "0" * 59,
        created_by=tenant.user_id,
    )
    values.update(overrides)
    return QuestionBank(**values)


async def _fts_count(db_session, question_id: uuid.UUID, query: str) -> int:
    """How many rows with this id match ``query`` through ``search_document``.

    Written as raw SQL on purpose. The claim under test is about the column PostgreSQL
    maintains, so the query has to reach the server exactly as written, with no ORM
    translation layer standing between the assertion and the ``@@`` operator.
    """

    result = await db_session.execute(
        sa.text(
            "SELECT count(*) FROM question_bank "
            "WHERE id = CAST(:qid AS uuid) "
            "  AND search_document @@ plainto_tsquery('english', :q)"
        ),
        {"qid": str(question_id), "q": query},
    )
    return int(result.scalar_one())


# ---------------------------------------------------------------------------
# Revisions 0010-0011 — the paper workflow
# ---------------------------------------------------------------------------


async def test_paper_version_and_approval_round_trip(db_session, tenant, curriculum):
    """A paper, a rendered version and a two-step review trail, all read back.

    The read is hostile to false positives in the same way as the tenancy round trip: the
    identity map is emptied before the SELECTs, so nothing asserted on can be the object
    that was inserted.

    Note what this test does **not** do: order the approvals by ``created_at``. See
    :func:`test_an_approval_trail_written_in_one_transaction_has_no_order`.
    """

    reviewer = User(email="head.of.maths@example.com", full_name="Head of Mathematics")
    paper = _paper(tenant, curriculum)
    db_session.add_all([reviewer, paper])
    await db_session.flush()

    assert paper.id is not None, "gen_random_uuid() server default did not populate id"

    db_session.add_all(
        [
            PaperVersion(
                paper_id=paper.id,
                label="v1",
                content={"sections": [{"name": "A", "questions": ["Q1", "Q2"]}]},
                answer_key={"A": ["c", "a"]},
                pdf_key="papers/2026/unit-test-1.pdf",
            ),
            Approval(paper_id=paper.id, reviewer_id=tenant.user_id, action="submit"),
            Approval(
                paper_id=paper.id,
                reviewer_id=reviewer.id,
                action="approve",
                comment="Section A is well balanced.",
            ),
        ]
    )
    await db_session.commit()

    paper_id = paper.id
    reviewer_id = reviewer.id
    db_session.expunge_all()

    stored = (
        await db_session.execute(
            sa.select(GeneratedPaper).where(GeneratedPaper.id == paper_id)
        )
    ).scalar_one()
    # doc 03 §5 gives `generated_papers.status` a 'draft' default; the workflow moves it.
    assert stored.status == "draft"
    # Asserted here rather than straight after the flush on purpose: `row_version` is a
    # *server* default, so its value is only reliably in hand once a SELECT has fetched it.
    assert stored.row_version == 1
    assert stored.total_marks == Decimal("20.0")
    assert stored.duration_min == 45
    assert stored.blueprint["total_marks"] == 20
    assert stored.deleted_at is None
    assert isinstance(stored.created_at, dt.datetime)
    assert stored.created_at.tzinfo is not None

    version = (
        await db_session.execute(
            sa.select(PaperVersion).where(PaperVersion.paper_id == paper_id)
        )
    ).scalar_one()
    assert version.label == "v1"
    assert version.content["sections"][0]["questions"] == ["Q1", "Q2"]
    assert version.docx_key is None
    # doc 03 §5 calls this table immutable and still lists `updated_at`. It is nullable
    # with no default and nothing wires an onupdate to it, so a fresh row has NULL here —
    # which is the whole reason the column is reported as an open item rather than used.
    assert version.updated_at is None

    trail = (
        (
            await db_session.execute(
                sa.select(Approval)
                .where(Approval.paper_id == paper_id)
                .order_by(Approval.action)
            )
        )
        .scalars()
        .all()
    )
    assert [entry.action for entry in trail] == ["approve", "submit"]
    assert trail[0].reviewer_id == reviewer_id
    assert trail[0].comment == "Section A is well balanced."
    assert trail[1].comment is None


async def test_an_approval_trail_written_in_one_transaction_has_no_order(
    db_session, tenant, curriculum
):
    """**Measuring a gap.** ``created_at`` cannot sequence events inside a transaction.

    ``func.now()`` compiles to ``CURRENT_TIMESTAMP``, which is the *transaction* start
    time, not the statement time. Two approvals appended in one transaction therefore
    carry byte-identical timestamps, and ``ix_approvals_paper_id_created_at`` — the index
    doc 03 §5 provides for reading the trail — cannot break the tie. ``approvals`` has no
    sequence column, so the trail's order is undefined in exactly the case a review
    workflow is most likely to produce it (submit-then-approve in one request).

    The same reasoning applies to ``audit_logs.ts``. Both are reported in the hand-off
    notes; neither is fixed here, because inventing a ``seq bigint`` column doc 03 does
    not specify would be inventing schema.
    """

    paper = _paper(tenant, curriculum)
    db_session.add(paper)
    await db_session.flush()
    db_session.add_all(
        [
            Approval(paper_id=paper.id, action="submit"),
            Approval(paper_id=paper.id, action="comment"),
        ]
    )
    await db_session.commit()

    stamps = (
        (
            await db_session.execute(
                sa.select(Approval.created_at).where(Approval.paper_id == paper.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(stamps) == 2
    assert stamps[0] == stamps[1], (
        "two rows inserted in one transaction have different created_at values, so "
        "now() is no longer the transaction timestamp - re-check this gap"
    )


async def test_approval_actions_are_a_closed_vocabulary(db_session, tenant, curriculum):
    """``ck_approvals_action`` is enforced by the server (ADR-011: CHECK, never ENUM)."""

    paper = _paper(tenant, curriculum)
    db_session.add(paper)
    await db_session.flush()

    db_session.add(Approval(paper_id=paper.id, action="rubber_stamp"))
    with pytest.raises(IntegrityError) as exc_info:
        await db_session.flush()
    assert "ck_approvals_action" in str(exc_info.value)
    await db_session.rollback()


async def test_a_paper_cannot_have_two_versions_with_the_same_label(
    db_session, tenant, curriculum
):
    """``uq_paper_versions_paper_id_label`` is per paper, not global.

    Both halves matter. Without the first, "v1" could mean two different renders of one
    paper; without the second, the second paper anyone creates could not have a "v1".
    """

    first_paper = _paper(tenant, curriculum)
    second_paper = _paper(tenant, curriculum, title="Unit Test 2 - Polynomials")
    db_session.add_all([first_paper, second_paper])
    await db_session.flush()

    db_session.add(PaperVersion(paper_id=first_paper.id, label="v1", content={}))
    await db_session.commit()

    first_paper_id = first_paper.id
    second_paper_id = second_paper.id

    db_session.add(PaperVersion(paper_id=first_paper_id, label="v1", content={}))
    with pytest.raises(IntegrityError) as exc_info:
        await db_session.flush()
    assert "uq_paper_versions_paper_id_label" in str(exc_info.value)
    await db_session.rollback()
    db_session.expunge_all()

    # Same label, different paper: accepted.
    db_session.add(PaperVersion(paper_id=second_paper_id, label="v1", content={}))
    await db_session.commit()
    labels = await db_session.execute(
        sa.select(sa.func.count())
        .select_from(PaperVersion)
        .where(
            PaperVersion.label == "v1",
            PaperVersion.paper_id.in_([first_paper_id, second_paper_id]),
        )
    )
    assert labels.scalar_one() == 2


# ---------------------------------------------------------------------------
# Revisions 0012-0013 — subscriptions, the deferred FK, and the usage ledger
# ---------------------------------------------------------------------------


async def test_revision_0018_completes_the_deferred_subscription_foreign_key(
    db_session, tenant
):
    """The one expand-migrate-contract sequence in the chain, proved end to end.

    Revision 0002 created ``organizations.subscription_id`` as a bare column because
    ``subscriptions`` did not exist yet; revision 0018 adds the constraint. A catalog dump
    can show a row in ``pg_constraint``, which is why this test does the two things a dump
    cannot: it stores a real id through the column, and it proves a *wrong* id is refused.
    Until 0018 has run, the second half of this test passes any UUID at all.
    """

    subscription = Subscription(
        org_id=tenant.org_id,
        plan="standard",
        seats=25,
        period_start=dt.date(2026, 4, 1),
        period_end=dt.date(2027, 3, 31),
        provider_ref="sub_test_0001",
    )
    db_session.add(subscription)
    await db_session.flush()

    subscription_id = subscription.id
    organization = await db_session.get(Organization, tenant.org_id)
    assert organization is not None
    # The column is set directly rather than through the `current_subscription`
    # relationship: the relationship carries post_update=True to break the FK cycle, and
    # this test is about the constraint, not about SQLAlchemy's flush ordering.
    organization.subscription_id = subscription_id
    await db_session.commit()
    db_session.expunge_all()

    stored = await db_session.get(Organization, tenant.org_id)
    assert stored is not None and stored.subscription_id == subscription_id
    stored_subscription = await db_session.get(Subscription, subscription_id)
    assert stored_subscription is not None
    # Read back rather than asserted after the flush: 'active' is a server default.
    assert stored_subscription.status == "active", "doc 03 §5 defaults subscriptions.status"
    assert stored_subscription.period_end == dt.date(2027, 3, 31)
    assert stored_subscription.seats == 25

    # A second subscription may not reuse the provider's reference (doc 03 §5). Note this
    # constraint is a plain UNIQUE, not partial on deleted_at: a soft-deleted row keeps
    # its provider_ref reserved, which is deliberate and deviates from ADR-004.
    db_session.add(
        Subscription(org_id=tenant.org_id, plan="pro", provider_ref="sub_test_0001")
    )
    with pytest.raises(IntegrityError) as exc_info:
        await db_session.flush()
    assert "uq_subscriptions_provider_ref" in str(exc_info.value)
    await db_session.rollback()
    db_session.expunge_all()

    # And the deferred foreign key really is a foreign key.
    organization = await db_session.get(Organization, tenant.org_id)
    assert organization is not None
    organization.subscription_id = uuid.uuid4()
    with pytest.raises(IntegrityError) as exc_info:
        await db_session.flush()
    assert "fk_organizations_subscription_id_subscriptions" in str(exc_info.value)
    await db_session.rollback()


async def test_the_usage_ledger_is_append_only_by_construction(db_session, tenant):
    """``usage.id`` is ``GENERATED ALWAYS AS IDENTITY``, so a client cannot choose it.

    That is the structural half of CN-22's append-only rule: a ledger whose ids are
    assigned by the client can have gaps forged into it. The INSERT that must fail is
    written as raw SQL because the point is what *PostgreSQL* refuses, and an ORM that
    quietly dropped the column would turn this proof into a tautology.

    ``"usage"`` is quoted throughout: it is a reserved word in the SQL standard.
    """

    metered = Usage(
        org_id=tenant.org_id,
        user_id=tenant.user_id,
        metric="ai_tokens",
        qty=Decimal("1450"),
    )
    db_session.add(metered)
    await db_session.flush()
    assert isinstance(metered.id, int) and metered.id > 0, (
        "the identity column did not assign an id"
    )
    await db_session.commit()

    with pytest.raises(DBAPIError) as exc_info:
        await db_session.execute(
            sa.text(
                'INSERT INTO "usage" (id, org_id, metric, qty) '
                "VALUES (1, CAST(:org_id AS uuid), 'ocr_pages', 3)"
            ),
            {"org_id": str(tenant.org_id)},
        )
    assert "cannot insert" in str(exc_info.value)
    await db_session.rollback()


async def test_the_usage_ledger_refuses_a_non_positive_quantity(db_session, tenant):
    """``ck_usage_qty_positive`` holds — and that has a consequence worth pinning down.

    doc 03 §5 asks for ``qty > 0``, so it is implemented as written. The consequence is
    that a metering correction cannot be a negative row: reversing an over-count needs a
    compensating mechanism the schema does not provide. Reported, not resolved.
    """

    for rejected in (Decimal("0"), Decimal("-5")):
        db_session.add(
            Usage(org_id=tenant.org_id, metric="papers_generated", qty=rejected)
        )
        with pytest.raises(IntegrityError) as exc_info:
            await db_session.flush()
        assert "ck_usage_qty_positive" in str(exc_info.value)
        await db_session.rollback()
        db_session.expunge_all()


# ---------------------------------------------------------------------------
# Revisions 0014-0015 — audit trail and daily analytics
# ---------------------------------------------------------------------------


async def test_the_audit_trail_accepts_any_action_string(db_session, tenant):
    """**Measuring a gap.** ``audit_logs.action`` has no closed vocabulary.

    Every other status-like text column in this schema carries a CHECK (ADR-011). This one
    does not, for a reason: doc 03 defines no vocabulary for it, and a CHECK over a guessed
    list would make the table *reject* the recording of an action nobody anticipated —
    losing exactly the event the trail exists to preserve. An unconstrained column is the
    safer failure mode here, and the naming discipline belongs in the application.

    Also asserted: ``org_id`` accepts NULL, which is what makes platform-level events
    (a Super Admin signing in) recordable at all.
    """

    unanticipated_action = "an.action.no.vocabulary.anticipated"
    db_session.add_all(
        [
            AuditLog(
                org_id=tenant.org_id,
                actor_id=tenant.user_id,
                action="paper.approved",
                entity="generated_papers",
                entity_id=uuid.uuid4(),
                ip="203.0.113.7",
                meta={"paper_title": "Unit Test 1"},
            ),
            AuditLog(action=unanticipated_action),
        ]
    )
    await db_session.commit()
    db_session.expunge_all()

    rows = (
        (
            await db_session.execute(
                sa.select(AuditLog)
                .where(AuditLog.action.in_([unanticipated_action, "paper.approved"]))
                .order_by(AuditLog.action)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2
    unanticipated, tenant_event = rows
    assert unanticipated.action == unanticipated_action
    assert unanticipated.org_id is None and unanticipated.actor_id is None
    assert unanticipated.meta is None
    assert tenant_event.action == "paper.approved"
    # Worth pinning down for P1-T5: the model annotates `ip` as `str`, but asyncpg decodes
    # PostgreSQL's `inet` into an `ipaddress` object, so a caller gets IPv4Address here and
    # a plain string is only what goes *in*. The annotation is the thing that is wrong.
    assert str(tenant_event.ip) == "203.0.113.7"
    assert tenant_event.meta == {"paper_title": "Unit Test 1"}
    assert all(isinstance(row.id, int) for row in rows)


async def test_analytics_daily_is_keyed_by_organization_and_day(db_session, tenant):
    """The one composite-primary-key table in the schema (doc 03 §5: ``pk(org_id, day)``).

    The duplicate is attempted in raw SQL deliberately. Adding a second ORM instance with
    the same identity raises SQLAlchemy's own ``FlushError`` before a statement is ever
    emitted, which would prove something about the ORM's identity map rather than about
    PostgreSQL's primary key.
    """

    db_session.add(
        AnalyticsDaily(
            org_id=tenant.org_id,
            day=dt.date(2026, 4, 1),
            papers=7,
            ai_questions=120,
            retrieved_questions=45,
            avg_difficulty=Decimal("2.5"),
            coverage={"chapters": {"Real Numbers": 0.8}},
        )
    )
    await db_session.commit()
    db_session.expunge_all()

    stored = (
        await db_session.execute(
            sa.select(AnalyticsDaily).where(
                AnalyticsDaily.org_id == tenant.org_id,
                AnalyticsDaily.day == dt.date(2026, 4, 1),
            )
        )
    ).scalar_one()
    # An unqualified `numeric` keeps the scale it was given: doc 03 §5 specifies no
    # precision for avg_difficulty, and this is what that choice means in practice.
    assert stored.avg_difficulty == Decimal("2.5")
    assert stored.coverage["chapters"]["Real Numbers"] == 0.8

    with pytest.raises(IntegrityError) as exc_info:
        await db_session.execute(
            sa.text(
                "INSERT INTO analytics_daily "
                "(org_id, day, papers, ai_questions, retrieved_questions, avg_difficulty) "
                "VALUES (CAST(:org_id AS uuid), DATE '2026-04-01', 1, 1, 1, 1.0)"
            ),
            {"org_id": str(tenant.org_id)},
        )
    assert "pk_analytics_daily" in str(exc_info.value)
    await db_session.rollback()


# ---------------------------------------------------------------------------
# Revision 0018 — the generated search column
# ---------------------------------------------------------------------------


async def test_search_document_is_maintained_by_postgresql(db_session, tenant, curriculum):
    """``question_bank.search_document`` needs no application code and no backfill.

    Three claims, each with its own assertion:

    1. **A plain INSERT populates it.** Nothing sets the column — it is
       ``GENERATED ALWAYS AS ... STORED``, so SQLAlchemy omits it from the INSERT and the
       server computes it. This is why doc 03 §9 step 19's "backfill derived search
       artifacts" is vacuous by construction: ``ADD COLUMN`` on a stored generated column
       rewrites the table, and every row afterwards is written with it already filled.
    2. **It indexes the answer, not just the stem.** The expression spans ``stem``,
       ``answer`` and ``rubric``, so a word that appears only inside the ``answer`` jsonb
       is findable. That is the half of the expression a stem-only index would miss.
    3. **An UPDATE recomputes it.** No trigger exists to keep it fresh, and none is
       needed.

    The English dictionary is pinned in the expression (``'english'::regconfig``, required
    for immutability), while ``question_bank.language`` is per row. A Hindi question is
    therefore stemmed as English — reported in the hand-off notes.
    """

    question = _question(tenant, curriculum)
    db_session.add(question)
    await db_session.commit()

    question_id = question.id
    db_session.expunge_all()

    stored = (
        await db_session.execute(
            sa.select(QuestionBank).where(QuestionBank.id == question_id)
        )
    ).scalar_one()
    assert stored.search_document is not None, (
        "the generated column is empty, so nothing is maintaining it"
    )
    # Asserted on the shape rather than on a specific lexeme: the exact output of the
    # English Snowball stemmer is not this schema's promise, but `'lexeme':position` pairs
    # are what a tsvector is, and asyncpg hands the column back as that text form.
    assert "'" in stored.search_document and ":" in stored.search_document

    # 1 + 2: stemming works ("roots" -> "root"), and the answer text is searchable.
    assert await _fts_count(db_session, question_id, "irrational square roots") == 1
    assert await _fts_count(db_session, question_id, "parity") == 1
    assert await _fts_count(db_session, question_id, "photosynthesis") == 0

    # 3: editing the row recomputes it. Legitimate here - the row is a draft, and BR-8
    # only forbids editing an *approved* question (see test_question_lineage.py).
    stored.stem = "Explain why photosynthesis requires chlorophyll."
    await db_session.commit()
    db_session.expunge_all()

    assert await _fts_count(db_session, question_id, "photosynthesis") == 1
    assert await _fts_count(db_session, question_id, "irrational") == 0
    # The answer was not edited, so its contribution survives the recomputation.
    assert await _fts_count(db_session, question_id, "parity") == 1
