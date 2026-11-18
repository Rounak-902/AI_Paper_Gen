# AI Question Paper Generation Platform — Project Summary
*(Consolidated from prior working sessions, most weight on the session of 2026-07-31)*

Working directory: `d:\ai paper`

> ## ⚠️ Status: the seven conflicts in §Phase 0 are now RESOLVED
>
> This document was written while the seven architectural conflicts below were still open. They have
> since been decided and the decisions are recorded in [`docs/ADR/`](ADR/) and propagated through docs
> 01–09. **Where this document disagrees with an ADR, the ADR wins.**
>
> Two of the resolutions are *overrides* rather than a choice between the options originally on the
> table, and they supersede every recommendation in this file:
>
> - **Backend framework: FastAPI (Python)**, not NestJS — [ADR-006](ADR/ADR-006-fastapi-over-nestjs.md).
>   This carries SQLAlchemy + Alembic (not Prisma) and Celery (not BullMQ) with it.
> - **AI provider: the Gemini API**, not the Claude family — [ADR-008](ADR/ADR-008-gemini-as-sole-ai-provider.md).
>
> The **security audit in §2 remains current** — it describes the state of the actual codebase, which
> this reconciliation pass did not touch. Finding #14 (schema drift) is resolved *in the documentation*
> by [ADR-012](ADR/ADR-012-schema-drift-reconciliation.md); the corresponding migrations still need to
> be written. Every other finding is still open.
>
> See [`CHANGELOG.md`](CHANGELOG.md) for the full record of what changed and why.

---

## Status of today's session

Today's session (`62b68a94`) is the most important one — it read all 12 docs (~19.6k lines),
audited the whole codebase, produced a full technical roadmap, a security audit, a new
production architecture + tech stack, and then you gave it the final pivot instruction
(minimize LLM dependency, DB as source of truth). It confirmed the plan, wrote a 9-item
TODO list, and was about to create a git safety-net commit before moving any files —
then it hit repeated `403 pre-consume quota failed` errors and stalled.

**Nothing has been moved, created, or edited on disk yet.** The next action, when you
resume, is: create the git snapshot commit, then execute the backend/frontend/shared
restructure.

---

# 1. Technical Roadmap — AI Question Paper Generation Platform

## Where the project actually stands

| Layer | State |
|---|---|
| `apps/api` | NestJS shell — `HealthController`, `PrismaModule`, **empty** `AuthModule`/`TenancyModule` |
| `apps/web` | Next.js shell — `layout.tsx`, `page.tsx`, no App Router groups |
| `packages/db` | Prisma with **3 of 26** models (`Organization`, `User`, `Membership`), no migrations |
| `packages/contracts` | Stub `index.ts` |
| `infra/` | `docker-compose.yml` (Postgres + Redis) only |
| Missing entirely | `apps/workers`, `packages/config`, `packages/events`, `packages/ui`, RLS, tests, CI, Dockerfiles, k8s manifests |

Schema drift already exists vs `docs/03_Database_Design.md`: `User.fullName` vs `name`,
missing `User.status`, `Membership.status` undocumented but missing `branch_id`, `Role`
has 3 values while docs specify 6 (`super_admin`, `org_admin`, `hod`, `teacher`,
`reviewer`, `viewer`). Fix in Phase 1 before building on top of it.

**Assumption for estimates:** 5–6 engineers (2 backend, 1.5 frontend, 1 AI/ML, 0.5 DevOps).

## Phase 0 — Decisions & de-risking spikes (2 weeks)

Seven contradictions existed between `Blueprint_AI_Question_Paper_Generator.md` and docs 02–09. **All
seven are now resolved.** The table below records what the conflict was and how it was decided.

| # | Conflict | Blueprint said | Docs 02/03/05/06/09 said | **RESOLUTION** | ADR |
|---|---|---|---|---|---|
| 1 | Vector search | Defer pgvector; MVP = metadata filter + tsvector + pg_trgm | pgvector/HNSW on critical path | **Defer.** `RetrievalService` returns scored candidates so ANN slots in behind it. Adoption trigger: a measured paraphrase-recall gap in the eval suite. | [ADR-007](ADR/ADR-007-defer-pgvector-retrieval.md) |
| 2 | Assembler | Real constraint solver + feasibility gate + compromise report ("the core IP") | Greedy fill: required→flexible→report gaps | **Build the solver.** Feasibility gate returns feasible / feasible-with-compromise / infeasible, with a documented relaxation order. | [ADR-009](ADR/ADR-009-constraint-solver-assembler.md) |
| 3 | OCR engine | Mathpix / vision-LLM → LaTeX, primary path | Cloud Vision / Tesseract; formula recognition = future | **Math OCR primary.** Mathpix first, Gemini vision fallback, plain OCR for prose — all behind `DocumentExtractor`. Phase-0 spike picks the default. | [ADR-014](ADR/ADR-014-math-first-ocr.md) |
| 4 | AI correctness | Execute numeric solutions (sympy/sandbox); assert exactly-one-correct MCQ | LLM-as-judge; flag for review | **Execute-and-verify**, as a hard gate. LLM-as-judge is advisory only and never blocks. | [ADR-010](ADR/ADR-010-execute-and-verify-ai-correctness.md) |
| 5 | Deployment | ECS Fargate at MVP, no K8s until scale demands it | Full Kubernetes in all envs | **Compose → Fargate for MVP/Beta.** K8s manifests written and CI-validated, adopted only on a named trigger. | [ADR-013](ADR/ADR-013-ecs-fargate-before-kubernetes.md) |
| 6 | Models | Claude family: `claude-haiku-4-5` / `claude-sonnet-5` | `text-embedding-3-small`, GPT-4o, stale Claude names | **OVERRIDDEN — neither.** The **Gemini API** is the sole provider: Pro tier for generation, Flash tier for classification, Gemini embeddings. Model IDs live in config. | [ADR-008](ADR/ADR-008-gemini-as-sole-ai-provider.md) |
| 7 | RBAC | 6 roles incl. hod, reviewer, viewer | 3 roles (matches current schema) | **3 roles** — `teacher`, `coaching_admin`, `super_admin` — as a constrained text column, not a native ENUM. `hod`/`reviewer`/`viewer` reserved. | [ADR-011](ADR/ADR-011-three-roles-extendable-enum.md) |

**Additionally overridden, outside the seven:** the backend framework is **FastAPI (Python)**, not
NestJS, bringing SQLAlchemy + Alembic and Celery with it ([ADR-006](ADR/ADR-006-fastapi-over-nestjs.md)).
Every "NestJS", "Prisma", and "BullMQ" reference elsewhere in *this* document predates that decision and
is retained only as a record of the codebase's state at audit time.

**Spikes (parallel, timeboxed):**

| Spike | Question it answers | Exit artifact |
|---|---|---|
| S-1 Math OCR | Mathpix vs vision-LLM vs Tesseract on 3 real STEM textbooks | LaTeX + diagram quality scorecard; provider decision |
| S-2 Assembler | Can a solver satisfy pattern × difficulty × source-mix × chapter coverage jointly from thin pools? | Prototype over synthetic pools; relaxation order documented |
| S-3 Answer verification | Can worked solutions be executed safely and reliably? | sympy/sandbox harness, pass rate on fixture set |
| S-4 Retrieval | Is FTS+trigram sufficient after the hard metadata filter? | Recall baseline on seeded corpus → decides ① empirically |

**Exit:** 7 ADRs merged, 4 spike reports, decisions frozen.

## Phase 1 — Foundation: schema, tenancy, auth (3 weeks)

**Data (`packages/db`)** — implement `03_Database_Design.md` §9 migration order 1–18, all 26 tables:
Extensions (pgcrypto, citext, pg_trgm, vector if ①=adopt) → tenancy → curriculum → curriculum_versions
→ programs → question_bank (lineage: root_question_id, previous_version_id, version_number, is_current)
→ embeddings → textbooks/assets → templates → papers → versions/approvals → subscriptions, usage,
audit, analytics, prompts → indexes/constraints/RLS/partitions/seeds → backfills.

Raw SQL migrations needed for RLS, partitions, GIN/trigram/HNSW indexes, generated `tsvector` columns.

**Backend:**
- `packages/config` — Zod env schema, boot-time validation, refuses to start on invalid config
- `packages/contracts` — DTOs, response/error envelopes, CN-1…CN-22 constants
- `packages/events` — event names + payload types (~20 events, doc 07 §15.2)
- `common/` — AuthGuard → TenantGuard → RolesGuard → scope filter; validation pipe (whitelist mode); global exception filter → error envelope with `ehRef`; response/request-ID/audit/timing interceptors
- `auth` — Argon2id, JWT ~15min, Redis-backed rotating single-use refresh with reuse detection, password reset, lockout
- `tenancy` — orgs, branches, memberships, invitations, org switching
- Repository base class: `org_id` predicate + `deleted_at IS NULL` + `row_version` optimistic locking
- **PgBouncer footgun guard:** `SET LOCAL app.org_id` inside the request transaction, never session-level

**Frontend:** App Router groups `(public)` / `(auth)`, `middleware.ts` edge guard, SessionProvider, TanStack Query + Zustand wiring, shadcn/ui + Tailwind base, login/forgot-password/accept-invite/select-org, app shell.

**Exit criteria:**
- ✅ Tenant-isolation test: org A cannot read org B — including across pooled PgBouncer checkouts
- ✅ Login → org select → guarded route → 403 on wrong role, all audited
- ✅ `prisma migrate` runs clean from empty DB; seed produces a usable dev tenant

## Phase 2 — Curriculum & Question Bank (3 weeks)

**Backend:** curriculum (boards→classes→subjects→chapters→outcomes, curriculum_versions, lineage via derived_from_chapter_id), programs, questions (CRUD, BR-8 lineage versioning — approved questions immutable, edits fork new version with exactly one is_current), content_hash dedup on insert, CSV/XLSX/JSON import with per-row validation + downloadable error report, bulk approve/reject/archive, faceted search.

**Frontend:** Question Bank (faceted filters), Question Details (provenance + lineage), Import wizard, Programs.

**Why before ingestion:** structured import populates the bank in days; OCR takes weeks. Building import first unblocks retrieval, generation, and the assembler for testing without waiting on the ingestion pipeline — the single most valuable sequencing decision in the plan.

**Exit:** ✅ Import 1,000 questions with row-level error report; ✅ all 13 question types round-trip create→version→search; ✅ exact duplicate rejected, near-duplicate flagged.

## Phase 3 — Retrieval + AI service layer (4 weeks, overlaps Phase 2 from week 2)

**Retrieval module** (read-only): metadata filter → FTS → [vector, if adopted] → hybrid rank → dedup → coverage optimizer → final selection. Returns scored candidates so an ANN backend slots in later without caller changes.

**AI module** — a pipeline of swappable stages, not a blob:
`Prompt Manager → Prompt Version Manager → Context Builder → Model Selector → LlmProvider adapter → Response Parser → Validation Engine (15 stages) → Similarity Checker → Difficulty/Bloom classifiers → Retry Manager`

Hard boundaries: the AI module never opens a DB connection; persistence is the caller's job. Prompt text lives in `packages/prompts` (git-versioned, PR-reviewed); `prompt_versions` records which version is active per (task, model) with a partial unique index, so rollback is one UPDATE.

Plus: cost cascade (cache → bank → parametric → local classifier → cheap LLM → primary LLM → escalate → degrade), per-tenant token budgets checked at rung entry, prompt-injection defense (untrusted content in delimited data blocks), eval harness (golden set, offline metrics).

**Exit:** ✅ Generate schema-valid MCQ grounded in a real chapter; ✅ verification catches injected wrong-answer and a two-correct MCQ; ✅ prompt promote/rollback without redeploy; ✅ eval suite runs in CI on prompt/model change.

## Phase 4 — Assembler & generation worker (4 weeks)

The core transaction — where the product is won or lost.

- **Feasibility gate** — runs the real solver with a short time budget before enqueueing. Three outcomes: feasible / feasible-with-compromise (naming exactly which constraint relaxes) / infeasible. Per-source marginal count check is necessary but **not sufficient**.
- **Constraint solver** — quota math, joint difficulty/Bloom/coverage/source balancing, documented relaxation order, compromise report.
- **Generation worker** — per-slot: retrieve → gap → generate → validate → verify → dedup; then paper-level checks (difficulty balance, Mixed-Mode 70% textbook floor, marks reconciliation, no in-paper duplicates).
- **Step orchestration** — BullMQ Flows with typed steps, per-step retry, idempotency keys. A slot failing verification retries just that slot; a crash after 28/31 slots resumes rather than restarts.
- **SSE progress** — worker → Redis pub/sub → NestJS → browser.
- **`apps/workers` scaffold** — shared repository layer, no second copy of business logic.

**Exit:** ✅ acceptance criteria pass end-to-end; ✅ property-based tests: solver always returns a valid paper or an honest "infeasible + why"; ✅ blueprint snapshot re-run satisfies every constraint; ✅ cancel mid-generation leaves no partial paper.

## Phase 5 — Paper Builder UI (4 weeks, overlaps Phase 4 from week 2)

The largest single frontend surface.

Three-pane model, client document model, blueprint + section builders, question cards, drag-and-drop reorder, **locking** (locks survive regeneration), per-question/per-section/whole-paper regeneration, live difficulty + Bloom + marks validation, live preview, undo/redo, autosave, conflict handling, validation gate before submit.

Plus SSE hub: one multiplexed connection per tab, patch-or-invalidate against TanStack cache, reconnect + missed-event recovery, heartbeat, polling fallback, tenant-scoped cache partitioning with hard reset on org switch.

**Exit:** ✅ fast path (login→builder→generate→preview→export) in minutes; ✅ regenerating one section leaves others byte-identical; ✅ mid-stream SSE reconnect loses no progress.

## Phase 6 — Templates, export, approval (3 weeks) → **MVP boundary**

- `templates` — layout/branding JSONB, draft→active→archived, clone-on-write, preview, org default
- `exports` — PDF (headless Chromium + KaTeX server-side) and DOCX (docxtemplater) behind `ExportRenderer` interface; artifacts to private storage; time-limited signed GET URLs; export history
- `approvals` — draft→submitted→review→approved/rejected, mandatory rejection comment, per-question comments, separation of duties, withdraw approval, version diff
- `notifications` — in-app + email, event-driven, deduplicated, aggregated

**MVP exit:** ✅ acceptance criteria pass; ✅ all 13 question types render correctly in PDF, DOCX, and preview; ✅ answer key exports as a separate artifact.

**Cumulative: ~19–21 weeks to MVP.**

## Phase 7 — Textbook ingestion (5 weeks, parallel track from Phase 2)

Six-stage job chain: `OCR → Parser → Classifier → Embedding → Import → Notify`

- Direct-to-storage signed upload; magic-byte + size + page validation; malware scan before OCR
- Native-text-layer detection first, OCR only where needed; deskew/denoise/binarize preprocessing; per-block confidence with review routing
- Structural parser → typed textbook_assets; chapter detection; question extraction rules per type; classification; dedup (exact hash, near-duplicate ≥0.95, mid-band → manual review); merge strategy enriches provenance
- Idempotency keyed per stage; resume from last durable status; DLQ + replay
- OCR Progress screen with per-stage SSE, per-page status, retry-failed-pages, chapter-mapping correction
- Operational runbooks: stuck OCR queue, stuck embedding queue, high DLQ volume, storage limits

**Note the state-machine trap:** only `uploaded → ocr → parsed → embedded | failed` are persisted. `processing`, `classified`, `completed`, `retry` are in-memory job phases — don't let them leak into the column.

**Exit:** ✅ 100-page textbook end-to-end < 10 min; ✅ reprocess produces zero duplicate questions; ✅ OCR failure retains partial assets and still lets the teacher generate from the existing bank.

## Phase 8 — Billing, usage, analytics, admin (3 weeks) → **Beta**

**Exit:** ✅ acceptance criteria pass; ✅ usage counters reconcile against the underlying event log; ✅ quota warning fires before the limit blocks work.

## Phase 9 — Hardening, deployment, observability (4 weeks) → **GA**

- **Testing** — ≥80% unit coverage on core; solver property tests; answer-correctness gate in CI; contract tests on retrieval/ai/assembler interfaces; RLS isolation tests incl. pooled connections; prompt-injection adversarial suite; Playwright E2E; k6 load on generation + retrieval
- **CI/CD** — build → test → security scan (npm audit, Trivy, SAST, secret detection) → Docker build → publish → deploy. Branch strategy feature/* → develop → main. Migrations run before code, expand-migrate-contract.
- **Infra** — 5 multi-stage Dockerfiles, non-root, healthchecks, image size targets; either ECS Fargate task defs or K8s manifest set
- **Observability** — Pino structured logs with correlation ID propagated into jobs, Prometheus metrics, OpenTelemetry traces API→queue→worker→LLM→DB, Sentry, Grafana dashboards, Alertmanager rules
- **Security** — Helmet/CSP, strict CORS, rate limits (100/user/min, 1000/org/min, 50 logins/IP/hr), secret rotation, PII masking in logs
- **Backup/DR** — continuous WAL + daily full + hourly incremental, restore drills, RTO ≤30min / RPO ≤5min

**Exit:** ✅ blue/green or rolling deploy with automatic rollback on failed readiness; ✅ restore-from-backup drill passes; ✅ SLOs instrumented and alerting.

## Phase 10 — Scale & enterprise (8–12 weeks, evidence-triggered)

Nothing built on a date — each item has a measurement that unlocks it:

| Item | Trigger |
|---|---|
| pgvector / embeddings | Eval suite shows a paraphrase-recall gap |
| Qdrant | >5–10M vectors |
| Dedicated search engine (Meilisearch) | Lexical recall gap, or post-filter candidate sets routinely >10k |
| Extract ai/ocr into services | Divergent scaling profile, or OCR needs GPU/different runtime |
| Temporal | Steps span services, hours/days, or need durable timers |
| Postgres sharding (Citus) | Single-DB limits approached |
| Cell architecture | ~1M teachers |
| SSO/SAML, data residency, SOC 2 | Enterprise contracts |

Then product roadmap: **V2** Worksheets → **V3** Student Analytics → **V4** Adaptive Testing → **V5** AI Tutor.

## Parallelization

```
Wk   0    4    8    12   16   20   24   28   32
     │────│────│────│────│────│────│────│────│
P0   ██
P1     ██████
P2          ██████
P3            ████████
P4                  ████████
P5                    ████████
P6                          ██████        ← MVP
P7          ████████████████               ← parallel track
P8                              ██████     ← Beta
P9                                  ████████ ← GA
```

| Track | Owns | Phases |
|---|---|---|
| Platform | schema, RLS, auth, tenancy, repositories, config | P1 → P8 |
| Domain | curriculum, questions, papers, approvals, templates, export | P2, P4, P6 |
| AI | retrieval, AI module, assembler, evals, prompts | P0 spikes, P3, P4 |
| Ingestion | OCR chain, parser, classifier, embeddings, import | P7 (independent) |
| Frontend | shell, all 14 screens, Paper Builder, SSE hub | P1, P2, P5, P6, P8 |
| DevOps | Docker, CI/CD, IaC, observability, DR | P1 (compose+CI), P9 |

## The four things most likely to sink this

1. **Assembler infeasibility surfacing late.** Greedy fill looks fine in demos with a rich bank and falls apart on thin, real corpora of a new coaching center. Spike S-2 in Phase 0 is cheap; discovering it in Phase 5 is not.
2. **AI producing wrong answers.** Structured output guarantees shape, not truth. LLM-as-judge is circular for exactly the errors that destroy trust (bad arithmetic, two-correct MCQs). Execute the solution; assert single-correct; prefer parametric variants on known-correct items.
3. **Math OCR quality.** Forms-oriented OCR mangles equations, and STEM textbooks are the target market. Corpus quality gates the entire retrieval-first thesis.
4. **Cold-start economics.** A new tenant's bank is empty, so 60–90% of early questions are AI-generated. Budget AI as a declining per-tenant curve, not a flat 10–20%, and seed the global corpus aggressively before launch.

---

# 2. Security Audit

## ⚠️ Rotate this credential now

`.env:1` contains a **live Supabase Postgres superuser password**:
```
postgresql://postgres:Panigrahi%40902@db.wbluyayvlhnurybpbkwv.supabase.co:5432/postgres
```
Gitignored, so probably never pushed — but it's a real production credential sitting in a plaintext file at the shared repo root. Treat it as compromised:
1. Supabase → Settings → Database → **Reset database password**
2. Create a dedicated `app_user` role (not `postgres`) for the application
3. `git log --all -p -- .env` to confirm it was never committed. If it was, the history needs rewriting and the project key rotating too.

Also `JWT_SECRET=replace-me-in-dev` is a known placeholder signing key present in both `.env` and `.env.example`.

## Audit findings (19 total, ranked)

| # | Severity | Finding | Location |
|---|---|---|---|
| 1 | Critical | Live Supabase superuser password in plaintext at shared repo root | `.env:1` |
| 2 | Critical | Placeholder JWT signing key; no entropy/length check at boot | `.env:4`, `.env.example:4` |
| 3 | Critical | Prisma connects as `postgres` superuser → will bypass RLS policies even after written | `.env:1`, `prisma.service.ts` |
| 4 | Critical | No RLS, no `org_id` on any model, no tenant context — "a breach waiting to happen" | `schema.prisma` |
| 5 | High | `ConfigModule.forRoot` with no validation schema — boots happily with placeholder secret | `app.module.ts:9` |
| 6 | High | `main.ts` has zero security middleware: no helmet, no CORS allowlist, no ValidationPipe, no exception filter, no body cap, no rate limit, no request-id | `main.ts:5-11` |
| 7 | High | No `.dockerignore` — first `COPY . .` bakes `.env` into an image layer permanently | *(absent)* |
| 8 | High | No ESLint/Prettier config or dependency, yet root declares lint/format scripts — no boundary rule is mechanically enforceable | root `package.json:7-8` |
| 9 | Medium | `enableShutdownHooks` uses `process.on('beforeExit')` — removed from Prisma 5's library engine, never fires on SIGTERM | `prisma.service.ts:14-18` |
| 10 | Medium | Unauthenticated health endpoint discloses infra state; no liveness/readiness split | `health.controller.ts:9-12` |
| 11 | Medium | `packages/contracts` publishes raw TypeScript source, no exports map — the exact channel by which a server constant becomes a browser string | `packages/contracts/package.json:4-5` |
| 12 | Medium | `turbo.json` uses "pipeline" but turbo@^2.0.0 renamed it "tasks" — `pnpm dev`/`build` broken as committed | `turbo.json:3` |
| 13 | Medium | Compose runs `postgres:16` not `pgvector/pgvector:pg16`; no pg_trgm/citext bootstrap; passwordless Redis | `infra/docker-compose.yml` |
| 14 | Medium | Schema drift from docs/03 (fullName vs name, missing User.status, Membership.status undocumented, missing branch_id, Role 3 vs 6 values) | `schema.prisma` |
| 15 | Low | No CSP/HSTS/frame headers, `poweredByHeader` left on | `next.config.mjs` |
| 16 | Low | `noEmit:true` in base vs `outDir:dist` in api mismatch | `tsconfig.base.json:7` |
| 17 | Low | `packages/db/tsconfig.json` includes only `**/*.prisma` — inert config | `packages/db/tsconfig.json:3` |
| 18 | Low | `next lint` declared, eslint not installed | `apps/web/package.json:7` |
| 19 | Low | 3 of 26 tables; no migrations dir; no apps/workers; no packages/config,events; no tests; no CI; no Dockerfiles | *(absent)* |

Note: Next.js loads `.env` from `apps/web/`, so the root `.env` is NOT currently inlined into the bundle. The exposure is (a) a real production credential in the tree, (b) no structural boundary preventing it — `packages/db` already uses `dotenv -e ../../.env --`, so pulling root secrets into any workspace is one copy-paste away.

## Root cause: one build context

```
apps/web (client component)
   └─ imports @ai-paper/contracts          ← raw TS, no boundary
        └─ which imports a "constants" file
             └─ which reads process.env.SOMETHING
                  └─ bundler inlines the literal value into .next/static/*.js
                       └─ visible in View Source and the console, forever
```

You cannot lint your way out of this reliably because `apps/web` and `apps/api` share a lockfile, node_modules tree, tsconfig base, and env root. **The fix is physical, not procedural: give each side its own build context.**

## Trust-zone model

```
╔═ ZONE 0 ── UNTRUSTED ══════════════════════════════════════════════╗
║  Browser: JS bundle · devtools console · localStorage · View Source ║
║  ASSUME EVERYTHING HERE IS PUBLISHED.                               ║
║  Holds: an opaque httpOnly cookie it cannot read. Nothing else.     ║
╚════════════════════════════════════════════════════════════════════╝
        │  fetch('/api/proxy/...')   same-origin, cookie sent automatically
        │  no Authorization header ever constructed in the browser
        ▼
╔═ ZONE 1 ── EDGE / BFF ═════════════════════════════════════════════╗
║  Next.js server runtime: route handlers · middleware · RSC          ║
║  Never shipped to the browser.                                      ║
║  Holds: SESSION_SECRET, encrypted session cookie, API_INTERNAL_URL  ║
║  Never holds: DB creds, LLM keys, JWT signing key                   ║
╚════════════════════════════════════════════════════════════════════╝
        │  Authorization: Bearer <access token>   private network only
        ▼
╔═ ZONE 2 ── PRIVATE ════════════════════════════════════════════════╗
║  NestJS API  +  Workers (no ingress at all)                         ║
║  Holds: DATABASE_URL · REDIS_URL · JWT keys · GEMINI_API_KEY ·      ║
║         S3 creds · MATHPIX_KEY · STRIPE_SECRET                      ║
╚════════════════════════════════════════════════════════════════════╝
        ▼
╔═ ZONE 3 ── DATA ═══════════════════════════════════════════════════╗
║  Postgres (RLS forced, app_user role) · Redis · S3 · LLM providers  ║
╚════════════════════════════════════════════════════════════════════╝
```

**The decisive move:** the access token never enters the browser. Browser gets only an encrypted httpOnly SameSite=Strict cookie. `document.cookie` returns nothing. Zone 1 exchanges cookie → session → bearer token server-side. XSS can ride a session but cannot exfiltrate a credential.

## Folder architecture

> **Status: DECIDED.** This layout was adopted as the project's canonical structure in reconciliation
> Pass 2 and recorded in [ADR-015](ADR/ADR-015-workspace-root-split.md). All architecture docs (02, 07,
> 08, 09, Blueprint) are now aligned.

```
papergen/
├── backend/                    ← Python workspace root (own pyproject.toml, own venv, own .env)
├── frontend/                   ← Node workspace root (own package.json, own lockfile, own .env.local)
├── shared/                     ← contracts + docs only, zero secrets
├── infra/
├── docs/
└── .github/workflows/
```

Two workspace roots in one git repo — one PR can still change an endpoint and its caller atomically, but the two build graphs never touch. See [`07_Backend_Architecture.md`](07_Backend_Architecture.md) §3 for the detailed `backend/` tree (FastAPI + Celery, SQLAlchemy, Alembic), [`08_Frontend_Architecture.md`](08_Frontend_Architecture.md) for `frontend/` (Next.js), and the [Blueprint](Blueprint_AI_Question_Paper_Generator.md) §13 for a compact overview.

> **Note:** The detailed sub-trees below (`backend/`, `frontend/`, `shared/`) were written during the
> initial audit of the reference prototype at `D:\ai paper` and reflect its **NestJS/Prisma/BullMQ**
> conventions. The locked stack is **FastAPI/SQLAlchemy/Celery** (ADR-006). The sub-trees are preserved
> as reference material for the prototype; the authoritative target structure is in doc 07 §3.

### backend/
```
backend/
├── apps/
│   ├── api/                              # the ONLY internet-facing process
│   │   └── src/
│   │       ├── main.ts                   # thin: composes bootstrap/*
│   │       ├── bootstrap/
│   │       │   ├── security.ts            # helmet · CORS allowlist · body cap · rate limit
│   │       │   ├── validation.ts          # ZodValidationPipe, whitelist+forbid unknown
│   │       │   ├── observability.ts       # pino · OTel · /metrics
│   │       │   └── shutdown.ts            # SIGTERM/SIGINT drain (fixes finding #9)
│   │       ├── common/
│   │       │   ├── guards/                # Auth → Tenant → Roles → Scope
│   │       │   ├── interceptors/          # envelope · audit · timing · request-id
│   │       │   ├── filters/               # ONE exception filter → error envelope + ehRef
│   │       │   ├── decorators/            # @CurrentUser @CurrentOrg @Roles @Public
│   │       │   └── mappers/               # entity → DTO field ALLOWLIST (never spread)
│   │       ├── modules/
│   │       │   ├── platform/              auth · tenancy · audit · notifications · flags
│   │       │   ├── catalog/               curriculum · programs · templates
│   │       │   ├── content/               questions · textbooks
│   │       │   ├── intelligence/          retrieval · ai · assembler        ★ core IP
│   │       │   ├── delivery/              papers · approvals · exports
│   │       │   └── commerce/              billing · usage · analytics
│   │       └── infra/                     prisma · redis · queue · storage · mailer
│   └── workers/                           # BullMQ consumers, zero HTTP surface
│       └── src/jobs/{ingestion,generation,export,embedding,notification,cleanup,analytics}/
├── packages/
│   ├── config/          # Zod env schema — SERVER ONLY, throws on boot if invalid
│   ├── db/              # prisma schema · SQL migrations · RLS policies · seeds
│   ├── domain/          # pure rules: BR-1…BR-11, marks math, solver. ZERO I/O.
│   ├── prompts/         # versioned prompt templates — IP, never leaves backend
│   ├── ai-providers/    # LlmProvider adapters (Anthropic, local, …)
│   ├── events/          # event names + payload types
│   └── testing/         # factories · RLS harness · Testcontainers fixtures
├── backend/.env         # server-only secrets; never shipped to the browser
├── frontend/.env.local   # browser-safe NEXT_PUBLIC_* vars only
├── .dockerignore
└── Dockerfile.{api,worker}
```

Grouping the 17 modules into six bounded contexts (platform, catalog, content, intelligence, delivery, commerce) fixes navigability. `intelligence/` is flagged as the core IP.

Every module has the same anatomy:
```
<module>/
├── <module>.module.ts
├── <module>.controller.ts      # 3–5 lines per handler, no `if`
├── <module>.service.ts         # all business logic
├── <module>.repository.ts      # the ONLY file allowed to touch Prisma
├── dto/                        # re-exported from shared/contracts
├── mappers/                    # entity → response DTO, explicit fields
└── __tests__/
```

`packages/domain` being pure and I/O-free is what makes the assembler testable — property-based tests over randomized question pools run in milliseconds with no database.

### frontend/
```
frontend/
├── src/
│   ├── app/
│   │   ├── (public)/                login · forgot-password · accept-invite/[token]
│   │   ├── (app)/                   select-org · dashboard · notifications · profile · settings
│   │   │   ├── (teacher)/           papers · papers/[id] · papers/[id]/build ·
│   │   │   │                        question-bank · question-bank/[id] · textbooks/[id]
│   │   │   ├── (admin)/             admin/{users,programs,templates,approvals,billing}
│   │   │   └── (platform)/          platform/{orgs,analytics,prompt-versions,audit}
│   │   └── api/                     ★ BFF — the only place a token exists
│   │       ├── auth/[...action]/route.ts    # login, refresh, logout, switch-org
│   │       ├── proxy/[...path]/route.ts     # attaches bearer server-side
│   │       └── stream/[channel]/route.ts    # SSE relay
│   ├── server/                      every file starts: import 'server-only'
│   │   ├── session.ts               # encrypted cookie (iron-session)
│   │   ├── api-client.ts            # server→API fetch
│   │   └── env.server.ts            # SESSION_SECRET, API_INTERNAL_URL
│   ├── features/                    12 modules, uniform anatomy
│   │   └── <feature>/{components,hooks,api.ts,schema.ts,types.ts,index.ts}
│   ├── components/{ui,shared,layout}/
│   ├── lib/                         browser-safe ONLY — query client, sse hub, format
│   └── env.client.ts                explicit NEXT_PUBLIC_* allowlist, `as const`
├── .env.local                       NO database URL. NO LLM key. NO JWT secret.
└── Dockerfile
```

`src/server/` vs `src/lib/` is the boundary you can see. `server/` carries the `server-only` package import, so if a client component ever imports it, the build fails rather than shipping the secret.

### shared/
```
shared/contracts/src/
├── dto/        # Zod request + response schemas (safe to ship — field names only)
├── enums/      # role · status · qtype · source · difficulty · bloom
├── errors/     # error codes + EH-1…EH-18 refs
└── limits.ts   # CN-1…CN-22 public constants (500MB, 500 marks, 1–200 questions)
```

Three hard rules, all lint-enforced: no `process.env`, no Node built-ins, no imports from `backend/`.

## Tech stack

> **Superseded rows.** This table predates the reconciliation. The rows below marked ⚠️ have been
> decided differently and the ADR is authoritative: API framework, ORM, validation, queue, LLM,
> embeddings, OCR, tests, and prod infra. Rows not marked still stand.

| Concern | Decision (post-reconciliation) | ADR |
|---|---|---|
| ⚠️ API framework | **FastAPI (Python)** on uvicorn — not NestJS | [ADR-006](ADR/ADR-006-fastapi-over-nestjs.md) |
| ⚠️ ORM / migrations | **SQLAlchemy 2.x async + Alembic** — not Prisma | [ADR-006](ADR/ADR-006-fastapi-over-nestjs.md) |
| ⚠️ Validation | **Pydantic v2**, `extra="forbid"` — not Zod/class-validator on the server | [ADR-006](ADR/ADR-006-fastapi-over-nestjs.md) |
| ⚠️ Queue | **Celery + Redis** — not BullMQ | [ADR-006](ADR/ADR-006-fastapi-over-nestjs.md) |
| ⚠️ Tests | **pytest** (backend) + Vitest/Playwright (frontend) — not Jest | [ADR-006](ADR/ADR-006-fastapi-over-nestjs.md) |
| ⚠️ LLM | **Gemini API** — Pro tier generate, Flash tier classify; IDs in config | [ADR-008](ADR/ADR-008-gemini-as-sole-ai-provider.md) |
| ⚠️ Embeddings | **Gemini embedding model**, stored at 1536 dims; deferred until a recall gap | [ADR-007](ADR/ADR-007-defer-pgvector-retrieval.md), [ADR-008](ADR/ADR-008-gemini-as-sole-ai-provider.md) |
| ⚠️ OCR | **Mathpix primary**, Gemini vision fallback, plain OCR for prose | [ADR-014](ADR/ADR-014-math-first-ocr.md) |
| ⚠️ Prod infra (MVP) | **ECS Fargate**; K8s manifests written, adopted on trigger | [ADR-013](ADR/ADR-013-ecs-fargate-before-kubernetes.md) |
| Database | Postgres 16+ with `pg_trgm`, `citext`, RLS forced; `app_user` non-superuser role | [ADR-001](ADR/ADR-001-postgresql-transactional-database.md) |
| Auth | Argon2id · JWT RS256 · rotating single-use refresh in Redis · no token in the browser | — |
| Frontend | Next 15 App Router · React 19 · TanStack Query + Zustand · Tailwind + shadcn/ui | — (unchanged) |

### Original recommendation table *(historical — retained for the reasoning, not the conclusions)*

| Concern | Recommendation | vs. your docs/code |
|---|---|---|
| Runtime | Node 22 LTS | docs pin node:20-alpine — EOL April 2026 |
| API framework | NestJS 11 + Fastify adapter | pkg has Nest 10 + Express; Fastify ~2× throughput on JSON |
| ORM | Prisma 6 | pkg has 5.18 |
| Database | Postgres 17 + pgvector + pg_trgm + citext, RLS forced | compose uses plain postgres:16 |
| DB role | app_user — non-superuser, non-owner | currently postgres superuser (bypasses RLS) |
| Validation | Zod + nestjs-zod, schemas in shared/ | docs say class-validator; Zod lets one schema serve both sides |
| Queue | BullMQ 5 + Redis 7 (Flows for multi-step) | as docs |
| Auth | Argon2id · JWT RS256 · refresh in Redis · BFF cookie, no token in browser | docs allow HS256 and a client-held token |
| Tests | Vitest + Supertest + Testcontainers; Playwright E2E | docs say Jest |
| Frontend | Next 15 App Router · React 19 · TS 5.7 | as docs |
| State | TanStack Query v5 (server) + Zustand (client) | as docs |
| UI | Tailwind v4 + shadcn/ui + Radix | as docs |
| Forms | React Hook Form + Zod resolver (shared schema) | as docs |
| Real-time | SSE, relayed through the BFF | as docs |
| Math | KaTeX (client render + server-side for PDF) | as docs |
| Export | Playwright Chromium → PDF, docxtemplater → DOCX, behind ExportRenderer | docs say headless Chromium |
| OCR | Mathpix primary + vision-LLM fallback, behind DocumentExtractor | doc 05 defaults to Cloud Vision/Tesseract — needs an ADR |
| LLM | Claude behind LlmProvider: claude-sonnet-5 generate · claude-haiku-4-5 classify · claude-opus-5 audits | doc 06 lists GPT-4o and stale Claude names |
| Embeddings | Deferred; interface in place, adopt on measured recall gap | needs an ADR |
| Observability | Pino · OpenTelemetry · Prometheus · Grafana · Loki · Sentry | as docs |
| Dev infra | Docker Compose (pgvector, Redis, MinIO, Mailpit) | compose missing pgvector/MinIO |
| Prod infra (MVP) | ECS Fargate — one api service, one worker service | doc 09 assumes full K8s |
| IaC | Terraform | as docs |
| CI | GitHub Actions + bundle-leak scanner | new |

Three deltas worth arguing about: Zod over class-validator, Vitest over Jest, Fargate before Kubernetes.

## The six mechanisms that make a leak visible

| # | Mechanism | Where | Stops |
|---|---|---|---|
| 1 | Split env, no root .env | backend/.env, frontend/.env.local | Frontend process cannot physically read DATABASE_URL |
| 2 | Boot-time env validation | backend/packages/config/src/schema.ts | Refuses to start on placeholder secret, short keys, missing vars |
| 3 | `import 'server-only'` | top of every frontend/src/server/*.ts | Build fails if a client component imports session/API code |
| 4 | Explicit public allowlist | frontend/src/env.client.ts | Every browser-visible value enumerated in one 10-line file |
| 5 | Response mappers | backend/.../common/mappers/ | password_hash, content_hash never serialized — allowlist, never spread |
| 6 | CI bundle scanner | .github/workflows/security.yml | Greps built .next/static for backend env keys + high-entropy strings; fails the build |

Plus tenant isolation: `FORCE ROW LEVEL SECURITY` on all tenant tables, `SET LOCAL app.org_id` inside the request transaction (never session-level — the PgBouncer leak), and a test asserting the setting doesn't survive a pooled checkout.

**Review rule:** any PR touching `env.client.ts`, `shared/contracts/`, or `frontend/src/server/` is a security review.

## Migration path (~1 week)

| Step | Work | Outcome |
|---|---|---|
| 0 | Rotate Supabase password; create app_user; regenerate JWT_SECRET; delete root .env | Live exposure closed |
| 1 | Create backend/ frontend/ shared/; git mv apps→new locations; two lockfiles; fix turbo.json pipeline→tasks | Build contexts separated |
| 2 | packages/config + Zod schema; harden main.ts (helmet, CORS, ZodValidationPipe, exception filter, request-id, rate limit); fix shutdown hooks; split health live/ready | Findings 5,6,9,10 closed |
| 3 | ESLint+Prettier+boundary rules; .dockerignore; server-only; env.client.ts; bundle scanner in CI | Findings 7,8,11 closed, regression-proofed |
| 4 | Reconcile schema.prisma with doc 03 (26 tables), RLS policies, pgvector compose, isolation tests | Findings 3,4,13,14 closed |

After step 4 you're at Phase 1 exit criteria from the roadmap.

**Decision on repo structure:** two workspace roots inside one git repo (right for a 5–6 person team). Split into two separate git repos only if you later need independent release cadences, a separate frontend vendor with no backend access, or per-repo compliance boundaries.

---

# 3. The final pivot instruction (most recent, not yet executed)

Your instruction: *"reduce the llm dependency the most you can and create all the internal tools — with those internal tools I'll add the content to database and then all the questions will be made using those database and only the llm will be used for paper generation."*

Confirmed reading: **the LLM stops being infrastructure and becomes a single, optional, gap-fill call.**
- **Internal tools** (deterministic, no LLM) do all ingestion, extraction, classification, dedup, and authoring → populate the DB
- **The database corpus** is the source of truth for questions
- **The LLM** is invoked only during paper generation, and only when retrieval + parametric variants cannot fill a slot

This removes embeddings/pgvector, the LLM classifier, LLM validation, and most of the AI module from the critical path.

**9-item TODO list created (execution not yet started):**
1. Create a git safety net (snapshot commit) before restructuring — **was in progress, interrupted, needs retry**
2. Restructure into backend/ frontend/ shared/ (git mv apps/api→backend/apps/api, apps/web→frontend, packages/db→backend/packages/db, packages/contracts→shared/contracts)
3. Wire workspace/turbo/tsconfig for the new layout (fix turbo.json pipeline→tasks; two lockfiles)
4. Split env files + add boot-time Zod config validation (packages/config)
5. Harden API bootstrap (helmet, CORS allowlist, ZodValidationPipe, exception filter, request-id, rate limit) + fix Prisma shutdown hooks + Prisma tenant context (RLS, SET LOCAL app.org_id)
6. Build deterministic internal-tool engines: rule-based classifier, hash/trigram dedup, document parser/extractor, parametric variant generator (NO LLM)
7. Add frontend secret-isolation boundary (server-only, env.client.ts, BFF proxy)
8. Write internal tools architecture doc
9. Ensure LLM is wired ONLY into the paper-generation gap-fill path (remove embeddings/LLM-classifier from critical path)

**Security constraints to preserve throughout:** the live Supabase credential and JWT_SECRET in `.env` must be treated as compromised and never committed/pushed/exposed; `.env` stays gitignored; the architecture's core requirement is that no secret ever reaches the frontend bundle/console. Rotating the Supabase password and JWT_SECRET requires your own Supabase console — that part is on you.

**Where it stalled:** attempting the first execution step (git safety-net commit) via:
```
cd "d:/ai paper" && git init -q && git add -A && git -c user.email=dev@local -c user.name=dev commit -q -m "Snapshot before backend/frontend restructure" && git log --oneline
```
This hit a transient model-availability error, then after a model switch, repeated `403 pre-consume quota failed` errors. **No files have been created, moved, or edited.**

## Next step when you resume

Retry the git safety-net snapshot (verify `.env` is excluded by `.gitignore` first), then proceed to the restructure (move apps/api→backend/apps/api, apps/web→frontend, packages/db→backend/packages/db, packages/contracts→shared/contracts).