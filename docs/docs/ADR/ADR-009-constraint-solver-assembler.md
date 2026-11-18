# ADR-009: The assembler is a constraint solver with a feasibility gate

- **Status:** Accepted and implemented with OR-Tools CP-SAT (P4-T2, 2026-09-19) — resolves conflict #2
- **Date:** 2026-08-13
- **Affects:** `06_AI_Architecture.md`, `02_System_Architecture.md`, `07_Backend_Architecture.md`, `01_Product_Requirements.md`

## Context

Doc 06 §14.2 specified a greedy assembler: fill required slots (exact marks and type) first, fill
flexible slots second, report whatever could not be filled. The Blueprint called this the product's
central mistake and specified a real constraint solver with a feasibility gate and a compromise report,
describing it as "the core IP."

The technical substance of the disagreement: a paper must satisfy pattern, per-section marks,
difficulty mix, Bloom mix, source mix, chapter/outcome coverage, and no-in-paper-duplicates **jointly**,
drawn from finite pools. A greedy pass that satisfies constraints in priority order can consume, in an
early slot, the only candidate a later slot required — and then report a gap that a different
assignment would not have produced.

The failure is worse than a gap, because it is **invisible and misattributed**. It looks like "the bank
is too thin," so the response is to add questions rather than to fix the assignment. And it does not
appear in a demo against a rich seeded bank; it appears against the thin, lopsided corpus of a newly
onboarded coaching centre — the exact population the product must serve on day one.

## Decision

**Build a real constraint solver, with a feasibility gate that runs before enqueueing and a compromise
report that names what was relaxed.**

### Feasibility gate

Runs **before the generation job is enqueued**, executing the same solver with a short time budget
(`AI_SOLVER_TIME_BUDGET_MS`). A per-source marginal count check is necessary but **not sufficient** —
it can pass and still fail mid-assembly, which is the whole point.

| Outcome | Meaning | What the teacher sees |
|---|---|---|
| **feasible** | A valid assignment exists | Generation proceeds |
| **feasible with compromise** | A valid assignment exists only if one constraint relaxes | The exact constraint, the exact amount, and an accept/adjust choice |
| **infeasible** | No assignment exists | Concrete options: reduce a source share, broaden chapters, or reallocate to AI |

### Relaxation order — documented and fixed

So that a compromise is predictable and reviewable rather than emergent:

1. **Never relaxed** — total marks (BR-3), section structure (PP-2), allowed types per section (PP-3),
   no duplicates within the paper, curriculum scope.
2. **Relaxed first** — difficulty mix within its stated tolerance.
3. **Relaxed second** — per-source share.
4. **Relaxed third** — Bloom mix.
5. **Relaxed fourth** — chapter/outcome coverage spread.
6. **Relaxed last** — the Mixed-Mode 70% textbook floor (BR-1), a product guarantee.

P4-T4 makes the slash above operational: learning-outcome coverage and chapter coverage
share one curriculum-coverage optimization tier. Neither taxonomy is lexicographically
preferred over the other. They remain separate facts/compromises in output, and this
decision preserves the five-tier order rather than silently inserting a sixth priority.

P0-T1 selected OR-Tools CP-SAT. P4-T2 implements these five soft tiers as exact lexicographic stages
from most protected to least protected (floor → chapter → Bloom → source → difficulty). The spike's
static coefficients remain decision evidence, not the production ordering mechanism: fixed numeric
weights can be overtaken by aggregate lower-tier slack as the bank grows.

### Compromise report

Machine-readable, naming which constraint relaxed, by how much, and why. It is the same structure the
coverage report (FR-4.19) renders. A paper that silently under-delivers is a correctness bug; a paper
that states exactly what it could not do is a usable result.

### Testing obligation

The solver lives in `packages/domain` — pure, I/O-free — so **property-based tests over randomized
question pools** run in milliseconds without a database. The invariant they assert: *the solver always
returns either a valid paper or an honest "infeasible + why"* — never a paper violating a non-relaxable
constraint, and never a silent compromise.

## Consequences

**Positive.** Infeasibility surfaces **before** the job runs, not after a teacher has waited. The
product's quality claim becomes defensible: a paper either satisfies the blueprint or explains itself.
Thin corpora degrade transparently instead of silently. The solver is the highest-value unit-test
target in the codebase and is testable in isolation.

**Negative / accepted.** This is the single largest engineering item in the plan — roughly a 3–4 week
delta over greedy fill, and the piece most likely to be underestimated. Solver runtime must be bounded;
a time budget with a "best assignment found so far" fallback prevents a pathological blueprint from
hanging a request. The relaxation order is a product decision as much as a technical one and needs
explicit sign-off, because it encodes what the platform is willing to compromise on.

**De-risking.** Spike S-2 in Phase 0 prototypes the solver against synthetic thin pools and produces the
documented relaxation order as its exit artifact. Discovering solver limitations in Phase 0 is cheap;
discovering them in Phase 5 is not.

## Alternatives considered

- **Greedy fill: required → flexible → report gaps** (doc 06's original). Simple, fast, and adequate
  against a rich bank. Rejected: it fails exactly where the product must not — thin corpora — and it
  fails *silently*, misattributing an assignment problem to a content problem.
- **Greedy fill plus backtracking on failure.** A middle path that handles many cases. Rejected because
  it still cannot answer "is this blueprint satisfiable?" *before* running, which is what the
  feasibility gate exists to provide, and its worst-case behaviour is harder to bound than a solver's.
- **Full ILP/CP-SAT solver from day one** (OR-Tools). The Phase-0 spike selected this option and P4-T2
  promoted it to production. The hand-written search remains rejected by the measured spike result.
- **Ask the LLM to assemble the paper.** Rejected outright. Constraint satisfaction over finite pools is
  a solved algorithmic problem with checkable output; delegating it to a model makes the one part of the
  system that can be *proven* correct into the part that cannot.
