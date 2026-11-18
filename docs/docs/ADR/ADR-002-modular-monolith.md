# ADR-002: Use a modular monolith instead of premature microservices

- **Status:** Accepted
- **Date:** 2026-08-13
- **Affects:** `02_System_Architecture.md`, `07_Backend_Architecture.md`, `09_Deployment.md`

## Context

The system decomposes naturally into seventeen modules — auth, tenancy, curriculum, programs,
questions, textbooks, retrieval, ai, papers, templates, approvals, exports, billing, usage, analytics,
notifications, audit — with genuinely different load profiles. OCR is CPU-heavy and spiky. Generation
is I/O-bound on AI calls. The question bank is read-heavy.

Different load profiles are the standard argument for microservices. But at MVP the team is 5–6
engineers, the deployment target is a handful of services, and no module has a *measured* scaling
conflict with another. Splitting now would buy independent scaling that nothing yet needs, in exchange
for network hops on every cross-module call, distributed transactions where a local one would do, a
service mesh, per-service CI, and inter-service auth.

## Decision

**One Python workspace (`backend/`) containing the FastAPI API and the Celery workers as sub-packages,
with boundaries enforced by module interfaces rather than network hops.**

The discipline that makes this a modular monolith rather than a big ball of mud:

- Each module owns its tables. Cross-module reads go through the owning module's service, never
  directly into another module's repository or tables.
- Modules communicate through injected services and, for side effects, through domain events emitted
  after commit.
- The dependency graph is acyclic and enforced by the `Depends()` wiring — a cycle is an import error.
- Business rules live in `packages/domain`, which is pure and performs no I/O.
- Contract tests pin the shapes of the `retrieval`, `ai`, and `assembler` interfaces, so a later
  extraction to a network service cannot silently change them.

Workers are a separate *process*, not a separate *codebase*: they import the same repositories and
services. There is exactly one implementation of retrieval, AI orchestration, and assembly.

## Consequences

**Positive.** One deployable, one CI pipeline, one debugging target, one transaction boundary. A
generation flow can be traced in a single stack trace rather than across four services. Refactoring a
boundary is a code change, not a coordinated multi-service release.

**Negative / accepted.** Every module scales together on the API tier — though the worker split already
separates the loads that actually differ. A memory leak in one module affects the whole process. The
boundaries are conventions backed by review and contract tests, not physics, so they require sustained
discipline.

**Extraction path.** Each module's public service interface is its future network interface. When a
module's scaling profile genuinely diverges — the most likely first candidate is OCR, if it needs GPU
hardware or a different runtime — it lifts out with its interface unchanged. The trigger is a measured
limit, not a headcount or a date.

## Alternatives considered

- **Microservices from day one.** Correct end state at 100k+ teachers, wrong start. It front-loads
  network partitions, distributed tracing complexity, service discovery, and per-service deploy
  coordination onto a team that has not yet proven the product's core thesis.
- **Serverless functions per endpoint.** Attractive for cost at low volume, but cold starts hurt the
  interactive path, long generation jobs collide with execution-time limits, and connection pooling
  against Postgres becomes a problem to solve rather than a default.
- **Two services split as API + worker only, with no internal module boundaries.** This is what the
  deployment topology looks like anyway — but without internal boundaries the code degenerates into
  cross-module table access, which is precisely what makes later extraction impossible.
