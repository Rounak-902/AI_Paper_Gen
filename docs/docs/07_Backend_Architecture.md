# 07 Backend Architecture

## 1. Purpose

This document defines the backend architecture for the AI-powered Question Paper Generation SaaS platform. The backend is the single source of business logic, tenant isolation, authentication, authorization, AI orchestration, question management, and paper generation.

Every mutation, every query, every background job, and every AI call flows through the backend. The frontend never touches the database. External services never bypass the API. Workers reuse the same repository layer. This document specifies how.

This document should be read alongside `02_System_Architecture.md` (module boundaries, flows), `03_Database_Design.md` (schema), `06_AI_Architecture.md` (AI internals), and `01_Product_Requirements.md` (business rules, error catalogue).

---

## 2. Backend Principles

These principles are enforced through code conventions, guards, and review — not aspirational goals.

### 2.1 Modular Monolith

The backend is a single **FastAPI (Python)** application with strict module boundaries. Each module owns its tables, its rules, and its public service API. Modules communicate through injected services and domain events — never by reaching into another module's tables. This gives us the development speed of a monolith with the boundary discipline of microservices.

FastAPI was chosen over NestJS because this workload is dominated by concurrent I/O waits — retrieval fan-out, generation polling, SSE progress streams, and long-lived AI provider calls — and FastAPI's async-native design (Starlette + asyncio, native `async def` handlers) maps directly onto that. See [ADR-006](ADR/ADR-006-fastapi-over-nestjs.md).

### 2.2 Domain-Driven Design (Lightweight)

Each module represents a bounded context. Domain logic lives in services, not in routers or repositories. Modules own their vocabulary: "blueprint" means something specific in the Papers module, "scope" means something specific in Auth. Cross-module concepts are negotiated through Pydantic schemas in `packages/contracts`.

### 2.3 Thin Routers, Fat Services

Routers handle HTTP concerns only: declare the request/response models, resolve dependencies, call the service, return the result. A route handler is typically 3-5 lines. Business logic, validation, orchestration, and side effects all live in the service layer.

### 2.4 Repository Pattern

Data access is isolated behind repository classes. Services never write raw SQL or touch the SQLAlchemy session directly. This centralizes tenant scoping (`org_id` predicate), soft-delete filters, pagination, and optimistic locking in one layer.

### 2.5 Dependency Injection via `Depends()`

FastAPI's `Depends()` resolves every collaborator: the database session, the current user, the org context, the resolved role, repositories, and services. Dependencies are declared in the signature, which makes the graph explicit at every call site and makes overriding a dependency in tests a one-line `app.dependency_overrides` entry. No global singletons, no import cycles.

### 2.5.1 Async All the Way Down

Route handlers, repositories, and provider adapters are `async def`. Any genuinely blocking call — PDF rendering, sympy execution, an OCR library without an async client — runs in a worker process or a thread pool executor, never inline on the event loop. A single blocking call in a handler stalls every concurrent request on that process, which is the one way this design can be silently ruined.

### 2.6 Event-Driven Internally

After a successful database commit, modules emit domain events. Notifications, metering, analytics, cache invalidation, and embedding triggers subscribe to these events. This keeps the write path clean and the side effects decoupled.

### 2.7 Queue-Based Background Jobs

Heavy operations — OCR, AI generation, PDF export, embedding computation — are enqueued to **Celery** (Redis broker + result backend) and processed by worker processes. The API never blocks on these operations. Workers are stateless, horizontally scalable, and share the same repository layer as the API.

### 2.8 No Business Logic Inside Routers

Routers are the HTTP boundary. They declare the request model, resolve dependencies, call the service, and return. They never contain conditional logic, database queries, or side effects. If a route handler has an `if` statement, it belongs in the service.

### 2.9 Every Request Tenant-Scoped

Every authenticated request resolves an organization context from the JWT. Every database query carries `org_id`. PostgreSQL RLS provides a second line of defense. A missed application-level check cannot leak cross-tenant data.

### 2.10 Validation First, Fail Fast

Invalid input is rejected at the edge by the Pydantic request model before it reaches business logic. Business rule violations are caught in the service layer before database writes. Database constraints (unique, check, foreign key) are the last line of defense, never the first.

Request models are declared with `model_config = ConfigDict(extra="forbid")`. An unknown field is a
**400, not a silently ignored key** — this is the mass-assignment defence, and "forbid" is stricter and
easier to audit than stripping. The server never trusts a client-supplied `org_id`, `role`, `status`,
`created_by`, or `scope`; those are always taken from the resolved request context.

### 2.11 Separate Process, Separate Log Stream

The backend (`backend/`) and the frontend (`frontend/`) are **physically separate workspace roots**
([ADR-015](ADR/ADR-015-workspace-root-split.md)). The API and the Celery workers are Python processes
within `backend/`; the Next.js app is a Node process in `frontend/`. They are built from different
images, run as different services, and write structured JSON to their own stdout. A developer can read
the backend’s logs without the frontend’s interleaved into them (`docker compose logs -f api`).
Nothing about the backend runs inside the frontend’s process.

---

## 3. Folder Structure

The project uses **three top-level workspace roots** ([ADR-015](ADR/ADR-015-workspace-root-split.md)):
`backend/` (Python — API + workers), `frontend/` (Node — Next.js), and `shared/` (non-code-execution
assets). The backend is one Python workspace with its own `pyproject.toml` and venv; the API and
workers are sub-packages within it, not separate top-level directories.

```
backend/                               # Python workspace root (own pyproject.toml, own venv)
├── src/
│   ├── main.py                        # create_app(): routers, middleware, exception handlers, lifespan
│   ├── bootstrap/
│   │   ├── security.py                # security headers, CORS allow-list, body cap, rate limit
│   │   ├── observability.py           # structlog JSON to stdout, OpenTelemetry, /metrics
│   │   └── lifespan.py                # startup/shutdown: engine, Redis pool, graceful drain on SIGTERM
│   │
│   ├── common/                        # Cross-cutting concerns shared by all modules
│   │   ├── deps.py                    # get_db, get_current_user, get_org_context, require_roles
│   │   ├── middleware/                # request-id, timing, audit, response envelope
│   │   ├── errors.py                  # exception handlers -> one error envelope with ehRef
│   │   ├── pagination.py              # shared page/cursor dependency
│   │   └── mappers/                   # ORM entity -> response model, explicit field ALLOWLIST
│   │
│   ├── modules/                       # Business modules — one per bounded context
│   │   ├── auth/                      # Login, JWT, refresh, password, sessions
│   │   ├── tenancy/                   # Organizations, branches, memberships, invitations
│   │   ├── curriculum/                # Boards, classes, subjects, chapters, outcomes, versions
│   │   ├── programs/                  # Programs, program_subjects, program_teachers
│   │   ├── questions/                 # CRUD, versioning, import, dedup, approval status
│   │   ├── textbooks/                 # Upload, ingestion orchestration, asset management
│   │   ├── retrieval/                 # Candidate selection pipeline (read-only)
│   │   ├── ai/                        # Prompt management, Gemini adapter, validation
│   │   ├── papers/                    # Blueprint, assembly, versions, generation orchestration
│   │   ├── templates/                 # Layout + branding definitions
│   │   ├── approvals/                 # Review workflow, separation of duties
│   │   ├── exports/                   # PDF/DOCX render orchestration
│   │   ├── billing/                   # Subscriptions, plan enforcement
│   │   ├── usage/                     # Metering, quota checks
│   │   ├── analytics/                 # Daily rollups, dashboards
│   │   ├── notifications/             # Event subscribers, channel fan-out
│   │   └── audit/                     # Append-only audit log
│   │
│   ├── workers/                       # Celery worker entry points — ZERO HTTP surface
│   │   ├── celery_app.py              # Celery application, queues, routing, beat schedule
│   │   └── jobs/
│   │       ├── ocr/                   # OCR / extraction worker
│   │       ├── generation/            # Paper generation worker (Celery chords per slot)
│   │       ├── export/                # PDF/DOCX render worker
│   │       ├── embedding/             # Embedding computation worker (inert until ADR-007 fires)
│   │       ├── notification/          # Async notification delivery
│   │       ├── cleanup/               # Orphan cleanup, expired session reaping
│   │       └── analytics/             # Daily rollup aggregation
│   │
│   └── infra/                         # Infrastructure wiring
│       ├── db.py                      # async engine, sessionmaker, SET LOCAL app.org_id per request
│       ├── redis.py                   # Redis client, cache helpers
│       ├── storage.py                 # Object storage client, signed URL generation
│       └── queue.py                   # Celery app + task signatures (producer side)
│
├── packages/
│   ├── config/                        # Pydantic Settings — SERVER ONLY, raises on invalid boot config
│   ├── db/                            # SQLAlchemy models, Alembic migrations, RLS policies, seeds
│   ├── domain/                        # Pure rules: BR-1…BR-11, marks math, the solver. ZERO I/O.
│   ├── prompts/                       # Versioned prompt templates — never leaves the backend
│   ├── events/                        # Event names + payload models
│   └── testing/                       # Factories, RLS harness, Testcontainers fixtures
│
├── pyproject.toml
└── .env                               # Server-only secrets; never shipped to the browser

frontend/                              # Node workspace root (own package.json, own lockfile)
└── (see doc 08)                       # Separate app, separate process, separate image, separate log stream

shared/                                # Cross-cutting non-code-execution assets
├── contracts/                         # OpenAPI spec, Pydantic/Zod request/response schemas
├── prompts/                           # Prompt text from packages/prompts (git-versioned)
└── docs/                              # Shared documentation
```

### 3.1 Why the split is physical, not procedural

`backend/` is Python; `frontend/` is Node. They are **separate workspace roots** with separate
lockfiles, separate dependency trees, and separate build contexts. This matters more than tidiness:

| Property | Consequence |
|---|---|
| Separate workspace roots | A physically separate build context prevents a server-only constant from ever becoming reachable from client code, which a shared workspace root cannot structurally guarantee. |
| Separate env files | `frontend/` has no `DATABASE_URL`, no `GEMINI_API_KEY`, no JWT signing key to leak. |
| Separate processes | Each writes to its own stdout; backend logs are readable in isolation (§19.4). |
| Separate images and scaling | The API scales on request latency; workers scale on queue depth. |
| Trade-off | Two lockfiles/dependency trees to keep in sync via `shared/` contracts, not shared imports. |

`packages/domain` being pure and I/O-free is what makes the assembler testable — property-based tests
over randomized question pools run in milliseconds with no database.

**Rule:** `backend/src/workers/` imports the same repositories and services as the API in `backend/src/`.
There is exactly one implementation of retrieval, AI orchestration, and assembly. A second copy in the
worker is the failure mode this layout exists to prevent.

### Why each module exists

| Module | Reason |
|--------|--------|
| `auth` | Isolates authentication concerns (JWT, refresh, password hashing) from business modules |
| `tenancy` | Owns organization lifecycle, branch management, membership CRUD, and invitation flows |
| `curriculum` | Encapsulates the board→class→subject→chapter→outcome hierarchy and versioning |
| `programs` | Manages teacher-to-subject assignments that drive scope filtering |
| `questions` | Central question bank with versioning, import, deduplication, and status management |
| `textbooks` | Upload orchestration, OCR job management, and asset lifecycle |
| `retrieval` | Read-only candidate selection pipeline — keeps search logic separate from CRUD |
| `ai` | Prompt versioning, LLM provider adapters, validation — isolates AI complexity |
| `papers` | Blueprint capture, generation orchestration, paper versioning, assembly |
| `templates` | Reusable layout/branding — kept separate so templates evolve independently |
| `approvals` | Review workflow with separation-of-duties enforcement |
| `exports` | PDF/DOCX rendering orchestration and signed URL management |
| `billing` | Subscription plans, seats, plan-limit enforcement |
| `usage` | Metering (papers, AI tokens, OCR pages, exports) — decoupled from billing |
| `analytics` | Daily rollups, org/platform dashboards — read-heavy, no mutations |
| `notifications` | Event-driven fan-out across channels — decoupled from business modules |
| `audit` | Append-only log — written by interceptors, never by business services |

---

## 4. Module Responsibilities

This section documents every module's responsibilities, owned tables, exposed API, dependencies, and emitted events.

---

### 4.1 Auth Module

**Responsible for:**
- Email/password login with rate limiting and brute-force protection
- JWT access token issuance (short-lived, ~15 min)
- Refresh token rotation (long-lived, server-side, single-use)
- Password hashing (Argon2id with per-user salt)
- Password reset (single-use expiring token via email)
- Session management and revocation (Redis-backed)
- Token validation and user resolution (`get_current_user`)

**Owns tables:** Session state in Redis (not a SQL table)

**Exposes:**
- `POST /auth/login` — authenticate and receive tokens
- `POST /auth/logout` — revoke session
- `POST /auth/refresh` — rotate tokens
- `POST /auth/password-reset` — request password reset
- `POST /auth/password-reset/confirm` — confirm password reset

**Dependencies:** Tenancy module (user lookup, membership resolution)

**Emits:** `user.logged_in`, `user.logged_out`, `user.password_reset`

---

### 4.2 Tenancy Module

**Responsible for:**
- Organization CRUD and lifecycle management
- Branch management within organizations
- User account management (profile, status)
- Membership management (user ↔ org ↔ branch ↔ role)
- Invitation flow (create, accept, expire)
- Organization context resolution (`get_org_context`)
- Active organization switching for multi-org users

**Owns tables:** `organizations`, `branches`, `users`, `memberships`

**Exposes:**
- `POST /orgs` — create organization (Super Admin)
- `GET /orgs` — list user's organizations
- `PATCH /orgs/:id` — update organization settings
- `POST /orgs/:id/branches` — create branch
- `GET /orgs/:id/branches` — list branches
- `POST /orgs/:id/invitations` — invite user
- `PATCH /memberships/:id` — change role
- `GET /auth/me` — current user profile with memberships

**Dependencies:** Auth module (password hashing for new users)

**Emits:** `membership.created`, `membership.role_changed`, `invitation.sent`, `org.settings_updated`

---

### 4.3 Curriculum Module

**Responsible for:**
- Board / class / subject / chapter / learning outcome hierarchy CRUD
- Curriculum version management (versioned syllabus snapshots)
- Alignment lookups used by retrieval and generation
- Global vs. org-override curriculum management

**Owns tables:** `boards`, `classes`, `subjects`, `chapters`, `learning_outcomes`, `curriculum_versions`

**Exposes:**
- `GET /boards` — list boards
- `GET /classes?boardId=` — list classes for a board
- `GET /subjects?classId=` — list subjects for a class
- `GET /chapters?subjectId=` — list chapters for a subject
- `GET /outcomes?chapterId=` — list learning outcomes
- `GET /curriculum-versions?boardId=` — list versions
- `POST / PATCH` — Super Admin for global, Coaching Admin for org overrides

**Dependencies:** Auth & Tenancy (scope)

**Emits:** `curriculum.version_published`

---

### 4.4 Programs Module

**Responsible for:**
- Program definitions (exam targets, academic year, status)
- Subject-to-program mapping
- Teacher-to-program assignments (drives teacher scope filtering)

**Owns tables:** `programs`, `program_subjects`, `program_teachers`

**Exposes:**
- `POST /programs` — create program
- `GET /programs` — list programs (org-scoped)
- `PATCH /programs/:id` — update program
- `POST /programs/:id/subjects` — assign subjects
- `POST /programs/:id/teachers` — assign teachers

**Dependencies:** Tenancy, Curriculum

**Emits:** `program.created`, `program.teacher_assigned`

---

### 4.5 Questions Module

**Responsible for:**
- Question CRUD with full versioning and lineage tracking
- Structured import from files and textbook assets
- Content-hash deduplication on insert
- Status transitions (draft → approved, approved → new version)
- Embedding trigger on create/version
- Search (metadata + full-text + semantic via Retrieval)

**Owns tables:** `question_bank`, `question_embeddings`

**Exposes:**
- `POST /questions` — create question
- `GET /questions` — list/search questions (paginated, filterable)
- `GET /questions/:id` — single question with version history
- `PATCH /questions/:id` — edit (draft: in-place; approved: new version)
- `DELETE /questions/:id` — soft delete
- `POST /questions/import` — bulk import from file
- `POST /questions/:id/approve` — approve question
- `POST /questions/:id/reject` — reject question
- `GET /questions/:id/versions` — version history

**Dependencies:** Curriculum, Textbooks (extracted assets), AI (embedding), Tenancy

**Emits:** `question.created`, `question.approved`, `question.versioned`, `embedding.requested`

---

### 4.6 Textbooks Module

**Responsible for:**
- Upload handling (signed URL generation, completion callback)
- Ingestion orchestration (enqueue OCR job, track status)
- Asset management (extracted paragraphs, exercises, diagrams)
- Chapter mapping management
- Reprocessing support

**Owns tables:** `textbooks`, `textbook_assets`

**Exposes:**
- `POST /textbooks` — request upload (returns signed PUT URL)
- `POST /textbooks/:id/complete` — confirm upload complete
- `GET /textbooks` — list textbooks (org-scoped)
- `GET /textbooks/:id` — textbook status and metadata
- `POST /textbooks/:id/reprocess` — trigger re-ingestion
- `GET /textbooks/:id/assets` — list extracted assets

**Dependencies:** Curriculum (chapter mapping), Storage, Queue

**Emits:** `textbook.uploaded`, `textbook.parsed`, `textbook.embedded`, `textbook.failed`

---

### 4.7 Retrieval Module

**Responsible for:**
- Candidate selection pipeline (metadata filter → vector search → FTS → ranking)
- Hybrid ranking with configurable weights
- Deduplication (content hash + embedding similarity)
- Coverage optimization across blueprint slots
- Question search for the Question Bank screen

**Owns tables:** None (read-only over `question_bank`, `question_embeddings`, `textbook_assets`)

**Exposes:**
- Internal: `RetrievalService.selectCandidates(slot)` — used by Papers module
- `POST /questions/search` — advanced search for Question Bank UI

**Dependencies:** Questions, Textbooks, Curriculum

**Emits:** None (synchronous, stateless)

---

### 4.8 AI Module

**Responsible for:**
- Prompt template management and versioning
- Provider-agnostic LLM calls via adapter interface
- Response parsing (JSON extraction from LLM output)
- Question validation pipeline (structure, curriculum, difficulty, Bloom, similarity)
- Embedding computation (via provider adapter)
- Quality scoring
- Cost tracking per call

**Owns tables:** `prompt_versions`

**Exposes:**
- Internal: `AiService.generate(...)`, `AiService.validate(...)`, `AiService.embed(...)`
- `POST /prompt-versions` — create candidate (Super Admin)
- `POST /prompt-versions/:id/promote` — promote to active (Super Admin)
- `POST /prompt-versions/:id/retire` — retire version (Super Admin)
- `GET /prompt-versions` — list versions

**Dependencies:** Retrieval (grounding), Curriculum

**Emits:** `prompt.version_promoted`, `prompt.version_retired`

---

### 4.9 Papers Module

**Responsible for:**
- Blueprint capture, validation, and snapshotting
- Generation orchestration (retrieval → AI gap-fill → assembly)
- Paper versioning (each edit creates a new version)
- Coverage/marks reconciliation
- Submission for approval

**Owns tables:** `generated_papers`, `paper_versions`

**Exposes:**
- `POST /papers/generate` — submit blueprint, enqueue generation
- `GET /papers` — list papers (org-scoped, paginated)
- `GET /papers/:id` — paper with current version, provenance, coverage report
- `PATCH /papers/:id` — edit paper (creates new version)
- `POST /papers/:id/submit` — submit for approval
- `GET /papers/:id/versions` — version history

**Dependencies:** Retrieval, AI, Questions, Curriculum, Billing (quota), Usage, Templates

**Emits:** `paper.generation_requested`, `paper.generated`, `paper.submitted`

---

### 4.10 Templates Module

**Responsible for:**
- Reusable layout/branding definitions (header, footer, sections, fonts)
- Template lifecycle (draft → active → archived)
- Preview rendering with sample data

**Owns tables:** `templates`

**Exposes:**
- `POST /templates` — create template
- `GET /templates` — list templates (org-scoped)
- `PATCH /templates/:id` — update template
- `POST /templates/:id/archive` — archive template

**Dependencies:** Tenancy

**Emits:** `template.saved`

---

### 4.11 Approvals Module

**Responsible for:**
- Review workflow (submitted → approved/rejected)
- Separation-of-duties enforcement (author cannot be sole approver)
- Comment capture on approval/rejection
- Unlocking export on approval

**Owns tables:** `approvals`

**Exposes:**
- `POST /papers/:id/approve` — approve paper
- `POST /papers/:id/reject` — reject paper with comment
- `GET /papers/:id/approvals` — approval history

**Dependencies:** Papers, Tenancy

**Emits:** `paper.approved`, `paper.rejected`

---

### 4.12 Exports Module

**Responsible for:**
- PDF/DOCX rendering orchestration
- Export job enqueueing and status tracking
- Signed download URL generation
- Answer key inclusion control

**Owns tables:** None (renders `paper_versions`; artifacts in object storage)

**Exposes:**
- `POST /papers/:id/export` — request export
- `GET /exports/:id` — export status + download URL

**Dependencies:** Papers, Templates, Storage, Queue, Usage

**Emits:** `export.requested`, `export.ready`, `export.failed`

---

### 4.13 Billing Module

**Responsible for:**
- Subscription plan management (plan, seats, period)
- Plan-limit enforcement (quota checks before metered actions)
- Provider reference tracking (Stripe/Razorpay future integration)

**Owns tables:** `subscriptions`

**Exposes:**
- `GET /subscriptions` — current subscription
- `POST /subscriptions` — create subscription (Super Admin)
- `PATCH /subscriptions/:id` — update plan/seats

**Dependencies:** Tenancy

**Emits:** `plan.limit_reached`

---

### 4.14 Usage Module

**Responsible for:**
- Metering: papers generated, AI tokens consumed, OCR pages processed, exports rendered
- Quota counters (checked before metered actions)
- Usage reporting for dashboards

**Owns tables:** `usage`

**Exposes:**
- `GET /usage` — usage report (org-scoped)
- Internal: `UsageService.checkQuota(org, metric)`, `UsageService.record(org, metric, qty)`

**Dependencies:** Tenancy, Billing (plan limits)

**Emits:** `usage.recorded`

---

### 4.15 Analytics Module

**Responsible for:**
- Daily rollup aggregation (papers, AI questions, retrieved questions, difficulty, coverage)
- Organization and platform dashboard queries
- Rebuildable from source data

**Owns tables:** `analytics_daily`

**Exposes:**
- `GET /analytics/org` — organization dashboard data
- `GET /analytics/platform` — platform-wide data (Super Admin)

**Dependencies:** Reads across domains via rollup jobs

**Emits:** None

---

### 4.16 Notifications Module

**Responsible for:**
- Subscribe to domain events and resolve recipients (by role + scope)
- Template-based message composition
- Channel fan-out (in-app, email; SMS/push future)
- Read-state management
- Per-user notification preferences

**Owns tables:** Notification log (delivery + read state)

**Exposes:**
- `GET /notifications` — list notifications (paginated)
- `POST /notifications/:id/read` — mark as read

**Dependencies:** All emitting modules (via event subscriptions)

**Emits:** None

---

### 4.17 Audit Module

**Responsible for:**
- Append-only record of every mutation and access-control change
- Written by an interceptor on every mutating handler, not by business services
- Queryable by Coaching Admin (org-scoped) and Super Admin (cross-tenant)

**Owns tables:** `audit_logs`

**Exposes:**
- `GET /audit` — audit trail (paginated, filterable)

**Dependencies:** None (written by interceptor)

**Emits:** None

---

## 5. Dependency Graph

Module dependencies flow top-down. Circular dependencies are forbidden; the `Depends()` graph makes a cycle an immediate import error rather than a runtime surprise.

```
                        Auth & Tenancy
                     (foundation - no deps)
                              |
              +-------+-------+-------+--------+
              |       |       |       |        |
              v       v       v       v        v
         Curriculum Programs Billing Templates Audit
              |       |       |
              +---+---+       |
              |   |           |
              v   v           v
          Questions  Usage  Notifications
              |       |
              +---+---+
              |       |
              v       v
          Retrieval  AI
              |       |
              +---+---+
                  |
                  v
              Papers
                  |
                  v
             Approvals
                  |
                  v
              Exports
```

### Dependency Rules

| Rule | Rationale |
|------|-----------|
| **No circular imports** | If A depends on B, B must never depend on A. Use events for reverse communication. |
| **Foundation modules have no business deps** | Auth & Tenancy are injected everywhere but depend on nothing else. |
| **Retrieval is read-only** | It queries Questions and Textbooks but never writes — no mutation coupling. |
| **AI is stateless** | It receives context, returns results. It never opens a DB connection. Persistence is the caller's job. |
| **Papers orchestrates** | It's the highest-level business module, coordinating Retrieval, AI, Questions, and Usage. |
| **Audit is passive** | Written by interceptors, never called by business services. |
| **Notifications subscribe** | They react to events, never imported by emitting modules. |

### Anti-Patterns to Avoid

```
WRONG:                          RIGHT:
Questions -> Papers             Papers -> Questions
  (creates circular dep)          (one-way)

WRONG:                          RIGHT:
AI -> Database (direct)         AI -> returns result
  (leaks concerns)               Papers -> persists result

WRONG:                          RIGHT:
Controller -> Repository        Controller -> Service -> Repository
  (skips business logic)          (proper layering)
```

---

## 6. Request Lifecycle

Every HTTP request follows the same path through the backend. No shortcuts, no bypasses.

```
Browser
  |
  v
Next.js (attach session token + org context)
  |
  v
FastAPI edge (HTTPS terminated upstream at the LB / ingress)
  |
  v
Middleware stack (outermost first)
  |  - Security headers (CSP, nosniff, frame options, HSTS)
  |  - CORS (strict allow-list, credentials explicit)
  |  - Body size cap
  |  - Correlation ID (X-Request-Id generation/propagation, bound to structlog contextvars)
  |  - Rate limiter (Redis-backed, per-user + per-org)
  |  - Timing
  |
  v
Dependency chain  (declared per-route via Depends(); deny by default)
  |  1. get_current_user   : validate JWT -> resolve user            -> 401
  |  2. get_org_context    : resolve active org -> verify membership -> 403
  |  3. require_roles(...)  : check memberships.role against the route's
  |                          declared roles                          -> 403
  |  4. get_scope          : narrow for teacher (assigned programs/subjects)
  |  5. get_db             : open transaction, SET LOCAL app.org_id
  |
  v
Pydantic request model
  |  - Field types, constraints, and coercion
  |  - extra="forbid": unknown fields are rejected, not stripped
  |  - Reject: 422 with per-field error details
  |
  v
Route handler (router)
  |  - Signature declares the model + resolved dependencies
  |  - Call service method
  |  - Return the response model (middleware wraps in the envelope)
  |
  v
Service Layer
  |  - Business logic, rules, orchestration
  |  - Transaction management
  |  - Call repository for data access
  |  - Emit domain events after commit
  |
  v
Repository Layer
  |  - SQLAlchemy async queries with org_id predicate
  |  - Soft-delete filters (WHERE deleted_at IS NULL)
  |  - Pagination, filtering, sorting
  |  - Optimistic locking (row_version check)
  |
  v
PostgreSQL (+ RLS second line of defense)
  |
  v
Response
  |  - Envelope middleware wraps in the success shape
  |  - Audit middleware logs the mutation
  |  - Timing middleware records latency
  |
  v
Next.js -> Browser
```

**Ordering note.** The dependency chain runs *before* the request body is validated, so an
unauthenticated or wrong-role caller never has their payload parsed. `get_db` opens the transaction
last, after the org context is known, so `SET LOCAL app.org_id` is always issued with a resolved tenant
and never with a stale one.

---

## 7. Authentication Architecture

### 7.1 JWT Token Strategy

```
Login
  |
  v
Verify credentials (email + Argon2id hash comparison)
  |
  v
Issue tokens:
  +-- Access Token (JWT, signed, ~15 min TTL)
  |     Payload: { userId, activeOrgId, role, iat, exp }
  |
  +-- Refresh Token (opaque UUID, ~7 days TTL)
        Stored server-side in Redis with session metadata
        Single-use: each refresh issues new pair + invalidates old
```

### 7.2 Password Hashing

- **Algorithm:** Argon2id (memory-hard, side-channel resistant)
- **Salt:** Per-user, randomly generated
- **Parameters:** Tuned for ~250ms on target hardware
- **Storage:** `users.password_hash` (includes algorithm, salt, and hash)
- **Null hash:** OAuth/SSO users have `password_hash = NULL` (password login disabled)

### 7.3 Refresh Token Rotation

```
Client sends refresh token
  |
  v
Redis lookup: token exists and not expired?
  |
  +-- No -> 401, force re-login
  |
  +-- Yes -> Issue new access + refresh token
              Invalidate old refresh token
              Store new refresh token in Redis
              If old token is reused after invalidation:
                -> Reuse detection -> revoke entire session family
```

### 7.4 Organization Context Resolution

```
JWT contains activeOrgId
  |
  v
get_org_context:
  1. Load user's memberships
  2. Verify membership exists for activeOrgId
  3. Resolve role from membership
  4. Attach { user, org, role, scope } to request context
  5. All downstream queries use req.org.id as org_id filter
```

### 7.5 Dependencies

The guard chain is a chain of FastAPI dependencies. Each one is a small callable that either returns a
resolved value or raises — there is no way to reach a handler having skipped one, because the handler
cannot be called without its declared dependencies being resolved first.

| Dependency | Purpose |
|-----------|---------|
| `get_current_user` | Validates the JWT, resolves the user, rejects expired/invalid/revoked tokens (401) |
| `get_org_context` | Resolves the active organization, verifies an active membership exists (403) |
| `require_roles(*roles)` | Dependency factory; checks `memberships.role` against the route's declared roles (403) |
| `get_scope` | Resolves the teacher's program/subject scope for query narrowing |
| `get_db` | Yields an `AsyncSession` inside a transaction with `SET LOCAL app.org_id` already applied |

**Deny by default.** The router is mounted with `dependencies=[Depends(get_current_user), Depends(get_org_context)]`
so authentication and tenancy apply to every route in it. Public routes (login, health) live on a
separate router that does not carry those dependencies — opting *out* is explicit and visible in one
place, rather than every protected route having to remember to opt *in*.

```python
router = APIRouter(
    prefix="/papers",
    tags=["papers"],
    dependencies=[Depends(get_current_user), Depends(get_org_context)],
)

@router.post("/generate", status_code=202, response_model=GenerateAccepted)
async def generate_paper(
    body: GeneratePaperRequest,
    user: AuthUser = Depends(get_current_user),
    org: OrgContext = Depends(get_org_context),
    _: None = Depends(require_roles("teacher", "coaching_admin")),
    svc: PapersService = Depends(get_papers_service),
) -> GenerateAccepted:
    return await svc.generate(user, org, body)
```

**Testing note.** `app.dependency_overrides[get_current_user] = fake_user` swaps identity for a test
without touching the route, which is the practical payoff of resolving authorization through the
dependency graph rather than through decorators read by a framework container.

---

## 8. Authorization

### 8.1 Role-Based Access Control (RBAC)

`memberships.role` carries **exactly three values** at MVP — `teacher`, `coaching_admin`, `super_admin`
— stored as a constrained text column rather than a native PostgreSQL ENUM so that adding a role later
is a CHECK-constraint migration, not an `ALTER TYPE` (ADR-011). The full vocabulary, including the
reserved-but-unimplemented values `hod`, `reviewer`, and `viewer`, is defined once in
`03_Database_Design.md` §7.2. This table is the authorization view of that vocabulary.

| Capability | Teacher | Coaching Admin | Super Admin |
|-----------|---------|----------------|-------------|
| View own papers | Yes | Yes | Yes (cross-tenant) |
| Generate papers | Yes | Yes | No (operational) |
| Edit own questions | Yes | Yes | Yes |
| Approve papers | Restricted | Yes | Yes |
| Manage users | No | Yes (own org) | Yes (any org) |
| Manage programs | No | Yes | Yes |
| Manage templates | No | Yes | Yes |
| Manage billing | No | View only | Yes |
| Manage prompt versions | No | No | Yes |
| Manage curriculum (global) | No | No | Yes |
| View audit logs | No | Yes (own org) | Yes (cross-tenant) |
| View platform analytics | No | No | Yes |

### 8.2 Scope Restrictions

**Teacher scope narrowing:**
- Teachers see only questions, papers, and textbooks within their assigned programs and subjects
- Scope is resolved from `program_teachers` → `program_subjects` → subject/chapter filtering
- Applied automatically by the Scope Filter in the guard chain

**Branch scoping:**
- `memberships.branch_id` narrows a member to one branch; null means org-wide access
- Where a resource carries `branch_id` (`programs`, and anything derived from them), the scope filter
  adds the branch predicate alongside `org_id`

**Ownership rules:**
- Teachers can edit/delete only their own draft questions (`created_by = user.id`)
- Approved questions require a new version (anyone with access can version)
- Paper author cannot be the sole approver (separation of duties, BR-6)

**Note on "reviewer".** Approval authority at MVP belongs to `coaching_admin`, constrained by BR-6.
There is no separate `reviewer` role; separation of duties is enforced behaviourally (author ≠ approver)
rather than by a distinct role. `reviewer` is a reserved value for when approve-only access without
authoring rights becomes a real requirement.

### 8.3 Implementation

```python
# Service checks business rules; the router already proved the caller's role.
class PapersService:
    def __init__(
        self,
        papers_repo: PapersRepository,
        retrieval: RetrievalService,
        ai: AiService,
        usage: UsageService,
        events: EventBus,
    ) -> None:
        self._papers = papers_repo
        self._retrieval = retrieval
        self._ai = ai
        self._usage = usage
        self._events = events

    async def generate(
        self, user: AuthUser, org: OrgContext, body: GeneratePaperRequest
    ) -> GenerateAccepted:
        self._validate_blueprint(body.blueprint)              # BR-3, CN-4, CN-5, CN-12
        await self._usage.check_quota(org.id, "papers")        # BR-10
        gate = await self._assembler.feasibility_gate(org, body.blueprint)  # ADR-009
        if gate.outcome == "infeasible":
            raise BusinessRuleError(code="BLUEPRINT_INFEASIBLE", eh_ref="EH-9", detail=gate.reason)
        paper = await self._papers.create_with_quota(org, user, body)       # one transaction
        await enqueue_generation.kiq(paper_id=paper.id, org_id=org.id)      # after commit
        await self._events.emit("paper.generation_requested", paper_id=paper.id)
        return GenerateAccepted(paper_id=paper.id, job_id=..., status="assembling")
```

Role checks live in the router's `require_roles(...)` dependency; **business** rules — quota,
blueprint reconciliation, feasibility, separation of duties — live in the service. Neither layer
duplicates the other.

---

## 9. Validation Strategy

Validation is layered. Each layer catches different classes of errors. No single layer is trusted alone.

```
Layer 1: Client-Side (advisory only, never trusted)
  |  - Form validation, type checks, required fields
  |  - Purpose: fast feedback to user
  |  - Trust level: ZERO
  |
  v
Layer 2: Pydantic request-model validation (at the route boundary)
  |  - Typed fields with constraints: UUID, Literal enums, ge/le, min_length
  |  - extra="forbid": unknown properties are REJECTED, not stripped
  |    (mass-assignment prevention, and stricter than stripping)
  |  - Returns 422 with per-field errors
  |  - Trust level: structural correctness
  |
  v
Layer 3: Business Rule Validation (Service Layer)
  |  - Blueprint marks reconciliation (BR-3)
  |  - Plan quota check (BR-10)
  |  - Separation of duties (BR-6)
  |  - Curriculum version alignment
  |  - Question ownership verification
  |  - Returns 400/403/409 with business error codes
  |
  v
Layer 4: Database Constraints (PostgreSQL)
  |  - Foreign key constraints (referential integrity)
  |  - Unique constraints (email, content_hash)
  |  - Check constraints (enum values, positive numbers)
  |  - NOT NULL constraints
  |  - row_version for optimistic locking (409 on conflict)
  |  - RLS for tenant isolation
  |  - Trust level: absolute (last line of defense)
```

---

## 10. Error Handling

### 10.1 Global Exception Handlers

A single set of `@app.exception_handler(...)` registrations catches every error class and transforms it
into the standard error envelope. No module formats its own HTTP error. Handlers are registered for the
domain exception base class, `RequestValidationError`, `HTTPException`, and `Exception` (the catch-all,
which logs with the request id and returns an opaque 500 with an `ehRef` — never a stack trace).

```json
{
  "success": false,
  "error": {
    "code": "BLUEPRINT_MARKS_MISMATCH",
    "message": "Section marks (38) do not equal total marks (40).",
    "details": [{"field": "sections[2].marks", "issue": "..."}],
    "ehRef": "EH-8"
  },
  "meta": {"requestId": "req_abc123"}
}
```

### 10.2 Error Categories

| Category | HTTP Status | Examples |
|----------|-------------|---------|
| **Validation errors** | 422 | Missing required field, invalid UUID, marks out of range |
| **Authentication errors** | 401 | Expired token, invalid credentials, revoked session |
| **Authorization errors** | 403 | Insufficient role, wrong org, ownership violation |
| **Not found** | 404 | Question not found, paper not found |
| **Conflict** | 409 | Optimistic lock failure, duplicate content_hash |
| **Business rule errors** | 400 | Marks mismatch, quota exceeded, invalid state transition |
| **Rate limit** | 429 | Per-user or per-org rate limit exceeded |
| **AI errors** | 502 | Gemini timeout, invalid response, safety block, all retries exhausted |
| **Worker errors** | 500 | OCR failure, export render failure |
| **Database errors** | 500 | Connection failure, constraint violation (unexpected) |

### 10.3 Error Codes

Every error carries a machine-readable `code` string. Error codes map to the error catalogue (EH-1 through EH-18) in `01_Product_Requirements.md`.

### 10.4 Retry Strategy

| Error Type | Strategy |
|-----------|----------|
| Gemini timeout | Retry with exponential backoff, max 3 attempts, then fall through to retrieval or record an unmet slot (AI-3) |
| Gemini rate limit | Respect the `Retry-After` header, re-queue with delay |
| Database connection | Connection pool retry, circuit breaker |
| Optimistic lock conflict | Re-read and retry once |
| Queue job failure | Bounded retries with backoff → dead letter queue |

---

## 11. Service Layer

### 11.1 Service Responsibilities

Services are the heart of the backend. They contain all business logic.

**Services SHOULD:**

| Responsibility | Example |
|---------------|---------|
| Business logic | Blueprint validation, marks reconciliation |
| Orchestration | Coordinate retrieval → AI → assembly |
| Transactions | Wrap multi-table mutations atomically |
| Domain events | Emit `paper.generated` after successful commit |
| Authorization checks | Verify ownership, separation of duties |
| Quota checks | Verify plan limits before metered actions |

**Services SHOULD NOT:**

| Anti-pattern | Why |
|-------------|-----|
| HTTP concerns | No `Response` objects, no header manipulation — that's the router |
| Raw SQL / direct session use | Use the repository — centralizes scoping and soft-delete |
| Prompt formatting | That's the AI module's Prompt Builder |
| Direct cache reads/writes | Use repository cache wrappers or the cache service |
| Blocking calls | Nothing synchronous and slow on the event loop — offload to a worker or a thread pool |
| Logging business data | Use structured logging with the correlation ID |

### 11.2 Service Structure

```python
class PapersService:
    def __init__(
        self,
        papers_repo: PapersRepository,
        usage: UsageService,
        events: EventBus,
    ) -> None:
        self._papers = papers_repo
        self._usage = usage
        self._events = events

    async def generate(
        self, user: AuthUser, org: OrgContext, body: GeneratePaperRequest
    ) -> Paper:
        # 1. Business validation
        self._validate_blueprint(body.blueprint)

        # 2. Quota check
        await self._usage.check_quota(org.id, "papers")

        # 3. One transaction: create paper + initial version + reserve quota + audit
        async with self._papers.transaction() as tx:
            paper = await tx.create_paper(org=org, created_by=user.id, blueprint=body.blueprint)
            await tx.create_initial_version(paper.id)
            await tx.record_usage(org.id, "papers", 1)
            await tx.audit(org.id, user.id, "paper.create", entity_id=paper.id)

        # 4. Enqueue AFTER commit — a job must never reference a rolled-back row
        generate_paper.delay(paper_id=str(paper.id), org_id=str(org.id))

        # 5. Emit AFTER commit
        await self._events.emit("paper.generation_requested", paper_id=paper.id)

        return paper
```

Services are constructed by a `Depends()` provider (`get_papers_service`) rather than by a container
scanning decorators, so the dependency graph is readable at the call site and trivially overridable in
tests.

---

## 12. Repository Layer

### 12.1 Repository Pattern with SQLAlchemy

Every module has a repository class that wraps SQLAlchemy async queries. The repository enforces tenant
scoping, soft-delete filtering, and optimistic locking so no service can forget them:

```python
class QuestionsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def find_many(
        self, org_id: UUID, filters: QuestionFilters, page: Pagination
    ) -> Sequence[QuestionBank]:
        stmt = (
            select(QuestionBank)
            .where(
                QuestionBank.org_id == org_id,          # tenant isolation
                QuestionBank.deleted_at.is_(None),      # soft-delete filter
                QuestionBank.is_current.is_(True),      # only current versions
                *self._build_filters(filters),          # whitelisted filter keys only
            )
            .order_by(*self._build_sort(page.sort))     # whitelisted sort fields only
            .offset(page.offset)
            .limit(page.limit)
        )
        return (await self._s.scalars(stmt)).all()

    async def update(
        self, id: UUID, org_id: UUID, data: dict, expected_version: int
    ) -> None:
        stmt = (
            update(QuestionBank)
            .where(
                QuestionBank.id == id,
                QuestionBank.org_id == org_id,
                QuestionBank.row_version == expected_version,   # optimistic lock
                QuestionBank.deleted_at.is_(None),
            )
            .values(
                **data,
                row_version=QuestionBank.row_version + 1,
                updated_at=func.now(),
            )
        )
        result = await self._s.execute(stmt)
        if result.rowcount == 0:
            raise ConflictError(code="CONCURRENT_MODIFICATION", eh_ref="EH-18")

    async def soft_delete(self, id: UUID, org_id: UUID) -> None:
        await self._s.execute(
            update(QuestionBank)
            .where(QuestionBank.id == id, QuestionBank.org_id == org_id)
            .values(deleted_at=func.now())
        )
```

**Tenant context is set once, per transaction, by `get_db`:**

```python
async def get_db(org: OrgContext = Depends(get_org_context)) -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        async with session.begin():
            # SET LOCAL — scoped to THIS transaction only.
            # A session-level SET would leak the tenant to the next request that
            # borrows this backend from PgBouncer in transaction pooling mode.
            await session.execute(
                text("SET LOCAL app.org_id = :org_id"), {"org_id": str(org.id)}
            )
            yield session
```

This is the PgBouncer footgun the whole tenancy model turns on, and it is covered by a test that
asserts `current_setting('app.org_id')` does not survive a pooled checkout.

### 12.2 Repository Responsibilities

| Concern | Implementation |
|---------|---------------|
| **Tenant isolation** | Every query includes `org_id` in the WHERE clause |
| **Soft delete** | All reads filter `deleted_at IS NULL`; deletes set `deleted_at` |
| **Pagination** | Offset-based (`skip/take`) with cursor support for large sets |
| **Filtering** | Whitelisted filter keys mapped to SQLAlchemy `where` clauses; unknown keys rejected |
| **Sorting** | Whitelisted sort fields; default sort per entity |
| **Full-text search** | PostgreSQL `tsvector` + GIN index via raw query for `q` parameter |
| **Bulk operations** | Batch inserts via `createMany`; batch updates via transactions |
| **Optimistic locking** | `row_version` check in UPDATE WHERE clause; 409 on mismatch |

---

## 13. Database Transactions

### 13.1 Transaction Rules

| Rule | Rationale |
|------|-----------|
| Keep transactions short | No LLM calls, no HTTP calls, no file I/O inside a transaction |
| Enqueue after commit | Jobs must never reference data that a rollback erased |
| Emit events after commit | Subscribers must never react to work that was rolled back |
| One writer per entity | Avoid multi-service transactions where possible |
| Idempotent post-commit | If the enqueue/event fires twice, the subscriber handles it safely |

### 13.2 Atomic Operations

```python
# Paper generation setup: all-or-nothing.
# The session is already inside a transaction opened by get_db, with
# SET LOCAL app.org_id applied — so RLS is active for every statement below.
async def create_paper_with_quota(
    self, org: OrgContext, user: AuthUser, data: CreatePaperData
) -> GeneratedPaper:
    paper = GeneratedPaper(org_id=org.id, created_by=user.id, **data.model_dump())
    self._s.add(paper)
    await self._s.flush()                       # need paper.id below

    self._s.add(PaperVersion(paper_id=paper.id, label="v1", content={}))
    self._s.add(Usage(org_id=org.id, user_id=user.id, metric="papers", qty=1))
    self._s.add(AuditLog(org_id=org.id, actor_id=user.id,
                         action="paper.create", entity="generated_papers",
                         entity_id=paper.id))
    return paper
    # Commit happens when get_db's `session.begin()` block exits.
    # The Celery enqueue and the domain event fire AFTER that, in the service.
```

### 13.3 Concurrency Control

| Strategy | Use Case |
|----------|----------|
| **Optimistic locking** | Question edits, paper edits, template updates — `row_version` column checked on UPDATE |
| **Database serializable isolation** | Quota checks — prevent double-spending under concurrent requests |
| **Idempotency keys** | Generation and export requests — client sends `Idempotency-Key` header to prevent double-enqueue |
| **Content hash uniqueness** | Question imports — `content_hash` unique index prevents exact duplicate inserts |

### 13.4 Deadlock Prevention

- Acquire locks in consistent order (parent before child: paper → version → questions)
- Keep transaction scope minimal (no multi-module transactions)
- Use advisory locks for operations that touch multiple rows of the same entity (batch import)

---

## 14. Background Jobs

All heavy operations run as **Celery tasks** consumed from Redis-backed queues by `backend/src/workers/`. Tasks are stateless, idempotent, and independently scalable. Every task is registered with `acks_late=True` so an ungraceful worker death returns the task to the queue rather than losing it, and every task carries an idempotency key so the redelivery that follows cannot duplicate data.

### 14.1 OCR Worker

| Property | Value |
|----------|-------|
| **Queue** | `ocr` (Celery) |
| **Input** | `{ textbookId, orgId }` |
| **Output** | Extracted `textbook_assets` + status transition to `parsed` → `embedded` |
| **Steps** | Fetch file → split pages → OCR per page → structural parsing → chapter mapping → embed assets |
| **Retry** | 3 attempts with exponential backoff |
| **Failure** | Status → `failed`; partial assets retained; teacher notified (NT-2) |
| **Timeout** | 10 min per job; 30s per page |
| **Concurrency** | 2 per worker instance (CPU/memory bound) |

### 14.2 Generation Worker

| Property | Value |
|----------|-------|
| **Queue** | `generation` (Celery) |
| **Input** | `{ paperId, orgId }` |
| **Output** | Filled `paper_versions.content` + stored questions |
| **Steps** | Feasibility gate → solve → per-slot Celery chord (retrieve → gap-fill via AI → validate → **execute-and-verify** → dedup) → paper-level checks → save version. A slot that fails verification retries only that slot. |
| **Retry** | 2 attempts per slot (AI-bounded); a crash mid-paper resumes from the last completed slot rather than restarting |
| **Failure** | Partial paper with coverage gaps; teacher notified with guidance |
| **Timeout** | 5 min per job |
| **Concurrency** | 5 per worker (LLM rate-limit aware) |

### 14.3 Embedding Worker

| Property | Value |
|----------|-------|
| **Queue** | `embedding` (Celery) — inert until pgvector is adopted (ADR-007) |
| **Input** | `{ questionId }` or `{ batch: questionId[] }` |
| **Output** | `question_embeddings` record(s) with `is_current = true` |
| **Steps** | Load stem → call embedding provider → store vector → mark previous as not current |
| **Retry** | 3 attempts |
| **Failure** | Question stored without embedding; queued for retry |
| **Timeout** | 30s per question, 5 min per batch |
| **Concurrency** | 10 per worker (IO-bound, not CPU) |

### 14.4 Export Worker

| Property | Value |
|----------|-------|
| **Queue** | `export` (Celery) |
| **Input** | `{ paperId, versionId, templateId, includeAnswerKey }` |
| **Output** | PDF/DOCX stored in object storage; `export.ready` event |
| **Steps** | Load paper version → load template → render → store artifact → generate signed URL |
| **Retry** | 3 attempts |
| **Failure** | `export.failed` event → teacher notified (NT-10) |
| **Timeout** | 2 min per job |
| **Concurrency** | 3 per worker (memory-bound rendering) |

### 14.5 Notification Worker

| Property | Value |
|----------|-------|
| **Queue** | `notifications` (Celery) |
| **Input** | `{ event, recipients, template, channel }` |
| **Output** | Delivered notification + delivery record |
| **Steps** | Resolve recipients → render template → deliver via channel (in-app/email) → record delivery |
| **Retry** | 3 attempts (email transient failures) |
| **Failure** | Log failed delivery; do not retry indefinitely |
| **Timeout** | 30s per notification |

### 14.6 Cleanup Worker

| Property | Value |
|----------|-------|
| **Queue** | `cleanup` (Celery Beat schedule) |
| **Input** | None (runs on schedule) |
| **Output** | Reaped orphans, expired sessions, old exports |
| **Steps** | Delete orphaned upload records → purge expired sessions → lifecycle-expire old exports |
| **Schedule** | Every 6 hours |

### 14.7 Analytics Worker

| Property | Value |
|----------|-------|
| **Queue** | `analytics` (Celery Beat schedule) |
| **Input** | `{ date }` |
| **Output** | `analytics_daily` rows upserted for the date |
| **Steps** | Aggregate papers, AI questions, retrieved questions, avg difficulty per org per day |
| **Schedule** | Daily at 02:00 UTC |
| **Idempotent** | Upsert on (org_id, day) — safe to re-run |

---

## 15. Event-Driven Architecture

Even inside a modular monolith, domain events keep modules decoupled. Business modules emit events after a successful commit; subscribers react without the emitter knowing who is listening.

### 15.1 Event Flow Example

```
Question Imported (question.created)
  |
  +---> Embedding Worker: compute and store embedding
  |
  +---> Analytics: increment daily question count
  |
  +---> Audit: log import action
  |
  +---> Notification: notify relevant users (NT-12)
  |
  +---> Cache: invalidate question search results
```

### 15.2 Event Catalogue

| Event | Emitted By | Subscribers |
|-------|-----------|-------------|
| `textbook.uploaded` | Textbooks | OCR trigger |
| `textbook.parsed` | OCR Worker | Notifications (NT-1), Questions |
| `textbook.failed` | OCR Worker | Notifications (NT-2) |
| `question.created` | Questions | Embedding, Analytics, Audit, Cache |
| `question.approved` | Questions | Cache invalidation, Analytics |
| `question.versioned` | Questions | Embedding (re-embed), Audit |
| `embedding.requested` | Questions | Embedding Worker |
| `paper.generation_requested` | Papers | Usage (reserve), Audit |
| `paper.generated` | Generation Worker | Notifications (NT-3), Usage (finalize) |
| `paper.submitted` | Papers | Notifications (NT-4 to reviewers) |
| `paper.approved` | Approvals | Notifications (NT-5), Analytics |
| `paper.rejected` | Approvals | Notifications (NT-6) |
| `export.requested` | Exports | Usage, Audit |
| `export.ready` | Export Worker | Notifications (NT-9) |
| `export.failed` | Export Worker | Notifications (NT-10) |
| `prompt.version_promoted` | AI | Cache invalidation |
| `plan.limit_reached` | Usage | Notifications (NT-15), Billing |
| `usage.recorded` | Usage | Analytics |
| `org.settings_updated` | Tenancy | Cache invalidation |
| `template.saved` | Templates | Cache invalidation |

### 15.3 Event Rules

- Events are emitted **after commit** — subscribers never react to rolled-back work.
- Subscribers are **idempotent** — the same event delivered twice must not double-send or double-meter.
- Events carry **ids + context**, not whole entities — subscribers load what they need through the owning module's service.
- Event delivery is **at-least-once** — subscribers must handle duplicates gracefully.

---

## 16. Caching

### 16.1 Cache Infrastructure

Redis serves as the caching layer. All cache keys are org-scoped to enforce tenant isolation at the cache level.

### 16.2 Cache Key Convention

```
cache:{orgId}:{entity}:{identifier}
cache:{orgId}:questions:search:{hashOfFilters}
cache:global:curriculum:{boardId}:classes
```

### 16.3 Cached Data

| Data | Strategy | TTL | Invalidation Event |
|------|----------|-----|--------------------|
| User session / token state | Write-through | Session length | `user.logged_out`, role change |
| Organization settings | Lazy (read-through) | 15 min | `org.settings_updated` |
| Curriculum hierarchy | Lazy | 60 min | `curriculum.version_published` |
| Subject / chapter lists | Lazy | 30 min | Curriculum change |
| Template layouts | Lazy | 15 min | `template.saved` |
| Question search results | Lazy | 5 min | `question.created`, `question.approved` |
| Active prompt versions | Write-through | Until change | `prompt.version_promoted` |
| Plan/quota counters | Write-through | Period | `usage.recorded` |

### 16.4 Cache Invalidation Strategy

- **Event-driven invalidation:** Domain events trigger targeted cache key deletion. No blanket cache flush.
- **Pattern-based deletion:** When a question is approved, delete all `cache:{orgId}:questions:search:*` keys.
- **TTL safety net:** Even without explicit invalidation, cached data expires within the TTL.
- **Never cache sensitive data:** Passwords, tokens, and API keys are never cached (sessions use Redis directly, not the cache layer).

---

## 17. AI Integration

> Full AI architecture is documented in `06_AI_Architecture.md`. This section covers only the backend integration boundary.

### 17.1 Integration Flow

```
Backend (Papers Service)
  |
  v
AI Module (AiService facade)
  |
  +---> Prompt Builder: construct grounded prompt
  |       (curriculum + exemplars + textbook snippets + constraints)
  |
  +---> LlmProvider adapter (GeminiAdapter): send prompt, receive response
  |       (provider-agnostic interface; Gemini is the only implementation)
  |
  +---> Response Parser: extract JSON question objects
  |
  +---> Validation Engine: validate structure, curriculum, difficulty
  |
  +---> Correctness Verifier: execute numeric solutions, assert single-correct MCQ
  |
  +---> Similarity Checker: dedup against question bank
  |
  v
Return validated questions to Papers Service
  |
  v
Papers Service persists to database
  (AI module never opens a DB connection)
```

### 17.2 Key Boundaries

| Rule | Enforcement |
|------|-------------|
| AI module is stateless | It receives context in, returns results out. No DB connection. |
| Persistence is the caller's job | Papers service stores questions and records provenance. |
| Token tracking flows through Usage | AI module reports tokens; Usage module meters them. |
| Prompt versions are managed via API | Super Admin CRUD through the AI module's endpoints; promote/rollback is one UPDATE, no redeploy. |

---

## 18. File Management

### 18.1 Upload Flow

```
Client                    FastAPI                   Object Storage
  |                         |                           |
  | 1. POST /textbooks      |                           |
  |   {name, type, size}    |                           |
  |------------------------>|                           |
  |                         | 2. Validate type + size   |
  |                         | 3. Generate signed PUT URL|
  |                         |-------------------------->|
  |                         |<-- signed URL ------------|
  |<-- {textbookId, url} ---|                           |
  |                         |                           |
  | 4. PUT file bytes ------|-------------------------->|
  |<-- 200 -----------------|                           |
  |                         |                           |
  | 5. POST /textbooks/:id/complete                     |
  |------------------------>| 6. Verify object exists   |
  |                         |-------------------------->|
  |                         |<-- ok --------------------|
  |                         | 7. Enqueue OCR job        |
  |<-- {jobId} -------------|                           |
```

### 18.2 Storage Architecture

| Concern | Implementation |
|---------|---------------|
| **Upload** | Client uploads directly to object storage via signed PUT URL — never through the API |
| **Storage layout** | `org/{orgId}/textbooks/{id}/raw/`, `org/{orgId}/exports/{id}/` |
| **Access** | Private bucket; all reads via short-lived signed GET URLs |
| **Metadata** | File metadata stored in `textbooks` table (file_key, status, pages) |
| **Virus scan** | Optional: scan stored object before enqueuing OCR; infected → quarantine + reject |
| **Delete strategy** | Soft delete in DB; lifecycle policy expires orphaned objects; hard delete on org removal |
| **Signed URL TTL** | Upload: 15 min; Download: 1 hour |

---

## 19. Logging

### 19.1 Logging Strategy

Structured JSON logging with **structlog**, written to **stdout**. Every log entry includes the
correlation ID for end-to-end tracing. Nothing writes to a log file — the process emits to stdout and
the platform (Docker, ECS, or Kubernetes) owns collection, rotation, and shipping. This is the
Twelve-Factor rule and it is what makes the same image behave identically in every environment.

### 19.2 Log Categories

| Category | What | Where |
|----------|------|-------|
| **Application logs** | Service-level operations, business events | API + Workers |
| **Request logs** | HTTP method, path, status, latency, user, org | API (interceptor) |
| **Audit logs** | Every mutation, role change, access-control event | `audit_logs` table (append-only) |
| **Worker logs** | Job start, progress, completion, failure | Workers |
| **AI logs** | Model, tokens, latency, prompt_version, quality scores | AI module |
| **Performance logs** | Slow queries (>500ms), slow API responses (>2s) | API + DB |

### 19.3 Correlation ID

Every request generates (or propagates) an `X-Request-Id`. This ID is:
- Attached to all log entries during the request
- Propagated into queued jobs (`requestId` in job data)
- Included in error responses (`meta.requestId`)
- Used to trace a generation request across API → queue → worker → Gemini → DB

The correlation ID is bound to a `contextvar` at the start of the request and injected into every log
line by a structlog processor, so no function has to thread it through its arguments. It is copied into
the Celery task headers on enqueue and re-bound by the worker on pickup, which is what keeps the trace
unbroken across the queue boundary.

### 19.4 Independent Log Streams

The backend is its own process with its own log stream. This is an architectural requirement, not a
convenience: a developer must be able to read what the API did without the frontend’s output
interleaved into it.

| Process | Service name | Stream |
|---|---|---|
| FastAPI API | `api` | Its own stdout |
| Celery workers | `worker-ocr`, `worker-generation`, `worker-export` | One stream each |
| Next.js frontend | `web` | Separate stdout, separate container |

Every line carries `"service": "api"` (or the worker name), so even when streams are aggregated they
remain separable by field rather than by guesswork.

**Tailing just the backend locally:**

```bash
docker compose logs -f api                      # API only
docker compose logs -f worker-generation        # one worker only
docker compose logs -f api worker-generation    # both, still labelled
```

Without Docker, `backend/` and `frontend/` run as two terminals — `uvicorn` in one, `pnpm dev` in the
other. There is no mode in which the backend runs inside the frontend's process. See
`09_Deployment.md` §6.5 for the local-development service definitions.

---

## 20. Security

### 20.1 Authentication Security

| Control | Implementation |
|---------|---------------|
| Password storage | Argon2id with per-user salt; never plaintext |
| JWT signing | RS256 or HS256 with rotatable signing key |
| Refresh tokens | Server-side (Redis), single-use, rotation with reuse detection |
| Brute force | Per-account + per-IP rate limits + lockout (Redis counters) |
| Generic failures | Never disclose whether an account exists |

### 20.2 Transport Security

| Control | Implementation |
|---------|---------------|
| HTTPS | TLS everywhere; HSTS enabled; HTTP → HTTPS redirect |
| Helmet | Security headers (CSP, X-Content-Type-Options, frame options) |
| CORS | Strict allow-list (web origin only); credentials mode explicit |
| Body size | Request body size capped (default 10MB for API, 500MB for uploads via signed URL) |

### 20.3 Rate Limiting

```
Per-user:   100 requests / minute (API)
Per-org:    1000 requests / minute (API)
Per-IP:     50 login attempts / hour
Per-org:    Plan-based generation limits / month
```

Redis-backed counters with sliding window. Exhaustion returns `429` with `Retry-After` header.

### 20.4 Injection Prevention

| Threat | Mitigation |
|--------|-----------|
| **SQL injection** | SQLAlchemy expression language with bound parameters only; no f-string or concatenated SQL, ever. Raw SQL (RLS, partitions, index DDL) lives in Alembic revisions, never in a request path. |
| **Prompt injection** | User input is injected into a structured prompt template, never concatenated into system prompts; input sanitization; output validation |
| **XSS** | React output encoding by default; CSP headers; sanitize rich text |
| **CSRF** | httpOnly + Secure + SameSite cookies; CSRF token on state-changing forms |
| **Mass assignment** | DTO whitelisting; server never trusts client-supplied orgId, role, or status |

### 20.5 File Validation

```
Client-side check (type + size)    -- advisory only, never trusted
  |
  v
Server validation
  - MIME type + magic-byte sniffing (PDF/DOCX/PNG/JPG only)
  - Size <= 500 MB, page count cap
  |
  v
Signed URL upload (private bucket)
  |
  v
Virus/malware scan on stored object
  - Infected -> quarantine + reject + notify
  - Clean -> enqueue OCR
```

### 20.6 Secrets Management

- No secrets in the repository or client bundle
- Secrets injected via environment variables or secrets manager at deploy time
- Config validated against schema at boot (`packages/config`); app refuses to start with missing/invalid config
- Database credentials, API keys, and signing keys are rotatable without code changes
- Sensitive fields (password_hash) never appear in API responses or logs

---

## 21. Performance

### 21.1 Database Performance

| Strategy | Implementation |
|----------|---------------|
| **Indexes** | Composite indexes on all common query patterns (see `03_Database_Design.md` §8) |
| **Pagination** | Offset-based with cursor support for large result sets; `pageSize` capped server-side |
| **Batch queries** | `createMany` for imports; batched embedding inserts |
| **Connection pooling** | SQLAlchemy async pool (default 10 per process); PgBouncer in transaction pooling mode for production — with the `SET LOCAL` rule in §12.1 |
| **Slow query detection** | Queries >500ms logged and alerted |

### 21.2 API Performance

| Strategy | Implementation |
|----------|---------------|
| **Redis cache** | Read-through cache for hot, slowly-changing data (curriculum, templates) |
| **Lazy loading** | Related entities loaded only when requested (include/select) |
| **Streaming** | SSE/WebSocket for real-time job progress (no polling) |
| **Response filtering** | Role-based field filtering (sensitive fields excluded) |
| **Compression** | gzip/brotli response compression for large payloads |

### 21.3 Worker Performance

| Strategy | Implementation |
|----------|---------------|
| **Queue-based** | Workers decouple arrival rate from processing rate; bursts queue, not degrade |
| **Concurrency limits** | Per-worker concurrency caps (OCR: 2, Generation: 5, Export: 3) |
| **Horizontal scaling** | Add worker replicas; queue distributes work automatically |
| **Batch processing** | Embedding worker processes batches of up to 2048 texts per API call |

---

## 22. API Standards

### 22.1 REST Naming Conventions

```
GET    /papers                    # List (paginated, filterable, sortable)
POST   /papers/generate           # Action (not plain CRUD)
GET    /papers/:id                # Single resource
PATCH  /papers/:id                # Partial update
DELETE /papers/:id                # Soft delete
POST   /papers/:id/submit         # State transition (sub-resource action)
GET    /papers/:id/versions        # Nested collection (one level max)
```

### 22.2 HTTP Status Codes

| Status | Usage |
|--------|-------|
| 200 | Successful read or update |
| 201 | Resource created |
| 202 | Accepted (async job enqueued) |
| 204 | Successful delete (no content) |
| 400 | Business rule violation |
| 401 | Authentication failure |
| 403 | Authorization failure |
| 404 | Resource not found |
| 409 | Conflict (optimistic lock, duplicate) |
| 422 | Validation error (malformed input) |
| 429 | Rate limit exceeded |
| 500 | Internal server error |
| 502 | Upstream provider failure (LLM) |

### 22.3 Response Envelope

**Success:**
```json
{
  "success": true,
  "data": { },
  "message": "Paper generated",
  "meta": {
    "requestId": "req_abc123",
    "pagination": { "page": 1, "pageSize": 20, "total": 142, "hasNext": true }
  }
}
```

**Error:**
```json
{
  "success": false,
  "error": {
    "code": "QUOTA_EXCEEDED",
    "message": "Monthly paper generation limit reached.",
    "details": [],
    "ehRef": "EH-13"
  },
  "meta": { "requestId": "req_abc123" }
}
```

### 22.4 Pagination, Filtering, Sorting

```
GET /questions?page=2&pageSize=50
    &filter[subjectId]=...&filter[difficulty]=medium
    &filter[status]=approved&filter[source]=textbook
    &q=photosynthesis
    &sort=-createdAt,marks
```

- `pageSize` capped server-side (default 20, max 100)
- Filters use a whitelisted set per resource; unknown keys rejected
- `q` triggers full-text search
- Sort: `-` prefix = descending; comma-separated for multi-sort
- Cursor-based pagination: `?cursor=...` for large sequential reads

### 22.5 API Versioning

- Version in path: `/api/v1/...`
- Breaking changes ship as `/api/v2` with deprecation window for v1
- Additive changes (new optional fields, new endpoints) stay within current version

### 22.6 API Documentation

- **OpenAPI 3.1** spec generated automatically by FastAPI from the route signatures and Pydantic models
- **Swagger UI** at `/api/docs` and ReDoc at `/api/redoc` in development and staging; both disabled in production
- DTOs serve as the single source of truth for request/response schemas

---

## 23. Configuration

### 23.1 Environment Variables

All configuration is loaded from environment variables and validated at startup by the Pydantic `Settings` model in `packages/config`. The app **refuses to start** with missing or invalid configuration — a placeholder signing key, a short secret, or an unset model ID is a boot failure, not a runtime surprise. Settings are injected via `Depends(get_settings)`, cached with `lru_cache`.

### 23.2 Configuration Categories

| Category | Examples |
|----------|---------|
| **Database** | `DATABASE_URL`, `DB_POOL_SIZE`, `DB_SSL` |
| **Redis** | `REDIS_URL`, `REDIS_TLS`, `REDIS_MAX_RETRIES` |
| **Auth** | `JWT_SECRET`, `JWT_EXPIRY`, `REFRESH_TOKEN_EXPIRY`, `ARGON_MEMORY_COST` |
| **Storage** | `STORAGE_BUCKET`, `STORAGE_REGION`, `STORAGE_ENDPOINT` |
| **AI** | `AI_PROVIDER` (=`gemini`), `GEMINI_API_KEY`, `AI_MODEL_GENERATE`, `AI_MODEL_CLASSIFY`, `AI_EMBEDDING_MODEL`, `AI_SAFETY_THRESHOLD` |
| **Queue** | `QUEUE_REDIS_URL`, `QUEUE_DEFAULT_RETRIES`, `QUEUE_DEFAULT_BACKOFF` |
| **Rate Limiting** | `RATE_LIMIT_PER_USER`, `RATE_LIMIT_PER_ORG`, `LOGIN_RATE_LIMIT` |
| **Feature Flags** | `FEATURE_AI_GENERATION`, `FEATURE_MULTILINGUAL`, `FEATURE_ADVANCED_ANALYTICS` |
| **Observability** | `LOG_LEVEL`, `SENTRY_DSN`, `OTEL_ENDPOINT` |

### 23.3 Feature Flags

Feature flags enable gradual rollout and safe experimentation:

```python
if settings.feature_ai_generation:
    ...  # AI generation path
else:
    ...  # Retrieval-only fallback: honest gaps, never a hard failure
```

Every AI- and vendor-dependent capability has a kill switch with a **defined degraded behaviour**. A
flag with no fallback path is an outage with extra steps. Turning off AI generation degrades to
retrieval-only papers with slots marked "needs manual authoring" (§18.1.1 rung 7).

Flags are environment-variable based in v1, with a future path to a feature flag service.

---

## 24. Monitoring

### 24.1 Health Checks

```
GET /health         -> { status: 'ok', uptime: ..., version: ... }   (unauthenticated, no infra detail)
GET /health/ready   -> checks: database, redis, storage, migrations applied  (readiness gate)
GET /health/live    -> process alive, event loop responsive              (liveness probe)
```

### 24.2 Metrics (Prometheus)

| Metric | Type | Description |
|--------|------|-------------|
| `http_requests_total` | Counter | Total requests by method, path, status |
| `http_request_duration_seconds` | Histogram | Request latency distribution |
| `celery_queue_depth` | Gauge | Current tasks waiting per queue — the worker autoscaling signal |
| `celery_task_duration_seconds` | Histogram | Task processing time per queue |
| `celery_task_failures_total` | Counter | Failed tasks per queue |
| `db_pool_active` | Gauge | Active database connections |
| `db_query_duration_seconds` | Histogram | Query latency distribution |
| `ai_tokens_total` | Counter | Gemini tokens consumed (in/out by model tier) |
| `ai_cost_total` | Counter | Estimated cost by model tier; labelled with `cascade_rung` so a rising share of expensive rungs alerts (see `06_AI_Architecture.md` §18.1.1) |
| `ai_latency_seconds` | Histogram | Gemini response time |
| `cache_hits_total` | Counter | Cache hit count by key pattern |
| `cache_misses_total` | Counter | Cache miss count by key pattern |

### 24.3 Dashboards (Grafana)

- **API Health:** request rate, latency p50/p95/p99, error rate, rate limit hits
- **Queue Health:** depth, processing rate, failure rate, DLQ size per queue
- **Database Health:** connection pool usage, slow queries, transaction rate
- **AI Health:** generation success rate, average tokens, cost trends, latency
- **Business Metrics:** papers generated/day, AI vs. retrieval ratio, acceptance rate

### 24.4 Alerting

| Alert | Condition | Severity |
|-------|-----------|----------|
| API error rate spike | >5% 5xx in 5 min | Critical |
| Queue DLQ growth | >10 messages in DLQ | Warning |
| Database connection exhaustion | Pool >80% utilized | Warning |
| Gemini unavailable | >3 consecutive failures | Critical |
| Slow API responses | p95 >3s for 10 min | Warning |
| Quota exhaustion | Org at 90% of plan limit | Info → Coaching Admin |

---

## 25. Deployment Considerations

### 25.1 Stateless Backend

The FastAPI application is fully stateless. Session state lives in Redis. File storage is external. This enables horizontal scaling without sticky sessions.

### 25.2 Scaling Architecture

```
Load Balancer
  |
  +---> API Instance 1 (FastAPI/uvicorn)
  +---> API Instance 2 (FastAPI/uvicorn)
  +---> API Instance N (FastAPI/uvicorn)
  |
  +---> All share:
         - PostgreSQL (connection pooled via PgBouncer)
         - Redis (shared cache + queue)
         - Object Storage (shared bucket)

Workers (independent scaling):
  +---> OCR Worker x2 (CPU-bound)
  +---> Generation Worker x5 (IO-bound, LLM rate aware)
  +---> Export Worker x2 (memory-bound)
  +---> Embedding Worker x3 (IO-bound)
```

### 25.3 Deployment Rules

| Rule | Rationale |
|------|-----------|
| **Stateless API** | No sticky sessions; any instance handles any request |
| **Shared Redis** | All instances share cache and queue state |
| **Connection pool** | PgBouncer in front of PostgreSQL; per-instance pool capped |
| **Rolling updates** | Zero-downtime deploys; health checks gate traffic |
| **Build once** | Same Docker image promoted across environments |
| **Migrations first** | Database migrations run before new code deploys (backward-compatible) |
| **Workers scale independently** | Generation workers scale on queue depth, not API traffic |

---

## 26. Future Evolution

The modular monolith is designed for extraction. Each module can become an independent service when scaling demands it.

### 26.1 Extraction Path

```
Current: FastAPI Modular Monolith
  |
  v
Phase 1: Extract hot-path modules
  +---> AI Service (independent, GPU-optimized)
  +---> OCR Service (independent, CPU-optimized)
  |
  v
Phase 2: Extract high-traffic modules
  +---> Question Service (high read volume)
  +---> Analytics Service (heavy aggregation)
  |
  v
Phase 3: Full service mesh (if needed)
  +---> Billing Service
  +---> Notification Service
  +---> Export Service
```

### 26.2 What Makes Extraction Safe

| Design Decision | Why It Helps |
|----------------|-------------|
| Module boundaries | Each module has a public service API — replace DI with HTTP/gRPC |
| Repository pattern | Data access is already isolated — each service gets its own DB |
| Event-driven communication | Events already decouple modules — switch to a message broker |
| Shared contracts | `packages/contracts` already defines the API surface |
| Stateless AI | AI module has no DB connection — already deployable independently |

---

## 27. Backend Sequence Diagrams

### 27.1 Login

```
Browser         Next.js         FastAPI (Auth)     PostgreSQL      Redis
  |                |               |                  |              |
  | 1. email+pwd   |               |                  |              |
  |--------------->|               |                  |              |
  |                | 2. POST       |                  |              |
  |                |  /auth/login  |                  |              |
  |                |-------------->|                  |              |
  |                |               | 3. Rate check ---|------------->|
  |                |               | 4. Load user ----|->            |
  |                |               | 5. Verify pwd    |              |
  |                |               | 6. Load roles ---|->            |
  |                |               | 7. Issue JWT     |              |
  |                |               | 8. Store refresh |------------->|
  |                |               | 9. Audit --------|->            |
  |                |<-- tokens ----|                  |              |
  |<-- session ----|               |                  |              |
```

### 27.2 Upload Textbook

```
Browser         Next.js         FastAPI          Storage        Redis       Worker
  |                |               |                |              |           |
  | 1. Choose file |               |                |              |           |
  |--------------->| 2. POST       |                |              |           |
  |                |  /textbooks   |                |              |           |
  |                |-------------->| 3. Validate    |              |           |
  |                |               | 4. Signed URL->|              |           |
  |                |<-- url -------|                |              |           |
  |<-- url --------|               |                |              |           |
  | 5. PUT file ---|---------------|--------------->|              |           |
  |<-- 200 --------|               |                |              |           |
  | 6. POST        |               |                |              |           |
  |  /complete --->|-------------->| 7. Verify ---->|              |           |
  |                |               | 8. Enqueue ----|------------->|           |
  |<-- jobId ------|<-- jobId -----|                |              |---------->|
  |                |               |                |              |   OCR...  |
```

### 27.3 Generate Paper

```
Teacher         Next.js         FastAPI (Papers)  PostgreSQL    Redis      Gen Worker
  |                |               |                  |            |            |
  | 1. Blueprint   |               |                  |            |            |
  |--------------->| 2. POST       |                  |            |            |
  |                |  /papers/     |                  |            |            |
  |                |  generate     |                  |            |            |
  |                |-------------->| 3. Validate      |            |            |
  |                |               | 4. Check quota   |            |            |
  |                |               | 5. TX: create -->|            |            |
  |                |               | 6. Enqueue ------|----------->|            |
  |                |<-- 202 -------|                  |            |            |
  |<-- jobId ------|               |                  |            |----------->|
  |                |               |                  |            | 7. Consume |
  |                |               |                  |            |  Retrieve  |
  |                |               |                  |            |  AI fill   |
  |                |               |                  |            |  Validate  |
  |                |               |                  |            |  Assemble  |
  |                |               |                  |<-----------|  Store     |
  |<-- SSE:done ---|<-- event -----|<-- pub/sub ------|------------|            |
```

### 27.4 Approval Flow

```
Teacher         Reviewer        FastAPI           PostgreSQL
  |                |               |                  |
  | 1. POST        |               |                  |
  |  /papers/:id/  |               |                  |
  |  submit        |               |                  |
  |--------------->|               |                  |
  |                |               | 2. Status->      |
  |                |               |   submitted ---->|
  |                |               | 3. Notify NT-4   |
  |                |<-- notify ----|                  |
  |                |               |                  |
  |                | 4. POST       |                  |
  |                |  /papers/:id/ |                  |
  |                |  approve      |                  |
  |                |-------------->| 5. Check BR-6    |
  |                |               |   (author!=      |
  |                |               |    approver)     |
  |                |               | 6. TX: insert    |
  |                |               |   approval +     |
  |                |               |   status->       |
  |                |               |   approved ----->|
  |                |<-- 200 -------|                  |
  |<-- notify NT-5 |               |                  |
```

### 27.5 Export Flow

```
User            FastAPI (Exports)  Redis         Export Worker    Storage
  |                |                  |               |              |
  | 1. POST        |                  |               |              |
  |  /papers/:id/  |                  |               |              |
  |  export        |                  |               |              |
  |--------------->| 2. Check status  |               |              |
  |                |   (approved?)    |               |              |
  |                | 3. Enqueue ----->|               |              |
  |<-- exportId ---|                  |-------------->|              |
  |                |                  |               | 4. Render    |
  |                |                  |               | 5. Store --->|
  |                |                  |<-- done ------|              |
  |<-- NT-9 -------|<-- event --------|               |              |
  |                |                  |               |              |
  | 6. GET /exports/:id               |               |              |
  |--------------->| 7. Signed URL -->|               |              |
  |<-- download ---|                  |               |              |
```

---

## 28. Backend Design Decisions

Architectural decisions that save future developers from re-litigating choices.

Full records are in [`docs/ADR/`](ADR/); the tables below are the summary a reviewer needs in place.

### 28.1 Why FastAPI? (ADR-006)

| Consideration | Decision |
|--------------|----------|
| **Concurrency profile** | The workload is dominated by concurrent I/O waits — retrieval fan-out, generation polling, SSE progress streams, long-lived AI provider calls. Starlette + asyncio with native `async def` handlers holds thousands of idle connections per process at low memory cost. |
| **Validation and contracts** | Pydantic v2 validates at the boundary and generates the OpenAPI 3.1 spec from the same models. One definition, no drift between the schema and the docs. |
| **Dependency injection** | `Depends()` makes the auth → tenancy → role → scope chain explicit in the signature and trivially overridable in tests, without a container that resolves by decorator scanning. |
| **AI/ML ecosystem** | sympy for answer verification, the OCR and document toolchain, and the Gemini SDK are all Python-native. The alternative was a two-language backend, which doubles CI, ops, and hiring for no MVP benefit. |
| **Maturity** | FastAPI/Starlette/Pydantic are widely deployed in production at scale, actively maintained, and not on any critical path that depends on an experimental library. |
| **Known failure mode** | One blocking call in an `async def` handler stalls every concurrent request on that worker process. Mitigated by §2.5.1, offloading blocking work to Celery or a thread pool, and by a latency-regression check in CI. |

### 28.2 Why PostgreSQL? (ADR-001)

| Consideration | Decision |
|--------------|----------|
| **JSONB** | Flexible schema for blueprints, options, rubrics, meta, and coverage reports |
| **Row-Level Security** | Tenant isolation enforced at the database layer as a second line of defense |
| **Full-text search** | GIN + tsvector + trigram cover MVP retrieval and dedup without Elasticsearch **or** a vector store |
| **pgvector, when needed** | Semantic search stays available in the same engine — adopted on evidence, not assumed (ADR-007) |
| **Maturity** | Battle-tested, ACID-compliant, excellent tooling, and the operational knowledge is commodity |

### 28.3 Why Redis?

| Consideration | Decision |
|--------------|----------|
| **Multi-purpose** | One dependency serves cache, the Celery broker and result backend, rate limiting, pub/sub, and session storage |
| **Performance** | Sub-millisecond reads for cache hits; org-keyed data stays fast |
| **Pub/sub** | Real-time job progress events from workers to the API to the browser |
| **Simplicity** | One less infrastructure component to manage compared to separate queue + cache systems |

### 28.4 Why pgvector (not a dedicated vector DB)?

**Prior question first: why no vector search at all yet (ADR-007).** After the hard metadata filter the
candidate set per blueprint slot is tens to low hundreds of short questions, where lexical ranking over
an exactly-filtered set matches ANN quality. Deferring removes an embedding worker, an HNSW index, a
reindex-on-model-change path, and a per-question embedding cost from the MVP. Adoption is triggered by a
measured paraphrase-recall gap in the eval suite, not by a date.

When that trigger fires, pgvector is the first step rather than a dedicated vector store:

| Consideration | Decision |
|--------------|----------|
| **Operational simplicity** | No separate Pinecone/Weaviate/Qdrant cluster to manage |
| **Transactional consistency** | Embeddings are ACID-consistent with the questions they belong to |
| **Hybrid search** | Combine vector similarity with metadata filters and full-text search in one query |
| **Scale** | HNSW handles millions of vectors; a dedicated store is warranted past roughly 5–10M |
| **Cost** | No additional SaaS cost for a vector database |
| **Index-width constraint** | HNSW caps at 2000 dimensions for `vector`, so Gemini's 3072-dim output is truncated to 1536 (`03_Database_Design.md` §5) |

### 28.5 Why Modular Monolith?

| Consideration | Decision |
|--------------|----------|
| **Speed to MVP** | One deployable, one CI pipeline, one debugging target |
| **Extractable** | Module boundaries are service boundaries — extract when scaling demands it |
| **Shared contracts** | `packages/contracts` provides one Pydantic definition per shape without API versioning overhead |
| **Lower infra cost** | No service mesh, no inter-service auth, no distributed tracing complexity |

### 28.6 Why Repository Pattern?

| Consideration | Decision |
|--------------|----------|
| **Centralized tenant scoping** | Every query goes through the repository where `org_id` is enforced |
| **Soft-delete consistency** | `deleted_at IS NULL` filter applied in one place, not scattered across services |
| **Testability** | Repositories can be mocked for unit testing services without a database |
| **Optimistic locking** | `row_version` check centralized in the update method |

### 28.7 Why Background Workers?

| Consideration | Decision |
|--------------|----------|
| **API responsiveness** | Generation (5-30s), OCR (minutes), export (seconds) would block HTTP requests |
| **Scalability** | Workers scale independently of API traffic; queue absorbs burst |
| **Resilience** | Failed jobs are retried automatically; DLQ captures permanent failures |
| **Cost control** | LLM rate limits are respected per-worker; no thundering herd |

### 28.8 Why SQLAlchemy + Alembic? (ADR-006)

| Consideration | Decision |
|--------------|----------|
| **Maturity** | SQLAlchemy has been the default Python ORM for over fifteen years. Every failure mode is documented and every operational question has been answered by someone before. |
| **Async support** | SQLAlchemy 2.x async with asyncpg matches the FastAPI concurrency model end to end |
| **Raw SQL where it matters** | RLS policies, partitions, GIN/trigram indexes, generated `tsvector` columns and (later) HNSW are hand-written in Alembic revisions. Alembic autogenerate does not model these, and pretending otherwise is how a schema silently diverges. |
| **Shared across API and workers** | `packages/db` is the single source of truth; both processes import the same models and repositories |
| **Migrations** | Ordered, reviewable, reversible revisions; expand-migrate-contract keeps every migration compatible with the previously deployed version |
| **Alternative considered** | Prisma's Python client — rejected: it is markedly less mature than the TypeScript client, and raw-SQL escape hatches for RLS and partitioning are exactly what this schema depends on most |
| **Fallback if it breaks** | The repository layer is the only code that touches the session, so the ORM surface area is small and replaceable; the schema itself is plain PostgreSQL and portable regardless. |

### 28.9 Why Celery? (ADR-006)

| Consideration | Decision |
|--------------|----------|
| **Maturity** | The default Python task queue for over a decade, with a long production track record at scale |
| **Redis broker** | Reuses Redis, already present for cache, rate limits, and pub/sub — no new infrastructure |
| **Multi-step orchestration** | Chains, groups, and chords express the generation pipeline as typed steps with per-step retry, so a slot that fails verification retries just that slot and a crash after 28 of 31 slots resumes rather than restarts |
| **Operational surface** | Per-queue routing, visibility timeouts, rate limits, and Flower/Prometheus exporters for queue-depth autoscaling |
| **Alternative considered** | `arq` — smaller, natively async, and appealing, but a much smaller operational track record. Rejected for the critical path on the maturity requirement. |
| **Known failure modes** | Redis is not a durable broker: an unacked task can be lost on an ungraceful broker failure. Mitigated by `acks_late=True`, idempotency keys on every task, durable status on the entity row (so state is recoverable from PostgreSQL, not from the queue), and a DLQ with replay. |
| **Fallback if it breaks** | Task signatures are thin wrappers over service methods; swapping the runner does not touch business logic. |

---

## Related Documents

- `01_Product_Requirements.md` — requirements, business rules (BR-1…BR-11), AI rules (AI-1…AI-12), error catalogue (EH-1…EH-18), notifications (NT-1…NT-15)
- `02_System_Architecture.md` — module boundaries, system flows, deployment
- `03_Database_Design.md` — schema, indexes, migrations, RLS
- `05_Textbook_Ingestion.md` — OCR pipeline detail
- `06_AI_Architecture.md` — retrieval, generation, validation, prompt versioning

## Related Decisions

| ADR | Bearing on this document |
|---|---|
| [ADR-002](ADR/ADR-002-modular-monolith.md) | §2.1 modular monolith, §5 dependency graph, §26 extraction path |
| [ADR-004](ADR/ADR-004-soft-deletes-and-append-only-audit.md) | §12 repository soft-delete and audit rules |
| [ADR-005](ADR/ADR-005-retrieval-first-ai-with-validation-gates.md) | §17 AI integration boundary |
| [ADR-006](ADR/ADR-006-fastapi-over-nestjs.md) | The whole framework surface: routers, Pydantic, `Depends()`, SQLAlchemy, Alembic, Celery |
| [ADR-007](ADR/ADR-007-defer-pgvector-retrieval.md) | §28.4, the inert embedding queue |
| [ADR-008](ADR/ADR-008-gemini-as-sole-ai-provider.md) | §17 boundary, §23 AI configuration |
| [ADR-009](ADR/ADR-009-constraint-solver-assembler.md) | §14.2 generation worker steps |
| [ADR-010](ADR/ADR-010-execute-and-verify-ai-correctness.md) | Verification inside the generation pipeline |
| [ADR-011](ADR/ADR-011-three-roles-extendable-enum.md) | §8 authorization |
- `08_Frontend_Architecture.md` — screens and client architecture
- `09_Deployment.md` — environments, CI/CD, scaling, operations
