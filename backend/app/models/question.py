"""``question_bank`` — the centre of the model (doc 03 §5, §9 step 6; ADR-003).

ADR-003 is the reason this table looks the way it does: questions are the core
domain entity and papers are *projections* of them, not the other way round. Every
question the platform has ever held — curated, textbook-extracted, previous-year,
AI-generated — lives in this one table with a ``source`` discriminator, rather than
in five near-identical tables that would each need their own retrieval path.

Versioning (BR-8, ADR-003)
--------------------------
An approved question is **immutable**. Editing one does not UPDATE it; it *forks* a
new row that carries:

* ``root_question_id`` — the lineage identity, shared by every version;
* ``previous_version_id`` — the row this one was forked from;
* ``version_number`` — 1, 2, 3 … within the lineage;
* ``is_current`` — true on exactly one row per lineage (the partial unique index
  below is what makes "exactly one" a database fact rather than a convention);
* ``content_hash`` — the dedup key, so the same question arriving twice from two
  sources is recognisable.

The old version stays queryable forever, which is the entire point: a paper printed
last term must still resolve to the text that was actually printed.

**Immutability is now a database rule, not a convention** (revision 0020). A
``BEFORE UPDATE`` trigger, ``trg_question_bank_reject_approved_edit``, fires on rows
whose *previous* status was ``approved`` and raises ``23514`` if the UPDATE changes any
column outside ``status``, ``updated_at``, ``row_version``, ``is_current``,
``deleted_at`` and ``search_document``. A CHECK constraint could not express this — it
cannot see ``OLD`` — so until 0020 the invariant lived entirely in a repository layer
that does not exist yet, and any ``psql`` session could rewrite a printed question.

Three things follow that are not obvious from reading this model:

* ``is_current`` and ``deleted_at`` *must* stay mutable. Forking requires clearing the
  old version's ``is_current`` (the partial unique index below), and ``deleted_at`` is
  the only ADR-004-sanctioned retirement path. Blocking either would forbid the
  versioning model the trigger exists to protect.
* The comparison is a whole-row ``jsonb`` diff, so **a column added to this model later
  is protected automatically** — ``prompt_version_id`` below inherited it without
  touching the function.
* The trigger is invisible to ``alembic check`` in both directions. Nothing in
  autogenerate will notice it being dropped by hand; ``tests/test_question_immutability
  .py`` and the catalog assertions in ``scripts/verify-p1-t4.ps1`` are the only guards.

**Open item, flagged in the P1-T4 hand-off notes.** doc 03 §5 declares
``root_question_id`` *nullable*, and PostgreSQL unique indexes are NULLS DISTINCT.
So both lineage constraints below are inert for any row whose ``root_question_id``
is NULL — meaning BR-8 is enforced only once a lineage has been explicitly rooted.
The column is left nullable exactly as doc 03 specifies rather than tightened here,
but the consequence is real: whatever creates questions must set
``root_question_id = id`` on the first version. The round-trip test in
``tests/test_question_lineage.py`` roots its lineages explicitly and asserts the
constraint bites, so the enforcement gap is measured rather than assumed.

Revision 0019 (doc 03 §9 step 19, "backfill current pointers") now closes part of
this: it runs an idempotent ``UPDATE ... SET root_question_id = id WHERE
root_question_id IS NULL AND previous_version_id IS NULL``, so no *unforked* row is
left unrooted. The second predicate is what keeps the backfill honest — self-rooting a
row that has a predecessor would split one lineage into two in a way no constraint
detects, so those rows are counted and logged instead of guessed at.

It deliberately stops short of adding NOT NULL, reversing what the paragraph above
originally anticipated, and the reason is mechanical rather than a change of heart: a
column default cannot reference another column, so ``root_question_id`` can only be set
after ``id`` is known. NOT NULL would therefore force every INSERT to supply both,
moving UUID generation out of ``gen_random_uuid()`` and into Python and breaking any
plain ``INSERT INTO question_bank (...)``. Rooting new rows stays the repository
layer's job (P1-T5); see revision 0019's docstring for the full argument.

Search artifacts — doc 03 §9 step 18, migration 0018
---------------------------------------------------
The generated ``tsvector`` column ``search_document``, the GIN index over it, the two
GIN indexes on the UUID arrays, and the ``pg_trgm`` index on ``stem``. They arrive in
revision 0018, the one revision that owns search artifacts, and — unlike round 1, when
they were held back so the models and the head revision matched — they are all declared
on this model.

That is a **deliberate correction to doc 03 §8**, which says to "write partitions, RLS
policies, GIN/trigram indexes, generated ``tsvector`` columns … as raw SQL inside
Alembic revisions" because "Alembic autogenerate does not model these". Half of that is
right and half is wrong, and the wrong half is not harmless:

* A **column** that exists in the database and not in ``Base.metadata`` is drift, full
  stop. ``alembic check`` reports it, and the next person to run
  ``--autogenerate`` gets a revision that drops it.
* SQLAlchemy 2.x does model every one of these declaratively — ``Computed`` for the
  generated column, ``postgresql_using="gin"`` for the access method,
  ``postgresql_ops={"stem": "gin_trgm_ops"}`` for the trigram opclass — so raw SQL buys
  nothing here and costs the drift check.
* What genuinely *cannot* be modelled is partitioning and RLS policies. Those really do
  need ``op.execute(...)`` with a matching downgrade, and neither is created by this
  task (partitioning: gap reported; RLS: P1-T5).

Revision 0018 therefore uses ``op.add_column``/``op.create_index`` for these, not
``op.execute``, and ``alembic check`` at head is what proves the declarations and the
DDL agree.

``question_embeddings`` is not created at all — see revision 0007's docstring and
ADR-007.
"""

from __future__ import annotations

import decimal as _decimal
import uuid as _uuid
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import (
    Base,
    RowVersionMixin,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    check_in,
    row_version_positive,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.curriculum import Board, Chapter, Class, Subject
    from app.models.organization import Organization
    from app.models.prompt import PromptVersion
    from app.models.user import User

#: doc 03 §7.3. ``intext`` is an in-textbook question (printed in the chapter body);
#: ``textbook`` is one extracted from the exercise sets. The distinction matters to
#: BR-4 provenance reporting, which is why they are separate values.
QUESTION_SOURCES: tuple[str, ...] = (
    "institute_bank",
    "textbook",
    "intext",
    "previous_year",
    "ai_generated",
    "template_generated",
)

#: doc 03 §7.3, **narrowed by revision 0023**.
#:
#: DELIBERATE DEVIATION FROM DOC 03, confirmed: §7.3 lists three values — ``global``,
#: ``org``, ``branch`` — and ``branch`` is dropped here. ``question_bank`` has no
#: ``branch_id`` column, and doc 03 §5 states conditional org_id rules for ``global``
#: and ``org`` only, so a row with ``scope = 'branch'`` recorded *that* it was
#: branch-scoped without recording **which** branch, and was indistinguishable from an
#: org-scoped row at query time. Revision 0006 shipped the value and reported the gap
#: (P1-T4 report item 10); the confirmed resolution is to remove the value until
#: branch-level scoping is actually designed, rather than invent the column, its
#: ``ON DELETE`` semantics and its RLS visibility rule here.
#:
#: Revision 0023 carries the reasoning, the pre-flight check, and the note that with
#: ``branch`` gone this column is fully derivable from ``org_id IS NULL``.
QUESTION_SCOPES: tuple[str, ...] = ("global", "org")

#: doc 03 §7.3.
QUESTION_DIFFICULTIES: tuple[str, ...] = ("easy", "medium", "hard")

#: doc 03 §7.3 — the six levels of the revised Bloom taxonomy. Nullable column, so
#: the CHECK admits NULL: a CHECK constraint rejects a row only when it evaluates to
#: FALSE, and ``NULL IN (...)`` is NULL.
QUESTION_BLOOM_LEVELS: tuple[str, ...] = (
    "remember",
    "understand",
    "apply",
    "analyze",
    "evaluate",
    "create",
)

#: doc 03 §7.3.
QUESTION_STATUSES: tuple[str, ...] = ("draft", "approved", "rejected", "archived")

#: doc 03 §7.3: "the 13 types QT-1 … QT-13 in 01_Product_Requirements.md". Order
#: preserved as QT-1 … QT-13 so the CHECK constraint reads back against that list.
QUESTION_TYPES: tuple[str, ...] = (
    "mcq",
    "true_false",
    "one_word",
    "fill_blank",
    "short_answer",
    "long_answer",
    "assertion_reason",
    "case_study",
    "numerical",
    "diagram_based",
    "match",
    "very_short_answer",
    "source_based",
)

#: The SQL expression behind the ``search_document`` generated column, kept as a single
#: module constant so the model and revision 0018 cannot drift apart in wording. See the
#: column definition for why each piece is shaped the way it is.
#:
#: Revision 0018 repeats this string as a frozen literal rather than importing it — a
#: migration that imports application code stops describing the schema as it was when
#: the migration was written, which is the one thing a migration is for.
_SEARCH_DOCUMENT_EXPRESSION = (
    "to_tsvector('english'::regconfig, "
    "coalesce(stem, '') || ' ' || "
    "coalesce(answer::text, '') || ' ' || "
    "coalesce(rubric::text, ''))"
)


class QuestionBank(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    SoftDeleteMixin,
    RowVersionMixin,
    Base,
):
    """One version of one question."""

    __tablename__ = "question_bank"

    # -- Lineage (ADR-003, BR-8) --------------------------------------------
    # RESTRICT on both, from doc 03 §5: "lineage references should generally be
    # restricted or set null to preserve history". Of the two options offered,
    # RESTRICT is the one that preserves history — SET NULL would keep the row and
    # silently erase which lineage it belonged to, leaving an orphan version that
    # neither BR-8 constraint can see. doc 03 does not choose; flagged.
    #
    # Consequence worth knowing: a row that roots its own lineage
    # (root_question_id = id) cannot be hard-deleted while RESTRICT is in force,
    # because it is its own referent. That is acceptable — ADR-004 forbids hard
    # deletes from application code outright — but a maintenance script doing tenant
    # erasure has to NULL the self-reference first.
    root_question_id: Mapped[Optional[_uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("question_bank.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    previous_version_id: Mapped[Optional[_uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("question_bank.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    version_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("1"),
    )
    is_current: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
    )

    # -- Ownership and scope -------------------------------------------------
    # CASCADE: consistent with every other org-owned table in doc 03 §5, and only
    # reachable through the hard-delete offboarding path.
    org_id: Mapped[Optional[_uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
    )
    scope: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'global'"),
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)

    # -- Curriculum placement ------------------------------------------------
    # All four nullable per doc 03 §5, and all four RESTRICT: doc 03 §5 chapters says
    # "restrict deletion if outcomes, questions, or textbook assets still reference
    # the chapter", and the same posture applies up the curriculum chain.
    board_id: Mapped[Optional[_uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("boards.id", ondelete="RESTRICT"),
        nullable=True,
    )
    class_id: Mapped[Optional[_uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("classes.id", ondelete="RESTRICT"),
        nullable=True,
    )
    subject_id: Mapped[Optional[_uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subjects.id", ondelete="RESTRICT"),
        nullable=True,
    )
    chapter_id: Mapped[Optional[_uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chapters.id", ondelete="RESTRICT"),
        nullable=True,
    )

    # -- Classification ------------------------------------------------------
    qtype: Mapped[str] = mapped_column(Text, nullable=False)
    # numeric(4,1) exactly as doc 03 §5 specifies: marks are half-mark granular
    # ("1.5 marks") and must not be float — 0.1 has no exact binary representation,
    # and a paper's marks have to sum to the blueprint total exactly.
    marks: Mapped[_decimal.Decimal] = mapped_column(Numeric(4, 1), nullable=False)
    difficulty: Mapped[str] = mapped_column(Text, nullable=False)
    bloom: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # NOT NULL with no default: doc 03 §5's "Default values" list covers
    # version_number, is_current, scope, meta, status, timestamps and row_version and
    # deliberately does not mention `language`. Flagged as an open item — every INSERT
    # must supply it until doc 03 names a default.
    language: Mapped[str] = mapped_column(Text, nullable=False)

    # -- Content -------------------------------------------------------------
    stem: Mapped[str] = mapped_column(Text, nullable=False)
    # JSONB, shape varying by qtype: a list for `mcq` options, an object for `match`
    # pairs. Typed as Any rather than dict[...] for that reason. doc 03 §7 keeps the
    # canonical text in `stem` precisely so the searchable content is not trapped
    # inside JSON (§3 Normalization: "do not store mutable canonical content only
    # inside JSON blobs").
    options: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    answer: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    rubric: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    # doc 03 §5's "GIN on search document built from stem, answer, and rubric", as a
    # STORED generated column (doc 03 §8: "generated `tsvector` columns"). Added by
    # revision 0018.
    #
    # Three details are load-bearing:
    #
    # * **The two-argument `to_tsvector`.** A generated column's expression must be
    #   IMMUTABLE, and the one-argument form is only STABLE — it reads
    #   `default_text_search_config`, a session GUC. Passing the config explicitly as
    #   `'english'::regconfig` is what makes the expression immutable, and PostgreSQL
    #   rejects the column outright otherwise. It also pins the analyzer: two servers
    #   with different `default_text_search_config` values cannot disagree about what a
    #   row's search vector contains.
    # * **`::text` on the JSONB columns.** `jsonb_out` is immutable, so the cast is
    #   allowed here; `answer` and `rubric` hold prose worth searching, and their JSON
    #   punctuation is discarded by the tokenizer anyway.
    # * **`coalesce` on all three.** `answer` and `rubric` are nullable, and `NULL`
    #   anywhere in a `||` chain makes the whole concatenation NULL — without the
    #   coalesce, a question with no rubric would have an empty search vector and
    #   silently vanish from every full-text query.
    #
    # No weighting (`setweight`) and no `simple`-config fallback for non-English
    # content: doc 03 specifies neither, and `language` is a per-row column, so a
    # per-language analyzer would need either a dynamic regconfig — which cannot be
    # immutable — or one generated column per language. Reported as an open item; today
    # every row is analyzed as English, which for Hindi or Odia stems means the vector
    # degrades to unstemmed tokens rather than breaking.
    search_document: Mapped[Optional[str]] = mapped_column(
        TSVECTOR,
        Computed(_SEARCH_DOCUMENT_EXPRESSION, persisted=True),
        nullable=True,
    )

    # -- Taxonomy ------------------------------------------------------------
    # UUID[] rather than bridge tables, per doc 03 §3 "Many-to-many": "questions ↔
    # taxonomy via JSONB and arrays in the finalized architecture, with future
    # normalization possible if needed". No foreign key is possible on an array
    # element in PostgreSQL, so referential integrity here is the application's job.
    #
    # Flagged: `learning_outcome_ids` points at `learning_outcomes.id`, which exists.
    # `competency_ids` points at a `competencies` table that appears nowhere in
    # doc 03 — not in §2's entity list, not in §5, not in §8's model list. The column
    # ships because §5 requires it; the missing table is reported.
    learning_outcome_ids: Mapped[list[_uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)),
        nullable=False,
        server_default=text("'{}'::uuid[]"),
    )
    competency_ids: Mapped[list[_uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)),
        nullable=False,
        server_default=text("'{}'::uuid[]"),
    )
    meta: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )

    # -- Dedup and lifecycle -------------------------------------------------
    content_hash: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    # Default 'draft', not 'approved'. doc 03 §5 gives a conditional default —
    # "status approved for curated content or draft for authoring flows" — which is
    # two defaults, so one had to be chosen. 'draft' is the safe direction: a row
    # that reaches the table without an explicit status must not be publishable, and
    # a seed or import that wants curated content says so. Flagged.
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'draft'"),
    )
    # SET NULL, not CASCADE: a hard-deleted author must not take their questions with
    # them. This is why ADR-012 §2 added `users.status` — disabling an identity has to
    # be possible precisely because rows like this one reference it. doc 03 §5 does
    # not specify the action for this FK; inferred, and flagged.
    created_by: Mapped[Optional[_uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # -- AI provenance (revision 0022, P1-T4 report item 7) ------------------
    # Not in doc 03 §5. Added because §5 specifies `prompt_versions` and gives no
    # column anywhere that references it, so the schema could record that prompt v7
    # existed but not that any question came out of it — and `source` already admits
    # `ai_generated`, so every AI question was unattributable in exactly the
    # situation where attribution matters.
    #
    # Nullable because five of the six `source` values describe questions no LLM prompt
    # produced (including `template_generated`); NULL means "not attributed to an LLM
    # prompt", not "unknown". SET NULL
    # because this is a provenance pointer, not an ownership edge: CASCADE would
    # delete real questions when an old prompt row is cleaned up. Indexed because
    # "which questions came from prompt version X" is the whole point of the column,
    # and because SET NULL scans the referencing side on every prompt deletion.
    #
    # Deliberately not constrained against `source` — see revision 0022 for why the
    # obvious CHECK is wrong in both directions.
    prompt_version_id: Mapped[Optional[_uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("prompt_versions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # -- Relationships -------------------------------------------------------
    root_question: Mapped[Optional["QuestionBank"]] = relationship(
        "QuestionBank",
        remote_side=lambda: [QuestionBank.id],
        foreign_keys=lambda: [QuestionBank.root_question_id],
        back_populates="lineage_versions",
    )
    lineage_versions: Mapped[list["QuestionBank"]] = relationship(
        "QuestionBank",
        foreign_keys=lambda: [QuestionBank.root_question_id],
        back_populates="root_question",
        cascade="save-update, merge",
    )
    previous_version: Mapped[Optional["QuestionBank"]] = relationship(
        "QuestionBank",
        remote_side=lambda: [QuestionBank.id],
        foreign_keys=lambda: [QuestionBank.previous_version_id],
        back_populates="next_versions",
    )
    next_versions: Mapped[list["QuestionBank"]] = relationship(
        "QuestionBank",
        foreign_keys=lambda: [QuestionBank.previous_version_id],
        back_populates="previous_version",
        cascade="save-update, merge",
    )
    organization: Mapped[Optional["Organization"]] = relationship(
        "Organization",
        back_populates="questions",
    )
    board: Mapped[Optional["Board"]] = relationship("Board")
    school_class: Mapped[Optional["Class"]] = relationship("Class")
    subject: Mapped[Optional["Subject"]] = relationship("Subject")
    chapter: Mapped[Optional["Chapter"]] = relationship(
        "Chapter",
        back_populates="questions",
    )
    author: Mapped[Optional["User"]] = relationship(
        "User",
        back_populates="authored_questions",
    )
    # One-directional, like `board`/`school_class`/`subject` above, and for a stronger
    # reason than those: the reverse side would be "every question this prompt version
    # produced", which is an unbounded collection that no code path wants loaded. The
    # query it would serve is a filtered select on `prompt_version_id`, which the index
    # revision 0022 adds already covers.
    prompt_version: Mapped[Optional["PromptVersion"]] = relationship("PromptVersion")

    __table_args__ = (
        # -- Controlled vocabularies (ADR-011: CHECK on text, never a native ENUM) --
        check_in("source", QUESTION_SOURCES, name="source"),
        check_in("scope", QUESTION_SCOPES, name="scope"),
        check_in("qtype", QUESTION_TYPES, name="qtype"),
        check_in("difficulty", QUESTION_DIFFICULTIES, name="difficulty"),
        check_in("bloom", QUESTION_BLOOM_LEVELS, name="bloom"),
        check_in("status", QUESTION_STATUSES, name="status"),
        CheckConstraint("version_number > 0", name="version_number_positive"),
        row_version_positive(),
        # -- The two conditional scope rules, doc 03 §5 --------------------------
        # Written as implications (`NOT p OR q`) rather than CASE expressions because
        # that is what PostgreSQL stores and reads back cleanly. Both columns in each
        # are NOT NULL or explicitly NULL-tested, so there is no three-valued-logic
        # hole where a NULL quietly satisfies the constraint.
        CheckConstraint(
            "scope <> 'global' OR org_id IS NULL",
            name="scope_global_requires_null_org",
        ),
        CheckConstraint(
            "scope <> 'org' OR org_id IS NOT NULL",
            name="scope_org_requires_org",
        ),
        # -- Lineage uniqueness (BR-8) -------------------------------------------
        # NOT partial on deleted_at, and that is deliberate. ADR-004's partial-index
        # rule exists so a *human-visible name* can be reused after deletion. A
        # version number is not a name; it is append-only lineage identity. If
        # soft-deleting version 3 freed the number, a later fork could mint a second
        # version 3 and every previous_version_id chain through that lineage would
        # become ambiguous. History that can be rewritten is not history.
        Index(
            "uq_question_bank_root_question_id_version_number",
            "root_question_id",
            "version_number",
            unique=True,
        ),
        # doc 03 §5: "partial unique(root_question_id) where is_current = true", and
        # ADR-003's "partial unique index enforcing exactly one current version per
        # root (BR-8)". The predicate is `is_current` alone, with no deleted_at term:
        # adding one would let a tombstoned-but-still-current row sit alongside a new
        # current row, which is the exact state BR-8 forbids. Retiring a lineage means
        # setting is_current = false, not relying on the tombstone.
        Index(
            "uq_question_bank_root_question_id_current",
            "root_question_id",
            unique=True,
            postgresql_where=text("is_current"),
        ),
        # -- Retrieval -----------------------------------------------------------
        # doc 03 §5 asks for a ten-column composite "retrieval index" in exactly this
        # order. Named explicitly rather than by convention: the generated name would
        # be `ix_question_bank_org_id_scope_source_board_id_class_id_subject_id_
        # chapter_id_qtype_difficulty_status`, 100 characters, and PostgreSQL
        # truncates identifiers to 63 bytes *silently*. A truncated name still works
        # on CREATE and then makes the downgrade's DROP INDEX and alembic's
        # name-based comparison disagree with the model. Short explicit name, no
        # truncation, reversible.
        #
        # Worth knowing about the shape: a ten-column btree is only selective on its
        # leading columns, so in practice this serves `org_id, scope, source` lookups
        # and acts as a covering index for the rest. That is doc 03's call, not a
        # deviation, but it is the kind of index worth re-measuring against real
        # cardinalities once there is data (doc 03 §9 step 18 is where that lands).
        Index(
            "ix_question_bank_retrieval",
            "org_id",
            "scope",
            "source",
            "board_id",
            "class_id",
            "subject_id",
            "chapter_id",
            "qtype",
            "difficulty",
            "status",
        ),
        # doc 03 §5: "created_by, created_at" — one composite, not two singles.
        Index("ix_question_bank_created_by_created_at", "created_by", "created_at"),
        # -- Search artifacts, doc 03 §9 step 18 (revision 0018) -----------------
        # doc 03 §5 lists four, and all four are GIN. GIN rather than GiST for every
        # one of them because GIN is the right structure for "many keys per row, read
        # far more often than written" — which describes a tsvector, a UUID array and a
        # trigram set equally well. The Blueprint §8 agrees ("GIN for FTS, trigram, and
        # LO arrays").
        Index(
            "ix_question_bank_search_document",
            "search_document",
            postgresql_using="gin",
        ),
        # The two taxonomy arrays. These are what make `learning_outcome_ids @> ARRAY[...]`
        # an index scan instead of a sequential one — FR-4.19's coverage report filters
        # on exactly that, and doc 03 §3 chose arrays over bridge tables on the
        # assumption these indexes exist.
        Index(
            "ix_question_bank_learning_outcome_ids",
            "learning_outcome_ids",
            postgresql_using="gin",
        ),
        Index(
            "ix_question_bank_competency_ids",
            "competency_ids",
            postgresql_using="gin",
        ),
        # doc 03 §5: "trigram on stem". Requires the `pg_trgm` extension, which doc 03
        # §9 step 1 enables (revision 0001) — so this index is creatable at 0018 without
        # any further setup.
        #
        # This is not a second full-text index. `search_document` answers "which
        # questions are about photosynthesis"; the trigram index answers the
        # substring-and-typo questions a tsvector cannot: FR-3.10's incremental search
        # box (`ILIKE '%photosyn%'`, which no stemmer will match), and the near-duplicate
        # check behind AI-4 via `similarity()`. Different question, different index.
        #
        # `postgresql_ops` is mandatory: without `gin_trgm_ops` a GIN index on a text
        # column has no usable operator class and CREATE INDEX fails outright.
        Index(
            "ix_question_bank_stem_trgm",
            "stem",
            postgresql_using="gin",
            postgresql_ops={"stem": "gin_trgm_ops"},
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<QuestionBank id={self.id!r} root={self.root_question_id!r} "
            f"v{self.version_number!r} current={self.is_current!r} "
            f"status={self.status!r} qtype={self.qtype!r}>"
        )


__all__ = [
    "QUESTION_BLOOM_LEVELS",
    "QUESTION_DIFFICULTIES",
    "QUESTION_SCOPES",
    "QUESTION_SOURCES",
    "QUESTION_STATUSES",
    "QUESTION_TYPES",
    "QuestionBank",
]
