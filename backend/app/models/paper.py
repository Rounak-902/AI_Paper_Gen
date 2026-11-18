"""``generated_papers`` / ``paper_versions`` / ``approvals`` (doc 03 §5, §9 steps 10-11).

The rest of doc 03 §4's **Papers** group, after ``templates`` (§9 step 9). ADR-003 is
what makes the shape here unusual: a paper is a *projection* of ``question_bank``,
not a container of questions. There is no ``paper_questions`` bridge table anywhere in
doc 03, and that is not an omission — the selection lives inside
``paper_versions.content`` as a rendered snapshot, so a paper printed last term keeps
resolving to the text that was actually printed even after every question in it has
been forked to a new version.

Three tables, three different lifecycles
----------------------------------------
==================  ==============  ================================================
Table               Lifecycle       Columns that say so
==================  ==============  ================================================
generated_papers    mutable         ``updated_at`` NOT NULL, ``deleted_at``,
                                    ``row_version``
paper_versions      immutable       ``created_at`` only; no ``deleted_at``,
                                    no ``row_version``
approvals           append-only     ``created_at`` only; no ``deleted_at``,
                                    no ``row_version``, no ``updated_at``
==================  ==============  ================================================

That table is read off doc 03 §5's own column lists. Nothing was added to make the
three consistent with each other, because they are deliberately not consistent.

BR-6 (separation of duties) — now a database rule
-------------------------------------------------
BR-6 requires that the author of a paper is not its approver. The schema's first
contribution is to make that *checkable*: ``generated_papers.created_by`` and
``approvals.reviewer_id`` are separate columns on separate rows, and ``approvals`` carries
no ``updated_at`` or ``deleted_at``, so the trail is append-only by construction.

P1-T4 shipped without enforcing it and said so: a CHECK constraint may only reference
columns of its own row, so ``reviewer_id <> created_by`` is not expressible on
``approvals`` — that comparison needs a join. **Revision 0021 closes it** with the
``BEFORE INSERT`` trigger that was one of the two options named at the time (the other was
denormalizing ``created_by`` onto ``approvals``, rejected because it would put one fact in
two places and then need a trigger of its own to keep them agreeing).

Four things about that trigger belong here rather than only in the migration, because they
change what this module's rows can contain:

* **It fires on ``action IN ('approve', 'reject')`` only.** ``approvals`` is a workflow
  trail, not a decision table, and the author *is* the submitter — a blanket rule would
  have made it impossible for a paper to reach ``submitted`` at all. Authors may still
  ``submit`` and ``comment`` on their own papers.
* **A NULL ``reviewer_id`` bypasses it entirely**, because doc 03 §5 makes that column
  nullable and BR-6 cannot be evaluated against a decision that names nobody. Closing that
  needs a same-row CHECK requiring ``reviewer_id IS NOT NULL`` when the action is a
  decision; it is expressible and unconfirmed, so it is not there.
* **``UPDATE`` is not covered.** The trigger is ``BEFORE INSERT``, so rewriting a
  decision's ``reviewer_id`` afterwards still reaches the forbidden state. Whether
  ``approvals`` should be *genuinely* append-only — rejecting all UPDATEs and DELETEs, per
  CN-22 — is a broader question than BR-6.
* **It says nothing about entitlement.** Any other user can approve, including one with no
  membership in the paper's organization. That needs doc 03 §7.2's RBAC vocabulary (P1-T7)
  and an approval policy nobody has specified.

All three gaps are measured in ``tests/test_approval_separation_of_duties.py`` rather than
left to prose, and the service layer still owns the *good error message* — the trigger is
the backstop, not the user experience.

BR-8's "approved questions are immutable" was closed the same way in revision 0020 — see
``app/models/question.py``.

Judgment calls, all reported
----------------------------
* **``created_by`` is RESTRICT here, and SET NULL on ``question_bank`` and
  ``templates``.** Not an inconsistency: doc 03 §5 makes this column **required**,
  which removes SET NULL from the menu. The consequence is worth stating plainly —
  a user who has ever created a paper can never be hard-deleted, only disabled
  (``users.status``, ADR-012 §2).
* **``program_id`` / ``curriculum_version_id`` / ``template_id`` are SET NULL.**
  doc 03 §5 names no action for any of the three. They are provenance pointers, and
  ``blueprint`` is described by doc 03 as a *snapshot* — the point of a snapshot is
  that it survives its source. SET NULL also keeps the tenant-offboarding path
  working: ``programs`` is itself ``ON DELETE CASCADE`` from ``organizations``, so a
  RESTRICT on ``program_id`` would abort a hard org delete depending on the order
  PostgreSQL happens to fire the two referential-integrity triggers in.
* **``approvals.action`` values come from the Blueprint, not doc 03.** doc 03 §5 says
  only "action constrained to workflow states" and §7.3 omits the column entirely.
  See :data:`APPROVAL_ACTIONS`.
* **``paper_versions.updated_at`` is nullable with no default**, exactly as doc 03 §5
  specifies, on a table doc 03 calls immutable. Implemented literally and flagged: an
  immutable row has no use for the column at all, and no ``onupdate`` is wired to it
  because wiring one would be a claim that the row is editable.
"""

from __future__ import annotations

import datetime as _dt
import decimal as _decimal
import uuid as _uuid
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
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
    from app.models.curriculum import CurriculumVersion
    from app.models.organization import Organization
    from app.models.program import Program
    from app.models.template import Template
    from app.models.user import User

_SOFT_DELETE_PREDICATE = "deleted_at IS NULL"

#: doc 03 §5 and §7.3 agree on these five, in this order, so nothing is invented.
#: The lifecycle is draft → submitted → review → approved | rejected; BR-6 governs who
#: may move a paper out of ``review``, and the trail of who did lives in ``approvals``.
GENERATED_PAPER_STATUSES: tuple[str, ...] = (
    "draft",
    "submitted",
    "review",
    "approved",
    "rejected",
)

#: doc 03 §5 says "action constrained to workflow states" and never enumerates them;
#: §7.3 has no row for this column. These four come from the Blueprint §8 DDL comment
#: (``action text NOT NULL, -- submit/approve/reject/comment``), which is the only
#: place in the document set that spells them out — the same precedent as
#: :data:`app.models.organization.ORG_TYPES`.
#:
#: Note that they are *verbs*, not the paper states in
#: :data:`GENERATED_PAPER_STATUSES`, which is why doc 03's "workflow states" wording is
#: the looser of the two sources. ``comment`` matters: a reviewer leaving a note
#: without deciding is a real workflow event, and dropping it would force reviewers to
#: overload ``reject``. Flagged in the hand-off notes; adding a value is a one-line
#: CHECK migration (ADR-011).
APPROVAL_ACTIONS: tuple[str, ...] = ("submit", "approve", "reject", "comment")


class GeneratedPaper(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    SoftDeleteMixin,
    RowVersionMixin,
    Base,
):
    """A paper aggregate: its blueprint, its provenance, and its workflow state."""

    __tablename__ = "generated_papers"

    org_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # RESTRICT because doc 03 §5 makes the column required — see the module docstring.
    created_by: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    program_id: Mapped[Optional[_uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("programs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # The blueprint as requested: section structure, per-section qtype and marks
    # targets, difficulty mix, chapter weighting. JSONB per doc 03 §8, and a *snapshot*
    # per doc 03 §5 — it is not re-derived from the program or template later, which is
    # what makes a paper reproducible after either has been edited.
    blueprint: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    curriculum_version_id: Mapped[Optional[_uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("curriculum_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    template_id: Mapped[Optional[_uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("templates.id", ondelete="SET NULL"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    # numeric(5,1), one digit more than `question_bank.marks`: a paper totals many
    # questions, so 100.0 and 999.5 both have to fit where a single question needs at
    # most 999.9. Half-mark granular and exact, for the same reason marks are not float.
    #
    # Nullable, and no CHECK that it equals the sum of the selected questions' marks:
    # doc 03 §5 asks for neither, and the sum lives inside `paper_versions.content`
    # where a CHECK constraint cannot reach it. Blueprint-total reconciliation is a
    # service-layer invariant.
    total_marks: Mapped[Optional[_decimal.Decimal]] = mapped_column(
        Numeric(5, 1),
        nullable=True,
    )
    duration_min: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'draft'"),
        index=True,
    )

    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="papers",
    )
    author: Mapped["User"] = relationship("User", back_populates="authored_papers")
    program: Mapped[Optional["Program"]] = relationship(
        "Program",
        back_populates="generated_papers",
    )
    curriculum_version: Mapped[Optional["CurriculumVersion"]] = relationship(
        "CurriculumVersion",
        back_populates="generated_papers",
    )
    template: Mapped[Optional["Template"]] = relationship(
        "Template",
        back_populates="generated_papers",
    )
    versions: Mapped[list["PaperVersion"]] = relationship(
        "PaperVersion",
        back_populates="paper",
        cascade="save-update, merge",
    )
    approvals: Mapped[list["Approval"]] = relationship(
        "Approval",
        back_populates="paper",
        cascade="save-update, merge",
    )

    __table_args__ = (
        check_in("status", GENERATED_PAPER_STATUSES, name="status"),
        row_version_positive(),
        # doc 03 §5 indexes: org_id, status, created_at, program_id, and the composite
        # (created_by, created_at). The first, second and fourth are `index=True`
        # above; these two are the ones that need naming.
        Index("ix_generated_papers_created_at", "created_at"),
        # One composite, not two singles — "which papers did this teacher build, most
        # recent first" is the query, the same reading applied to
        # `question_bank(created_by, created_at)` in revision 0006.
        Index("ix_generated_papers_created_by_created_at", "created_by", "created_at"),
        # No unique constraint: doc 03 §5 lists none for this table, so two papers may
        # share a title. Deliberate — a teacher generating "Class 10 Maths — Unit Test 1"
        # for two batches is the normal case, not a mistake.
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<GeneratedPaper id={self.id!r} org_id={self.org_id!r} "
            f"title={self.title!r} status={self.status!r}>"
        )


class PaperVersion(UUIDPrimaryKeyMixin, Base):
    """An immutable, renderable snapshot of a paper.

    No ``deleted_at`` and no ``row_version``: doc 03 §5 lists neither, and both would
    be meaningless here. Optimistic locking guards concurrent *edits*, and there are
    none; a soft-delete flag on an immutable render is a tombstone on a receipt. The
    row goes away only when its paper is hard-deleted, which doc 03 §5 states as
    "generated_papers delete cascades versions" — the one cascade rule in this group
    that doc 03 spells out explicitly.
    """

    __tablename__ = "paper_versions"

    paper_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("generated_papers.id", ondelete="CASCADE"),
        nullable=False,
    )
    # The human label: "Set A", "Set B", "Revised". Unique per paper, which is what
    # makes multi-set generation (FR-5) safe — two "Set A"s for one paper would make
    # the printed artifact ambiguous.
    label: Mapped[str] = mapped_column(Text, nullable=False)
    # The rendered selection: sections, the questions in order, their marks, the
    # instruction block. This is the ADR-003 projection — question *ids* alone would
    # not survive a later fork of any question in the paper, so the content is
    # snapshotted rather than referenced.
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    answer_key: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    # Object-storage keys, not URLs: a signed URL expires, a key does not. Nullable
    # because a version exists from the moment it is composed, and rendering is an
    # asynchronous job that fills these in afterwards.
    pdf_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    docx_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # TimestampMixin is deliberately not used: it makes `updated_at` NOT NULL with a
    # default and adds `onupdate=now()`. doc 03 §5 declares `updated_at` **nullable**
    # here and gives a default for `created_at` only. Written out literally; see the
    # module docstring for why no `onupdate` is attached.
    created_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[Optional[_dt.datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    paper: Mapped["GeneratedPaper"] = relationship(
        "GeneratedPaper",
        back_populates="versions",
    )

    __table_args__ = (
        # A real UNIQUE constraint rather than the partial unique *index* used
        # everywhere else in this schema — and that difference is the point. ADR-004's
        # `WHERE deleted_at IS NULL` predicate exists to let a name be reused after a
        # soft delete; this table has no soft delete, so there is nothing to make the
        # index partial on and a plain constraint is the honest expression of the rule.
        UniqueConstraint("paper_id", "label", name="uq_paper_versions_paper_id_label"),
        # doc 03 §5 indexes: paper_id and created_at. The first is redundant with the
        # leading column of the unique constraint above, and is created anyway because
        # doc 03 lists it in its own right — the same call made for
        # `ix_question_bank_root_question_id` in revision 0006. One extra btree on a
        # UUID column, in exchange for lineage lookups that keep working unchanged if
        # the composite is ever altered.
        Index("ix_paper_versions_paper_id", "paper_id"),
        Index("ix_paper_versions_created_at", "created_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<PaperVersion id={self.id!r} paper_id={self.paper_id!r} "
            f"label={self.label!r}>"
        )


class Approval(UUIDPrimaryKeyMixin, Base):
    """One append-only entry in a paper's review trail (BR-6).

    ``reviewer_id`` is RESTRICT rather than SET NULL, which is the opposite of the
    choice made for every authorship column on a mutable table. The reason is that
    ``ON DELETE SET NULL`` is itself an UPDATE, and an append-only row may not be
    updated — "the trail outlives the person" and "the trail may not be rewritten"
    point in opposite directions here, and for a separation-of-duties record the
    second wins. Practically: you cannot hard-delete a user who has ever approved a
    paper, which is the correct answer for an audit trail and the reason
    ``users.status`` exists (ADR-012 §2).

    ``paper_id`` is CASCADE because doc 03 §5 says so outright ("paper delete cascades
    approvals in hard-delete scenarios"). Note the asymmetry that leaves: erasing a
    paper erases its approval history, while erasing a *user* cannot. That is doc 03's
    call, and it is coherent — the trail exists to answer "who approved this paper",
    a question that stops existing along with the paper.
    """

    __tablename__ = "approvals"

    paper_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("generated_papers.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Nullable per doc 03 §5: a system-generated transition (an automatic `submit` from
    # a scheduled job, say) has no human reviewer. Consequence worth carrying next to the
    # column: revision 0021's BR-6 trigger cannot evaluate a decision that names nobody, so
    # its WHEN clause skips NULL rows outright rather than relying on three-valued logic.
    reviewer_id: Mapped[Optional[_uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    paper: Mapped["GeneratedPaper"] = relationship(
        "GeneratedPaper",
        back_populates="approvals",
    )

    __table_args__ = (
        check_in("action", APPROVAL_ACTIONS, name="action"),
        # doc 03 §5 indexes, both composite and both time-ordered: "the trail for this
        # paper" and "everything this reviewer has decided".
        Index("ix_approvals_paper_id_created_at", "paper_id", "created_at"),
        Index("ix_approvals_reviewer_id_created_at", "reviewer_id", "created_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<Approval id={self.id!r} paper_id={self.paper_id!r} "
            f"action={self.action!r}>"
        )


__all__ = [
    "APPROVAL_ACTIONS",
    "GENERATED_PAPER_STATUSES",
    "Approval",
    "GeneratedPaper",
    "PaperVersion",
]
