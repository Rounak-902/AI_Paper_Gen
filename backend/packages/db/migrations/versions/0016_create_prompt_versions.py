"""create prompt_versions (doc 03 §9 step 16)

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-17

doc 03 §9 step 16: "Create prompt_versions." The AI prompt registry and release tracker.

The prompt *text* is deliberately not in this table. The Blueprint §8 is explicit that it
"lives in packages/prompts (git-versioned, PR-reviewed)" and that this table "records
which version is ACTIVE per (task, model) and the eval score that promoted it, so a bad
prompt is one UPDATE to roll back — no redeploy". That sentence explains every column
below: ``template_ref`` is a path or content hash into that git-tracked directory rather
than the prompt body, because a prompt reviewed in a pull request and a prompt sitting in
a production table are two different artifacts and only one of them has an approval
history; ``eval_score`` records the golden-set numbers that justified the promotion; and
``status`` is what a rollback flips.

Every literal is frozen on purpose — see revision 0003's docstring.

Notes on shape
--------------
* **``task`` and ``model`` are open text with no CHECK**, and that asymmetry with
  ``usage.metric`` in revision 0013 is intentional. The Blueprint §8 comment lists
  ``'generate_mcq', 'classify_difficulty', 'synthesize_rubric', ...`` with an explicit
  ellipsis, and doc 03 §5 asks for no constraint on either column — a CHECK here would
  mean a migration every time the pipeline grows a step, which is the opposite of what a
  registry is for. ``model`` additionally names a vendor's catalogue entry, which no
  table here owns.
* **No ``CHECK (version > 0)``.** doc 03 §5 asks for none, and the same restraint was
  applied to ``chapters.seq`` in revision 0003. ``question_bank`` *does* carry
  ``version_number > 0`` — because doc 03 specifies it there and only there.
* **``author`` is SET NULL**, the mutable-table posture: this row must survive its author
  leaving, and unlike an approval it is not evidence of who authorised anything.
* **``previous_prompt_version_id`` is RESTRICT**, matching the two self-references on
  ``question_bank``: a lineage whose middle link can be deleted is not a lineage. The
  self-referencing FK is declared inline in ``CREATE TABLE``, which PostgreSQL accepts.
* **``uq_prompt_versions_task_version`` is not partial on ``deleted_at``**, the same call
  made for ``uq_question_bank_root_question_id_version_number`` in revision 0006: version
  numbers identify history, so letting a tombstoned row's number be reissued would make
  "generate_mcq v4" ambiguous and every ``previous_prompt_version_id`` chain through it
  unreadable.
* **``uq_prompt_versions_task_model_active`` is the constraint that makes rollback a
  single UPDATE.** doc 03 §5 asks for "partial unique(task, model) where status =
  active"; the Blueprint ships the identical index as ``idx_prompt_active`` and calls it
  "exactly one active version per (task, model)". Promoting a candidate therefore
  requires demoting the incumbent in the same transaction, and the database refuses to
  let both be active for even an instant. The predicate is ``status = 'active'`` alone,
  with no ``deleted_at IS NULL`` term — adding one would permit a
  soft-deleted-but-still-active row to sit beside a newly promoted one, which is
  precisely the state "exactly one" forbids.
* **``ix_prompt_versions_task`` duplicates the leading column of both uniqueness rules**
  and is created because doc 03 §5 lists it in its own right — same call as
  ``ix_paper_versions_paper_id``.
* **``deleted_at`` exists and is never expected to be used.** doc 03 §5 lists the column
  and also says "retire by status, not delete". Both are implemented: the column is
  there, and ``status = 'retired'`` is the operative mechanism.

Open item, reported not resolved — the top traceability gap in P1-T4
-------------------------------------------------------------------
The Blueprint §8 finishes with: "Generated questions record their provenance for
traceability/rollback audits: ``question_bank.meta->>'prompt_version_id'`` references
``prompt_versions(id)``."

**That reference cannot exist.** doc 03 §5 gives ``question_bank`` no
``prompt_version_id`` column, so the pointer lives inside a JSONB blob where PostgreSQL
cannot enforce it: no foreign key, no type check, no index unless one is added by hand,
and nothing stopping a typo or a stale id. "Which questions came from the prompt version
we just rolled back" — the exact question this table exists to answer — is therefore a
JSONB scan with no referential guarantee behind it.

Nothing is invented here to fix it: adding a real ``question_bank.prompt_version_id``
column would be a schema change doc 03 does not specify. It is reported as the
highest-value item after the embeddings contradiction, because the cost of closing it
later is a backfill out of JSONB across every AI-generated question in the bank.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0016"
down_revision: Union[str, Sequence[str], None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ACTIVE_VERSION_PREDICATE = "status = 'active'"


def upgrade() -> None:
    """Upgrade schema."""

    op.create_table(
        "prompt_versions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("task", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("template_ref", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("author", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("eval_score", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'candidate'"), nullable=False),
        sa.Column("previous_prompt_version_id", postgresql.UUID(as_uuid=True), nullable=True),
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
        # doc 03 §5 and §7.3 both list these three, and the Blueprint §8 DDL carries the
        # identical CHECK inline — one of the few vocabularies with three concurring
        # sources.
        sa.CheckConstraint(
            "status IN ('candidate', 'active', 'retired')",
            name=op.f("ck_prompt_versions_status"),
        ),
        sa.CheckConstraint(
            "row_version > 0",
            name=op.f("ck_prompt_versions_row_version_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["author"],
            ["users.id"],
            name=op.f("fk_prompt_versions_author_users"),
            ondelete="SET NULL",
        ),
        # Self-referencing, declared inline: PostgreSQL resolves it against the table
        # being created.
        sa.ForeignKeyConstraint(
            ["previous_prompt_version_id"],
            ["prompt_versions.id"],
            name=op.f("fk_prompt_versions_previous_prompt_version_id_prompt_versions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prompt_versions")),
        sa.UniqueConstraint("task", "version", name=op.f("uq_prompt_versions_task_version")),
    )
    # doc 03 §5 indexes: task, model, status, created_at.
    op.create_index(op.f("ix_prompt_versions_created_at"), "prompt_versions", ["created_at"])
    op.create_index(op.f("ix_prompt_versions_model"), "prompt_versions", ["model"])
    op.create_index(op.f("ix_prompt_versions_status"), "prompt_versions", ["status"])
    op.create_index(op.f("ix_prompt_versions_task"), "prompt_versions", ["task"])
    # "Exactly one active version per (task, model)" — see the module docstring.
    op.create_index(
        "uq_prompt_versions_task_model_active",
        "prompt_versions",
        ["task", "model"],
        unique=True,
        postgresql_where=sa.text(_ACTIVE_VERSION_PREDICATE),
    )


def downgrade() -> None:
    """Downgrade schema.

    Fully reversible: one table created from nothing, so dropping it restores revision
    0015's state exactly. The self-referencing foreign key goes with the table.
    """

    op.drop_index("uq_prompt_versions_task_model_active", table_name="prompt_versions")
    op.drop_index(op.f("ix_prompt_versions_task"), table_name="prompt_versions")
    op.drop_index(op.f("ix_prompt_versions_status"), table_name="prompt_versions")
    op.drop_index(op.f("ix_prompt_versions_model"), table_name="prompt_versions")
    op.drop_index(op.f("ix_prompt_versions_created_at"), table_name="prompt_versions")
    op.drop_table("prompt_versions")
