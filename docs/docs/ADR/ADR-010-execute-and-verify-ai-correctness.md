# ADR-010: Verify AI correctness by execution, not by LLM judgement

- **Status:** Accepted — resolves conflict #4
- **Date:** 2026-08-13
- **Affects:** `06_AI_Architecture.md`, `01_Product_Requirements.md`, `07_Backend_Architecture.md`, `09_Deployment.md`

## Context

Doc 06's validation pipeline listed answer correctness as stage 10 with the action **"Flag for
review"** — an advisory check, backed by an LLM-as-judge call. The Blueprint identified this as the
platform's #1 product risk and specified execution-based verification instead.

The argument against LLM-as-judge here is not that models are unreliable in general. It is that
**LLM-as-judge is circular for precisely the error class that destroys trust**. A model that made an
arithmetic slip generating a question is not independently positioned to catch that slip when asked to
grade it; the same reasoning process produces the same error. The failures that matter most — wrong
numeric answers, MCQs with two correct options or none — are exactly the ones self-judgement is worst
at detecting.

And the consequence is asymmetric. A teacher who hands out a paper with a wrong answer in an exam hall
does not file a bug report; they stop using the product and tell colleagues why. Structured output
guarantees **shape**, not **truth**.

## Decision

**Answer correctness is a hard gate, verified by computation. An LLM is never the sole gate on a
correctness claim.**

### Numerical and quantitative items

1. The model must emit a `solution_steps` field containing a machine-executable derivation alongside
   the human-readable working.
2. The verifier evaluates it with **sympy** inside a sandboxed runner.
3. The computed result must equal the stated answer within the declared tolerance, with units checked.
4. On mismatch the item is **rejected and regenerated** — never repaired in place, never flagged-and-kept.

**Sandbox constraints:** no network, no filesystem, a hard CPU-time cap
(`AI_VERIFY_SANDBOX_TIMEOUT_MS`), a memory cap, and a restricted expression namespace. Execution is a
verification step, not an extensibility point; nothing user-authored reaches it except the model's own
derivation.

### MCQ items

Assert **exactly one** option satisfies the key. Zero-correct and two-correct are both hard failures.
Distractors must be textually distinct from each other and from the key.

### Parametric generation is preferred where it applies

For quantitative slots the best path is not generation at all: take a known-correct retrieved item,
vary its parameters within declared constraints, and recompute the answer deterministically. The result
is **correct by construction**, and it is cheaper than generation — cost and quality point the same way.
Free-form generation is the fallback when no template exists.

### Qualitative items

Long and short answers cannot be executed. They receive an advisory LLM-as-judge confidence flag,
surfaced to the teacher, which **never blocks**. The human approval step (AI-6) is the real gate.

### CI enforcement

The answer-correctness check runs in CI against a fixture set of blueprints and gates the generation
pipeline. A regression in correctness pass rate fails the build.

## Consequences

**Positive.** The platform's most damaging failure mode is caught by a check that cannot be fooled by
plausible-sounding reasoning. Verified items can be presented to teachers with justified confidence.
Parametric generation yields unlimited correct quantitative variants at near-zero marginal cost.

**Negative / accepted.** Requiring machine-executable solution steps constrains prompt design and
increases output tokens. The sandbox is real infrastructure with a real security surface — hence no
network, no filesystem, hard resource caps, and no user-supplied code path into it. Verification adds
latency per generated quantitative question. Rejection-and-regeneration raises the per-slot cost when a
model is having a bad day, which is what the bounded retry count and the AI-3 unmet-slot path exist to
contain.

**Coverage is honest about its own limits.** Execution verifies quantitative items and structural MCQ
validity. It does *not* verify that a history question's stated answer is historically accurate. That
residual is handled by grounding (generation cites retrieved source material), by the advisory flag,
and by mandatory human approval — and it should be stated plainly rather than papered over.

**De-risking.** Spike S-3 in Phase 0 builds the sympy/sandbox harness and measures the pass rate on a
fixture set before the pipeline depends on it.

## Alternatives considered

- **LLM-as-judge as the gate** (doc 06's original). Cheap and general. Rejected: circular for the error
  class that matters most. Retained as an *advisory* signal for qualitative items, where nothing better
  exists.
- **A second, different model as judge.** Breaks the strict circularity and is genuinely better than
  self-judgement. Rejected as the primary gate because it is still probabilistic where a deterministic
  check is available, and it doubles the AI cost of every generated item to get a weaker guarantee than
  execution provides.
- **Human review only, no automated verification.** Maximally safe and completely unscalable — it makes
  the teacher the arithmetic checker, which is the work the product exists to remove.
- **Skip quantitative question types entirely at MVP.** Sidesteps the problem, and guts the value
  proposition for STEM, which is the target market.
