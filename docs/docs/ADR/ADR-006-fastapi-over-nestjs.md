# ADR-006: FastAPI (Python) over NestJS for the backend

- **Status:** Accepted (supersedes the NestJS assumption in the original docs 02/07/09)
- **Date:** 2026-08-13
- **Affects:** `02_System_Architecture.md`, `07_Backend_Architecture.md`, `09_Deployment.md`, `03_Database_Design.md`, Blueprint §12–13

## Context

Docs 02, 07, and 09 assumed a NestJS (Node/TypeScript) backend, and the partially-implemented codebase
is a NestJS shell. This decision changes that.

The workload is dominated by **concurrent I/O waits**, not CPU: a single paper generation fans out
retrieval queries per slot, holds AI provider calls open for seconds, streams SSE progress for the
duration of the job, and polls job state. Almost all of that wall-clock time is spent waiting on a
socket.

The second force is ecosystem. Answer verification executes worked solutions with **sympy**; ingestion
uses Python-native document and OCR tooling; the AI path uses the Gemini SDK. On a Node backend these
become either a second Python service — the two-language split that doubles CI, ops, and hiring — or
shell-outs across a process boundary on the platform's most correctness-critical path.

## Decision

**The backend is FastAPI (Python) on uvicorn**, and the stack that follows from it:

| Concern | Choice | Replaces |
|---|---|---|
| Web framework | **FastAPI** (Starlette + asyncio), native `async def` handlers | NestJS |
| Validation / contracts | **Pydantic v2**, `extra="forbid"`, OpenAPI 3.1 generated from the models | class-validator DTOs |
| Dependency injection | **`Depends()`** — declared in the signature, overridable in tests | Nest's DI container |
| ORM | **SQLAlchemy 2.x async** + asyncpg | Prisma |
| Migrations | **Alembic**, with hand-written SQL for RLS, partitions, and index DDL | `prisma migrate` |
| Task queue | **Celery** + Redis broker; chains and chords for multi-step jobs | BullMQ |
| Tests | **pytest** + Testcontainers | Jest |

Auth, RLS, tenancy-guard logic, module boundaries, and every security control are **conceptually
unchanged** — only the implementation language moves. The guard chain becomes a dependency chain
(`get_current_user → get_org_context → require_roles → scope`) with the same deny-by-default semantics
and the same `SET LOCAL app.org_id` discipline inside the request transaction.

`backend/` (the Python workspace containing both API and workers) is physically separate from `frontend/`
(Next.js, Node). Because the frontend and the backend are separate workspace roots ([ADR-015](ADR-015-workspace-root-split.md)),
that boundary is physical: no shared dependency tree, build graph, runtime, or log stream.

## Consequences

**Positive.** The concurrency model matches the workload — thousands of idle connections per process at
low memory cost, which is exactly what SSE and long AI calls need. sympy, the OCR toolchain, and the
Gemini SDK are first-class rather than cross-process. One backend language, not two. Pydantic gives one
definition that both validates and documents, so the OpenAPI schema cannot drift from the code.

**Negative / accepted.** The existing NestJS shell is discarded — small, since it is a health controller
and two empty modules. TypeScript types no longer flow end-to-end from the ORM to the browser; the
client's types are **generated from the OpenAPI document in CI**, and a drift fails the build. Python
type checking is opt-in, so `mypy --strict` in CI is mandatory rather than optional.

**The dominant failure mode** is a blocking call inside an `async def` handler, which stalls every
concurrent request on that worker process. Mitigations: all I/O is async; genuinely blocking work (PDF
rendering, sympy execution, sync-only libraries) runs in Celery or a thread-pool executor; and a
latency-regression check runs in CI.

### Why each piece is a safe production choice

- **FastAPI/Starlette/Pydantic** — widely deployed in production, actively maintained, large ecosystem.
  Fallback: the ASGI interface is standard, so the server is replaceable independently of the app.
- **SQLAlchemy** — the default Python ORM for over fifteen years; every failure mode is documented.
  Fallback: only the repository layer touches the session, so the ORM surface is small, and the schema
  is plain PostgreSQL regardless.
- **Alembic** — the standard companion to SQLAlchemy; revisions are ordinary reviewable Python.
- **Celery** — the mature Python task queue, with per-queue routing and Prometheus exporters for
  queue-depth autoscaling. **Known failure mode:** Redis is not a durable broker, so an unacked task can
  be lost on ungraceful broker failure. Mitigated by `acks_late=True`, idempotency keys on every task,
  and durable status on the entity row — state is recoverable from PostgreSQL, not from the queue.
  Fallback: task signatures are thin wrappers over service methods, so the runner is swappable.

## Alternatives considered

- **NestJS (the original assumption).** Genuinely good: first-class module boundaries, a mature
  guard/interceptor chain, end-to-end TypeScript. Rejected because Node's concurrency advantage over
  async Python is not decisive for this workload, while the Python ecosystem advantage on the
  verification and ingestion paths is — and because keeping NestJS would have forced either a
  two-language backend or a weaker answer-correctness gate, which is the platform's #1 product risk.
- **NestJS + a separate Python AI/OCR service.** Preserves both ecosystems, at the cost of a network
  hop on the hot path, two CI pipelines, two deploy targets, and two hiring profiles — explicitly the
  split the Blueprint warns against for a team of this size.
- **Prisma's Python client instead of SQLAlchemy.** Rejected: markedly less mature than the TypeScript
  client, and the raw-SQL escape hatches this schema most depends on (RLS policies, partitions, GIN and
  trigram index DDL) are exactly where it is weakest.
- **`arq` instead of Celery.** Lighter, natively async, and a genuinely nice fit for a FastAPI app.
  Rejected for the critical path on the maturity requirement — a much smaller production track record
  and a thinner operational toolchain. Revisit if Celery's sync-worker model becomes a measured problem.
- **Django + DRF.** Batteries included and very mature, but the ORM is sync-first and the request model
  fits this async-heavy workload less naturally.
