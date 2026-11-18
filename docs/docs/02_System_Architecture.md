# 02 System Architecture

## Overview

The platform is designed as a modular monolith with clear internal modules for curriculum, questions, retrieval, paper assembly, AI, approvals, billing, and analytics.

## High-Level Components

- Web frontend
- Backend application
- Worker processes for OCR, generation, and export
- PostgreSQL transactional database
- Redis for cache and workflow coordination
- Object storage for uploads and exports
- AI service layer for generation and validation

## Architectural Principles

- Keep the core platform as one deployable application.
- Use module boundaries instead of premature microservices.
- Keep the database as the system of record.
- Use queue-based async work for heavy operations.
- Ensure tenant isolation from the first release.

## Technology Stack

| Layer | Technology | Responsibility |
|-------|-----------|----------------|
| Client | Browser | Teacher / Coaching Admin / Super Admin UI |
| Frontend | **Next.js** (`frontend/`) | Rendering, routing, session handling, real-time job progress |
| Backend | **FastAPI** (Python, `backend/`) | REST API, auth, tenancy, RBAC, business modules, job enqueueing (ADR-006) |
| ORM / migrations | **SQLAlchemy 2.x async + Alembic** | Typed data access shared by API and workers; reviewable, reversible migrations |
| Queue | **Celery + Redis** | Task queues (OCR, generation, export), cache, rate limits, progress channels |
| Workers | **Celery processes** (`backend/src/workers/`) | OCR worker, Generation worker, Export worker — same Python workspace as the API, separate processes, separate log streams |
| Retrieval | Retrieval Engine module | Metadata filter + full-text + trigram candidate selection; ANN deferred (ADR-007) |
| Assembler | Assembler module | Constraint solver + feasibility gate + compromise report (ADR-009) — the core IP |
| AI | AI service layer | Grounded generation and validation behind the `LlmProvider` interface; **Gemini** is the only implementation (ADR-008) |
| Database | **PostgreSQL** (+ `pg_trgm`, `citext`; pgvector deferred) | System of record, RLS-backed tenant isolation |
| Storage | Object storage | Raw uploads, extracted pages, rendered exports |

**The frontend and the backend are physically separate workspace roots** ([ADR-015](ADR/ADR-015-workspace-root-split.md)). `frontend/` is a Node workspace (own `package.json`, own lockfile); `backend/` is a Python workspace (own `pyproject.toml`, own venv) containing both the FastAPI API and the Celery workers as sub-packages. `shared/` holds cross-cutting, non-code-execution assets (OpenAPI contracts, prompt text) — it is never imported as live code at build time; generated types flow into the consuming workspace via CI. The two build contexts share no dependency tree, no runtime, and no log stream. The backend is never embedded in the frontend — there is no mode in which a Next.js route handler is the API.

## Core Stack Diagram

The canonical path from a teacher's click to persisted data:

```
                            Teacher
                               |
                               v
                           Next.js                (frontend: UI, session, progress)
                               |
                               v
                           FastAPI                (backend: auth, tenancy, RBAC, modules)
                               |
                               v
                     Celery / Redis Queue         (async task dispatch + progress)
                               |
                               v
                         OCR Worker               (extract pages -> assets)
                               |
                               v
                      Retrieval Engine            (candidate selection: filter + FTS + trigram)
                               |
                               v
                              AI                  (grounded generation + validation, Gemini)
                               |
                               v
                          Assembler               (constraint solve -> valid paper or honest gap)
                               |
                               v
                          PostgreSQL              (system of record)
```

## Component Topology

The same components, showing how they actually interconnect rather than as a single chain:

```
   ┌──────────┐
   │ Browser  │  Teacher / Coaching Admin / Super Admin
   └────┬─────┘
        │ HTTPS
        v
   ┌─────────────────────┐
   │      Next.js        │  SSR/CSR, auth session, real-time progress (SSE)
   └────┬────────────────┘
        │ REST (org-scoped, bearer token)
        v
   ┌───────────────────────────────────────────────────────────────┐
   │                        FastAPI  API                           │
   │  ┌────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────┐  │
   │  │  Auth  │ │Curriculum│ │Questions │ │  Papers  │ │Billing│  │
   │  │Tenancy │ │ Programs │ │  Import  │ │ Assembly │ │ Usage │  │
   │  └────────┘ └──────────┘ └──────────┘ └──────────┘ └───────┘  │
   │  ┌────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────┐  │
   │  │Textbook│ │Retrieval │ │AI Service│ │Approvals │ │Audit  │  │
   │  │Ingest  │ │ Engine   │ │  Layer   │ │          │ │Analytx│  │
   │  └────────┘ └──────────┘ └──────────┘ └──────────┘ └───────┘  │
   └───┬──────────────┬──────────────┬───────────────────┬─────────┘
       │              │              │                   │
       │ enqueue      │ cache        │ SQL               │ put/get
       v              v              v                   v
   ┌────────┐   ┌──────────┐   ┌───────────┐      ┌──────────────┐
   │ Celery │   │  Redis   │   │PostgreSQL │      │Object Storage│
   │ Queues │   │  Cache   │   │ +pg_trgm  │      │ uploads/     │
   └───┬────┘   └──────────┘   └───────────┘      │ exports      │
       │                            ^  ^          └──────────────┘
       │ consume                    │  │                 ^
       v                            │  │                 │
   ┌──────────────────────────────┐ │  │                 │
   │          Workers             │ │  │                 │
   │  ┌───────────┐               │ │  │                 │
   │  │OCR Worker │───────────────┼─┘  │                 │
   │  ├───────────┤               │    │                 │
   │  │Generation │──> Retrieval ─┼────┘                 │
   │  │  Worker   │──> AI Service │                      │
   │  ├───────────┤               │                      │
   │  │Export     │───────────────┼──────────────────────┘
   │  │ Worker    │               │
   │  └───────────┘               │
   └──────────────┬───────────────┘
                  │ progress events
                  v
             Redis pub/sub ──> FastAPI ──> Next.js ──> Browser
                                                        │
   ┌──────────────┐                                     │
   │ Gemini API   │<── AI Service Layer (LlmProvider interface)
   └──────────────┘
```

## Project Folder Architecture

A monorepo keeps the frontend, backend, and shared contracts in one place with clear ownership.
The critical property is that **`frontend/` (Node/Next.js) and `backend/` (Python — FastAPI API +
Celery workers) are physically separate workspace roots** ([ADR-015](ADR/ADR-015-workspace-root-split.md))
with separate lockfiles, separate build graphs, separate runtimes, and separate log streams. They are
colocated in one repository so a single PR can change an endpoint and its caller atomically — not so
they can share a process.

```
papergen/
├── backend/                         # Python workspace root (own pyproject.toml, own venv)
│   ├── src/
│   │   ├── main.py                  # create_app(): routers, middleware, handlers, lifespan
│   │   ├── bootstrap/               # security headers/CORS, observability, lifespan
│   │   ├── common/
│   │   │   ├── deps.py              # get_current_user, get_org_context, require_roles, get_db
│   │   │   ├── middleware/          # response envelope, audit, request-id, timing
│   │   │   ├── errors.py           # exception handlers -> error envelope + ehRef
│   │   │   └── mappers/            # ORM entity -> response model (field allowlist)
│   │   ├── modules/
│   │   │   ├── auth/                # login, tokens, sessions
│   │   │   ├── tenancy/             # org context, memberships
│   │   │   ├── curriculum/          # boards, classes, subjects, chapters, outcomes
│   │   │   ├── programs/            # programs, program_subjects, program_teachers
│   │   │   ├── questions/           # CRUD, versioning, import, dedup, approval
│   │   │   ├── textbooks/           # upload, ingestion orchestration
│   │   │   ├── retrieval/           # Retrieval Engine — candidate selection pipeline
│   │   │   ├── ai/                  # prompt mgmt, Gemini adapter, validation
│   │   │   ├── papers/              # blueprint, assembler orchestration, versions
│   │   │   ├── templates/           # layout + branding
│   │   │   ├── approvals/           # review workflow
│   │   │   ├── exports/             # PDF/DOCX render orchestration
│   │   │   ├── billing/             # subscriptions, plan enforcement
│   │   │   ├── usage/               # metering
│   │   │   ├── analytics/           # rollups, dashboards
│   │   │   ├── notifications/       # fan-out to channels
│   │   │   └── audit/               # append-only audit log
│   │   ├── workers/                 # Celery consumers. ZERO HTTP surface.
│   │   │   ├── celery_app.py        # app, queues, routing, beat schedule
│   │   │   └── jobs/{ocr,generation,export,embedding,notification,cleanup,analytics}/
│   │   └── infra/                   # db session, redis, storage, Celery producer
│   ├── packages/
│   │   ├── config/                  # Pydantic Settings — SERVER ONLY, raises on invalid boot config
│   │   ├── db/                      # SQLAlchemy models, Alembic migrations, RLS policies, seeds
│   │   ├── domain/                  # pure rules: BR-1…BR-11, marks math, the solver. ZERO I/O.
│   │   ├── prompts/                 # versioned prompt templates — never leaves the backend
│   │   └── events/                  # domain event names + payload models
│   ├── pyproject.toml
│   └── .env                         # server-only secrets; never shipped to the browser
│
├── frontend/                        # Node workspace root (own package.json, own lockfile)
│   ├── src/
│   │   ├── app/                     # routes; one folder per screen (S-1 … S-14)
│   │   ├── components/              # UI components
│   │   ├── features/                # feature-scoped hooks + data access
│   │   ├── lib/                     # api client, auth session, SSE client
│   │   └── styles/
│   └── .env.local                   # browser-safe NEXT_PUBLIC_* vars only
│
├── shared/                          # Cross-cutting non-code-execution assets
│   ├── contracts/                   # OpenAPI spec, Pydantic/Zod request/response schemas
│   └── docs/                        # Shared documentation
│
├── infra/                           # IaC, docker-compose, deployment manifests
└── docs/                            # this documentation set
```

**Rules**

- The client lives only in `frontend/`. It talks to the backend over HTTP only. It cannot import backend
  code — the two are different languages in different workspace roots, which makes the boundary physical.
- Workers reuse services and repositories from `backend/src/`, never a second copy — one implementation
  of retrieval, AI orchestration, and assembly.
- `backend/packages/db` is the single source of the SQLAlchemy models and Alembic revisions; both the
  API and the workers depend on it.
- `backend/packages/domain` is pure and I/O-free. This is what makes the assembler's solver testable
  with property-based tests over randomized pools, in milliseconds, with no database.
- Cross-module imports go through a module's public surface (its service), never directly into another
  module's repository.
- `backend/packages/config` is server-only. No backend secret has a path into the browser bundle,
  because `frontend/` is a separate workspace root that cannot resolve backend packages.

**Process and log separation.** `api`, each worker, and `web` run as separate services with independent
stdout streams (`docker compose logs -f api` reads the backend alone). See `09_Deployment.md` §6.5 and
`07_Backend_Architecture.md` §19.4.


## Module Boundaries

Each FastAPI module owns a slice of the domain: its tables, its rules, and its public API. Code belongs to the module that owns the data it mutates. Modules communicate through services injected with `Depends()` and through domain events — never by reaching into another module's tables.

The **Owns Tables** column maps 1:1 to the table groups in `03_Database_Design.md`.

### Auth & Tenancy

- **Responsibilities:** Authentication, token/session lifecycle, organization context resolution, membership and role management, invitations.
- **Owns tables:** `organizations`, `branches`, `users`, `memberships`
- **Public API:** `POST /auth/login`, `POST /auth/refresh`, `POST /auth/logout`, `POST /auth/password-reset`, `GET /auth/me`, `POST /orgs`, `GET /orgs`, `POST /orgs/:id/invitations`, `PATCH /memberships/:id` (role change)
- **Consumes:** — (foundation module)
- **Emits:** `user.logged_in`, `membership.role_changed`, `invitation.sent`

### Curriculum

- **Responsibilities:** Board/class/subject/chapter/outcome hierarchy, curriculum versioning, alignment lookups used by retrieval and generation.
- **Owns tables:** `boards`, `classes`, `subjects`, `chapters`, `learning_outcomes`, `curriculum_versions`
- **Public API:** `GET /boards`, `GET /classes`, `GET /subjects`, `GET /chapters`, `GET /outcomes`, `GET /curriculum-versions`, `POST/PATCH` (Super Admin for global; Coaching Admin for org overrides)
- **Consumes:** Auth & Tenancy (scope)
- **Emits:** `curriculum.version_published`

### Programs

- **Responsibilities:** Program definitions, subject mapping, teacher assignments (drives teacher scope).
- **Owns tables:** `programs`, `program_subjects`, `program_teachers`
- **Public API:** `POST /programs`, `GET /programs`, `PATCH /programs/:id`, `POST /programs/:id/teachers`, `POST /programs/:id/subjects`
- **Consumes:** Auth & Tenancy, Curriculum
- **Emits:** `program.teacher_assigned`

### Questions

- **Responsibilities:** Question CRUD, versioning/lineage, search, import, de-duplication, approval-status transitions, embedding triggers.
- **Owns tables:** `question_bank`, `question_embeddings`
- **Public API:** `POST /questions`, `GET /questions`, `GET /questions/:id`, `PATCH /questions/:id`, `DELETE /questions/:id`, `POST /questions/import`, `POST /questions/:id/approve`, `POST /questions/:id/reject`, `GET /questions/:id/versions`
- **Consumes:** Curriculum, Textbooks (extracted assets), AI (embedding), Auth & Tenancy
- **Emits:** `question.created`, `question.approved`, `question.versioned`, `embedding.requested`

### Textbook Ingestion

- **Responsibilities:** Upload handling, ingestion orchestration, extraction into typed assets, chapter mapping, reprocessing.
- **Owns tables:** `textbooks`, `textbook_assets`
- **Public API:** `POST /textbooks` (request upload), `GET /textbooks`, `GET /textbooks/:id` (status), `POST /textbooks/:id/reprocess`, `GET /textbooks/:id/assets`
- **Consumes:** Curriculum (chapter mapping), Storage, Queue
- **Emits:** `textbook.uploaded`, `textbook.parsed`, `textbook.embedded`, `textbook.failed`

### Retrieval Engine

- **Responsibilities:** Candidate selection for generation — metadata filtering, full-text + trigram ranking, hybrid ranking, reranking, dedup, coverage optimization. Vector search is a deferred stage behind the same interface (ADR-007).
- **Owns tables:** — (read-only over `question_bank`, `question_embeddings`, `textbook_assets`)
- **Public API:** internal service only (`RetrievalService.selectCandidates(slot)`); optionally `POST /questions/search` for the Question Bank screen
- **Consumes:** Questions, Textbooks, Curriculum
- **Emits:** — (synchronous)

### AI Service

- **Responsibilities:** Prompt management and versioning, provider-agnostic LLM calls, response parsing, validation, similarity checks, difficulty/Bloom classification, distractor generation, retries.
- **Owns tables:** `prompt_versions`
- **Public API:** internal service only (`AiService.generate(...)`, `AiService.validate(...)`, `AiService.embed(...)`); `POST /prompt-versions`, `POST /prompt-versions/:id/promote`, `POST /prompt-versions/:id/retire` (Super Admin)
- **Consumes:** Retrieval (grounding), Curriculum
- **Emits:** `prompt.version_promoted`, `prompt.version_retired`

### Papers / Assembly

- **Responsibilities:** Blueprint capture and snapshotting; the **feasibility gate** and constraint-solving **assembler** (ADR-009); orchestration of the Retrieval Engine + AI to fill slots; paper versioning; coverage/marks reconciliation and the compromise report.
- **Owns tables:** `generated_papers`, `paper_versions`
- **Public API:** `POST /papers/generate`, `GET /papers`, `GET /papers/:id`, `PATCH /papers/:id`, `POST /papers/:id/versions`, `POST /papers/:id/submit`
- **Consumes:** Retrieval, AI, Questions, Curriculum, Billing (quota), Usage
- **Emits:** `paper.generation_requested`, `paper.generated`, `paper.submitted`

### Templates

- **Responsibilities:** Reusable layout/branding definitions, template lifecycle (`active`/`draft`/`archived`).
- **Owns tables:** `templates`
- **Public API:** `POST /templates`, `GET /templates`, `PATCH /templates/:id`, `POST /templates/:id/archive`
- **Consumes:** Auth & Tenancy
- **Emits:** `template.saved`

### Approvals

- **Responsibilities:** Review workflow, separation-of-duties enforcement, approval/rejection with comments.
- **Owns tables:** `approvals`
- **Public API:** `POST /papers/:id/approve`, `POST /papers/:id/reject`, `GET /papers/:id/approvals`
- **Consumes:** Papers, Auth & Tenancy
- **Emits:** `paper.approved`, `paper.rejected`

### Exports

- **Responsibilities:** Orchestrate PDF/DOCX rendering, store artifacts, issue secure download links.
- **Owns tables:** — (renders `paper_versions`; artifacts live in object storage)
- **Public API:** `POST /papers/:id/export`, `GET /exports/:id` (status + link)
- **Consumes:** Papers, Templates, Storage, Queue, Usage
- **Emits:** `export.requested`, `export.ready`, `export.failed`

### Billing & Usage

- **Responsibilities:** Subscription plans and seats, plan-limit enforcement, usage metering, quota checks.
- **Owns tables:** `subscriptions`, `usage`
- **Public API:** `GET /subscriptions`, `POST /subscriptions` (Super Admin), `PATCH /subscriptions/:id`, `GET /usage`
- **Consumes:** Auth & Tenancy; queried by Papers/Exports/Textbooks before metered actions
- **Emits:** `usage.recorded`, `plan.limit_reached`

### Analytics

- **Responsibilities:** Daily rollups, org and platform dashboards; rebuildable from source.
- **Owns tables:** `analytics_daily`
- **Public API:** `GET /analytics/org`, `GET /analytics/platform` (Super Admin)
- **Consumes:** reads across domains via rollup jobs
- **Emits:** —

### Notifications

- **Responsibilities:** Subscribe to domain events, fan out to channels (in-app, email; SMS/push later), manage read state.
- **Owns tables:** `notifications` (delivery + read state), defined in `03_Database_Design.md` §5
- **Public API:** `GET /notifications`, `POST /notifications/:id/read`
- **Consumes:** all emitting modules (via events)
- **Emits:** —

### Audit

- **Responsibilities:** Append-only record of every mutation and access-control change.
- **Owns tables:** `audit_logs`
- **Public API:** `GET /audit` (Coaching Admin scoped; Super Admin cross-tenant)
- **Consumes:** written by an interceptor on every mutating handler
- **Emits:** —

## Database Access Pattern

There is exactly one path to the database. This keeps tenant isolation, auditing, and metering enforceable in one place.

```
              ┌───────────┐
   Frontend   │  Next.js  │   ✗  never touches the database directly
   (browser)  └─────┬─────┘
                    │ REST only (org-scoped token)
                    v
              ┌───────────┐
   Backend    │  FastAPI  │   ✓  the ONLY writer of canonical data
              └─────┬─────┘       (guards → service → repository → RLS)
                    │ shared modules / packages
                    v
              ┌───────────┐
   Workers    │ OCR / Gen │   ✓  read + write via the SAME repositories
              │ / Export  │       (no private SQL, no bypass of RLS)
              └─────┬─────┘
                    │
                    v
              ┌───────────┐
   AI layer   │AI Service │   ✗  never opens a DB connection of its own;
              └───────────┘       receives context in, returns results out
```

| Actor | DB access | Notes |
|-------|-----------|-------|
| Frontend (Next.js) | **Never** | All data via the REST API; no connection string ever reaches the client. |
| Backend (FastAPI) | **Yes** | Sole owner of write paths; the dependency chain + RLS enforce tenancy on every query. |
| Workers | **Yes** | Through the same repository layer as the API — never hand-rolled SQL that skips scoping. Workers set `app.org_id` from the task payload with the same `SET LOCAL` discipline. |
| AI layer | **Only through backend** | Stateless: given grounded context, returns generated/validated content; persistence is the caller's job. |
| External services | **Never** | Integrate via the API or events, never direct DB access. |

**Why:** a single access path means org-scoping, RLS, auditing (BR-11), and metering (BR-10) cannot be accidentally bypassed by a second code path.

## API Design Philosophy

The API is REST over HTTPS, resource-oriented, versioned, and uniform. Every client and worker can rely on the same envelope, error shape, and query conventions.

### Resource conventions

Plural nouns, standard verbs, nesting only one level deep.

```
GET    /papers                # list (paginated, filterable, sortable)
POST   /papers/generate       # action verb where it isn't plain CRUD
GET    /papers/:id            # single resource
PATCH  /papers/:id            # partial update
DELETE /papers/:id            # soft-delete where lifecycle applies
POST   /papers/:id/submit     # state transition as a sub-resource action
GET    /papers/:id/versions   # nested collection
```

### Versioning

- Version in the path: `/api/v1/...`. Breaking changes ship as `/api/v2` while `v1` is supported through a deprecation window.
- Additive changes (new optional fields, new endpoints) stay within the current version.

### Success envelope

Every successful response uses one shape:

```json
{
  "success": true,
  "data": { },
  "message": "Paper generated",
  "meta": { "requestId": "req_...", "pagination": { } }
}
```

### Error envelope

Every error — validation, auth, business rule — uses one shape and maps to the error catalogue (EH-1 … EH-18):

```json
{
  "success": false,
  "error": {
    "code": "BLUEPRINT_MARKS_MISMATCH",
    "message": "Section marks (38) do not equal total marks (40).",
    "details": [{ "field": "sections[2].marks", "issue": "…" }],
    "ehRef": "EH-8"
  },
  "meta": { "requestId": "req_..." }
}
```

- HTTP status carries the class (`400/401/403/404/409/422/429/5xx`); `error.code` carries the machine-readable reason.
- `requestId` is propagated into queued jobs so a failure can be traced end to end.

### Pagination, filtering, sorting

```
GET /questions?page=2&pageSize=50            # cursor supported for large sets: ?cursor=...
                &filter[subjectId]=...&filter[difficulty]=medium
                &filter[status]=approved&filter[source]=textbook
                &q=photosynthesis                    # full-text
                &sort=-createdAt,marks                # '-' = descending
```

- `pageSize` is capped server-side; the cap is returned in `meta.pagination`.
- Filters use a whitelisted set per resource; unknown filter keys are rejected, not ignored.
- List responses always include `meta.pagination` (`page`, `pageSize`, `total`, `hasNext`, and `cursor` where applicable).

### Cross-cutting API rules

- **Idempotency:** mutating actions that trigger jobs accept an `Idempotency-Key` header so client retries never double-enqueue.
- **Validation:** requests validated at the edge by Pydantic models from `packages/contracts`, with `extra="forbid"` so unknown fields are rejected rather than silently dropped.
- **Auth:** every non-public route requires a bearer token and resolves org context (see Authentication Flow).
- **Rate limiting:** per-user and per-org limits (Redis); exhaustion returns `429` with `Retry-After` (EH-14).
- **Consistency:** field naming is `camelCase` in JSON; timestamps are ISO-8601 UTC.

## Request Flow

The generic path for any synchronous API call. Every request is authenticated, tenant-scoped, authorized, and audited.

```
Browser
  │  1. user action
  v
Next.js
  │  2. attach session token + active org context
  v
FastAPI ── Edge concerns ───────────────────────────────────
  │  3. HTTPS terminated upstream, rate limit (Redis), request id
  │  4. get_current_user  : validate token -> resolve user
  │  5. get_org_context   : resolve active organization -> set org context
  │  6. require_roles(..) : check memberships.role against required role
  │  7. Pydantic model    : validate + coerce; extra="forbid"
  v
Business module (service layer)
  │  8. cache read (Redis) on hot, org-keyed reads
  v
Repository / SQLAlchemy
  │  9. query with org_id predicate; SET LOCAL app.org_id + RLS enforce isolation
  v
PostgreSQL
  │ 10. rows returned
  v
Business module
  │ 11. write audit record for mutating actions (append-only)
  │ 12. emit domain event -> notifications / usage metering
  v
FastAPI
  │ 13. serialize response (explicit field allowlist, role-filtered)
  v
Next.js ──> Browser
```

**Rules**

- Deny by default: a request that fails any guard never reaches a business module.
- Every tenant-owned query carries `org_id`; RLS is the second line of defence, not the first.
- Heavy work is never done in-request — it is enqueued and a job id is returned (see Generation Flow, OCR Flow).
- Mutations are audited before the response is returned.
- Failures map to the error catalogue in `01_Product_Requirements.md` (EH-1 … EH-18).

## Authentication Flow

### Login and session establishment

```
Browser                Next.js              FastAPI (Auth)       PostgreSQL      Redis
   │                      │                      │                   │            │
   │ 1. email+password    │                      │                   │            │
   ├─────────────────────>│                      │                   │            │
   │                      │ 2. POST /auth/login  │                   │            │
   │                      ├─────────────────────>│                   │            │
   │                      │                      │ 3. rate-limit check ───────────>│
   │                      │                      │ 4. load user by email ─────────>│
   │                      │                      │<── user + hash ────│            │
   │                      │                      │ 5. verify password hash        │
   │                      │                      │ 6. load memberships ──────────>│
   │                      │                      │<── orgs + roles ───│            │
   │                      │                      │ 7. issue access + refresh token│
   │                      │                      │ 8. store refresh/session ──────>│
   │                      │                      │ 9. audit: login success ──────>│
   │                      │<── tokens + orgs ────│                   │            │
   │<── session set ──────│                      │                   │            │
   │                      │                      │                   │            │
   │ 10. if multiple orgs: select active organization                            │
   │ 11. org context attached to every subsequent request                        │
```

### Per-request authorization

```
Request with token
      │
      v
  get_current_user ── token valid? ──NO──> 401 + audit failed attempt
      │ YES
      v
  get_org_context ── active membership in this org? ──NO──> 403 + audit
      │ YES
      v
  require_roles(..) ── does memberships.role satisfy the route's requirement? ──NO──> 403 + audit
      │ YES
      v
  Scope filter ── teacher scoped to specific programs/subjects? ── narrow the query
      │
      v
  Handler executes with (user, org, role, scope) in request context
      │
      v
  PostgreSQL RLS re-asserts org_id isolation at the row level
```

### Failure and lifecycle paths

```
Invalid credentials      -> generic failure (no account disclosure) + audit + rate-limit counter
Repeated failures        -> lockout window (Redis counter) + audit
Expired access token     -> refresh via refresh token -> new access token
Revoked session/member   -> refresh rejected -> forced re-login (takes effect next request)
Password reset           -> single-use expiring token by email -> invalidated on use
Cross-tenant access      -> Super Admin only, time-boxed, every read audited
```

Maps to FR-1.1–FR-1.9 and NFR-2 in `01_Product_Requirements.md`.

## Security Architecture

Security is layered: nothing depends on a single control. Tenant isolation (covered above) is the backbone; the controls below wrap it.

```
                         Internet
                            │
                            v
              ┌──────────────────────────┐
              │  Edge: TLS, HSTS, WAF     │  HTTPS only, HTTP->HTTPS
              └────────────┬─────────────┘
                           v
              ┌──────────────────────────┐
              │ Next.js                   │  httpOnly+Secure+SameSite cookies
              │  - no secrets in client   │  CSRF token on state-changing forms
              └────────────┬─────────────┘
                           v
              ┌──────────────────────────┐
              │ FastAPI edge middleware   │  security headers, strict CORS
              │  - rate limit (Redis)     │  body-size caps, request-id
              └────────────┬─────────────┘
                           v
              ┌──────────────────────────┐
              │ Dependency chain          │  Auth -> Tenant -> Roles -> Scope
              └────────────┬─────────────┘
                           v
              ┌──────────────────────────┐
              │ Service + Repository      │  parameterized queries (no string SQL)
              └────────────┬─────────────┘
                           v
              ┌──────────────────────────┐
              │ PostgreSQL + RLS          │  org_id row-level isolation
              └──────────────────────────┘
```

### Authentication & session

| Control | Approach |
|---------|----------|
| Password storage | Argon2id (or bcrypt) with per-user salt; never plaintext, never reversible. |
| Access token | Short-lived JWT (≈15 min) carrying `userId`, active `orgId`, `role`. |
| Refresh token | Long-lived, rotating, single-use; stored server-side (Redis) so it can be revoked. |
| Token rotation | Each refresh issues a new refresh token and invalidates the old one; reuse detection forces logout of the session family. |
| Session revocation | Role change / removal / logout invalidates server-side session state; takes effect on the next request. |
| Password reset | Single-use, expiring, emailed token; invalidated on use. |
| Brute force | Per-account + per-IP rate limits and lockout windows (Redis counters); generic failure messages (no account enumeration). |

### Transport & headers

- TLS everywhere; HSTS; HTTP redirected to HTTPS.
- Security-header middleware sets CSP, `X-Content-Type-Options`, frame options, and referrer policy.
- Strict CORS allow-list (the web origin only); credentials mode explicit.

### Web/session attacks

| Threat | Mitigation |
|--------|------------|
| CSRF | Auth cookies are `httpOnly` + `Secure` + `SameSite`; CSRF token on state-changing form posts. |
| XSS | Output encoding by default (React); CSP; sanitize any rich text; never `dangerouslySetInnerHTML` on untrusted content. |
| SQL injection | ORM with parameterized queries only; no string-concatenated SQL, ever. |
| Clickjacking | Frame-ancestors CSP / `X-Frame-Options`. |
| Mass assignment | Pydantic `extra="forbid"`; the server never trusts a client-supplied `orgId`, `role`, `status`, `createdBy`, or `scope`. |

### File upload security (textbooks)

```
Client-side check (type + size)      ── advisory only, never trusted
        │
        v
Server validation
   - MIME + magic-byte sniff (PDF/DOCX/PNG/JPG)     (CN-2, EH-4)
   - size <= 500 MB, page-count cap                 (CN-1, CN-6, EH-5)
        │
        v
Signed, expiring upload URL -> direct-to-storage    (private bucket)
        │
        v
Virus / malware scan on the stored object
   - infected  -> quarantine + reject + notify       (EH-4)
   - clean     -> enqueue OCR
```

- Uploads land in a **private** bucket; nothing is publicly readable.
- Files are served only via short-lived signed URLs.
- Rendered/extracted artifacts inherit the same private + signed-URL rules.

### Secrets & configuration

- No secrets in the repo or the client bundle. Secrets come from a secrets manager / injected environment at deploy time.
- Config is validated by the Pydantic `Settings` model in `packages/config` at boot; the app refuses to start on missing, placeholder, or invalid config.
- Database credentials, provider API keys, and signing keys are rotatable without code changes.

### Authorization recap

- Deny-by-default guard chain (Auth → Tenant → Roles → Scope) precedes every handler.
- RLS is the second line of defence, so a missed application-level check still cannot leak cross-tenant rows.
- Cross-tenant Super Admin access is time-boxed and fully audited (BR-7, BR-11).

Maps to NFR-2 and BR-7 / BR-11 in `01_Product_Requirements.md`.

## Generation Flow

Paper generation is the platform's core transaction: retrieval-first, AI only for gaps, validated, and fully provenance-tracked.

```
Teacher (Paper Builder)
   │ 1. blueprint: board, class, subject, chapters, pattern, marks,
   │    difficulty mix, source mix, generation mode
   v
Next.js
   │ 2. POST /papers/generate
   v
FastAPI (Papers / Assembler)
   │ 3. guards: auth -> tenant -> role
   │ 4. validate blueprint (marks reconcile, CN-4/CN-5, source mix, BR-1 feasible)
   │    └── invalid -> 400 with the exact mismatch (EH-8), nothing enqueued
   │ 5. check plan quota (BR-10) ── exhausted -> blocked (EH-13)
   │ 6. snapshot blueprint -> INSERT paper (status = draft) ──────────> PostgreSQL
   │ 7. enqueue generation job ──────────────────────────────────────> Redis Queue
   │ 8. return { paperId, jobId } immediately
   v
Generation Worker  (consumes Redis Queue)
   │
   │ 9. load blueprint snapshot + org generation policy ─────────────> PostgreSQL
   │
   │ 10. FOR EACH blueprint slot (section, type, marks, difficulty, chapter):
   │     │
   │     ├─ Retrieval Engine
   │     │    a. hard filters: org scope, chapter, type, marks, difficulty,
   │     │       status = approved, source allowed by mode, curriculum version
   │     │    b. full-text (tsvector) + trigram (pg_trgm) ranking
   │     │       [vector similarity is a deferred stage — ADR-007]
   │     │    c. exclude questions already placed in this paper
   │     │
   │     ├─ enough candidates? ──YES──> fill from retrieval   [AI-1: no AI call]
   │     │
   │     └─ NO ──> AI allowed for this mode / org policy?
   │                 │ NO  -> record unmet slot              [AI-3]
   │                 │ YES
   │                 v
   │              AI Service Layer
   │                 d. build grounded context (textbook assets / exemplars / outcomes)
   │                 e. resolve active prompt version
   │                 f. call LLM provider
   │                 g. validate: structure -> answer -> scope -> difficulty  [AI-5]
   │                 h. similarity check vs. in-scope bank
   │                      > 90% -> reject + regenerate       [AI-4]
   │                 i. bounded retries exhausted -> unmet slot [AI-3]
   │                 j. attach source = ai_generated + prompt version [AI-8]
   │                 k. persist question as draft ───────────────────> PostgreSQL
   │
   │ 11. paper-level checks:
   │     - difficulty balance ── off target -> regenerate ONLY offending slots [AI-2]
   │     - Mixed Mode floor ── textbook-derived < 70% -> reduce AI share / flag [BR-1]
   │     - duplicates within paper -> none permitted
   │     - marks reconciliation vs. blueprint [BR-3]
   │
   │ 12. persist paper questions + provenance + coverage report ─────> PostgreSQL
   │ 13. record usage (papers, AI questions) ────────────────────────> PostgreSQL
   │ 14. publish progress/completion ───────────────────────────────> Redis pub/sub
   v
FastAPI ──> Next.js (live progress) ──> Teacher
   │
   │ 15. notification "Paper Ready" (NT-3)
   v
Paper Preview: source badges, coverage report, warnings, unmet slots
   │
   └─> edit / swap / lock / regenerate section -> new paper version
       └─> submit for approval -> Approval Flow -> export
```

### Generation failure paths

```
Timeout           -> retry once -> timeout again -> cancel job -> blueprint preserved
                     -> notify with guidance (EH-2, NT-8)
LLM error/refusal -> retry with backoff -> fallback prompt -> still failing
                     -> retrieval fill -> else unmet slot (EH-3, AI-3)
Insufficient bank -> AI gap-fill where allowed -> else unmet slots reported (EH-9)
Cancelled by user -> job dropped, queue slot freed, no partial paper persisted
Worker outage     -> job stays queued (not lost); status shows "waiting" (EH-16)
```

## Object Storage Flow

Large binaries — raw uploads, extracted pages, rendered exports — never stream through the API. Clients talk to storage directly using short-lived signed URLs; the API only issues permission and records metadata.

### Upload (direct-to-storage, signed URL)

```
Browser                 FastAPI (Textbooks)       Object Storage        Redis        Worker
   │                          │                        │                 │             │
   │ 1. POST /textbooks       │                        │                 │             │
   │    {name,type,size,tags} │                        │                 │             │
   ├─────────────────────────>│                        │                 │             │
   │                          │ 2. validate type+size  │                 │             │
   │                          │    (CN-1,CN-2,EH-4/5)   │                 │             │
   │                          │ 3. INSERT textbooks     │                 │             │
   │                          │    (status=uploaded)    │                 │             │
   │                          │ 4. create signed PUT URL│                 │             │
   │                          ├───────────────────────>│                 │             │
   │                          │<── signed URL ──────────│                 │             │
   │<── {textbookId,uploadUrl}│                        │                 │             │
   │                          │                        │                 │             │
   │ 5. PUT file bytes ───────┼───────────────────────>│  (private)      │             │
   │<── 200 ──────────────────┼────────────────────────│                 │             │
   │                          │                        │                 │             │
   │ 6. POST /textbooks/:id/complete                    │                 │             │
   ├─────────────────────────>│ 7. verify object exists │                 │             │
   │                          │    + size/etag match ──>│                 │             │
   │                          │<── ok ──────────────────│                 │             │
   │                          │ 8. enqueue OCR job ─────┼────────────────>│             │
   │                          │ 9. return {jobId}       │                 │ 10. consume │
   │<── {jobId} ──────────────│                        │                 ├────────────>│
   │                          │                        │  11. GET object <┼─────────────│
```

**Why direct-to-storage:** a 500 MB file never occupies API memory or a request thread; the API stays fast and stateless. The completion callback (step 6) is what makes the upload "real" — an abandoned signed URL leaves an `uploaded` record with no object, which a sweeper reaps (no orphan assets).

### Download (signed read)

```
Client -> GET /exports/:id  ->  FastAPI checks ownership + status
                            ->  issues short-lived signed GET URL
                            ->  client fetches artifact directly from storage
```

### Storage layout & rules

```
bucket (private)
├── org/{orgId}/textbooks/{textbookId}/raw/…       # original upload
├── org/{orgId}/textbooks/{textbookId}/pages/…      # extracted page artifacts
└── org/{orgId}/exports/{exportId}/…                # rendered PDF/DOCX
```

- Every object is namespaced by `orgId` — path scoping mirrors row scoping.
- Bucket is private; access is always via signed, expiring URLs (no public objects).
- Lifecycle policies expire transient artifacts (old exports, orphaned raws) automatically.

## OCR Flow

Textbook ingestion turns an uploaded file into chapter-mapped, embedded, retrievable assets.

```
Teacher (Upload Textbook)
   │ 1. choose file + tag board / class / subject
   v
Next.js
   │ 2. validate type + size client-side (PDF / DOCX / PNG / JPG, <= 500 MB)
   │ 3. request upload target
   v
FastAPI (Textbook Ingest)
   │ 4. guards: auth -> tenant -> role
   │ 5. server-side validation: MIME + magic bytes + size (CN-1, CN-2)
   │    └── invalid -> rejected before storage (EH-4, EH-5)
   │ 6. issue signed upload URL ─────────────────────────────────> Object Storage
   v
Browser ── direct upload of raw file ─────────────────────────────> Object Storage
   │
   v
FastAPI
   │ 7. INSERT textbooks (status = uploaded) ────────────────────> PostgreSQL
   │ 8. enqueue OCR job ─────────────────────────────────────────> Redis Queue
   │ 9. return { textbookId, jobId }
   v
OCR Worker  (consumes Redis Queue)
   │
   │ 10. status -> ocr                                        ──> PostgreSQL
   │ 11. fetch raw file                                       <── Object Storage
   │ 12. split into pages; page count check (CN-6)
   │ 13. FOR EACH page:
   │     a. OCR / text extraction (native text layer preferred, OCR as fallback)
   │     b. store page artifact                               ──> Object Storage
   │     c. publish per-page progress                         ──> Redis pub/sub
   │
   │ 14. structural parsing -> typed textbook_assets:
   │     paragraph | exercise | question | diagram | table | activity | formula
   │     each retaining page-level provenance
   │ 15. status -> parsed                                     ──> PostgreSQL
   │
   │ 16. chapter mapping (heading detection + curriculum match)
   │     └── low confidence -> flag for manual mapping (EH-6, FR-2.9)
   │
   │ 17. [deferred — ADR-007] generate embeddings for retrievable assets
   │     INSERT into question_embeddings                      ──> PostgreSQL
   │     At MVP this stage is a no-op and status advances directly.
   │ 18. status -> embedded                                   ──> PostgreSQL
   │ 19. record usage (OCR pages)                             ──> PostgreSQL
   │ 20. publish completion                                   ──> Redis pub/sub
   v
FastAPI ──> Next.js (OCR Progress, live) ──> Teacher
   │
   │ 21. notification "OCR Completed" (NT-1)
   v
Question Extraction
   │ 22. assets of kind exercise/question -> proposed questions
   │     source = textbook (printed exercises) or intext (explanatory body)
   │ 23. de-duplicate on content_hash; near-duplicates flagged
   │ 24. created as status = draft                            ──> PostgreSQL
   v
Teacher reviews in Question Bank -> approve / edit / reject
   │
   v
Approved questions become retrievable material for the Generation Flow
```

### OCR state machine

```
   uploaded ──> ocr ──> parsed ──> embedded        (happy path)
       │         │        │
       │         v        v
       └────> failed <────┘
                 │
                 └─> retry (bounded, backoff)
                       ├─ resume from failed pages only (idempotent)
                       └─ partially extracted assets are RETAINED and usable
```

### OCR failure paths

```
Unsupported / corrupt file -> rejected at validation, no record created (EH-4)
Oversize / too many pages  -> rejected with the actual limit named (EH-5)
Storage unavailable        -> upload retried; no orphan textbook record (EH-17)
OCR engine failure         -> retry(bounded) -> status = failed
                              -> notify teacher + retry action (EH-1, NT-2)
                              -> teacher can still generate from the existing bank
Ambiguous chapter mapping  -> assets flagged, manual mapping prompted (EH-6)
Reprocess request          -> idempotent; no duplicate assets (FR-2.11)
```

## Retrieval Architecture

The **Retrieval Engine** is a pipeline, not a single query. Each blueprint slot passes through stages that progressively narrow a large bank down to the best-fit, non-duplicate candidates.

```
   Blueprint slot (section, type, marks, difficulty, chapter, mode)
         │
         v
   ┌────────────────────┐
   │ Metadata Filter    │  org scope, curriculum version, chapter, type,
   │                    │  marks, difficulty, status=approved,
   │                    │  source allowed by generation mode
   └─────────┬──────────┘
             v
   ┌────────────────────┐
   │ FTS Search         │  full-text match on stem/keywords (GIN/trigram)
   └─────────┬──────────┘
             v
   ┌────────────────────┐
   │ Vector Search      │  semantic nearest-neighbour (pgvector HNSW)
   │  [DEFERRED]        │  adopted on measured recall evidence — ADR-007
   └─────────┬──────────┘
             v
   ┌────────────────────┐
   │ Hybrid Ranking     │  blend lexical + semantic scores
   └─────────┬──────────┘
             v
   ┌────────────────────┐
   │ Reranker           │  fit to slot intent (outcome, Bloom, difficulty)
   └─────────┬──────────┘
             v
   ┌────────────────────┐
   │ Duplicate Removal  │  content_hash + high-similarity dedup;
   │                    │  exclude questions already placed in this paper
   └─────────┬──────────┘
             v
   ┌────────────────────┐
   │ Coverage Optimizer │  spread across chapters/outcomes per weightage
   └─────────┬──────────┘
             v
   ┌────────────────────┐
   │ Final Selection    │  enough? -> fill (AI-1)   |   short? -> gap -> AI
   └────────────────────┘
```

- **Reads only.** Retrieval never writes; it returns candidate ids + scores to Papers/Assembly.
- **Hard filters first** (cheap, index-backed) shrink the set before expensive vector/rerank stages.
- **Coverage optimization** is why retrieval sees the whole slot set, not one slot in isolation — it balances chapter/outcome spread against the blueprint weightage.
- A slot that comes up short after dedup is a **gap** handed to the AI pipeline (or recorded as unmet if AI isn't allowed — AI-3).

## AI Service Architecture

The AI layer is decomposed into focused components behind one `AiService` facade. No caller ever talks to an LLM directly.

```
                         ┌───────────────┐
   caller (Assembly) ──> │   AiService    │  facade
                         └───────┬───────┘
       ┌─────────────┬───────────┼───────────┬──────────────┐
       v             v           v           v              v
┌────────────┐ ┌───────────┐ ┌────────┐ ┌──────────┐ ┌──────────────┐
│Prompt Mgr  │ │Prompt Ver.│ │Context │ │Model     │ │Response      │
│(templates) │ │Manager    │ │Builder │ │Selector  │ │Parser        │
└────────────┘ └───────────┘ └────────┘ └────┬─────┘ └──────┬───────┘
                                              v              v
                                        ┌───────────┐  ┌──────────────┐
                                        │LLM Adapter│  │Validation    │
                                        │(provider- │  │Engine        │
                                        │ agnostic) │  └──────┬───────┘
                                        └───────────┘         │
   ┌──────────────┬───────────────┬──────────────┬───────────┼───────────────┐
   v              v               v              v           v               v
┌────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌──────────┐ ┌──────────────┐
│Safety  │ │Similarity  │ │Difficulty  │ │Bloom       │ │Distractor│ │Question      │
│Valid.  │ │Checker     │ │Estimator   │ │Classifier  │ │Generator │ │Improver      │
└────────┘ └────────────┘ └────────────┘ └────────────┘ └──────────┘ └──────────────┘
                                                             │
                                                             v
                                                       ┌───────────┐
                                                       │Retry Mgr  │  bounded, backoff
                                                       └───────────┘
```

| Component | Responsibility |
|-----------|----------------|
| Prompt Manager | Owns prompt templates and their assembly from parts. |
| Prompt Version Manager | Resolves the **active** prompt version; records which version produced each output (`prompt_versions`, AI-8). |
| Context Builder | Assembles grounded context: textbook assets, exemplars, outcomes, constraints. |
| Model Selector | Picks model/params per task (generation vs. classification vs. embedding). |
| LLM Adapter | Provider-agnostic call boundary (see AI Provider Strategy). |
| Response Parser | Parses model output into structured question objects; rejects malformed output. |
| Validation Engine | Orchestrates structure → answer → scope → difficulty checks (AI-5). |
| Safety Validator | Filters unsafe/off-scope content. |
| Similarity Checker | Compares candidates vs. the in-scope bank; > 90% ⇒ reject + regenerate (AI-4). |
| Difficulty Estimator | Validates/labels difficulty against the requested level. |
| Bloom Classifier | Assigns/validates Bloom level. |
| Distractor Generator | Produces plausible MCQ distractors. |
| Question Improver | Refines stems/options that fail a soft check before hard rejection. |
| Retry Manager | Bounded retries with backoff; escalates to fallback prompt then gap (AI-3). |

## AI Provider Strategy

The platform is provider-agnostic. Business logic depends on the `LLMAdapter` interface, never on a concrete SDK.

```
        AiService
            │
            v
   ┌──────────────────┐
   │   LLM Adapter    │   interface: generate(), classify(), embed()
   │  (interface)     │
   └───┬───┬───┬───┬──┘
       │   │   │   │
       v   v   v   v
   ┌──────────────┐┌─────────────────────────┐
   │Gemini Adapter││ (future adapters — each  │
   │ IMPLEMENTED  ││  needs its own prompt    │
   │              ││  versions + eval run)    │
   └──────────────┘└─────────────────────────┘
```

- **Provider:** the **Gemini API**, and only Gemini (ADR-008). A Pro-tier model handles generation and hard reasoning; a Flash-tier model handles classification, tagging, and validation support. Model IDs live in configuration and are never hardcoded, so a model upgrade is a config change plus an eval run.
- Provider choice is configuration, selectable per task by the Model Selector (e.g. a cheaper model for classification, a stronger model for generation).
- Adapters normalize request/response shapes, token accounting, and error taxonomy so the Retry Manager sees one uniform failure model.
- **Never call a provider SDK directly** from a module — always through the adapter, so failover and cost/latency policy live in one place.

## AI Generation Pipeline

The end-to-end path for a single generated question, expanded from the "Retrieval Engine → AI → Done" shorthand. This is the heart of the product.

```
   Blueprint slot
       │
       v
   Retrieval Engine ────────> enough approved candidates?
       │                          │ YES -> use retrieved question   [AI-1] ─────┐
       │ NO / short               │                                             │
       v                                                                        │
   Candidate Selection  (best partial matches / exemplars for grounding)        │
       │                                                                        │
       v                                                                        │
   Context Builder  (textbook assets + exemplars + outcome + constraints)       │
       │                                                                        │
       v                                                                        │
   Prompt Builder  (template + active prompt version)          [AI-8]           │
       │                                                                        │
       v                                                                        │
   LLM  (via adapter)                                                           │
       │                                                                        │
       v                                                                        │
   Response Parser  (structured object or reject-malformed)                     │
       │                                                                        │
       v                                                                        │
   ┌───────────── Validation gates (fail -> improve or regenerate) ──────────┐  │
   │ Safety Validation                                                       │  │
   │ Answer Validation      (a correct, unambiguous answer exists)           │  │
   │ Scope Validation       (within chapter/curriculum)                      │  │
   │ Difficulty Validation  (matches requested level)          [AI-2 basis]  │  │
   │ Bloom Validation       (matches requested cognitive level)              │  │
   │ Similarity Check       (> 90% vs bank -> reject+regenerate) [AI-4]      │  │
   └──────────────────────────────┬──────────────────────────────────────────┘  │
       │ pass                      │ fail (bounded retries -> fallback -> gap AI-3)│
       v                                                                        │
   Persist question (status=draft, source=ai_generated, prompt version)         │
       │                                                                        │
       ├<───────────────────────────────────────────────────────────────────────┘
       v
   Paper Assembly  (place into slot)
       │
       v
   Coverage Validation  (paper-level: difficulty balance [AI-2],
                         Mixed-Mode 70% floor [BR-1], marks reconcile [BR-3],
                         no in-paper duplicates)
       │
       v
   Save paper version + provenance + coverage report
```

Per-question validation is a gate stack: a soft failure routes to the **Question Improver** for one refinement pass; a hard failure routes to the **Retry Manager** (bounded retries → fallback prompt → recorded gap). Paper-level coverage validation runs once the slots are filled and can trigger targeted regeneration of only the offending slots (AI-2) rather than the whole paper.

## Caching Strategy

Redis backs both queues and cache. The cache is org-keyed and event-invalidated so a tenant never sees another tenant's data and never sees stale data after a write.

| Cached data | Strategy | TTL | Invalidation |
|-------------|----------|-----|--------------|
| User session / token state | Write-through | session length | On logout / role change / refresh rotation |
| Organization settings | Lazy (read-through) | 15 min | On settings update event |
| Curriculum (boards/classes/subjects) | Lazy | 60 min | On `curriculum.version_published` |
| Subject / chapter lists | Lazy | 30 min | On curriculum change |
| Blueprint templates | Lazy | 15 min | On `template.saved` / archive |
| Question search results | Lazy | 5 min | On `question.created` / `question.approved` for that scope |
| Prompt versions (active set) | Write-through | until change | On `prompt.version_promoted` / `retired` |
| Plan/quota counters | Write-through | period | On `usage.recorded` |

- **Lazy (read-through):** miss → load from DB → populate cache. Used for read-heavy, slowly-changing reference data.
- **Write-through:** update cache and DB together. Used where staleness is unacceptable (sessions, active prompt versions, quotas).
- **Invalidate on update:** domain events (see Event Architecture) drop or refresh the affected keys — the cache is never left stale after a write.
- Cache keys always include `orgId` (except truly global reference data), so isolation holds at the cache layer too.

## Transaction Boundaries

Multi-step mutations commit atomically. If any step fails, nothing partial persists.

**Generate Paper** — a single DB transaction wraps the synchronous setup, then enqueues:

```
BEGIN
  1. INSERT generated_papers   (status = draft, blueprint snapshot)
  2. INSERT paper_versions     (v1, empty)
  3. check + reserve quota     (subscriptions / usage)   [BR-10]
  4. INSERT usage              (generation reserved)
  5. INSERT audit_logs         (paper.generation_requested)
COMMIT
  ── only after commit ──> enqueue generation job (Redis)
```

The **enqueue happens after commit**, never inside the transaction: a job must never reference a paper row that a rollback erased. Conversely, the paper row exists before the job runs, so the worker can always load it.

Other transactional units:

| Action | Atomic unit |
|--------|-------------|
| Approve question (new version) | Insert new version + flip previous `is_current` + audit — one transaction (BR-8). |
| Approve paper | Insert `approvals` row + set paper `approved` + audit — one transaction. |
| Import questions | Per-batch transaction; a bad row fails its batch, not the whole import. |
| Record usage | Usage insert + counter update — one transaction. |

**Rules:** keep transactions short (no LLM/network calls inside a transaction); do external/async work only after commit; make the post-commit enqueue idempotent so a retry can't double-book.

## Event Architecture

Modules stay decoupled by emitting domain events after a successful commit. Notifications, metering, analytics, and cache invalidation subscribe rather than being called inline.

```
   textbook.uploaded
        └─> textbook.parsed
                └─> question.extracted
                        └─> embedding.generated
                                └─> question.approved
paper.generation_requested
        └─> paper.generated
                └─> paper.submitted
                        └─> paper.approved  |  paper.rejected
                                └─> export.requested
                                        └─> export.ready
```

| Event | Emitted when | Typical subscribers |
|-------|--------------|---------------------|
| `textbook.uploaded` | Upload completed | OCR trigger |
| `textbook.parsed` / `textbook.embedded` | Extraction / embedding done | Notifications (NT-1), Questions |
| `textbook.failed` | Ingestion failed | Notifications (NT-2) |
| `question.extracted` | Asset → proposed question | Questions (dedup), Notifications (NT-12) |
| `question.approved` | Question approved | Cache invalidation, Analytics |
| `paper.generation_requested` | Generation enqueued | Usage (reserve), Audit |
| `paper.generated` | Job completed | Notifications (NT-3), Usage (finalize) |
| `paper.submitted` | Sent for review | Notifications (NT-4) |
| `paper.approved` / `paper.rejected` | Review decision | Notifications (NT-5/NT-6), Analytics |
| `export.ready` / `export.failed` | Render outcome | Notifications (NT-9/NT-10) |
| `plan.limit_reached` | Quota exhausted | Notifications (NT-15), Billing |

- Events are emitted **after commit**, so subscribers never react to work that rolled back.
- Subscribers are idempotent — the same event delivered twice must not double-send or double-meter.
- Events carry ids + context, not whole entities; subscribers load what they need through the owning module.

## Notification Architecture

Notifications subscribe to domain events and fan out across channels behind one service, so adding SMS/push later is a new channel adapter, not new call sites.

```
   Domain events
        │
        v
   ┌──────────────────────┐
   │ Notification Service │  resolve recipients (role + scope), template,
   │                      │  preferences, dedup, read-state
   └──────────┬───────────┘
     ┌────────┼─────────┬─────────────┐
     v        v         v             v
 ┌──────┐ ┌───────┐ ┌─────────┐  ┌─────────┐
 │In-app│ │ Email │ │  SMS    │  │  Push   │
 │(now) │ │ (now) │ │(future) │  │(future) │
 └──────┘ └───────┘ └─────────┘  └─────────┘
```

- **In-app** and **Email** ship in v1; **SMS** and **Push** are future channels behind the same interface.
- Recipients are resolved by role + scope (e.g. NT-4 → the org's reviewers), honoring per-user preferences.
- Each notification maps to the catalogue NT-1 … NT-15 in `01_Product_Requirements.md` and deep-links to the right screen.
- Delivery + read-state are persisted; the in-app channel powers the notifications panel (S-15).

## Failure Recovery

Every queue-backed operation is designed to fail safely and be replayable.

```
   Job enqueued (idempotency key)
        │
        v
   Worker picks up ──> success ──> ack
        │ error
        v
   Retry? (attempt < max)
        │ YES -> re-enqueue with exponential backoff (+jitter)
        │ NO
        v
   Dead Letter Queue (DLQ)  ── inspectable, replayable
        │
        ├─ automatic: nothing further (awaiting operator)
        └─ manual: operator replays after fixing root cause
```

| Mechanism | Policy |
|-----------|--------|
| Retry count | Bounded per job type (e.g. OCR 3, generation 2, export 3). |
| Backoff | Exponential with jitter to avoid thundering herds. |
| Idempotent jobs | Keyed by (entity id + attempt-invariant key); a replay never duplicates data (assets, questions, usage). |
| Poison messages | Jobs that always fail are moved to the DLQ instead of looping forever. |
| Dead-letter queue | Terminal failures land here with full context and `requestId` for tracing. |
| Automatic retry | Transient errors (timeouts, provider blips) retried within the bounded budget. |
| Manual retry / replay | Operators (and, for user-facing cases, the teacher) can replay DLQ jobs after a fix. |
| Partial results | OCR retains already-extracted assets and resumes from failed pages (idempotent). |
| Worker outage | In-flight jobs are not acked, so they return to the queue — never silently lost (EH-16). |

Maps to the error catalogue EH-1 … EH-18 in `01_Product_Requirements.md`.

## Worker Scaling Strategy

Workers scale horizontally and independently — the queue decouples arrival rate from processing rate, so bursts wait instead of degrading the API.

| Worker | Baseline replicas | Scales on | Concurrency notes |
|--------|-------------------|-----------|-------------------|
| OCR Worker | 2 | Queue depth / oldest-job age | CPU/IO heavy; cap per-worker concurrency to protect memory on large files. |
| Generation Worker | 5 | Queue depth; generation is the hot path | Bounded parallel LLM calls per job to respect provider rate limits. |
| Export Worker | 2 | Queue depth | Render concurrency capped to bound memory per instance. |

- **Horizontal scaling:** add replicas; the queue distributes work, no coordination needed.
- **Autoscaling:** replica count tracks queue depth / age thresholds, with min/max bounds.
- **Concurrency limits:** each worker caps in-flight jobs so a single instance can't exhaust memory or blow provider rate limits.
- Workers are stateless and idempotent, so scaling in/out is safe mid-flight.

## Observability

Every request and job is traceable end to end; failures alert before users report them.

```
   App + Workers
        │  structured logs (Pino)      -> log aggregation
        │  metrics (Prometheus)        -> Grafana dashboards + alerts
        │  traces (OpenTelemetry)      -> request -> job -> DB spans
        │  errors (Sentry)             -> grouped exceptions + release tags
        v
   Alerts (queue depth, error rate, latency, DLQ growth, quota breaches)
```

| Signal | Tooling | Examples |
|--------|---------|----------|
| Logging | Pino (structured JSON) | Request id propagated into jobs; one line per request/job outcome. |
| Metrics | Prometheus + Grafana | Queue depth, job latency, generation success rate, API p95, DB pool usage. |
| Tracing | OpenTelemetry | A generation request traced across API → queue → worker → LLM → DB. |
| Errors | Sentry | Grouped exceptions with release + tenant context. |
| Alerts | Alertmanager | DLQ growth, error-rate spike, latency SLO breach, quota exhaustion. |

The platform dashboard (FR-8.2) surfaces queue depth and failure rate to Super Admins.

## Additional Sequence Diagrams

Complementing Authentication, Generation, and OCR, these cover the remaining core transactions.

### Question Import

```
User -> POST /questions/import (file/rows)
     -> API: validate rows against schema (per-row)
     -> enqueue import job (large files) | inline (small)
     -> Worker: for each row -> normalize -> content_hash -> dedup check
             -> duplicate? skip + report | new? INSERT (status=draft)
             -> trigger embedding (question_embeddings)
     -> emit question.extracted; usage recorded
     -> notify NT-11 with an error report for skipped/failed rows
```

### Paper Approval

```
Teacher -> POST /papers/:id/submit  (status draft -> submitted)  -> NT-4 to reviewers
Reviewer -> GET /papers/:id (review mode)
         -> POST /papers/:id/approve | /reject (comment)
            - separation-of-duties check (BR-6): author != sole approver
            - transaction: INSERT approvals + set status + audit
         -> approved -> NT-5 (author), unlocks export
            rejected -> NT-6 (author) with comments; returns to editing
```

### Export

```
User -> POST /papers/:id/export (template, answer-key?)
     -> check status=approved (BR-5) + quota (BR-10)
     -> enqueue export job -> return {exportId}
     -> Export Worker: render (template) -> PDF/DOCX -> store (private)
             -> success: emit export.ready -> NT-9 -> signed download URL
             -> failure: retry -> DLQ -> NT-10 (EH-11)
```

### Template Save

```
User -> POST /templates (layout+branding)  -> status=draft
     -> preview render (sample paper)
     -> PATCH /templates/:id -> status=active   (emit template.saved -> cache invalidation)
     -> POST /templates/:id/archive -> status=archived (kept for existing papers)
```

### Question Editing & Versioning

```
User -> PATCH /questions/:id
     -> is the question approved+immutable? (BR-8)
        - draft  : edit in place
        - approved: transaction ->
             INSERT new version (lineage root preserved)
             set previous is_current=false, new is_current=true
             re-embed; audit
     -> only one current version per root at all times
```

### Prompt Update (Super Admin)

```
Super Admin -> POST /prompt-versions (candidate)
            -> evaluate against a fixed test set
            -> POST /prompt-versions/:id/promote  (candidate -> active)
                 - previous active -> retired (one active per role/scope)
                 - emit prompt.version_promoted -> cache invalidation
            -> new generations record the new active version (AI-8)
            -> rollback = promote a prior version
```

### Subscription Renewal / Limit

```
Billing (scheduled) -> evaluate period + usage vs. plan
     -> nearing expiry / threshold -> NT-7 (Coaching Admin + tenant Super Admin)
     -> renewal succeeds -> reset counters for new period
     -> renewal fails / quota exhausted -> plan.limit_reached
             -> restricted actions blocked (BR-10, EH-13) -> NT-15
```

## Deployment Flow (Summary)

Full detail lives in `09_Deployment.md`; the architecture-level path:

```
Developer -> push -> GitHub
     -> CI: lint + typecheck + unit/integration tests + build
     -> Docker image build (api, web, workers) -> registry
     -> deploy (staging -> production) via IaC/manifests
     -> DB migrations run (ordered, see 03_Database_Design.md §9)
     -> health checks + smoke tests -> traffic shift
     -> observability confirms error rate / latency within SLO
```

- Build once, promote the same image across environments.
- Migrations are ordered and backward-compatible so a rollout never breaks the running version.
- Health checks gate traffic; a failed check halts the rollout.

## Architecture Decision Records (ADR Summary)

Key decisions and their rationale. Full records live under [`docs/ADR/`](ADR/).

| Decision | Choice | Reason | ADR |
|----------|--------|--------|-----|
| Database | **PostgreSQL** | JSONB for flexible snapshots, GIN/tsvector/trigram for search, and row-level security for tenant isolation — in one mature engine. | [ADR-001](ADR/ADR-001-postgresql-transactional-database.md) |
| Architecture style | **Modular monolith** | Fast to ship an MVP; module boundaries let high-load modules be extracted into services later without a schema redesign. | [ADR-002](ADR/ADR-002-modular-monolith.md) |
| Core domain entity | **Questions** | Papers are projections of selected questions, not the unit of record. | [ADR-003](ADR/ADR-003-questions-as-core-domain-entity.md) |
| Deletion & audit | **Soft delete + append-only audit** | History survives; BR-11 auditability is structural rather than procedural. | [ADR-004](ADR/ADR-004-soft-deletes-and-append-only-audit.md) |
| AI workflow | **Retrieval-first with validation gates** | AI fills gaps only; every generated item passes an ordered gate before it can enter a paper. | [ADR-005](ADR/ADR-005-retrieval-first-ai-with-validation-gates.md) |
| Backend framework | **FastAPI (Python)** | Async-native (Starlette + asyncio) matches a workload dominated by concurrent I/O waits — retrieval fan-out, generation polling, SSE, long AI calls. Brings SQLAlchemy + Alembic and Celery with it. | [ADR-006](ADR/ADR-006-fastapi-over-nestjs.md) |
| Vector search | **Deferred** — metadata filter + tsvector + pg_trgm at MVP | After the hard filter the candidate set is small enough that lexical ranking matches ANN. pgvector is adopted on a measured paraphrase-recall gap. | [ADR-007](ADR/ADR-007-defer-pgvector-retrieval.md) |
| AI provider | **Gemini API only**, behind `LlmProvider` | One provider, tiered by task (Pro for generation, Flash for classification). The adapter keeps a future swap localised. | [ADR-008](ADR/ADR-008-gemini-as-sole-ai-provider.md) |
| Assembler | **Constraint solver + feasibility gate** | Greedy fill silently fails on thin corpora. The solver returns a valid paper or an honest "infeasible + why". | [ADR-009](ADR/ADR-009-constraint-solver-assembler.md) |
| AI correctness | **Execute-and-verify** | Structured output guarantees shape, not truth. Numeric answers are executed; MCQs are asserted single-correct. LLM-as-judge is advisory only. | [ADR-010](ADR/ADR-010-execute-and-verify-ai-correctness.md) |
| RBAC | **3 roles, extendable enum** | `teacher`, `coaching_admin`, `super_admin` as a constrained text column; `hod`/`reviewer`/`viewer` reserved. | [ADR-011](ADR/ADR-011-three-roles-extendable-enum.md) |
| Deployment | **Compose → ECS Fargate at MVP** | Two services and one database do not justify a Kubernetes control plane. K8s manifests are written and validated but not adopted until scale evidence. | [ADR-013](ADR/ADR-013-ecs-fargate-before-kubernetes.md) |
| Frontend framework | **Next.js (App Router)** | SSR + routing + streaming progress; one client for all roles. Unchanged by the backend decision. | — |
| Queue / cache | **Celery + Redis** | One dependency serves task queues, cache, rate limits, and pub/sub progress; Celery is the mature Python-native task queue. | [ADR-006](ADR/ADR-006-fastapi-over-nestjs.md) |
| File handling | **Direct-to-storage signed URLs** | Large uploads/exports never pass through the API tier. | — |

## Cross-Cutting Concerns

| Concern | Mechanism |
|---------|-----------|
| Tenant isolation | `org_id` predicate in every query + PostgreSQL row-level security |
| Authorization | Guard chain (auth → tenant → role) + query-level scope narrowing |
| Async work | Redis queues with bounded retries, backoff, and dead-letter handling |
| Real-time progress | Worker → Redis pub/sub → FastAPI → Next.js (SSE) → browser |
| Idempotency | Job keys and content hashing so retries never duplicate data |
| Provenance | Source + prompt version recorded on every question (BR-4, AI-8) |
| Audit | Append-only audit log on every mutation and access-control change (BR-11) |
| Metering | Usage events on generation, AI questions, OCR pages, and exports (BR-10) |
| Caching | Redis for hot, org-keyed reads; invalidated on domain events |
| Observability | Request ids propagated into jobs; queue depth and failure rate on the platform dashboard (FR-8.2) |
| Swappability | AI provider and Retrieval Engine behind interfaces; modules extractable into services without schema change |

## Related Documents

- `01_Product_Requirements.md` — requirements, business rules, AI rules (AI-1…AI-12), error catalogue (EH-1…EH-18), notifications (NT-1…NT-15)
- `03_Database_Design.md` — schema, statuses, enums, indexes
- `05_Textbook_Ingestion.md` — ingestion and extraction detail
- `06_AI_Architecture.md` — retrieval, generation, validation, prompt versioning
- `07_Backend_Architecture.md` — module responsibilities
- `08_Frontend_Architecture.md` — screens and client architecture
- `09_Deployment.md` — environments, scaling, operations
