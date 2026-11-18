# 06 AI Architecture

## 1. Purpose

This document defines the architecture of the AI subsystem responsible for question generation, retrieval, validation, paper assembly, and quality assurance within the AI-powered Question Paper Generation SaaS platform.

It specifies how Large Language Models (LLMs), Retrieval-Augmented Generation (RAG), embeddings, prompt versioning, and validation pipelines work together to produce curriculum-aligned question papers. The AI subsystem is the engine behind the platform's core value proposition: transforming a teacher's blueprint into a high-quality, source-aware, curriculum-compliant question paper in minutes.

This document should be read alongside `02_System_Architecture.md` (module boundaries), `03_Database_Design.md` (question_bank, question_embeddings, prompt_versions), and `05_Textbook_Ingestion.md` (extraction pipeline).

---

## 2. AI Design Principles

These principles govern every AI decision in the platform. They are non-negotiable constraints, not aspirational goals.

### 2.1 Retrieval First

Always attempt to retrieve existing approved questions from the question bank, textbook assets, and previous papers before invoking AI generation. AI is the fallback, not the default.

### 2.2 AI Generates Only Missing Questions

The AI generation engine is invoked only when the retrieval pipeline cannot satisfy a blueprint slot. Every generated question is a gap-filler, not a replacement for curated content.

### 2.3 Never Hallucinate Curriculum

AI prompts are grounded with curriculum context (board, class, subject, chapter, learning outcomes). The system never asks the LLM to invent curriculum structure. All taxonomy references are injected from the database.

### 2.4 Every AI Output Must Be Traceable

Every generated question records its prompt_version_id, model, provider, token count, generation timestamp, and the grounding context that produced it. No black-box outputs are permitted.

### 2.5 Every Question Must Have Provenance

The `source` field on `question_bank` records whether a question came from `institute_bank`, `textbook`, `intext`, `previous_year`, or `ai_generated`. Papers display this provenance to teachers for transparency.

### 2.6 Deterministic Prompting

Prompts are templated and versioned. Dynamic context (curriculum, examples, constraints) is injected at runtime, but the template structure is fixed per version. This ensures reproducibility and supports A/B evaluation.

### 2.7 Version Everything

Prompt templates, embedding models, embedding vectors, and generated questions all carry version identifiers. Any change to prompts or models creates a new version, never an in-place mutation.

### 2.8 Human Editable

Every AI-generated question enters the system as a draft. Teachers can edit, approve, or reject any AI output before it enters the final paper. AI is a tool, not a decision-maker.

### 2.9 Cost Aware

The system tracks token usage, estimates cost per generation, and routes between models based on complexity. Retrieval is always cheaper than generation. Caching and batching reduce unnecessary LLM calls.

### 2.10 Provider Agnostic in Design, Gemini in Practice

The AI service layer abstracts over LLM providers behind the `LlmProvider` interface (§17). Switching
providers requires a new adapter, not a rewrite.

**The only implemented adapter is Gemini** (ADR-008). The abstraction is retained because it is what
makes a future provider change a localised code change, but it is not a per-call routing mechanism: a
provider swap carries its own prompt versions and its own eval baseline, because prompts tuned against
one model do not transfer without re-evaluation. The interface buys optionality and negotiating
leverage, not free substitution.

### 2.11 Correctness Is Verified, Not Judged

Structured output guarantees *shape*, not *truth*. Numeric and quantitative answers are checked by
**executing** the worked solution; MCQs are checked by asserting exactly one option satisfies the key.
An LLM is never the sole gate on a correctness claim (§10, ADR-010).

---

## 3. AI System Overview

The AI subsystem follows a retrieval-first, generate-on-demand, validate-everything pipeline. The high-level flow from teacher input to exported paper is:

```
Teacher
  |
  v
Blueprint Definition
  |  (total marks, sections, question types, difficulty mix,
  |   Bloom mix, source mix, chapter weightage)
  |
  v
Blueprint Validation
  |  (marks sum, section constraints, curriculum alignment)
  |
  v
Retrieval Engine
  |  (metadata filter -> vector search -> full-text search -> ranking)
  |
  v
Question Bank Candidates
  |
  v
Coverage Check: Enough Questions?
  |
  +-- Yes --------------------------------> Paper Assembly
  |                                            |
  +-- No (gaps identified)                     v
       |                                   Answer Key
       v                                       |
  Prompt Builder                               v
       |                                   Export (PDF/DOCX)
       v
  AI Generation Engine (LLM)
       |
       v
  JSON Response Parser
       |
       v
  Validation Pipeline
  (grammar, marks, difficulty, Bloom,
   curriculum, duplicate, similarity)
       |
       v
  Approved? ----No----> Retry / Reject
       |
      Yes
       |
       v
  Store in Question Bank
  (source = ai_generated, status = draft)
       |
       v
  Paper Assembly
       |
       v
  Export
```

---

## 4. AI Modules

The AI subsystem is composed of six core modules. Each module has clear responsibilities, inputs, outputs, and boundaries.

### 4.1 Retrieval Engine

**Purpose:** Select the best existing questions from the question bank that match a blueprint slot.

**Responsibilities:**
- Search textbook-extracted questions (`source = textbook`)
- Search previous year questions (`source = previous_year`)
- Search institute bank questions (`source = institute_bank`)
- Search in-text comprehension questions (`source = intext`)
- Apply metadata filtering (board, class, subject, chapter, difficulty, Bloom, marks, qtype, status)
- Rank lexically with `tsvector` full-text search and `pg_trgm` trigram similarity
- Perform semantic (vector) search **only once pgvector is adopted** — deferred at MVP (ADR-007)
- Rank candidates by relevance, recency, and diversity
- Deduplicate results using content_hash and embedding similarity

**Inputs:**
- Blueprint slot (subject, chapter, marks, difficulty, Bloom, qtype, source preference)
- Organization scope (org_id, scope)
- Curriculum version context

**Outputs:**
- Ranked list of candidate questions with relevance scores
- Coverage report (which slots are filled, which have gaps)

### 4.2 Prompt Builder

**Purpose:** Construct grounded, structured prompts for the LLM from blueprint requirements and curriculum context.

**Responsibilities:**
- Build system prompt with role definition and output format
- Build developer prompt with generation rules and constraints
- Build user prompt with specific question requirements
- Inject curriculum context (board, class, subject, chapter title, learning outcomes)
- Inject retrieved example questions as few-shot exemplars
- Inject textbook snippets for grounding (paragraphs, definitions, formulas)
- Inject paper constraints (marks, difficulty, Bloom level, question type, language)
- Inject output format specification (strict JSON schema)
- Select and apply the active prompt_version for the task

**Inputs:**
- Blueprint slot requiring AI generation
- Active prompt_version record
- Retrieved example questions (for few-shot grounding)
- Textbook assets (paragraphs, definitions, formulas for the relevant chapter)
- Curriculum metadata (chapter title, learning outcomes, board name)

**Outputs:**
- Complete prompt payload (system + developer + user messages)
- Prompt metadata (prompt_version_id, token estimate, grounding context summary)

### 4.3 AI Generation Engine

**Purpose:** Generate questions only when the retrieval engine cannot satisfy a blueprint slot.

**Responsibilities:**
- Send constructed prompts to the active LLM provider
- Parse structured JSON responses into question objects
- Handle provider errors, timeouts, and rate limits with retries
- Track token usage per request for cost metering
- Support multiple providers behind the provider interface

**Provider (ADR-008):** the Gemini API, addressed by **tier**, never by a dated model string:

| Tier | Used for | Selected by |
|------|----------|-------------|
| **Gemini Pro tier** | Question generation, rubric synthesis, hard reasoning, vision-based OCR fallback | `AI_MODEL_GENERATE` |
| **Gemini Flash tier** | Difficulty and Bloom classification, tagging, extraction, validation support, repair | `AI_MODEL_CLASSIFY` |
| **Gemini embedding model** | Embeddings, once ANN retrieval is adopted (ADR-007) | `AI_EMBEDDING_MODEL` |

Exact model IDs live in configuration and are never hardcoded in application code or in this document
(§25). A model upgrade is a config change plus an eval run, not a code change.

**Provider switching** requires only a new adapter implementing the `LlmProvider` interface. No business
logic changes — but a swap also requires its own prompt versions and its own eval baseline before it
can be promoted (§2.10).

**Inputs:**
- Constructed prompt payload from Prompt Builder
- Model selection (from prompt_version or routing rules)
- Generation parameters (temperature, max_tokens, top_p)

**Outputs:**
- Parsed question object(s) in the question_bank schema format
- Generation metadata (model, provider, tokens_in, tokens_out, latency_ms, cost_estimate)

### 4.4 Validation Engine

**Purpose:** Validate every AI-generated question against curriculum, structural, and quality rules before it enters the question bank.

**Responsibilities:**
- **Marks validation:** Verify marks match the requested value and are within allowed ranges
- **Difficulty validation:** Confirm difficulty label matches the requested level
- **Bloom validation:** Verify Bloom taxonomy level is correct for the question type
- **Grammar validation:** Check for grammatical correctness and language quality
- **Curriculum validation:** Ensure the question falls within the specified chapter and learning outcomes
- **Formatting validation:** Verify JSON structure, required fields, and option format
- **Answer correctness (execute-and-verify, ADR-010):** For numerical and quantitative items, *execute*
  the worked solution in a sandboxed runner (sympy) and assert the computed result equals the stated
  answer. For MCQs, assert **exactly one** option satisfies the key — never zero, never two. This is a
  hard gate, not an advisory flag.
- **Rubric validation:** Verify rubric exists for subjective questions and marks sum correctly
- **Source tagging:** Ensure source is set to `ai_generated` and prompt_version_id is recorded
- **Language validation:** Confirm the question is in the requested language

**Inputs:**
- Generated question object
- Blueprint slot constraints
- Curriculum context

**Outputs:**
- Validation result (pass/fail with detailed error list)
- Quality scores per dimension

### 4.5 Similarity Checker

**Purpose:** Detect duplicate and near-duplicate questions to prevent repetition within a paper and across the question bank.

**Responsibilities:**
- Compare AI-generated question embedding against existing question bank embeddings
- Compute cosine similarity scores
- Flag questions that exceed the similarity threshold
- Distinguish between exact duplicates (content_hash match) and semantic duplicates (embedding similarity)

**Rejection criteria:**
- Cosine similarity > configurable threshold (default: 0.92)
- Content hash exact match

**Inputs:**
- Generated question embedding
- Question bank embeddings for the same chapter/subject scope

**Outputs:**
- Similarity score against closest match
- Accept/reject decision with matched question_id if rejected

### 4.6 Paper Assembler

**Purpose:** Produce a paper that **jointly** satisfies every blueprint constraint from finite candidate
pools, or return an honest, specific account of why it cannot.

> **Terminology.** "Assembler" (equivalently "Paper Assembler") is the one name for this component
> across the whole document set. It is not called the "generation engine" — that term is reserved for
> nothing, and the component that calls the LLM is the **AI Generation Engine** (§4.3).

**Responsibilities:**
- Run the **feasibility gate** before any work is enqueued (§14.2)
- Solve the joint constraint problem: pattern × marks × difficulty mix × Bloom mix × source mix ×
  chapter/outcome coverage × no in-paper duplicates
- Apply the documented relaxation order when the problem is over-constrained, and emit a
  **compromise report** naming exactly which constraint was relaxed and by how much
- Arrange questions into sections as defined by the blueprint
- Order questions within sections (typically ascending marks)
- Calculate and verify total marks per section and overall
- Verify difficulty mix, Bloom mix, and source mix match the blueprint targets
- Verify chapter coverage meets the blueprint weightage
- Ensure no duplicate questions appear within the paper
- Generate the answer key from question answers and rubrics
- Generate paper metadata (generation timestamp, source breakdown, coverage report)
- Create the paper_version record with the assembled content

**Inputs:**
- Retrieved questions (from retrieval engine)
- Generated questions (from AI generation + validation)
- Blueprint specification
- Template layout (if specified)

**Outputs:**
- Assembled paper content (JSONB structure matching paper_versions.content)
- Answer key (JSONB structure matching paper_versions.answer_key)
- Coverage and quality report

---

## 5. Supported Generation Modes

Generation modes are defined once, in `01_Product_Requirements.md` under **GM-1 … GM-7**. That is the
authoritative list; this section states only what each mode means *to the AI subsystem*. The mode is
carried on the blueprint snapshot, so every paper records which mode produced it.

| ID | Mode | AI involvement | Raw material | `source` recorded |
|----|------|----------------|--------------|-------------------|
| **GM-1** | Textual Questions | **None.** Pure retrieval. | `textbook_assets` of kind `exercise` and `question` | `textbook` |
| **GM-2** | In-text Questions | **Grounded extraction only.** The model is given the passage and must produce questions answerable from it. | `textbook_assets` of kind `paragraph`, `table`, `diagram`, `activity`, `formula` | `intext` |
| **GM-3** | AI Generated | **Full authoring.** | Curriculum outcomes + retrieved exemplars for style | `ai_generated` |
| **GM-4** | Mixed Mode *(default)* | **Gap-fill only**, capped at 30% | All of the above, retrieval-first | Multiple |
| **GM-5** | Previous Year Questions | **None.** Pure retrieval. | `question_bank` where `source = previous_year` | `previous_year` |
| **GM-6** | Institute Question Bank | **None.** Pure retrieval, org-scoped. | `question_bank` where `source = institute_bank` | `institute_bank` |
| **GM-7** | Global Question Bank | **None.** Read-only retrieval of platform-curated content. | Global-scope `question_bank` rows | Global shared |

### 5.1 Rules the AI subsystem enforces per mode

- **AI-1.** If retrieval satisfies every slot, the LLM is not called at all and zero AI usage is metered.
- **AI-7.** GM-2 questions must cite the `textbook_assets` record they were derived from; a question
  without a citation is rejected.
- **AI-9 / BR-1.** In GM-4 the AI share cannot exceed 30%, and at least 70% of questions must be
  textbook-derived (`textbook` + `intext`). If retrieval cannot reach the floor, the assembler reduces
  the AI share and reports the gap, or flags the paper — it never silently breaches the floor.
- **AI-3.** In the retrieval-only modes (GM-1, GM-5, GM-6, GM-7) a shortfall is reported as an unmet
  slot. AI never silently substitutes for a mode that excludes it.
- Organization policy may disable any mode outright (Settings, UJ-2 step 6).

### 5.2 Per-source shares within GM-4

GM-4 accepts an explicit percentage breakdown per source. The shares are constraints handed to the
assembler's solver (§14), not a fill order — the solver satisfies them jointly with the difficulty,
Bloom, and coverage targets, or reports which one it had to relax.

**Example configuration:**

| Source | Percentage |
|--------|-----------|
| Textbook | 40% |
| In-text | 30% |
| Previous Year | 20% |
| AI Generated | 10% |

Shares must sum to 100% (CN-14) and respect BR-1.

---

## 6. Retrieval Architecture (RAG)

The retrieval pipeline is the first and most important stage of paper generation. It narrows the bank
with a hard metadata filter, then ranks what survives.

**Vector search is deferred (ADR-007).** After the hard filter — board, class, subject, chapter, type,
marks, difficulty, status — the candidate set for a slot is typically tens to low hundreds of short
questions. At that cardinality lexical ranking over an exactly-filtered set matches ANN quality without
an embedding worker, an HNSW index, a reindex-on-model-change path, or a vector store to operate. The
stage below is specified so it can be switched on without changing any caller.

### 6.1 Retrieval Pipeline

```
Blueprint Slot
  |
  v
Metadata Filter                                   [MVP]
  |  (org_id, scope, board_id, class_id, subject_id,
  |   chapter_id, difficulty, bloom, marks, qtype,
  |   source allowed by generation mode,
  |   status = approved, is_current = true)
  |
  v
Full-Text Search (PostgreSQL tsvector)            [MVP]
  |  (GIN index on stem + answer + rubric)
  |
  v
Trigram Similarity (pg_trgm)                      [MVP]
  |  (fuzzy surface match on stem)
  |
  v
Vector Search (pgvector HNSW)             [DEFERRED — ADR-007]
  |  (cosine similarity against question_embeddings
  |   where is_current = true)
  |
  v
Hybrid Ranking
  |  MVP weights:      metadata 0.4 + fts 0.4 + trigram 0.2
  |  With vectors:     metadata 0.3 + fts 0.2 + vector 0.5
  |                    (fused by Reciprocal Rank Fusion)
  |
  v
Deduplication
  |  (content_hash exact match removal;
  |   trigram similarity > 0.95 removal at MVP,
  |   embedding cosine > 0.95 once vectors exist)
  |
  v
Coverage Optimizer
  |  (spread across chapters/outcomes per blueprint weightage —
  |   sees the whole slot set, not one slot in isolation)
  |
  v
Top-K Selection
  |  (configurable, default K = 20 per slot)
  |
  v
Scored Candidate Questions  ──▶ Assembler
```

**The interface is the contract.** `RetrievalService.selectCandidates(slot)` returns scored candidates.
Whether a score came from lexical ranking or from ANN is invisible to the caller, which is what makes
the deferred stage a drop-in rather than a refactor.

**The adoption trigger** is a measured paraphrase-recall gap in the eval harness (§20) — not a date and
not intuition. Lexical shortfall is a different failure mode and points at a dedicated search engine,
not at embeddings.

### 6.2 Search Strategy Details

**Metadata Filtering** *(MVP)*
Applied first as a hard filter to narrow the search space. Uses the composite index on `(org_id, scope, source, board_id, class_id, subject_id, chapter_id, qtype, difficulty, status)`. Only `status = approved` and `is_current = true` questions are considered. This stage does most of the discrimination, which is the reason ANN is not needed early.

**Full-Text Search** *(MVP)*
PostgreSQL GIN index on a generated tsvector column combining `stem`, `answer`, and `rubric` fields. Provides keyword-level matching. Exact-terminology matches — formulae, named laws, defined terms — fall out of lexical ranking naturally, which is a strength, not a limitation, for curriculum content.

**Trigram Similarity** *(MVP)*
`pg_trgm` GIN index on `stem`. Supports fuzzy surface matching and powers near-duplicate detection without embeddings.

**Vector Search** *(deferred — ADR-007)*
Uses pgvector's HNSW index on `question_embeddings.embedding` for approximate nearest neighbor search. The query embedding is computed from the blueprint slot description (chapter title + learning outcomes + question type + difficulty). Cosine distance is the similarity metric.

**Hybrid Ranking**
Combines the active stages using configurable weights (§25.1). At MVP: metadata 0.4, full-text 0.4, trigram 0.2. With vectors adopted: metadata 0.3, full-text 0.2, vector 0.5, fused by Reciprocal Rank Fusion. Weights are tunable per deployment.

**Deduplication**
Removes exact duplicates (same `content_hash`) and near-duplicates from the candidate set — trigram similarity > 0.95 at MVP, embedding cosine similarity > 0.95 once vectors exist. Preserves the highest-ranked version of each cluster. Also excludes questions already placed in the paper being assembled.

---

## 7. Embedding Architecture

> **Status: deferred (ADR-007).** Nothing in this section is built at MVP. It is specified now so that
> adopting embeddings later is an additive migration and a config change, not a redesign. The trigger
> is a measured paraphrase-recall gap in the eval harness (§20).

### 7.1 Purpose

Embeddings enable semantic search across the question bank. They allow the Retrieval Engine to find questions that are conceptually similar to a blueprint requirement, even when the exact keywords differ — the one thing lexical ranking genuinely cannot do.

### 7.2 Embedding Specification

| Property | Value |
|----------|-------|
| **Embedding Model** | Gemini embedding model (tier reference; exact model ID in `AI_EMBEDDING_MODEL`, never hardcoded) |
| **Native output width** | 3072 dimensions |
| **Stored width** | **1536** — Matryoshka-truncated and re-normalised by the provider adapter |
| **Why truncate** | pgvector's HNSW and IVFFlat indexes accept at most **2000** dimensions for the `vector` type. A raw 3072-dim vector cannot be ANN-indexed. Truncation to 1536 keeps the index path available; `halfvec(3072)` (HNSW limit 4000) is the documented alternative if full fidelity is ever needed. |
| **Storage** | `question_embeddings.embedding`, declared `vector(1536)`; the actual width is recorded per row in `dimensions` |
| **Index Type** | HNSW (Hierarchical Navigable Small World) |
| **Similarity Metric** | Cosine similarity (1 - cosine distance) |
| **HNSW Parameters** | `m = 16`, `ef_construction = 200`, `ef_search = 100` (tunable) |

Truncation and re-normalisation happen inside the Gemini adapter, so `dimensions` is always the truth
about a stored row and a future width change is detectable rather than silent.

### 7.3 Update Strategy

- Embeddings are computed on question creation and on content change (new version).
- When a question is versioned (new `version_number`), a new embedding record is created with `is_current = true`, and the previous embedding is marked `is_current = false`.
- The `previous_embedding_id` field maintains the version chain.
- Batch embedding is used during import operations (up to 2048 texts per API call).
- Embedding computation is asynchronous: the `embedding.requested` event triggers a worker job.

### 7.4 Versioning

- Each embedding record stores the `model` name and `dimensions` used.
- If the embedding model changes globally, all questions are re-embedded in a background migration job.
- The `checksum` field on `question_embeddings` detects content changes that require re-embedding.
- Old embedding versions are soft-deleted, not purged, to support rollback.

---

## 8. Prompt Architecture

The prompt architecture follows a structured three-layer design. Prompts are versioned and stored in the `prompt_versions` table. **No actual prompt content is stored in this document.** This section describes the structure and composition rules.

### 8.1 Prompt Layers

**System Prompt**
Defines the AI's role, capabilities, and behavioral constraints. Sets the output format (strict JSON schema). Establishes the rules the model must follow (never generate outside curriculum, never invent chapters, always provide an answer). This layer is the same across all generation tasks of the same type.

**Developer Prompt**
Contains the generation rules specific to the task: marks allocation rules, difficulty definitions (what makes a question easy/medium/hard), Bloom taxonomy level descriptions, question type structural requirements (MCQ must have 4 options with exactly 1 correct, etc.), and language/formatting rules.

**User Prompt**
The dynamic per-request prompt containing the specific generation request: the exact chapter, learning outcomes, question type, marks, difficulty, Bloom level, and language required.

### 8.2 Dynamic Context Injection

The following context is injected into the prompt at runtime, never hardcoded:

| Context | Source | Purpose |
|---------|--------|---------|
| **Retrieved Questions** | Retrieval engine output | Few-shot exemplars showing the expected quality and format |
| **Curriculum Metadata** | `chapters`, `learning_outcomes` | Grounds generation in the actual syllabus |
| **Bloom Rules** | Developer prompt template | Defines what each Bloom level means for question construction |
| **Difficulty Rules** | Developer prompt template | Defines what easy/medium/hard means structurally |
| **Textbook Snippets** | `textbook_assets` (paragraphs, definitions, formulas) | Provides factual grounding to prevent hallucination |
| **Output Format** | JSON schema specification | Enforces structured output matching question_bank columns |

### 8.3 Output Format

The LLM is instructed to return a strict JSON array of question objects. Each object maps directly to the `question_bank` schema:

```json
[
  {
    "stem": "...",
    "qtype": "mcq",
    "marks": 2,
    "difficulty": "medium",
    "bloom": "application",
    "language": "en",
    "options": [
      {"label": "A", "text": "...", "is_correct": false},
      {"label": "B", "text": "...", "is_correct": true},
      {"label": "C", "text": "...", "is_correct": false},
      {"label": "D", "text": "...", "is_correct": false}
    ],
    "answer": {"correct_option": "B", "explanation": "..."},
    "rubric": null,
    "learning_outcome_ids": []
  }
]
```

### 8.4 Prompt Versioning

Prompts are never edited in place. Every change creates a new record in `prompt_versions` with an incremented `version` number.

- **Task scoping:** Each prompt is scoped to a `task` (e.g., `generate_mcq`, `generate_subjective`, `validate_question`, `classify_difficulty`).
- **Model binding:** Each prompt version is bound to the `model` it was tuned and evaluated against — the resolved Gemini model ID from configuration. A prompt promoted against the Flash tier is not automatically valid on the Pro tier; it needs its own eval run.
- **Lifecycle:** `candidate` -> `active` -> `retired`. Only one version per (task, model) pair can be `active` at a time (enforced by partial unique index).
- **Rollback:** Promoting a previous version to `active` automatically retires the current active version.
- **Traceability:** Every AI-generated question records the `prompt_version_id` that produced it via the `created_by` + `meta` fields.

---

## 9. AI Generation Pipeline

The generation pipeline is the complete end-to-end flow from blueprint submission to stored, validated
questions. The pipeline follows the **software-first hierarchy**: retrieval, parametric/template-based
generation, and deterministic classification run before any Gemini call. Most slots should be filled
without reaching the LLM (§18.1.1).

```
Teacher submits blueprint
  |
  v
Blueprint Validation
  |  (marks sum check, section constraints,
  |   curriculum version alignment, plan quota check)
  |
  v
Job Enqueued (Redis)
  |  (paper.generation_requested event, async)
  |
  v
Generation Worker picks up job
  |
  v
For each blueprint slot:
  |
  +---> Retrieval Engine                                       ← SOFTWARE: free
  |       |
  |       v
  |     Enough candidates?
  |       |
  |       +-- Yes --> Add to paper candidates
  |       |
  |       +-- No --> Identify gap
  |                    |
  |                    v
  |                  Template-Based Generation                 ← SOFTWARE: free
  |                    |  (match slot to a question template;
  |                    |   instantiate with random parameters;
  |                    |   compute answer deterministically)
  |                    |
  |                    v
  |                  Template produced a valid question?
  |                    |
  |                    +-- Yes --> Validation Pipeline
  |                    |             (same gates as Gemini output)
  |                    |
  |                    +-- No --> Gemini Generation            ← AI: costly
  |                                 |
  |                                 v
  |                               Prompt Builder
  |                                 |  (inject curriculum, examples,
  |                                 |   textbook snippets, constraints)
  |                                 |
  |                                 v
  |                               LLM Provider Call
  |                                 |
  |                                 v
  |                               JSON Response Parser
  |                                 |  (extract question objects,
  |                                 |   handle malformed responses)
  |                                 |
  |                                 v
  |                               Validation Pipeline
  |                    |             (grammar, marks, difficulty,
  |                    |              Bloom, curriculum, format)
  |                    |
  |                    v
  |                  Passed? --No--> Retry (max 2)
  |                    |               |
  |                   Yes              +--> Log failure,
  |                    |                    skip slot
  |                    v
  |                  Duplicate Check
  |                    |  (content_hash + embedding
  |                    |   similarity check)
  |                    |
  |                    v
  |                  Passed? --No--> Regenerate
  |                    |
  |                   Yes
  |                    |
  |                    v
  |                  Store in question_bank
  |                    (source = ai_generated | template_generated,
  |                     status = draft,
  |                     prompt_version recorded if AI)
  |
  v
Paper Assembly
  |  (arrange sections, calculate marks,
  |   verify coverage, generate answer key)
  |
  v
Store paper_version
  |
  v
Emit paper.generated event
  |
  v
Notify teacher (progress -> complete)
```

### 9.1 Template-Based Generation

A **question template** is a parameterized stem with typed placeholders, a generator function that
instantiates them, and a deterministic answer-computation function. Templates are the primary
generation path for question types whose structure is regular enough to parameterize.

| Property | Description |
|---|---|
| **Stem template** | A string with typed placeholders, e.g. `"Find the value of {a}x² + {b}x + {c} when x = {d}"` |
| **Parameter spec** | Types, ranges, and constraints for each placeholder (e.g. `a: int[-5,5], a≠0`) |
| **Generator** | A pure function that samples parameters within spec, instantiates the stem, and computes the correct answer deterministically (sympy, arithmetic, lookup) |
| **Applicable types** | Primarily `numerical`, `fill_blank`, `one_word`, `mcq` (quantitative), `true_false` (formula-based); less applicable to `long_answer`, `case_study`, `source_based` |
| **Quality** | Template output is *more* trustworthy than Gemini output because the answer is computed, not predicted. It still passes the same validation pipeline. |
| **Source tag** | `source = template_generated` — distinct from `ai_generated` for cost tracking |

Templates are stored in `backend/packages/prompts/templates/` alongside prompt text, version-controlled
in git. A template that produces a question failing validation is a code bug, not a retry scenario.


---

## 10. Question Validation Pipeline

Every AI-generated question passes through a multi-stage validation pipeline before it is accepted. Validation failures are categorized and logged for prompt improvement.

### 10.1 Validation Stages

| Stage | Check | Rule | Action on Failure |
|-------|-------|------|-------------------|
| 1 | **JSON Structure** | Response must be valid JSON matching the expected schema | Retry generation |
| 2 | **Required Fields** | stem, qtype, marks, difficulty, language must be present | Reject |
| 3 | **Marks** | Must match requested marks value exactly | Reject |
| 4 | **Difficulty** | Must match requested difficulty level | Reject |
| 5 | **Bloom** | Must match requested Bloom taxonomy level (if specified) | Reject |
| 6 | **Question Type** | Structure must match qtype rules (MCQ: 4 options, 1 correct; etc.) | Reject |
| 7 | **Language** | Must be in the requested language | Reject |
| 8 | **Grammar** | Basic grammar and spelling check | Flag for review |
| 9 | **Curriculum Alignment** | Content must relate to the specified chapter and outcomes | Reject |
| 10 | **Answer Correctness (executed)** | Numerical/quantitative: the worked solution is executed in a sandbox and must equal the stated answer. MCQ: exactly one option satisfies the key. | **Reject** — hard gate (ADR-010) |
| 10b | **Qualitative Plausibility** | For long/short answers, where no execution is possible: LLM-as-judge produces an advisory confidence flag | **Flag only** — never blocks |
| 11 | **Rubric Completeness** | Subjective questions must have rubric with marks breakdown | Reject |
| 12 | **Formatting** | No markdown artifacts, proper punctuation, clean text | Flag for review |
| 13 | **Duplicate Check** | content_hash must not match existing questions | Reject |
| 14 | **Similarity Check** | Embedding similarity must be below threshold | Reject |
| 15 | **Source Tagging** | source = ai_generated, prompt_version_id recorded | Auto-fix |

### 10.2 Validation Results

Each validation produces a structured result:

```json
{
  "passed": true,
  "stages": [
    {"stage": "marks", "passed": true},
    {"stage": "difficulty", "passed": true},
    {"stage": "grammar", "passed": true, "score": 0.94},
    {"stage": "similarity", "passed": true, "score": 0.31, "closest_id": "..."}
  ],
  "overall_score": 0.92,
  "flags": []
}
```

### 10.3 Execute-and-Verify (ADR-010)

Stage 10 is the single most important gate in the platform, because wrong answers are the fastest way
to destroy teacher trust — and they are exactly the failure class an LLM cannot be trusted to catch in
itself. Asking a model to check its own arithmetic is circular.

**Numerical and quantitative items**

1. The model is required to emit a `solution_steps` field containing a machine-executable derivation
   alongside the human-readable working.
2. The verifier evaluates it with **sympy** inside a sandboxed runner.
3. The computed result must equal the stated answer within the declared tolerance, with units checked.
4. Mismatch ⇒ the item is **rejected and regenerated**, never repaired in place and never flagged-and-kept.

**Sandbox constraints** — the runner has no network access, no filesystem access, a hard CPU-time cap,
a memory cap, and a restricted expression namespace. Execution is a verification step, not an
extensibility point; nothing user-authored reaches it except the model's own derivation.

**MCQ items**

Assert **exactly one** option satisfies the key. Zero-correct and two-correct MCQs are both hard
failures. Distractors must be textually distinct from each other and from the key.

**Parametric generation is preferred where it applies**

For quantitative slots, the cheapest and most trustworthy path is not generation at all: take a
known-correct retrieved item, vary its parameters within declared constraints, and recompute the answer
deterministically. The result is correct by construction. Free-form generation is the fallback when no
suitable template exists.

**Qualitative items**

Long and short answers cannot be executed. They get an advisory LLM-as-judge confidence flag which is
surfaced to the teacher and never blocks. The human approval step (AI-6) is the real gate here.

**CI enforcement.** The answer-correctness check runs in CI against a fixture set of blueprints and
gates the generation pipeline. A regression in correctness pass rate fails the build (§20).

---

## 11. Duplicate Detection

### 11.1 Exact Duplicate Detection

Uses the `content_hash` field on `question_bank`. The hash is computed from a normalized representation of the question stem, options, and answer. Two questions with the same content_hash are considered exact duplicates regardless of metadata differences.

**Algorithm:** SHA-256 of normalized (lowercased, whitespace-collapsed, punctuation-stripped) stem + sorted options text + answer text.

### 11.2 Near-Duplicate Detection

**At MVP:** `pg_trgm` trigram similarity on the normalised stem. Two questions above the threshold are
near-duplicates. This catches the dominant real-world case — the same exercise reprinted across
editions, or reworded slightly.

**Once vectors are adopted (ADR-007):** embedding cosine similarity via pgvector, which additionally
catches paraphrases that share no surface text.

**Default threshold:** 0.92 (configurable via `AI_SIMILARITY_THRESHOLD`). The threshold applies to
whichever signal is active; both are cosine-like scores in [0, 1].

Note that "duplicate" is context-dependent and the threshold is tuned on real data, not fixed by
intuition: within one paper, testing a concept twice is forbidden; across Set A / Set B variants it
must differ; against a teacher's recent papers it is a staleness nudge rather than a rejection.

### 11.3 Semantic Duplicate Detection (advisory)

For questions near the threshold and flagged during teacher review, the system can run a more expensive
semantic comparison using an LLM judge call. This is **advisory only** and is triggered on demand by
the teacher — it is never part of the automated pipeline and never a gate, consistent with §10.3.

### 11.4 Deduplication Scope

- **Within a paper:** No two questions in the same generated paper may be duplicates (exact or near).
- **Within the question bank:** Exact duplicates are blocked on insert (content_hash uniqueness). Near-duplicates are flagged but allowed if the teacher explicitly approves.
- **Cross-organization:** Global-scope questions are deduplicated across the platform. Org-scope questions are deduplicated within the organization.

---

## 12. Question Classification

AI-assisted classification enriches question metadata during import, generation, and review.

### 12.1 Classification Dimensions

| Dimension | Values | Method |
|-----------|--------|--------|
| **Difficulty** | easy, medium, hard | Rule/heuristic signals first; Gemini Flash tier for the residual |
| **Bloom** | remember, understand, apply, analyze, evaluate, create | Stem verb analysis first; Gemini Flash tier for the residual |
| **Chapter** | chapter_id from curriculum | Metadata from blueprint; heading/lineage match for imports |
| **Learning Outcome** | learning_outcome_ids | Similarity mapping against the chapter's outcome list |
| **Marks** | numeric(4,1) | Parsed mark hint, blueprint requirement, or teacher assignment |
| **Question Type** | The 13 types QT-1 … QT-13 defined in `01_Product_Requirements.md`: `mcq`, `true_false`, `one_word`, `fill_blank`, `short_answer`, `long_answer`, `assertion_reason`, `case_study`, `numerical`, `diagram_based`, `match`, `very_short_answer`, `source_based` | Structural analysis of question format (deterministic) |
| **Language** | en, hi, etc. | Language detection or blueprint requirement |
| **Source** | institute_bank, textbook, intext, previous_year, ai_generated | Origin tracking, **never inferred** |

Classification is deliberately cheap. It is a supervised-classification problem, not a reasoning
problem: deterministic rules handle the majority, and only low-confidence residuals escalate to the
Gemini Flash tier. A Pro-tier model is never used to label difficulty.

### 12.2 Classification Pipeline (software-first)

Classification follows the software-first hierarchy: deterministic rules run first; Gemini Flash is
called only when the rules cannot produce a confident label. "Genuinely inconclusive" is defined by
`CLASSIFICATION_FALLBACK_THRESHOLD` (default 0.7) — the rules emit a confidence score, and only
scores below the threshold escalate.

For imported questions without complete metadata, the classification pipeline runs as a background job:

1. **Deterministic rules** (free, always run first):
   - **Question type:** Structural analysis of the stem — option count, blank presence, true/false
     keywords, assertion/reason markers, match-column detection. Fully deterministic; never needs AI.
   - **Difficulty:** Heuristic signals: step count, operator complexity, vocabulary level (grade-band
     word lists), answer length, presence of multi-step reasoning keywords. Produces a score + label.
   - **Bloom level:** Stem verb analysis against the Bloom verb taxonomy (e.g. "list" → remember,
     "compare" → analyze, "design" → create). Produces a score + label.
   - **Chapter:** Metadata from the blueprint (for generated questions) or heading/lineage match from
     the textbook parser (for imports). Falls through to vector search only for unanchored imports.
   - **Marks:** Parsed mark hint ("[3]", "3 marks") or defaulted by qtype.
   - **Language:** Script/character-set detection.

2. **Confidence check** — if all required dimensions have scores ≥ `CLASSIFICATION_FALLBACK_THRESHOLD`,
   classification is complete. No LLM call.

3. **Gemini Flash fallback** (only for low-confidence residuals):
   - Embed the question stem (if not already embedded).
   - Use the embedding to find the nearest chapter (vector search, if available; FTS/trigram otherwise).
   - Call the classification LLM prompt (task = `classify_question`) with the stem, the rules' partial
     results, the matched chapter, and available learning outcomes.
   - Validate and store the classification results.

4. **Validate and store** the final classification (from whichever source).

The expectation is that most questions are classified entirely by step 1. Step 3 exists for questions
where the stem is ambiguous or the heuristics conflict — not as a co-routine that runs on every item.


---

## 13. Textbook Understanding

The textbook understanding pipeline transforms raw uploaded documents into structured, retrievable content that grounds AI generation. This section describes how the AI interprets textbook content.

### 13.1 Extraction Pipeline

| Stage | Technology | Output |
|-------|-----------|--------|
| **OCR** | **Mathpix (primary)** → LaTeX for equation-dense pages; Gemini Pro-tier vision as fallback; Tesseract/cloud OCR for plain-text and table pages. All behind the `DocumentExtractor` interface (ADR-009). | Raw text + LaTeX per page |
| **Paragraph Extraction** | Layout analysis + NLP sentence boundary detection | `textbook_assets` with `kind = paragraph` |
| **Exercise Detection** | Pattern matching (numbered lists, "Q.", "Exercise") + LLM classification | `textbook_assets` with `kind = exercise` |
| **Question Detection** | Pattern matching + structural analysis | `textbook_assets` with `kind = question` |
| **Example Detection** | "Example", "Solved Example", "Illustration" keyword + LLM | `textbook_assets` with `kind = activity` |
| **Definition Detection** | Pattern matching ("is defined as", bold terms) + NLP | Stored in `textbook_assets.data` JSONB |
| **Formula Detection** | LaTeX/MathML pattern recognition, image-based formula OCR | Stored in `textbook_assets.data` JSONB |
| **Diagram Detection** | Image region detection, caption extraction | `textbook_assets` with `kind = diagram` |
| **Activity Detection** | "Activity", "Do it yourself" keyword patterns | `textbook_assets` with `kind = activity` |
| **Table Detection** | Structural analysis of grid patterns | `textbook_assets` with `kind = table` |

### 13.2 Chapter Mapping

Extracted assets are mapped to chapters using:

1. **Explicit page ranges:** If the textbook's table of contents is extracted, chapter boundaries are mapped by page number.
2. **Heading detection:** Chapter headings detected via font size, bold text, and numbering patterns.
3. **LLM-assisted mapping:** For ambiguous content, an LLM call maps the asset to the most relevant chapter based on content similarity to chapter titles and learning outcomes.

### 13.3 Learning Outcome Mapping

Each extracted question and exercise is mapped to learning outcomes by:
1. Embedding the extracted text.
2. Computing similarity against learning outcome descriptions for the mapped chapter.
3. Assigning the top-matching learning outcomes above a confidence threshold.

---

## 14. Paper Assembly Logic

The paper assembler takes validated questions (retrieved + generated) and arranges them into a final paper that satisfies all blueprint constraints.

### 14.1 Assembly Order

1. **Section assignment:** Assign questions to sections based on blueprint section definitions (by qtype, marks range, or chapter).
2. **Question ordering:** Within each section, order questions by ascending marks (configurable: ascending difficulty, chapter sequence, or random).
3. **Marks verification:** Verify that each section's question marks sum to the section's target marks. Verify that all sections sum to total paper marks.
4. **Difficulty mix verification:** Check that the distribution of easy/medium/hard questions matches the blueprint target (within tolerance of +/- 1 question).
5. **Bloom mix verification:** Check that the Bloom taxonomy distribution matches the blueprint target.
6. **Source mix verification:** Check that the source breakdown (textbook/AI/previous_year/etc.) matches the blueprint percentages.
7. **Chapter coverage verification:** Check that questions cover the specified chapters with the requested weightage.
8. **Duplicate check:** Final sweep to ensure no duplicate questions within the paper.
9. **Answer key generation:** Compile answers and rubrics for all questions in paper order.
10. **Metadata generation:** Record generation statistics (sources used, AI tokens consumed, coverage percentage, quality scores).

### 14.2 Constraint Satisfaction Strategy (ADR-009)

The assembler is a **real constraint solver**, not a greedy fill. Greedy fill demos well against a rich
bank and fails against the thin, real corpus of a newly onboarded coaching centre — which is precisely
the population the product must serve on day one.

**Why greedy is insufficient.** Pattern, difficulty mix, Bloom mix, source mix, and chapter coverage
must hold **jointly**. A greedy pass that satisfies each constraint in priority order can consume, in
an early slot, the only candidate that a later slot needed, and then report a gap that a different
assignment would not have produced. The failure is silent and looks like "the bank is too thin."

#### Feasibility gate

Runs **before the job is enqueued**, executing the same solver with a short time budget. A per-source
marginal count check ("does each source have enough questions for its quota?") is necessary but **not
sufficient** — it can pass and still fail mid-assembly. Three outcomes:

| Outcome | Meaning | What the teacher sees |
|---|---|---|
| **feasible** | A valid assignment exists | Generation proceeds |
| **feasible with compromise** | A valid assignment exists only if one constraint relaxes | The exact constraint, the exact amount, and an accept/adjust choice — e.g. "only 2 previous-year questions exist for these chapters; the remaining 6% will be reallocated to AI" |
| **infeasible** | No assignment exists | Concrete options: reduce a source share, broaden chapters, or reallocate to AI |

No silent substitution, and no "looked feasible, produced a bad paper" surprise after the job runs.

#### Relaxation order

Documented and fixed, so the compromise is predictable and reviewable rather than emergent:

1. **Never relaxed** — total marks (BR-3), section structure (PP-2), allowed types per section (PP-3),
   no duplicates within the paper, curriculum scope.
2. **Relaxed first** — difficulty mix within its stated tolerance.
3. **Relaxed second** — per-source share.
4. **Relaxed third** — Bloom mix.
5. **Relaxed fourth** — chapter/outcome coverage spread.
6. **Relaxed last** — the Mixed-Mode 70% textbook floor (BR-1), which is a product guarantee.

**Learning-outcome priority (P4-T4).** Outcome coverage is co-optimized with chapter
coverage inside tier 4; it is not a sixth tier and is not advisory-only. Both dimensions
measure curriculum spread, so a lexicographic preference between them would be arbitrary.
Their report entries remain distinct, while their aggregate slack is protected equally
relative to Bloom (less protected) and the textbook floor (more protected).

**P4-T2 production engine (2026-09-19).** The P0-T1 spike selected OR-Tools CP-SAT and P4-T2
promoted that engine into `packages/domain/assembler`. The five soft tiers are enforced
lexicographically, not by relying on the spike's fixed `1/10/100/1000` coefficients: aggregate slack
in a larger pool must never invert the fixed order. The synchronous API boundary is
`POST /api/v1/orgs/{org_id}/papers/feasibility`; it loads the RLS-visible P2 bank and returns before
any generation enqueue.

#### Compromise report

Every non-trivial outcome carries a machine-readable report naming which constraint relaxed, by how
much, and why — the same structure the coverage report (FR-4.19) renders. A paper that silently
under-delivers is a correctness bug; a paper that says exactly what it could not do is a usable result.

#### Testing obligation

The solver is the highest-value unit-test target in the platform. Property-based tests over randomized
question pools assert the invariant: **the solver always returns either a valid paper or an honest
"infeasible + why"** — never a paper that violates a non-relaxable constraint, and never a silent
compromise. `packages/domain` holds the solver as pure, I/O-free code precisely so these tests run in
milliseconds without a database.

---

## 15. AI Quality Scoring

Every AI-generated question receives a composite quality score. Questions below the confidence threshold are rejected or flagged for human review.

### 15.1 Scoring Dimensions

| Dimension | Weight | Description |
|-----------|--------|-------------|
| **Curriculum Score** | 0.25 | How well the question aligns with the specified chapter and learning outcomes |
| **Difficulty Score** | 0.15 | How accurately the question matches the requested difficulty level |
| **Bloom Score** | 0.10 | How accurately the question matches the requested Bloom level |
| **Similarity Score** | 0.15 | Inverse of similarity to existing questions (higher = more unique) |
| **Grammar Score** | 0.15 | Language quality and grammatical correctness |
| **Coverage Score** | 0.10 | How well the question contributes to blueprint coverage |
| **Format Score** | 0.10 | Structural correctness (options count, answer presence, rubric) |

### 15.2 Composite Score

```
overall_confidence = sum(dimension_score * weight)
```

### 15.3 Threshold Actions

| Score Range | Action |
|-------------|--------|
| >= 0.85 | Auto-accept (stored as draft) |
| 0.70 - 0.84 | Accept with review flag |
| 0.50 - 0.69 | Regenerate (retry up to 2 times) |
| < 0.50 | Reject and log for prompt improvement |

---

## 16. Prompt Versioning

### 16.1 Version Lifecycle

```
candidate ──promote──> active ──retire──> retired
                          ^                  |
                          |                  |
                          +---promote--------+
                          (rollback)
```

### 16.2 Prompt Version Record

Each prompt version in the `prompt_versions` table contains:

| Field | Purpose |
|-------|---------|
| `id` | Unique identifier (UUID) |
| `task` | Generation task scope (e.g., `generate_mcq`, `classify_difficulty`) |
| `version` | Monotonically increasing version number per task |
| `template_ref` | Reference to the prompt template (stored in code or config, not in DB) |
| `model` | The LLM model this prompt is optimized for |
| `author` | User who created this version |
| `eval_score` | JSONB containing offline evaluation metrics |
| `status` | `candidate`, `active`, or `retired` |
| `previous_prompt_version_id` | Links to the previous version for lineage |

### 16.3 Operational Procedures

**Release:** A Super Admin promotes a `candidate` prompt to `active` via `POST /prompt-versions/:id/promote`. The currently active version for the same (task, model) is automatically retired.

**Rollback:** A Super Admin promotes a previously retired version back to `active`. The same retirement cascade applies.

**A/B Testing:** Two candidate versions can be evaluated side-by-side using offline evaluation (see Section 20). Only one is promoted to active based on evaluation results.

**Evaluation:** Each candidate prompt is evaluated against a benchmark set of expected questions before promotion. The `eval_score` JSONB stores precision, recall, quality scores, and human ratings.

---

## 17. AI Provider Layer

### 17.1 Architecture

```
Generation Service / Validation Service
  |
  v
LlmProvider (abstract interface — the stable contract)
  |
  +---> GeminiAdapter                     ← the ONLY implemented adapter
  |       Pro tier    : generation, rubric synthesis, hard reasoning, vision OCR fallback
  |       Flash tier  : classification, tagging, extraction, repair, validation support
  |       Embedding   : embeddings (deferred until ADR-007 fires)
  |
  +---> (future adapters implement the same contract; each carries its own
         prompt versions and its own eval baseline before it can be promoted)
```

The abstraction is kept because it localises a future provider change to one class. It is **not** a
per-call routing mechanism, and it is not a cost-shopping layer — see §18.

### 17.2 Provider Interface

The contract is provider-agnostic; the implementation targets the Gemini API. Pydantic models define
the request/response shapes so validation happens at the boundary.

```python
from abc import ABC, abstractmethod
from pydantic import BaseModel

class LlmRequest(BaseModel):
    messages: list[Message]
    model: str                      # resolved from config by tier — never a literal
    temperature: float = 0.3
    max_tokens: int = 2048
    response_schema: dict | None = None   # Gemini structured output

class LlmResponse(BaseModel):
    content: str
    model: str
    provider: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    cost_estimate: float
    finish_reason: str
    safety_blocked: bool = False

class LlmProvider(ABC):
    id: str

    @abstractmethod
    async def generate(self, request: LlmRequest) -> LlmResponse: ...

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]: ...

    @abstractmethod
    def estimate_tokens(self, text: str) -> int: ...

    @abstractmethod
    async def is_available(self) -> bool: ...
```

`GeminiAdapter` implements this against the Gemini API: it maps tier names to configured model IDs,
requests structured output via a response schema, normalises Gemini's finish reasons and safety blocks
into the uniform failure taxonomy the Retry Manager consumes, truncates and re-normalises embeddings to
the stored width (§7.2), and reports token usage for metering.

### 17.3 Provider Selection

The **tier** is chosen by the Model Selector from the task; the **concrete model ID** is resolved from
configuration; the active `prompt_version` for that (task, model) pair supplies the prompt. No model
string appears in application code.

Failover, if a second adapter ever exists, is a deliberate pipeline-wide switch with its own prompt
versions and eval run — not a per-request fallback. Switching model families mid-pipeline changes the
output distribution enough to invalidate prompts and eval baselines tuned on the original.

---

## 18. Cost Optimization

### 18.1 Strategies

| Strategy | Description | Impact |
|----------|-------------|--------|
| **Retrieval first** | Avoid generation entirely when existing questions suffice | Eliminates LLM cost for retrieved questions |
| **Parametric variants** | Vary parameters of a known-correct retrieved item and recompute the answer deterministically | Free, and *more* trustworthy than generation (§10.3) |
| **Prompt caching** | Cache compiled prompts for identical blueprint slots within a session | Reduces prompt construction overhead |
| **Embedding caching** | Cache computed embeddings; only re-embed on content change (checksum-based) | Reduces embedding API calls by ~80% |
| **Batch embedding** | Embed in batches during imports (batch size tuned to the provider) | Reduces per-text overhead |
| **Token estimation** | Estimate tokens before sending to catch oversized prompts | Prevents wasted calls |
| **Prompt compression** | Remove redundant whitespace, use concise instructions | Reduces input token cost |
| **Model tiering** | Gemini **Flash tier** for classification, tagging, extraction, repair and validation support; Gemini **Pro tier** reserved for actual question generation and hard reasoning | Reduces cost several-fold for non-generation tasks |
| **Result caching** | Cache generation results by `(prompt_hash, model)` and by identical (chapter, qtype, marks, difficulty) combinations within a time window | Reduces redundant generations |

### 18.1.1 The cost cascade (software-first)

The cascade is **the primary design instrument** of the AI-minimization pivot. It is an ordered sequence
evaluated per request inside the AI module; the first rung that satisfies the request wins. Rungs 0–3
are not "early-exit optimizations on the way to an LLM call" — they are the **expected path** for
most requests. Gemini rungs (4–6) exist for the residual that software genuinely cannot handle.

```
request for a question / classification
 ├─ 0. EXACT CACHE     content hash · (prompt_hash, model) → stored result       ~free
 ├─ 1. BANK REUSE      retrieval from question_bank (org + global + PYQ)         ~free
 ├─ 2. TEMPLATE GEN    instantiate a parameterized template, compute answer      ~free   ← SOFTWARE
 ├─ 3. RULES/LOCAL     deterministic classifiers for difficulty · Bloom · dedup  ~free   ← SOFTWARE
 ├─ 4. GEMINI FLASH    extraction, low-confidence classification residual,
 │                     repair (only when rung 3 score < CLASSIFICATION_           $       ← AI FALLBACK
 │                     FALLBACK_THRESHOLD)
 ├─ 5. GEMINI PRO      question generation for types that cannot be templated,    $$      ← AI FALLBACK
 │                     rubric synthesis
 ├─ 6. ESCALATION      Pro tier with extended reasoning — only on repeated
 │                     verification failure, or scheduled audits                  $$$
 └─ 7. DEGRADE         budget exhausted / provider down → serve retrieval-only,
                       mark slots "needs manual authoring", never hard-fail        —
```

**The goal is that most requests never reach rung 4.** Rungs 0–3 are free and cover the majority of
calls. For a mature tenant with a populated bank and good template coverage, the Gemini rungs should be
hit only for question types that are inherently non-templatizable (long_answer, case_study, source_based)
or for genuinely novel content not in the bank.

Never reaching rung 5 is a larger lever than which model sits at rung 5. Rung 7 is a first-class
outcome — a budget-exhausted tenant gets a partially-retrieved paper with honest gaps, not a 500.

**Enforcement.** Per-tenant token budgets are checked **at rung entry**, before spend. The AI module
emits `ai_cost_usd` and `cascade_rung` per call, so a rising share of rung-4/5/6 traffic is an alert
rather than a surprise on the invoice.

**Cold-start caveat.** Rungs 1–2 depend on a populated bank and template library. A brand-new tenant
skips them and lands on rung 5 for most slots. Budget AI as a declining per-tenant curve from
onboarding, not a flat minority share. Template coverage grows as the platform matures.


### 18.2 Cost Tracking

Every LLM call records:
- `tokens_in`, `tokens_out` in the usage table (`metric = ai_tokens_in`, `ai_tokens_out`)
- `cost_estimate` computed from provider pricing tables
- Aggregated daily in `analytics_daily.ai_questions`

---

## 19. Safety Architecture

### 19.1 Hard Constraints

The AI system must never:

| Constraint | Enforcement |
|------------|-------------|
| Generate questions outside the specified syllabus/chapter | Curriculum validation in the validation pipeline |
| Leak another organization's data | Tenant-scoped retrieval; AI layer receives only org-scoped context |
| Generate offensive, violent, or inappropriate content | System prompt safety instructions + post-generation content filter |
| Ignore the requested marks value | Marks validation rejects non-matching questions |
| Ignore the blueprint constraints | Full blueprint validation before and after assembly |
| Ignore the requested language | Language validation in the validation pipeline |
| Access the database directly | AI layer is stateless; all data access goes through the backend service layer |
| Store or log student data | No student data exists in the platform (teacher-facing only) |

### 19.2 Defense in Depth

1. **Provider-level safety:** Gemini safety settings are configured explicitly per call (§19.3).
2. **Prompt-level safety:** System prompt includes explicit safety instructions.
3. **Input-level safety:** Untrusted content is delimited as data, never as instructions (§19.4).
4. **Validation-level safety:** Every output is validated against curriculum and content rules.
5. **Assembly-level safety:** The assembler rejects questions that fail any validation.
6. **Human-level safety:** All AI output enters as `draft` and requires teacher review (AI-6).

### 19.3 Gemini Safety Settings

Safety thresholds are set explicitly on every request rather than left to provider defaults, because
"whatever the default happened to be this quarter" is not a reviewable security posture. Gemini exposes
per-category thresholds; the platform sets all four harm categories to block medium-and-above:

| Harm category | Threshold |
|---|---|
| Harassment | Block medium and above |
| Hate speech | Block medium and above |
| Sexually explicit | Block medium and above |
| Dangerous content | Block medium and above |

Thresholds are configuration (`AI_SAFETY_THRESHOLD`), so tightening them is a config change with an
audit trail, not a code change.

**Handling a safety block.** A blocked or truncated response is *not* a generation failure to retry
blindly. The adapter surfaces `safety_blocked = true` and the blocking category; the Retry Manager
treats it as a **non-retryable** outcome for that prompt, logs it against the `prompt_version_id`, and
falls through to retrieval or records an unmet slot (AI-3). Retrying an identical prompt that tripped a
safety filter simply burns budget. A sustained rise in safety blocks for one prompt version is a signal
that the prompt is wrong, and it alerts.

Subject matter matters here: legitimate curriculum content in biology, history, and chemistry can trip
naive filters. Blocks are therefore logged with the chapter and prompt version so thresholds can be
tuned against evidence rather than anecdote.

### 19.4 Prompt-Injection Defense

Textbook OCR output, retrieved exemplars, and teacher-supplied instructions are all **untrusted input**.

- Untrusted content is inserted only inside clearly delimited data blocks, accompanied by an explicit
  instruction that content within the block is **data, not instructions**.
- The system prompt asserts that any instruction found inside exemplars or textbook text must be
  ignored.
- The model is given no tool access beyond structured emission — there is no capability to escalate to.
- Every generated output is re-validated against the schema and the curriculum scope **after**
  generation, so a successful injection still cannot place content in a paper.
- An adversarial prompt-injection suite runs in CI against crafted exemplars and OCR text.

---

## 20. Evaluation Framework

### 20.1 Offline Evaluation

Conducted before promoting a prompt version to active. Uses a benchmark dataset of expected questions per chapter and type.

| Metric | Description |
|--------|-------------|
| **Precision** | Percentage of generated questions that pass all validation stages |
| **Recall** | Percentage of benchmark question types successfully generated |
| **Quality Score** | Average composite quality score (Section 15) across benchmark |
| **Format Compliance** | Percentage of responses that parse as valid JSON on first attempt |
| **Curriculum Alignment** | Percentage of questions that pass curriculum validation |
| **Latency** | Average generation time per question |
| **Cost** | Average `ai_cost_usd` per benchmark question, aggregated from P3-T6 `AiCallTelemetry` (with tokens and `cascade_rung` retained); the eval layer never reprices tokens |

**P3-T7 executable contract (2026-09-17).** The golden set is an adapter over P2's
`sample_import.json` and matching curriculum seed, not a second hand-maintained question fixture. It
currently contains 79 questions, 72 chapter/type cells, 13 chapters, and 12 question types. Precision
counts cases completing every pipeline validation stage; recall counts chapter/type cells with at least
one successful generation. Section 15's seven weights are applied to independently observed pipeline
facts. CI uses a deterministic provider replay through the real `CostCascade`; provider-backed studies
use the same harness and caller-resolved prompts.

### 20.2 Online Evaluation

Conducted on live traffic after a prompt version is promoted.

| Metric | Description |
|--------|-------------|
| **Teacher Acceptance Rate** | Percentage of AI-generated questions kept in final papers |
| **Teacher Edit Rate** | Percentage of AI-generated questions edited before use |
| **Regeneration Rate** | Percentage of generation attempts that require retry |
| **Manual Review Rate** | Percentage of questions flagged for human review |

### 20.3 Prompt Comparison

When evaluating a candidate prompt against the current active prompt:

1. Generate questions for the same benchmark set using both prompts.
2. Compare quality scores, precision, format compliance, and cost.
3. Optionally: blind A/B test with teachers (show questions from both prompts without labeling).
4. Promote only if the candidate outperforms or matches the active version on all key metrics.

The comparison API accepts two existing P3-T3 `PipelinePrompts` values. In a live study those values
come from `PromptResolver` and therefore retain the `prompt_versions` row IDs, artifact hashes, and
model bindings. Both variants are required to return the same ordered benchmark case IDs.

### 20.4 Model Comparison

Same methodology as prompt comparison, but holding the prompt constant and switching the model. Used when evaluating a new LLM release or a cheaper model alternative.

The P3-T7 runner records both configured model bindings in the report and computes metric deltas over
the identical frozen case tuple. No parallel prompt-version registry or model table exists in eval code.

---

## 21. AI Metrics

### 21.1 Operational Metrics

| Metric | Source | Granularity |
|--------|--------|-------------|
| Generation Time (end-to-end) | Generation worker logs | Per paper |
| LLM Latency | Provider response time | Per LLM call |
| Retriever Recall | Retrieved vs. required questions | Per blueprint |
| Duplicate Rate | Similarity checker rejections | Per generation batch |
| Validation Failure Rate | Validation pipeline rejections | Per question |
| Retry Rate | Questions requiring regeneration | Per question |

### 21.2 Quality Metrics

| Metric | Source | Granularity |
|--------|--------|-------------|
| Teacher Acceptance Rate | Paper edit tracking | Per organization, per month |
| Teacher Edit Rate | Paper version diffs | Per organization, per month |
| Average Quality Score | Validation pipeline scores | Per prompt version |
| Curriculum Alignment Rate | Validation pipeline | Per chapter |

### 21.3 Cost Metrics

| Metric | Source | Granularity |
|--------|--------|-------------|
| Average Tokens (in/out) | Usage table | Per model, per task |
| Average Cost per Question | P3-T6 `AiCallTelemetry.ai_cost_usd` (usage + deployment pricing), aggregated without repricing | Per model, per task, per cascade rung |
| AI Usage (total calls) | Usage table | Per organization, per day |
| Prompt Usage | Generation logs | Per prompt version |
| Model Usage | Generation logs | Per model |

### 21.4 Dashboard

All metrics are aggregated into `analytics_daily` and surfaced on:
- **Platform dashboard** (Super Admin): cross-tenant AI health, cost trends, model performance.
- **Organization dashboard** (Coaching Admin): org-level generation stats, acceptance rates, cost allocation.

---

## 22. Future AI Roadmap

These capabilities are planned for future releases and are not part of the v1 architecture.

| Feature | Description | Priority |
|---------|-------------|----------|
| **Adaptive Paper Generation** | Adjust difficulty based on class performance data | Medium |
| **Student Performance-Based Papers** | Generate papers targeting individual student weaknesses | Low |
| **Difficulty Prediction** | ML model to predict question difficulty from text features | Medium |
| **Automatic Bloom Detection** | Fine-tuned classifier for Bloom taxonomy without LLM | Medium |
| **Automatic Rubric Generation** | Generate marking rubrics for subjective questions | High |
| **Multilingual Generation** | Generate questions in Hindi, regional languages | High |
| **Diagram Question Generation** | Generate questions that reference or include diagrams | Low |
| **Handwritten OCR** | Support handwritten question paper scanning | Low |
| **Voice Question Generation** | Generate questions from spoken input | Low |
| **AI Paper Reviewer** | AI-powered review of assembled papers for coherence | Medium |
| **AI Question Improver** | Suggest improvements to existing questions | Medium |

---

## 23. Sequence Diagrams

### 23.1 Generation Flow

```
Teacher          Next.js         FastAPI         Redis           Gen Worker    Retrieval Engine    AI Service       PostgreSQL
  |                 |               |               |               |               |               |               |
  | 1. Submit       |               |               |               |               |               |               |
  |   blueprint     |               |               |               |               |               |               |
  |---------------->|               |               |               |               |               |               |
  |                 | 2. POST       |               |               |               |               |               |
  |                 |  /papers/     |               |               |               |               |               |
  |                 |  generate     |               |               |               |               |               |
  |                 |-------------->|               |               |               |               |               |
  |                 |               | 3. Validate   |               |               |               |               |
  |                 |               |   blueprint   |               |               |               |               |
  |                 |               | 4. Check quota|               |               |               |               |
  |                 |               | 5. Save paper |               |               |               |               |
  |                 |               |   (draft)  ---|---------------|---------------|-------------->|               |
  |                 |               | 6. Enqueue    |               |               |               |               |
  |                 |               |   job ------->|               |               |               |               |
  |                 |<-- 202 -------|               |               |               |               |               |
  |<-- job_id ------|               |               |               |               |               |               |
  |                 |               |               | 7. Consume    |               |               |               |
  |                 |               |               |   job ------->|               |               |               |
  |                 |               |               |               | 8. Retrieve   |               |               |
  |                 |               |               |               |   candidates->|               |               |
  |                 |               |               |               |               | 9. Query   -->|               |
  |                 |               |               |               |               |<-- results ---|               |
  |                 |               |               |               |<-- ranked  ---|               |               |
  |                 |               |               |               |               |               |               |
  |                 |               |               |               | 10. Gaps?     |               |               |
  |                 |               |               |               |   Yes         |               |               |
  |                 |               |               |               | 11. Build  -->|               |               |
  |                 |               |               |               |   prompt      |-------------->|               |
  |                 |               |               |               |               |               | 12. LLM call  |
  |                 |               |               |               |               |<-- questions--|               |
  |                 |               |               |               | 13. Validate  |               |               |
  |                 |               |               |               | 14. Dedup     |               |               |
  |                 |               |               |               | 15. Store  ---|---------------|-------------->|
  |                 |               |               |               |               |               |               |
  |                 |               |               |               | 16. Assemble paper            |               |
  |                 |               |               |               | 17. Save version ------------|-------------->|
  |                 |               |               |               |               |               |               |
  |                 |               |               |<-- progress --|               |               |               |
  |<-- SSE: done ---|<-- event -----|<-- pub/sub ---|               |               |               |               |
  |                 |               |               |               |               |               |               |
```

### 23.2 Validation Flow

```
Gen Worker       Validator        Similarity       PostgreSQL
  |                 |               |               |
  | 1. Validate     |               |               |
  |   question ---->|               |               |
  |                 | 2. Check      |               |
  |                 |   structure   |               |
  |                 | 3. Check      |               |
  |                 |   marks       |               |
  |                 | 4. Check      |               |
  |                 |   difficulty  |               |
  |                 | 5. Check      |               |
  |                 |   Bloom       |               |
  |                 | 6. Check      |               |
  |                 |   curriculum  |               |
  |                 | 7. Check      |               |
  |                 |   grammar     |               |
  |                 | 8. Dedup ---->|               |
  |                 |               | 9. Embed      |
  |                 |               |   question    |
  |                 |               | 10. Search    |
  |                 |               |   similar --->|
  |                 |               |<-- matches ---|
  |                 |               | 11. Compare   |
  |                 |<-- result ----|               |
  |<-- validation   |               |               |
  |   result        |               |               |
```

### 23.3 Embedding Flow

```
Questions Module  Event Bus        Embed Worker     AI Service       PostgreSQL
  |                 |               |               |               |
  | 1. question     |               |               |               |
  |   created/      |               |               |               |
  |   versioned     |               |               |               |
  | 2. Emit ------->|               |               |               |
  |   embedding.    |               |               |               |
  |   requested     | 3. Deliver -->|               |               |
  |                 |               | 4. Load       |               |
  |                 |               |   question ---|-------------->|
  |                 |               |<-- stem ------|               |
  |                 |               | 5. Embed ---->|               |
  |                 |               |               | 6. Provider   |
  |                 |               |               |   embed call  |
  |                 |               |<-- vector ----|               |
  |                 |               | 7. Store      |               |
  |                 |               |   embedding --|-------------->|
  |                 |               |   (is_current |               |
  |                 |               |    = true,    |               |
  |                 |               |    prev marked|               |
  |                 |               |    false)     |               |
```

---

## 24. Error Handling

### 24.1 Error Catalogue

| Error | Cause | Handling |
|-------|-------|----------|
| **No Questions Retrieved** | Empty question bank for the requested scope | Fall through to AI generation; if AI-only mode fails, return partial paper with coverage gaps |
| **Low Coverage** | Not enough questions to fill the blueprint | Return paper with coverage report; teacher can accept partial or regenerate |
| **Duplicate Questions** | AI generated a near-duplicate of an existing question | Regenerate with modified prompt (add "avoid similarity to: ..." context) |
| **LLM Timeout** | Provider did not respond within the configured timeout | Retry with exponential backoff (max 3 attempts); switch to fallback provider if available |
| **Embedding Failure** | Embedding API error or timeout | Retry with backoff; queue for later processing; question is stored without embedding |
| **OCR Failure** | Textbook page could not be processed | Mark page as failed; continue with remaining pages; report to user |
| **Provider Down** | LLM provider is unreachable | Switch to fallback provider; if no fallback, queue the job for retry with delay |
| **Rate Limit** | Provider rate limit exceeded | Queue with backoff; respect `Retry-After` header; spread requests across providers |
| **Prompt Failure** | Prompt produces consistently invalid output | Log failure with prompt_version_id; alert Super Admin; fall back to previous prompt version |
| **JSON Parse Error** | LLM returned non-JSON or malformed JSON | Retry with explicit JSON instruction; if persistent, use a JSON repair heuristic |
| **Validation Cascade Failure** | Multiple validation stages fail | Reject the question; log all failures; do not retry more than 2 times per slot |
| **Quota Exceeded** | Organization has exceeded their plan's generation quota | Return 402/429 with clear message; do not enqueue the job |

### 24.2 Retry Strategy

```
Attempt 1: Generate with standard prompt
  |
  Fail? --> Attempt 2: Regenerate with enhanced context
                         (add "previous attempt failed because: ...")
              |
              Fail? --> Attempt 3: Regenerate with fallback model
                          |
                          Fail? --> Log, skip slot, report gap
```

---

## 25. AI Configuration

All AI behavior is controlled by configurable values. No magic numbers are hardcoded.

### 25.1 Configuration Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `AI_SIMILARITY_THRESHOLD` | 0.92 | Cosine similarity threshold for duplicate detection |
| `AI_TOP_K_RETRIEVAL` | 20 | Number of candidate questions retrieved per slot |
| `AI_TEMPERATURE` | 0.3 | LLM temperature for generation (low for consistency) |
| `AI_MAX_TOKENS` | 2048 | Maximum output tokens applied by the pipeline request builder to both generation and classification calls |
| `AI_PROVIDER` | gemini | Active LLM provider (only `gemini` is implemented) |
| `AI_MODEL_GENERATE` | *(Gemini Pro tier ID)* | Model for generation and hard reasoning. Set in config; never hardcoded. |
| `AI_MODEL_CLASSIFY` | *(Gemini Flash tier ID)* | Model for classification, tagging, extraction, repair |
| `AI_TENANT_TOKEN_BUDGET` | 100000 | Default per-tenant token ceiling used by the pre-spend reservation gate. A plan-backed gate may supply a tenant-specific value through the same interface. |
| `AI_ESCALATION_THINKING_BUDGET` | 4096 | Additional provider reasoning-token reservation used only at cascade rung 6. |
| `AI_COST_FLASH_INPUT_PER_MILLION_USD` / `AI_COST_FLASH_OUTPUT_PER_MILLION_USD` | 0 / 0 | Deployment-owned Flash pricing used to emit `ai_cost_usd`; zero means no price has been configured, not a hardcoded provider claim. |
| `AI_COST_PRO_INPUT_PER_MILLION_USD` / `AI_COST_PRO_OUTPUT_PER_MILLION_USD` | 0 / 0 | Deployment-owned standard Pro pricing used to emit `ai_cost_usd`. |
| `AI_COST_ESCALATION_INPUT_PER_MILLION_USD` / `AI_COST_ESCALATION_OUTPUT_PER_MILLION_USD` | 0 / 0 | Deployment-owned rung-6 pricing used to emit `ai_cost_usd`. |
| `AI_EMBEDDING_MODEL` | *(Gemini embedding model ID)* | Embedding model. Used only once ADR-007 fires. |
| `AI_EMBEDDING_DIMENSIONS` | 1536 | Stored vector width — Matryoshka-truncated from the model's native 3072 so HNSW's 2000-dim index limit is respected (§7.2) |
| `AI_SAFETY_THRESHOLD` | block_medium_and_above | Gemini safety threshold applied to all harm categories (§19.3) |
| `AI_RETRY_COUNT` | 2 | Maximum retries for failed generation |
| `AI_GENERATION_TIMEOUT_MS` | 30000 | Timeout for a single LLM call |
| `AI_BATCH_EMBED_SIZE` | 2048 | Maximum texts per embedding batch |
| `AI_QUALITY_THRESHOLD` | 0.70 | Minimum quality score for auto-accept |
| `AI_QUALITY_AUTO_ACCEPT` | 0.85 | Quality score above which questions are auto-accepted |
| `AI_HNSW_M` | 16 | HNSW index parameter: max connections per node |
| `AI_HNSW_EF_CONSTRUCTION` | 200 | HNSW index parameter: construction search depth |
| `AI_HNSW_EF_SEARCH` | 100 | HNSW index parameter: query search depth |
| `AI_HYBRID_WEIGHT_METADATA` | 0.4 | Hybrid search weight for metadata relevance (0.3 once vectors are adopted) |
| `AI_HYBRID_WEIGHT_FTS` | 0.4 | Hybrid search weight for full-text search (0.2 once vectors are adopted) |
| `AI_HYBRID_WEIGHT_TRIGRAM` | 0.2 | Hybrid search weight for trigram similarity |
| `AI_HYBRID_WEIGHT_VECTOR` | 0.0 | Hybrid search weight for vector similarity — 0 until ADR-007 fires, then 0.5 |
| `AI_VECTOR_SEARCH_ENABLED` | false | Master switch for the deferred ANN stage |
| `AI_SOLVER_TIME_BUDGET_MS` | 2000 | Time budget for the feasibility-gate dry-run solve (§14.2) |
| `AI_VERIFY_SANDBOX_TIMEOUT_MS` | 2000 | CPU-time cap for the answer-execution sandbox (§10.3) |
| `AI_FALLBACK_PROVIDER` | null | Reserved. No second provider is implemented; a swap is a deliberate pipeline-wide change (§17.3), not an automatic failover. |
| `CLASSIFICATION_FALLBACK_THRESHOLD` | 0.7 | Minimum confidence score from deterministic classification rules before Gemini Flash is called (§12.2). Below this threshold, the question escalates to rung 4 of the cost cascade. |
| `TEMPLATE_GENERATION_ENABLED` | true | Master switch for template-based question generation (§9.1). When true, the generation pipeline attempts to fill slots from parameterized templates before calling Gemini. |

**Note on `AI_HNSW_*`:** the HNSW parameters above are inert until pgvector is adopted. They are kept
so the adoption step has no undefined configuration.

### 25.2 Configuration Source

All parameters are loaded from environment variables, validated at startup by the Pydantic
`Settings` model in `packages/config`, and injected into request handlers and workers via FastAPI's
`Depends()`. The application **refuses to start** on missing or invalid configuration — a placeholder
signing key, a short secret, or an unset model ID is a boot failure, not a runtime surprise. Changes
require a deployment or config reload, not a database update.

Model IDs are configuration, never literals in code or in this document. This is what makes a model
upgrade a config change plus an eval run.

---

## 26. AI Data Flow

The complete data flow from teacher action to exported paper:

```
Teacher
  |
  v
Blueprint (JSONB)
  |  - total_marks, duration, sections[]
  |  - per section: qtype, marks, count,
  |    difficulty_mix, bloom_mix, source_mix
  |  - chapter_weightage, learning_outcomes
  |
  v
Retrieval Engine
  |  - Metadata filter (org, board, class,
  |    subject, chapter, difficulty, status)
  |  - Full-text search (GIN tsvector)
  |  - Trigram similarity (pg_trgm)
  |  - [deferred] Vector search (pgvector HNSW)
  |  - Hybrid ranking + dedup + coverage optimizer
  |
  v
Question Bank (PostgreSQL)
  |  - question_bank table
  |  - question_embeddings table
  |
  v
Gap Analysis
  |  - Compare retrieved vs. required
  |  - Identify unfilled slots
  |
  v
Prompt Builder
  |  - Load active prompt_version
  |  - Inject curriculum context
  |  - Inject retrieved exemplars
  |  - Inject textbook snippets
  |  - Inject constraints
  |
  v
LLM Provider
  |  - Send prompt
  |  - Receive JSON response
  |  - Track tokens + cost
  |
  v
Validation Pipeline
  |  - Structure, marks, difficulty
  |  - Bloom, curriculum, grammar
  |  - Duplicate check (hash + embedding)
  |  - Quality scoring
  |
  v
Question Bank (Store)
  |  - source = ai_generated
  |  - status = draft
  |  - prompt_version_id recorded
  |  - embedding computed async
  |
  v
Paper Assembler
  |  - Arrange sections
  |  - Calculate marks
  |  - Verify coverage
  |  - Generate answer key
  |
  v
Paper Version (PostgreSQL)
  |  - paper_versions.content (JSONB)
  |  - paper_versions.answer_key (JSONB)
  |
  v
Export Worker
  |  - Apply template layout
  |  - Render PDF / DOCX
  |  - Store in object storage
  |
  v
Teacher Review + Approval
```

---

## 27. Appendix

### 27.1 Glossary

| Term | Definition |
|------|-----------|
| **LLM** | Large Language Model. A neural network trained on text that generates human-like text responses. This platform uses the Gemini family exclusively (ADR-008). |
| **Model tier** | A capability class rather than a specific model — "Pro tier" for generation and hard reasoning, "Flash tier" for classification and support tasks. Concrete model IDs live in configuration so an upgrade never touches code. |
| **Execute-and-verify** | Checking a quantitative answer by running the worked solution in a sandbox and comparing the computed result to the stated answer, rather than asking a model to judge it (§10.3, ADR-010). |
| **Assembler** | The component that turns a blueprint plus candidate pools into a valid paper by joint constraint satisfaction (§14). The single canonical name for this component across the document set. |
| **Feasibility gate** | A short-budget dry-run solve executed before a generation job is enqueued, returning feasible / feasible-with-compromise / infeasible (§14.2). |
| **Compromise report** | The machine-readable record of which constraint the solver relaxed, by how much, and why. |
| **Cost cascade** | The ordered per-request sequence cache → bank → parametric → rules → Flash → Pro → escalate → degrade (§18.1.1). |
| **Embedding** | A fixed-length numerical vector representation of text that captures semantic meaning. Used for similarity search. |
| **RAG** | Retrieval-Augmented Generation. A pattern where relevant documents are retrieved and injected into an LLM prompt to ground the response in factual content. |
| **Prompt** | The structured input sent to an LLM, including system instructions, context, and the specific request. |
| **Hallucination** | When an LLM generates plausible-sounding but factually incorrect or fabricated content. The retrieval-first architecture minimizes this risk. |
| **HNSW** | Hierarchical Navigable Small World. An approximate nearest neighbor index algorithm used by pgvector for fast similarity search over high-dimensional vectors. |
| **Cosine Similarity** | A measure of similarity between two vectors, computed as the cosine of the angle between them. Ranges from -1 (opposite) to 1 (identical). Used as the primary similarity metric for embeddings. |
| **Top-K** | The number of results returned from a search query. In retrieval, Top-K controls how many candidate questions are considered per blueprint slot. |
| **Bloom Taxonomy** | A classification framework for educational learning objectives. Six levels: Remember, Understand, Apply, Analyze, Evaluate, Create. Used to categorize question cognitive complexity. |
| **Learning Outcome** | A specific, measurable statement of what a student should know or be able to do after studying a chapter. Stored in `learning_outcomes` table and used to ground AI generation. |
| **Curriculum Version** | A versioned snapshot of the syllabus (board + academic year). Stored in `curriculum_versions` and used to ensure questions align with the correct syllabus edition. |
| **Blueprint** | The teacher's specification for a paper: total marks, sections, question types, difficulty/Bloom/source distribution, and chapter coverage. Stored as JSONB on `generated_papers.blueprint`. |
| **Content Hash** | A SHA-256 hash of normalized question content used for exact duplicate detection. Stored on `question_bank.content_hash`. |
| **pgvector** | A PostgreSQL extension that adds vector data types and similarity search operators. **Deferred** at MVP (ADR-007); adopted on a measured paraphrase-recall gap. |
| **pg_trgm** | A PostgreSQL extension providing trigram similarity. Powers MVP fuzzy matching and near-duplicate detection without embeddings. |
| **Provider Interface** | The abstract interface (`LlmProvider`) that all LLM provider adapters implement. Gemini is the only implemented adapter; the interface exists so a future change is localised, not so providers can be swapped per call. |

---

## 28. Related Decisions

| ADR | Bearing on this document |
|---|---|
| [ADR-005](ADR/ADR-005-retrieval-first-ai-with-validation-gates.md) | Retrieval-first principle and the validation gate stack |
| [ADR-007](ADR/ADR-007-defer-pgvector-retrieval.md) | §6 retrieval pipeline, §7 embeddings |
| [ADR-008](ADR/ADR-008-gemini-as-sole-ai-provider.md) | §17 provider layer, §18 tiering, §19.3 safety, §25 config |
| [ADR-009](ADR/ADR-009-constraint-solver-assembler.md) | §4.6 and §14.2 assembler |
| [ADR-010](ADR/ADR-010-execute-and-verify-ai-correctness.md) | §10.3 execute-and-verify |
| **Prompt Version** | A versioned, immutable record of a prompt template stored in `prompt_versions`. Ensures every AI output is traceable to the exact prompt that produced it. |
