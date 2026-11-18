# AI Question Paper Generation Platform — Production Blueprint

> Prepared as a build-ready blueprint for a professional engineering team. Opinionated defaults are given; alternatives noted where they materially change cost or risk.

---

## 1. Executive Summary

**Product.** A multi-tenant SaaS platform that lets teachers assemble high-quality exam papers by blending five question sources — Institute Bank, Textbook Exercise, In-text, Previous Year, and AI-generated — under a teacher-configured distribution (percentages or exact counts), with automatic answer keys, rubrics, and PDF/DOCX export.

**Core differentiator.** A **hybrid retrieval-first engine**: the system prefers vetted, curriculum-aligned human questions and only invokes generative AI to fill gaps. This keeps quality high, cost low, and hallucination risk bounded, while still giving teachers unlimited variety on demand.

**Key architectural bets.**
- **Retrieval before generation (as a *principle*, right-sized to the data).** The system prefers vetted, curriculum-aligned human questions and treats generation as gap-fill. At MVP this is a metadata pre-filter + Postgres full-text + trigram search — **not** an ANN/vector stack. After the hard filter (board/class/subject/chapter/type/difficulty) the candidate set per slot is typically tens to low-hundreds of items, where exact filter + lexical ranking is as good as ANN and far cheaper to operate. Vector search is added later, on evidence of a measured retrieval-quality gap (§10, §18).
- **Modular monolith, not microservices.** One deployable app in **one language** with clean internal module boundaries (`retrieval`, `ai`, `assembler`, `curriculum`, `papers`, `billing`) plus one worker process. Vendor/model swap is enforced by a module *interface*, not a network hop — you get the same isolation without gRPC, service mesh, or multi-language ops. Extract a module into its own service later, only when a metric demands it (§7, §18).
- **The assembler is the core IP.** Turning `pattern × difficulty × source-distribution × chapter/LO coverage × dedup` into a valid paper from finite pools is a multi-constraint satisfaction problem, not "fill by priority". It gets a real solver with a documented relaxation order and a compromise report (§4, §20) — this is where the bulk of engineering effort goes.
- **AI *correctness* is verified, not trusted.** Structured output guards *shape*, not *truth*. Quantitative items are answer-checked by computation (execute the worked solution) and prefer parametric generation (vary numbers on a known-correct retrieved item); AI output is framed as teacher-approved drafts (§5, §10).
- **Tenancy by row-level isolation from commit one.** Shared schema + `org_id` + RLS is built with the first tenant, never retrofitted (retrofitting isolation is a breach waiting to happen). Documented path to schema-per-tenant for enterprise (§8, §14).

**Model strategy.** The **Gemini API**, addressed by tier, never by a dated model string: a **Flash-tier** model for cheap classification, tagging, and extraction; a **Pro-tier** model for question generation, validation support, and hard reasoning (rubric synthesis, competency-mapping audits), used sparingly at the top end. Model IDs are config, never hardcoded (ADR-008).

**Target economics.** MVP runs at roughly **$250–500/mo** infra for ~100 active teachers (single Fargate app + one worker, no Kubernetes, no vector DB); ~$3.5–6k/mo at 10k teachers; scaling analysis in §18–19. AI cost is controlled by the retrieval-first design at steady state — but note the **cold-start reality**: a new coaching center's institute bank is empty, so early usage leans heavily on the global corpus + AI generation. Budget AI as a *declining curve* from launch, not a flat 10–20% (§3, §15, §23).

**Delivery.** MVP in ~10–12 weeks is realistic *because* of the simplifications above (monolith, deferred vector search, right-sized OCR, RLS day-one). Beta at ~4–5 months, GA at ~7–8 months (§21).

---

## 2. Functional Requirements

**Authentication & tenancy**
- FR-1 Email/password + OAuth (Google, Microsoft) sign-in; SSO (SAML/OIDC) for enterprise.
- FR-2 RBAC roles at v1: **`teacher`, `coaching_admin`, `super_admin`** — three values on `memberships.role`, stored as a constrained text column so adding a role is a CHECK-constraint migration. `hod`, `reviewer`, and `viewer` are **reserved** values, documented but not implemented; approval authority at v1 belongs to `coaching_admin` bounded by BR-6 separation of duties (ADR-011).
- FR-3 Multi-org isolation: a user's data access is scoped to their organization(s).

**Curriculum & content**
- FR-4 Manage Boards (CBSE/ICSE/State/IB/IGCSE), Classes, Subjects, Chapters, Learning Outcomes, and their hierarchy.
- FR-5 Ingest textbooks (PDF/scans) and auto-extract chapters, exercises, in-text questions, activities, diagrams, tables, keywords, learning outcomes.
- FR-6 Manage a per-organization Institute Bank (CRUD, import CSV/DOCX, bulk tag, approve).
- FR-7 Store Previous Year Questions with year/board/exam metadata.

**Paper generation**
- FR-8 Teacher configures: Board, Class, Subject, Chapters, Learning Outcomes, Exam Type, Question Pattern, total Marks (flexible), Duration, Difficulty Distribution, Language, Instructions, and **Source Distribution** (percent or exact count per source).
- FR-9 System assembles a paper honoring the pattern (section-wise question types/marks), difficulty mix, chapter/LO coverage, and source distribution — filling from sources by priority and generating only when needed.
- FR-10 Duplicate/near-duplicate suppression across selected questions and against prior papers by the same teacher/organization.
- FR-11 Auto-generate answer key and rubrics per question.
- FR-12 Multiple versions/sets of the same blueprint (Set A/B/C) with shuffled order and equivalent difficulty.
- FR-13 Manual editing: swap, regenerate, edit, reorder, lock questions; re-balance marks.

**Templates, export, workflow**
- FR-14 Configurable paper templates (header, logo, sections, marks column, instructions block).
- FR-15 Export to PDF and DOCX; math via LaTeX/MathML rendering; optional answer-key export.
- FR-16 Approval workflow: draft → submitted → under review → approved/rejected, with comments and version history.

**Analytics, billing, admin**
- FR-17 Analytics: papers generated, source mix, chapter/LO coverage, difficulty distribution, question reuse, generation cost per teacher/organization.
- FR-18 Subscription plans, seat/usage metering, quotas, overage handling, invoices.
- FR-19 Admin portal: organization management, feature flags, content moderation, usage dashboards, audit log search.

---

## 3. Non-Functional Requirements

| Attribute | Target |
|---|---|
| Availability | 99.9% API (MVP), 99.95% (enterprise) |
| Latency | Retrieval-only paper < 3 s p95; paper with AI generation < 30 s p95 (async with progress) |
| Throughput | 100 concurrent generations (MVP) → 5k (enterprise) via horizontal workers |
| OCR ingestion | 300-page textbook processed < 15 min |
| Durability | RPO ≤ 5 min, RTO ≤ 30 min; PITR on primary DB |
| Scalability | Stateless tiers scale horizontally; documented path 100 → 1M teachers (§18) |
| Security | Encryption in transit (TLS 1.2+) and at rest (KMS); SOC 2-aligned controls; GDPR/India DPDP compliant |
| Data residency | Region pinning per tenant (e.g., India `ap-south-1`, EU `eu-west-1`) |
| Consistency | Strong within tenant transactions; eventual for analytics/search indexes |
| Observability | Full tracing, structured logs, RED/USE metrics, SLO alerting |
| Accessibility | WCAG 2.1 AA frontend |
| Cost efficiency | AI spend ≤ 25% of infra cost at steady state via retrieval-first + model tiering. **Cold-start exception:** a new coaching center's institute bank is empty, so early usage is AI-heavy (60–90% of questions generated). Budget AI as a declining curve from onboarding, not a flat minority. |
| Maintainability | 80%+ unit coverage on core logic; IaC for all infra |

---

## 4. User Flow

```
Teacher
  │  login (RBAC, org scoped)
  ▼
Dashboard ──► "New Paper"
  │
  ▼
Configure blueprint
  Board▸Class▸Subject▸Chapters▸LOs
  ExamType▸Pattern▸Marks▸Duration
  Difficulty mix▸Language▸Instructions
  Source distribution (40/20/20/10/10)
  │  validate feasibility (enough questions per source/pattern?)
  ▼
Assemble (async job) ──► progress stream (SSE/WebSocket)
  │  retrieve by priority ▸ dedupe ▸ generate gaps ▸ validate ▸ answer key + rubric
  ▼
Review draft ──► edit / swap / regenerate / lock / reorder
  │
  ▼
Generate versions (A/B/C)  ──►  Export (PDF/DOCX)
  │
  ▼
(optional) Submit for approval ──► HOD reviews ──► approved
  │
  ▼
Download / archive / analytics updated
```

**Feasibility gate (FR-9 support).** Before running, the assembler does a **fast dry-run solve** — not just a per-source count. A marginal check ("does each source have enough questions for its quota?") is necessary but *not sufficient*: it can pass and then fail mid-assembly because the difficulty mix, chapter/LO coverage, and source distribution can't all be satisfied *jointly* from the available pools. The gate therefore runs the same constraint solver used for assembly (§20) with a short time budget and reports one of three outcomes: (a) **feasible** — proceed; (b) **feasible with compromise** — show exactly which constraint must relax (e.g. "only 2 Previous-Year questions exist for these chapters; the other 6% will be reallocated to AI") and let the teacher accept or adjust; (c) **infeasible** — offer to reduce a source, reallocate to AI, or broaden chapters. No silent substitution, and no "looked feasible, produced a bad paper" surprise after the job runs.

---

## 5. AI Workflow (end-to-end)

```
Blueprint ─► Plan (section/type/marks/difficulty/source quotas)
        │
        ▼
   Constraint-solver assembly (joint satisfaction; see §4, §20)
        │
        ▼
   ┌─────────────── Per-slot resolution (priority order) ───────────────┐
   │ 1 Institute Bank → filtered search (board/class/subj/chapter/LO/    │
   │                   type/difficulty) → lexical rank → dedupe          │
   │ 2 Textbook      → same, over textbook_questions                     │
   │ 3 In-text       → same, over intext_questions                       │
   │ 4 Previous Year → same, over question_bank(source=previous_year)    │
   │ 5 AI Generate   → prefer PARAMETRIC (vary numbers on a known-correct │
   │                   retrieved item) → else RAG-grounded free-gen →     │
   │                   Claude → JSON → schema validate → repair           │
   └─────────────────────────────────────────────────────────────────────┘
        │
        ▼
   Correctness verification
     • quantitative/numerical → EXECUTE the worked solution (sympy /
       sandboxed code) and assert final answer matches; reject on mismatch
     • MCQ → assert exactly one option satisfies the key; no two-correct/none
     • qualitative → LLM-as-judge (advisory) + human-approve
        │
        ▼
   Cross-slot dedupe (context-aware: exact hash · trigram · soft concept
   overlap) & coverage check
        │
        ▼
   Difficulty/Bloom/Competency verification (auto-tag, reconcile to plan)
        │
        ▼
   Answer key + rubric synthesis  ─►  Paper object (versioned)  ─►  Export
```

AI is invoked in three distinct roles: **generation** (fill gaps — parametric first, free-gen second), **classification** (difficulty/Bloom/competency tagging of any untagged item), and **validation/repair** (schema + pedagogical checks). Crucially, **factual/numeric correctness is verified by computation, not by the model judging itself** — LLM-as-judge is a circular check for the class of errors (wrong arithmetic, two-correct MCQs) that most damages teacher trust. Details in §10–11.

---

## 6. High-Level Architecture

```
                         ┌──────────────┐
   Teachers / Admins ───►│  CDN (CF)    │
                         └──────┬───────┘
                                │
                         ┌──────▼───────┐
                         │  Web (Next)  │   (marketing SSR + app SPA + admin)
                         └──────┬───────┘
                                │  HTTPS/JWT
                    ┌───────────▼─────────────────────────────┐
                    │      Application (single deployable)     │
                    │  ┌────────────────────────────────────┐  │
                    │  │ HTTP layer (FastAPI): authN/Z · RBAC │  │
                    │  │ rate limit · Pydantic · idempotency  │  │
                    │  ├────────────────────────────────────┤  │
                    │  │ Modules (in-process, typed ifaces):  │  │
                    │  │  curriculum · questions · retrieval  │  │
                    │  │  assembler · ai · papers · templates │  │
                    │  │  approvals · billing · analytics     │  │
                    │  └────────────────────────────────────┘  │
                    └───────┬───────────────┬──────────┬───────┘
                            │ enqueue        │ read/write│ LLM calls
                     ┌──────▼──────┐  ┌──────▼──────┐  ┌─▼─────────┐
                     │ Queue        │  │  Postgres    │  │  Gemini   │
                     │ (Celery on   │  │  (RDS)       │  │   API     │
                     │  Redis)      │  │ +FTS/trigram │  └───────────┘
                     └──────┬──────┘  │ (pgvector    │
                            │         │  OPTIONAL,   │
                     ┌──────▼───────┐ │  added later)│
                     │  Worker       │ └──────────────┘
                     │ (one process, │      │
                     │  job-typed):  │  ┌───▼────┐  ┌─────────┐  ┌──────────┐
                     │ OCR·Gen·Export│  │ Redis  │  │  S3     │  │ Math OCR │
                     │ ·Embed(later) │  │(cache/ │  │(objects)│  │ (Mathpix │
                     └───────────────┘  │ locks) │  └─────────┘  │ /vision) │
                                        └────────┘               └──────────┘
```

Boundaries are **module interfaces inside one process**, not network services. The `ai` module is the only code that talks to the LLM vendor; the `retrieval` module is the only one that owns search (and, later, embeddings). Swapping vendor, search backend, or OCR provider is a localized code change behind a stable interface — the same isolation microservices would give, without the gRPC hops, service mesh, multi-language ops, and deploy coordination that a 100–10k-teacher product does not need. When a real metric justifies it (§18), a module is extracted into its own service with its interface unchanged.

---

## 7. Detailed Architecture

**Deployment shape.** One **modular monolith** application + one background **worker** process, both from the same codebase in one language (see §12 for the language decision). This is not a rejection of separation of concerns — it's enforcing it with *module boundaries* instead of *network boundaries*, which keeps the isolation while removing distributed-systems overhead the current scale doesn't warrant. Each module below is an in-process component with a typed interface; any one can later be lifted into its own service without touching its callers.

**Frontend (Next.js/React).** SSR marketing + authenticated SPA app (admin is a role-gated area of the same app, not a separate deployable). Blueprint builder, draft editor (with question locking and inline regenerate), template designer, analytics dashboards. Real-time job progress via SSE. Math rendering with KaTeX.

**HTTP layer (FastAPI).** Terminates auth (JWT verification, tenant resolution), enforces RBAC through the `Depends()` chain, validates requests with Pydantic models (`extra="forbid"`), applies rate limiting, and honours idempotency keys on mutating endpoints. Dispatches to modules by direct in-process calls.

**Domain modules.**
- *curriculum · questions · templates · papers · approvals · billing · analytics* — business logic and CRUD; emit domain events onto the queue (e.g., `paper.assemble.requested`, `textbook.uploaded`). `curriculum` also owns **syllabus versioning and rollover diffs** (§8).
- *flags* — feature-flag and kill-switch evaluation (§12.2); every AI/vendor-dependent capability resolves through it, so degradation is a config change rather than a deploy.
- *assembler* — the core module: takes a blueprint plan and produces a valid paper via constraint-satisfaction over the candidate pools (§4, §20). Owns quota math, joint difficulty/coverage/source balancing, relaxation ordering, and the compromise report.
- *retrieval* — owns search. At MVP: metadata pre-filter + Postgres `tsvector` full-text + `pg_trgm` trigram similarity, ranked lexically. **No ANN/embeddings at MVP.** The module interface returns scored candidates so an embedding/ANN backend can be slotted in behind it later with no caller changes.
- *ai* — the only code that calls the LLM vendor, structured as an internal **pipeline of swappable in-process stages** rather than one blob: **prompt builder** (versioned templates, §11) → **inference driver** (a provider-agnostic interface — Gemini today, other vendors behind the same contract, §12.1) → **schema validator** → **repair/regenerate loop** → **correctness verifier** (numeric execution for quantitative items, single-answer checks for MCQ) → **classifier** (difficulty/Bloom/competency — deterministic rules first, a Gemini Flash-tier model for the residual, §10). Each stage has a typed interface, so any one can be replaced or reordered without touching the others — this is the "each stage becomes replaceable" property, achieved in-process, no network hop. Enforces per-tenant token budgets, the cost cascade (§19.1), and prompt-injection defenses. Caches by content hash.

**Worker (async).** A single worker process that handles job types off the queue:
- *OCR/ingestion* — textbook → structured content (§, "Textbook Processing"); math → LaTeX via Mathpix / vision LLM (see data stores).
- *generation* — runs the assemble pipeline per paper job, as a Celery chain with a per-slot chord so a slot that fails verification retries alone.
- *export* — renders PDF (headless Chromium/`weasyprint`) and DOCX (`docxtemplater`).
- *embedding* — **only introduced if/when vector search is added**; (re)embeds new/edited questions.

**Workflow orchestration (multi-step jobs are explicit, not opaque).** Paper generation and textbook ingestion are *not* single atomic jobs — each is a directed sequence with fan-out, per-step retry, and partial-failure recovery: `plan → solve → (retrieve ∥ generate per slot) → verify → dedupe → answer-key/rubric → export`. Treating that as one big `generation` function loses the two things that matter most under load: (a) a slot that fails verification should retry *just that slot*, not re-run the whole paper; (b) a crash after 28 of 31 slots should resume, not restart. So the worker uses a **step orchestrator** with typed steps, per-step retry/backoff, idempotency keys, and progress events (which drive the SSE stream in §9). At MVP this is **Celery chains and chords** (parent/child tasks with dependencies) — zero new infrastructure beyond the Redis already in the stack. This is deliberately *not* a distributed workflow engine: **adopt Temporal (or equivalent) only when** steps must span services, span hours/days, or need durable timers/signals — none of which the MVP pipeline requires. The step boundaries are defined now so the escalation to Temporal later is a runner swap, not a rewrite.

**Data stores.**
- *PostgreSQL (RDS)* — relational source of truth; `tsvector` FTS + `pg_trgm` for retrieval and dedup at MVP; RLS for tenant isolation (from day one); partitioning for hot tables. `pgvector` is installed **only when a measured retrieval gap justifies embeddings** — it is not on the MVP critical path.
- *Redis* — cache (retrieval results, curriculum trees), distributed locks (assemble idempotency), rate-limit counters, job de-dupe.
- *S3* — textbook uploads, generated PDFs/DOCX, diagram images.
- *Vector store* — **deferred.** `pgvector` (HNSW) is the first step when embeddings are added; **Qdrant** only when embedding count crosses ~5–10M vectors (§18).
- *Math/diagram OCR* — **Mathpix** (or a vision LLM) for equation-heavy pages → LaTeX, plus layout-aware diagram cropping. Plain-text/table pages can still use Textract/Tesseract, but STEM textbooks (the target market) are equation- and diagram-dense, where forms-oriented OCR fails — so math OCR is the primary path, not the fallback (§10, §23).

**Async messaging.** **Celery on Redis** at MVP — one fewer managed dependency, and per-queue routing covers the ordering the pipeline needs. Tasks use `acks_late=True` plus idempotency keys, and durable state lives on the entity row rather than in the queue, so a broker failure is recoverable from PostgreSQL. DLQ per queue with alerting. SQS remains a swap behind the same task signatures if broker durability ever becomes the binding constraint.

---

## 8. Database Schema

PostgreSQL. All tenant-owned tables carry `org_id` and are protected by **Row-Level Security**. Types abbreviated. `uuid` PKs (`gen_random_uuid()`), `created_at/updated_at timestamptz`, soft delete via `deleted_at` where relevant.

```sql
-- ============ TENANCY & IDENTITY ============
CREATE TABLE organizations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  org_type text NOT NULL DEFAULT 'coaching_center'
    CHECK (org_type IN ('coaching_center','school','tutor','publisher')),
  region text NOT NULL DEFAULT 'ap-south-1',
  subscription_id uuid,
  settings jsonb NOT NULL DEFAULT '{}',
  status text NOT NULL DEFAULT 'active',
  created_at timestamptz NOT NULL DEFAULT now()
);

-- Optional: branch within an organization (e.g. coaching chain with multiple centers)
-- Leave branch_id nullable at MVP; add NOT NULL constraint when branch support is built.
CREATE TABLE branches (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL REFERENCES organizations(id),
  name text NOT NULL,
  region text,
  UNIQUE (org_id, name)
);

-- Optional: programs / batches — the org's OWN teaching structure.
-- Deliberately kept ORTHOGONAL to curriculum, not stacked on top of it. A coaching
-- institute's real hierarchy is Org → Branch → Program ("JEE Advanced 2027 Batch",
-- "NEET Dropper", "Class 10 Foundation") → the subjects that program teaches. That
-- is org-owned, tenant-editable data. Curriculum (board → class → subject → chapter
-- → LO) is board-owned reference data, shared across all tenants and versioned by
-- the board's calendar. Fusing them into one chain
-- (Org→Programs→Courses→Subjects→Chapters→Topics) would force every tenant to own a
-- private copy of CBSE's syllabus — destroying corpus sharing, multiplying the
-- ingestion cost by tenant count, and making a board revision an N-tenant migration.
-- So: programs REFERENCE curriculum subjects; they do not contain them.
CREATE TABLE programs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL REFERENCES organizations(id),
  branch_id uuid REFERENCES branches(id),        -- null = all branches
  name text NOT NULL,                            -- 'JEE Advanced 2027 Batch'
  exam_target text,                              -- 'JEE_ADV','NEET','CBSE_BOARD','UPSC_PRELIMS'
  academic_year text,
  status text NOT NULL DEFAULT 'active',
  UNIQUE (org_id, branch_id, name)
);
-- which curriculum subjects a program teaches (many-to-many, no duplication)
CREATE TABLE program_subjects (
  program_id uuid NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
  subject_id uuid NOT NULL REFERENCES subjects(id),
  PRIMARY KEY (program_id, subject_id)
);
-- teachers are assigned to programs; papers optionally belong to one
CREATE TABLE program_teachers (
  program_id uuid NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
  user_id uuid NOT NULL REFERENCES users(id),
  PRIMARY KEY (program_id, user_id)
);

CREATE TABLE users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email citext UNIQUE NOT NULL,
  password_hash text,               -- null when SSO-only
  full_name text NOT NULL,          -- ORM attribute `fullName` (ADR-012)
  status text NOT NULL DEFAULT 'active'
    CHECK (status IN ('active','disabled','suspended')),
  created_at timestamptz NOT NULL DEFAULT now()
);

-- a user can belong to multiple organizations with a role each
CREATE TABLE memberships (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id),
  org_id uuid NOT NULL REFERENCES organizations(id),
  branch_id uuid REFERENCES branches(id),   -- null = org-wide access
  -- Three implemented values. Constrained text, NOT a native ENUM, so adding a
  -- reserved value later is a CHECK-constraint migration rather than an ALTER TYPE
  -- that locks the table and cannot be reversed cleanly. (ADR-011)
  role text NOT NULL CHECK (role IN
    ('teacher','coaching_admin','super_admin')),
  -- Reserved for future use, deliberately NOT in the constraint yet:
  --   'hod'      — departmental approver; needs a department dimension that does not exist
  --   'reviewer' — approve-only; BR-6 already enforces author != approver behaviourally
  --   'viewer'   — read-only; no recorded customer requirement
  status text NOT NULL DEFAULT 'active'
    CHECK (status IN ('active','invited','suspended')),
  UNIQUE (user_id, org_id)
);
CREATE INDEX ON memberships (org_id, role);

-- ============ CURRICULUM ============
CREATE TABLE boards (            -- global reference data
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code text UNIQUE NOT NULL,     -- CBSE, ICSE, IB, IGCSE, STATE_XX
  name text NOT NULL
);
CREATE TABLE classes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  board_id uuid NOT NULL REFERENCES boards(id),
  name text NOT NULL,            -- "Class 10", "Grade 11"
  UNIQUE (board_id, name)
);
CREATE TABLE subjects (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  class_id uuid NOT NULL REFERENCES classes(id),
  name text NOT NULL,
  UNIQUE (class_id, name)
);
CREATE TABLE chapters (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  subject_id uuid NOT NULL REFERENCES subjects(id),
  seq int NOT NULL,
  title text NOT NULL,
  UNIQUE (subject_id, seq)
);
CREATE TABLE learning_outcomes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  chapter_id uuid NOT NULL REFERENCES chapters(id),
  code text,                     -- e.g. "10.SCI.2.3"
  description text NOT NULL
);
CREATE INDEX ON learning_outcomes (chapter_id);

-- ---- Curriculum versioning (syllabi change every year; questions must survive it) ----
-- Boards revise syllabi annually: CBSE drops chapters, merges units, renumbers.
-- Without versioning, a "deleted chapter" either orphans its questions or silently
-- keeps generating out-of-syllabus papers — both are correctness bugs, not cosmetics.
-- Version at SUBJECT-SYLLABUS granularity: a version owns a chapter list.
CREATE TABLE curriculum_versions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  board_id uuid NOT NULL REFERENCES boards(id),
  academic_year text NOT NULL,     -- '2026-27' (board's own label, not a date range guess)
  label text,                      -- 'CBSE 2026-27 rationalised syllabus'
  status text NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft','active','superseded')),
  effective_from date,
  source_ref text,                 -- circular/PDF the syllabus was digitised from
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (board_id, academic_year)
);

-- subjects and chapters belong to a version:
ALTER TABLE subjects ADD COLUMN curriculum_version_id uuid REFERENCES curriculum_versions(id);
ALTER TABLE chapters ADD COLUMN curriculum_version_id uuid REFERENCES curriculum_versions(id);
-- LINEAGE — the column that makes rollover cheap instead of catastrophic.
-- When 2027-28 is digitised, an unchanged chapter points at its 2026-27 ancestor,
-- so every question tagged to the old chapter is inherited by the new one via
-- lineage traversal. Only genuinely changed/dropped chapters need question review.
ALTER TABLE chapters ADD COLUMN derived_from_chapter_id uuid REFERENCES chapters(id);
CREATE INDEX ON chapters (derived_from_chapter_id);
CREATE INDEX ON chapters (curriculum_version_id, subject_id, seq);
```

**How versioning is actually used (the part that matters more than the DDL):**
- A paper request carries a `curriculum_version_id` (defaulted from the org's current academic year). Retrieval resolves chapters *within that version*, then widens to lineage ancestors for question sourcing — so a 2027 paper can reuse a 2026 question on a carried-over chapter, but never one from a dropped chapter.
- **Dropped chapters are `superseded`, never deleted.** Their questions stay queryable (a teacher may still be revising older batches) but are excluded from new-syllabus retrieval. Deleting board reference data destroys the audit trail on every historical paper that used it.
- Rollover is an **ingestion job, not a migration**: digitise the new circular → create version → auto-link unchanged chapters by title/LO similarity → a human confirms the diff. The diff report ("6 chapters carried, 2 merged, 1 dropped, 1 new") is the reviewable artefact.
- Competitive-exam syllabi (JEE/NEET/UPSC/SSC) use the same table with `academic_year` as the exam cycle. They change less often than boards but change less predictably, which is the same argument for versioning.

```sql

-- ============ QUESTIONS (unified + typed detail) ============
-- Single logical question table with a `source` discriminator keeps
-- retrieval uniform; source-specific fields live in jsonb `meta`.
CREATE TABLE question_bank (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid REFERENCES organizations(id),   -- null = global/shared corpus
  scope text NOT NULL DEFAULT 'global'
    CHECK (scope IN ('global','org','branch')),
  source text NOT NULL CHECK (source IN
    ('institute_bank','textbook','intext','previous_year','ai_generated')),
  board_id uuid REFERENCES boards(id),
  class_id uuid REFERENCES classes(id),
  subject_id uuid REFERENCES subjects(id),
  chapter_id uuid REFERENCES chapters(id),
  qtype text NOT NULL,           -- mcq, short, long, fill_blank, match, numerical, ...
  marks numeric(4,1) NOT NULL,
  difficulty text NOT NULL CHECK (difficulty IN ('easy','medium','hard')),
  bloom text CHECK (bloom IN
    ('remember','understand','apply','analyze','evaluate','create')),
  language text NOT NULL DEFAULT 'en',
  stem text NOT NULL,            -- question text (may contain LaTeX)
  options jsonb,                 -- for MCQ/match
  answer jsonb,                  -- answer key
  rubric jsonb,                  -- scoring rubric
  learning_outcome_ids uuid[] DEFAULT '{}',
  competency_ids uuid[] DEFAULT '{}',
  meta jsonb NOT NULL DEFAULT '{}',   -- year (PYQ), exercise_no (textbook), etc.
  content_hash text NOT NULL,    -- normalized-text hash for exact dedupe
  status text NOT NULL DEFAULT 'approved',  -- draft/approved/rejected
  created_by uuid REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  deleted_at timestamptz
);
-- Retrieval-critical composite index (metadata pre-filter path):
CREATE INDEX idx_qb_filter ON question_bank
  (board_id, class_id, subject_id, chapter_id, qtype, difficulty)
  WHERE deleted_at IS NULL;
CREATE INDEX idx_qb_source ON question_bank (source, subject_id);
CREATE INDEX idx_qb_hash ON question_bank (org_id, content_hash);
CREATE INDEX idx_qb_lo ON question_bank USING gin (learning_outcome_ids);
CREATE INDEX idx_qb_fts ON question_bank USING gin (to_tsvector('simple', stem));
-- Trigram index powers MVP lexical similarity + near-duplicate detection WITHOUT embeddings:
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX idx_qb_trgm ON question_bank USING gin (stem gin_trgm_ops);

-- Convenience views (or physical tables if fields diverge a lot):
CREATE VIEW textbook_questions AS SELECT * FROM question_bank WHERE source='textbook';
CREATE VIEW intext_questions   AS SELECT * FROM question_bank WHERE source='intext';
CREATE VIEW generated_questions AS SELECT * FROM question_bank WHERE source='ai_generated';
CREATE VIEW institute_bank_questions AS SELECT * FROM question_bank WHERE source='institute_bank';

-- ============ EMBEDDINGS (DEFERRED — add only when vector search is justified) ============
-- NOT on the MVP critical path. MVP retrieval uses the idx_qb_fts (tsvector) and
-- a pg_trgm index below. Create this table + extension only when a measured
-- retrieval-quality gap justifies ANN (see §10 step 2 / §18).
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE question_embeddings (
  question_id uuid PRIMARY KEY REFERENCES question_bank(id) ON DELETE CASCADE,
  model text NOT NULL,           -- embedding model id (for reindex safety)
  dimensions smallint NOT NULL,  -- actual stored width, so a change is detectable
  -- Gemini's embedding model emits 3072 dims natively and supports Matryoshka
  -- truncation. pgvector's HNSW/IVFFlat indexes cap at 2000 dims for `vector`,
  -- so we truncate to 1536 and re-normalise in the adapter. halfvec(3072) is the
  -- documented alternative (HNSW limit 4000). See ADR-008.
  embedding vector(1536) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_qe_ann ON question_embeddings
  USING hnsw (embedding vector_cosine_ops);

-- ============ TEXTBOOK INGESTION ARTIFACTS ============
CREATE TABLE textbooks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid REFERENCES organizations(id),
  subject_id uuid REFERENCES subjects(id),
  file_key text NOT NULL,        -- S3 key
  status text NOT NULL DEFAULT 'uploaded', -- uploaded/ocr/parsed/embedded/failed
  pages int,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE textbook_assets (   -- diagrams, tables extracted
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  textbook_id uuid NOT NULL REFERENCES textbooks(id),
  chapter_id uuid REFERENCES chapters(id),
  kind text NOT NULL,            -- diagram/table/activity
  s3_key text, data jsonb,
  page int
);

-- ============ PAPERS ============
CREATE TABLE templates (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid REFERENCES organizations(id),
  name text NOT NULL,
  layout jsonb NOT NULL          -- header, sections, marks col, instructions
);
CREATE TABLE generated_papers (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL REFERENCES organizations(id),
  created_by uuid NOT NULL REFERENCES users(id),
  program_id uuid REFERENCES programs(id),  -- optional: which batch this paper is for
  blueprint jsonb NOT NULL,      -- full config snapshot (reproducible)
  curriculum_version_id uuid REFERENCES curriculum_versions(id),  -- syllabus it was built against
  template_id uuid REFERENCES templates(id),
  title text NOT NULL,
  total_marks numeric(5,1),
  duration_min int,
  status text NOT NULL DEFAULT 'draft', -- draft/submitted/review/approved/rejected
  created_at timestamptz NOT NULL DEFAULT now()
) PARTITION BY RANGE (created_at);   -- monthly partitions
CREATE TABLE generated_papers_2026_07 PARTITION OF generated_papers
  FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');

CREATE TABLE paper_versions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  paper_id uuid NOT NULL REFERENCES generated_papers(id),
  label text NOT NULL,           -- "A","B","v2"
  content jsonb NOT NULL,        -- ordered question refs + inlined snapshots
  answer_key jsonb,
  pdf_key text, docx_key text,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (paper_id, label)
);

CREATE TABLE approvals (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  paper_id uuid NOT NULL REFERENCES generated_papers(id),
  reviewer_id uuid REFERENCES users(id),
  action text NOT NULL,          -- submit/approve/reject/comment
  comment text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON approvals (paper_id, created_at);

-- ============ BILLING / METERING / AUDIT / ANALYTICS ============
CREATE TABLE subscriptions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL REFERENCES organizations(id),
  plan text NOT NULL,            -- free/standard/pro/enterprise
  seats int, status text NOT NULL DEFAULT 'active',
  period_start date, period_end date,
  provider_ref text              -- Stripe subscription id
);
CREATE TABLE usage (             -- append-only metering
  id bigint GENERATED ALWAYS AS IDENTITY,
  org_id uuid NOT NULL,
  user_id uuid,
  metric text NOT NULL,          -- papers_generated, ai_tokens, ocr_pages
  qty numeric NOT NULL,
  ts timestamptz NOT NULL DEFAULT now()
) PARTITION BY RANGE (ts);
CREATE INDEX ON usage (org_id, metric, ts);

CREATE TABLE audit_logs (
  id bigint GENERATED ALWAYS AS IDENTITY,
  org_id uuid, actor_id uuid,
  action text NOT NULL, entity text, entity_id uuid,
  ip inet, meta jsonb,
  ts timestamptz NOT NULL DEFAULT now()
) PARTITION BY RANGE (ts);
CREATE INDEX ON audit_logs (org_id, ts);

CREATE TABLE analytics_daily (   -- rollups (populated by worker)
  org_id uuid, day date,
  papers int, ai_questions int, retrieved_questions int,
  avg_difficulty numeric, coverage jsonb,
  PRIMARY KEY (org_id, day)
);

-- ============ PROMPT REGISTRY (versioned prompts with eval scores) ============
-- Prompt *text* lives in packages/prompts (git-versioned, PR-reviewed). This table
-- records which version is ACTIVE per (task,model) and the eval score that promoted it,
-- so a bad prompt is one UPDATE to roll back — no redeploy — and every generated
-- question can be traced to the exact prompt version that produced it.
CREATE TABLE prompt_versions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  task text NOT NULL,            -- 'generate_mcq', 'classify_difficulty', 'synthesize_rubric', ...
  version int NOT NULL,          -- monotonic per task
  template_ref text NOT NULL,    -- path/hash into packages/prompts (source of truth)
  model text NOT NULL,           -- model id this version was tuned/evaluated against
  author uuid REFERENCES users(id),
  eval_score jsonb,              -- golden-set results: {correctness, coverage, dup_rate, schema_fail}
  status text NOT NULL DEFAULT 'candidate'  -- candidate/active/retired
    CHECK (status IN ('candidate','active','retired')),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (task, version)
);
-- Exactly one active version per (task, model):
CREATE UNIQUE INDEX idx_prompt_active ON prompt_versions (task, model)
  WHERE status = 'active';
-- Generated questions record their provenance for traceability/rollback audits:
--   question_bank.meta->>'prompt_version_id' references prompt_versions(id).
```

**Design notes.**
- **Unified `question_bank`** with a `source` discriminator (rather than five near-identical tables) keeps retrieval a single query path; the schema still exposes `textbook_questions` / `intext_questions` / `generated_questions` / `institute_bank_questions` as views to match the domain vocabulary.
- **Content ownership model.** Questions split into two layers by `scope`: `global` (platform-owned corpus — textbook, in-text, previous year; `org_id IS NULL`) shared across all tenants and curated once by the platform; and `org` (institute-private — institute bank, saved AI-generated; `org_id = X`) owned exclusively by one coaching center and invisible to others. A new tenant gets a usable product on day one from the global corpus without uploading anything. The `scope` column makes ownership explicit and leaves room for a future `branch` tier (chain-wide sharing within one org).
- **Isolation (from day one, never retrofitted).** RLS policy `USING (org_id = current_setting('app.org_id')::uuid OR org_id IS NULL)` — the `NULL` branch exposes the shared global corpus (boards, published textbooks, PYQs). RLS and the `org_id` column ship with the *first* tenant: adding tenant isolation later means revisiting every query and every test, and one missed filter is a cross-tenant breach. For coaching centers, whose proprietary question sets are their crown jewels, airtight isolation is also a sales requirement, not just a security control.
- **⚠️ Pooler footgun.** `current_setting('app.org_id')` must be set with **`SET LOCAL` inside the request transaction**, never a session-level `SET`. With **PgBouncer in transaction/statement pooling**, a session-level `SET` leaks the tenant context to whichever request borrows that backend next — a silent cross-tenant read. Rule: every request opens a transaction, issues `SET LOCAL app.org_id = $1`, does its work, commits. Add an isolation test that asserts the setting does not survive across pooled checkouts.
- **Indexes:** composite B-tree for the metadata pre-filter, GIN for FTS, trigram, and LO arrays. HNSW is added only with the deferred embeddings table.
- **Partitioning:** time-range on `generated_papers`, `usage`, `audit_logs` (high-volume append). Drop/archive old partitions to S3.
- **Optimization:** connection pooling (PgBouncer — see footgun above), read replicas for analytics, `pg_stat_statements` for hotspot tuning, materialized `analytics_daily`.

---

## 9. API Design (REST)

Base: `/api/v1`. Auth: `Authorization: Bearer <JWT>`. Tenant resolved from token. All mutations accept `Idempotency-Key`. Errors: RFC-7807 problem+json.

**Auth**
```
POST /auth/login            { email, password } → { accessToken, refreshToken }
POST /auth/oauth/callback   { provider, code } → tokens
POST /auth/refresh          { refreshToken } → { accessToken }
```

**Curriculum (read-heavy, cacheable)**
```
GET  /boards
GET  /boards/{id}/classes
GET  /classes/{id}/subjects
GET  /subjects/{id}/chapters?include=learning_outcomes
```

**Question bank**
```
POST /questions            (create)         body: {source, qtype, marks, ...}
POST /questions/import      (bulk CSV/DOCX)  → { jobId }
GET  /questions?subjectId&chapterId&source&qtype&difficulty&q=<text>&page
PATCH /questions/{id}
DELETE /questions/{id}
```

**Textbook ingestion**
```
POST /textbooks            { subjectId, fileName } → { textbookId, uploadUrl }  (presigned S3 PUT)
POST /textbooks/{id}/process → { jobId }
GET  /textbooks/{id}       → { status, pages, chaptersExtracted }
```

**Paper generation** — the core endpoint:
```
POST /papers
```
Request:
```json
{
  "title": "Class 10 Science - Unit Test 2",
  "boardId": "…", "classId": "…", "subjectId": "…",
  "chapterIds": ["…","…"],
  "learningOutcomeIds": ["…"],
  "examType": "unit_test",
  "pattern": {
    "sections": [
      { "name": "A", "qtype": "mcq",   "count": 10, "marksEach": 1 },
      { "name": "B", "qtype": "short", "count": 5,  "marksEach": 3 },
      { "name": "C", "qtype": "long",  "count": 3,  "marksEach": 5 }
    ]
  },
  "totalMarks": 40,
  "durationMin": 90,
  "difficulty": { "easy": 0.4, "medium": 0.4, "hard": 0.2 },
  "language": "en",
  "instructions": "All questions are compulsory.",
  "sourceDistribution": {
    "institute_bank": 0.10, "textbook": 0.40, "intext": 0.20,
    "previous_year": 0.20, "ai_generated": 0.10
  },
  "templateId": "…",
  "versions": ["A","B"]
}
```
Response (202 — async):
```json
{ "paperId": "…", "jobId": "…", "status": "assembling",
  "streamUrl": "/api/v1/papers/…/events" }
```

```
GET  /papers/{id}                 → { status, blueprint, versions:[…] }
GET  /papers/{id}/events          (SSE progress: retrieved 24/31, generating 7, validating…)
POST /papers/{id}/questions/{slot}/regenerate   { hint? } → replaces one slot
PATCH /papers/{id}/questions/{slot}             { action: "swap"|"lock"|"edit", ... }
POST /papers/{id}/versions        { labels:["C"] }
POST /papers/{id}/export          { format:"pdf"|"docx", includeAnswerKey:true } → { fileUrl }
```

**Approval workflow**
```
POST /papers/{id}/submit
POST /papers/{id}/review   { action:"approve"|"reject"|"comment", comment }
GET  /papers/{id}/history
```

**Analytics / billing / admin**
```
GET  /analytics/overview?from&to
GET  /analytics/coverage?subjectId
GET  /subscription
POST /admin/organizations          (super_admin)
GET  /admin/audit-logs?actorId&from&to
```

Sample success envelope:
```json
{ "data": { "...": "..." }, "meta": { "requestId": "…" } }
```

---

## 10. AI Pipeline (detailed)

**1. Grounding / RAG.** For generation slots, the AI module retrieves 3–8 exemplar questions (same board/class/subject/chapter/LO/type/difficulty) plus the chapter's learning outcomes and any style rules; these ground the model so generated questions match curriculum tone and scope. For quantitative slots the exemplar is also the **parametric template** (step 5b).

**2. Retrieval backend — start simple, earn the vector stack.** At MVP, retrieval is a **metadata pre-filter + Postgres `tsvector` full-text + `pg_trgm` trigram** ranking. Reasoning: every retrieval is hard-filtered by board/class/subject/chapter/type/difficulty, after which the candidate set is usually tens to low-hundreds of short questions — a regime where lexical + exact filtering matches ANN quality at a fraction of the operational cost (no embedding worker, no HNSW, no reindex-on-model-change, no Qdrant migration). **Add embeddings only when a measured gap appears** (e.g. paraphrase recall shortfalls surfaced by the eval suite in §20). When that happens: embed on create/edit, store the embedding model id per row for safe reindex, `pgvector` HNSW first, Qdrant beyond ~5–10M vectors.

**3. Metadata filtering.** Every retrieval **pre-filters** by board/class/subject/chapter/LO/type/difficulty/source first — both a quality and a cost lever (smaller candidate set), and the reason ANN is unnecessary early.

**4. Ranking / hybrid search.** MVP: lexical (`tsvector` rank + trigram similarity) over the filtered set; exact-terminology matches (formulae, named laws) fall out naturally. *When embeddings are added:* fuse lexical + vector ANN with **Reciprocal Rank Fusion** to also catch paraphrase/conceptual matches.

**5. Re-ranking.** Optional and deferred with vectors: when the fused candidate set is large, re-rank top ~50 by a cross-encoder (or an LLM-scored relevance for small sets). At MVP the filtered set is small enough that lexical rank + the assembler's balancing suffices.

**5b. Generation strategy (quantitative-first safety).** For numerical/quantitative slots, prefer **parametric generation**: take a known-correct retrieved item, vary its parameters within declared constraints, and recompute the answer deterministically — this yields unlimited fresh variants that are *correct by construction*. Only fall back to free-form RAG-grounded generation when no suitable template exists.

**6. Duplicate detection (context-aware).** Layered, and the meaning of "duplicate" depends on context — not one global threshold:
- *Exact* — `content_hash` on normalized text.
- *Surface variant* — trigram similarity (`pg_trgm`).
- *Concept overlap* — a **soft** signal (shared LO + similar keywords, or embedding cosine once vectors exist). This feeds the assembler's coverage balancing rather than hard-rejecting: within one paper, testing a concept twice may be intended; across Set A/Set B it must differ; against a teacher's recent papers it's a staleness nudge. Any similarity cutoff is **configurable and tuned on real data**, never a hardcoded magic number.

**7. Difficulty estimation.** Any untagged/AI question is classified into easy/medium/hard using rubric signals (steps required, cognitive load, vocabulary); reconciled against the plan's difficulty distribution. **Classifier tier (cost cascade, §19):** start with a **cheap local model** — a fine-tuned sentence-transformer (MiniLM) or small BERT head trained on tagged bank items — which is ~free per call, has no vendor latency, and runs in the worker. Fall back to Haiku only for low-confidence cases the local model punts on. Do *not* pay a frontier model to label difficulty; this is a supervised-classification problem, not a reasoning one, and the bank supplies the training labels.

**8. Bloom mapping.** Same classifier assigns a Bloom level (remember→create); the assembler nudges selection so the paper isn't all "remember".

**9. Competency mapping.** Questions are mapped to curriculum competencies/LOs; coverage report shows which LOs are represented.

**10. Correctness verification (not self-judgment).** Structured output guarantees *shape*, not *truth*, so correctness is checked independently:
- *Numerical/quantitative* — the worked solution is **executed** (sympy / a sandboxed code runner); the computed result must match the stated answer or the item is rejected and regenerated. Never trust the model to grade its own arithmetic.
- *MCQ* — assert **exactly one** option satisfies the key (no two-correct, no none-correct); distractors must be distinct.
- *Structural/pedagogical* — schema (§11), marks match section, no answer leakage in stem, language matches, reading level appropriate for class.
- *Qualitative (long/short)* — LLM-as-judge is **advisory only**, surfaced to the teacher with a confidence flag; the human approves. LLM-as-judge is never the sole gate for a correctness claim.

**11. Answer key.** Generated alongside each question (for AI) or pulled from the bank (for retrieved). For numericals, the *executed* worked solution + final answer; for MCQ, correct option + distractor rationale.

**12. Rubrics.** For long/short answers, a marks-band rubric ("full/partial/zero credit" criteria) synthesized (Sonnet) and stored in `rubric`.

**13. PDF generation.** Export worker renders the paper via a templated HTML → headless Chromium (PDF) and `docxtemplater` (DOCX); math via KaTeX server-side; diagrams pulled from `textbook_assets`/S3 (cropped during Mathpix/vision OCR, §7).

**14. The acceptance gate (one ordered pipeline, fail-fast).** The checks above are not ad-hoc — every AI-generated item passes through a single ordered gate before it is eligible to enter a paper, and a failure at any stage short-circuits to repair/regenerate rather than continuing. Ordered cheapest-and-most-decisive first:

```
generated item
  → 1. schema valid?         (JSON Schema, §11)         ─fail→ repair
  → 2. structural checks?    (marks=section, no answer leak in stem, language, options distinct)
  → 3. correctness verified? (execute numeric / single-correct MCQ, §10.10)  ─fail→ regenerate
  → 4. difficulty & Bloom match plan?  (classifier, §10.7–8; reconcile or re-slot)
  → 5. within scope?         (chapter/LO containment)
  → 6. not a duplicate?      (hash · trigram · soft concept, §10.6 — context-dependent)
  → 7. qualitative advisory  (LLM-as-judge flag for long/short — NON-blocking, surfaced to teacher)
  → 8. human approval        (draft status; teacher/HOD approves before reuse or publish)
  ACCEPTED → status='approved', provenance recorded (prompt_version_id, model)
```

Stages 1–3 are hard gates (machine-decidable, block on fail). Stages 4–6 reconcile or re-slot rather than reject. Stage 7 is advisory only — the reasons in §10.10 (LLM-as-judge is circular for correctness). Stage 8 is the human-in-the-loop: AI output is always a *draft* until approved. Each stage emits a structured result so the eval suite (§20) can measure pass rates per stage and per prompt version.

**Model tiering summary.** Extraction/classification → cheap local classifier or Haiku; generation/validation → Sonnet; hard reasoning/audits → Opus (rare). All calls are cached by `(prompt_hash, model)` and budgeted per tenant, and flow through the cost cascade (§19).

---

## 11. Prompt Engineering

**System prompt (stable, cached):**
```
You are an expert examination author for {board} curriculum, {subject}, {class}.
You write pedagogically sound, unambiguous, curriculum-aligned questions.
Rules:
- Output ONLY valid JSON matching the provided schema. No prose.
- Stay strictly within the given chapters and learning outcomes.
- Never include the answer inside the question stem.
- Match the requested difficulty and Bloom level.
- Use {language}. Render math in LaTeX ($...$).
- Reuse terminology from the provided exemplars; do not contradict them.
```

**Developer prompt (per-call constraints):**
```
Task: Generate {n} {qtype} question(s), {marksEach} marks each,
difficulty={difficulty}, bloomTarget={bloom}.
Chapters: {chapterTitles}. Learning outcomes: {loList}.
Exemplars (style + scope reference, do NOT copy): {exemplars}.
Avoid semantic duplicates of: {existingStems}.
Each question must include: stem, options?(mcq), answer, rubric, difficulty, bloom, loIds.
```

**Dynamic prompt builder (pseudocode):**
```
buildPrompt(slot, retrieved, plan):
  ctx = topK(retrieved, 5)                    // RAG exemplars
  avoid = paper.selectedStems + teacher.recentStems
  return {
    system: render(SYSTEM, plan.meta),
    developer: render(DEVELOPER, {slot, exemplars: ctx, existingStems: avoid}),
    tools: [{ name:"emit_questions", input_schema: QUESTION_SCHEMA }]  // forces structure
  }
```

**JSON Schema (structured output tool):**
```json
{
  "type": "object",
  "required": ["questions"],
  "properties": {
    "questions": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["stem","answer","difficulty","bloom","marks"],
        "properties": {
          "stem":       { "type": "string", "minLength": 5 },
          "qtype":      { "enum": ["mcq","short","long","fill_blank","match","numerical"] },
          "options":    { "type": "array", "items": { "type": "string" } },
          "answer":     {},
          "rubric":     { "type": "array", "items": {
                            "type":"object",
                            "required":["band","criteria","marks"],
                            "properties":{ "band":{"type":"string"},
                              "criteria":{"type":"string"},
                              "marks":{"type":"number"} } } },
          "difficulty": { "enum": ["easy","medium","hard"] },
          "bloom":      { "enum": ["remember","understand","apply","analyze","evaluate","create"] },
          "marks":      { "type": "number" },
          "loIds":      { "type": "array", "items": { "type": "string" } }
        }
      }
    }
  }
}
```
Using tool-use / structured output strongly biases the model toward schema-valid JSON and removes most brittle string-parsing. It is **not an absolute guarantee**: the application must still validate every response against the schema and handle the failure path (repair loop, then regenerate, then surface). Treat structured output as "shape is almost always right," not "shape cannot be wrong" — and remember it says nothing about whether the *content* is correct (that's the verification step, §10.10).

**Validation prompt (LLM-as-judge, used sparingly on flagged items):**
```
Given this question and its metadata, answer strictly as JSON:
{ "valid": bool, "issues": [string], "answerCorrect": bool,
  "difficultyMatches": bool, "withinScope": bool }
Question: {q}. Expected difficulty: {d}. Chapters/LOs: {scope}.
```

**Repair prompt:**
```
The following question failed validation: {issues}.
Return a corrected version as JSON matching the schema.
Change only what is necessary to fix the listed issues. Question: {q}.
```

**Regeneration prompt (teacher-triggered "give me another"):**
```
Generate a DIFFERENT question for the same slot (same type/marks/difficulty/LO),
semantically distinct from: {rejectedStem}. {optional teacher hint}.
```

**Prompt-injection defense.** Retrieved/OCR'd content is inserted only inside clearly delimited data blocks with an instruction that content within is *data, not instructions*; the system prompt asserts the model must ignore any instructions found inside exemplars or textbook text.

---

## 12. Technology Stack

| Layer | Choice | Rationale |
|---|---|---|
| Frontend | Next.js (React), TypeScript, Tailwind, KaTeX | SSR + SPA, fast, math rendering |
| Application | **Single modular monolith — FastAPI (Python)** | One deployable, module-boundary isolation. Chosen over NestJS: the workload is dominated by concurrent I/O waits (retrieval fan-out, generation polling, SSE, long AI calls) which Starlette + asyncio serves directly, and the verification/OCR/AI toolchain is Python-native — which also avoids the two-language split (ADR-006) |
| Modules | curriculum · questions · retrieval · assembler · ai · papers · billing · analytics | In-process modules with typed interfaces, wired by FastAPI `Depends()`; extract to a service later only on evidence |
| Worker | **Celery + Redis**, job-typed | OCR, generation, export; embedding job added only with vectors. Celery over `arq` on production track record — see ADR-006 |
| DB | PostgreSQL 16+ (+ `pg_trgm`, `citext`, `tsvector`) | Relational + FTS + trigram cover MVP retrieval and dedup with no vector DB |
| ORM / migrations | **SQLAlchemy 2.x (async) + Alembic** | Battle-tested; raw SQL inside revisions for RLS, partitions, and index DDL that autogenerate cannot model. Prisma's Python client was rejected on maturity (ADR-006) |
| Validation | **Pydantic v2** | One model validates the request and generates the OpenAPI 3.1 schema; `extra="forbid"` blocks mass assignment |
| Vector (deferred) | pgvector → Qdrant | Added only when a measured retrieval gap appears; Qdrant beyond ~5–10M vectors |
| Cache/locks | Redis | Cache, rate limit, locks; can also back the job queue early |
| Queue | **Celery on Redis** (+ DLQ) at MVP; SQS remains an option later | Reuses the Redis already in the stack and drops a dependency; Celery is the mature Python-native task queue (ADR-006) |
| Object store | S3 | Uploads, exports, assets |
| Math OCR | **Mathpix (or vision LLM) → LaTeX** for equations/diagrams; Textract/Tesseract for plain text/tables | STEM textbooks are equation/diagram-dense — forms-oriented OCR mangles math; math OCR is the primary path |
| LLM | **Gemini API** — Flash tier / Pro tier | Tiered by task; model IDs in config, never hardcoded (ADR-008) |
| Answer verification | sympy / sandboxed code runner | Execute worked solutions to check numerical answers — not LLM self-judgment |
| Embeddings (deferred) | **Gemini embedding model**, stored at 1536 dims (truncated from 3072) | Consistent, reindexable; introduced with vector search. 1536 keeps HNSW indexing legal (ADR-008) |
| Export | Headless Chromium (PDF), docxtemplater (DOCX), **behind an `ExportRenderer` interface** (§12.1) | Fidelity now; LaTeX/HTML added as one class each, not a worker fork |
| Feature flags | Postgres table + Redis cache, in-process evaluation (§12.2) | Kill switches without redeploy; no vendor SDK at MVP |
| Auth | JWT + refresh, OAuth/OIDC, SAML (enterprise) | Standard |
| Infra | Terraform, **ECS Fargate at MVP** (EKS only at scale), no Helm until K8s | IaC, portability, minimal ops |
| Observability | OpenTelemetry, Prometheus, Grafana, Loki, Sentry | Traces/metrics/logs/errors |
| CI/CD | GitHub Actions (+ Argo CD once on K8s) | Build/test + GitOps deploy |
| Payments | Stripe | Subscriptions/metering |

### 12.1 Extension points (where the stack is deliberately swappable)

Three parts of this stack will change — vendors get deprecated, customers demand formats, models get cheaper. Those three get a **typed interface at the boundary**; everything else is allowed to be concrete. This is "plugin architecture" scoped to where it earns its keep, not applied everywhere as ceremony. All three are in-process strategy interfaces (a registry + implementations), *not* dynamically loaded external plugins — there is no third-party plugin ecosystem to support, so a dynamic loader would buy nothing and cost sandboxing, versioning, and a stable public API forever.

**1. AI provider (`InferenceDriver`)** — the interface the `ai` module's inference stage calls (§7):
```
InferenceDriver:
  complete(systemPrompt, messages, schema, opts) → { content, usage, finishReason }
  supportsPromptCaching / supportsStructuredOutput / contextWindow / costPerMTok
```
Implementation: **Gemini (the only one built)**. Other vendors and local runtimes can implement the same contract later, e.g. for on-prem tenants that require no egress. **Each provider carries its own prompt versions and its own eval baseline** (§11) — the interface makes a provider swap *possible*, it does not make it free. Failover is pipeline-wide and deliberate, not per-call price shopping (see §19.1).

**2. Export format (`ExportRenderer`)**:
```
ExportRenderer:
  format: 'pdf'|'docx'|'latex'|'html'|...
  render(paper, template, opts) → { stream, mimeType, filename }
```
PDF (Chromium) and DOCX (docxtemplater) ship at MVP. **LaTeX and HTML are the reason this interface exists**: LaTeX is a genuine ask from serious STEM institutes and from anyone wanting print-shop-grade math, and HTML is the path to online tests later. All renderers consume the same normalized paper document + template, so adding a format is one class, not a fork of the export worker.

**3. OCR provider (`DocumentExtractor`)** — Mathpix today, vision-LLM or Textract behind the same `extract(page) → { latex, text, assets, confidence }` contract. Phase-0 spikes (§21) exist precisely to pick the default, and the interface means that choice isn't permanent.

### 12.2 Feature flags & kill switches

Flags are **infrastructure, not an admin CRUD screen** (FR-19 describes the UI; this describes the mechanism). Every AI-dependent and vendor-dependent capability is individually disableable **without a redeploy**, because in an AI product the failure mode isn't "the server is down" — it's "diagram questions started producing garbage" or "Mathpix is rate-limiting us." Both need a 30-second response, and a rollback of the whole release is the wrong instrument.

- **Storage.** A `feature_flags` table (`key`, `scope`, `org_id?`, `enabled`, `variant`, `rollout_pct`) fronted by Redis with short TTL + pub/sub invalidation. Evaluated in-process, sub-millisecond, no vendor SDK — LaunchDarkly-class tooling is a later swap behind the same `flags.enabled(key, ctx)` call, not an MVP dependency.
- **Resolution order.** `org override → plan tier → percentage rollout → global default`. Every evaluation is attributable in logs (`flag_key`, `resolved_from`) so "why did this tenant see that?" is answerable.
- **Kill switches (the point).** `ai.generation`, `ai.diagram_questions`, `ai.classifier.local`, `ocr.mathpix`, `export.docx`, `retrieval.vector`, per-provider `ai.provider.<name>` — each flips to off/fallback independently. Flipping `ai.generation` off degrades to retrieval-only papers with honestly-marked gaps (rung 7, §19.1); flipping `ai.classifier.local` off falls back to Haiku; flipping `ocr.mathpix` off queues ingestion instead of failing it. **Every kill switch has a defined degraded behavior — a flag with no fallback path is just an outage with extra steps.**
- **Release use.** New prompt versions, new question types, and the pgvector cutover ship dark → internal org → percentage rollout → default-on, with the eval suite (§20) gating each step and the prompt registry (§8) giving a second, independent rollback lever.
- **Discipline.** Flags have an owner and a removal date; stale flags are technical debt with a combinatorial blast radius. Permanent flags are only the kill switches and plan-tier entitlements — everything else is deleted once it's fully rolled out.

---

## 13. Folder Structure (monorepo)

```
papergen/
├─ backend/                # Python workspace root (own pyproject.toml, own venv)
│  ├─ src/
│  │  ├─ main.py           # create_app(): FastAPI — the ONLY internet-facing backend process
│  │  ├─ modules/{auth,tenancy,curriculum,questions,retrieval,assembler,
│  │  │    ai,papers,templates,approvals,billing,analytics,flags,admin}/
│  │  │    # each module = typed interface + impl; NO network boundary between them
│  │  └─ workers/          # Celery worker entry points, job-typed.
│  │     └─ jobs/{ocr,generation,export,embedding}/   # embedding added later
│  ├─ packages/
│  │  ├─ config/           # Pydantic Settings — SERVER ONLY, raises on invalid boot config
│  │  ├─ domain/           # pure rules: BR-1…BR-11, marks math, the solver. ZERO I/O.
│  │  ├─ prompts/          # versioned prompt templates
│  │  └─ db/               # SQLAlchemy models, Alembic revisions, RLS policies, seeds
│  └─ pyproject.toml
├─ frontend/               # Node workspace root (own package.json, own lockfile)
│  └─ src/                 # Next.js frontend (app + role-gated admin area).  NODE.
├─ shared/                 # Cross-cutting non-code-execution assets
│  ├─ contracts/           # OpenAPI spec, Pydantic/Zod request/response schemas
│  └─ docs/                # Shared documentation
├─ infra/
│  └─ terraform/           # VPC, ECS Fargate, RDS, Redis, S3, IAM, CF
│                          # (EKS/Helm added only at the scale that needs it)
├─ .github/workflows/      # CI pipelines
└─ docs/                   # ADRs, runbooks, this blueprint
```

Note: `retrieval` and `ai` are **modules inside `backend/src/modules/`**, not separate services. The internal `sdk` package disappears — modules call each other directly through their interfaces, wired by `Depends()`.

`frontend/` is a Node workspace; `backend/` is a Python workspace containing both the FastAPI API and the Celery workers. They are **physically separate workspace roots** ([ADR-015](ADR/ADR-015-workspace-root-split.md)) — they share a repository, not a build graph, a runtime, or a log stream, which is what makes the frontend/backend boundary physical rather than a lint rule. If load metrics later justify it, a module is lifted into its own deployable with the interface unchanged and an `sdk` reintroduced for just that hop.


---

## 14. Security

- **AuthN.** JWT access (15 min) + rotating refresh (httpOnly, revocable); OAuth/OIDC (Google/Microsoft); SAML for enterprise SSO. MFA optional for admins.
- **AuthZ.** RBAC enforced in the FastAPI `Depends()` chain (`get_current_user` → `get_org_context` → `require_roles`) *and* at the DB via RLS (defense in depth); every query is tenant-scoped by `org_id`, set with `SET LOCAL app.org_id` inside the request transaction.
- **Encryption.** TLS 1.2+ everywhere; RDS/S3/EBS encrypted with KMS CMKs; field-level encryption for PII if required by tenant.
- **Secrets.** AWS Secrets Manager / SSM Parameter Store; no secrets in env files or images; rotation enabled; IRSA (IAM Roles for Service Accounts) so pods get scoped creds, no static keys.
- **Rate limiting.** Per-user and per-tenant token buckets in Redis at the API edge; separate stricter limits on generation/export/OCR endpoints; per-tenant AI token budgets checked at cost-cascade rung entry, before spend.
- **Prompt-injection defense.** Untrusted content (OCR text, retrieved exemplars, teacher instructions) is sandboxed in delimited data blocks; system prompt forbids executing instructions found in data; generated output re-validated before storage; no tool/system access is exposed to the model beyond structured emit.
- **Content safety.** Output moderation pass on generated questions; profanity/PII filters.
- **Audit logs.** Append-only `audit_logs` for auth, RBAC changes, content edits, exports, admin actions; immutable retention (S3 with object lock for enterprise).
- **App security.** Input validation (Pydantic, `extra="forbid"`), output encoding, parameterized queries only (SQLAlchemy expression language — no f-string SQL), CSRF protection on cookie flows, security headers/CSP, dependency scanning (`pip-audit` for Python, `npm audit` for the frontend), SBOM.
- **Compliance posture.** GDPR + India DPDP: data residency pinning, right-to-erasure workflow, DPA with subprocessors; SOC 2-aligned controls.

---

## 15. Deployment (AWS reference)

```
                          MVP (ECS Fargate — no Kubernetes)
Route53 ─► CloudFront ─► ALB ─► ECS Fargate (private subnets)
                                  ├─ app     (1–2 tasks, autoscale on CPU/conc.)
                                  └─ worker  (1 task, autoscale on queue depth)
Fargate ─► RDS PostgreSQL (Multi-AZ) [private]   # +pg_trgm/tsvector, no vector DB
Fargate ─► ElastiCache Redis (Multi-AZ) [private]
Fargate ─► ElastiCache Redis = Celery broker + DLQ  (SQS remains a later swap)
Fargate ─► S3 (uploads/exports)  via VPC endpoint
Fargate ─► Mathpix / Secrets Manager / KMS / CloudWatch
Egress ─► NAT ─► Gemini API (allowlisted)

                          At scale (10k+) — migrate to EKS only if needed
EKS ─► app/worker Deployments + HPA/KEDA ─► RDS (+replicas), Redis cluster, Qdrant
```

- **Compute.** **ECS Fargate at MVP** — one `app` service + one `worker` service, no Kubernetes, no Helm, no KEDA. Worker autoscales on SQS/queue depth; `app` on CPU + concurrency. Move to EKS only when multi-service topology and fine-grained autoscaling actually pay for their operational cost (§18).
- **Data.** RDS PostgreSQL Multi-AZ + read replica; PITR; automated snapshots. ElastiCache Redis Multi-AZ.
- **Networking.** VPC with public (ALB/NAT) and private (compute/data) subnets; security groups least-privilege; VPC endpoints for S3/SQS/Secrets to avoid NAT cost.
- **Edge.** CloudFront for static + signed URLs for exports; WAF on ALB/CF.
- **IAM.** Task roles (Fargate) / IRSA (EKS) per service; least-privilege policies; no long-lived keys.
- **Regions.** Deploy per data-residency requirement (e.g., `ap-south-1`, `eu-west-1`).

**Cost estimates** (order-of-magnitude, USD/month, infra only; AI usage billed separately):

| Tier | Teachers | Compute | DB+Redis | Storage/CDN | AI (est.) | **Infra total** |
|---|---|---|---|---|---|---|
| Small (MVP) | ~100 | $80–120 (2× Fargate small) | $150 (RDS t-class MAZ) | $30 | see note ↓ | **~$250–500** |
| Medium | ~10k | $1.2k (EKS spot mix) | $1.5k (RDS r-class + replica) | $300 | $500–2k | **~$3.5–6k** |
| Enterprise | ~100k+ | $8–15k | $8–12k (sharded/large) | $2k | $5–20k | **~$25–50k+** |

**AI cost is a curve, not a constant.** At steady state, retrieval-first keeps generated questions to ~10–20% of volume, most classification runs on Haiku, and results are cached — so AI stays a minority line (~$50–150/mo at 100 teachers). **But at cold start every new coaching center has an empty institute bank and generates 60–90% of its questions**, so onboarding-heavy months can run 3–5× the steady-state AI figure. Plus one-time corpus-build costs: Mathpix/vision OCR of seed textbooks (equation-dense pages are the expensive ones) and PYQ ingestion. Model the first 1–2 months per tenant as AI-heavy and let it decline as their bank fills.

---

## 16. CI/CD

- **CI (GitHub Actions).** On PR: lint, typecheck, unit tests, build, container scan (Trivy), SAST, schema/contract tests, ephemeral preview env for the web app. Migrations validated against a throwaway Postgres.
- **CD (Argo CD / GitOps).** Merge to `main` → build+push images → update Helm values in the env repo → Argo syncs to `staging`. Promotion to `prod` via PR approval. **Blue/green or canary** for API/AI services; DB migrations gated (expand-migrate-contract pattern, backward compatible).
- **Prompt & schema versioning.** Prompts and JSON schemas live in `packages/`; changes go through the same PR + eval gate (a regression suite of golden generations) before deploy.
- **Rollback.** Argo one-click rollback; images immutable and tagged by SHA.

---

## 17. Monitoring & Observability

- **Metrics (Prometheus/Grafana).** RED per service (rate/errors/duration); queue depth + age; generation success rate, retrieval-vs-generated ratio, dedupe rejection rate; AI tokens/cost per tenant; DB connections, replication lag; cache hit ratio.
- **Tracing (OpenTelemetry).** End-to-end trace from `POST /papers` through retrieval → AI → validation → export, so slow slots are attributable.
- **Logging (Loki).** Structured JSON, `requestId` + `orgId` + `jobId` correlation; PII-scrubbed.
- **Errors (Sentry).** Frontend + backend exceptions with release tagging.
- **SLOs & alerts.** p95 latency, availability, DLQ non-empty, AI error/refusal spike, cost-budget breach → PagerDuty. Dashboards per tenant for enterprise.
- **AI-specific.** Track hallucination/validation-failure rate, repair-loop frequency, schema-mismatch rate; sample generations for human QA.

---

## 18. Scaling (100 → 1M teachers)

| Stage | Teachers | Strategy |
|---|---|---|
| **100** | Single region, **ECS Fargate, modular monolith + one worker**, 1 RDS Multi-AZ (FTS/trigram, no vector DB), Redis single. Vertical + light autoscale. No Kubernetes. |
| **10k** | Scale the monolith horizontally (more Fargate tasks); RDS read replicas, PgBouncer (transaction pooling — mind the `SET LOCAL` rule, §8), Redis cluster, CDN caching for curriculum. Add pgvector **only if** the eval suite shows a retrieval gap. Per-tenant rate limits + AI budgets. Move to EKS only if service-level autoscaling is genuinely needed. |
| **100k** | Extract hot modules (retrieval/ai) into their own services **on evidence**. Move vectors to **Qdrant** cluster (if adopted). **Shard/partition** Postgres by `org_id` (Citus or app-level tenant sharding); analytics to a warehouse (Redshift/ClickHouse). Multi-region active-passive. Aggressive caching + read replicas. Provisioned + spot mix. |
| **1M** | **Cell-based architecture**: tenants sharded into independent "cells" (own DB/vector/queue) behind a routing layer — blast-radius isolation + near-linear scale. Multi-region active-active with region-pinned tenants. Batch/off-peak generation, dedicated AI throughput (provisioned capacity), tiered storage, per-cell autoscaling. |

Cross-cutting: the monolith and its worker scale horizontally (they're stateless); state (DB/vector/object) scales via sharding/partitioning/replicas; heavy work is always async so spikes queue rather than fail. **Every arrow past stage 100 is triggered by a measured limit, not built up front** — the whole point of starting as a monolith is to earn each piece of distribution.

### 18.1 Deferral register — patterns deliberately NOT adopted, and what would change that

Each of the following is a real pattern with a real benefit. Each is also, at this product's stage, a cost paid now for an option exercised later — and in several cases the *valid kernel* of the idea is already implemented by something simpler. Recording them here with an explicit trigger is the point: "not yet" is only an engineering position if it names the evidence that would flip it. Without the trigger it's just an opinion, and the pattern gets adopted eighteen months later in a panic, or never.

| Pattern | Valid kernel (why it's tempting) | What we do instead, now | **Adoption trigger** |
|---|---|---|---|
| **Full event-driven architecture** (event bus, choreography, event sourcing) | Decoupling; audit trail; replay; async fan-out | We already have the parts that pay: **domain events on a queue** (§7) for async work, **append-only `audit_logs`** for the audit trail, and a **blueprint snapshot** on every paper for reproducibility. What we skip is *choreography* — services reacting to each other's events with no owner of the flow. Our multi-step flows are **explicitly orchestrated** (Celery chains/chords, §7), which for a 7-step pipeline is more debuggable, not less. | A third-party integration needs our events (webhooks/LMS sync), **or** ≥3 independent consumers need the same event, **or** cross-service sagas appear. Then: outbox table → real broker. Choreography only if orchestration is measurably the bottleneck, which it will not be. |
| **OCR as its own service** | Heavy, spiky, different scaling profile from the API; Python-native tooling; isolate a crashy dependency | OCR is already a **separate job type on a separate worker process** with its own queue and its own autoscaling signal (queue depth). It does not share a request path with the API, cannot block it, and scales independently *today*. The only thing a separate deployable adds is a network hop and a second CI pipeline. | OCR needs **different hardware** (GPU for a local vision model) or a **different language runtime** than the app, **or** its scaling is fighting the other job types for worker slots. Both are real possibilities — this is the most likely first extraction. |
| **Dedicated search engine** (Meilisearch / OpenSearch / Elasticsearch) | Correct: **a vector DB is not a search engine.** BM25, faceting, typo tolerance, synonyms, and analyzers are real capabilities Postgres FTS approximates crudely. | Postgres `tsvector` + `pg_trgm` over a **hard-prefiltered candidate set of tens to low hundreds** (§10.2–10.3). At that cardinality the ranking function barely matters — the metadata filter has already done the discrimination. A search cluster here would be a second stateful system to run, sync, and reindex for a set small enough to sort in memory. | Teacher-facing **free-text search over the whole bank** becomes a primary workflow (not slot-filling retrieval), **or** the post-filter candidate set routinely exceeds ~10k, **or** the eval suite (§20) shows lexical recall — not paraphrase recall — as the failure mode. Meilisearch first (single binary); OpenSearch only at genuine scale. Note this is a *different* trigger from the pgvector one: lexical shortfall → search engine, semantic shortfall → embeddings. |
| **Knowledge graph** (concept/prerequisite graph) | Genuinely the most interesting item on the list. Prerequisite chains enable real capabilities: "this question needs Ohm's Law, which needs Current" → diagnostic papers, remediation paths, true cognitive-load estimation. | A **flat LO/competency tagging model** with array membership (`learning_outcome_ids`, `competency_ids`) plus chapter lineage (§8). This supports coverage reporting and scope containment — which is what paper *assembly* actually needs. Prerequisite edges are needed for adaptive/diagnostic products, which we do not ship. | Adaptive testing, diagnostic remediation, or learning-path features enter the roadmap (§22 lists them as future). Then: add a `concept_edges` table (`from_concept`, `to_concept`, `relation`) in Postgres with recursive CTEs — **not** a graph database. Neo4j earns its place at millions of edges with deep multi-hop traversal, which a per-subject prerequisite DAG will not reach. The hard part is authoring accurate edges (a curriculum-expert cost), not storing them. |
| **Microservice decomposition** | Independent scaling/deploy; team autonomy | Modular monolith with typed module interfaces and contract tests (§20) that pin the shapes, so extraction is mechanical. | A module's scaling profile genuinely diverges (§18, 100k row), or team count makes deploy contention real. |
| **Distributed workflow engine** (Temporal) | Durable timers, signals, multi-day workflows | Celery chains/chords with typed steps, per-step retry, idempotency keys (§7). | Steps span services, span hours/days, or need durable timers/signals. |
| **Vector database** | Paraphrase/semantic recall | FTS + trigram over prefiltered sets (§10.2). | Eval suite shows a **paraphrase** recall gap → pgvector HNSW → Qdrant past ~5–10M vectors. Recorded as ADR-007. |

The through-line: every row names a **measurement**, not a date or a headcount. If none of the triggers fire, none of these get built, and that is a successful outcome rather than a deferred obligation.

---

## 19. Cost Estimation

Covered quantitatively in §15. Levers, in priority order:
1. **Retrieval-first** — the single biggest AI-cost control; generated questions are the exception, not the rule.
2. **Model tiering** — deterministic rules or the Gemini Flash tier for classification/extraction, the Gemini Pro tier for generation, extended-reasoning escalation only for audits.
3. **Caching** — prompt caching (stable system prompts), result caching by content hash, curriculum caching in Redis/CDN.
4. **Spot + KEDA** — workers on spot, scaled to zero when idle.
5. **VPC endpoints** — cut NAT data-transfer cost.
6. **Partition archival** — cold papers/logs to S3 Glacier.
7. **Per-tenant budgets** — hard ceilings prevent runaway spend and map cleanly to plan tiers.

### 19.1 The AI cost cascade

Cost control is not one lever applied globally — it is an **ordered cascade evaluated per request**, implemented inside the `ai` module (§7) so no caller can bypass it. Each rung is tried before the next; the first that satisfies the request wins:

```
request for a question/classification
 ├─ 0. EXACT CACHE      content hash · (prompt_hash, model) → stored result        ~$0
 ├─ 1. BANK REUSE       retrieval from question_bank (org + shared + PYQ)          ~$0
 ├─ 2. PARAMETRIC       vary params of a verified template, recompute answer       ~$0   ← quantitative
 ├─ 3. RULES/LOCAL      deterministic classifiers for difficulty · Bloom · dedup   ~$0   ← classification only
 ├─ 4. GEMINI FLASH     extraction, low-confidence classification, repair            $
 ├─ 5. GEMINI PRO       actual question generation, rubric synthesis                 $$
 ├─ 6. ESCALATION       Pro tier + extended reasoning — only on repeated
 │                      verification failure, or scheduled audits                     $$$
 └─ 7. DEGRADE          budget exhausted / provider down → serve retrieval-only,
                        mark slots "needs manual authoring", never hard-fail        —
```

Notes on why the order is this way, not the obvious one:
- **Rungs 0–3 are free and cover the majority of calls.** Most AI spend in this product is not creative generation; it's classification and repetition. Pushing those to cache/bank/parametric/local is where the money actually is — model choice at rung 5 is a smaller lever than never reaching rung 5.
- **Rung 2 before any LLM.** A parametric variant is both cheaper *and* more trustworthy than a generated one (correct by construction, §10.5b). Cost and quality point the same direction here — take it.
- **Multi-vendor is not a rung.** Per-call vendor price shopping is a false economy: frontier-tier prices track each other, and switching vendors mid-pipeline changes the output distribution enough to break prompts tuned on one model and invalidate eval baselines. The provider abstraction (§12) exists for **availability and negotiating leverage**, not for cost routing. A provider change is a deliberate pipeline-wide switch with its own prompt versions and its own eval run, never question-by-question.
- **Rung 7 is a first-class outcome.** A budget-exhausted tenant gets a partially-retrieved paper with honest gaps, not a 500. This is the same graceful-degradation path the kill switches in §12.2 use.

**Enforcement.** Per-tenant token budgets are checked at rung entry (not after spend); the `ai` module emits `ai_cost_usd` and `cascade_rung` per call to the metrics in §17, so the cascade's effectiveness is observable — a rising share of rung-5/6 calls is an alert, not a surprise on the invoice.

**Cold start caveat.** Rungs 1–2 depend on a populated bank, so a brand-new tenant skips them and lands on rung 5 for most slots. This is the curve described in §15, and it is the reason seed corpora and PYQ ingestion are onboarding work, not nice-to-haves.

---

## 20. Testing

- **Unit** — **assembler as a constraint solver** is the priority target: joint feasibility (does a valid assignment exist?), quota math, difficulty/coverage/source balancing, the documented relaxation order when infeasible, and correctness of the compromise report. Property-based tests over randomized pools (does the solver always return either a valid paper or an honest "infeasible + why"?). Plus dedupe, RBAC guards, schema validators. Target 80%+ on core.
- **Answer-correctness** — for numerical/quantitative items, execute the worked solution and assert it equals the stated answer; for MCQ, assert exactly one option is correct. These run on generated items in CI against a fixture set of blueprints, and gate the generation pipeline.
- **Contract** — JSON-schema contract tests on the module interfaces (`retrieval`, `ai`, `assembler`) so a later extraction to a network service can't drift the shape. OpenAPI tests on the public API.
- **Integration** — DB (with **RLS on, plus the pooled-connection isolation test** from §8), retrieval quality on a seeded corpus, queue/worker round-trips.
- **AI evals** — golden-set regression: fixed blueprints → assert coverage, difficulty distribution within tolerance, zero schema failures, no duplicates, no answer leakage, **and answer-correctness pass rate above threshold**. Also track paraphrase recall — the metric that decides *whether embeddings are ever needed* (§10.2). Run in CI on prompt/schema/model changes. LLM-as-judge for quality scoring with human spot-checks.
- **E2E** — Playwright: full teacher flow (configure → feasibility gate → assemble → edit → export → approve).
- **Load** — k6 against generation + retrieval endpoints; verify queue backpressure and autoscaling.
- **Security** — SAST/DAST, dependency scan, prompt-injection test suite (adversarial exemplars/OCR), RLS isolation tests (tenant A cannot read tenant B, including across pooled connections).
- **Chaos/DR** — periodic restore-from-backup drills; kill-worker/kill-AZ game days.

---

## 21. Roadmap

| Phase | Duration | Scope | Key risks | Dependencies |
|---|---|---|---|---|
| **Phase 0 — Spikes** | ~1–2 wks | **De-risk before building:** (1) Mathpix/vision OCR on 2–3 real STEM textbooks — measure LaTeX + diagram quality; (2) assembler solver prototype on synthetic pools; (3) numeric answer-verification harness. | Wrong OCR tool; assembler infeasibility surprises | Sample textbooks; seed questions |
| **MVP** | ~10–12 wks | Auth+RBAC, **RLS multi-tenancy from day one**, curriculum, question bank CRUD, **assembler (constraint solver + feasibility gate + compromise report)**, retrieval via FTS/trigram (bank+textbook+PYQ), AI gap-fill with **numeric verification + parametric generation**, PDF export | Retrieval quality on thin corpora; assembler edge cases | Phase 0 results; seed curriculum + corpus |
| **Beta** | +6–8 wks | Math OCR ingestion pipeline at scale, dedupe tuning, difficulty/Bloom tagging, DOCX, templates, approval workflow, basic analytics, tenant onboarding tooling | Ingestion edge cases; cold-start AI cost | Mathpix; onboarding flow |
| **Production (GA)** | +6–8 wks | Subscriptions/metering, admin portal, versions A/B/C, rubrics, full observability, autoscaling, security hardening, SOC2-track | Scale/perf under real load; cost control | Stripe; monitoring stack |
| **Scale/Enterprise** | +8–12 wks | SSO/SAML, data residency, **vector search + Qdrant (only if evals justify)**, module→service extraction, sharding, audit exports, SLAs, dedicated cells | Data migration; multi-region complexity | Measured need; sharding decision |
| **AI Enhancements** | ongoing | Adaptive difficulty, embeddings/rerankers if warranted, competency analytics, OCR answer-sheet grading | Model drift; eval maintenance | Eval infra from §20 |

**Note the reordering vs. a conventional plan:** RLS tenancy and the assembler-as-solver move *into* MVP (retrofitting isolation is a breach risk; the assembler is the core IP), while vector search moves *out* to Scale (it's earned by evals, not assumed). Phase 0 spikes exist because the two biggest technical unknowns — math OCR quality and assembler feasibility — are cheap to test and expensive to get wrong late.

---

## 22. Future Enhancements

- **Adaptive exams** — item-response-theory difficulty targeting per student cohort.
- **OCR answer checking** — scan handwritten answer sheets, auto-grade against rubric (human-in-loop).
- **Diagram generation** — programmatic/AI diagrams for geometry, circuits, biology.
- **Math rendering** — richer LaTeX/MathML authoring and validation.
- **Learning analytics** — per-LO mastery, item difficulty calibration from real results.
- **AI tutor** — student-facing practice from the same corpus.
- **Voice input** — dictate blueprint/instructions.
- **Multilingual translation** — generate/translate papers across regional languages with terminology consistency.

---

## 23. Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| AI **wrong answers** (not just hallucination) | Loss of trust — the #1 product risk | **Verify by computation** (execute numeric solutions), single-correct MCQ checks, parametric generation for quantitative items; retrieval-first; human review workflow; correctness evals in CI. LLM-as-judge is advisory, never the sole gate. |
| **Cold-start AI cost** (empty institute bank → 60–90% generated early) | Margin, esp. during onboarding waves | Budget AI as a per-tenant declining curve; seed global corpus aggressively pre-launch; per-tenant budgets/alerts; prompt + result caching |
| **Coaching-class data trust** (won't upload proprietary Q-sets to shared SaaS) | Adoption/sales blocker | Airtight RLS isolation as a *sales feature*; clear "your bank is private, invisible to competitors" contract; never train shared models on tenant-private content |
| **Math/diagram OCR failure** on STEM textbooks | Corpus quality → undermines retrieval-first thesis | **Mathpix/vision LLM → LaTeX** as primary path (not Textract); Phase-0 spike on real books before committing; human correction UI; confidence flags |
| **Assembler infeasibility** (constraints can't jointly be met) | Bad papers slip through / silent compromise | Feasibility gate runs a real dry-run solve; documented relaxation order; explicit compromise report; property-based solver tests (§20) |
| Thin corpus per organization → over-reliance on AI | Quality/cost | Feasibility gate; shared global corpus; onboarding content import |
| Prompt injection via OCR/exemplars | Security | Data sandboxing, output re-validation, no tool exposure |
| Tenant data leakage (incl. **pooled-connection `SET LOCAL` leak**) | Severe/legal | RLS from day one + BFF guards defense-in-depth; `SET LOCAL` inside txn; isolation tests across pooled checkouts |
| **Over-engineering / premature distribution** | Slows MVP, raises ops + hiring cost | Modular monolith on Fargate; defer vector DB/K8s/cells until a metric demands them (§18) |
| Runaway AI cost | Margin | Per-tenant budgets, caching, model tiering, alerts |
| Vendor/model lock-in | Strategic | AI **module** abstraction; model id as config; schema-driven output |
| Scale cliffs (single DB) | Outage | Documented migration triggers (§18) before limits hit |
| Copyright of textbook content | Legal | Per-organization licensing scope; global corpus only for owned/licensed content |
| Regulatory (data residency) | Compliance | Region pinning, DPDP/GDPR workflows |

---

## 24. Recommendations

Priorities are ordered by leverage — the first three are where this product is won or lost.

1. **Concentrate effort on the assembler — it's the core IP.** Build it as an explicit constraint solver with a documented relaxation order and an honest compromise report, not a greedy "fill by priority" that paints itself into corners. The feasibility gate must run a real dry-run solve, not just per-source counts.
2. **Verify AI correctness by computation, not by the model judging itself.** Execute numeric solutions; enforce single-correct MCQs; prefer parametric generation (vary a known-correct item) for quantitative slots. Frame AI output as teacher-approved drafts. Wrong answers are the fastest way to lose trust.
3. **Build a modular monolith, not microservices.** One language, one app + one worker, module-boundary isolation, ECS Fargate. This is the single biggest lever on time-to-MVP and ops cost. Extract services later, on measured evidence (§18).
4. **Retrieval-first as a principle, right-sized to the data.** Start with Postgres FTS + trigram + metadata filter — **no vector DB**. Add embeddings/Qdrant only when the eval suite shows a paraphrase-recall gap. The corpus is still the moat; the ANN machinery is not.
5. **RLS multi-tenancy from commit one** (never retrofitted), and mind the PgBouncer `SET LOCAL` footgun (§8). For coaching classes, airtight isolation is also a sales requirement.
6. **Right-size OCR to the market: Mathpix/vision for math**, and spike it on real STEM textbooks before committing — ingestion quality gates the whole retrieval thesis.
7. **Budget AI as a cold-start curve, not a flat minority,** and seed the global corpus aggressively before launch so new tenants are useful on day one.
8. **Invest early in evals** — golden-set regression (coverage, difficulty, correctness pass rate, paraphrase recall) is what lets you change prompts/models safely and tells you *when* to add embeddings.
9. **Enforce the feasibility gate and treat all ingested/retrieved text as untrusted** — validate every generated question before it can enter a paper.

---

*End of blueprint. This document is structured to be handed directly to engineering as the basis for ADRs, epics, and sprint planning.*
