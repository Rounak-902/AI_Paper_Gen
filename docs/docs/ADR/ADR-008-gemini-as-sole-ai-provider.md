# ADR-008: Gemini as the sole AI provider

- **Status:** Accepted — resolves conflict #6 (as a product override, not a choice between the documented options)
- **Date:** 2026-08-13
- **Affects:** `06_AI_Architecture.md`, `07_Backend_Architecture.md`, `09_Deployment.md`, `05_Textbook_Ingestion.md`, Blueprint

## Context

The documentation set named three different provider strategies. Doc 06 listed OpenAI, Anthropic,
Google, and DeepSeek adapters with `gpt-4o`, `claude-3.5-sonnet`, and `text-embedding-3-small` in
config tables and cost estimates. Doc 02 named the latest Claude model as default. The Blueprint
specified a Claude tiering (`claude-haiku-4-5` / `claude-sonnet-5` / `claude-opus-4-8`). Several of
those model IDs were already stale.

Beyond the inconsistency, the deeper problem was **hardcoded dated model strings scattered across
documents**. Any such string is wrong within months, and one embedded in application code turns a model
upgrade into a code change and a redeploy.

## Decision

**The Gemini API is the sole AI provider for every AI task**: generation, validation support,
classification (difficulty, Bloom), duplicate-detection support, vision-based OCR fallback, and
embeddings.

**Tiering mirrors the existing cheap/expensive split** — only the provider filling each tier changes:

| Tier | Tasks | Config key |
|---|---|---|
| **Pro tier** (stronger) | Question generation, rubric synthesis, hard reasoning, vision OCR fallback | `AI_MODEL_GENERATE` |
| **Flash tier** (cheap/fast) | Classification, tagging, extraction, repair, validation support | `AI_MODEL_CLASSIFY` |
| **Embedding model** | Embeddings — inert until ADR-007 fires | `AI_EMBEDDING_MODEL` |

**No dated model IDs anywhere.** Documents and code reference *tiers*; concrete model IDs live only in
configuration. This is the "model IDs are config, never hardcoded" principle already stated in the
Blueprint, applied consistently. A model upgrade is a config change plus an eval run.

**Embedding dimensionality — a real constraint, not a detail.** The Gemini embedding model emits
**3072** dimensions natively and supports Matryoshka truncation. pgvector's HNSW and IVFFlat indexes
accept at most **2000** dimensions for the `vector` type, so a raw 3072-dim vector **cannot be
ANN-indexed at all**. Therefore: the adapter truncates and re-normalises to **1536**, the column is
`vector(1536)`, and `AI_EMBEDDING_DIMENSIONS = 1536`. `halfvec(3072)` (HNSW limit 4000) is the
documented alternative if full fidelity is ever required. The width is recorded per row in
`question_embeddings.dimensions` so a change is detectable rather than silent.

**The adapter pattern stays.** `LlmProvider` remains the abstract contract; `GeminiAdapter` is the only
implementation. The abstraction exists to keep a future provider change localised — not to enable
per-call routing. A provider swap carries its own prompt versions and its own eval baseline, because
prompts tuned against one model family do not transfer without re-evaluation. Multi-vendor failover as
a *cost* strategy is explicitly rejected: frontier prices track each other, and switching mid-pipeline
changes the output distribution enough to invalidate every eval baseline.

**Safety is configured explicitly.** Gemini's four harm categories (harassment, hate speech, sexually
explicit, dangerous content) are set to block medium-and-above rather than left to provider defaults. A
safety block is surfaced as `safety_blocked` and treated as **non-retryable** — retrying an identical
prompt that tripped a filter only burns budget. Blocks are logged against the prompt version and the
chapter, because legitimate curriculum content in biology, history, and chemistry can trip naive
filters, and thresholds should be tuned against evidence.

## Consequences

**Positive.** One provider account, one key to rotate, one rate-limit budget, one set of eval baselines
to maintain. Long-context Pro-tier models suit grounded generation with textbook excerpts and
exemplars. Native vision capability gives the OCR pipeline a fallback without a fourth vendor
(ADR-014). Tier-based references mean this documentation does not go stale when models are renamed.

**Negative / accepted.** Single-provider concentration: an outage or a policy change is not absorbed by
automatic failover. Mitigated by the degrade rung — retrieval-only papers with honest gaps rather than
a hard failure — by kill switches per capability, and by the adapter making a deliberate swap a
localised change. Provider-specific behaviour (safety categories, structured-output semantics, finish
reasons) is normalised inside `GeminiAdapter` rather than leaking into business logic.

**Fallback if it breaks.** The `LlmProvider` contract is implemented once; a second adapter is a bounded
piece of work. What is *not* free is re-tuning prompts and re-establishing eval baselines — budget that
explicitly rather than assuming a swap is a config toggle.

## Alternatives considered

- **Anthropic Claude family** (the Blueprint's recommendation). A strong choice on reasoning quality
  and prompt-caching economics. Rejected by explicit product decision in favour of Gemini.
- **OpenAI GPT-4o family** (doc 06's original). Rejected on the same product decision; its model IDs
  were already stale in the docs.
- **Multi-provider with automatic per-call failover** (doc 09's original fallback chain
  `OpenAI → Anthropic → Gemini`). Rejected on substance, not just cost: it triples the prompt-version
  and eval-baseline maintenance burden, and silently changes output distribution mid-pipeline, which
  is precisely what makes a quality regression untraceable.
- **Self-hosted open-weight models** (Ollama/vLLM). Attractive for on-prem tenants with no-egress
  requirements and for the cheap classifier tier. Deferred, not rejected — the adapter interface leaves
  the door open, and rungs 0–3 of the cost cascade already avoid the LLM for most classification work.
