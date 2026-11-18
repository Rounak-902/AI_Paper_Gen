"""``prompt_versions`` (doc 03 §5, §9 step 16).

The AI prompt registry. The prompt *text* is not here — the Blueprint §8 is explicit
that it "lives in packages/prompts (git-versioned, PR-reviewed)" and that this table
"records which version is ACTIVE per (task, model) and the eval score that promoted it,
so a bad prompt is one UPDATE to roll back — no redeploy".

That single sentence explains every column. ``template_ref`` is a path or content hash
into that git-tracked directory rather than the prompt body, because a prompt reviewed
in a pull request and a prompt sitting in a production table are two different
artifacts and only one of them has an approval history. ``eval_score`` records the
golden-set numbers that justified the promotion. ``status`` is what a rollback flips.

The traceability gap, and how it was closed
-------------------------------------------
The Blueprint §8 finishes with: "Generated questions record their provenance for
traceability/rollback audits: ``question_bank.meta->>'prompt_version_id'`` references
``prompt_versions(id)``."

As written that reference could not exist. doc 03 §5 gave ``question_bank`` no
``prompt_version_id`` column, so the pointer lived inside a JSONB blob where PostgreSQL
cannot enforce it: no foreign key, no type check, no index unless one is added by hand,
and nothing stopping a typo or a stale id. "Which questions came from the prompt version
we just rolled back" was therefore a JSONB scan with no referential guarantee behind it.

P1-T4 shipped it that way and reported it as the highest-value item after the embeddings
contradiction, on the reasoning that adding a real column would be a schema change doc 03
does not specify. **That decision was reversed** — it is item 7 of the P1-T4 report,
confirmed — and revision 0022 adds ``question_bank.prompt_version_id`` as a nullable
foreign key to this table with ``ON DELETE SET NULL`` and its own index. The rollback
audit is now an indexed join.

Three details of that column are worth knowing from this side of the relationship:

- **``ON DELETE SET NULL``, not RESTRICT.** A deleted prompt version should not be able to
  pin thousands of questions in place, and losing the pointer degrades an audit rather than
  corrupting a question. In practice it should never fire: doc 03 §5's rule for this table
  is "retire by status, not delete", which is why it carries ``deleted_at`` and is still
  never expected to use it.
- **Nullable, and it will stay largely null.** Only AI-generated questions have a prompt
  provenance; anything with ``source = 'manual'`` or ``'uploaded'`` has none, and every row
  written before revision 0022 has none either. No backfill out of JSONB is attempted,
  because ``meta->>'prompt_version_id'`` was never enforced and so cannot be trusted to
  contain a valid id.
- **The relationship is one-directional.** ``QuestionBank.prompt_version`` exists;
  ``PromptVersion.questions`` deliberately does not, because the reverse side is an
  unbounded collection — every question a prompt version ever produced — and no code path
  wants it loaded. The rollback query belongs in a repository method with a ``WHERE`` and a
  ``LIMIT``, not on an attribute access.

The JSONB key is not removed and is not deprecated here, because whether ``meta`` should be
cleaned up is a data question for P1-T5's repository layer rather than a schema one.
"""

from __future__ import annotations

import uuid as _uuid
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import ForeignKey, Index, Integer, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
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
    from app.models.user import User

#: doc 03 §5: ``candidate`` / ``active`` / ``retired``, and doc 03 §7.3 lists the same
#: three. The Blueprint §8 DDL agrees and even carries the CHECK inline, so this is one
#: of the few vocabularies with three concurring sources.
#:
#: The lifecycle is one-way in practice — a retired prompt is superseded, not revived —
#: but no constraint says so, because a CHECK cannot see the previous value of a column.
#: doc 03 §5's "retire by status, not delete" is the operative rule, and it is why this
#: table has ``deleted_at`` and is still never expected to use it.
PROMPT_VERSION_STATUSES: tuple[str, ...] = ("candidate", "active", "retired")


class PromptVersion(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    SoftDeleteMixin,
    RowVersionMixin,
    Base,
):
    """One evaluated, promotable prompt revision for a ``(task, model)`` pair."""

    __tablename__ = "prompt_versions"

    # Open vocabulary, and deliberately so: the Blueprint §8 comment lists
    # `'generate_mcq', 'classify_difficulty', 'synthesize_rubric', ...` with an explicit
    # ellipsis, and doc 03 §5 asks for no constraint. A CHECK here would mean a
    # migration every time the pipeline grows a step, which is the opposite of what a
    # registry is for. Contrast `usage.metric`, where doc 03 *did* ask for one.
    task: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    # "Monotonic per task", per the Blueprint. No `version > 0` CHECK: doc 03 §5 asks
    # for none, and the same restraint was applied to `chapters.seq`. `question_bank`
    # does carry `version_number > 0` — because doc 03 specifies it there.
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    # Path or content hash into packages/prompts. Text rather than a structured pair of
    # columns because doc 03 §5 types it as text and the reference format is the prompt
    # package's business, not the schema's.
    template_ref: Mapped[str] = mapped_column(Text, nullable=False)
    # The model id this revision was tuned and evaluated against — a plain string, with
    # no FK, because models are a vendor's catalogue and not a table here. It is part of
    # the active-version key below: a prompt that scores well on one model is not
    # promoted on another for free.
    model: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    # SET NULL, the mutable-table posture: this row must survive its author leaving, and
    # unlike an approval it is not evidence of who authorised anything.
    author: Mapped[Optional[_uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Golden-set results: {correctness, coverage, dup_rate, schema_fail} per the
    # Blueprint. JSONB because the metric set changes as the eval harness grows, and
    # nothing queries individual metrics relationally — they are read whole, by a human
    # deciding whether to promote.
    eval_score: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'candidate'"),
        index=True,
    )
    # RESTRICT, matching the self-references on `question_bank`: a lineage whose middle
    # link can be deleted is not a lineage. Nullable — the first version of any task has
    # no predecessor.
    previous_prompt_version_id: Mapped[Optional[_uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("prompt_versions.id", ondelete="RESTRICT"),
        nullable=True,
    )

    author_user: Mapped[Optional["User"]] = relationship("User")
    previous_version: Mapped[Optional["PromptVersion"]] = relationship(
        "PromptVersion",
        remote_side=lambda: [PromptVersion.id],
        foreign_keys=lambda: [PromptVersion.previous_prompt_version_id],
        back_populates="next_versions",
    )
    next_versions: Mapped[list["PromptVersion"]] = relationship(
        "PromptVersion",
        foreign_keys=lambda: [PromptVersion.previous_prompt_version_id],
        back_populates="previous_version",
        cascade="save-update, merge",
    )

    __table_args__ = (
        check_in("status", PROMPT_VERSION_STATUSES, name="status"),
        # doc 03 §5: "unique(task, version)". Not partial on `deleted_at`, and that is
        # the same call made for `uq_question_bank_root_question_id_version_number`:
        # version numbers identify history, so allowing a tombstoned row's number to be
        # reissued would make "generate_mcq v4" ambiguous and every
        # `previous_prompt_version_id` chain through it unreadable.
        UniqueConstraint("task", "version", name="uq_prompt_versions_task_version"),
        # doc 03 §5: "partial unique(task, model) where status = active" — the Blueprint
        # ships the identical index as `idx_prompt_active` and calls it "exactly one
        # active version per (task, model)". This is the constraint that makes rollback
        # a single UPDATE: promoting a candidate requires demoting the incumbent in the
        # same transaction, and the database refuses to let both be active for even an
        # instant.
        #
        # The predicate is `status = 'active'` alone, with no `deleted_at IS NULL` term,
        # for the reason spelled out on `uq_question_bank_root_question_id_current`:
        # adding one would permit a soft-deleted-but-still-active row to sit beside a
        # newly promoted one, which is precisely the state "exactly one" forbids.
        Index(
            "uq_prompt_versions_task_model_active",
            "task",
            "model",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        row_version_positive(),
        # doc 03 §5 indexes: task, model, status (all three `index=True` above) and
        # created_at. `task` is redundant with the leading column of both uniqueness
        # rules and is created because doc 03 lists it — the same call as
        # `ix_paper_versions_paper_id`.
        Index("ix_prompt_versions_created_at", "created_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<PromptVersion id={self.id!r} task={self.task!r} v{self.version!r} "
            f"model={self.model!r} status={self.status!r}>"
        )


__all__ = ["PROMPT_VERSION_STATUSES", "PromptVersion"]
