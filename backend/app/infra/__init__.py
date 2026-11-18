"""Marks ``app.infra`` as a package — doc 07 §3's ``src/infra/``.

Doc 07 §3 places third-party and transport clients here ("``infra/`` — clients for
things outside the process"), separate from ``app/db`` (which owns the SQL engine,
session factory and RLS context) and from ``app/modules`` (business logic).
"""
