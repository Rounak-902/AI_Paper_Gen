# ADR-012: Schema drift reconciliation — `full_name`, `status`, `branch_id`

- **Status:** Accepted
- **Date:** 2026-08-13
- **Affects:** `03_Database_Design.md`, `04_ER_Diagram.drawio`, Blueprint §8

## Context

Four discrepancies existed between `03_Database_Design.md` and the partially-implemented schema:

| # | Field | Documented | Implemented |
|---|---|---|---|
| 1 | `users.name` vs `users.fullName` | `name` | `fullName` |
| 2 | `users.status` | present | **missing** |
| 3 | `memberships.status` | **absent** | present |
| 4 | `memberships.branch_id` | present | **missing** |

Drift like this is corrosive because both sides look authoritative. Someone reads the doc and writes
`user.name`; someone else reads the model and writes `user.fullName`; the mismatch surfaces at runtime.

The governing principle: **changing a document is cheaper than migrating data**, so the implementation
wins on naming disagreements. But that principle is about *renames*, not about *omissions* — a field the
architecture genuinely requires is an additive migration, which is nearly free and carries no data-loss
risk. The two cases must be judged separately rather than applying one rule to all four.

## Decision

### 1. `users.full_name` — the document changes

The column is **`full_name`** (ORM attribute `fullName`). The implementation was already correct;
`03_Database_Design.md` §5, §7.1, the Blueprint's DDL, and the `.drawio` ER diagram are corrected.
Renaming a populated column plus every reference to it, to satisfy a document, is cost with no benefit.

### 2. `users.status` — the document wins; add the column

**Retained in the schema.** This is not a stylistic preference: authentication depends on it. FR-1.6
requires disabling a user account, and AC-1 requires that revoked access take effect on the next
request — both of which need a way to disable an identity **without deleting it**, since the user is
referenced by authored questions, papers, and approvals. Values: `active` / `disabled` / `suspended`.

*Migration D-1:* add nullable → backfill `'active'` → set `NOT NULL` → add CHECK. Additive, reversible,
no data risk.

### 3. `memberships.status` — the implementation wins; document it

**Documented, not dropped.** The invitation flow (FR-1.5, "pending invites expire") requires a
membership row that exists but grants no access — otherwise an invitation must live in a separate table
or be inferred from a null. Values: `active` / `invited` / `suspended`.

*Migration D-3:* the column already exists; add the CHECK constraint and index to match the spec.

### 4. `memberships.branch_id` — the document wins; add the column

**Retained.** Branch scoping is part of the architecture, not an aspiration: `programs.branch_id`
already exists, `branches` is a real table, and `07_Backend_Architecture.md` §8.2 describes branch
narrowing in the scope filter. Without it, a member cannot be scoped to one branch of a multi-branch
coaching chain. Null means org-wide access.

*Migration D-2:* add nullable FK to `branches.id` → add index → no backfill needed.

## Consequences

**Positive.** One authoritative definition per field across the document set, the ER diagram, and the
code. Three additive migrations, no destructive change, no rename of a populated column. The `role`
vocabulary is settled in the same pass (ADR-011), so the whole identity/tenancy surface is now
consistent.

**Negative / accepted.** Two columns must be added to a schema that is already partially deployed. Both
are nullable-then-backfilled, so both are safe under expand-migrate-contract and compatible with a
running previous version. Anyone holding a local branch that references `user.name` must rename —
a compile/attribute error, not a silent failure.

**Not yet done.** These migrations are specified in `03_Database_Design.md` §9.1 but **not written**.
The reconciliation pass changed documentation; the Alembic revisions are implementation work.

## Alternatives considered

- **Rename the column to `name` to match the document.** Rejected: a migration, an ORM change, and a
  sweep of every reference, purchasing nothing but agreement with a document that is easier to correct.
- **Drop `users.status` from the docs to match the implementation.** Rejected: it is load-bearing for
  FR-1.6 and AC-1. Omitting it would mean discovering during auth implementation that disabling a user
  requires deleting them.
- **Drop `memberships.branch_id` and defer branch scoping.** Tempting, since branches are optional at
  MVP. Rejected: `programs.branch_id` and the `branches` table already exist, so removing it would leave
  a half-built branch model, and adding it later means backfilling access decisions rather than
  defaulting them to null.
- **Treat `memberships.status` as redundant with `deleted_at`.** Rejected: they mean different things.
  `deleted_at` is "this record is gone"; `invited` is "this record exists and deliberately grants
  nothing yet." Overloading soft-delete to express an invitation would break every read path that
  filters `deleted_at IS NULL`.
