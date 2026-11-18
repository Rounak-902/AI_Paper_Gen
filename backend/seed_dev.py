"""Minimal development seed for the PaperGen schema (doc 03 §9 step 18, "seed data").

doc 03 §9 step 18 asks for "seed data" alongside indexes, constraints, RLS policies and
partitions. This file is where it lives, and **deliberately not** revision 0018. Three
reasons, in order of weight:

1. **Alembic revisions are forward-only in production.** A seeded row that turns out to be
   wrong cannot be corrected by editing the revision that inserted it — the revision has
   already run. Fixing it means another migration, and the history now contains a lie.
2. **doc 03 names no seed content whatsoever.** Not one board code, not one plan name.
   Anything in a migration would therefore be invented data wearing a schema migration's
   authority, in a chain whose entire premise is "implement doc 03, report the gaps".
3. **The records a real deployment needs are tenant-configurable, not structural.** CBSE,
   ICSE and the state boards are curriculum *data* an institute owns and edits. A
   migration that inserts them makes them undeletable-by-design in every environment,
   including the ones that want their own.

What this creates
-----------------
One coherent tenant, enough to exercise the schema end to end by hand: an organization
with a branch, an admin and a teacher, a CBSE → Class 10 → Mathematics → Real Numbers
curriculum path under a 2025-26 curriculum version, one learning outcome, a program wired
to that subject and that teacher, one paper template, and two questions — one approved and
one draft — rooted as single-version lineages.

It does **not** create subscriptions, usage, audit, analytics, prompt or notification rows.
Those are all produced by running the product, and seeding them would fabricate history:
a usage row nobody metered, an audit entry for an action nobody took.

Safety
------
Two independent guards, because the ``.env`` in this repository points ``DATABASE_URL`` at
a **remote managed instance** and this script writes rows:

* **There is no default DSN.** ``--url`` or ``SEED_DATABASE_URL`` is required, and
  ``settings.DATABASE_URL`` is never consulted. Seeding the wrong database has to be
  something you typed, not something you forgot to override.
* **Non-local hosts are refused** unless ``--allow-remote`` is passed, and
  ``ENVIRONMENT=production`` is refused outright with no override at all.

Idempotency
-----------
Every row has a hardcoded UUID and is inserted only if ``session.get()`` returns nothing,
so re-running is a no-op that reports what it found. That property is what makes this
script safe to call from the verification harness after every ``alembic upgrade head``,
which is also how it stays honest: a seed that silently stopped matching the schema would
fail there rather than the first time a developer needed it.

Usage::

    cd backend
    python seed_dev.py --url postgresql+asyncpg://postgres:...@localhost:5432/papergen

Exit codes: 0 on success (including "already seeded"), 1 on a refused DSN or a database
error.
"""
from __future__ import annotations

import argparse
import asyncio
import decimal
import hashlib
import os
import sys
import uuid
from typing import Any, Optional

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.models import (
    Board,
    Branch,
    Chapter,
    Class,
    CurriculumVersion,
    LearningOutcome,
    Membership,
    Organization,
    Program,
    ProgramSubject,
    ProgramTeacher,
    QuestionBank,
    Subject,
    Template,
    User,
)

#: Hosts considered local. Anything else needs --allow-remote.
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0", ""})


def _uid(suffix: str) -> uuid.UUID:
    """A stable, obviously-synthetic UUID from a 12-hex-digit suffix.

    The all-zero leading groups make a seeded row recognisable at a glance in any query
    result, which matters when a developer is trying to work out whether the data in front
    of them came from this file or from using the app. The ``4`` and ``8`` nibbles keep
    these well-formed as version-4 variant-1 UUIDs — PostgreSQL's ``uuid`` type does not
    check either, but a client library might.
    """

    return uuid.UUID(f"00000000-0000-4000-8000-{suffix}")


# -- Identity and tenancy ----------------------------------------------------
ORG_ID = _uid("000000000001")
BRANCH_ID = _uid("000000000002")
ADMIN_ID = _uid("000000000003")
TEACHER_ID = _uid("000000000004")
ADMIN_MEMBERSHIP_ID = _uid("000000000005")
TEACHER_MEMBERSHIP_ID = _uid("000000000006")
# -- Curriculum --------------------------------------------------------------
BOARD_ID = _uid("000000000010")
VERSION_ID = _uid("000000000011")
CLASS_ID = _uid("000000000012")
SUBJECT_ID = _uid("000000000013")
CHAPTER_ID = _uid("000000000014")
OUTCOME_ID = _uid("000000000015")
# -- Programs and templates --------------------------------------------------
PROGRAM_ID = _uid("000000000020")
TEMPLATE_ID = _uid("000000000021")
# -- Questions ---------------------------------------------------------------
QUESTION_APPROVED_ID = _uid("000000000030")
QUESTION_DRAFT_ID = _uid("000000000031")


def _content_hash(stem: str) -> str:
    """The dedup key doc 03 §5 requires on ``question_bank``.

    SHA-256 of the stem is a stand-in, not the production rule: whatever the ingestion
    pipeline eventually hashes (normalised stem? stem plus options plus answer?) is a
    P2 decision, and doc 03 specifies the column without specifying the function. Kept
    here so the seeded rows carry a value that is at least stable and collision-free
    across re-runs.
    """

    return hashlib.sha256(stem.encode("utf-8")).hexdigest()


def _resolve_url(explicit: Optional[str]) -> URL:
    """The DSN to seed, as a ``URL`` object. Exits 1 rather than raising.

    Returns the object and never a string - see the comment on the return statement.
    """

    url = explicit or os.environ.get("SEED_DATABASE_URL")
    if not url:
        print(
            "seed_dev: no target database. Pass --url or set SEED_DATABASE_URL.\n"
            "          DATABASE_URL is deliberately NOT used as a fallback - this script "
            "writes rows,\n          and that variable may point at a shared or remote "
            "database.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    parsed = make_url(url)
    # A bare `postgresql://` DSN would select a *sync* driver, which create_async_engine
    # rejects with a confusing error. Normalising here is friendlier than documenting it.
    if not parsed.drivername.startswith("postgresql+"):
        parsed = parsed.set(drivername="postgresql+asyncpg")

    # Returned as a URL object, deliberately, and never as a string. SQLAlchemy's URL
    # defines __repr__ and no __str__, so `str(url)` resolves to __repr__, which calls
    # `render_as_string()` - and that method's default is hide_password=True, which
    # substitutes the literal three characters `***` for the password. This function
    # used to end in `return str(parsed)`, so the seed connected with `***` as its
    # password and asyncpg answered InvalidPasswordError: a message that reads like a
    # wrong credential in docker-compose.yml rather than a discarded one, and one the
    # verification log disguised further by redacting real passwords the same way.
    # Passing the object through means no accidental interpolation can drop it;
    # create_async_engine and make_url both take a URL unchanged.
    return parsed


def _check_guards(url: URL, *, allow_remote: bool) -> None:
    """Refuse production outright; refuse remote hosts without an explicit opt-in."""

    environment = os.environ.get("ENVIRONMENT", "").strip().lower()
    if environment == "production":
        print(
            "seed_dev: refusing to run with ENVIRONMENT=production. There is no override "
            "flag for this on purpose.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    host = (url.host or "").lower()
    if host not in _LOCAL_HOSTS and not allow_remote:
        # The host is safe to print; the DSN carries a password and is never echoed.
        print(
            f"seed_dev: refusing to seed remote host {host!r}. Point --url at a local "
            "database (infra/docker-compose.yml starts one), or pass --allow-remote if "
            "you are certain.",
            file=sys.stderr,
        )
        raise SystemExit(1)


async def _ensure(session: AsyncSession, model: Any, pk: Any, build) -> bool:
    """Insert ``build()`` under primary key ``pk`` unless it is already there.

    Returns True when a row was created. ``build`` is a callable rather than an instance
    so that nothing is constructed on the "already present" path — cheap, and it keeps
    the flush ordering below easy to read.
    """

    if await session.get(model, pk) is not None:
        return False
    session.add(build())
    await session.flush()
    return True


async def seed(session: AsyncSession) -> dict[str, int]:
    """Create the development tenant. Idempotent; returns per-group creation counts."""

    created: dict[str, int] = {"tenancy": 0, "curriculum": 0, "programs": 0, "questions": 0}

    # -- Identity and tenancy (migration 0002) -------------------------------
    # No password_hash on either user: hashing and login are P1-T7, and inventing a hash
    # here would put an unverifiable credential in a file people copy from. These accounts
    # exist to own rows, not to log in.
    created["tenancy"] += await _ensure(
        session,
        Organization,
        ORG_ID,
        lambda: Organization(
            id=ORG_ID,
            name="Sunrise Coaching Centre",
            org_type="coaching_center",
            region="ap-south-1",
            status="active",
        ),
    )
    created["tenancy"] += await _ensure(
        session,
        Branch,
        BRANCH_ID,
        lambda: Branch(id=BRANCH_ID, org_id=ORG_ID, name="Main Campus", region="ap-south-1"),
    )
    created["tenancy"] += await _ensure(
        session,
        User,
        ADMIN_ID,
        lambda: User(id=ADMIN_ID, email="admin@papergen.local", full_name="Dev Admin"),
    )
    created["tenancy"] += await _ensure(
        session,
        User,
        TEACHER_ID,
        lambda: User(id=TEACHER_ID, email="teacher@papergen.local", full_name="Dev Teacher"),
    )
    # The admin's membership carries no branch_id: doc 03 §9.1's D-2 makes the column
    # nullable precisely so an org-wide role is not forced to pick a branch.
    created["tenancy"] += await _ensure(
        session,
        Membership,
        ADMIN_MEMBERSHIP_ID,
        lambda: Membership(
            id=ADMIN_MEMBERSHIP_ID, user_id=ADMIN_ID, org_id=ORG_ID, role="coaching_admin"
        ),
    )
    created["tenancy"] += await _ensure(
        session,
        Membership,
        TEACHER_MEMBERSHIP_ID,
        lambda: Membership(
            id=TEACHER_MEMBERSHIP_ID,
            user_id=TEACHER_ID,
            org_id=ORG_ID,
            branch_id=BRANCH_ID,
            role="teacher",
        ),
    )

    # -- Curriculum (migrations 0003, 0004) ----------------------------------
    # Board, class and version are global: doc 03 §3 makes curriculum platform-owned, and
    # none of these tables has an org_id.
    created["curriculum"] += await _ensure(
        session,
        Board,
        BOARD_ID,
        lambda: Board(id=BOARD_ID, code="CBSE", name="Central Board of Secondary Education"),
    )
    created["curriculum"] += await _ensure(
        session,
        CurriculumVersion,
        VERSION_ID,
        lambda: CurriculumVersion(
            id=VERSION_ID,
            board_id=BOARD_ID,
            academic_year="2025-26",
            label="CBSE 2025-26 syllabus",
            status="active",
        ),
    )
    created["curriculum"] += await _ensure(
        session,
        Class,
        CLASS_ID,
        lambda: Class(id=CLASS_ID, board_id=BOARD_ID, name="Class 10"),
    )
    created["curriculum"] += await _ensure(
        session,
        Subject,
        SUBJECT_ID,
        lambda: Subject(
            id=SUBJECT_ID,
            class_id=CLASS_ID,
            curriculum_version_id=VERSION_ID,
            name="Mathematics",
        ),
    )
    created["curriculum"] += await _ensure(
        session,
        Chapter,
        CHAPTER_ID,
        lambda: Chapter(
            id=CHAPTER_ID,
            subject_id=SUBJECT_ID,
            curriculum_version_id=VERSION_ID,
            seq=1,
            title="Real Numbers",
        ),
    )
    created["curriculum"] += await _ensure(
        session,
        LearningOutcome,
        OUTCOME_ID,
        lambda: LearningOutcome(
            id=OUTCOME_ID,
            chapter_id=CHAPTER_ID,
            code="LO-10-MATH-1.1",
            description=(
                "Apply Euclid's division lemma to compute the HCF of two positive integers."
            ),
        ),
    )

    # -- Programs and templates (migrations 0005, 0009) ----------------------
    created["programs"] += await _ensure(
        session,
        Program,
        PROGRAM_ID,
        lambda: Program(
            id=PROGRAM_ID,
            org_id=ORG_ID,
            branch_id=BRANCH_ID,
            name="Class 10 CBSE - 2025-26",
            exam_target="CBSE Board",
            academic_year="2025-26",
        ),
    )
    # Composite primary keys, so `get()` takes a tuple in declaration order.
    created["programs"] += await _ensure(
        session,
        ProgramSubject,
        (PROGRAM_ID, SUBJECT_ID),
        lambda: ProgramSubject(program_id=PROGRAM_ID, subject_id=SUBJECT_ID),
    )
    created["programs"] += await _ensure(
        session,
        ProgramTeacher,
        (PROGRAM_ID, TEACHER_ID),
        lambda: ProgramTeacher(program_id=PROGRAM_ID, user_id=TEACHER_ID),
    )
    # A deliberately thin layout. The real template schema is FR-5's subject, and a
    # plausible-looking one here would be mistaken for a specification.
    created["programs"] += await _ensure(
        session,
        Template,
        TEMPLATE_ID,
        lambda: Template(
            id=TEMPLATE_ID,
            org_id=ORG_ID,
            name="Unit Test - 20 marks",
            created_by=ADMIN_ID,
            status="active",
            layout={
                "total_marks": 20,
                "duration_minutes": 45,
                "sections": [
                    {"name": "A", "qtype": "mcq", "count": 5, "marks_each": 1},
                    {"name": "B", "qtype": "short_answer", "count": 5, "marks_each": 3},
                ],
            },
        ),
    )

    # -- Questions (migrations 0006, 0018, 0019) -----------------------------
    # `root_question_id` is set explicitly to the row's own id. That is the repository
    # layer's job in the real system (P1-T5, ADR-003) and there is no database default
    # that can do it, so a seed writing rows directly has to do it by hand — otherwise
    # these rows sit outside `uq_question_bank_root_question_id_current` entirely, which
    # is exactly the gap revision 0019 exists to close.
    approved_stem = (
        "Show that any positive odd integer is of the form 6q + 1, 6q + 3 or 6q + 5, "
        "where q is some integer."
    )
    created["questions"] += await _ensure(
        session,
        QuestionBank,
        QUESTION_APPROVED_ID,
        lambda: QuestionBank(
            id=QUESTION_APPROVED_ID,
            root_question_id=QUESTION_APPROVED_ID,
            org_id=ORG_ID,
            scope="org",
            source="institute_bank",
            board_id=BOARD_ID,
            class_id=CLASS_ID,
            subject_id=SUBJECT_ID,
            chapter_id=CHAPTER_ID,
            qtype="long_answer",
            marks=decimal.Decimal("3.0"),
            difficulty="medium",
            bloom="apply",
            language="en",
            stem=approved_stem,
            answer={"text": "Apply Euclid's division lemma with divisor 6."},
            rubric={"steps": ["States the lemma", "Enumerates remainders", "Concludes"]},
            learning_outcome_ids=[OUTCOME_ID],
            content_hash=_content_hash(approved_stem),
            status="approved",
            created_by=TEACHER_ID,
        ),
    )
    draft_stem = "The decimal expansion of 13/3125 terminates after how many places?"
    created["questions"] += await _ensure(
        session,
        QuestionBank,
        QUESTION_DRAFT_ID,
        lambda: QuestionBank(
            id=QUESTION_DRAFT_ID,
            root_question_id=QUESTION_DRAFT_ID,
            org_id=ORG_ID,
            scope="org",
            source="institute_bank",
            board_id=BOARD_ID,
            class_id=CLASS_ID,
            subject_id=SUBJECT_ID,
            chapter_id=CHAPTER_ID,
            qtype="mcq",
            marks=decimal.Decimal("1.0"),
            difficulty="easy",
            bloom="understand",
            language="en",
            stem=draft_stem,
            options={"choices": ["3", "4", "5", "6"]},
            answer={"correct": "5"},
            learning_outcome_ids=[OUTCOME_ID],
            content_hash=_content_hash(draft_stem),
            status="draft",
            created_by=TEACHER_ID,
        ),
    )

    await session.commit()
    return created


async def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Seed a development database with one coherent PaperGen tenant."
    )
    parser.add_argument(
        "--url",
        default=None,
        help=(
            "target DSN, e.g. postgresql+asyncpg://postgres:...@localhost:5432/papergen. "
            "Falls back to SEED_DATABASE_URL. DATABASE_URL is never used."
        ),
    )
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="permit a non-localhost host. Off by default; see the module docstring.",
    )
    args = parser.parse_args(argv)

    url = _resolve_url(args.url)
    _check_guards(url, allow_remote=args.allow_remote)

    print(f"seed_dev: target {url.host}:{url.port or 5432}/{url.database}")

    engine = create_async_engine(url, pool_pre_ping=True)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            created = await seed(session)
    except SQLAlchemyError as exc:
        # Only the exception type and message, never the DSN.
        print(f"seed_dev: FAILED - {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        await engine.dispose()

    total = sum(created.values())
    detail = ", ".join(f"{group} {count}" for group, count in created.items())
    if total:
        print(f"seed_dev: created {total} row(s) ({detail})")
    else:
        print("seed_dev: already seeded, nothing to do")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
