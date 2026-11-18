"""create question_bank (doc 03 §9 step 6)

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-17

doc 03 §9 step 6: "Create question_bank." One table, and the most important one in
the schema — ADR-003 makes questions the core domain entity and papers projections
of them, so every retrieval path, every provenance claim (BR-4) and every version
guarantee (BR-8) resolves here.

Every literal is frozen on purpose — see revision 0003's docstring.

The two lineage indexes are the BR-8 enforcement
------------------------------------------------
doc 03 §5 asks for both, and they do different jobs::

    uq_question_bank_root_question_id_version_number   UNIQUE (root, version_number)
    uq_question_bank_root_question_id_current          UNIQUE (root) WHERE is_current

The first makes version numbers immutable identity inside a lineage; the second is
ADR-003's "exactly one current version per root". Neither is partial on
``deleted_at``, and both omissions are deliberate:

* on the version-number index, freeing a number when a version is soft-deleted would
  let a later fork mint a second "version 3" and every ``previous_version_id`` chain
  through that lineage would become ambiguous;
* on the ``is_current`` index, adding ``AND deleted_at IS NULL`` would permit a
  tombstoned-but-still-current row to sit beside a live current row — exactly the
  state BR-8 forbids. Retiring a lineage means setting ``is_current = false``.

**Open item, reported not resolved.** doc 03 §5 declares ``root_question_id``
*nullable* and PostgreSQL unique indexes are NULLS DISTINCT, so both indexes are
inert for any row that has not been explicitly rooted. BR-8 is therefore enforced
only from the moment a lineage has a root. The column ships nullable exactly as
specified; whatever creates questions must set ``root_question_id = id`` on the first
version (P1-T5's repository base class), or doc 03 §9 step 19's "backfill current
pointers" migration populates it and then adds NOT NULL.
``backend/tests/test_question_lineage.py`` roots its lineages explicitly and asserts
the second index actually rejects a duplicate current row, so the enforced half is
measured rather than assumed.

Not created here — doc 03 §9 step 18
------------------------------------
Four of the eight indexes doc 03 §5 lists for this table are search artifacts: the
GIN index over a search document built from ``stem``/``answer``/``rubric``, the two
GIN indexes on the UUID arrays, and the ``pg_trgm`` index on ``stem``. doc 03 §8
requires those to be written as raw SQL inside a revision, and §9 step 18 is the step
that owns "indexes, constraints, RLS policies, partitions" — so they arrive in
revision 0018 together with the generated ``tsvector`` column they need. They are
absent from the model as well as from this file, because a model index the database
does not have makes ``alembic check`` report drift.

``question_embeddings`` (§9 step 7) is not created at all — see revision 0007.

Judgment calls, all reported in the P1-T4 hand-off notes
-------------------------------------------------------
* **``status`` defaults to ``'draft'``.** doc 03 §5 gives two defaults for one column
  — "approved for curated content **or** draft for authoring flows". ``'draft'`` is
  the safe direction: a row that arrives without an explicit status must not be
  publishable, and a seed that wants curated content can say so.
* **``scope`` defaults to ``'global'``** ("global for seeded shared content"). Safe
  because ``ck_question_bank_scope_global_requires_null_org`` makes a wrongly
  defaulted org-owned row fail loudly instead of leaking across tenants.
* **``language`` is NOT NULL with no default**, since doc 03 §5's defaults list names
  seven columns and not this one. Every INSERT must supply it.
* **Lineage FK actions are RESTRICT.** doc 03 §5 offers "restricted **or** set null";
  of the two, RESTRICT is the one that actually preserves history, because SET NULL
  would keep the row while erasing which lineage it belonged to. Consequence: a row
  that roots its own lineage cannot be hard-deleted, which is acceptable under
  ADR-004 but means a tenant-erasure script has to NULL the self-reference first.
* **``created_by`` is SET NULL** — doc 03 §5 names no action. A hard-deleted author
  must not take their questions with them; that is precisely why ADR-012 §2 added
  ``users.status``.
* **``scope = 'branch'`` is accepted but un-narrowable.** §7.3 lists the value, §5
  gives conditional rules for ``global`` and ``org`` only, and there is no
  ``branch_id`` column on this table. The value is kept because removing it would be
  the silent resolution.
* **``competency_ids`` references a table that does not exist.** doc 03 §5 requires
  the column; ``competencies`` appears in no §2 entity list, no §5 section and no §8
  model list. The column ships, the missing table is reported. (No FK is possible on
  an array element in PostgreSQL either way — doc 03 §3 accepts that.)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    op.create_table(
        "question_bank",
        # -- identity ---------------------------------------------------------
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        # -- lineage (ADR-003, BR-8) ------------------------------------------
        sa.Column("root_question_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("previous_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("version_number", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("is_current", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        # -- ownership and scope ----------------------------------------------
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("scope", sa.Text(), server_default=sa.text("'global'"), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        # -- curriculum placement ---------------------------------------------
        sa.Column("board_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("class_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("chapter_id", postgresql.UUID(as_uuid=True), nullable=True),
        # -- classification ---------------------------------------------------
        sa.Column("qtype", sa.Text(), nullable=False),
        # numeric(4,1) exactly as doc 03 §5 specifies. Marks are half-mark granular
        # and must not be float: a paper's marks have to sum to the blueprint total
        # exactly, and 0.1 has no exact binary representation.
        sa.Column("marks", sa.Numeric(precision=4, scale=1), nullable=False),
        sa.Column("difficulty", sa.Text(), nullable=False),
        sa.Column("bloom", sa.Text(), nullable=True),
        sa.Column("language", sa.Text(), nullable=False),
        # -- content ----------------------------------------------------------
        # Canonical searchable text stays in `stem`, not inside a JSON blob — doc 03
        # §3 Normalization is explicit about that, and step 18's tsvector depends on it.
        sa.Column("stem", sa.Text(), nullable=False),
        sa.Column("options", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("answer", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("rubric", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        # -- taxonomy ---------------------------------------------------------
        sa.Column(
            "learning_outcome_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            server_default=sa.text("'{}'::uuid[]"),
            nullable=False,
        ),
        sa.Column(
            "competency_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            server_default=sa.text("'{}'::uuid[]"),
            nullable=False,
        ),
        sa.Column(
            "meta",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        # -- dedup and lifecycle ----------------------------------------------
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'draft'"), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        # -- audit (ADR-004) --------------------------------------------------
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        # -- controlled vocabularies (ADR-011: CHECK on text, never a native ENUM) --
        # `bloom` is nullable and its CHECK still admits NULL, because a CHECK rejects
        # a row only when it evaluates to FALSE and `NULL IN (...)` is NULL.
        sa.CheckConstraint(
            "bloom IN ('remember', 'understand', 'apply', 'analyze', 'evaluate', 'create')",
            name=op.f("ck_question_bank_bloom"),
        ),
        sa.CheckConstraint(
            "difficulty IN ('easy', 'medium', 'hard')",
            name=op.f("ck_question_bank_difficulty"),
        ),
        sa.CheckConstraint(
            "qtype IN ('mcq', 'true_false', 'one_word', 'fill_blank', 'short_answer', "
            "'long_answer', 'assertion_reason', 'case_study', 'numerical', "
            "'diagram_based', 'match', 'very_short_answer', 'source_based')",
            name=op.f("ck_question_bank_qtype"),
        ),
        sa.CheckConstraint(
            "scope IN ('global', 'org', 'branch')",
            name=op.f("ck_question_bank_scope"),
        ),
        sa.CheckConstraint(
            "source IN ('institute_bank', 'textbook', 'intext', 'previous_year', 'ai_generated')",
            name=op.f("ck_question_bank_source"),
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'approved', 'rejected', 'archived')",
            name=op.f("ck_question_bank_status"),
        ),
        sa.CheckConstraint(
            "row_version > 0",
            name=op.f("ck_question_bank_row_version_positive"),
        ),
        sa.CheckConstraint(
            "version_number > 0",
            name=op.f("ck_question_bank_version_number_positive"),
        ),
        # -- the two conditional scope rules, doc 03 §5 -----------------------
        # Written as implications (NOT p OR q) rather than CASE, because that is what
        # PostgreSQL stores and reads back cleanly from pg_get_constraintdef.
        sa.CheckConstraint(
            "scope <> 'global' OR org_id IS NULL",
            name=op.f("ck_question_bank_scope_global_requires_null_org"),
        ),
        sa.CheckConstraint(
            "scope <> 'org' OR org_id IS NOT NULL",
            name=op.f("ck_question_bank_scope_org_requires_org"),
        ),
        # -- foreign keys -----------------------------------------------------
        # Curriculum placement: all four RESTRICT, per doc 03 §5 chapters ("restrict
        # deletion if outcomes, questions, or textbook assets still reference the
        # chapter"), with the same posture applied up the chain.
        sa.ForeignKeyConstraint(
            ["board_id"],
            ["boards.id"],
            name=op.f("fk_question_bank_board_id_boards"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["chapter_id"],
            ["chapters.id"],
            name=op.f("fk_question_bank_chapter_id_chapters"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["class_id"],
            ["classes.id"],
            name=op.f("fk_question_bank_class_id_classes"),
            ondelete="RESTRICT",
        ),
        # SET NULL: content outlives its author. See the module docstring.
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_question_bank_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_question_bank_org_id_organizations"),
            ondelete="CASCADE",
        ),
        # Self-references. RESTRICT on both — the history-preserving choice of the two
        # doc 03 §5 offers.
        sa.ForeignKeyConstraint(
            ["previous_version_id"],
            ["question_bank.id"],
            name=op.f("fk_question_bank_previous_version_id_question_bank"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["root_question_id"],
            ["question_bank.id"],
            name=op.f("fk_question_bank_root_question_id_question_bank"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["subject_id"],
            ["subjects.id"],
            name=op.f("fk_question_bank_subject_id_subjects"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_question_bank")),
    )

    # -- indexes, doc 03 §5 (search artifacts excluded — see the docstring) ----
    op.create_index(op.f("ix_question_bank_content_hash"), "question_bank", ["content_hash"])
    # doc 03 §5 lists "created_by, created_at" as one entry: one composite index, not
    # two singles — "which questions did this author write, most recent first".
    op.create_index(
        "ix_question_bank_created_by_created_at",
        "question_bank",
        ["created_by", "created_at"],
    )
    op.create_index(
        op.f("ix_question_bank_previous_version_id"),
        "question_bank",
        ["previous_version_id"],
    )
    # The ten-column composite from doc 03 §5, in doc 03's order. Named explicitly:
    # the convention name would be ~100 characters and PostgreSQL truncates
    # identifiers to 63 bytes *silently*, which still creates the index but then makes
    # this revision's DROP INDEX and alembic's name comparison disagree with the model.
    #
    # Shape note, not a deviation: a ten-column btree is selective only on its leading
    # columns, so in practice this serves `org_id, scope, source` lookups and covers
    # the rest. Worth re-measuring against real cardinalities at §9 step 18.
    op.create_index(
        "ix_question_bank_retrieval",
        "question_bank",
        [
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
        ],
    )
    # Redundant on paper with the leading column of the version-number unique index
    # below, and kept because doc 03 §5 lists `root_question_id` as an index in its own
    # right. The cost is one extra btree on a UUID column; the benefit is that lineage
    # lookups keep working unchanged if step 19 ever alters the composite.
    op.create_index(
        op.f("ix_question_bank_root_question_id"),
        "question_bank",
        ["root_question_id"],
    )
    # -- BR-8 lineage uniqueness ---------------------------------------------
    op.create_index(
        "uq_question_bank_root_question_id_version_number",
        "question_bank",
        ["root_question_id", "version_number"],
        unique=True,
    )
    op.create_index(
        "uq_question_bank_root_question_id_current",
        "question_bank",
        ["root_question_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )


def downgrade() -> None:
    """Downgrade schema.

    Fully reversible: one table created from nothing, so dropping it restores
    revision 0005's state exactly. The self-referencing foreign keys need no special
    handling — ``DROP TABLE`` takes constraints that reference the table itself with it.
    """

    op.drop_index("uq_question_bank_root_question_id_current", table_name="question_bank")
    op.drop_index("uq_question_bank_root_question_id_version_number", table_name="question_bank")
    op.drop_index(op.f("ix_question_bank_root_question_id"), table_name="question_bank")
    op.drop_index("ix_question_bank_retrieval", table_name="question_bank")
    op.drop_index(op.f("ix_question_bank_previous_version_id"), table_name="question_bank")
    op.drop_index("ix_question_bank_created_by_created_at", table_name="question_bank")
    op.drop_index(op.f("ix_question_bank_content_hash"), table_name="question_bank")
    op.drop_table("question_bank")
