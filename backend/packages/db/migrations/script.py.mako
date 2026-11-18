"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision: str = ${repr(up_revision)}
down_revision: Union[str, Sequence[str], None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    """Upgrade schema."""
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """Downgrade schema.

    doc 03 §8: revisions are forward-only in production and follow
    expand-migrate-contract. A downgrade still has to be written and has to work —
    it is how a bad deploy is backed out of staging, and an unwritten downgrade is
    discovered at the worst possible moment. If a step genuinely cannot be reversed,
    say so in a comment and raise, rather than leaving `pass`.
    """
    ${downgrades if downgrades else "pass"}
