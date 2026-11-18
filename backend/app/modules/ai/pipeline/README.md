# P3-T4 AI generation pipeline

The caller loads a P3-T1 `SlotRetrievalResult`, a `BlueprintSlot`, curriculum
evidence and vetted `template_refs`. `ContextBuilder.build` validates that these
inputs belong together and checks the reviewed P3-T3.5 template catalog. A template
is eligible only when its type, marks, difficulty, Bloom (if requested) and
language match. The mapping to chapters/topics/outcomes is caller-vetted; type
alone never establishes eligibility. Unknown references fail as caller errors.

`GenerationPipeline.run` receives that context, an unchanged P3-T2 `LlmProvider`,
and a caller-owned async similarity callback. For fallback, the caller also
provides `PipelinePrompts` resolved with P3-T3 `PromptResolver` against the current
`AI_MODEL_GENERATE` / `AI_MODEL_CLASSIFY` values. Use `generate_question/v1` and
`classify_question/v1` artifacts through the existing registry activation path.
No model IDs appear in pipeline code. The AI module imports no database/session
factory and opens no database connection.

## Stages and operation budgets

1. Context Builder assembles and checks caller-supplied retrieval and curriculum.
2. Model Selector chooses the eligible template or provider fallback.
3. Adapter invokes `generate` once per attempt on fallback only.
4. Response Parser applies typed question parsing and maps malformed, duplicate-key,
   nonfinite, truncated or safety-blocked output to real `LlmProviderError` (502/EH-3).
5. Validation Engine checks requested metadata, canonical structure, curriculum
   placement, cited evidence, rubric, and independently executes quantitative
   solution claims. Incorrect content is rejected, never repaired in place.
6. Similarity Checker delegates to the P2-T4 path through a callback.
7. Classifiers run deterministic difficulty/Bloom rules first. Only a confidence
   below `CLASSIFICATION_FALLBACK_THRESHOLD` uses one Flash `classify` call combining
   difficulty, Bloom, language, content alignment, grammar/format flags and qualitative advisory correctness.
   Content alignment/language are acceptance gates; grammar/format flags and
   classification disagreement/low confidence are teacher review flags. The
   model cannot establish alignment just by claiming matching curriculum IDs.
8. Retry Manager wraps each individual generate/parse or classify/parse operation;
   True/False generation also runs full validation inside that attempt.
   It never retries the whole pipeline or repeats a successful generation after a
   classifier failure. Only True/False `executable_solution_required` failures
   with null/omitted `solution_steps` or `solution_expression` permit a corrective
   retry. The original request is resent with one fixed user note requesting a
   complete replacement and executable numeric comparison; no fields are patched.
   Empty strings and all other validation and exact duplicate failures terminate this run;
   a caller may request a fresh candidate. Template failures are code/caller errors.

Template success: **generate=0, classify=0, embed=0**, including when passed a
Gemini adapter whose three methods are raising spies. Fallback success without
retries is **generate=1, classify=0..1, embed=0** depending on local-rule
confidence. No embeddings are requested.
Tests reuse P3-T3.5's `AsyncMock(side_effect=AssertionError(...))` tripwire pattern
with explicit expected side effects followed by a raising unexpected-call guard
on fallback. The live driver wraps real Gemini methods and raises over budget.

Two retries / three attempts per operation cover brief transient failures while
bounding spend and latency. Backoff is 0.5s, then 1s, respecting numeric Retry-After
up to 30s. Nonretryable errors or larger/nonfinite advice terminate immediately.
True/False corrective retries share this same three-attempt generation budget
with provider errors, use the same 0.5s/1s backoff, and preserve terminal rejection.
Every replacement is parsed and fully validated, including independent expression
execution and boolean answer matching, before similarity/classification proceeds.
Null `solution_units` alone is not a retry trigger; its existing validity policy
is unchanged. MCQ, fill-blank and other question types do not opt into recovery.
The existing adapter already disables hidden SDK retries. Successful result metadata
records provider model/version, metering, prompt identities, retry events and flags.

## P3-T6 cost cascade

`CostCascade` wraps this pipeline with exact cache → P3-T1 bank reuse → P3-T3.5
template → local rules → Flash → Pro → extended-reasoning escalation → degrade.
It requires injected cache, authorized bank resolver, and tenant token-budget gate
interfaces. `BudgetedProvider` makes an atomic worst-case reservation immediately
before every Flash/Pro attempt, including retries, and emits per-call
`ai_cost_usd`/`cascade_rung` telemetry. Provider or budget exhaustion returns a
typed retrieval-only/manual-authoring result rather than raising a request-level
500. Escalation starts after exactly two standard verification failures. The
explicit scheduled-audit entry point exists; scheduling infrastructure is deferred.

## Verification coverage

Reviewed templates are correct by construction for MCQ/true-false/numerical and
numeric-only fill-blank/one-word catalog entries. Provider numerical and
formula-based true-false require executable solutions; numeric MCQ checks the
computed key against all four options, including numeric-equivalent distractors.
Numeric options may include a unit suffix matching the derivation's declared
unit; such suffixes cannot bypass the execution gate. One-word and single fill-blank numeric answers are computed; vocabulary answers
and qualitative types receive advisory correctness review only. Nonnumeric MCQ
gets the strict unique-key/distinct-option structural gate plus advisory factual
review. No factual correctness guarantee is claimed from an LLM judge.

The SymPy subprocess interprets a closed arithmetic AST; it never evals or
sympifies model text. Limits are 512 input characters, 100 AST nodes, depth 20,
bounded literals/intermediate values and exponents. Allowed operations are numeric
arithmetic, selected SymPy functions and a single numeric comparison. Filesystem
and network audit events are denied after trusted imports. Before receiving input,
the worker installs a 256 MiB memory cap and 2s CPU cap (POSIX rlimits / Windows Job
Object). Trusted startup has a separate 15s deadline; model-expression execution
has a maximum 2s wall deadline. Timeout/cancellation kills and reaps the subprocess.
Unsupported formulas fail closed; symbolic algebra coverage is intentionally limited.
Unit checks compare the declared solution unit to the answer unit (normalizing
only superscript-square/cube notation, e.g. cm² = cm^2); they do not
perform dimensional inference from natural language.

## Persistence and dedup

`questions.pipeline_service.similarity_checker` accepts an existing scoped session
and calls the unchanged P2 question creation service inside a rollback-only
savepoint. This reuses content hashing, exact conflict, >=0.80 pg_trgm review
warnings, visibility, authorization and audit logic without copying any dedup SQL.
The session must have no pending ORM writes before the probe. No probe row/audit
is retained. `create_pipeline_question` repeats the same real check on final
insertion, closing the race after the probe. Transaction commit remains caller-owned.
Near similarity warns rather than rejects, preserving P2-T4's established behavior.
Probe insertion requires existing curriculum and prompt-version rows; callers must
flush their seed/registry writes first. It is an authorized transactional write
probe, not a read-only retrieval operation.

## Reproduce

Install the backend dependencies including SymPy. With existing local PostgreSQL
and ignored infra credentials available, run `scripts/verify-p3-t4.ps1`. It migrates
only `papergen_test`, runs focused and earlier-stage regression tests, then performs
live Flash generate/classify proofs for numerical, MCQ, true-false, numeric fill-blank
and short answer. All live seed writes are in a rolled-back transaction. Output is
saved to `scratch/p3-t4-verification/tests.log` and `live.log`.

The final retry proof uses a local real HTTP 503 fault server through the unchanged
Gemini adapter with SDK retries disabled. It is an induced HTTP outage, not evidence
of a live Gemini outage. Unit tests separately prove classifier-only retry behavior.
The verification driver requires Flash-tier configuration and reuses existing model
values unchanged. **Pro-tier remains unverified and blocks before P4.**

### Current closeout limitation (2026-09-13)

The focused and earlier-stage PostgreSQL/unit suite passed 130 tests. An earlier
five-type live Flash run passed with generate=5/classify=5/embed=0. A subsequent
final-code rerun accepted numerical content, then returned upstream HTTP 429:
`GenerateRequestsPerDayPerProjectPerModel-FreeTier`, limit 20, for the existing
Flash model. The final full live quality gate is therefore pending quota
availability; it must not be reported as passing on the finished code. Existing
Flash configuration was reused unchanged; no configuration commit was necessary.

Raw current artifacts are in `scratch/p3-t4-verification/`: `tests.log` (raising
branch spies and real dedup), `live.log` (latest actual Flash responses), `fault.log`
(three real local HTTP 503 responses, 0.5/1s backoff, actual 502/EH-3 envelope), and
`quota.log` (actual upstream quota body). The earlier five-type counter was printed
in the session transcript; `live.log` now contains the latest rerun, not that earlier
run. `verify-p3-t4.ps1 -SkipLive -SkipMigrations` reruns tests without spending
provider quota. The Python driver supports `--fault-only` and `--quota-probe` for
separate explicit diagnostics. A quota probe is one live generate operation and is
not a generation-quality proof.

The documentation follow-up records this separately as **project open item #9**;
it is not part of the Pro-tier deferral. See
[the exact stage/path boundary and selective resume plan](../../../../../docs/docs/P3-T4_Live_Gate_Status.md)
and [the project open-item entry](../../../../../docs/docs/CHANGELOG.md).
The final numerical instance completed the entire pipeline; only MCQ, true-false,
fill-blank and short-answer final-code coverage remains. No already-passing test,
tripwire or retry proof needs to be rerun. The next documented daily reset after
the failure is 2026-09-14 12:30 IST. A clean full gate uses 10 real Gemini calls;
with permitted retries it may use up to 30, so the 20/day ceiling is a verification
cadence constraint even though one clean run fits an unused allowance.
