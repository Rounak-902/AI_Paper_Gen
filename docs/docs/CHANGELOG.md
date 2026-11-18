# Documentation Reconciliation — Changelog

**Date:** 2026-08-13
**Scope:** All 12 documentation files in `docs/`, plus 14 new ADRs and this changelog.
**Purpose:** Make the documentation set internally consistent and usable as the single source of truth
for implementation.

This log is organised by the issue each change resolves, so a reviewer can audit the decisions without
re-reading every file. Every substantive change is listed. Reformatting-only edits are not.

**Read [`ADR/README.md`](ADR/README.md) first** — the ADRs carry the reasoning; this file records what
moved.

---

## Summary

| Category | Count |
|---|---|
| Architectural conflicts resolved | 7 |
| Product overrides applied | 2 (FastAPI backend, Gemini AI provider) |
| Schema drift items reconciled | 4 |
| Cross-document defects fixed | 9 |
| ADRs written | 14 |
| Files edited | 12 (10 markdown + 1 `.drawio` + ADR README) |
| Files created | 15 (14 ADRs + this changelog) |

**Two decisions were product overrides rather than a choice between the options on the table:**
the backend is **FastAPI (Python)**, not NestJS ([ADR-006](ADR/ADR-006-fastapi-over-nestjs.md)); and
the AI provider is **Gemini**, not the Claude family or OpenAI
([ADR-008](ADR/ADR-008-gemini-as-sole-ai-provider.md)). Both supersede every prior recommendation,
including those in `10_Project_Summary.md`.

---

## Part 1 — The seven architectural conflicts

Each was decided per `10_Project_Summary.md`'s recommendation unless noted. All seven now have an ADR.

### Conflict 1 — Vector search: **defer pgvector** → [ADR-007](ADR/ADR-007-defer-pgvector-retrieval.md)

MVP retrieval is metadata filter + `tsvector` FTS + `pg_trgm` trigram. `RetrievalService` returns scored
candidates so an ANN backend slots in with no caller change. Adoption trigger: a measured
paraphrase-recall gap in the eval harness.

| File | Change |
|---|---|
| `03_Database_Design.md` | Design Principles: "pgvector is used for embedding search" → deferred, evidence-triggered. `question_embeddings` marked **deferred** with the rationale. Migration order §9 step 7 explicitly **gated**; renumbered to 19 steps. |
| `06_AI_Architecture.md` | §6.1 pipeline redrawn: FTS and trigram as MVP stages, vector search marked `[DEFERRED]`. §6.2 rewritten per stage with MVP/deferred labels. §7 given a deferred-status banner. §25.1 hybrid weights split into MVP (0.4/0.4/0.2) and post-adoption (0.3/0.2/0.5); added `AI_VECTOR_SEARCH_ENABLED`. §26 data flow updated. |
| `02_System_Architecture.md` | Tech stack, component topology, retrieval pipeline diagram, and OCR flow step 17 all marked deferred. |
| `05_Textbook_Ingestion.md` | Dedup §12 near-duplicate = trigram at MVP, embedding once adopted. Design decision #4 rewritten. `EMBEDDING_PROVIDER` marked inert. |
| `07_Backend_Architecture.md` | §28.4 reframed: "why no vector search yet" precedes "why pgvector when we do". Embedding queue marked inert. |
| `09_Deployment.md` | Postgres image is `postgres:16`, swapped to the pgvector image only on adoption. Component table updated. |
| `08_Frontend_Architecture.md` | §18.8 search modes now **server-advertised capability**, not hardcoded — semantic/hybrid appear without a frontend release. |

### Conflict 2 — Assembler: **real constraint solver** → [ADR-009](ADR/ADR-009-constraint-solver-assembler.md)

| File | Change |
|---|---|
| `06_AI_Architecture.md` | §4.6 Paper Assembler responsibilities rewritten to lead with the feasibility gate and joint constraint satisfaction. §14.2 replaced greedy fill with: feasibility gate (3 outcomes), **documented relaxation order** (5 tiers, from never-relaxed to relaxed-first), compromise report, and the property-based testing obligation. |
| `02_System_Architecture.md` | Assembler added to the tech stack and the core stack diagram as a distinct component. Papers module responsibilities updated. |
| `07_Backend_Architecture.md` | Generation Worker steps now start with the feasibility gate and solve; per-slot retry semantics stated. Service example calls the gate before enqueueing. |
| `01_Product_Requirements.md` | Appendix C links FR-4.9, FR-4.19, AI-3, BR-1, BR-3 to ADR-009. |

### Conflict 3 — OCR engine: **math-first** → [ADR-014](ADR/ADR-014-math-first-ocr.md)

| File | Change |
|---|---|
| `05_Textbook_Ingestion.md` | §7 OCR engine replaced with a routing table: Mathpix primary, Gemini Pro-tier vision fallback, Tesseract/cloud OCR for prose — all behind `DocumentExtractor`. `OCR_PROVIDER` default `cloud_vision` → `mathpix`, plus two new routing keys. §37.5 and §42.1 runbooks updated. §39 "Formula OCR limited" reframed. |
| `06_AI_Architecture.md` | §13.1 extraction table OCR row rewritten. |
| `09_Deployment.md` | `MATHPIX_APP_KEY` added to the secrets/rotation table. |

### Conflict 4 — AI correctness: **execute-and-verify** → [ADR-010](ADR/ADR-010-execute-and-verify-ai-correctness.md)

| File | Change |
|---|---|
| `06_AI_Architecture.md` | §4.4 answer-correctness responsibility rewritten as a hard gate. §10.1 stage 10 action changed **"Flag for review" → "Reject"**; new stage 10b for the non-blocking qualitative advisory. **New §10.3** covering sympy execution, sandbox constraints, MCQ single-correct assertion, parametric generation preference, and CI enforcement. §11.3 semantic dedup explicitly marked advisory-only. New principle §2.11. |
| `01_Product_Requirements.md` | AI-5 rewritten to require verification by execution. Two rows added to the quality-threshold table. |
| `06_AI_Architecture.md` §25.1 | Added `AI_VERIFY_SANDBOX_TIMEOUT_MS`. |
| `09_Deployment.md` | CI test stage now includes the answer-correctness gate and solver property tests as build-failing checks. |

### Conflict 5 — Deployment: **Fargate at MVP** → [ADR-013](ADR/ADR-013-ecs-fargate-before-kubernetes.md)

| File | Change |
|---|---|
| `09_Deployment.md` | §1 Deployment Philosophy gained a **target-by-stage table** and three named Kubernetes adoption triggers. §7 retitled "*(scale target — not the MVP topology)*" with a banner, and a **new §7.0** describing the Fargate topology and its K8s-concept mapping. Migration procedure now describes an ECS one-off task at MVP, a K8s Job at scale. |
| `02_System_Architecture.md` | ADR summary table gained the deployment row. |

**No content was deleted.** The full Kubernetes design is retained and reframed.

### Conflict 6 — Models: **overridden to Gemini** → [ADR-008](ADR/ADR-008-gemini-as-sole-ai-provider.md)

Not resolved per `10_Project_Summary.md`'s recommendation (Claude 5 family) — **overridden by product
decision**. See Part 2.

### Conflict 7 — RBAC: **three roles, extendable enum** → [ADR-011](ADR/ADR-011-three-roles-extendable-enum.md)

The three values are **`teacher`, `coaching_admin`, `super_admin`** — determined by evidence, not
assumption: docs 01, 07, and 08 already used exactly these, and the implemented schema has three
values. Only the Blueprint used the six-role set.

| File | Change |
|---|---|
| `03_Database_Design.md` | `memberships.role` constraint now names the three values. **New §7.2** defines the full vocabulary, the reserved values (`hod`, `reviewer`, `viewer`) with the reason each is deferred, and exactly what adding one later requires. States why it is a CHECK-constrained text column and **not** a native `ENUM`. |
| `07_Backend_Architecture.md` | §8.1 preamble added; §8.2 gained branch scoping and a note on why there is no separate `reviewer` role. |
| `08_Frontend_Architecture.md` | §7.2 states the three values are complete, explains why a new backend role will not break the client, and forbids `role === 'coaching_admin'` comparisons. |
| `01_Product_Requirements.md` | Personas section gained a persona→role-value mapping table and the reserved-values note. FR-7 approval authority clarified. |
| `Blueprint_…md` | FR-2 rewritten; the `memberships` DDL CHECK now lists three values with the reserved values as inline comments. |

---

## Part 2 — Tech stack overrides

### Override A — Backend: **FastAPI (Python)**, not NestJS → [ADR-006](ADR/ADR-006-fastapi-over-nestjs.md)

Applied to every file describing backend runtime, framework, folder structure, or request lifecycle.

| Concern | Before | After |
|---|---|---|
| Framework | NestJS | **FastAPI** (Starlette + asyncio, `async def` handlers) |
| Structure | Modules/controllers | **Routers** |
| Validation | class-validator DTOs, `ValidationPipe` | **Pydantic v2**, `extra="forbid"` |
| DI | Nest DI container, decorators | **`Depends()`** |
| Guards | `AuthGuard → TenantGuard → RolesGuard` | **`get_current_user → get_org_context → require_roles → scope`** |
| ORM | Prisma | **SQLAlchemy 2.x async** |
| Migrations | `prisma migrate` | **Alembic** (raw SQL for RLS, partitions, index DDL) |
| Queue | BullMQ | **Celery + Redis** |
| Tests | Jest | **pytest** + Testcontainers |
| Port | 3001 | **8000** |

Files changed: `02`, `07`, `09`, Blueprint (§7, §12, §13), plus `05` and `08` where they reference the
backend. All TypeScript code samples in `07` were rewritten in Python.

**Security guarantees were preserved, not weakened.** The `SET LOCAL app.org_id` PgBouncer discipline,
RLS as second-line defence, deny-by-default authorization, mass-assignment prevention (now stricter —
`extra="forbid"` rejects rather than strips), and secret isolation are all carried across and made more
explicit.

**Frontend framework untouched**, as instructed.

### Override B — AI provider: **Gemini**, not Claude/OpenAI → [ADR-008](ADR/ADR-008-gemini-as-sole-ai-provider.md)

Every reference to `gpt-4o`, `gpt-4o-mini`, `claude-3.5-sonnet`, Claude 3 Haiku, `claude-haiku-4-5`,
`claude-sonnet-5`, `claude-opus-4-8`, Anthropic, OpenAI, DeepSeek, and `text-embedding-3-small` was
replaced — including in code samples, config tables, cost-estimation tables, fallback chains, secret
inventories, and glossaries.

**Tiering** mirrors the existing cheap/expensive split: **Pro tier** for generation, rubric synthesis,
hard reasoning, and vision OCR fallback; **Flash tier** for classification, tagging, extraction, repair,
and validation support; **Gemini embedding model** for embeddings.

**No dated model IDs anywhere.** Documents reference tiers; concrete IDs live in `AI_MODEL_GENERATE`,
`AI_MODEL_CLASSIFY`, `AI_EMBEDDING_MODEL`.

**Embedding dimensionality — a substantive technical change, not a find-and-replace.** The Gemini
embedding model emits **3072** dimensions natively, but pgvector's HNSW/IVFFlat indexes cap at **2000**
for the `vector` type — so a raw 3072-dim vector **cannot be ANN-indexed at all**. Resolution: truncate
(Matryoshka) and re-normalise to **1536** in the adapter; column is `vector(1536)`; `halfvec(3072)`
documented as the alternative. Applied in `03` §5, `06` §7.2/§25.1, `05` §27, `09` §11, and the
Blueprint's DDL (which previously said `vector(1024)`).

**Adapter pattern retained** — `LlmProvider` stays abstract, `GeminiAdapter` is the sole implementation.
Documented explicitly as *not* a per-call routing mechanism: a provider swap carries its own prompt
versions and eval baseline.

**Safety (doc 06 §19) made Gemini-specific**: the four harm categories with explicit thresholds, safety
blocks treated as **non-retryable**, blocks logged against prompt version and chapter (because
legitimate biology/history/chemistry content can trip naive filters). Prompt-injection defence promoted
to its own subsection §19.4.

**Doc 09's multi-provider fallback chain** (`OpenAI GPT-4 → Anthropic Claude → Google Gemini`) was
replaced with per-task degradation paths ending in the honest-gaps degrade rung.

---

## Part 3 — Schema drift → [ADR-012](ADR/ADR-012-schema-drift-reconciliation.md)

Judged case by case rather than by one blanket rule: renames favour the implementation (cheap doc fix),
but a field the architecture genuinely requires is an additive migration (near-free, no data risk).

| # | Item | Resolution | Rationale |
|---|---|---|---|
| 1 | `users.name` vs `fullName` | **Doc changes → `full_name`** | Implementation already correct; renaming a populated column buys nothing |
| 2 | `users.status` missing | **Doc wins → add column** | FR-1.6 and AC-1 need to disable an identity without deleting it |
| 3 | `memberships.status` undocumented | **Implementation wins → document it** | FR-1.5 invitations need a row that grants no access yet |
| 4 | `memberships.branch_id` missing | **Doc wins → add column** | `programs.branch_id` and `branches` already exist; branch scoping is in the guard chain |

**Files updated:** `03_Database_Design.md` §5 (both tables, with drift notes), **§7 rewritten** from a
one-line pointer into a real data dictionary with §7.1 identity columns, §7.2 the RBAC vocabulary, and
§7.3 all other controlled vocabularies; **§8 retitled** "ORM Model Design" (SQLAlchemy, not Prisma);
**§9.1 added** with the three drift-reconciliation migrations (D-1, D-2, D-3) and their shapes;
`04_ER_Diagram.drawio` (`name` → `full_name` in the `users` entity; `status` row added to
`memberships`; verified well-formed XML); Blueprint DDL for both tables.

---

## Part 4 — Cross-document consistency defects fixed

These were found during the pass and are **not** part of the seven conflicts.

| # | Defect | Fix |
|---|---|---|
| 1 | **Rival generation-mode list.** `06` §5 defined 7 modes (Textbook Only, AI Only, Mixed, Intext Only, Exercise Only, Previous Year, Custom Mix) that did not match `01`'s GM-1…GM-7. | `06` §5 rewritten as a GM-1…GM-7 table stating only what each mode means to the AI subsystem, deferring the definition to `01`. |
| 2 | **Question types 8 vs 13.** `06` §12.1 listed 8 types; `01` defines 13 (QT-1…QT-13). | `06` §12.1 now lists all 13 by their canonical values and points at `01`. Also added to `03` §7.3. |
| 3 | **Wrong EH ids throughout `05`.** Ingestion used EH-1 for invalid file, EH-2 for unsupported format, EH-3 for malware, EH-4 for OCR failure. `01`'s catalogue says EH-1 = OCR failed, EH-4 = unsupported/corrupt file, EH-5 = size/page limit. | All ingestion error mappings in `05` §4, §5, §7, §17, §19 remapped to the catalogue. |
| 4 | **`notifications` table referenced but never defined.** `02` and `07` both assigned the Notifications module a "notification log table — proposed; not yet in `03_Database_Design.md`". | Table defined in `03` §5 with columns, constraints (including the `unique(user_id, dedup_key)` that enforces the documented suppression rule), and indexes; added to §2, §4, §6 ERD, §8, and §9 step 17. `02` updated to reference it as existing. |
| 5 | **"Retriever" vs "Retrieval Engine".** Two names for one component. | Standardised on **"Retrieval Engine"** (service `RetrievalService`) across `02`, `06`, `07`. |
| 6 | **"Assembler" vs "generation engine".** | Standardised on **"Assembler"**; the LLM-calling component is the **"AI Generation Engine"**. Both defined in `06`'s glossary. |
| 7 | **Guard-chain vocabulary in `08` contradicted `07`.** `08` referenced `AuthGuard`/`TenantGuard`/`RolesGuard` in ~20 places. | All renamed to the FastAPI dependency chain. `08` now describes the backend consistently with `07`. |
| 8 | **`08` §16.2 assumed a shared runtime schema package** between backend and client — impossible across Python and TypeScript. | Rewritten: language-neutral constants in `packages/contracts`, plus client types **generated from the FastAPI OpenAPI document in CI**, with drift failing the build. |
| 9 | **`08` §18.8 hardcoded semantic/hybrid search modes** that will not exist at MVP. | Modes are now server-advertised capability; the selector is not rendered if only lexical is available. |

---

## Part 5 — Security and reliability, made consistent everywhere

Per the non-negotiable requirements, these were checked for consistent description across every layer's
document rather than only in `07` §20 and `09` §17.

- **Tenant isolation (RLS).** Described identically in `02`, `03`, `05`, `07`, `08`, `09`. The
  **PgBouncer `SET LOCAL` footgun** — a session-level `SET` leaks tenant context to the next borrower of
  a pooled backend — is now stated in `03` §8, `07` §12.1 (with the `get_db` code), and the Blueprint,
  each with the isolation test that asserts it does not survive a pooled checkout.
- **Auth token handling.** Argon2id, ~15-min access JWT, rotating single-use refresh in Redis with reuse
  detection, no token in the browser — consistent across `02`, `07`, `08`, `09`.
- **Input validation.** Pydantic `extra="forbid"` at the boundary, business rules in the service,
  database constraints last. `08` restates the client's rules as advisory-only projection.
- **Secrets management.** No secrets in the repo or the client bundle; boot-time validation that
  **refuses to start** on placeholder or invalid config; rotation without code changes. Now consistent
  in `02`, `07`, `09`, and the Blueprint. `09`'s secret inventory reduced to the credentials that
  actually exist.
- **Dependency scanning.** `09` CI now runs `pip-audit` (Python) **and** `npm audit` (frontend), plus
  Trivy image scanning, SAST, secret detection, and a prompt-injection adversarial suite.
- **Every locked-in technology choice carries its production-safety case** in its ADR: known failure
  modes, who runs it at scale, and the fallback. See ADR-001 (Postgres), ADR-006 (FastAPI, SQLAlchemy,
  Alembic, Celery — including Redis's non-durable-broker failure mode and its mitigations), ADR-008
  (Gemini), ADR-013 (Fargate).

### Frontend/backend separation and visible backend logs

Confirmed and strengthened, per the explicit requirement:

- `02` "Project Folder Architecture" rewritten: `apps/web` is Node, `apps/api` and `apps/workers` are
  Python. The boundary is **physical** — no shared dependency tree, build graph, runtime, or log stream
  — not a lint rule.
- `07` §3 folder structure rewritten with a table of what the split buys, plus **new §2.11** and
  **new §19.4 "Independent Log Streams"** with the exact commands to tail the backend alone.
- **`09` gained a new §6.5 "Local Development — Separate Services, Separate Log Streams"** with an
  annotated `docker-compose.yml` showing `api`, `worker-*`, and `web` as distinct services, the
  `docker compose logs -f api` commands, and the no-Docker two-terminal alternative. It states
  explicitly that root `pnpm dev` must **not** start the API.
- `09` §7.0 records one CloudWatch log group per Fargate service.
- Structured JSON to stdout carrying `"service"`, so streams stay separable by field even when
  aggregated.

**No doc described the backend running inside the frontend process**, so no "needs human decision" flag
was raised on that point. `08` §24.5's narrow BFF route handlers (cookie operations only) were already
scoped correctly and are now stated more explicitly as *not* the backend.

---

## Part 6 — Files changed

### Edited in place (12)

| File | Nature of change |
|---|---|
| `01_Product_Requirements.md` | Role mapping, AI-5 verification, FR/NFR retrieval wording, Appendix C |
| `02_System_Architecture.md` | FastAPI, folder architecture, terminology, ADR summary table rewritten with links |
| `03_Database_Design.md` | Schema drift, `notifications`, RBAC vocabulary, §7 data dictionary, §8 ORM, §9 migrations, §11 |
| `04_ER_Diagram.drawio` | `users.full_name`, `memberships.status` (XML verified well-formed) |
| `05_Textbook_Ingestion.md` | Math-first OCR, EH remapping, Gemini embeddings, Celery, role values |
| `06_AI_Architecture.md` | Gemini, solver, execute-and-verify, GM/QT alignment, deferred vectors, safety, glossary |
| `07_Backend_Architecture.md` | Full FastAPI rewrite incl. all code samples; RBAC; log streams; design-decision tables |
| `08_Frontend_Architecture.md` | Backend surface renamed, roles, contract strategy, search capability |
| `09_Deployment.md` | Fargate-first, FastAPI/Celery/Alembic, Gemini, local-dev log separation, CI |
| `10_Project_Summary.md` | Status banner, conflict table with resolutions, superseded tech-stack rows |
| `Blueprint_…md` | Gemini, FastAPI, Celery, 3 roles, `full_name`, `vector(1536)`, folder structure |
| `ADR/README.md` | Rewritten from a stub list into a real index with governance mapping |

`Production_Grade_Claude_Prompt_AI_Question_Paper_Generator.md` was **not edited** — it is the verbatim
historical prompt that produced the Blueprint, not a specification. Editing it would falsify a record.
Noted below.

### Created (15)

`ADR/ADR-001` … `ADR-014` and this file.

---

## Needs human decision

Items I could not resolve from the documents without guessing.

### 1. The two competing repository layouts

`10_Project_Summary.md` proposes `backend/ frontend/ shared/` as top-level directories with **two
workspace roots and two lockfiles**, as a security measure (a physically separate build context so a
server constant cannot become a browser string). The instructions for this pass specify `apps/api`,
`apps/workers`, `apps/web`, which is what docs 02, 07, and 09 use.

**I applied `apps/*`** as instructed. The security goal is still met — `apps/web` is Node and
`apps/api`/`apps/workers` are Python, so they cannot share a dependency tree — but this achieves it via
the *language* boundary rather than the *workspace-root* boundary that doc 10 argued for.

**Decide:** keep `apps/*` (current state), or adopt doc 10's `backend/ frontend/ shared/` split. If the
latter, docs 02, 07, 09, 10, and the Blueprint need a coordinated update.

### 2. The "minimize LLM dependency" pivot in `10_Project_Summary.md` §3

Doc 10 records a final, **unexecuted** instruction: *"reduce the llm dependency the most you can and
create all the internal tools — with those internal tools I'll add the content to database and then all
the questions will be made using those database and only the llm will be used for paper generation."*
Its reading was that the LLM becomes a single optional gap-fill call, removing embeddings, the LLM
classifier, and most LLM validation from the critical path.

This pass moved **substantially in that direction** — deferred embeddings (ADR-007), deterministic-rules-first
classification, parametric generation preferred over LLM generation, execute-and-verify instead of
LLM-as-judge — but it did **not** remove the AI module from the critical path, because the instructions
for this pass explicitly required Gemini for generation, validation, classification, and
duplicate-detection support.

**Decide:** is the §3 pivot still active? If so, docs 05 and 06 need a further pass to make LLM
classification strictly optional and to specify the deterministic internal tools that replace it. I did
not act on it because doing so would contradict the current instructions.

### 3. Whether the exact model tier names should be pinned

Model IDs are deliberately in config, referenced only as "Pro tier" and "Flash tier". This is correct
for durability, but it means **no document states which concrete Gemini model to start with**, and
someone must set `AI_MODEL_GENERATE` and `AI_MODEL_CLASSIFY` before anything runs.

**Decide:** whether a single non-normative "current defaults" line belongs somewhere — probably in
`.env.example` rather than in the architecture docs, so it can go stale without falsifying a
specification.

### 4. `Production_Grade_Claude_Prompt_AI_Question_Paper_Generator.md`

Left unedited as a historical record. It still says "Kubernetes", "Vector DB", and its filename
references Claude. Rewriting it would misrepresent what was actually asked.

**Decide:** leave as-is (recommended, possibly with a one-line "historical record" header), or restate
it as a living specification — in which case it needs the same treatment as everything else.

### 5. Solver implementation strategy

ADR-009 fixes the *design* (joint constraint satisfaction, documented relaxation order, compromise
report) but deliberately leaves the *engine* open — a hand-written search with the relaxation ladder, or
a CP-SAT model via OR-Tools. Phase-0 spike S-2 is the intended decision point.

**Decide at S-2 exit**, and record it as a follow-up ADR. If OR-Tools is chosen, it becomes a
critical-path dependency and needs the same production-safety justification as every other locked-in
choice.

### 6. ADR length exceeds the ~400-word guideline

Several ADRs run 550–950 words rather than the ~400 target. This was a deliberate trade-off against two
other explicit requirements: each locked-in stack choice must document known failure modes, production
track record, and fallback; and rejected alternatives must be preserved in the ADR rather than deleted.
ADR-006, 008, 009, 010, 011, and 013 are the longest. Much of the excess is table content rather than
prose.

**Decide:** accept as-is, or split the stack-safety justifications into a separate
"production-readiness" appendix so the decision records themselves stay short.

### 7. Doc 10's security audit findings are still open

Findings 1–13 and 15–19 describe the **actual codebase**, which this pass did not touch. The live
Supabase credential and the placeholder `JWT_SECRET` (findings 1 and 2) are flagged as critical and
require console access to rotate. Finding 14 (schema drift) is resolved *in documentation* by ADR-012,
but the three Alembic migrations are not written.

**These are implementation work, not documentation work**, and are listed here only so they are not
assumed to have been handled by this pass.

---

## Reconciliation Pass 2 — Resolutions (2026-08-14)

**Scope:** Resolve "Needs human decision" items #1 and #2 from Pass 1. Item #3 is explicitly left open.

### Item #1 resolved — Repository layout: adopt workspace-root split

**Decision:** adopt `10_Project_Summary.md`'s original proposal — three top-level, independent workspace
roots: `backend/` (Python — API + workers), `frontend/` (Node — Next.js), `shared/` (non-code assets).
Reject the `apps/*` layout that was applied in Pass 1.

**Recorded in:** [ADR-015](ADR/ADR-015-workspace-root-split.md)

**Files updated:**
| File | What changed |
|---|---|
| `02_System_Architecture.md` | Tech-stack table, separation paragraph → `backend/`/`frontend/` |
| `07_Backend_Architecture.md` | §2.11, §3 folder tree, §3.1 rationale, worker rule |
| `09_Deployment.md` | Docker compose `env_file` paths, `cd` commands |
| `10_Project_Summary.md` | Folder section reframed from proposal to decided |
| `Blueprint_…md` | §13 folder tree + description paragraphs |
| `ADR-002` | Decision paragraph |
| `ADR-006` | Separation paragraph |
| `IMPLEMENTATION_PLAN.md` | P1-T1, P3-T2, P4-T1, D-3, D-7, R-3, §4.2 |
| **[NEW]** `ADR-015` | Records the workspace-root-split decision |

### Item #2 resolved — AI minimization: software-first, Gemini as last resort

**Decision:** adopt the full software-first hierarchy from `10_Project_Summary.md` §3:

1. **Generate** — retrieval from bank, then template-based/parametric generation (deterministic answer)
2. **Classify** — deterministic rules first, Gemini Flash only for low-confidence residual
3. **Calculate** — execute-and-verify (already software-first)
4. **Validate** — structural/schema validation (already software-first)
5. **Gemini** — only when software genuinely cannot produce a result

**Files updated:**
| File | What changed |
|---|---|
| `06_AI_Architecture.md` | §9 generation pipeline (template stage added), §12.2 classification (rules-first with `CLASSIFICATION_FALLBACK_THRESHOLD`), §18.1.1 cost cascade (reframed as primary path), §25.1 config (2 new keys) |
| `05_Textbook_Ingestion.md` | §11 classification table (rules-first, explicit determinism annotations) |
| `ADR-005` | Decision section rewritten with full software-first hierarchy |
| `IMPLEMENTATION_PLAN.md` | P3-T3.5 (template engine), P3-T6 cascade updated, P3 exit criteria (software-first metric), R-4 resolved |

### Item #3 — Model tier names: STILL OPEN

No change. The documents continue to reference "Pro tier" and "Flash tier" without pinning concrete
model IDs. This is intentional for durability. The decision on whether to add a non-normative
"current defaults" line (e.g. in `.env.example`) is deferred.

---

## Implementation verification follow-up — P3-T4 (2026-09-13)

### Project open item #9 — Gemini daily quota and pending Flash live gate

**Status: OPEN.** This is a throughput/verification-cadence issue, separate from
P3-T2 open item #4 (Pro-tier remains unverified before P4). Existing Flash
configuration is reused unchanged; no code, tests, tripwire or retry policy is
modified by this follow-up.

> Gemini API daily quota (20 req/day) is insufficient for completing a single live
> verification gate in one day; verification runs must be split across multiple
> days or quota needs to be increased before P4.

**Scope qualification:** one clean current five-type P3-T4 gate needs **10 real
Gemini calls**, so it fits an otherwise-unused 20/day allowance. Two clean runs
consume all 20; one run with the permitted retries can require **up to 30 attempts**.
The issue is insufficient headroom for reliable retry/development cadence, not
that every clean single gate intrinsically exceeds 20. Future P3/P4 gate sizes
remain unmeasured. Increase quota or adopt explicit split-day checkpoints before P4.

**Latest final-code coverage:** numerical and short-answer retain their earlier
end-to-end PASS evidence. The final consolidated attempt after Prompt E added full
MCQ and True/False PASS evidence, including a naturally triggered True/False
null-solution corrective retry. On 2026-09-14, two independent fill-blank samples
completed all stages: marker/answer counts were 1/1 and 2/2, every marker was a
literal three-underscore run, and all four generate/classify responses ended with
`STOP`. P3-T4's final-code Flash live gate is complete.

**Cadence update:** a minimum 15-second interval is suitable for the documented
5 RPM cap. The direct console check before the fill-blank remainder showed 0/20
RPD; the two clean pipelines used four successful calls and the post-run console
showed 4/20 RPD. The earlier 429 still proves that spacing cannot prevent shared
daily exhaustion. Keep item #9 open for future gate headroom: verify RPD before a
run, retain per-type checkpoints and stop on the first 429 unless quota is increased.

See [exact stage/path statuses, raw evidence and resume plan](P3-T4_Live_Gate_Status.md).
The Flash coverage gap is closed. Resolving quota cadence and the Pro-tier item
remain separate pre-P4 concerns.

---

## P3-T5 sandbox hardening follow-up (2026-09-15)

### Project open item #10 — sin/cos whitelisted but non-functional in expression worker

**Status: RESOLVED (2026-09-15).** Fixed by adding a non-trivial warmup
(`float(s.sin(s.pi/6)); float(s.cos(s.pi/6))`) in the trusted startup phase
of `expression_worker.py`, before `sys.addaudithook(deny)` is installed. This
forces SymPy's mpmath integration layer to fully initialize — including all
lazy-loaded data files — while file I/O is still permitted. The audit hook
then denies all subsequent file-open events as before, with no new attack
surface: the warmup runs before any user input is read from stdin, so no
model-supplied expression can influence what gets loaded.

**Fix details:** Option 1 (pre-import) was used, but refined: merely importing
`mpmath` was insufficient because SymPy's internal dispatch path from
`sympy.sin` → `mpmath.sin` involves additional lazy-loading beyond the mpmath
module import itself. Warmup values must be non-trivial: `sin(0)`/`cos(0)`
evaluate to exact SymPy integers without invoking mpmath, so they skip the
lazy-loading path. `sin(pi/6)`/`cos(pi/6)` produce Rational results that
exercise the full dispatch chain.

**Verification:** All four previously-failing expressions now return correct
values: `cos(0)` → 1.0, `sin(pi/6)` → 0.5, `cos(pi/3)` → 0.5
(so `50*4*cos(pi/3)` → 100.0), `pi*(31.4159/(2*pi))**2` → 78.54. P0-T2
regression improved from 37/41 → 41/41 compatible passing. All 55 adversarial
security tests (27 attribute-traversal, 16 structural, 9 import/exec,
3 resource) remain blocked. Full suite: 70 passed, 0 failed.

> `sin` and `cos` are listed in the expression worker's function whitelist and in
> the `generate_question/v1` prompt's allowed-expression-language spec, but their
> SymPy evaluation triggers `mpmath` lazy-loading which attempts file I/O after
> `sys.addaudithook(deny)` is installed. The audit hook denies the `open` event,
> causing `PermissionError` inside the subprocess. The worker exits non-zero and
> `compute()` raises `PipelineValidationError("solution_execution")`.

**Production exposure path: CONFIRMED.** The generation prompt at
[`generate_question/v1.json:7`](../backend/packages/prompts/artifacts/generate_question/v1.json)
explicitly tells the model: "Allowed expression language: ... sqrt, sin, cos,
abs, Rational, floor, ceiling, pi, E". The model may therefore emit
`sin(pi/6)` or `cos(theta)` as a `solution_expression`. When the validation
engine tries to execute such an expression, it will fail, triggering a
validation rejection and potentially a retry. No template in the current
P3-T3.5 catalog references trigonometry or angles, so template-path questions
are not affected. Fallback-path questions for trigonometry-related curriculum
items (e.g. Class 10 math) **can** hit this.

**Possible fixes** (not addressed in P3-T5; deferred for resolution):
1. Pre-import `mpmath` in the trusted startup block before `sys.addaudithook`
   (add `import mpmath; mpmath.mp` to expression_worker.py before the audit hook
   installation). Minimal change, preserves security posture.
2. Refine the audit hook to allow reads from the `sympy`/`mpmath` package directory.
3. Remove `sin`/`cos` from both the worker whitelist and the prompt spec, and
   document the expression language as arithmetic-only.

**Evidence:** P3-T5 adversarial test suite
[`test_sandbox_attribute_traversal.py`](../backend/tests/test_sandbox_attribute_traversal.py)
CLASS 6 (2 tests confirming the PermissionError). P0-T2 regression
[`test_sandbox_p0t2_regression.py`](../backend/tests/test_sandbox_p0t2_regression.py)
identified 3 fixture expressions affected (e18, e21, e23) plus 1 complex-pi
expression (e13) that triggers the same mpmath lazy-load path.

---

## P3-T7 eval harness and S-4 measurement (2026-09-17)

- Added the bank-backed Section 20.1 offline evaluator, prompt/model comparison over P3-T3
  `ResolvedPrompt` bindings, and a deterministic CI runner through P3-T6 `CostCascade` telemetry.
- Extended the existing CI workflow with PostgreSQL 16, the 79-question offline run, the S-4
  FTS/`pg_trgm` spike, and uploaded JSON metric artifacts.
- Recorded S-4 lexical paraphrase recall **0.00** (0/20), gap **1.00**, and the pgvector adoption
  trigger gap **0.20**. The threshold is breached, but no vector schema or embedding work was added.
- Resolved the corpus judgment call: unchanged P2 bank targets plus hand-authored semantic query
  paraphrases; P2-T4's `exactly`/`precisely` pair was rejected as a surface-only fixture.
- Characterized S-4 as a worst-case low-lexical-overlap stress corpus rather than a representative
  production sample; R-12 requires representative expert-labeled traffic before an adoption decision.
- Confirmed R-15 is not currently live in the checked databases: the seven failing stems have zero
  matches, including in a database with 63 questions and 58 approved/current/non-deleted rows.
- Surfaced R-12 through R-16 in `IMPLEMENTATION_PLAN.md`: adoption follow-up, missing
  `diagram_based` bank coverage, live-provider promotion gate, seven bank/generation validation
  mismatches, and hosted CI proof after the first push.

---

## P3 phase complete (2026-09-19)

- P3-T1 through P3-T7 are implemented and every P3 exit criterion is recorded as passing.
- P3-T6 adds the cost-aware cascade, per-tenant budget enforcement, real `ai_cost_usd` telemetry, and
  adversarial prompt-injection defenses.
- P3-T7 adds the bank-backed offline evaluator, prompt/model comparison, CI execution, and the S-4
  PostgreSQL FTS/`pg_trgm` measurement without adopting pgvector.
- R-12 through R-16 remain explicit follow-ups or promotion gates and do not reopen the completed P3
  implementation scope.

---

## P4-T4 live evidence closure (2026-09-20)

- Forced an isolated solver-selected slot through the real idempotent Celery assembly task's live
  Gemini generation branch. The persisted trace records retrieve, rubric gap, generation, validation,
  verification, and P2 dedup; the generated question and paper version are durable database rows.
- Forced a textbook-to-`ai_generated` substitution and observed the real `PaperAssemblyError` boundary;
  the transaction left no paper version or generated replacement row.
- Split R-17 from R-15 after an ID/stem comparison: 58 approved live-bank short answers missing
  `rubric.key_points` have zero overlap with R-15's seven absent MCQ/numerical import fixtures.
- Classified the local `gemini-2.5-flash` generation/classification bindings as the deliberate
  P3-T4 free-tier verification choice already tracked by P3-T2 project open item #4. No standalone
  R-18 was created; this run adds Flash-path evidence without resolving Pro-tier verification.
