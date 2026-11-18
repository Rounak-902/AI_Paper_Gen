"""Cross-cutting HTTP concerns: envelope, errors, guard chain, middleware (P1-T6).

Doc 07 §3 places these in ``src/common/``:

    src/common/
      deps.py          # get_db, get_current_user, get_org_context, require_roles
      middleware/      # request-id, timing, audit, response envelope
      errors.py        # exception handlers -> one error envelope with ehRef
      pagination.py    # shared page/cursor dependency

They live in ``app/common/`` here for the same reason P1-T5's session plumbing
lives in ``app/db/`` rather than ``src/infra/``: ``app/`` is where this codebase
actually keeps its packages, and ``src/main.py`` is a two-line re-export of
``app.main``. Moving the tree to ``src/`` is a rename, not a redesign, and doing it
in this task would touch every P1-T1..T5 import for no behavioural gain.
"""
