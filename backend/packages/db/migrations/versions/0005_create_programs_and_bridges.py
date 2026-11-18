"""create programs and bridges (doc 03 §9 step 5)

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-17

doc 03 §9 step 5: "Create programs, program_subjects, program_teachers." This is the
first tenant-owned table outside identity — ``programs.org_id`` puts it in scope for
the RLS policies P1-T5 adds.

Every literal is frozen on purpose — see revision 0003's docstring.

Deliberate shapes
-----------------
* **The two bridges carry no audit columns**, matching doc 03 §5, and here the
  omission is load-bearing rather than abbreviated (unlike the curriculum tables in
  0003, which were given them). Both bridges use a composite primary key over their
  two FK columns, and a primary key cannot be partial. Adding ``deleted_at`` to a
  composite-PK bridge would hit exactly the failure ADR-004 documents — "a
  soft-deleted row still occupies its unique key" — with no partial-index escape
  hatch, so un-assigning a teacher from a program would permanently block
  re-assigning them. The rule applied throughout P1-T4 is therefore: **soft delete
  applies where uniqueness can be made partial.** It can everywhere else in this
  schema; it cannot here, so these two take hard deletes. No history is lost — the
  program and the teacher both survive the row.

* **``programs.status`` vocabulary is invented.** doc 03 §5 says only "status
  constrained to active lifecycle values", default ``active``, and §7.3 does not list
  this column at all. Two values are used, ``active`` and ``archived``: ``deleted_at``
  already means "gone", so the only other state a batch needs is "it ran, it is over,
  keep it for its papers". Same situation as ``ORG_STATUSES`` in P1-T3, and reported
  the same way. Widening it later is a one-line CHECK migration (ADR-011).

Known gap, reported rather than silently fixed
----------------------------------------------
doc 03 §5 asks for ``unique(org_id, branch_id, name)``. ``branch_id`` is nullable and
PostgreSQL unique indexes are NULLS DISTINCT by default, so this does **not** prevent
two org-wide programs (``branch_id IS NULL``) sharing a name. doc 03 never addresses
the null case. The literal constraint ships; the stricter form is one line either way
(``NULLS NOT DISTINCT``, PG15+, or a second partial index ``WHERE branch_id IS NULL``)
but choosing between them turns on whether an org-wide and a branch-scoped program may
share a name, which is a product question. Adding the stricter form later is additive;
removing it after duplicate rows exist is not — so the loose form ships and the
question is asked. The same gap exists on ``textbooks`` (0008) and ``templates``
(0009) and wants one answer covering all three.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SOFT_DELETE_PREDICATE = "deleted_at IS NULL"


def upgrade() -> None:
    """Upgrade schema."""

    # ------------------------------------------------------------------
    # programs — institute-owned batch or program (doc 03 §5)
    # ------------------------------------------------------------------
    op.create_table(
        "programs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("exam_target", sa.Text(), nullable=True),
        sa.Column("academic_year", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
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
        sa.CheckConstraint(
            "status IN ('active', 'archived')",
            name=op.f("ck_programs_status"),
        ),
        sa.CheckConstraint("row_version > 0", name=op.f("ck_programs_row_version_positive")),
        # doc 03 §5: "deleting a branch should set branch_id to null" — losing a branch
        # promotes the program to org-wide rather than deleting it.
        sa.ForeignKeyConstraint(
            ["branch_id"],
            ["branches.id"],
            name=op.f("fk_programs_branch_id_branches"),
            ondelete="SET NULL",
        ),
        # doc 03 §5: "org deletion cascades programs". Reachable only through the
        # hard-delete offboarding path; ADR-004 forbids hard deletes from app code.
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_programs_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_programs")),
    )
    # doc 03 §5 indexes: org_id, branch_id, exam_target, status.
    op.create_index(op.f("ix_programs_branch_id"), "programs", ["branch_id"])
    op.create_index(op.f("ix_programs_exam_target"), "programs", ["exam_target"])
    op.create_index(op.f("ix_programs_org_id"), "programs", ["org_id"])
    op.create_index(op.f("ix_programs_status"), "programs", ["status"])
    # See the module docstring for the NULLS DISTINCT gap this leaves open.
    op.create_index(
        "uq_programs_org_id_branch_id_name",
        "programs",
        ["org_id", "branch_id", "name"],
        unique=True,
        postgresql_where=sa.text(_SOFT_DELETE_PREDICATE),
    )

    # ------------------------------------------------------------------
    # program_subjects — programs ↔ subjects (doc 03 §3, §5)
    # ------------------------------------------------------------------
    op.create_table(
        "program_subjects",
        sa.Column("program_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        # doc 03 §5: "program delete cascades".
        sa.ForeignKeyConstraint(
            ["program_id"],
            ["programs.id"],
            name=op.f("fk_program_subjects_program_id_programs"),
            ondelete="CASCADE",
        ),
        # RESTRICT: curriculum is shared reference data and a subject some batch is
        # still teaching must not vanish underneath it. doc 03 §5 states this table's
        # cascade only as "program delete cascades", so the subject side is inferred
        # from the RESTRICT posture the curriculum group takes throughout. Flagged.
        sa.ForeignKeyConstraint(
            ["subject_id"],
            ["subjects.id"],
            name=op.f("fk_program_subjects_subject_id_subjects"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("program_id", "subject_id", name=op.f("pk_program_subjects")),
    )
    # doc 03 §5 lists only `subject_id`. The other direction is already covered: the
    # composite PK's own index leads with program_id, so "which subjects does this
    # program teach" needs nothing extra.
    op.create_index(op.f("ix_program_subjects_subject_id"), "program_subjects", ["subject_id"])

    # ------------------------------------------------------------------
    # program_teachers — programs ↔ users (doc 03 §3, §5)
    #
    # Not an authorization record: authority comes from memberships.role, and this
    # narrows a teacher's assignment (doc 03 §7.2, "optionally narrowed to assigned
    # programs and subjects").
    # ------------------------------------------------------------------
    op.create_table(
        "program_teachers",
        sa.Column("program_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["program_id"],
            ["programs.id"],
            name=op.f("fk_program_teachers_program_id_programs"),
            ondelete="CASCADE",
        ),
        # CASCADE, mirroring memberships.user_id in 0002: an assignment to a
        # hard-deleted person means nothing, and unlike authorship it records no
        # history worth keeping.
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_program_teachers_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("program_id", "user_id", name=op.f("pk_program_teachers")),
    )
    op.create_index(op.f("ix_program_teachers_user_id"), "program_teachers", ["user_id"])


def downgrade() -> None:
    """Downgrade schema.

    Fully reversible; dropped in reverse dependency order.
    """

    op.drop_index(op.f("ix_program_teachers_user_id"), table_name="program_teachers")
    op.drop_table("program_teachers")

    op.drop_index(op.f("ix_program_subjects_subject_id"), table_name="program_subjects")
    op.drop_table("program_subjects")

    op.drop_index("uq_programs_org_id_branch_id_name", table_name="programs")
    op.drop_index(op.f("ix_programs_status"), table_name="programs")
    op.drop_index(op.f("ix_programs_org_id"), table_name="programs")
    op.drop_index(op.f("ix_programs_exam_target"), table_name="programs")
    op.drop_index(op.f("ix_programs_branch_id"), table_name="programs")
    op.drop_table("programs")
