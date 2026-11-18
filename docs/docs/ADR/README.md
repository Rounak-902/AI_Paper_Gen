# Architecture Decision Records

This folder contains the architecture decision records for major product and platform choices.

Each ADR follows the same format — **Context**, **Decision**, **Consequences**, **Alternatives
considered**. These are decision records, not design documents: they capture *what* was decided and
*why the alternatives lost*, so that a choice is not silently re-litigated a year later by someone who
cannot see the reasoning.

**On length.** The target is roughly 400 words of body. Several of these run longer — ADR-006, 008,
009, 010, 011, and 013 in particular — for two deliberate reasons: each locked-in stack choice must
document its known failure modes, its production track record, and its fallback; and rejected
alternatives are preserved here in full rather than deleted from the documents they were removed from.
Both are requirements, and both cost words. Where the extra length is table content rather than prose,
it is information density rather than padding.

**ADRs are authoritative.** Where a document in `docs/` disagrees with an ADR, the ADR wins and the
document is a defect to be fixed.

## Index

### Foundational decisions

| ADR | Decision | Status |
|---|---|---|
| [ADR-001](ADR-001-postgresql-transactional-database.md) | Use PostgreSQL as the transactional database | Accepted |
| [ADR-002](ADR-002-modular-monolith.md) | Use a modular monolith instead of premature microservices | Accepted |
| [ADR-003](ADR-003-questions-as-core-domain-entity.md) | Keep questions as the core domain entity | Accepted |
| [ADR-004](ADR-004-soft-deletes-and-append-only-audit.md) | Use soft deletes and append-only audit records | Accepted |
| [ADR-005](ADR-005-retrieval-first-ai-with-validation-gates.md) | Use retrieval-first AI workflows with validation gates | Accepted |

### Resolutions of the seven documented conflicts

These resolve the contradictions recorded in `10_Project_Summary.md` between the Blueprint and docs
02/03/05/06/09.

| ADR | Conflict | Decision | Status |
|---|---|---|---|
| [ADR-007](ADR-007-defer-pgvector-retrieval.md) | #1 Vector search | Defer pgvector; MVP is metadata filter + FTS + trigram, adopted on a measured paraphrase-recall gap | Accepted |
| [ADR-009](ADR-009-constraint-solver-assembler.md) | #2 Assembler | Real constraint solver + feasibility gate + compromise report, not greedy fill | Accepted |
| [ADR-014](ADR-014-math-first-ocr.md) | #3 OCR engine | Math-first: Mathpix primary, Gemini vision fallback, plain OCR for prose | Accepted |
| [ADR-010](ADR-010-execute-and-verify-ai-correctness.md) | #4 AI correctness | Execute-and-verify as a hard gate; LLM-as-judge is advisory only | Accepted |
| [ADR-013](ADR-013-ecs-fargate-before-kubernetes.md) | #5 Deployment | ECS Fargate at MVP; Kubernetes manifests written, adopted on a named trigger | Accepted |
| [ADR-008](ADR-008-gemini-as-sole-ai-provider.md) | #6 Models | **Overridden** — Gemini API is the sole provider, tiered by task | Accepted |
| [ADR-011](ADR-011-three-roles-extendable-enum.md) | #7 RBAC | Three roles (`teacher`, `coaching_admin`, `super_admin`) as an extendable enum | Accepted |

### Overrides and reconciliation

| ADR | Decision | Status |
|---|---|---|
| [ADR-006](ADR-006-fastapi-over-nestjs.md) | FastAPI (Python) over NestJS — brings SQLAlchemy + Alembic and Celery with it | Accepted |
| [ADR-012](ADR-012-schema-drift-reconciliation.md) | Reconcile `full_name`, `users.status`, `memberships.status`, `memberships.branch_id` | Accepted |

## Which ADRs govern which documents

| Document | Governing ADRs |
|---|---|
| `01_Product_Requirements.md` | 005, 007, 009, 010, 011 |
| `02_System_Architecture.md` | 001–014 (summary table in "Architecture Decision Records") |
| `03_Database_Design.md` | 001, 003, 004, 006, 007, 008, 011, 012 |
| `05_Textbook_Ingestion.md` | 005, 006, 007, 014 |
| `06_AI_Architecture.md` | 005, 007, 008, 009, 010 |
| `07_Backend_Architecture.md` | 002, 004, 005, 006, 007, 008, 009, 010, 011 |
| `08_Frontend_Architecture.md` | 006 (backend surface), 007 (search capability), 011 (roles) |
| `09_Deployment.md` | 006, 008, 013 |
| `Blueprint_…md` | 006, 007, 008, 009, 010, 011, 012, 013, 014 |

## Writing a new ADR

1. Take the next free number. Numbers are never reused, even if an ADR is superseded.
2. Name the file `ADR-NNN-short-kebab-summary.md`.
3. Use the four standard headings. Keep it under roughly 400 words of body.
4. Record the alternatives **with the reason each lost**. An ADR with no rejected alternatives has not
   recorded a decision — it has recorded a preference.
5. Never edit a decision in place once it is Accepted. Write a new ADR that supersedes it, and mark the
   old one `Superseded by ADR-NNN`. The history is the point.
