"""grant DELETE on hard-delete bridge tables to app_user (P2-T2)

Revision ID: 0030
Revises: 0029
Create Date: 2026-08-30

Why this exists
---------------
Revision 0024 deliberately withholds DELETE from ``app_user`` across the board:
ADR-004 mandates soft deletion for everything, so ``DELETE`` is an out-of-band
maintenance operation that must run as the table owner, not the application role.

Revision 0005's docstring calls out the one exception to that rule: the two
many-to-many bridge tables, ``program_teachers`` and ``program_subjects``, cannot
use soft deletion. Both have a composite primary key over their two FK columns; a
soft-delete column on a composite PK means a deleted row permanently occupies its
unique position, blocking re-assignment. The docstring calls this out explicitly:

    "un-assigning a teacher from a program would permanently block re-assigning
    them. The rule applied throughout P1-T4 is therefore: soft delete applies where
    uniqueness can be made partial. It can everywhere else in this schema; it cannot
    here, so these two take hard deletes."

The gap: 0005 creates the tables BEFORE 0024's blanket ``GRANT SELECT, INSERT,
UPDATE``, so the two tables receive the blanket — but 0024 issues no ``DELETE``
anywhere and leaves no way for the bridge's assign_teachers / assign_subjects
repository methods to clear old rows before reinserting.

The fix: grant ``DELETE`` on exactly these two tables, and only these two, to
``app_user``. The blanket revocation in 0024 remains correct for all other tables.
No audit column is lost — both the program row and the teacher/subject row survive
a bridge hard-delete, and audit logging is on the program update, not the bridge.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0030"
down_revision: Union[str, Sequence[str], None] = "0029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_APP_ROLE = "app_user"

#: The two bridge tables whose assignment logic requires hard deletes (see 0005).
_BRIDGE_TABLES: tuple[str, ...] = ("program_teachers", "program_subjects")


def upgrade() -> None:
    """Grant DELETE on the two bridge tables to app_user.

    Idempotent: granting a privilege that already exists is a no-op in PostgreSQL.
    """
    for table in _BRIDGE_TABLES:
        op.execute(f"GRANT DELETE ON public.{table} TO {_APP_ROLE}")


def downgrade() -> None:
    """Revoke DELETE on the two bridge tables from app_user."""
    for table in _BRIDGE_TABLES:
        op.execute(f"REVOKE DELETE ON public.{table} FROM {_APP_ROLE}")
