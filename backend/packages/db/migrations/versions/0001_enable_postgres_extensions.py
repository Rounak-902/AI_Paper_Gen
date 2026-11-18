"""enable postgres extensions (doc 03 §9 step 1)

Revision ID: 0001
Revises:
Create Date: 2026-08-16

doc 03 §9 step 1: "Enable PostgreSQL extensions and base types (pgcrypto, citext,
pg_trgm). **Not `vector`** — see step 7."

Why each one is here, so nobody removes one as unused:

* ``pgcrypto`` — supplies ``gen_random_uuid()``, the server-side default behind every
  UUID primary key in this schema. (PostgreSQL 13+ also has that function in core;
  doc 03 §9 asks for the extension regardless, and P1-T7's password/token work is the
  other consumer.)
* ``citext`` — the type of ``users.email`` (doc 03 §5). Case-insensitive comparison
  belongs in the database rather than in every caller's ``lower()`` call.
* ``pg_trgm`` — not used by the tenancy tables at all. It is enabled here because
  doc 03 §9 makes it step 1, and because P2-T4's near-duplicate question detection and
  doc 03 §9 step 18's trigram indexes depend on it. Enabling an extension is cheap;
  discovering it is missing mid-migration is not.

Deliberately **not** enabled: ``vector``. ADR-007 defers pgvector, and doc 03 §9 step 7
gates it on a recorded paraphrase-recall gap in the eval suite.

These are all "trusted" extensions in PostgreSQL 13+, so the database owner can create
them without superuser rights — which matters on managed providers (Neon, RDS) where
no real superuser is available.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EXTENSIONS: tuple[str, ...] = ("pgcrypto", "citext", "pg_trgm")


def upgrade() -> None:
    """Upgrade schema."""
    for extension in EXTENSIONS:
        op.execute(f'CREATE EXTENSION IF NOT EXISTS "{extension}"')


def downgrade() -> None:
    """Downgrade schema.

    Dropped in reverse order, without CASCADE. That is deliberate: if some object
    still depends on an extension, PostgreSQL refuses the drop and the migration
    fails loudly, which is the correct outcome. ``DROP EXTENSION ... CASCADE`` would
    silently take dependent columns and indexes with it.

    Reversing this revision is only meaningful on a database PaperGen owns
    exclusively. On a shared database another schema may be using ``citext`` or
    ``pg_trgm``, in which case the drop either fails (good) or removes something
    another application needed (bad) — do not run this downgrade against a shared
    database.
    """
    for extension in reversed(EXTENSIONS):
        op.execute(f'DROP EXTENSION IF EXISTS "{extension}"')
