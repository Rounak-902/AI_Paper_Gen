# ADR-001: Use PostgreSQL as the transactional database

- **Status:** Accepted
- **Date:** 2026-08-13
- **Affects:** `03_Database_Design.md`, `02_System_Architecture.md`, `07_Backend_Architecture.md`, `09_Deployment.md`

## Context

The platform is a multi-tenant SaaS with strict isolation requirements (BR-7, CN-17), versioned
question lineage (BR-8), append-only audit and metering (BR-10, BR-11, CN-22), flexible per-paper
blueprint snapshots, and keyword search over a growing question bank. It also needs semantic retrieval
eventually, though not at MVP (ADR-007).

That combination normally invites several stores: a relational database, a search engine, a vector
database, and a document store for the JSON-shaped snapshots. Each additional stateful system carries
its own backup story, failover behaviour, consistency semantics, and on-call burden — a cost paid
continuously by a small team.

## Decision

**PostgreSQL is the single transactional source of truth**, carrying all four workloads:

- **Relational core** — organizations, users, memberships, curriculum, questions, papers, billing.
- **JSONB** — blueprint snapshots, layout, options, rubrics, `meta`, coverage reports. Schema-flexible
  where the shape is genuinely open, without giving up transactional guarantees.
- **Search** — GIN + generated `tsvector` columns for full text, `pg_trgm` for fuzzy matching and
  near-duplicate detection. No Elasticsearch at MVP.
- **Tenant isolation** — `FORCE ROW LEVEL SECURITY` on every tenant-owned table, with `org_id` set per
  request transaction via `SET LOCAL app.org_id`.

Version 16 or later. Extensions at MVP: `pgcrypto`, `citext`, `pg_trgm`. The application connects as a
non-superuser, non-owner `app_user` role, because a superuser silently bypasses RLS and would make the
entire isolation model decorative.

## Consequences

**Positive.** One stateful system to back up, restore, monitor, and reason about. Embeddings, when
adopted, stay ACID-consistent with the questions they describe rather than drifting in a separate
store. Hybrid queries combining a metadata filter, full-text rank, and (later) vector distance are one
query, not an application-side join across systems.

**Negative / accepted.** Postgres full-text search is cruder than a dedicated engine — no BM25 tuning,
weaker analyzers, no typo tolerance out of the box. This is acceptable because retrieval always runs
behind a hard metadata filter that reduces the candidate set to tens or low hundreds, where ranking
quality barely moves the outcome (ADR-007). Vertical scaling is the first lever; sharding is a Phase-10
decision with a named trigger.

**Known failure modes.** Connection exhaustion under pod fan-out — mitigated by PgBouncer, with the
`SET LOCAL` discipline that transaction pooling demands. Autovacuum falling behind on high-churn tables
(`question_bank`, `audit_logs`) — mitigated by per-table tuning and monitoring. Long-running analytics
queries competing with transactional load — mitigated by a read replica.

**Who runs it at scale.** PostgreSQL is among the most widely deployed databases in production, with
managed offerings from every major cloud, and RLS is a mature, well-understood feature rather than a
novelty. **Fallback if it breaks:** the schema is standard SQL with no vendor extensions on the critical
path, so it is portable to any managed Postgres.

## Alternatives considered

- **PostgreSQL + Elasticsearch + a vector DB from day one.** Best-in-class per workload, and correct at
  large scale. Rejected for MVP: three stateful systems to operate, keep in sync, and reindex, for a
  corpus small enough that the metadata filter dominates ranking. Adoption triggers are recorded in
  ADR-007 (semantic gap → pgvector) and in the Blueprint's deferral register (lexical gap → Meilisearch
  first, OpenSearch only at genuine scale).
- **MySQL.** Weaker JSONB, no native RLS, weaker full-text. Isolation would have to be enforced entirely
  in application code — one missed predicate away from a cross-tenant breach.
- **MongoDB.** Attractive for blueprint snapshots, but the domain is heavily relational (curriculum
  hierarchies, lineage, approvals) and the platform depends on multi-table transactions.
- **Supabase-hosted Postgres as the long-term platform.** Fine as managed Postgres; the decision here is
  the engine, not the vendor. Note that the audited codebase connects as the `postgres` superuser, which
  must be replaced by `app_user` before RLS means anything.
