# ADR-004: Use soft deletes and append-only audit records

- **Status:** Accepted
- **Date:** 2026-08-13
- **Affects:** `03_Database_Design.md`, `07_Backend_Architecture.md`, `01_Product_Requirements.md`

## Context

Two forces meet here. BR-11 requires that every create, update, delete, approval, and access-control
change be recorded in a trail that can settle a dispute months later. And the domain is full of records
that are referenced long after someone wants them "gone": a question used in an exported paper, a
template a past export was rendered with, a user who approved something and has since left.

A hard `DELETE` satisfies neither. It breaks foreign keys or silently cascades, and it destroys exactly
the history the audit requirement exists to preserve.

## Decision

**Soft deletes for business entities; append-only for audit and metering.**

- Every business entity carries `deleted_at timestamptz NULL`. Deletion sets the timestamp. Every read
  path filters `deleted_at IS NULL`, applied centrally in the repository base class rather than
  remembered per query.
- `audit_logs` and `usage` are **append-only**: no `UPDATE`, no `DELETE` in normal operation (CN-22).
  Both are time-partitioned; retention is managed by dropping or archiving whole partitions to cold
  storage, never by deleting rows.
- Audit records are written by middleware on every mutating handler, not by business services. A
  service that forgets to log cannot exist, because logging is not the service's job.
- `analytics_daily` is a rebuildable rollup, not a source of truth — it can be recomputed from the
  append-only tables at any time.
- Hard deletion exists only as an explicit maintenance operation (tenant offboarding, GDPR erasure) and
  is itself audited.

## Consequences

**Positive.** History survives. "Who approved this paper, and what did it look like then?" is
answerable. Accidental deletion is recoverable by clearing a timestamp rather than restoring a backup.
Referential integrity holds without cascade surprises. Compliance posture (GDPR, DPDP, SOC 2) has a
structural answer rather than a procedural one.

**Negative / accepted.** Every query must filter `deleted_at IS NULL`, and a forgotten filter leaks
deleted rows into a listing — which is why the filter lives in the repository and not in call sites.
Unique constraints interact awkwardly with soft deletion: a soft-deleted row still occupies its unique
key, so "delete and recreate with the same name" fails unless the constraint is partial
(`WHERE deleted_at IS NULL`). Tables grow monotonically, which is what partitioning is for.

**Right to erasure.** Soft deletion is not erasure. A GDPR/DPDP erasure request is a distinct,
audited hard-delete path that overwrites or removes personal data while preserving the non-personal
audit skeleton. Recording that distinction now avoids discovering later that "we never actually delete
anything" is a compliance problem rather than a feature.

## Alternatives considered

- **Hard deletes with audit-only history.** Simpler reads and smaller tables, but recovering a deleted
  entity means reconstructing it from log entries, and foreign keys either break or cascade destructively.
- **Event sourcing** (the entity is the fold of its events). Gives a perfect history and time-travel for
  free, and is genuinely tempting for question lineage. Rejected as disproportionate: it reshapes every
  read path and every developer's mental model to solve a problem that `deleted_at` plus an append-only
  log already solves. The pieces that pay — the audit trail, and the blueprint snapshot that makes a
  paper reproducible — are adopted without adopting the pattern wholesale.
- **Temporal tables / system-versioned rows.** Elegant, but PostgreSQL has no native support, and the
  extension route adds a dependency to solve a narrower problem than the audit log already covers.
