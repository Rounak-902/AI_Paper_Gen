"""make subject snapshots version-aware and reject direct chapter self-lineage

Revision ID: 0028
Revises: 0027
Create Date: 2026-08-28

P2-T1 found one real conflict in the P1 curriculum schema.  Curriculum versioning
is defined at subject-syllabus granularity: each annual version owns subject rows
and their chapter lists.  The original live-row unique index on
``subjects(class_id, name)`` nevertheless prevented two versions under the same
class from both containing a subject called, for example, ``Mathematics``.

The narrow correction is to include ``curriculum_version_id`` for versioned rows
and retain a separate unique index for nullable legacy rows.  A single composite
index would not protect the latter because PostgreSQL treats NULL values as
distinct.  ``chapters(subject_id, seq)`` deliberately stays unchanged: every
snapshot has a distinct subject row, so that constraint correctly prevents two
chapter sequence numbers inside one subject snapshot.

The chapter self-reference already rejects a missing ancestor via its foreign key.
This revision also rejects the one invalid lineage shape expressible as a row-local
CHECK: a chapter deriving directly from itself.  Longer cycles require traversal
and are rejected by the curriculum service before writes.

Downgrade is data-dependent.  Recreating the old unique index will fail if more
than one version now legitimately contains the same class/name pair.  PostgreSQL
runs the revision transactionally, so such a failure restores the two new indexes
instead of leaving the schema half-downgraded.

Strict verification also exposed a pre-existing P1 naming-convention mismatch in
revision 0026.  That revision passed names such as ``ck_invitations_role`` into a
metadata convention which itself prepends ``ck_invitations_``, so PostgreSQL holds
``ck_invitations_ck_invitations_role`` while the model correctly describes
``ck_invitations_role``.  This revision repairs the three names with metadata-only
``ALTER TABLE ... RENAME CONSTRAINT`` operations.  The downgrade restores the
0027 names exactly.  Revision 0027's intentional membership identity-lookup index
is retained; its missing ORM declaration is repaired in ``Membership.__table_args__``.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0028"
down_revision: Union[str, Sequence[str], None] = "0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_LIVE = "deleted_at IS NULL"


def upgrade() -> None:
    # Pre-existing P1 drift repair. Renaming does not rebuild or revalidate the
    # constraints; it only brings the catalog names in line with model metadata.
    op.execute(
        "ALTER TABLE public.invitations "
        "RENAME CONSTRAINT ck_invitations_ck_invitations_role "
        "TO ck_invitations_role"
    )
    op.execute(
        "ALTER TABLE public.invitations "
        "RENAME CONSTRAINT ck_invitations_ck_invitations_status "
        "TO ck_invitations_status"
    )
    op.execute(
        "ALTER TABLE public.invitations "
        "RENAME CONSTRAINT ck_invitations_ck_invitations_row_version_positive "
        "TO ck_invitations_row_version_positive"
    )

    op.drop_index("uq_subjects_class_id_name", table_name="subjects")
    op.create_index(
        "uq_subjects_class_id_curriculum_version_id_name",
        "subjects",
        ["class_id", "curriculum_version_id", "name"],
        unique=True,
        postgresql_where=sa.text(
            f"{_LIVE} AND curriculum_version_id IS NOT NULL"
        ),
    )
    op.create_index(
        "uq_subjects_class_id_name_unversioned",
        "subjects",
        ["class_id", "name"],
        unique=True,
        postgresql_where=sa.text(f"{_LIVE} AND curriculum_version_id IS NULL"),
    )
    op.create_check_constraint(
        op.f("ck_chapters_derived_from_not_self"),
        "chapters",
        "derived_from_chapter_id IS NULL OR derived_from_chapter_id <> id",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_chapters_derived_from_not_self"), "chapters", type_="check"
    )
    op.drop_index(
        "uq_subjects_class_id_name_unversioned", table_name="subjects"
    )
    op.drop_index(
        "uq_subjects_class_id_curriculum_version_id_name",
        table_name="subjects",
    )
    op.create_index(
        "uq_subjects_class_id_name",
        "subjects",
        ["class_id", "name"],
        unique=True,
        postgresql_where=sa.text(_LIVE),
    )

    # Restore revision 0027's exact physical names.
    op.execute(
        "ALTER TABLE public.invitations "
        "RENAME CONSTRAINT ck_invitations_role "
        "TO ck_invitations_ck_invitations_role"
    )
    op.execute(
        "ALTER TABLE public.invitations "
        "RENAME CONSTRAINT ck_invitations_status "
        "TO ck_invitations_ck_invitations_status"
    )
    op.execute(
        "ALTER TABLE public.invitations "
        "RENAME CONSTRAINT ck_invitations_row_version_positive "
        "TO ck_invitations_ck_invitations_row_version_positive"
    )
