# 01 Product Requirements

## Purpose

This document captures the product-level requirements for the AI-powered Question Paper Generation SaaS platform. It defines who the platform serves, what it must do, how well it must do it, and the business rules that constrain behavior.

## Core Goals

- Enable teachers to generate question papers from multiple sources.
- Support multi-tenant organizations with strict data isolation.
- Support dynamic paper patterns, marks, sections, difficulty, Bloom levels, and chapter weightage.
- Preserve question quality through retrieval-first workflows and validation.
- Support export, approval, and analytics.

## Scope

### In scope
- Multi-organization SaaS experience
- Question bank management
- Textbook ingestion and OCR-based extraction
- Paper generation and export
- Approval workflows
- Billing and usage tracking
- Audit and analytics

### Out of scope for v1
- Full school ERP functionality
- Complex LMS integrations
- Fully adaptive testing across all learners

## Success Criteria

- Teachers can generate a paper in minutes.
- Papers remain curriculum-aligned and source-aware.
- Multi-tenant data stays isolated.
- AI-generated content is auditable and versioned.

## Personas

Personas map 1:1 to the RBAC role values carried on `memberships.role`. Each persona is always scoped
to an organization (and optionally a branch), except the Super Admin who operates across tenants.

| Persona | `memberships.role` value |
|---|---|
| Teacher | `teacher` |
| Coaching Admin | `coaching_admin` |
| Super Admin | `super_admin` |

These three are the complete implemented set. `hod`, `reviewer`, and `viewer` are **reserved** values —
documented in `03_Database_Design.md` §7.2 but deliberately not implemented at v1, and adding one later
requires no schema change (ADR-011). Where this document refers to "designated reviewers" (FR-7.2,
NT-4), that authority belongs to `coaching_admin`, constrained by BR-6 separation of duties.

### Teacher

- **Role scope:** Single organization, often limited to assigned programs and subjects.
- **Primary goals:** Create question papers quickly, reuse trusted questions, keep papers curriculum-aligned, and export clean PDFs.
- **Key activities:** Upload or select textbooks, import and curate questions, define a blueprint, generate papers, edit drafts, and submit papers for approval.
- **Needs:** Fast generation, source transparency (which questions came from where), and predictable formatting.
- **Constraints:** Can only see questions and papers within their organization scope and the global shared bank; cannot approve their own papers where org policy requires separation of duties.

### Coaching Admin

- **Role scope:** Single organization, with authority over that organization's branches, programs, users, and content.
- **Primary goals:** Manage teachers and programs, enforce quality and curriculum standards, and control what gets published.
- **Key activities:** Invite and manage users, assign teachers to programs, manage templates, review and approve or reject papers, configure org-level generation policies, and monitor analytics and usage.
- **Needs:** Oversight of paper quality, approval control, visibility into usage against plan limits, and organization-wide analytics.
- **Constraints:** Bound to their own organization; cannot access other tenants' data.

### Super Admin

- **Role scope:** Cross-tenant platform operator.
- **Primary goals:** Keep the platform healthy, manage tenants and subscriptions, govern shared/global curriculum and question data, and manage AI prompt releases.
- **Key activities:** Create and configure organizations, manage subscription plans and seats, curate global curriculum and the shared question bank, promote and retire prompt versions, and investigate platform-wide audit and analytics.
- **Needs:** Global visibility, tenant lifecycle control, and governance over AI and curriculum assets.
- **Constraints:** Cross-tenant access is privileged and must be fully audited; direct edits to tenant-owned content should be exceptional and logged.

## Functional Requirements

Requirements are grouped by capability. Each is written as a behavior the platform must support.

### FR-1 Login and Access

- Users authenticate with email and password (and future SSO), receiving an organization-scoped session.
- A user may hold memberships in multiple organizations and switch active organization context.
- Access to every entity is enforced by role (`teacher`, `coaching_admin`, `super_admin`) and tenant scope.
- Failed logins, sessions, and role changes are recorded in the audit trail.

### FR-2 Upload Textbook

- Teachers and Coaching Admins can upload a textbook file to their organization, optionally tagged to a subject.
- Uploaded textbooks move through a processing lifecycle (`uploaded → ocr → parsed → embedded`, or `failed`).
- Extraction produces textbook assets (paragraphs, exercises, questions, diagrams, tables, activities, formulas) mapped to chapters where possible.
- Processing runs asynchronously; users can view status and are notified on completion or failure.

### FR-3 Import Questions

- Users can import questions into the question bank from structured files and from extracted textbook assets.
- Each imported question is classified by source (`institute_bank`, `textbook`, `intext`, `previous_year`, `ai_generated`), type, marks, difficulty (`easy`/`medium`/`hard`), Bloom level, and language.
- Imports are validated for required fields and de-duplicated using content hashing.
- Imported questions enter as `draft` unless imported into a curated shared set.

### FR-4 Generate Paper

- Users define a blueprint: total marks, duration, sections, question types, chapter/outcome weightage, difficulty mix, Bloom mix, and source mix (including generation mode).
- Generation is retrieval-first: existing approved questions are selected before any AI generation fills gaps.
- The blueprint is snapshotted onto the generated paper so results are reproducible.
- Generation runs asynchronously and returns a draft paper the user can review and edit.
- Every question in the result records its provenance (source, and prompt version for AI-generated content).

### FR-5 Export PDF

- Users can export any paper version to PDF, with an optional separate answer key.
- Export applies a selected template for layout and branding.
- Exports are generated asynchronously, stored in object storage, and downloadable via a secure link.
- DOCX export is supported where a template allows it.

### FR-6 Save Template

- Users can create, name, and save reusable paper templates (layout and branding).
- Templates are scoped to the organization; global/base templates may be provided by the platform.
- Templates are versioned via status (`active`/`draft`/`archived`) and can be reused across papers.

### FR-7 Approval

- Papers move through a workflow: `draft → submitted → review → approved` or `rejected`.
- Coaching Admins can approve or reject papers with comments. (Approval authority is a `coaching_admin` capability at v1; a dedicated `reviewer` role is a reserved, unimplemented value — see Personas.)
- Every approval action is recorded with reviewer, action, comment, and timestamp.
- Only approved papers are considered final for distribution; rejected papers return to editing.

### FR-8 Analytics

- Coaching Admins see organization analytics: papers generated, AI vs. retrieved question counts, average difficulty, and curriculum coverage.
- Super Admins see cross-tenant platform analytics and usage.
- Analytics are served from daily rollups and are rebuildable from source data.
- Usage metrics (papers, AI questions, exports) are metered per organization for billing and plan enforcement.

## Non-functional Requirements

### NFR-1 Performance

- Interactive actions (navigation, blueprint editing, question search) respond within ~2 seconds under normal load.
- Paper generation for a standard blueprint completes within a few minutes and runs asynchronously so the UI is never blocked.
- Retrieval uses appropriate indexes (composite metadata, GIN full-text, and trigram) to keep query latency low as the question bank grows. Semantic vector search is a deferred capability adopted on measured evidence (ADR-007), not a v1 requirement.

### NFR-2 Security

- Strict multi-tenant isolation: every tenant-owned query is scoped by organization, enforced at the application and database layers (e.g., row-level security).
- Role-based access control governs all actions; privileged cross-tenant access is restricted to Super Admins and fully audited.
- Data is encrypted in transit and at rest; uploads and exports are accessed via time-limited secure links.
- All sensitive actions are written to an append-only audit log.

### NFR-3 Availability

- Target availability of 99.9% for the core application.
- Asynchronous workers (OCR, generation, export) degrade gracefully: failures are retried and surfaced without data loss.
- Backups and point-in-time recovery are maintained for the transactional database.

### NFR-4 Scalability

- The platform scales horizontally at the application and worker tiers.
- Heavy operations run as queue-based background jobs so load spikes do not affect interactive users.
- Append-only tables (usage, audit logs) and analytics rollups are partitioned by time to sustain growth.
- The modular-monolith design allows extracting high-load modules into services later without a schema redesign.

## Business Rules

These rules constrain platform behavior and must be enforced by the system. Values are defaults that may be overridden by organization policy where noted.

- **BR-1 (Mixed Mode source floor):** When "Mixed Mode" is selected for generation, an AI-generated paper must contain at least **70% textbook-derived questions**. If retrieval cannot meet the floor, the system reduces AI content or flags the paper rather than silently violating the rule.
- **BR-2 (Retrieval-first):** AI generation only fills gaps that retrieval from approved questions cannot satisfy for the requested blueprint.
- **BR-3 (Blueprint integrity):** A paper's total marks, section marks, and question counts must reconcile with its blueprint before it can be submitted for approval.
- **BR-4 (Provenance required):** Every question placed in a paper must record its source; AI-generated questions must record the prompt version used.
- **BR-5 (Approval gate):** Only papers in `approved` status are final for distribution/export as final; drafts and rejected papers cannot be marked final.
- **BR-6 (Separation of duties):** Where org policy requires it, the user who authored/submitted a paper cannot be the sole approver.
- **BR-7 (Tenant isolation):** No user may read or write another organization's data; the only shared, cross-tenant content is explicitly global (base curriculum, shared question bank, base templates, active prompt versions).
- **BR-8 (Question versioning):** Approved questions are immutable; edits create a new version in the lineage, and only one version is current per question root.
- **BR-9 (Curriculum alignment):** Questions and papers are tied to a curriculum version; generating against a superseded curriculum version requires explicit confirmation.
- **BR-10 (Plan and usage limits):** Generation and export actions are metered; when an organization exceeds its subscription plan limits, restricted actions are blocked until the plan is upgraded or the period resets.
- **BR-11 (Auditability):** All create/update/delete actions on business entities and all approval and access-control changes are written to the append-only audit log.

## User Journeys

User journeys describe the end-to-end paths each persona takes through the product. Every step maps to a screen in the [Screen Inventory](#screen-inventory) and to one or more requirements in the [Feature Breakdown](#feature-breakdown).

### UJ-1 Teacher: textbook to exported paper (primary journey)

This is the platform's flagship journey. It is the path a first-time teacher takes from an empty organization to a finished, approved, exported paper.

```
  Login
    |
    v
  Dashboard  ---------------------------.
    |                                    |
    v                                    | (question bank already populated)
  Upload Textbook                        |
    |                                    |
    v                                    |
  OCR Processing  (async, notified)      |
    |                                    |
    v                                    |
  Question Extraction (assets -> draft questions)
    |                                    |
    v                                    |
  Review Questions (accept / edit / reject)
    |                                    |
    '------------------> Generate Paper <-'
                            |
                            v
                      Edit Paper (swap / lock / reorder / regenerate)
                            |
                            v
                      Submit for Approval
                            |
                            v
                      Approve  (Coaching Admin; see UJ-2)
                            |
                            v
                      Export PDF
```

**Step detail**

| # | Step | Screen | Requirement | State change |
|---|------|--------|-------------|--------------|
| 1 | Log in, select active organization | Login | FR-1.1, FR-1.4 | Session created, org context set |
| 2 | Land on role-aware dashboard | Dashboard | FR-8.1 | — |
| 3 | Upload textbook file, tag board/class/subject | Upload Textbook | FR-2.1–FR-2.4 | `textbooks.status = uploaded` |
| 4 | Watch OCR progress; receive notification | OCR Progress | FR-2.5–FR-2.7 | `uploaded → ocr → parsed` |
| 5 | System extracts assets and proposes questions | OCR Progress → Question Bank | FR-2.8, FR-3.4 | `parsed → embedded`; questions created as `draft` |
| 6 | Review, edit, accept or reject proposed questions | Question Bank, Question Details | FR-3.5–FR-3.8 | `draft → approved` / `rejected` |
| 7 | Configure blueprint and generate | Paper Builder | FR-4.1–FR-4.9 | Paper created as `draft`, blueprint snapshotted |
| 8 | Review draft; swap, lock, reorder, regenerate | Paper Preview | FR-4.10–FR-4.11 | New paper version |
| 9 | Submit for approval | Paper Preview | FR-7.1 | `draft → submitted` |
| 10 | Approval decision received | Notifications, Dashboard | FR-7.3–FR-7.5 | `submitted → review → approved` / `rejected` |
| 11 | Export final PDF (+ optional answer key) | Paper Preview | FR-4.12, FR-5.1–FR-5.6 | Export artifact stored |

**Entry variations**

- **Returning teacher (fast path):** Login → Dashboard → Paper Builder → Generate → Preview → Export. Steps 3–6 are skipped because the question bank is already populated. This path must complete in minutes, per Success Criteria.
- **Template-driven path:** Login → Dashboard → Templates → "Use template" → Paper Builder (pre-filled blueprint) → Generate. Reduces step 7 to a confirmation.
- **Rejection loop:** If step 10 returns `rejected`, the teacher returns to step 8 with reviewer comments attached, edits, and resubmits. There is no limit on resubmission rounds; each round is a new paper version and a new audit entry.

**Failure branches**

- OCR fails at step 4 → `textbooks.status = failed`, teacher notified, retry offered (see EH-1). The journey can still continue at step 7 using existing bank content.
- Generation times out at step 7 → the partial job is discarded, the blueprint is preserved so the teacher can retry without re-entering configuration (see EH-2).
- Retrieval cannot satisfy the blueprint at step 7 → the paper is returned with a coverage warning listing unmet slots rather than silently under-filling (see AI-3).

### UJ-2 Coaching Admin: onboarding, governance, and approval

```
  Login
    |
    v
  Dashboard (org health: pending approvals, usage vs. plan)
    |
    +--> Users ------> Invite teacher -> Assign role -> Assign programs
    |
    +--> Programs ---> Create program -> Map board / class / subject / curriculum version
    |
    +--> Templates --> Create / brand / publish template (draft -> active)
    |
    +--> Settings ---> Set org generation policy (allowed modes, AI ceiling, approval rules)
    |
    +--> Question Bank -> Curate: bulk approve / reject / retire questions
    |
    +--> Approvals ---> Review submitted paper
    |                     |
    |                     +-- Approve  -> paper approved -> teacher notified
    |                     |
    |                     '-- Reject   -> comments -> back to teacher (UJ-1 step 8)
    |
    '--> Analytics ---> Coverage, AI vs. retrieved mix, difficulty, teacher activity
```

**Step detail**

| # | Step | Screen | Requirement |
|---|------|--------|-------------|
| 1 | Log in to org admin context | Login | FR-1.1–FR-1.4 |
| 2 | Review org dashboard: pending approvals, usage vs. plan, failed jobs | Dashboard | FR-8.1–FR-8.3 |
| 3 | Invite users, set roles, assign to programs/subjects | Users | FR-1.5–FR-1.7 |
| 4 | Define programs and curriculum mapping | Programs | FR-3.1 |
| 5 | Create and publish paper templates | Templates | FR-6.1–FR-6.6 |
| 6 | Configure org generation policy and approval rules | Settings | FR-4.8, BR-1, BR-6 |
| 7 | Curate the question bank (approve/reject in bulk) | Question Bank | FR-3.6–FR-3.8 |
| 8 | Open a submitted paper, inspect provenance and blueprint reconciliation | Paper Preview | FR-7.2 |
| 9 | Approve or reject with comments | Paper Preview | FR-7.3–FR-7.4 |
| 10 | Monitor analytics and usage against plan | Analytics, Subscriptions | FR-8.1–FR-8.4 |

**Review checklist surfaced at step 8** — the reviewer must be able to see, without leaving the screen: blueprint vs. actual marks reconciliation (BR-3), per-question source badges (BR-4), the AI share of the paper, the Mixed Mode textbook floor status (BR-1), duplicate/similarity warnings (AI-4), and curriculum version alignment (BR-9).

### UJ-3 Super Admin: tenant, curriculum, and AI governance

```
  Login (platform context)
    |
    v
  Platform Dashboard (tenants, job health, error rates, AI spend)
    |
    +--> Organizations -> Create org -> Assign plan -> Seed admin user
    |
    +--> Subscriptions -> Define plans, seats, quotas -> Handle upgrades / expiry
    |
    +--> Global Curriculum -> Publish board / class / subject / chapter trees
    |                           -> Version and supersede curriculum
    |
    +--> Global Question Bank -> Curate shared, cross-tenant questions
    |
    +--> Prompt Versions -> Author (candidate) -> Evaluate -> Promote (active) -> Retire
    |
    +--> Audit & Analytics -> Cross-tenant usage, quality metrics, incident forensics
    |
    '--> Support access -> Enter tenant context (logged, time-boxed, audited)
```

**Step detail**

| # | Step | Screen | Requirement |
|---|------|--------|-------------|
| 1 | Log in with platform-operator privileges | Login | FR-1.1, FR-1.8 |
| 2 | Review platform health: queue depth, failure rate, AI usage | Dashboard | FR-8.2 |
| 3 | Create/configure organizations and seed the first admin | Settings → Organizations | FR-1.5 |
| 4 | Manage subscription plans, seats, and quotas | Subscriptions | FR-8.4, BR-10 |
| 5 | Publish and version global curriculum | Programs (global scope) | BR-9 |
| 6 | Curate the global shared question bank | Question Bank (global scope) | GM-7, BR-7 |
| 7 | Promote a prompt version `candidate → active`, retire the old one | Settings → Prompt Versions | BR-4 |
| 8 | Investigate cross-tenant audit trails and platform analytics | Analytics | FR-8.2, NFR-2 |

Cross-tenant access at steps 6–8 is privileged: every read of tenant-owned data is written to the audit log with actor, tenant, entity, and justification (NFR-2, BR-11).

## Feature Breakdown

Each functional requirement is decomposed into individually implementable and testable sub-requirements. IDs are stable and are referenced by user stories, acceptance criteria, and test plans.

### FR-1 Login and Access

| ID | Sub-requirement | Notes |
|----|-----------------|-------|
| FR-1.1 | Authenticate with email and password | Rate-limited; lockout after repeated failures |
| FR-1.2 | Reset password via emailed, single-use, expiring token | Token invalidated on use |
| FR-1.3 | Maintain a session with refresh and explicit logout | Revocation must take effect immediately |
| FR-1.4 | Select and switch active organization when a user holds multiple memberships | Org context is attached to every request |
| FR-1.5 | Invite a user by email and assign a role on `memberships.role` | Pending invites expire; the membership is created with `status = invited` and grants no access until accepted |
| FR-1.6 | Change or revoke a member's role, or disable a user account | Audited; takes effect on next request. Revocation sets `memberships.status`; account-level disablement sets `users.status`. |
| FR-1.7 | Assign a teacher to programs/subjects to scope their visible content | Optional per org policy |
| FR-1.8 | Enforce role- and tenant-scoped authorization on every endpoint | Deny by default |
| FR-1.9 | Record auth events (login, failure, logout, role change) in the audit log | Append-only |
| FR-1.10 | Support SSO / OAuth as a pluggable provider | Post-v1, no schema change required |

### FR-2 Upload Textbook

| ID | Sub-requirement | Notes |
|----|-----------------|-------|
| FR-2.1 | Upload a file via drag-and-drop or file picker | PDF, DOCX, PNG, JPG (see CN-2) |
| FR-2.2 | Validate file type, size, and page count before accepting | Reject early with a clear reason (CN-1) |
| FR-2.3 | Store the raw file in object storage and record metadata in `textbooks` | Raw file is never mutated |
| FR-2.4 | Tag the upload with board, class, subject, and optional edition/year | Drives chapter mapping |
| FR-2.5 | Enqueue an OCR job and return immediately | Never block the request thread |
| FR-2.6 | Report per-stage progress (`uploaded → ocr → parsed → embedded`) | Real-time updates on OCR Progress |
| FR-2.7 | Notify the uploader on completion or failure | NT-1, NT-2 |
| FR-2.8 | Extract assets into `textbook_assets` typed as paragraph, exercise, question, diagram, table, activity, or formula | Page-level provenance retained |
| FR-2.9 | Map extracted assets to chapters, with manual correction when auto-mapping is uncertain | Confidence surfaced to the user |
| FR-2.10 | Index extracted content for retrieval | Full-text and trigram indexes at v1; versioned embeddings once ADR-007 fires |
| FR-2.11 | Reprocess a textbook, or retry only the failed pages/stages | Idempotent; no duplicate assets |
| FR-2.12 | Delete or archive a textbook and its derived assets | Questions already approved are retained with provenance |

### FR-3 Import Questions

| ID | Sub-requirement | Notes |
|----|-----------------|-------|
| FR-3.1 | Select the target program, curriculum version, subject, and chapter for import | Required before import |
| FR-3.2 | Import from a structured file (CSV/XLSX/JSON) using a documented column mapping | Downloadable template provided |
| FR-3.3 | Validate every row: required fields, marks > 0, valid type, valid difficulty, valid Bloom level | Row-level error report |
| FR-3.4 | Promote extracted `textbook_assets` of kind `question`/`exercise` into question records | Source set to `textbook` or `intext` |
| FR-3.5 | Import previous-year papers and tag the year and board | Source `previous_year` |
| FR-3.6 | De-duplicate on `content_hash` and warn on near-duplicates by similarity | Trigram similarity at v1, embedding similarity once adopted. See AI-4. |
| FR-3.7 | Create imported questions in `draft` and route them for review | Never auto-approved |
| FR-3.8 | Bulk approve, reject, or archive questions | `draft → approved / rejected / archived` |
| FR-3.9 | Edit a question; approved questions fork a new version in the lineage | BR-8 |
| FR-3.10 | Search and filter the bank by chapter, type, marks, difficulty, Bloom level, source, status, and language | Full-text + faceted |
| FR-3.11 | Attach assets to a question (diagram, table, passage) | Required for diagram- and source-based types |
| FR-3.12 | Report import outcome: rows accepted, rejected, duplicated, with a downloadable error file | — |

### FR-4 Generate Paper

This is the platform's core transaction and is decomposed the most finely.

| ID | Sub-requirement | Notes |
|----|-----------------|-------|
| FR-4.1 | **Select Board** | Scoped to boards enabled for the organization |
| FR-4.2 | **Select Class** | Filtered by the selected board |
| FR-4.3 | **Select Subject** | Filtered by board + class; pinned to a curriculum version |
| FR-4.4 | **Select Chapters** | Multi-select, unlimited (CN-3); per-chapter weightage optional |
| FR-4.5 | **Select Paper Pattern** | From a saved pattern/template or built ad hoc (see Paper Pattern) |
| FR-4.6 | **Configure Marks** | Total marks, per-section marks, per-question marks, duration; live reconciliation (CN-4, CN-5) |
| FR-4.7 | **Configure Difficulty** | Target easy/medium/hard distribution, plus optional Bloom-level mix |
| FR-4.8 | **Configure Sources** | Choose a generation mode and per-source share, within org policy and BR-1 |
| FR-4.9 | **Generate Paper** | Validate blueprint → snapshot it → enqueue job → retrieval-first fill → AI gap-fill → validate → return draft |
| FR-4.10 | **Save Draft** | Persist the paper and its blueprint snapshot; resumable later |
| FR-4.11 | **Preview** | Rendered paper with per-question source badges, marks totals, and warnings |
| FR-4.12 | **Export** | Hand off to FR-5 for PDF/DOCX rendering |
| FR-4.13 | Show live generation progress and allow cancellation | Cancellation frees the queue slot |
| FR-4.14 | Swap an individual question for an alternative from the same slot's candidate pool | Keeps blueprint valid |
| FR-4.15 | Lock a question so regeneration cannot replace it | Locks survive regeneration |
| FR-4.16 | Regenerate a single question, a section, or the whole paper | Regeneration creates a new version |
| FR-4.17 | Reorder questions and sections | Numbering recomputed |
| FR-4.18 | Manually add a question from the bank, or author one inline | Inline questions enter the bank as `draft` |
| FR-4.19 | Display a coverage report: requested vs. achieved marks, difficulty, source mix, chapter spread | Blocks submission if BR-3 fails |
| FR-4.20 | Save the current configuration as a reusable template/pattern | Feeds FR-6 |

### FR-5 Export PDF

| ID | Sub-requirement | Notes |
|----|-----------------|-------|
| FR-5.1 | Export a paper version to PDF using the selected template | Deterministic for a given version |
| FR-5.2 | Optionally export a separate answer key / marking scheme | Never embedded in the question paper by default |
| FR-5.3 | Apply org branding: logo, institute name, header/footer, watermark | From template |
| FR-5.4 | Include configurable metadata: exam name, date, duration, total marks, instructions | Editable per export |
| FR-5.5 | Render the job asynchronously and store the artifact in object storage | Progress surfaced in UI |
| FR-5.6 | Provide download via a time-limited signed URL | NFR-2 |
| FR-5.7 | Export DOCX where the template supports it | Editable downstream |
| FR-5.8 | Render multiple sets/variants (shuffled order) of the same paper | Each variant is its own artifact |
| FR-5.9 | Meter each export against the organization's plan | BR-10 |
| FR-5.10 | Keep an export history per paper with actor, template, and timestamp | Re-downloadable |

### FR-6 Save Template

| ID | Sub-requirement | Notes |
|----|-----------------|-------|
| FR-6.1 | Create a template capturing layout, typography, spacing, and branding | Org-scoped |
| FR-6.2 | Create a blueprint pattern capturing sections, marks, types, and difficulty | Reusable across papers |
| FR-6.3 | Clone a platform base template or another org template as a starting point | Copy-on-write |
| FR-6.4 | Preview a template against sample content before publishing | — |
| FR-6.5 | Manage template lifecycle `draft → active → archived` | Archived templates stay valid for past exports |
| FR-6.6 | Set an organization default template | Pre-selected on export |
| FR-6.7 | Apply a template to an existing paper without regenerating questions | Layout-only change |

### FR-7 Approval

| ID | Sub-requirement | Notes |
|----|-----------------|-------|
| FR-7.1 | Submit a paper for approval; blocked unless BR-3 reconciliation passes | `draft → submitted` |
| FR-7.2 | Reviewer queue listing submitted papers with author, program, and age | Sortable |
| FR-7.3 | Approve a paper, optionally with a comment | `review → approved` |
| FR-7.4 | Reject a paper with a mandatory comment | `review → rejected`, returns to author |
| FR-7.5 | Request changes at the individual-question level | Comments anchored to questions |
| FR-7.6 | Enforce separation of duties when org policy requires it | BR-6 |
| FR-7.7 | Record every approval action with reviewer, action, comment, and timestamp | BR-11 |
| FR-7.8 | Notify the author on every state transition | NT-4, NT-5, NT-6 |
| FR-7.9 | Show the full approval history and version diff on the paper | Auditable |
| FR-7.10 | Allow an admin to withdraw approval, reverting to `draft` | Audited; prior exports remain traceable |

### FR-8 Analytics

| ID | Sub-requirement | Notes |
|----|-----------------|-------|
| FR-8.1 | Org dashboard: papers generated, approval throughput, pending approvals, failed jobs | Role-aware |
| FR-8.2 | Platform dashboard: tenants, queue health, job failure rates, AI usage | Super Admin only |
| FR-8.3 | Content quality metrics: AI vs. retrieved share, average difficulty, duplicate rate, rejection rate | Per org, per program |
| FR-8.4 | Curriculum coverage: chapters and outcomes exercised vs. neglected | Drives content gaps |
| FR-8.5 | Usage metering per organization: papers, AI questions, OCR pages, exports | Feeds billing |
| FR-8.6 | Plan-limit visibility with warning thresholds | NT-7 |
| FR-8.7 | Serve analytics from daily rollups, rebuildable from source data | NFR-1 |
| FR-8.8 | Export any analytics view to CSV | — |
| FR-8.9 | Filter every view by date range, program, subject, and teacher | — |

## User Stories

Stories are grouped by feature and written as `As a [role], I want [goal], so that [benefit].` Each story carries a priority (**P0** must-have for v1, **P1** important, **P2** nice-to-have) and traces to sub-requirements.

### Login and Access

| ID | Story | Priority | Traces to |
|----|-------|----------|-----------|
| US-1.1 | As a **Teacher**, I want to log in with my email and password, so that I can reach my organization's content securely. | P0 | FR-1.1, FR-1.3 |
| US-1.2 | As a **Teacher**, I want to reset my password myself, so that I am not blocked waiting for an admin. | P0 | FR-1.2 |
| US-1.3 | As a **Teacher** who works with two institutes, I want to switch my active organization, so that I never mix up content between them. | P1 | FR-1.4 |
| US-1.4 | As a **Coaching Admin**, I want to invite teachers by email and set their role, so that my team can start working without me sharing credentials. | P0 | FR-1.5 |
| US-1.5 | As a **Coaching Admin**, I want to restrict a teacher to their assigned subjects, so that they only see content relevant to them. | P1 | FR-1.7 |
| US-1.6 | As a **Coaching Admin**, I want to revoke access immediately when a teacher leaves, so that our question bank stays protected. | P0 | FR-1.6, FR-1.8 |
| US-1.7 | As a **Super Admin**, I want every authentication and role change logged, so that I can investigate any access dispute. | P0 | FR-1.9 |
| US-1.8 | As a **Coaching Admin**, I want my institute to sign in with our existing SSO, so that we do not manage another password. | P2 | FR-1.10 |

### Upload Textbook

| ID | Story | Priority | Traces to |
|----|-------|----------|-----------|
| US-2.1 | As a **Teacher**, I want to upload my prescribed textbook as a PDF, so that the platform can build questions from the exact book my class uses. | P0 | FR-2.1–FR-2.3 |
| US-2.2 | As a **Teacher**, I want to upload scanned images of a chapter, so that I can use material I only have on paper. | P0 | FR-2.1, CN-2 |
| US-2.3 | As a **Teacher**, I want to be told immediately if my file is too large or the wrong type, so that I do not wait for a failure. | P0 | FR-2.2 |
| US-2.4 | As a **Teacher**, I want to tag the upload with board, class, and subject, so that extracted content lands in the right place. | P0 | FR-2.4 |
| US-2.5 | As a **Teacher**, I want to see OCR progress and leave the page, so that I am not stuck watching a spinner. | P0 | FR-2.5–FR-2.6 |
| US-2.6 | As a **Teacher**, I want a notification when processing finishes, so that I can come back at the right moment. | P0 | FR-2.7, NT-1 |
| US-2.7 | As a **Teacher**, I want exercises, in-text passages, diagrams, and tables identified separately, so that different generation modes have the right raw material. | P0 | FR-2.8 |
| US-2.8 | As a **Teacher**, I want to correct a wrongly mapped chapter, so that my chapter-wise papers stay accurate. | P1 | FR-2.9 |
| US-2.9 | As a **Teacher**, I want to retry only the pages that failed, so that I do not re-process a 400-page book. | P1 | FR-2.11 |
| US-2.10 | As a **Coaching Admin**, I want to archive an outdated edition, so that teachers stop generating from a superseded book. | P1 | FR-2.12, BR-9 |

### Import Questions

| ID | Story | Priority | Traces to |
|----|-------|----------|-----------|
| US-3.1 | As a **Coaching Admin**, I want to bulk-import our existing question bank from a spreadsheet, so that years of institute material is available on day one. | P0 | FR-3.2–FR-3.3 |
| US-3.2 | As a **Coaching Admin**, I want a downloadable import template, so that my team formats the file correctly the first time. | P0 | FR-3.2 |
| US-3.3 | As a **Coaching Admin**, I want a row-by-row error report, so that I can fix only the bad rows and re-import. | P0 | FR-3.12 |
| US-3.4 | As a **Teacher**, I want textbook exercises turned into reviewable questions automatically, so that I do not retype them. | P0 | FR-3.4 |
| US-3.5 | As a **Teacher**, I want previous-year papers imported and tagged by year, so that I can build a PYQ-only practice paper. | P0 | FR-3.5, GM-5 |
| US-3.6 | As a **Coaching Admin**, I want duplicates detected on import, so that our bank does not fill with near-identical questions. | P0 | FR-3.6, AI-4 |
| US-3.7 | As a **Coaching Admin**, I want imported questions to arrive as drafts, so that nothing unreviewed reaches a real paper. | P0 | FR-3.7, BR-2 |
| US-3.8 | As a **Coaching Admin**, I want to bulk approve a verified batch, so that reviewing thousands of questions is practical. | P0 | FR-3.8 |
| US-3.9 | As a **Teacher**, I want to edit a question without destroying its history, so that papers already exported stay reproducible. | P0 | FR-3.9, BR-8 |
| US-3.10 | As a **Teacher**, I want to filter the bank by chapter, marks, difficulty, and type, so that I can find the exact question I need. | P0 | FR-3.10 |
| US-3.11 | As a **Teacher**, I want to attach a diagram or passage to a question, so that diagram-based and source-based questions are complete. | P0 | FR-3.11 |

### Generate Paper

| ID | Story | Priority | Traces to |
|----|-------|----------|-----------|
| US-4.1 | As a **Teacher**, I want to generate a full question paper in a few clicks, so that I can prepare tomorrow's exam quickly. | P0 | FR-4.1–FR-4.9 |
| US-4.2 | As a **Teacher**, I want to pick board, class, subject, and chapters, so that the paper matches exactly what I taught. | P0 | FR-4.1–FR-4.4 |
| US-4.3 | As a **Teacher**, I want to set chapter weightage, so that the chapters I emphasised carry more marks. | P1 | FR-4.4 |
| US-4.4 | As a **Teacher**, I want to choose a standard board pattern, so that my paper looks like the real exam. | P0 | FR-4.5, PP-1 |
| US-4.5 | As a **Teacher**, I want total and section marks validated as I type, so that I never generate an unbalanced paper. | P0 | FR-4.6, CN-4, CN-5 |
| US-4.6 | As a **Teacher**, I want to set an easy/medium/hard split, so that the paper is fair to the whole class. | P0 | FR-4.7 |
| US-4.7 | As a **Teacher**, I want to choose where questions come from — textbook, in-text, PYQ, our bank, or AI — so that I control the character of the paper. | P0 | FR-4.8, GM-1–GM-7 |
| US-4.8 | As a **Coaching Admin**, I want to cap how much of any paper may be AI-generated, so that our papers stay grounded in the prescribed book. | P0 | FR-4.8, BR-1 |
| US-4.9 | As a **Teacher**, I want existing approved questions used before anything new is generated, so that I get material I already trust. | P0 | FR-4.9, BR-2, AI-1 |
| US-4.10 | As a **Teacher**, I want to see progress while the paper generates and cancel if I made a mistake, so that I am not stuck waiting. | P0 | FR-4.13 |
| US-4.11 | As a **Teacher**, I want to save a draft and finish later, so that an interruption does not cost me my configuration. | P0 | FR-4.10 |
| US-4.12 | As a **Teacher**, I want to swap a question I dislike for an alternative of the same marks and type, so that the paper stays balanced. | P0 | FR-4.14 |
| US-4.13 | As a **Teacher**, I want to lock the questions I like before regenerating, so that regeneration does not undo my choices. | P0 | FR-4.15 |
| US-4.14 | As a **Teacher**, I want to regenerate just one section, so that I do not rebuild a paper that is mostly right. | P0 | FR-4.16 |
| US-4.15 | As a **Teacher**, I want to reorder questions and sections, so that the paper flows the way I teach. | P1 | FR-4.17 |
| US-4.16 | As a **Teacher**, I want to add my own question inline, so that I can include something specific to my class. | P0 | FR-4.18 |
| US-4.17 | As a **Teacher**, I want to see each question's source on the preview, so that I know what is textbook-derived and what is AI-written. | P0 | FR-4.11, BR-4 |
| US-4.18 | As a **Teacher**, I want a coverage report of requested vs. achieved marks and difficulty, so that I can trust the paper before submitting. | P0 | FR-4.19, BR-3 |
| US-4.19 | As a **Teacher**, I want to be warned rather than silently short-changed when the bank cannot fill a slot, so that I can adjust my blueprint. | P0 | FR-4.19, AI-3 |
| US-4.20 | As a **Teacher**, I want to save this configuration as a reusable pattern, so that next term's paper takes seconds. | P1 | FR-4.20, FR-6.2 |
| US-4.21 | As a **Coaching Admin**, I want generation pinned to a curriculum version, so that a syllabus change never silently alters our papers. | P1 | BR-9 |

### Export PDF

| ID | Story | Priority | Traces to |
|----|-------|----------|-----------|
| US-5.1 | As a **Teacher**, I want to export a print-ready PDF, so that I can photocopy it for the exam hall. | P0 | FR-5.1 |
| US-5.2 | As a **Teacher**, I want a separate answer key, so that I can mark quickly without exposing answers to students. | P0 | FR-5.2 |
| US-5.3 | As a **Coaching Admin**, I want our logo and institute name on every paper, so that our material is branded consistently. | P0 | FR-5.3 |
| US-5.4 | As a **Teacher**, I want to set exam name, date, duration, and instructions at export time, so that the header is correct. | P0 | FR-5.4 |
| US-5.5 | As a **Teacher**, I want the download link to be private, so that our papers do not leak before the exam. | P0 | FR-5.6, NFR-2 |
| US-5.6 | As a **Teacher**, I want an editable DOCX, so that I can make last-minute manual tweaks. | P1 | FR-5.7 |
| US-5.7 | As a **Coaching Admin**, I want shuffled Set A / Set B variants, so that we can reduce copying between benches. | P1 | FR-5.8 |
| US-5.8 | As a **Coaching Admin**, I want to see who exported what and when, so that I can trace a leak. | P1 | FR-5.10, BR-11 |

### Save Template

| ID | Story | Priority | Traces to |
|----|-------|----------|-----------|
| US-6.1 | As a **Coaching Admin**, I want to define our house layout once, so that every teacher's paper looks the same. | P0 | FR-6.1 |
| US-6.2 | As a **Coaching Admin**, I want to save our standard blueprint pattern, so that teachers stop reinventing section structures. | P0 | FR-6.2 |
| US-6.3 | As a **Coaching Admin**, I want to start from a platform base template, so that I do not design from scratch. | P1 | FR-6.3 |
| US-6.4 | As a **Coaching Admin**, I want to preview a template before publishing, so that a broken layout never reaches teachers. | P0 | FR-6.4 |
| US-6.5 | As a **Coaching Admin**, I want to archive an old template without breaking past exports, so that history stays intact. | P0 | FR-6.5 |
| US-6.6 | As a **Teacher**, I want a default template pre-selected, so that exporting is one click. | P1 | FR-6.6 |
| US-6.7 | As a **Teacher**, I want to restyle an existing paper with a different template, so that I do not regenerate content for a layout change. | P1 | FR-6.7 |

### Approval

| ID | Story | Priority | Traces to |
|----|-------|----------|-----------|
| US-7.1 | As a **Teacher**, I want to submit my paper for approval, so that it can be cleared for distribution. | P0 | FR-7.1 |
| US-7.2 | As a **Coaching Admin**, I want to approve papers, so that only verified papers are distributed to students. | P0 | FR-7.3 |
| US-7.3 | As a **Coaching Admin**, I want a queue of pending submissions with ages, so that nothing sits unreviewed before an exam. | P0 | FR-7.2 |
| US-7.4 | As a **Coaching Admin**, I want to see blueprint reconciliation and source mix while reviewing, so that I can judge quality in seconds. | P0 | FR-7.2, FR-4.19 |
| US-7.5 | As a **Coaching Admin**, I want to reject with a required comment, so that the teacher knows exactly what to fix. | P0 | FR-7.4 |
| US-7.6 | As a **Coaching Admin**, I want to comment on a single question, so that feedback is precise. | P1 | FR-7.5 |
| US-7.7 | As a **Coaching Admin**, I want authors blocked from approving their own papers, so that review is meaningful. | P1 | FR-7.6, BR-6 |
| US-7.8 | As a **Teacher**, I want to be notified the moment my paper is approved or rejected, so that I can act immediately. | P0 | FR-7.8, NT-5, NT-6 |
| US-7.9 | As a **Coaching Admin**, I want the full approval history and version diff, so that I can see what changed between rounds. | P1 | FR-7.9 |
| US-7.10 | As a **Coaching Admin**, I want to withdraw an approval if we spot an error, so that a flawed paper is not treated as final. | P1 | FR-7.10 |

### Analytics

| ID | Story | Priority | Traces to |
|----|-------|----------|-----------|
| US-8.1 | As a **Coaching Admin**, I want to see how many papers each teacher produced, so that I can understand workload and adoption. | P1 | FR-8.1, FR-8.9 |
| US-8.2 | As a **Coaching Admin**, I want to see the AI vs. retrieved share across our papers, so that I can confirm we stay textbook-grounded. | P0 | FR-8.3 |
| US-8.3 | As a **Coaching Admin**, I want curriculum coverage gaps highlighted, so that I know which chapters need more questions. | P1 | FR-8.4 |
| US-8.4 | As a **Coaching Admin**, I want to see usage against our plan with early warnings, so that we are never blocked mid-exam-season. | P0 | FR-8.5–FR-8.6, NT-7, NT-15 |
| US-8.5 | As a **Coaching Admin**, I want to export analytics to CSV, so that I can present them to management. | P2 | FR-8.8 |
| US-8.6 | As a **Super Admin**, I want queue health and job failure rates, so that I can catch platform degradation before customers report it. | P0 | FR-8.2 |
| US-8.7 | As a **Super Admin**, I want cross-tenant AI usage, so that I can manage cost and capacity. | P0 | FR-8.2, FR-8.5 |

## Acceptance Criteria

Each criterion is written to be independently verifiable. `✅` denotes a condition that must hold for the feature to be accepted.

### AC-1 Login and Access

- ✅ A user with valid credentials receives a session scoped to exactly one active organization.
- ✅ Invalid credentials return a generic failure that does not reveal whether the email exists.
- ✅ Repeated failed attempts trigger rate limiting/lockout, and each attempt is logged.
- ✅ A password reset link works once and expires; a used or expired link is rejected.
- ✅ A user with two memberships can switch organizations, and all subsequently loaded data belongs only to the newly selected organization.
- ✅ A Teacher requesting an admin-only endpoint receives an authorization failure, not partial data.
- ✅ A revoked membership loses access on the next request; no cached session survives revocation.
- ✅ A Teacher scoped to Subject A cannot see questions, textbooks, or papers belonging solely to Subject B.
- ✅ Login, logout, failed login, and role changes each produce exactly one audit record with actor, org, and timestamp.

### AC-2 Upload Textbook

- ✅ A valid PDF, DOCX, PNG, or JPG within the size limit uploads successfully and appears with status `uploaded`.
- ✅ A file exceeding 500 MB, or of an unsupported type, is rejected before upload completes with a message naming the actual limit.
- ✅ The raw file is retrievable unchanged from object storage after processing.
- ✅ Status advances `uploaded → ocr → parsed → embedded`, and the OCR Progress screen reflects each transition without a manual refresh.
- ✅ On completion the uploader receives an "OCR Completed" notification; on failure, an "Upload Failed" notification.
- ✅ Extracted assets are persisted with kind, page number, and textbook reference; at least paragraph, exercise, question, diagram, and table kinds are distinguished.
- ✅ Assets are mapped to chapters; unmapped or low-confidence assets are flagged for manual mapping rather than silently attached to the wrong chapter.
- ✅ Retrying a failed textbook does not create duplicate assets.
- ✅ A failed textbook shows status `failed` with a human-readable reason and a retry action.
- ✅ Archiving a textbook removes it from generation source selection but does not delete questions already approved from it.

### AC-3 Import Questions

- ✅ A correctly formatted file imports with a summary of rows accepted, rejected, and skipped as duplicates.
- ✅ A file with invalid rows imports the valid rows and returns a downloadable error report naming each bad row and reason.
- ✅ Rows with missing required fields, marks ≤ 0, or an unrecognised type/difficulty/Bloom value are rejected.
- ✅ Every imported question is stored with source, type, marks, difficulty, Bloom level, language, chapter, and curriculum version.
- ✅ An exact duplicate (same `content_hash`) is not inserted twice.
- ✅ A near-duplicate above the similarity threshold is flagged for review rather than silently accepted.
- ✅ Imported questions have status `draft` and never appear in a generated paper before approval.
- ✅ Bulk approve moves all selected questions to `approved` in one action and writes one audit record per question.
- ✅ Editing an `approved` question creates a new version; the previous version remains retrievable and exactly one version is marked current.
- ✅ Filtering by chapter + type + marks + difficulty returns only questions matching all filters.

### AC-4 Generate Paper

- ✅ A Teacher can generate a paper end to end from board/class/subject/chapter selection through preview.
- ✅ Total marks of the generated paper match the blueprint exactly; every section's marks match its configured value.
- ✅ The generated paper contains no duplicate questions — no question appears twice within one paper.
- ✅ The paper is exported successfully to PDF from the preview screen.
- ✅ Only chapters selected in the blueprint contribute questions.
- ✅ The achieved difficulty distribution matches the requested distribution within the stated tolerance, or the deviation is reported in the coverage report.
- ✅ The requested source mix is honoured; in Mixed Mode at least 70% of questions are textbook-derived, or the paper is flagged and the shortfall is stated (BR-1).
- ✅ Every question in the paper displays a source badge; AI-generated questions additionally record the prompt version used.
- ✅ Retrieval is attempted before generation: when the approved bank can satisfy the blueprint, zero AI questions are produced.
- ✅ Generation runs asynchronously with live progress; cancelling stops the job and leaves no partial paper.
- ✅ Saving a draft and returning later restores the identical blueprint and question set.
- ✅ Swapping a question replaces it with an alternative of the same marks and type, and totals remain reconciled.
- ✅ A locked question is never replaced by any regeneration of its section or of the whole paper.
- ✅ Regenerating a section leaves all other sections byte-identical.
- ✅ Reordering renumbers questions and sections consistently in the preview and in every export.
- ✅ A manually authored question is added to the paper and enters the bank as `draft`.
- ✅ The coverage report shows requested vs. achieved marks, difficulty, source mix, and chapter spread.
- ✅ Submission is blocked while the blueprint does not reconcile, with the specific mismatch named.
- ✅ Regenerating the same blueprint snapshot produces a paper that still satisfies every blueprint constraint.

### AC-5 Export PDF

- ✅ Exporting a paper version produces a downloadable PDF whose content matches the preview.
- ✅ The answer key exports as a separate artifact and is never included in the question-paper file unless explicitly requested.
- ✅ Org logo, name, header/footer, and watermark from the template appear on the output.
- ✅ Exam name, date, duration, total marks, and instructions appear exactly as entered.
- ✅ Export runs asynchronously; the user is notified and the artifact is stored durably.
- ✅ The download URL is signed and expires; an expired URL is rejected.
- ✅ DOCX export opens without corruption in a standard word processor when the template supports it.
- ✅ Variant sets contain the same questions in a different order, each as its own artifact.
- ✅ Each export increments the organization's export usage counter exactly once.
- ✅ Export history lists actor, template, format, and timestamp, and past artifacts remain downloadable.

### AC-6 Save Template

- ✅ A saved template is selectable on export by any user in the organization with permission.
- ✅ A saved blueprint pattern pre-fills the Paper Builder with the stored sections, marks, types, and difficulty.
- ✅ Cloning a base template creates an independent copy; editing the copy does not change the original.
- ✅ Template preview renders sample content without saving.
- ✅ Status transitions `draft → active → archived` are enforced; an archived template cannot be selected for new exports but past exports still render.
- ✅ The org default template is pre-selected on the export dialog.
- ✅ Applying a new template to an existing paper changes layout only — the question set is unchanged.

### AC-7 Approval

- ✅ A reconciled paper can be submitted and moves to `submitted`.
- ✅ A paper that fails blueprint reconciliation cannot be submitted.
- ✅ The reviewer queue lists all submitted papers in the organization with author, program, and time since submission.
- ✅ Approving moves the paper to `approved` and notifies the author.
- ✅ Rejecting requires a comment; without one the action is refused.
- ✅ A rejected paper returns to the author in an editable state with reviewer comments attached.
- ✅ Where separation of duties is enabled, the submitter cannot be the approver.
- ✅ Every approval action writes one record with reviewer, action, comment, and timestamp.
- ✅ The approval history shows every round, and a diff between versions is viewable.
- ✅ Withdrawing approval returns the paper to `draft` and is audited; existing exports remain traceable to the version that was approved.
- ✅ Only `approved` papers can be marked final for distribution.

### AC-8 Analytics

- ✅ The org dashboard shows papers generated, pending approvals, approval throughput, and failed jobs for the selected period.
- ✅ The platform dashboard is visible only to Super Admins and shows queue depth, job failure rate, and AI usage.
- ✅ AI vs. retrieved share, average difficulty, duplicate rate, and rejection rate are reported per org and per program.
- ✅ Curriculum coverage identifies chapters with zero or low question usage.
- ✅ Usage counters for papers, AI questions, OCR pages, and exports match the underlying event log.
- ✅ Crossing a plan-limit warning threshold produces a notification before the limit blocks work.
- ✅ Analytics views load within the interactive performance target and are served from rollups.
- ✅ CSV export of any view contains the same rows as the on-screen view.
- ✅ Date-range, program, subject, and teacher filters apply consistently across every view.

## Constraints

Hard product limits. Values marked *(plan-dependent)* may be tightened by subscription tier but never loosened beyond the platform maximum. Every limit must be enforced server-side and surfaced in the UI before the user hits it.

### Upload and ingestion

| ID | Constraint | Value | Enforcement |
|----|-----------|-------|-------------|
| CN-1 | Maximum upload size | **500 MB** per file | Rejected at request validation, before storage |
| CN-2 | Supported file types | **PDF, DOCX, Image (PNG, JPG/JPEG)** | Type + magic-byte check; unsupported types rejected |
| CN-3 | Maximum chapters per paper | **Unlimited** | No cap; retrieval performance must scale with selection |
| CN-4 | Maximum paper size | **500 marks** | Blueprint validation blocks generation above this |
| CN-5 | Minimum paper size | **5 marks** | Blueprint validation blocks generation below this |
| CN-6 | Maximum pages per textbook | 1,000 pages *(plan-dependent)* | Checked after page count is known; job fails fast with a clear reason |
| CN-7 | Concurrent ingestion jobs per organization | Bounded by plan | Excess jobs queue rather than fail |
| CN-8 | Raw upload retention | Retained for the life of the textbook record | Deleting a textbook deletes the raw file |

### Paper and blueprint

| ID | Constraint | Value |
|----|-----------|-------|
| CN-9 | Marks per question | 1–50 |
| CN-10 | Sections per paper | 1–20 |
| CN-11 | Questions per paper | 1–200 |
| CN-12 | Section marks must sum to total marks | Exact match required (BR-3) |
| CN-13 | Difficulty distribution must sum to 100% | Tolerance for rounding stated in the blueprint |
| CN-14 | Source mix must sum to 100% and respect BR-1 | Validated before the job is enqueued |
| CN-15 | Duration | 5–360 minutes |
| CN-16 | Variant sets per paper | 1–8 |

### Platform and tenancy

| ID | Constraint | Value |
|----|-----------|-------|
| CN-17 | An organization's data is never readable by another organization | Absolute (BR-7) |
| CN-18 | Users, papers, questions, and textbooks per organization | Unlimited by design; bounded by plan quota |
| CN-19 | Papers, AI questions, OCR pages, and exports per billing period | Plan quota (BR-10) |
| CN-20 | Export download link validity | Time-limited signed URL |
| CN-21 | Languages supported per question | One primary language per question record |
| CN-22 | Audit and usage records | Append-only; never updated or deleted |

## Generation Modes

Generation modes are the platform's primary differentiator. A mode determines **where questions come from**, not how they are formatted. Every mode is retrieval-first where retrieval is possible, and every question produced under any mode carries its source on the record (BR-4).

Modes map to the `source` values used throughout the schema: `textbook`, `intext`, `ai_generated`, `previous_year`, `institute_bank`, plus the global shared bank.

### GM-1 Textual Questions

Questions taken from the **exercises, question sets, and end-of-chapter problems printed in the textbook**.

- **Raw material:** `textbook_assets` of kind `exercise` and `question`.
- **Source recorded:** `textbook`.
- **Behaviour:** Pure retrieval. No AI authoring — extraction, cleanup, and classification only.
- **Use case:** "Give me a paper made only of the questions in the book," the most trusted mode for board-exam preparation.
- **Requires:** At least one textbook in `embedded` status for the selected chapters.
- **Fallback:** If the selected chapters have insufficient exercise questions, the shortfall is reported (AI-3); AI does not silently substitute.

### GM-2 In-text Questions

Questions derived from the **explanatory body of the chapter** — the narrative paragraphs, definitions, examples, tables, and diagrams — rather than from printed exercises.

- **Raw material:** `textbook_assets` of kind `paragraph`, `table`, `diagram`, `activity`, `formula`.
- **Source recorded:** `intext`.
- **Behaviour:** AI-assisted extraction grounded strictly in the supplied passage. The model is given the passage and must produce questions answerable from it; every question retains a page/asset reference for verification.
- **Use case:** Testing comprehension of material the textbook explains but never asks about — where most real classroom questions come from.
- **Guardrail:** A question whose answer is not present in the cited asset is rejected by validation (AI-5).

### GM-3 AI Generated

Completely new questions authored by the model on the selected topics.

- **Raw material:** Curriculum topic/outcome definitions, plus retrieved exemplars for style.
- **Source recorded:** `ai_generated`, with the `prompt_versions` identifier attached (BR-4).
- **Behaviour:** Used to fill blueprint slots retrieval cannot satisfy, or when the teacher explicitly wants fresh material.
- **Use case:** Practice sets that must not repeat known questions; unusual mark/type combinations absent from the bank.
- **Guardrails:** Scope validation against the selected chapters, answer-correctness validation, difficulty calibration, and duplicate rejection (AI-2, AI-4, AI-5). All output is `draft` until reviewed.

### GM-4 Mixed Mode

A controlled combination of the above, and the recommended default.

- **Behaviour:** The teacher (within org policy) sets a share per source; the generator fills the blueprint respecting those shares, retrieval-first.
- **Hard rule:** **At least 70% of questions must be textbook-derived** (`textbook` + `intext`) — BR-1. If retrieval cannot reach the floor, the system reduces the AI share and reports the gap, or flags the paper; it never quietly breaches the floor.
- **Use case:** Papers that feel like the textbook but are not verbatim-guessable.
- **Reporting:** The coverage report states the achieved share per source and whether the floor was met.

### GM-5 Previous Year Questions

Questions drawn from imported past board or institute papers.

- **Raw material:** Questions imported via FR-3.5, tagged with year and board.
- **Source recorded:** `previous_year`.
- **Behaviour:** Pure retrieval, filterable by year range and board.
- **Use case:** Trend-based revision papers; "last 10 years" practice sets.
- **Guardrail:** Year and board provenance must be present; a PYQ without a year is not eligible for this mode.

### GM-6 Institute Question Bank

The organization's own curated questions.

- **Raw material:** Questions with source `institute_bank`, plus any question the org has authored or approved.
- **Source recorded:** `institute_bank`.
- **Behaviour:** Pure retrieval, restricted to the organization (BR-7).
- **Use case:** Institutes with decades of proprietary material who want the platform's assembly and formatting, not its content.

### GM-7 Global Question Bank

Platform-curated questions shared across all tenants.

- **Raw material:** The global shared bank, governed by Super Admins (UJ-3 step 6).
- **Behaviour:** Read-only retrieval for tenants; only Super Admins add or retire entries.
- **Use case:** New organizations with an empty bank who need usable papers on day one.
- **Guardrail:** Global content is never editable in place by a tenant; using it in a paper copies a reference, and any tenant edit forks a tenant-owned version (BR-8).

### Mode comparison

| Mode | AI authoring | Source value | Needs textbook | Primary strength |
|------|--------------|--------------|----------------|------------------|
| GM-1 Textual | No | `textbook` | Yes | Maximum trust and exam fidelity |
| GM-2 In-text | Grounded | `intext` | Yes | Depth of coverage beyond printed exercises |
| GM-3 AI Generated | Yes | `ai_generated` | No | Unlimited fresh material |
| GM-4 Mixed | Bounded (≤30%) | Multiple | Yes | Best default balance |
| GM-5 Previous Year | No | `previous_year` | No | Exam-trend alignment |
| GM-6 Institute Bank | No | `institute_bank` | No | Proprietary quality |
| GM-7 Global Bank | No | Global shared | No | Cold-start coverage |

### Mode selection rules

- A mode requiring textbook content is disabled in the UI when no textbook for the selected chapters has reached `embedded`, with the reason shown.
- Organization policy may disable specific modes (for example, forbidding GM-3 entirely) — Settings, UJ-2 step 6.
- Mode is recorded on the blueprint snapshot, so a paper always shows which mode produced it.

## Question Types

Every question type is a first-class record type with its own required fields, validation, rendering rules, and export layout. Type is stored on the question and constrains the blueprint (a section can be restricted to specific types).

| ID | Type | Typical marks | Required fields | Validation | Export note |
|----|------|---------------|-----------------|------------|-------------|
| QT-1 | **MCQ** | 1 | Stem, 4 options, exactly one correct index | Options distinct; exactly one key; no "all of the above" unless allowed | Options labelled (a)–(d) |
| QT-2 | **True / False** | 1 | Statement, boolean answer | Statement must be unambiguously decidable | Rendered as a statement line |
| QT-3 | **One Word** | 1 | Question, expected answer, accepted synonyms | Answer must be a single word/short phrase | Answer blank line |
| QT-4 | **Fill in the Blank** | 1 | Sentence with one or more blanks, answer per blank | Blank count = answer count | Blanks as underscores |
| QT-5 | **Short Answer** | 2–3 | Question, model answer, key points | Model answer present; length appropriate to marks | Ruled answer space |
| QT-6 | **Long Answer** | 5+ | Question, model answer, marking scheme | Marking scheme point count consistent with marks | Extended answer space |
| QT-7 | **Assertion–Reason** | 1 | Assertion, Reason, correct relationship option | Both statements independently evaluable | Standard four-option relationship set |
| QT-8 | **Case Study** | 4–5 | Case passage, 2+ sub-questions with own marks | Sub-question marks sum to the parent marks | Passage printed once, sub-parts nested |
| QT-9 | **Numerical** | 2–5 | Problem, numeric answer, units, solution steps | Units required; tolerance stated where relevant | Working space provided |
| QT-10 | **Diagram Based** | 2–5 | Diagram asset, question, expected labels/answer | A diagram asset must be attached (FR-3.11) | Image placed inline at print resolution |
| QT-11 | **Match the Following** | 2–5 | Column A items, Column B items, correct pairing | Pairings complete; distractors allowed and declared | Two-column table |
| QT-12 | **Very Short Answer** | 1–2 | Question, concise answer | Answer length bounded | Single answer line |
| QT-13 | **Source Based** | 3–5 | Source extract (text/table/map), 2+ sub-questions | Every sub-question answerable from the extract | Extract printed once, sub-parts nested |

**Cross-cutting rules**

- Marks on a question must be consistent with its type's allowed range; the blueprint cannot request 5-mark MCQs unless the org explicitly permits it.
- Composite types (QT-8, QT-13) occupy one blueprint slot but carry sub-question marks that must sum to the slot's marks.
- Types requiring an attached asset (QT-10, and QT-13 where the source is an image) cannot be AI-generated without an available asset; the slot falls back to retrieval or is reported as unmet (AI-3).
- Answer keys render per type: MCQ shows the option letter, numerical shows the value with units, long answer shows the marking scheme.
- Every type must render correctly in both PDF and DOCX export, and in the on-screen preview.

## Paper Pattern

A paper pattern is the structural template for a paper: how many sections, what each section contains, and how marks are distributed. Patterns are saved and reused (FR-6.2) and are snapshotted onto every generated paper for reproducibility.

### Pattern anatomy

```
Paper
 |- Metadata          : exam name, board, class, subject, duration, total marks
 |- General instructions
 |- Section A         : question count x marks each, allowed types, difficulty target
 |     |- Q1 .. Q10
 |- Section B         : ...
 |- Section C         : ...
 '- Optional: internal choice, optional questions, variant sets
```

### Worked example — 40-mark unit test

| Section | Structure | Question type | Marks | Difficulty target |
|---------|-----------|---------------|-------|-------------------|
| **Section A** | 10 × 1 | MCQ | 10 | Easy 70%, Medium 30% |
| **Section B** | 5 × 2 | Short Answer | 10 | Medium |
| **Section C** | 4 × 5 | Long Answer | 20 | Medium 50%, Hard 50% |
| | | **Total** | **40** | |

```
Section A - 10 x 1 mark  = 10 marks  (MCQ)
Section B -  5 x 2 marks = 10 marks  (Short Answer)
Section C -  4 x 5 marks = 20 marks  (Long Answer)
                          ----------
                Total      40 marks
```

### Worked example — 80-mark board-style paper

| Section | Structure | Types | Marks |
|---------|-----------|-------|-------|
| A | 20 × 1 | MCQ, Assertion–Reason, One Word, Fill in the Blank | 20 |
| B | 6 × 2 | Very Short Answer | 12 |
| C | 7 × 3 | Short Answer | 21 |
| D | 3 × 5 | Long Answer (internal choice) | 15 |
| E | 2 × 6 | Case Study / Source Based | 12 |
| | | **Total** | **80** |

### Pattern rules

| ID | Rule |
|----|------|
| PP-1 | A pattern may be selected from saved org patterns, platform base patterns, or built ad hoc (FR-4.5). |
| PP-2 | Section marks must equal question count × marks per question, and section totals must sum to the paper total (CN-12). |
| PP-3 | Each section declares allowed question types; the generator never places a disallowed type in a section. |
| PP-4 | Each section may declare its own difficulty target; the paper-level target is the marks-weighted aggregate. |
| PP-5 | Chapter weightage is defined at paper level and distributed across sections proportionally unless overridden per section. |
| PP-6 | Internal choice ("attempt any 3 of 4") is expressed as extra questions flagged as optional; optional questions do not count toward total marks. |
| PP-7 | Section ordering and numbering are continuous across the paper by default and recomputed on reorder (FR-4.17). |
| PP-8 | A pattern is versioned; editing an `active` pattern creates a new version, leaving past papers' snapshots intact. |
| PP-9 | The pattern is stored in the paper's blueprint snapshot so the paper remains reproducible after the pattern changes. |
| PP-10 | Instructions text is part of the pattern and is rendered above Section A on export. |

## AI Rules

These rules govern when the AI is invoked, what it is allowed to produce, and when its output is rejected. They are product rules, not implementation detail: the behaviour must be observable and testable from outside the system.

### Invocation rules

| ID | Rule | Behaviour |
|----|------|-----------|
| **AI-1** | **If retrieval has enough questions → NO AI generation.** | When the approved question bank satisfies every blueprint slot for the requested chapters, types, marks, and difficulty, the AI is not called at all. The paper reports zero AI-generated questions and zero AI usage is metered. |
| **AI-2** | **If difficulty is not balanced → AI regenerates only the missing questions.** | The generator compares the achieved difficulty distribution against the target. It does not rebuild the paper; it regenerates only the specific slots causing the imbalance, leaving satisfied slots untouched. |
| **AI-3** | If retrieval and generation together cannot fill a slot → report, do not silently under-fill. | Unmet slots are listed in the coverage report with the reason (no eligible questions / type unavailable / asset missing). The paper is returned as a draft with warnings rather than quietly short of marks. |
| AI-4 | **If duplicate similarity > 90% → Reject.** | A generated question whose similarity to any existing question in scope exceeds 90% is discarded and regenerated. Exact matches are caught by `content_hash`; near-matches by embedding similarity. Repeated rejection for the same slot escalates to AI-3. |
| AI-5 | Every AI question must be validated **and its answer verified** before it enters a paper. | Validation covers structure (required fields for its type), scope (answerable from the selected chapters/curriculum), difficulty calibration, and language. **Correctness is verified by execution, not by judgement**: numerical answers are computed from the worked solution and must match; MCQs must have exactly one correct option. An LLM's own opinion of correctness is advisory and never the gate (ADR-010). Failing output is regenerated, not patched. |
| AI-6 | AI output is never auto-approved. | AI-generated questions enter as `draft`. Approval is a human action (FR-3.8). |
| AI-7 | Grounded modes must cite their source asset. | GM-2 (in-text) questions must reference the `textbook_assets` record they were derived from; a question without a citation is rejected. |
| AI-8 | Provenance is mandatory. | Every AI question records source `ai_generated` and the `prompt_versions` identifier that produced it (BR-4). |
| AI-9 | The AI share is capped by mode and policy. | In Mixed Mode the AI share cannot exceed 30% (BR-1); organization policy may cap it lower or disable AI generation entirely. |
| AI-10 | Retrieval is bounded to tenant + global scope. | The retriever never reads another tenant's questions or textbooks (BR-7). |
| AI-11 | Prompt changes are versioned, not edited in place. | A prompt moves `candidate → active → retired`; papers generated under a retired version remain attributable to it. |
| AI-12 | Generation is deterministic with respect to its snapshot. | Re-running a stored blueprint snapshot must produce a paper satisfying every constraint of that snapshot; the question set may differ but the constraints may not be violated. |

### Decision flow

```
  Blueprint slot to fill
        |
        v
  Retrieve approved questions matching
  (chapter, type, marks, difficulty, source policy)
        |
        +-- enough candidates? --YES--> fill from retrieval --> [AI-1: no AI call]
        |
        NO
        |
        v
  Is AI allowed for this mode / org policy?  --NO--> report unmet slot [AI-3]
        |
        YES
        |
        v
  Generate with grounded context (active prompt version)
        |
        v
  Validate: structure -> answer -> scope -> difficulty     [AI-5]
        |
        +-- fail --> regenerate (bounded attempts) --> exhausted? --> [AI-3]
        |
        v
  Similarity check vs. in-scope bank
        |
        +-- > 90% --> reject, regenerate                    [AI-4]
        |
        v
  Attach provenance + prompt version                        [AI-8]
        |
        v
  Place in paper as draft                                   [AI-6]
        |
        v
  Paper-level check: difficulty balance
        |
        +-- unbalanced --> regenerate only offending slots   [AI-2]
        |
        v
  Paper-level check: Mixed Mode textbook floor >= 70%
        |
        +-- breached --> reduce AI share / flag paper        [BR-1, AI-9]
        |
        v
  Return draft paper + coverage report
```

### Quality thresholds

| Metric | Threshold | Action on breach |
|--------|-----------|------------------|
| Duplicate similarity | > 90% | Reject and regenerate (AI-4) |
| Near-duplicate warning | 80–90% | Accept but flag for reviewer attention |
| Difficulty deviation from target | Beyond stated tolerance | Regenerate offending slots only (AI-2) |
| Validation failures for one slot | Bounded retry count | Report slot as unmet (AI-3) |
| Textbook-derived share in Mixed Mode | < 70% | Reduce AI content or flag the paper (BR-1) |
| Out-of-scope question | Any | Reject (AI-5) |
| Executed answer ≠ stated answer | Any mismatch | Reject and regenerate (AI-5, ADR-010) |
| MCQ with zero or two correct options | Any | Reject and regenerate (AI-5, ADR-010) |

## Error Handling

Every failure has a defined user-visible outcome. The principle throughout: **retry what is transient, fall back where a degraded result is still useful, and always tell the user what happened and what they can do next.** No failure may leave the system in a partial or ambiguous state.

### Error catalogue

| ID | Failure | Automatic response | Fallback | User outcome |
|----|---------|--------------------|----------|--------------|
| **EH-1** | **OCR failed** | Retry the job (bounded attempts, backoff); retry only failed pages where possible | Mark `textbooks.status = failed`; partially extracted assets are retained and usable | **Notify Teacher** ("Upload Failed") with the reason and a retry action; the teacher can still generate from the existing bank |
| **EH-2** | **Generation timeout** | Retry once; on second timeout **cancel** the job | Blueprint is preserved so the teacher can retry or simplify without re-entering configuration | Teacher sees "generation timed out", the preserved blueprint, and a suggestion (fewer chapters/marks or a narrower mode) |
| **EH-3** | **LLM failed** (provider error, malformed output, refusal) | Retry with backoff; then **fall back to the fallback prompt** (simpler, more constrained variant of the active prompt) | If the fallback also fails, fill the slot by retrieval or report it as unmet (AI-3) | Paper is returned as a draft with the affected slots flagged; no fabricated content is inserted |
| EH-4 | Unsupported or corrupt file | None — rejected at validation | — | Immediate, specific message naming the supported types (CN-2) |
| EH-5 | File exceeds size or page limit | None — rejected at validation | — | Message naming the actual limit and the file's size (CN-1, CN-6) |
| EH-6 | Chapter mapping ambiguous | Assets flagged low-confidence | Manual mapping UI | Teacher is prompted to confirm mapping (FR-2.9) |
| EH-7 | Import file rows invalid | Valid rows imported, invalid rows skipped | — | Downloadable row-level error report (FR-3.12) |
| EH-8 | Blueprint does not reconcile | Generation not enqueued | — | Inline validation naming the exact mismatch (BR-3, CN-12) |
| EH-9 | Retrieval returns insufficient candidates | AI gap-fill attempted where allowed | Unmet slots reported | Coverage warning listing unmet slots (AI-3) |
| EH-10 | Duplicate detected in generated output | Reject and regenerate | Report slot as unmet after bounded attempts | Reviewer sees no duplicates in the final paper (AC-4) |
| EH-11 | Export/render failed | Retry the render job | — | "Export failed" with retry; the paper itself is unaffected |
| EH-12 | Template render error (broken layout, missing asset) | Render aborted | Fall back to the org default or platform base template, clearly labelled | User is told the chosen template failed and which template was used instead |
| EH-13 | Plan limit reached | Action blocked | — | Message naming the exhausted quota and the upgrade/reset path (BR-10) |
| EH-14 | Unauthorized action | Request denied | — | Generic authorization message; the attempt is audited (NFR-2) |
| EH-15 | Session expired | Redirect to login | Unsaved blueprint preserved as a draft where possible | User re-authenticates and returns to the same screen |
| EH-16 | Queue unavailable / worker outage | Jobs remain queued, not lost | — | Status shows "waiting"; Super Admin sees the degradation on the platform dashboard (FR-8.2) |
| EH-17 | Storage unavailable on upload | Upload retried | Upload rejected without creating an orphan record | Clear failure; no half-created textbook |
| EH-18 | Concurrent edit conflict on a paper | Last-writer detection | Conflict surfaced | User is shown the conflict and can reload or re-apply changes |

### Retry policy

- **Transient failures** (provider timeouts, network errors, storage blips) are retried with exponential backoff up to a bounded attempt count.
- **Deterministic failures** (unsupported file, invalid blueprint, unauthorized) are never retried — retrying cannot change the outcome.
- **Partial progress is preserved:** OCR retries resume from failed pages; generation retries reuse the stored blueprint; locked questions survive regeneration.
- **Every terminal failure produces a notification and an audit record** naming the failure class, the entity, and the actor.
- **No silent degradation:** if the system delivers less than requested, the shortfall is stated on the artifact the user sees.

### Failure escalation flows

```
OCR:        job -> fail -> retry(bounded) -> fail -> status=failed -> notify teacher -> retry action
                    '-> partial pages extracted are kept and usable

Generation: job -> timeout -> retry(1) -> timeout -> cancel -> blueprint preserved -> notify + guidance

LLM:        call -> error/invalid -> retry(backoff) -> fail -> fallback prompt -> fail
                                                                    '-> retrieval fill -> else unmet slot (AI-3)

Export:     job -> render fail -> retry -> fail -> notify "export failed"
                    '-> template error -> fall back to default template (labelled)
```

## Notifications

Notifications keep asynchronous work visible. Every notification is delivered in-app and, where the event is time-sensitive or the user is likely absent, by email. Each is role-targeted, deduplicated, and links directly to the relevant screen.

| ID | Notification | Trigger | Recipients | Channels | Links to |
|----|--------------|---------|------------|----------|----------|
| **NT-1** | **OCR Completed** | Textbook reaches `embedded` | Uploader; org admins optionally | In-app, email | OCR Progress / Question Bank |
| **NT-2** | **Upload Failed** | Textbook reaches `failed`, or upload rejected | Uploader | In-app, email | Upload Textbook (with retry) |
| **NT-3** | **Paper Ready** | Generation job completes | Requesting teacher | In-app, email | Paper Preview |
| **NT-4** | **Approval Pending** | Paper enters `submitted` | Coaching Admins / designated reviewers | In-app, email | Paper Preview (review mode) |
| **NT-5** | **Paper Approved** | Paper enters `approved` | Paper author | In-app, email | Paper Preview (export) |
| **NT-6** | **Paper Rejected** | Paper enters `rejected` | Paper author | In-app, email | Paper Preview (edit, with comments) |
| **NT-7** | **Subscription Expiring** | Plan nears expiry, or usage crosses a warning threshold | Coaching Admins; Super Admin for the tenant | In-app, email | Subscriptions |
| NT-8 | Generation Failed | Generation job cancelled after retries (EH-2, EH-3) | Requesting teacher | In-app, email | Paper Builder (blueprint preserved) |
| NT-9 | Export Ready | Export artifact stored | Requesting user | In-app | Download link |
| NT-10 | Export Failed | Render job failed after retries (EH-11) | Requesting user | In-app | Paper Preview (retry) |
| NT-11 | Import Completed | Import job finishes | Importing user | In-app | Question Bank (with error report) |
| NT-12 | Questions Awaiting Review | New `draft` questions exceed a threshold | Coaching Admins | In-app | Question Bank (draft filter) |
| NT-13 | Invitation Received | User invited to an organization | Invited user | Email | Login / accept invite |
| NT-14 | Role Changed | Membership role changed | Affected user | In-app, email | Dashboard |
| NT-15 | Plan Limit Reached | A quota is exhausted (EH-13) | Coaching Admins | In-app, email | Subscriptions |

**Notification rules**

- Notifications are generated from domain events, so they never disagree with entity state.
- Bulk events are aggregated (one "12 questions awaiting review" rather than twelve notifications).
- Duplicate notifications for the same event and recipient are suppressed.
- Every notification respects tenant scope: a user is never notified about another organization's activity.
- Users can mark notifications read; unread counts appear on the Dashboard.
- Email delivery failures are retried and logged but never block the in-app notification.

## Screen Inventory

The screen inventory is the bridge between this PRD and the frontend architecture. Every screen below is a routable surface with a defined role visibility, primary purpose, and requirement trace. `08_Frontend_Architecture.md` should implement exactly this set for v1.

### Navigation map

```
                          Login
                            |
                   (org context selected)
                            |
                            v
                        Dashboard
                            |
   .------------.-----------+-----------.------------.-------------.
   |            |           |           |            |             |
   v            v           v           v            v             v
Question    Upload      Paper       Templates    Analytics     Settings
 Bank      Textbook     Builder                                   |
   |            |           |            |                        |
   v            v           v            |            .-----------+-----------.
Question      OCR       Paper           '----------->|  Users  Programs      |
Details     Progress    Preview                      |  Subscriptions        |
                                                     '-----------------------'
```

### Screen catalogue

| # | Screen | Route (indicative) | Visible to | Purpose | Key actions | Traces to |
|---|--------|--------------------|-----------|---------|-------------|-----------|
| S-1 | **Login** | `/login` | Public | Authenticate and select active organization | Sign in, reset password, accept invite, switch org | FR-1.1–FR-1.4, NT-13 |
| S-2 | **Dashboard** | `/` | All roles (role-aware content) | Landing surface: what needs attention now | Recent papers, pending approvals, job status, usage vs. plan, unread notifications | FR-8.1–FR-8.2, FR-8.6 |
| S-3 | **Question Bank** | `/questions` | All roles | Browse, search, filter, and curate questions | Faceted search, bulk approve/reject/archive, import, export, open details | FR-3.6–FR-3.12, FR-3.2 |
| S-4 | **Question Details** | `/questions/:id` | All roles | Inspect and edit one question with full provenance | Edit (forks version), view lineage, view source asset, attach assets, approve/reject | FR-3.9, FR-3.11, BR-4, BR-8 |
| S-5 | **Upload Textbook** | `/textbooks/new` | Teacher, Coaching Admin | Submit a textbook for ingestion | Drag-and-drop, tag board/class/subject, validate, submit | FR-2.1–FR-2.5, CN-1–CN-2 |
| S-6 | **OCR Progress** | `/textbooks/:id` | Teacher, Coaching Admin | Track ingestion, review extracted assets, fix mapping | Live stage progress, per-page status, retry failed pages, confirm chapter mapping, promote assets to questions | FR-2.6–FR-2.11, FR-3.4, EH-1, EH-6 |
| S-7 | **Paper Builder** | `/papers/new` | Teacher, Coaching Admin | Configure the blueprint and launch generation | Select board/class/subject/chapters, choose pattern, set marks/difficulty/sources, pick generation mode, validate, generate, save draft, save as pattern | FR-4.1–FR-4.10, FR-4.13, FR-4.20, GM-1–GM-7 |
| S-8 | **Paper Preview** | `/papers/:id` | All roles (review actions role-gated) | Review, edit, approve, and export a paper | Preview with source badges, swap/lock/regenerate/reorder, add question, coverage report, submit, approve/reject with comments, export PDF/DOCX, version history | FR-4.11–FR-4.19, FR-5.1–FR-5.10, FR-7.1–FR-7.10 |
| S-9 | **Templates** | `/templates` | Coaching Admin (Teacher read/use) | Manage layout templates and blueprint patterns | Create, clone, preview, publish, archive, set default | FR-6.1–FR-6.7, PP-1, PP-8 |
| S-10 | **Analytics** | `/analytics` | Coaching Admin, Super Admin | Understand usage, quality, and coverage | Filter by date/program/subject/teacher, view AI vs. retrieved mix, coverage gaps, export CSV | FR-8.1–FR-8.9 |
| S-11 | **Settings** | `/settings` | Coaching Admin, Super Admin | Configure organization and platform behaviour | Org profile and branding, generation policy (allowed modes, AI ceiling), approval rules, notification preferences; Super Admin: organizations, prompt versions | FR-4.8, FR-7.6, BR-1, BR-6, AI-9, AI-11 |
| S-12 | **Users** | `/settings/users` | Coaching Admin, Super Admin | Manage members and access | Invite, assign role, scope to programs/subjects, revoke, view audit of access changes | FR-1.5–FR-1.9 |
| S-13 | **Programs** | `/settings/programs` | Coaching Admin, Super Admin | Define curriculum structure in use | Create program, map board/class/subject/chapters, pin curriculum version; Super Admin: publish and supersede global curriculum | FR-3.1, BR-9 |
| S-14 | **Subscriptions** | `/settings/subscriptions` | Coaching Admin (own), Super Admin (all) | Manage plan, seats, and quotas | View usage vs. limits, upgrade/downgrade, invoices; Super Admin: define plans, assign to tenants | FR-8.4–FR-8.6, BR-10, NT-7, NT-15 |

### Supporting surfaces

These are not top-level screens but are required for the journeys above.

| # | Surface | Type | Purpose |
|---|---------|------|---------|
| S-15 | Notifications panel | Drawer / dropdown | Unread list, deep links (NT-1–NT-15) |
| S-16 | Import Questions | Modal / wizard within S-3 | Column mapping, validation preview, error report (FR-3.2, FR-3.12) |
| S-17 | Export dialog | Modal within S-8 | Template, format, metadata, answer-key toggle, variant count (FR-5.1–FR-5.8) |
| S-18 | Approval queue | Filtered view of S-8 list | Reviewer worklist sorted by age (FR-7.2) |
| S-19 | Version history / diff | Panel within S-8 | Compare paper versions and approval rounds (FR-7.9) |
| S-20 | Empty and error states | Per screen | Cold-start guidance (no textbook, empty bank) and every EH-x outcome |

### Screen coverage by persona

| Screen | Teacher | Coaching Admin | Super Admin |
|--------|---------|----------------|-------------|
| Login | ✅ | ✅ | ✅ |
| Dashboard | ✅ | ✅ | ✅ (platform view) |
| Question Bank | ✅ | ✅ | ✅ (global scope) |
| Question Details | ✅ | ✅ | ✅ |
| Upload Textbook | ✅ | ✅ | — |
| OCR Progress | ✅ | ✅ | ✅ (diagnostics) |
| Paper Builder | ✅ | ✅ | — |
| Paper Preview | ✅ | ✅ (review) | ✅ (read-only) |
| Templates | use only | ✅ | ✅ (base templates) |
| Analytics | — | ✅ | ✅ (cross-tenant) |
| Settings | own profile | ✅ | ✅ (platform) |
| Users | — | ✅ | ✅ |
| Programs | read | ✅ | ✅ (global curriculum) |
| Subscriptions | — | ✅ (own) | ✅ (all) |

**Cross-cutting UI requirements**

- Every screen is tenant-aware and role-aware: no element renders that the current role cannot act on.
- Screens showing asynchronous work (S-6, S-7, S-8) update in real time without a manual refresh.
- Every screen defines its empty state, loading state, and error state before it is considered complete.
- Every destructive action (archive, revoke, withdraw approval, delete textbook) requires confirmation and is audited.

## Future Roadmap

The roadmap is stated as capability releases rather than dates. Each version assumes the previous one is stable in production. The v1 scope is exactly what this PRD specifies; later versions are directional and will get their own requirements.

### V1 — Question Papers *(this PRD)*

The complete paper-generation product: multi-tenant onboarding, textbook ingestion and OCR extraction, question bank with import and curation, retrieval-first generation across all seven generation modes, all thirteen question types, paper patterns, editing and regeneration, approval workflow, PDF/DOCX export with branding, analytics, and subscription metering.

- **Goal:** A teacher can go from a prescribed textbook to an approved, exported paper in one sitting.
- **Success:** Papers generated in minutes, curriculum-aligned, source-auditable, tenant-isolated.

### V2 — Worksheet Generation

Extends the generation engine from formal examination papers to day-to-day classroom material.

- Worksheet, homework, and practice-sheet formats with lighter patterns and answer spaces.
- Chapter-wise and topic-wise revision sheets; daily practice problems.
- Difficulty-laddered sheets (progressive practice) and remedial sheets for weak topics.
- Bulk generation: one worksheet per chapter across a whole subject in a single action.
- Printable student and teacher copies from the same source.
- **Reuses:** the entire retrieval and question-type stack; adds new patterns and export layouts.

### V3 — Student Analytics

Closes the loop by bringing results back into the platform.

- Student and class rosters; mark entry or scanned-answer capture.
- Per-question, per-chapter, and per-outcome performance reporting.
- Class and cohort comparison; identification of weak topics and misconceptions.
- Question quality feedback: discrimination and difficulty measured from real attempts, fed back to calibrate stored difficulty.
- Teacher-facing insight: "your class is weakest on these three chapters."
- **Depends on:** V1 approved papers as the unit of assessment.

### V4 — Adaptive Testing

Uses V3's performance data to individualise assessment.

- Papers generated against a student's or cohort's demonstrated weaknesses rather than a fixed blueprint.
- Adaptive item selection: subsequent questions chosen from prior responses.
- Personalised revision packs and mastery tracking per outcome.
- Spaced-repetition scheduling for retained weak topics.
- **Depends on:** V3 performance data and calibrated question difficulty.

### V5 — AI Tutor

Turns the content and performance graph into an interactive learning companion.

- Conversational explanation of any question, grounded in the student's own textbook content.
- Step-by-step solution walkthroughs with hints before answers.
- Doubt resolution scoped to the prescribed curriculum.
- Practice-on-demand driven by the adaptive engine.
- Teacher visibility into what students are struggling with, aggregated and privacy-respecting.
- **Depends on:** V1 grounded content, V3 analytics, V4 adaptive selection.

### Roadmap dependency chain

```
V1 Question Papers
      |  (grounded content + approved papers)
      v
V2 Worksheet Generation
      |  (more assessment surface area)
      v
V3 Student Analytics
      |  (performance data + calibrated difficulty)
      v
V4 Adaptive Testing
      |  (personalised selection)
      v
V5 AI Tutor
```

**Explicitly out of scope for the whole roadmap horizon:** full school ERP (fees, attendance, HR), deep third-party LMS integration beyond export, and proctoring.

## Appendix A — Traceability Summary

| Feature | Requirements | Stories | Acceptance | Screens |
|---------|--------------|---------|------------|---------|
| Login and Access | FR-1.1–FR-1.10 | US-1.1–US-1.8 | AC-1 | S-1, S-12 |
| Upload Textbook | FR-2.1–FR-2.12 | US-2.1–US-2.10 | AC-2 | S-5, S-6 |
| Import Questions | FR-3.1–FR-3.12 | US-3.1–US-3.11 | AC-3 | S-3, S-4, S-16 |
| Generate Paper | FR-4.1–FR-4.20 | US-4.1–US-4.21 | AC-4 | S-7, S-8 |
| Export PDF | FR-5.1–FR-5.10 | US-5.1–US-5.8 | AC-5 | S-8, S-17 |
| Save Template | FR-6.1–FR-6.7 | US-6.1–US-6.7 | AC-6 | S-9 |
| Approval | FR-7.1–FR-7.10 | US-7.1–US-7.10 | AC-7 | S-8, S-18, S-19 |
| Analytics | FR-8.1–FR-8.9 | US-8.1–US-8.7 | AC-8 | S-2, S-10, S-14 |

## Appendix B — Identifier Conventions

| Prefix | Meaning |
|--------|---------|
| `FR-n.m` | Functional requirement / sub-requirement |
| `NFR-n` | Non-functional requirement |
| `BR-n` | Business rule |
| `UJ-n` | User journey |
| `US-n.m` | User story |
| `AC-n` | Acceptance criteria group |
| `CN-n` | Constraint |
| `GM-n` | Generation mode |
| `QT-n` | Question type |
| `PP-n` | Paper pattern rule |
| `AI-n` | AI rule |
| `EH-n` | Error handling case |
| `NT-n` | Notification |
| `S-n` | Screen |
| `ADR-n` | Architecture decision record (`docs/ADR/`) |

IDs are stable once published. Superseded items are marked deprecated rather than renumbered, so downstream design, test, and implementation documents never break.

**This document is the authority for these vocabularies.** Generation modes (GM-1 … GM-7), question
types (QT-1 … QT-13), business rules (BR-1 … BR-11), AI rules (AI-1 … AI-12), constraints
(CN-1 … CN-22), errors (EH-1 … EH-18), notifications (NT-1 … NT-15), and screens (S-1 … S-20) are
defined here and only here. Other documents reference them by ID and describe how they are implemented;
they never restate the list, because a restated list is a list that drifts.

## Appendix C — Decisions Affecting This Document

| ADR | Bearing |
|---|---|
| [ADR-005](ADR/ADR-005-retrieval-first-ai-with-validation-gates.md) | BR-2 retrieval-first, AI-1 … AI-12 |
| [ADR-007](ADR/ADR-007-defer-pgvector-retrieval.md) | FR-2.10, FR-3.6, NFR-1 — semantic search is deferred, not assumed |
| [ADR-009](ADR/ADR-009-constraint-solver-assembler.md) | FR-4.9, FR-4.19, AI-3, BR-1, BR-3 — the feasibility gate and compromise report |
| [ADR-010](ADR/ADR-010-execute-and-verify-ai-correctness.md) | AI-5 answer verification |
| [ADR-011](ADR/ADR-011-three-roles-extendable-enum.md) | Personas, FR-1.5 … FR-1.8, FR-7 approval authority |
