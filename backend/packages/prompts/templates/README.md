# Deterministic question templates (P3-T3.5)

`catalog/v1.json` is the reviewed, immutable catalog. Each entry has a stable
`<template_id>/v<version>` reference, typed integer/choice placeholders, optional
named constraints, a pure parameter-generator function, and a registered
deterministic answer function. The engine accepts an injected seed or exact typed
parameters, so generation is reproducible and directly testable.

The first catalog contains three materially different templates for each supported
type: `numerical`, numeric `fill_blank`, numeric `one_word`, quantitative `mcq`,
and formula-based `true_false`. One-word and fill-blank templates are intentionally
numeric-only, matching the P0-T2 finding that vocabulary answers cannot be verified
by computation.

## Validation boundary

The P3-T4 multi-stage AI Validation Engine does not exist yet. P3-T3.5 therefore
does not duplicate it. `app.modules.questions.template_service` converts generated
output to the real P2-T3 `QuestionCreateIn` schema (including per-qtype structure)
and passes that model to the real `questions.service.create_question`, which applies
authorization, content hashing, exact/near deduplication, auditing, and insertion.
P3-T4 will later add request-match, language/grammar, curriculum-alignment, executed
answer verification, and AI-response parsing gates. Parsing and model-claim checks
do not apply to this provider-free path; executed verification can still be added as
defense in depth even though answers here are computed by construction.

LLM prompt artifacts remain under `../artifacts/<task>/v<version>.json`; this package
does not import or call the Gemini adapter.

## Document interpretation

Doc 06 section 9.1 names placeholder types and constraints but does not define a
serialized schema, generator registry contract, random-source injection, rounding,
or derived-field representation. Version 1 resolves those gaps conservatively:

1. only bounded integers and finite discrete choices are admitted;
2. constraints use a closed named vocabulary (`not_equal`, `greater_than`,
   `divisible`) rather than evaluated expressions;
3. randomness is local and seedable, and exact parameter overrides are revalidated;
4. answer functions are a closed code registry, never code embedded in JSON;
5. every numeric result in v1 is exact, so no undocumented rounding policy is needed.
