"""make prompt promotion and rollback one externally-issued UPDATE

Revision ID: 0032
Revises: 0031
Create Date: 2026-09-10

Revision 0016 already created ``prompt_versions`` and the core database invariant:

    UNIQUE (task, model) WHERE status = 'active'

That non-deferrable partial unique index must remain the final correctness mechanism.
It also means a set-based status swap is not a safe implementation of "one UPDATE":
PostgreSQL does not promise UPDATE row order, so it may try to index the target active
row before removing the incumbent's active index entry.

This revision adds no table or column.  A narrowly-scoped BEFORE UPDATE trigger retires
the incumbent inside the caller's target-row UPDATE.  The whole operation is one atomic
statement from the caller's perspective, requires no application deployment for each
promotion/rollback, preserves both historical rows, and still lets the 0016 partial
unique index reject adversarial duplicate-active INSERTs and concurrency races.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0032"
down_revision: Union[str, Sequence[str], None] = "0031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FUNCTION_NAME = "fn_prompt_versions_retire_active_for_promotion"
_TRIGGER_NAME = "trg_prompt_versions_retire_active"

_CREATE_FUNCTION = f"""
CREATE OR REPLACE FUNCTION public.{_FUNCTION_NAME}()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $function$
BEGIN
    -- Serialize releases for one exact pair. Hash collisions only over-serialize;
    -- they cannot weaken the unique index or produce an invalid state.
    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtext(NEW.task),
        pg_catalog.hashtext(NEW.model)
    );

    UPDATE public.prompt_versions AS incumbent
    SET status = 'retired',
        updated_at = pg_catalog.now(),
        row_version = incumbent.row_version + 1
    WHERE incumbent.task = NEW.task
      AND incumbent.model = NEW.model
      AND incumbent.status = 'active'
      AND incumbent.id <> NEW.id;

    RETURN NEW;
END;
$function$
"""

_CREATE_TRIGGER = f"""
CREATE TRIGGER {_TRIGGER_NAME}
BEFORE UPDATE OF status ON public.prompt_versions
FOR EACH ROW
WHEN (NEW.status = 'active' AND OLD.status IS DISTINCT FROM 'active')
EXECUTE FUNCTION public.{_FUNCTION_NAME}()
"""


def upgrade() -> None:
    """Install the atomic promotion hook; revision 0016 retains schema ownership."""

    op.execute(_CREATE_FUNCTION)
    op.execute(_CREATE_TRIGGER)


def downgrade() -> None:
    """Remove only the promotion hook and leave the 0016 registry unchanged."""

    op.execute(
        f"DROP TRIGGER IF EXISTS {_TRIGGER_NAME} ON public.prompt_versions"
    )
    op.execute(f"DROP FUNCTION IF EXISTS public.{_FUNCTION_NAME}()")
