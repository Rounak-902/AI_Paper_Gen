"""Marks ``app.modules`` as a package — doc 07 §3's ``src/modules/``.

Doc 07 §3 lays out one package per bounded context (``modules/auth/``,
``modules/tenancy/``, ``modules/curriculum/`` …) with §5's rule that dependencies flow
top-down and cycles are forbidden. This file exists only so those packages are
importable; nothing belongs in it.

Note for parallel work: ``modules/tenancy/`` (P1-T8) is a sibling of ``modules/auth/``
(P1-T7) and per doc 07 §4.2 depends on it ("Dependencies: Auth module (password hashing
for new users)"). The arrow points tenancy → auth and must not be reversed.
"""
