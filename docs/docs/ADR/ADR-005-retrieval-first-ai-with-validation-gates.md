# ADR-005: Use retrieval-first AI workflows with validation gates

- **Status:** Accepted
- **Date:** 2026-08-13
- **Affects:** `06_AI_Architecture.md`, `01_Product_Requirements.md`, `07_Backend_Architecture.md`

## Context

The naive design for an "AI question paper generator" is to hand the blueprint to a model and render
what comes back. It demos beautifully and fails as a product for three reasons that compound:

1. **Trust.** Teachers stake their professional credibility on the paper. One wrong answer in an exam
   hall is remembered longer than fifty correct ones.
2. **Cost.** Generating every question on every paper makes AI a per-paper variable cost that scales
   linearly with usage, with no asset accumulating in return.
3. **Grounding.** Curriculum alignment is the product. A model asked to invent questions "for CBSE
   Class 10 Science" will produce plausible questions that are subtly out of syllabus.

Meanwhile, most institutes already hold the material — textbooks, previous-year papers, their own bank
— in unstructured form.

## Decision

**Retrieval is the default path; template-based generation fills quantitative gaps; deterministic rules
handle classification; AI is a last-resort gap-filler; and nothing any source produces enters a paper
without passing an ordered validation gate.**

**Software-first hierarchy.** The governing principle — adopted from `10_Project_Summary.md` §3 and
propagated into `06_AI_Architecture.md` §9/§12/§18 — is:

1. **Generate** — retrieve from the bank first; then instantiate parameterized question templates and
   compute answers deterministically (sympy, arithmetic, lookup). Templates are the primary path for
   question types whose structure is regular (numerical, fill_blank, quantitative MCQ, formula-based
   true/false).
2. **Classify** — deterministic rules (structural analysis, Bloom verb taxonomy, heuristic difficulty
   signals) run first; Gemini Flash is called only when the rules produce a confidence score below
   `CLASSIFICATION_FALLBACK_THRESHOLD`.
3. **Calculate** — execute-and-verify (sympy sandbox) is already software-first (ADR-010).
4. **Validate** — structural/schema validation is already software-first.

Gemini is invoked only when software genuinely cannot produce a result — for question types that are
inherently non-templatizable (long_answer, case_study, source_based), for classification dimensions
where heuristics conflict, or for genuinely novel content not in the bank. When the bank and templates
satisfy the whole blueprint, the LLM is not called at all and zero AI usage is metered.

**The cost cascade.** Formalised as an ordered per-request sequence inside the AI module, so no caller
can bypass it: exact cache → bank reuse → template generation → deterministic classifier → Flash tier →
Pro tier → escalation → degrade. The first rung that satisfies the request wins. Rungs 0–3 are free and
are the **expected path**, not early-exit optimizations; most requests should never reach rung 4.

**The gate stack.** Every generated item passes an ordered gate, cheapest and most decisive first, and
a failure short-circuits to repair or regeneration rather than continuing:

```
1. schema valid?              → fail: repair
2. structural checks?         (marks match section, no answer leak in stem, options distinct, language)
3. correctness verified?      → fail: regenerate      [hard gate — ADR-010]
4. difficulty & Bloom match?  → reconcile or re-slot
5. within scope?              (chapter / learning-outcome containment)
6. not a duplicate?           (hash · trigram · concept overlap — context-dependent)
7. qualitative advisory       (LLM-as-judge flag — NON-blocking)
8. human approval             (enters as draft; a teacher approves — AI-6)
```

Stages 1–3 are hard gates. Stages 4–6 reconcile or re-slot rather than reject. Stage 7 is advisory.
Stage 8 is the human in the loop, and it is not optional.

**Degradation is a first-class outcome.** If the budget is exhausted or the provider is down, the
system serves a retrieval-only paper with slots honestly marked "needs manual authoring" — never a
fabricated question, and never a hard failure.

## Consequences

**Positive.** AI cost tracks the *gap* between demand and corpus, not total volume — a declining
per-tenant curve as the bank fills. Retrieved questions are already teacher-approved, so quality
starts high. Grounding is structural: generation always receives real curriculum context and real
exemplars. Every question in every paper carries provenance (BR-4).

**Negative / accepted.** Retrieval-first is only as good as the corpus, so a new tenant with an empty
bank lands on rung 5 for most slots and generates 60–90% of early questions. This **cold-start reality**
must be budgeted as a curve, not a flat minority share, and it is the reason seeding the global corpus
and ingesting previous-year papers are onboarding work rather than nice-to-haves.

The gate stack adds latency per generated question and a real amount of code. Each stage must emit a
structured result so the eval harness can measure pass rates per stage and per prompt version —
otherwise the gate becomes a black box that nobody can tune.

## Alternatives considered

- **Generate everything, validate lightly.** Fastest to build, and the demo is indistinguishable.
  Rejected: unbounded cost, no accumulating asset, and it puts the platform's credibility on a model's
  arithmetic.
- **Retrieval only, no generation.** Maximally trustworthy, and genuinely correct for GM-1/GM-5/GM-6.
  Rejected as the global default because a thin bank then simply cannot produce a paper, which is the
  cold-start case where the product must still be useful.
- **Fine-tune a model on the curriculum instead of retrieving.** Moves grounding into weights, where it
  cannot be audited, updated per syllabus revision, or attributed per question. Retrieval keeps
  provenance inspectable, which BR-4 requires.
