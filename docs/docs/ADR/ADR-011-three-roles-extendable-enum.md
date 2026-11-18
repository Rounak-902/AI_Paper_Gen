# ADR-011: Three RBAC roles, as an extendable enum

- **Status:** Accepted — resolves conflict #7
- **Date:** 2026-08-13
- **Affects:** `03_Database_Design.md`, `07_Backend_Architecture.md`, `08_Frontend_Architecture.md`, `01_Product_Requirements.md`, Blueprint, `04_ER_Diagram.drawio`

## Context

The Blueprint specified six roles — `super_admin`, `org_admin`, `hod`, `teacher`, `reviewer`, `viewer`
— in FR-2 and in the `memberships` CHECK constraint. Everything else specified three:
`01_Product_Requirements.md` defines three personas, `07_Backend_Architecture.md` §8.1 is a
three-column capability matrix, `08_Frontend_Architecture.md` mirrors three roles in its capability
model, route groups, and navigation tables, and the implemented Prisma enum has three values.

So this is five-against-one, and the one is the document furthest from the implementation. It is also
the shape that matters: `hod` and `reviewer` are attractive on paper, but neither has a workflow that
exists yet. `hod` presupposes a department dimension the schema does not have. `reviewer` presupposes
that approve-only-without-authoring is a distinct need — whereas the actual requirement, BR-6, is
separation of duties, which is a *behavioural* rule (author ≠ approver) that no separate role is needed
to express.

## Decision

**Ship exactly three roles, in a column designed so a fourth is cheap.**

**Implemented values on `memberships.role`:**

| Value | Persona | Scope |
|---|---|---|
| `teacher` | Teacher | One organization, optionally narrowed to assigned programs and subjects |
| `coaching_admin` | Coaching Admin | One organization; authority over its branches, users, and content |
| `super_admin` | Super Admin | Cross-tenant platform operator; every cross-tenant read audited (BR-7, BR-11) |

**Reserved — documented, deliberately not implemented:**

| Value | Intended meaning | Why deferred |
|---|---|---|
| `hod` | Departmental approver | Requires a department dimension that does not exist in the schema |
| `reviewer` | Approve-only, cannot author | BR-6 already enforces author ≠ approver behaviourally |
| `viewer` | Read-only observer | No recorded customer requirement |

**"Extendable enum" means something specific:** `role` is a **constrained text column with a CHECK
constraint**, not a native PostgreSQL `ENUM` type. Adding a value is a one-line CHECK migration.
Adding a value to a native ENUM requires `ALTER TYPE ... ADD VALUE`, which cannot be run inside a
transaction block in older versions, cannot be reversed cleanly, and cannot remove a value at all.

**Adding a reserved role later requires:** extend the CHECK constraint; extend the capability matrix in
`07_Backend_Architecture.md` §8.1; extend the client capability mirror in `08_Frontend_Architecture.md`
§7.2; add a route group if the role needs its own surfaces. **No table or column changes.**

**The frontend is already insulated.** It loads its capability matrix from `/me/capabilities` rather
than hard-coding it, and `<Can>` gates on capabilities rather than on role equality — so a new backend
role does not break the client. This is why no component may compare `role === 'coaching_admin'`
directly.

## Consequences

**Positive.** The implemented schema, all five other documents, and the code now agree. Three roles
means three columns in every permission matrix, three route groups, and three paths to test — a real
reduction in surface area. Nothing is lost: every capability the six-role model expressed is either
implemented or has a documented value reserved for it.

**Negative / accepted.** Organizations with a genuine head-of-department layer must use
`coaching_admin` for those users, granting broader authority than a strict `hod` would. If that becomes
a real objection, `hod` is a migration and a matrix row — but it also needs the department dimension,
and that is the larger piece of work.

`super_admin` is stored on `memberships.role` alongside the tenant roles rather than as a separate
platform-level flag. This matches the implementation and every other document. It does mean a
platform operator holds a membership row, and that cross-tenant access is a capability of that role
rather than a structurally separate mechanism — which is why BR-7 and BR-11 require every cross-tenant
read to be time-boxed and audited.

## Alternatives considered

- **Six roles as the Blueprint specified.** Rejected: it would require changing five documents and the
  implemented schema to add two roles with no workflow behind them and one (`org_admin`) that is a
  rename of `coaching_admin` for no benefit. Speculative permission axes are not free — every role
  multiplies the permission matrix, the test surface, and the number of "can this role do X?" questions
  that must be answered forever.
- **A native PostgreSQL `ENUM` type.** Slightly better storage and type safety. Rejected on
  migration ergonomics: values cannot be removed, and adding one is more operationally awkward than a
  CHECK constraint edit. The extendability requirement is the deciding factor.
- **Full permission-based RBAC** (roles as bundles of granular permissions, stored in a table).
  Genuinely more flexible and the right end state if enterprise customers demand custom roles.
  Rejected as premature: it adds a permissions table, a role-permissions join, an assignment UI, and a
  resolution cache to serve three fixed roles.
- **`super_admin` as a separate platform-level flag on `users`.** Arguably cleaner conceptually — a
  platform operator is not a tenant member. Rejected because it contradicts the implemented schema and
  all four other documents, for a modelling improvement that changes no behaviour.
