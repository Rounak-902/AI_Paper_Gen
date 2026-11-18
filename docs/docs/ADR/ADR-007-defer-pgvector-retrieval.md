# ADR-007: Defer pgvector; MVP retrieval is metadata filter + FTS + trigram

- **Status:** Accepted — resolves conflict #1
- **Date:** 2026-08-13
- **Affects:** `06_AI_Architecture.md`, `03_Database_Design.md`, `02_System_Architecture.md`, `05_Textbook_Ingestion.md`, `09_Deployment.md`

## Context

Docs 02, 03, 05, 06, and 09 placed pgvector and an HNSW index on the critical path, with an embedding
worker, a `question_embeddings` table, and semantic search as a core retrieval stage. The Blueprint
argued the opposite: defer vectors until measurement justifies them.

The deciding observation is about **cardinality after filtering**. Every retrieval is hard-filtered
first by board, class, subject, chapter, question type, marks, difficulty, status, and the sources the
generation mode permits. After that filter, the candidate set for one blueprint slot is typically tens
to low hundreds of short questions. Approximate nearest-neighbour search exists to make similarity
search tractable over millions of candidates; over a hundred it solves a problem that no longer exists,
while still costing an embedding call per question, an index to build and rebuild, a reindex path for
every model change, and a worker to operate.

## Decision

**Ship MVP retrieval without embeddings. Adopt pgvector when a measurement says to.**

**MVP pipeline:** metadata filter (hard) → `tsvector` full-text (GIN) → `pg_trgm` trigram similarity →
hybrid rank → dedup → coverage optimizer → top-K.

**The interface is the contract.** `RetrievalService.selectCandidates(slot)` returns *scored
candidates*. Whether a score came from lexical ranking or from ANN is invisible to every caller, so the
vector stage drops in behind it without a single caller change.

**What is written but not created:** the `question_embeddings` table is fully specified in
`03_Database_Design.md` §5, and its migration is step 7 of §9 — explicitly gated. The `vector`
extension is not enabled at MVP. The embedding Celery queue exists and is inert.

**Adoption trigger — one specific measurement.** The eval harness tracks **paraphrase recall**: given a
question rephrased with no shared surface terms, does retrieval still find it? When that metric shows a
gap that materially affects paper quality, pgvector is adopted. This is deliberately *not* "when the
bank gets big" — size alone does not justify it, because the metadata filter scales with the bank.

### P3-T7 measurement record (2026-09-17)

The numeric trigger is now frozen as a **paraphrase-recall gap >= 0.20** (equivalently, lexical
paraphrase recall < 0.80) on a reviewed, no-shared-terminology corpus. S-4 measured **0/20 lexical
hits: recall 0.00, gap 1.00**, using PostgreSQL `plainto_tsquery` plus `pg_trgm` at the 0.30 recognition
boundary after chapter/type/difficulty filtering. The threshold is therefore breached.

The 20 targets are unchanged P2 bank questions. Their query-side paraphrases are hand-authored because
P2-T4's available near-duplicate pair is only an `exactly`/`precisely` surface edit and cannot measure
the semantic gap. Full pair-level scores are recorded in
`../evidence/p3-t7/2026-09-17/s4-recall.json`.

This record **does not adopt pgvector**. No `vector` extension, column, embedding population, or P3-T1
logic change is part of P3-T7. It closes the measurement/threshold question and opens the separately
reviewable adoption follow-up required by the breached trigger.

**Note the sibling trigger.** A *lexical* recall shortfall is a different failure and points at a
dedicated search engine (Meilisearch first), not at embeddings. Confusing the two leads to adopting the
wrong system for the observed symptom.

**On adoption:** enable `vector`, run migration step 7, backfill embeddings in a background job, flip
`AI_VECTOR_SEARCH_ENABLED`, shift the hybrid weights, and re-run the eval suite to confirm the gap
closed. Vectors are stored at **1536 dimensions** — Matryoshka-truncated from the Gemini embedding
model's native 3072, because pgvector's HNSW/IVFFlat indexes cap at 2000 dimensions for the `vector`
type (ADR-008).

## Consequences

**Positive.** MVP removes an embedding worker, an API cost per question, an index to tune and rebuild,
and a reindex-on-model-change migration path. Ingestion is shorter and cheaper. There is no "which
embedding model is this row?" problem to manage before there is a corpus worth embedding.

**Negative / accepted.** Paraphrase recall is weaker at MVP: a question asking the same thing in
entirely different words may not surface. Near-duplicate detection is trigram-based, so a semantic
duplicate with no shared wording can slip into the bank. Both are bounded by the hard metadata filter —
candidates are already the right chapter, type, and difficulty — and both are exactly what the eval
metric watches.

**Cost of being wrong.** Low and symmetric. If the gap appears, adoption is an additive migration plus a
backfill, with no caller changes. Nothing about deferring makes later adoption harder — which is the
property that makes deferral the right default rather than a gamble.

## Alternatives considered

- **pgvector on the critical path from day one** (what docs 02/03/05/06/09 said). Rejected: it pays the
  full operational cost of ANN before there is evidence that lexical ranking over a filtered set is
  insufficient. If the evidence arrives, the cost is paid then — and the interface makes that cheap.
- **A dedicated vector store (Qdrant/Pinecone/Weaviate) from the start.** Rejected twice over: it
  assumes the vector need, and it adds a second stateful system with its own sync and consistency
  problems. Qdrant remains the documented step past roughly 5–10M vectors, if that is ever reached.
- **Elasticsearch/Meilisearch for lexical search now.** Better ranking than Postgres FTS, but a second
  stateful system to run, sync, and reindex — for a post-filter candidate set small enough to sort in
  memory. Deferred with its own explicit trigger.
- **Embeddings for dedup only, not retrieval.** A narrower version of the same cost. Trigram similarity
  already catches the dominant real-world case (the same exercise reprinted or lightly reworded).
