# ADR-015: Workspace-Root Split (backend / frontend / shared)

**Status:** Accepted
**Date:** 2026-08-14
**Deciders:** Human (project lead)
**Supersedes:** The `apps/*` + `packages/*` layout described in the first reconciliation pass (CHANGELOG.md Pass 1)

## Context

The project documentation carried two competing folder layouts:

1. **`apps/{api,workers,web}` + `packages/*`** — used in docs 02, 07, 08, 09, the Blueprint, and ADR-002/006 after the first reconciliation pass.
2. **`backend/` + `frontend/` + `shared/`** — proposed in `10_Project_Summary.md` §2 as a workspace-root restructure.

Both enforced the same security invariant (the frontend build cannot reach backend-only constants), but (1) achieved it via separate subdirectories under a shared `apps/` parent, while (2) achieved it via **physically separate workspace roots** with independent lockfiles and venvs.

The reference prototype at `D:\ai paper` was mid-rename between the two layouts, with ~101 uncommitted files in a half-finished transition. The IMPLEMENTATION_PLAN.md §1.2 documents this state.

## Decision

Adopt the workspace-root split: **three top-level, independent workspace roots.**

```
papergen/
├── backend/       ← Python workspace root (own pyproject.toml, own venv)
│   ├── src/       ← FastAPI API + Celery workers as sub-packages
│   └── packages/  ← config, db, domain, prompts, events, testing
├── frontend/      ← Node workspace root (own package.json, own lockfile)
├── shared/        ← Cross-cutting non-code-execution assets (contracts, docs)
├── infra/
└── docs/
```

Key properties:

- **`backend/`** is one Python workspace. The API (`backend/src/main.py`) and the Celery workers (`backend/src/workers/`) are sub-packages within it — they share models, repositories, and services by import, not by RPC. There is exactly one implementation of each business rule.
- **`frontend/`** is one Node workspace. It has its own `package.json`, its own lockfile, and its own `.env.local`. It has no `DATABASE_URL`, no `GEMINI_API_KEY`, no JWT signing key.
- **`shared/`** holds non-code-execution assets: the OpenAPI contract (generated from FastAPI), shared prompt text, and documentation. It is never `import`ed as live code at build time — types flow into the consuming workspace via CI generation.
- **`packages/`** (config, db, domain, prompts, events, testing) are **inside `backend/`**, not at the monorepo root. They are Python packages used only by the backend workspace.

## Rationale

The `apps/*` layout under a shared root means the frontend and backend share a parent directory, and in the reference prototype, also shared a lockfile (`pnpm-workspace.yaml`), a `node_modules` tree, and a `tsconfig` base. Doc 10 §2 audit finding documented the exact failure: "`packages/db` already uses `dotenv -e ../../.env --`, so pulling root secrets into any workspace is one copy-paste away." The fix is structural, not procedural.

Separate workspace roots make the secret-leakage boundary **structural** (a build tool cannot resolve across workspace roots) rather than **procedural** (a lint rule that someone disables).

## Consequences

**Positive:**
- A server-only constant (DB URL, API key, JWT secret) cannot become reachable from client code by any import path.
- Each workspace has its own dependency graph, so a Python CVE does not trigger a Node rebuild or vice versa.
- Workers and the API share one venv and one set of SQLAlchemy models — no drift between them.

**Negative:**
- Contract sync requires a CI step: FastAPI generates the OpenAPI spec → `shared/contracts/` → CI generates TypeScript types for `frontend/`.
- Two lockfiles to maintain. Two CI build stages.
- `shared/` must never become a dumping ground for code that "both sides need" — that path leads back to a shared workspace root.

## References

- [`CHANGELOG.md`](../CHANGELOG.md) — "Needs human decision" item #1, resolved in Pass 2
- [`10_Project_Summary.md`](../10_Project_Summary.md) §2 — original proposal
- [`07_Backend_Architecture.md`](../07_Backend_Architecture.md) §3 — updated folder tree
- [`Blueprint_AI_Question_Paper_Generator.md`](../Blueprint_AI_Question_Paper_Generator.md) §13 — updated folder tree
