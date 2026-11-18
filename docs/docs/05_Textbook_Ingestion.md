# 05 Textbook Ingestion

## 1. Overview

### Why this module exists

Teachers and coaching institutes hold most of their trusted question material inside printed and digital textbooks, workbooks, and previous-year papers. That material is unstructured: it lives in PDFs and scans, mixed with prose, diagrams, and answer keys. The platform's core promise is **retrieval-first generation** — reuse existing, curriculum-aligned questions before asking the AI to author new ones (see [02_System_Architecture.md](02_System_Architecture.md) Retrieval Architecture). That promise is only credible if there is a large, clean, well-classified pool of real questions to retrieve from.

The Textbook Ingestion module is the pipeline that turns raw uploaded files into that pool. It converts a textbook into structured, chapter-anchored, classified, and vector-indexed questions that the generation engine can search.

### What problem it solves

- Removes manual data entry: a teacher uploads a chapter PDF instead of typing hundreds of questions.
- Preserves provenance: every extracted question is traceable back to a textbook, chapter, and page.
- Makes content retrievable: questions are embedded and indexed so semantically similar items surface during generation.
- Enforces tenant boundaries: ingested content is owned by the uploading organization and never leaks across tenants.

### How it fits into the platform

Ingestion is an **offline, asynchronous** pipeline driven by Celery workers in `backend/src/workers/`, a separate process set from the API with its own log stream. The client uploads a file directly to object storage, the API records metadata, and a chain of queued jobs (OCR → parse → classify → embed → import) processes the file without blocking the request. The output flows into `question_bank` and `question_embeddings`, which the online generation path reads from.

```
Teacher ── upload ──▶ Next.js ──▶ FastAPI API ──▶ Object Storage
                                       │
                                       ▼
                              Celery / Redis Queue
                                       │
        ┌──────────┬──────────┬────────┴────────┬───────────┬──────────────┐
        ▼          ▼          ▼                 ▼           ▼              ▼
     OCR Job → Parser Job → Classifier Job → Embedding Job → Import Job → Notify Job
        │          │             │                │             │
        └──────────┴─────────────┴────────────────┴─────────────┘
                                  ▼
                    PostgreSQL (textbooks, textbook_assets,
                    question_bank, question_embeddings, audit_logs)
```

### Scope

In scope: file upload, validation, OCR, structural parsing, educational-content detection, question extraction, classification, deduplication, embedding, import into the question bank, manual review, and reprocessing.

Out of scope: the online generation pipeline, paper assembly, and export (covered in [02_System_Architecture.md](02_System_Architecture.md) and later module docs).

### Responsibilities

- Accept and validate uploaded files (per CN-1, CN-2, CN-6).
- Persist ingestion metadata and extracted assets with full provenance.
- Orchestrate the processing job chain with retries and a dead-letter path.
- Produce classified, deduplicated, embedded questions in `question_bank` / `question_embeddings`.
- Surface progress, failures, and review queues to users via notifications (NT-x).

## 2. Objectives

The module delivers the following concrete outcomes:

- **Upload textbooks** — let teachers and coaching admins submit PDF, DOCX, PNG, and JPG source files through signed direct-to-storage uploads.
- **Extract educational content** — recover the structural skeleton of a book: chapters, subchapters, examples, activities, definitions, and notes.
- **Extract exercise questions** — pull end-of-chapter and exercise-block questions with their marks and answer keys where present.
- **Extract in-text questions** — capture questions embedded mid-prose ("Check your understanding", "Try this"), tagged as `source = intext`.
- **Classify questions** — assign qtype, marks, difficulty, bloom level, source, chapter, subject, language, and learning outcome.
- **Generate embeddings** — produce a versioned vector per question for semantic retrieval.
- **Store retrievable knowledge** — persist everything with provenance so generation can retrieve, cite, and reuse it.

## 3. Functional Requirements

These map to the PRD's FR-2.x ingestion requirements.

| ID | Requirement | Description |
|----|-------------|-------------|
| **FR-2.1** | Upload textbook | Accept a source file via signed direct-to-storage upload and record it in `textbooks` with status `uploaded`. |
| **FR-2.2** | Validate | Enforce file type (CN-2), size (CN-1, 500 MB max), and page cap (CN-6) on both client and server before enqueuing work. |
| **FR-2.3** | OCR processing | Convert scanned pages to text; skip OCR for pages with a reliable native text layer. |
| **FR-2.4** | Detect chapters | Identify chapter and subchapter boundaries and anchor extracted content to `chapters`. |
| **FR-2.5** | Detect exercises | Locate exercise blocks and end-of-chapter question sets. |
| **FR-2.6** | Detect in-text questions | Locate questions embedded within prose, distinct from exercise blocks. |
| **FR-2.7** | Generate embeddings | Produce a versioned embedding per extracted question and store it in `question_embeddings`. |
| **FR-2.8** | Import into Question Bank | Insert classified questions into `question_bank` with `source = textbook` or `intext`, status `draft`. |
| **FR-2.9** | Manual review | Present low-confidence extractions in a review queue for human approval before they become `approved`. |
| **FR-2.10** | Reprocess textbook | Re-run the pipeline for a textbook (e.g. after an engine upgrade) without creating duplicate questions. |

## 4. Supported File Formats

Validation is enforced against CN-2. The OCR column indicates whether pages are rasterized and OCR'd; the Native column indicates whether an embedded text layer is read directly.

| Format | OCR path | Native text path | Notes |
|--------|:--------:|:----------------:|-------|
| PDF | ✓ | ✓ | Native text layer used when present and reliable; falls back to OCR per page. |
| DOCX | ✓ | ✓ | Structured text and styles read natively; embedded images OCR'd. |
| PNG | ✓ | — | Image-only; always OCR'd. |
| JPG | ✓ | — | Image-only; always OCR'd. |

Files that fail the type check are rejected at upload with **EH-4** (unsupported or corrupt file, CN-2); files exceeding CN-1 or CN-6 are rejected with **EH-5** before any job is enqueued. (EH ids follow the catalogue in `01_Product_Requirements.md`: EH-1 is OCR failure, EH-2 generation timeout, EH-3 LLM failure.)

## 5. Upload Pipeline

The upload path keeps large binaries off the API process: the file travels directly from the browser to object storage using a short-lived signed URL, and the API only ever handles metadata.

**Steps:**

1. **Client validation** — the browser checks extension, MIME type, and size (CN-1, CN-2) before requesting an upload slot. Fast rejection, no network round-trip wasted on obviously invalid files.
2. **Server validation** — the API re-validates the declared type and size (never trusting the client), confirms the caller's role and tenant, and checks the org's quota/usage. Deny-by-default via the dependency chain (`get_current_user` → `get_org_context` → `require_roles`).
3. **Signed URL issuance** — the API generates a time-limited, single-use signed upload URL scoped to a tenant-namespaced object key (`{org_id}/textbooks/{uuid}/source.{ext}`). The key becomes `textbooks.file_key`.
4. **Direct upload to object storage** — the browser PUTs the file to storage using the signed URL. The API is not in the data path.
5. **Metadata persistence** — on upload confirmation, the API inserts a `textbooks` row: `org_id`, `subject_id`, `file_key`, `status = uploaded`, `pages` (null until counted), `row_version = 1`, timestamps. Optional `unique(org_id, file_key)` guards against duplicate registration.
6. **Queue creation** — the API enqueues the first job (OCR) on the Redis queue with the `textbook_id` as payload. The job is idempotent on `textbook_id` so a replay does not double-process.

Server-side content validation (magic-byte sniffing, malware scan — see §19) runs after the object lands and before OCR is dispatched; a file that fails is marked `failed` with **EH-4** and the pipeline stops.

## 6. Processing Pipeline

Once a `textbooks` row exists with status `uploaded`, an ordered chain of background jobs transforms the file. Each stage reads the previous stage's output from `textbook_assets` or the DB, writes its own, and advances the persisted status.

**Stages:**

1. **OCR / text acquisition** — for each page, use the native text layer when reliable, otherwise rasterize and OCR (§7). Produces per-page text with confidence scores. Advances `textbooks.status` → `ocr`.
2. **Page splitting** — normalize the document into discrete pages so every downstream asset can carry a `page` number for provenance.
3. **Text extraction** — assemble clean, ordered text per page from the native/OCR sources.
4. **Structural parsing** — segment text into structural units (headings, paragraphs, tables, images, formulae, exercises, activities) and write `textbook_assets` rows with the matching `kind` (§8). Advances `textbooks.status` → `parsed`.
5. **Chapter detection** — resolve heading structure into chapter/subchapter boundaries and link assets to `chapters` via `chapter_id` (§9).
6. **Question extraction** — mine exercise blocks and in-text prose for questions, emitting `question`-kind assets with raw stem, options, and answer where present (§10).
7. **Classification** — assign qtype, marks, difficulty, bloom, language, source, chapter, subject, and learning outcomes to each extracted question (§11).
8. **Embedding generation** — produce a versioned vector per question and store it in `question_embeddings` with model, dimensions, and checksum (§13). Advances `textbooks.status` → `embedded`.
9. **DB storage / import** — insert classified questions into `question_bank` (`source = textbook` / `intext`, `status = draft`), deduplicating against existing content (§12), then notify the uploader (NT-x).

Any stage failure moves the textbook toward the `failed` status after retries are exhausted (§17), with the failing job pushed to the dead-letter queue for inspection and replay.

## 7. OCR Architecture

OCR is the most failure-prone and compute-heavy stage, so it is designed to do the least work possible and to degrade gracefully.

- **Native text detection first** — for PDF and DOCX, the pipeline inspects each page for an embedded text layer. If the layer is present and passes a reliability check (sufficient character coverage, sane word ratios, no garbled encoding), the text is used verbatim and OCR is skipped for that page. This is faster, cheaper, and more accurate than OCR.
- **OCR engine — math-first (ADR-009 companion decision, recorded in [ADR-014](ADR/ADR-014-math-first-ocr.md))** — pages without a usable text layer (all PNG/JPG, scanned PDFs) are rasterized and routed by content:

  | Page content | Engine | Output |
  |---|---|---|
  | Equation- or diagram-dense (the STEM default) | **Mathpix — the primary path** | LaTeX + layout-aware diagram crops |
  | Mathpix unavailable, or a page it scores poorly | **Gemini Pro-tier vision** | LaTeX + text, lower determinism |
  | Plain prose and tables | Tesseract / cloud OCR | Plain text, table structure |

  STEM textbooks are the target market and they are equation- and diagram-dense. Forms-oriented OCR
  mangles mathematical notation, and corpus quality gates the entire retrieval-first thesis — a bank of
  garbled equations cannot be retrieved from, no matter how good the retrieval pipeline is. Math OCR is
  therefore the **primary** path, not a fallback. All three engines sit behind one `DocumentExtractor`
  interface — `extract(page) → { latex, text, assets, confidence }` — so the routing policy and the
  provider choice are both configuration, and a Phase-0 spike on real textbooks can change the default
  without touching the pipeline.
- **Image preprocessing** — before OCR, pages are deskewed, denoised, binarized, and up-scaled to the engine's preferred DPI. Preprocessing materially raises recognition accuracy on low-quality scans.
- **OCR confidence** — the engine returns per-block confidence. Blocks below a configured threshold are flagged; a page whose mean confidence is too low is marked for manual review (FR-2.9) rather than trusted silently.
- **Retry strategy** — transient OCR failures (timeouts, engine 5xx) retry with exponential backoff and jitter. A page that fails repeatedly is recorded as a failed page (counted in monitoring, §20) without aborting the whole textbook; the textbook can still complete on its remaining pages and surface the failures for review. A whole-document OCR failure exhausts retries then moves the textbook to `failed` (**EH-1**).

## 8. Structural Parser

The structural parser converts flat page text into typed segments, each persisted as a `textbook_assets` row whose `kind` column takes one of the schema-defined values.

| Segment | `textbook_assets.kind` | Notes |
|---------|------------------------|-------|
| Headings | (drives chapter detection) | Used to build chapter/subchapter structure; not always stored as an asset. |
| Paragraphs | `paragraph` | Body prose; the substrate for in-text question detection. |
| Tables | `table` | Preserved as structured `data` (JSONB) for match-the-following and data questions. |
| Images / diagrams | `diagram` | Stored by reference (`s3_key`) for diagram-based questions. |
| Formulae | `formula` | Captured as a distinct kind; math recognition is a future enhancement (§22). |
| Exercises | `exercise` | Exercise blocks and end-of-chapter sets; parent of extracted questions. |
| Activities | `activity` | "Do this" / lab-style activity blocks. |

Each asset carries `textbook_id`, optional `chapter_id`, optional `page`, and either inline `data` (JSONB) or an `s3_key` reference for binary content. The parser is deterministic and layout-aware; it does not classify questions — that is the extraction and classification stages' job.

## 9. Educational Content Detection

On top of the raw segments, this stage recovers the *pedagogical* structure of the book so that extracted questions can be anchored and retrieved by curriculum position.

- **Chapters & subchapters** — heading levels and numbering patterns are resolved into a chapter tree and matched to existing `chapters` rows (by number/title within the subject); assets are linked via `chapter_id`.
- **Exercises** — `exercise`-kind blocks are identified as the primary source of end-of-chapter questions.
- **Examples** — worked examples are detected and kept distinct from questions (they carry solutions inline and are not imported as answerable items).
- **Activities** — hands-on `activity` blocks are tagged but generally not imported as bank questions.
- **Definitions & notes** — definition boxes and margin notes are captured as `paragraph` assets with metadata, feeding retrieval context rather than the question pool.
- **In-text questions** — interrogatives embedded in prose ("Why does...?", "Check your understanding") are detected within `paragraph` assets and marked for extraction with `source = intext`.

## 10. Question Extraction Engine

This is the core of the module: turning detected exercise and in-text regions into structured question candidates. Extraction is rule-driven and pattern-based, with each recognized type mapped to the `question_bank.qtype` vocabulary and each candidate emitted as a `question`-kind asset before classification.

**Exercise questions** — numbered items inside `exercise` assets are split on their enumeration (1., 2., a), i)…), each becoming one candidate with its stem and any inline answer key.

**Per-type extraction rules:**

- **Fill-in-the-blanks** — detect blank runs (`____`, dotted leaders) inside a stem; the stem retains the blank marker, the answer (if keyed) is captured separately.
- **MCQs** — detect a stem followed by an option list (A/B/C/D, (a)/(b)…); options are stored in the `options` JSONB, the marked correct option (if present) in `answer`.
- **Match-the-following** — detect two parallel columns (often from a `table` asset); stored as paired lists in `options`/`answer` with the correct mapping.
- **Assertion–reason** — detect the "Assertion (A) … Reason (R)" template and its fixed option set; classified as its own qtype.
- **Long answer** — detect prompts with high mark allocation or verbs like "Explain / Describe / Discuss"; no options.
- **Short answer** — detect concise prompts ("Define", "State", "Name"); no options.
- **Numerical** — detect problems requiring computation (units, "Calculate", "Find the value"); answer captured as a value/expression.
- **Diagram questions** — detect prompts referencing a figure; linked to the relevant `diagram` asset via reference.
- **In-text questions** — extracted from `paragraph` assets flagged in §9, always tagged `source = intext`; otherwise treated like short/long answer for typing.

Every candidate records provenance (textbook, chapter, page) and its extraction confidence. Low-confidence candidates route to manual review (FR-2.9) instead of importing directly.

## 11. Question Classification

Each extracted candidate is enriched into a fully classified `question_bank` row. Classification follows the **software-first hierarchy** ([06_AI_Architecture.md](06_AI_Architecture.md) §12.2): deterministic rules and heuristics run first; Gemini Flash is called only for the residual where rules produce a confidence score below `CLASSIFICATION_FALLBACK_THRESHOLD` (default 0.7).

| Attribute | `question_bank` column | Source of value |
|-----------|------------------------|--------------------|
| Question type | `qtype` | Structural analysis of the stem — deterministic, never needs AI (§10). |
| Marks | `marks` (numeric(4,1)) | Parsed mark hint ("[3]", "3 marks") or defaulted by qtype. |
| Difficulty | `difficulty` (easy/medium/hard) | Heuristic signals first (step count, operator complexity, vocabulary level); Gemini Flash only for low-confidence residual. |
| Bloom level | `bloom` | Stem verb analysis against the Bloom verb taxonomy first; Gemini Flash only for ambiguous stems. |
| Source | `source` (`textbook` / `intext`) | Extraction origin (exercise vs prose) — deterministic from the parser. |
| Chapter | via `chapter_id` linkage | Chapter detection (§9) — heading/lineage match; vector search fallback for unanchored content. |
| Subject | via textbook's `subject_id` | Inherited from the textbook — deterministic. |
| Language | `language` | Script/character-set detection — deterministic. |
| Learning outcome | `learning_outcome_ids` / `competency_ids` (UUID[]) | Similarity mapping against the chapter's outcome list. |

The classifier also computes `content_hash` (used for deduplication, §12) and sets `status = draft`, `is_current = true`, `row_version = 1`. It never sets `approved` — approval is a human action (FR-2.9) preserving auditability (BR-11).

## 12. Deduplication

Ingestion must not flood the bank with duplicates — the same exercise may appear across editions, or a textbook may be reprocessed (FR-2.10). Deduplication runs before import.

- **Exact duplicate** — `content_hash` (a normalized hash of the stem + options + answer) is compared against existing `question_bank` rows for the same org/subject. A hash match is an exact duplicate and is not re-inserted.
- **Near duplicate** — for non-exact matches, the candidate is compared by **`pg_trgm` trigram similarity** on the normalised stem at MVP. Once pgvector is adopted (ADR-007) this becomes embedding cosine similarity over the HNSW index, which additionally catches paraphrases sharing no surface text.
- **Thresholds** — similarity above a high threshold (e.g. ≥ 0.95) is treated as a near-duplicate; a mid band routes to manual review; below the band the question is treated as new.
- **Merge strategy** — a detected duplicate does not discard information: provenance from the new occurrence (additional textbook/chapter/page) is attached to the existing question rather than creating a second row. Reprocessing therefore enriches provenance instead of multiplying rows, keeping question versioning immutable (BR-8).

## 13. Embedding Generation

Every imported question gets a vector so it is retrievable by semantic similarity. Embeddings are treated as a **versioned, pluggable contract**, not a hardcoded vendor dimension.

- **Model** — the embedding model is named per row in `question_embeddings.model`; the platform can run multiple models and migrate between them without losing history.
- **Vector & size** — the vector is stored in the `embedding` column and its length in `dimensions` (smallint), which must match the named model's contract. This lets the system validate and swap models safely.
- **Chunking** — a question stem is short and embedded as a single unit; long-answer prompts with extended context are truncated/normalized to the model's input window before embedding.
- **Metadata** — each embedding carries `checksum` (of the embedded input, for change detection), `is_current`, and `previous_embedding_id` to chain re-embeddings.
- **Storage** — vectors live in `question_embeddings` with the invariant **one current embedding per question per model** (`is_current = true`).
- **Indexing** — an HNSW index on `embedding` powers approximate nearest-neighbour search; supporting indexes exist on `(question_id, model)`, `is_current`, and `checksum`.

Re-embedding (model upgrade or edited stem) inserts a new row, points `previous_embedding_id` at the old one, and flips `is_current`, so retrieval always reads exactly one live vector per model while history is preserved. See [06_AI_Architecture.md](06_AI_Architecture.md) for model selection.

## 14. Database Interaction

Ingestion writes to a fixed chain of tables. Each row is scoped by `org_id` and protected by row-level security, so a worker only ever sees and writes its own tenant's data.

| Table | What it stores during ingestion |
| --- | --- |
| `textbooks` | One row per uploaded source file. Holds `org_id`, `subject_id`, `file_key`, `status`, `row_version`, page count, and processing timestamps. This row is the pipeline's state anchor — every stage advances its `status`. |
| `textbook_assets` | The structural output of parsing: one row per detected segment (`kind` ∈ paragraph / exercise / question / diagram / table / activity / formula). Carries `textbook_id`, `chapter_id`, `page`, and either inline `data` (JSONB) or an `s3_key` for extracted media. |
| `question_bank` | The importable questions promoted from `question`-kind assets. Holds the normalized stem, options, answer, classification columns (qtype, marks, difficulty, bloom, source, language), `content_hash`, `status = draft`, and `is_current`. |
| `question_embeddings` | One vector per imported question per model: `embedding`, `model`, `dimensions`, `checksum`, `is_current`, `previous_embedding_id`. Makes each question retrievable by semantic similarity. |
| `audit_logs` | An append-only trail of every state transition and manual review action (who reprocessed, who approved/rejected), satisfying auditability (BR-11). |
| `usage` | Per-org metering counters (pages processed, questions imported, embeddings generated) used for quota enforcement and billing. |

The write order mirrors the schema's migration dependencies: `question_bank` rows exist before their `question_embeddings`, and `textbooks` / `textbook_assets` are populated as parsing proceeds. All multi-row writes for a single stage commit inside one transaction so a partial parse never leaves orphaned assets.

## 15. State Machine

Two views of state coexist and must not be confused: the **persisted status** on `textbooks.status`, and the **in-memory job phases** the orchestrator moves through while a stage runs.

The logical orchestration flow is richer than what is durably stored:

```
uploaded ──▶ processing ──▶ ocr ──▶ parsed ──▶ classified ──▶ embedded ──▶ completed
                 │                                                            
                 └── on error ──▶ failed ──▶ retry ──▶ processing
```

Only a subset of these are persisted. The schema constrains `textbooks.status` to **`uploaded → ocr → parsed → embedded`**, or **`failed`** (default `uploaded`). The remaining labels are transient job phases that never reach the column:

| Logical phase | Persisted `textbooks.status` | Nature |
| --- | --- | --- |
| uploaded | `uploaded` | Persisted — row created, file landed. |
| processing | *(no change)* | In-memory — a worker holds the job; status stays at its last durable value. |
| ocr | `ocr` | Persisted — OCR/text extraction complete. |
| parsed | `parsed` | Persisted — structural parse complete. |
| classified | *(no change)* | In-memory — questions classified but not yet embedded; column stays `parsed`. |
| embedded | `embedded` | Persisted — embeddings generated; terminal success for the source file. |
| completed | *(no change)* | In-memory — import + notification done; represented by `embedded` plus imported `question_bank` rows. |
| failed | `failed` | Persisted — a stage exhausted retries; the job is on the DLQ. |
| retry | *(no change)* | In-memory — a `failed` row re-enqueued; on pickup it resumes at the failed stage. |

This keeps the persisted status small and monotonic (each durable transition only moves forward, or to `failed`), while the orchestrator tracks finer phases in the queue payload. Every persisted transition is written to `audit_logs` (BR-11).

## 16. Background Jobs

Ingestion runs as a chain of independent jobs on the Redis queue. Each job is idempotent on `textbook_id`, advances the persisted status where relevant, and enqueues the next job on success or the DLQ on failure. Workers scale per queue so slow stages (OCR) do not starve fast ones.

| Job | Trigger | Reads | Writes | Emits |
| --- | --- | --- | --- | --- |
| **OCR Job** | Upload validated | `textbooks.file_key` (object storage) | Extracted text/blocks; `status = ocr` | Parser Job |
| **Parser Job** | OCR complete | OCR output | `textbook_assets` rows; `status = parsed` | Classifier Job |
| **Classifier Job** | Parse complete | `question`-kind assets | Classification attributes on candidate questions (in-memory `classified` phase) | Embedding Job |
| **Embedding Job** | Classification complete | Classified question stems | `question_embeddings`; `status = embedded` | Question Import Job |
| **Question Import Job** | Embeddings ready | Candidates + dedup checks | `question_bank` rows (`status = draft`) after deduplication (§12) | Notification Job |
| **Notification Job** | Import complete | Import summary | — | User notification (NT-x) |

Each job persists its progress before enqueuing the next, so a crash between jobs is recovered by re-reading the durable `textbooks.status` rather than replaying from the start. A `failed` textbook re-enqueued via **retry** resumes at the job matching its last durable status, not the beginning.

## 17. Error Handling

Every failure mode has a detection point, a retry policy, a user-facing message, and a recovery path. Retries use exponential backoff with jitter; exhausted retries move the job to the DLQ and set `textbooks.status = failed`.

| Failure | Detection | Retry | User message | Recovery |
| --- | --- | --- | --- | --- |
| **Invalid file** | Client + server validation before job creation | None | "This file couldn't be accepted." | Re-upload a valid file (EH-4). |
| **Unsupported format** | Magic-byte check after object lands | None | "Only PDF, DOCX, PNG, and JPG are supported." | Convert and re-upload (EH-4, CN-2). |
| **Malware / failed scan** | Malware scan after upload | None | "This file failed a security check." | File quarantined; re-upload a clean copy (EH-4). |
| **Corrupt PDF** | OCR Job open/parse error | 1 retry | "We couldn't read this file." | Mark `failed`; user re-uploads. |
| **OCR failure** | Per-page low confidence or engine error | Backoff + jitter, capped | "Processing is taking longer than expected." | Low-confidence pages route to manual review (FR-2.9); whole-doc failure → `failed` (**EH-1**). |
| **Timeout** | Queue job exceeds stage limit | Re-enqueue once | *(none until exhausted)* | On exhaustion → DLQ + `failed`. |
| **Storage failure** | Object read/write error | Backoff + jitter | "Temporary problem processing your file." | Transient errors retry; persistent → `failed`. |
| **Duplicate upload** | Idempotency check on `file_key` / job key | N/A | "This file is already being processed." | Existing job reused; no second row created. |
| **Queue failure** | Enqueue/broker error | Backoff + jitter | *(none)* | Job held and re-enqueued when broker recovers. |
| **AI extraction failure** | Classifier/extraction confidence below floor | Retry once | *(none)* | Low-confidence candidates flagged for manual review (FR-2.9) rather than dropped. |

Failures never delete the source file or already-persisted assets, so a `retry` resumes cleanly from the last durable status.

## 18. Performance Requirements

These are per-textbook targets that size the worker pools and queue timeouts. They are ceilings, not guarantees — a document at the page cap will approach the upper bounds.

| Metric | Target | Notes |
| --- | --- | --- |
| Max upload size | 500 MB | Enforced client-side and server-side (CN-1). |
| Max pages | Per page cap | Rejected before job creation (CN-6). |
| OCR throughput | Pages processed per worker-minute, tracked as a fleet baseline | Slow stage; scaled horizontally. |
| Extraction time | Bounded per document | Parser + classifier stages share this budget. |
| Queue timeout | Per-stage limit | Exceeding it triggers the timeout path (§17). |
| Embedding speed | Batched per model call | Batch size tuned to the embedding provider (§13). |

Stages run as a pipeline across workers, so end-to-end latency is dominated by OCR, not the sum of all stages.

## 19. Security

Ingestion handles untrusted uploads, so every file crosses a validation boundary before any worker touches it, and every stage is tenant-scoped.

- **File validation** — extension, MIME, and magic-byte checks; only CN-2 formats accepted (rejections → EH-4; size and page-cap rejections → EH-5).
- **Malware scanning** — every upload is scanned before OCR; a failed scan quarantines the file (EH-4) and never enqueues a job.
- **Signed URLs** — uploads go direct-to-storage via short-lived signed URLs keyed `{org_id}/textbooks/{uuid}/source.{ext}`; no file passes through the API process.
- **Tenant isolation** — every `textbooks`, `textbook_assets`, `question_bank`, and `question_embeddings` row carries `org_id` and is enforced by PostgreSQL row-level security; workers query under the owning org context.
- **Access control** — upload, review, and reprocess actions pass the dependency chain (`get_current_user` → `get_org_context` → `require_roles`) with deny-by-default; only `coaching_admin` and `super_admin` can approve imported questions.
- **Audit logging** — every state transition and manual review action is appended to `audit_logs` (BR-11), giving a tamper-evident provenance trail.

## 20. Monitoring

Each stage emits metrics so operators can spot a stalled pipeline before users do. Key signals:

- **Upload success rate** — accepted vs rejected uploads (validation health).
- **OCR success rate** — documents fully OCR'd vs routed to `failed`.
- **Average OCR time** — per-document, the primary latency driver.
- **Failed pages** — per-document count of low-confidence pages sent to manual review (FR-2.9).
- **Queue length** — backlog per queue; the horizontal-scaling trigger.
- **Embedding failures** — failed `question_embeddings` writes or provider errors (§13).

Alerts fire on sustained queue growth, a drop in OCR success rate, or a rise in DLQ volume.

## 21. Sequence Diagrams

### 21.1 Upload

```
Teacher ──▶ Next.js ──▶ FastAPI API ──▶ (signed URL) ──▶ Object Storage
                              │
                              └──▶ textbooks row (status = uploaded) ──▶ enqueue OCR Job
```

### 21.2 OCR

```
OCR Worker ──▶ read file_key from Object Storage ──▶ OCR engine
      │                                                   │
      └── on success ──▶ status = ocr ──▶ enqueue Parser Job
      └── low-confidence pages ──▶ manual review queue (FR-2.9)
```

### 21.3 Parsing

```
Parser Worker ──▶ structural parse ──▶ textbook_assets (paragraph/exercise/question/…)
      │
      └──▶ status = parsed ──▶ enqueue Classifier Job
```

### 21.4 Question Extraction & Classification

```
Classifier Worker ──▶ question-kind assets ──▶ classify (type/marks/difficulty/Bloom/…)
      │
      └──▶ classified candidates ──▶ enqueue Embedding Job
```

### 21.5 Embedding

```
Embedding Worker ──▶ candidate stems ──▶ embedding model (batched)
      │
      └──▶ question_embeddings (is_current) ──▶ status = embedded ──▶ enqueue Import Job
```

### 21.6 Approval

```
Import Job ──▶ dedup (§12) ──▶ question_bank rows (status = draft) ──▶ Notification Job ──▶ user
      │
      └── coaching_admin ──▶ review ──▶ status = approved (BR-11 audit) ──▶ retrievable
```

## 22. Future Enhancements

- **Handwritten notebooks** — handwriting-capable OCR to ingest scanned student/teacher notes.
- **EPUB support** — treat reflowable EPUB as a native-text source alongside PDF/DOCX.
- **Formula recognition** — structured extraction of mathematical notation into a usable representation (beyond the `formula` asset kind).
- **Diagram understanding** — semantic interpretation of figures and charts, not just capture as `diagram` assets.
- **Multi-language OCR** — expanded language coverage for regional-language textbooks.

## 23. Related Documents

- [01_Product_Requirements.md](01_Product_Requirements.md) — FR-2.x, CN-x, EH-x, NT-x, BR-x definitions.
- [02_System_Architecture.md](02_System_Architecture.md) — OCR flow, worker scaling, object storage, failure recovery, observability.
- [03_Database_Design.md](03_Database_Design.md) — `textbooks`, `textbook_assets`, `question_bank`, `question_embeddings` schemas.
- [06_AI_Architecture.md](06_AI_Architecture.md) — embedding model selection, retrieval, classification.
- [07_Backend_Architecture.md](07_Backend_Architecture.md) — dependency chain, Celery workers, transaction boundaries.
- [09_Deployment.md](09_Deployment.md) — worker pool sizing and scaling configuration.
- [ADR-014](ADR/ADR-014-math-first-ocr.md) — the math-first OCR decision behind §7.
- [ADR-007](ADR/ADR-007-defer-pgvector-retrieval.md) — why §13 embeddings are deferred.
---

## 24. Module Boundaries

Clear ownership prevents ambiguity during development. This module is responsible for turning raw files into classified, embedded, draft questions. Everything before and after that pipeline belongs to another module.

### This module owns

| Responsibility | Details |
|---------------|---------|
| **Upload validation** | File type, size, page cap enforcement (CN-1, CN-2, CN-6) |
| **Signed URL issuance** | Generate time-limited upload URLs scoped to the tenant's storage namespace |
| **Textbook metadata** | CRUD on the `textbooks` table (status, file_key, pages, timestamps) |
| **OCR / text extraction** | All OCR engine calls, native text extraction, image preprocessing |
| **Structural parsing** | Segment detection (paragraphs, exercises, tables, diagrams, formulae, activities) |
| **Asset management** | CRUD on `textbook_assets` (extracted segments with provenance) |
| **Chapter detection** | Heading resolution, chapter-to-asset linking via `chapter_id` |
| **Question extraction** | Mining exercises and prose for question candidates |
| **Classification** | Assigning qtype, marks, difficulty, Bloom, language, learning outcomes |
| **Embedding trigger** | Computing and storing question embeddings in `question_embeddings` |
| **Deduplication** | Content-hash and vector similarity checks before import |
| **Import into question bank** | Inserting classified questions as `status = draft` in `question_bank` |
| **Reprocessing** | Re-running the pipeline for a textbook without creating duplicates |
| **Progress tracking** | Emitting progress events for real-time UI updates |

### This module does NOT own

| Responsibility | Owning Module |
|---------------|---------------|
| Paper generation | Papers module |
| Question approval (draft → approved) | Questions module / Approvals module |
| Question retrieval for generation | Retrieval module |
| AI question generation | AI module |
| Paper export (PDF/DOCX rendering) | Exports module |
| Billing / quota enforcement | Billing / Usage modules |
| Curriculum hierarchy (boards, classes, subjects) | Curriculum module |
| Template management | Templates module |
| User/org management | Tenancy module |
| Notification delivery | Notifications module |

### Boundary Rule

The Textbook Ingestion module **writes** questions and embeddings. It never **reads** them for generation purposes — that is the Retrieval module's job. This prevents circular dependencies and keeps the ingestion pipeline independent of the online generation path.

---

## 25. Public APIs

Every endpoint passes the dependency chain (`get_current_user` → `get_org_context` → `require_roles`) before reaching the route handler.

### 25.1 Upload Textbook

```
POST /textbooks
```

| Property | Value |
|----------|-------|
| **Description** | Request an upload slot for a new textbook |
| **Permission** | `teacher`, `coaching_admin` |
| **Input** | `{ name: string, subjectId: UUID, fileType: "pdf" \| "docx" \| "png" \| "jpg", fileSize: number, tags?: string[] }` |
| **Output** | `{ textbookId: UUID, uploadUrl: string, expiresAt: ISO8601 }` |
| **Status** | 201 Created |
| **Errors** | 400 (invalid type CN-2), 400 (exceeds size CN-1), 403 (insufficient role), 429 (quota exceeded) |

### 25.2 Confirm Upload

```
POST /textbooks/:id/complete
```

| Property | Value |
|----------|-------|
| **Description** | Confirm the file has been uploaded to object storage; triggers processing |
| **Permission** | `teacher`, `coaching_admin` (must be the uploader) |
| **Input** | `{ etag?: string }` (optional for verification) |
| **Output** | `{ textbookId: UUID, jobId: string, status: "uploaded" }` |
| **Status** | 200 OK |
| **Errors** | 404 (textbook not found), 409 (already processing), 400 (object not found in storage) |

### 25.3 List Textbooks

```
GET /textbooks
```

| Property | Value |
|----------|-------|
| **Description** | List textbooks for the current organization |
| **Permission** | `teacher` (scoped to assigned subjects), `coaching_admin` (all) |
| **Input** | Query params: `page`, `pageSize`, `filter[subjectId]`, `filter[status]`, `sort` |
| **Output** | Paginated list of textbook summaries |
| **Status** | 200 OK |

### 25.4 Get Textbook Details

```
GET /textbooks/:id
```

| Property | Value |
|----------|-------|
| **Description** | Get textbook metadata, processing status, and asset summary |
| **Permission** | `teacher` (if assigned to subject), `coaching_admin` |
| **Output** | `{ id, name, subjectId, status, pages, assetCounts, createdAt, updatedAt }` |
| **Status** | 200 OK |
| **Errors** | 404 (not found or not in scope) |

### 25.5 Get Processing Status

```
GET /textbooks/:id/status
```

| Property | Value |
|----------|-------|
| **Description** | Get detailed processing status with per-stage progress |
| **Permission** | `teacher`, `coaching_admin` |
| **Output** | `{ status, currentStage, progress: { ocr: %, parsing: %, classification: %, embedding: % }, failedPages: [], startedAt, completedAt }` |
| **Status** | 200 OK |

### 25.6 Get Processing Progress (SSE)

```
GET /textbooks/:id/progress
```

| Property | Value |
|----------|-------|
| **Description** | Server-Sent Events stream for real-time processing progress |
| **Permission** | `teacher`, `coaching_admin` |
| **Output** | SSE stream: `{ stage, progress, message, timestamp }` |
| **Connection** | Kept alive until processing completes or client disconnects |

### 25.7 List Extracted Assets

```
GET /textbooks/:id/assets
```

| Property | Value |
|----------|-------|
| **Description** | List extracted assets (paragraphs, exercises, questions, diagrams) |
| **Permission** | `teacher`, `coaching_admin` |
| **Input** | Query params: `filter[kind]`, `filter[chapterId]`, `page`, `pageSize` |
| **Output** | Paginated list of textbook assets with metadata |
| **Status** | 200 OK |

### 25.8 Reprocess Textbook

```
POST /textbooks/:id/reprocess
```

| Property | Value |
|----------|-------|
| **Description** | Re-run the ingestion pipeline (e.g., after engine upgrade) |
| **Permission** | `coaching_admin` |
| **Input** | `{ fromStage?: "ocr" \| "parsing" \| "classification" \| "embedding" }` |
| **Output** | `{ jobId: string }` |
| **Status** | 202 Accepted |
| **Errors** | 409 (already processing), 404 (not found) |

### 25.9 Delete Textbook

```
DELETE /textbooks/:id
```

| Property | Value |
|----------|-------|
| **Description** | Soft-delete textbook and its assets (questions remain in bank) |
| **Permission** | `coaching_admin` |
| **Status** | 204 No Content |
| **Errors** | 404 (not found), 409 (currently processing) |

---

## 26. Event Contracts

Every event emitted by this module is documented here with its payload, publisher, and expected consumers.

### 26.1 textbook.uploaded

| Property | Value |
|----------|-------|
| **Publisher** | Textbook Module (on upload confirmation) |
| **Payload** | `{ textbookId: UUID, orgId: UUID, subjectId: UUID, uploadedBy: UUID, fileType: string, fileSize: number, timestamp: ISO8601 }` |
| **Consumers** | OCR Worker (triggers processing) |

### 26.2 textbook.ocr_completed

| Property | Value |
|----------|-------|
| **Publisher** | OCR Worker |
| **Payload** | `{ textbookId: UUID, orgId: UUID, totalPages: number, ocrPages: number, nativePages: number, failedPages: number[], avgConfidence: number, timestamp: ISO8601 }` |
| **Consumers** | Parser Worker (triggers structural parsing), Progress Tracker |

### 26.3 textbook.parsed

| Property | Value |
|----------|-------|
| **Publisher** | Parser Worker |
| **Payload** | `{ textbookId: UUID, orgId: UUID, assetCounts: { paragraph: n, exercise: n, question: n, diagram: n, table: n, activity: n, formula: n }, chaptersDetected: number, timestamp: ISO8601 }` |
| **Consumers** | Classifier Worker, Notifications (NT-1 partial) |

### 26.4 textbook.classified

| Property | Value |
|----------|-------|
| **Publisher** | Classifier Worker |
| **Payload** | `{ textbookId: UUID, orgId: UUID, questionsClassified: number, lowConfidenceCount: number, timestamp: ISO8601 }` |
| **Consumers** | Embedding Worker |

### 26.5 textbook.embedded

| Property | Value |
|----------|-------|
| **Publisher** | Embedding Worker |
| **Payload** | `{ textbookId: UUID, orgId: UUID, questionsEmbedded: number, model: string, dimensions: number, timestamp: ISO8601 }` |
| **Consumers** | Import Worker |

### 26.6 textbook.import_completed

| Property | Value |
|----------|-------|
| **Publisher** | Import Worker |
| **Payload** | `{ textbookId: UUID, orgId: UUID, questionsImported: number, duplicatesSkipped: number, nearDuplicatesFlagged: number, source: "textbook" \| "intext", timestamp: ISO8601 }` |
| **Consumers** | Notification Worker (NT-1), Analytics (daily counters), Audit |

### 26.7 textbook.failed

| Property | Value |
|----------|-------|
| **Publisher** | Any worker stage on exhausted retries |
| **Payload** | `{ textbookId: UUID, orgId: UUID, failedStage: string, errorCode: string, errorMessage: string, retriesExhausted: boolean, timestamp: ISO8601 }` |
| **Consumers** | Notification Worker (NT-2), Audit |

### 26.8 textbook.reprocess_requested

| Property | Value |
|----------|-------|
| **Publisher** | Textbook Module (on reprocess API call) |
| **Payload** | `{ textbookId: UUID, orgId: UUID, fromStage: string, requestedBy: UUID, timestamp: ISO8601 }` |
| **Consumers** | Appropriate Worker (based on fromStage), Audit |

---

## 27. Configuration

All ingestion behavior is controlled by configurable values. No magic numbers are hardcoded.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `TEXTBOOK_MAX_UPLOAD_SIZE_MB` | 500 | Maximum upload size in megabytes (CN-1) |
| `TEXTBOOK_MAX_PAGES` | 1000 | Maximum page count per textbook (CN-6) |
| `TEXTBOOK_ALLOWED_TYPES` | pdf,docx,png,jpg | Accepted file types (CN-2) |
| `OCR_PROVIDER` | mathpix | Primary OCR engine (`mathpix`, `gemini_vision`, `tesseract`) — math-first by default (§7, ADR-014) |
| `OCR_FALLBACK_PROVIDER` | gemini_vision | Engine used when the primary fails or scores a page poorly |
| `OCR_PLAINTEXT_PROVIDER` | tesseract | Engine for prose/table pages with no mathematical notation |
| `OCR_TIMEOUT_SECONDS` | 30 | Per-page OCR timeout |
| `OCR_JOB_TIMEOUT_SECONDS` | 600 | Whole-document OCR job timeout |
| `OCR_CONFIDENCE_THRESHOLD` | 0.70 | Minimum per-block OCR confidence before flagging |
| `OCR_PAGE_CONFIDENCE_THRESHOLD` | 0.60 | Minimum mean page confidence before routing to review |
| `OCR_MAX_RETRIES` | 3 | Maximum retries for OCR failures |
| `OCR_DPI` | 300 | Target DPI for page rasterization |
| `PARSER_TIMEOUT_SECONDS` | 120 | Structural parsing timeout per textbook |
| `CLASSIFIER_TIMEOUT_SECONDS` | 120 | Classification timeout per textbook |
| `CLASSIFIER_CONFIDENCE_THRESHOLD` | 0.65 | Minimum classification confidence before flagging for review |
| `EMBEDDING_PROVIDER` | gemini | Embedding provider. **Inert until pgvector is adopted (ADR-007).** |
| `EMBEDDING_MODEL` | *(Gemini embedding model ID)* | Embedding model — set in config, never hardcoded |
| `EMBEDDING_DIMENSIONS` | 1536 | Stored vector width — Matryoshka-truncated from Gemini's native 3072 so pgvector's 2000-dim HNSW index limit is respected (`03_Database_Design.md` §5) |
| `EMBEDDING_BATCH_SIZE` | 2048 | Maximum texts per embedding API call |
| `EMBEDDING_MAX_RETRIES` | 3 | Maximum retries for embedding failures |
| `EMBEDDING_TIMEOUT_SECONDS` | 300 | Batch embedding timeout |
| `DEDUP_SIMILARITY_THRESHOLD` | 0.95 | Similarity threshold for near-duplicate detection — trigram at MVP, embedding cosine once vectors exist |
| `DEDUP_REVIEW_THRESHOLD` | 0.85 | Cosine similarity threshold for routing to manual review |
| `IMPORT_BATCH_SIZE` | 100 | Questions imported per transaction batch |
| `QUEUE_NAME_OCR` | ingestion:ocr | Redis queue name for OCR jobs |
| `QUEUE_NAME_PARSER` | ingestion:parser | Redis queue name for parser jobs |
| `QUEUE_NAME_CLASSIFIER` | ingestion:classifier | Redis queue name for classifier jobs |
| `QUEUE_NAME_EMBEDDING` | ingestion:embedding | Redis queue name for embedding jobs |
| `QUEUE_NAME_IMPORT` | ingestion:import | Redis queue name for import jobs |
| `SIGNED_URL_UPLOAD_TTL_SECONDS` | 900 | Upload signed URL expiry (15 min) |
| `SIGNED_URL_DOWNLOAD_TTL_SECONDS` | 3600 | Download signed URL expiry (1 hour) |
| `MALWARE_SCAN_ENABLED` | true | Enable/disable virus scanning on uploads |
| `CLEANUP_ORPHAN_HOURS` | 24 | Hours before orphaned uploads are reaped |

All parameters are loaded from environment variables and validated at startup by the Pydantic `Settings` model in `packages/config`; the application refuses to start on missing or invalid configuration.

---

## 28. Retry Strategy

Every stage has explicit retry parameters. Retries use exponential backoff with jitter to avoid thundering herds.

### 28.1 Retry Parameters by Stage

| Stage | Max Retries | Initial Backoff | Max Backoff | Jitter |
|-------|-------------|-----------------|-------------|--------|
| **OCR** | 3 | 2s | 32s | +/- 25% |
| **Parser** | 2 | 1s | 8s | +/- 25% |
| **Classifier** | 2 | 1s | 8s | +/- 25% |
| **Embedding** | 3 | 2s | 32s | +/- 25% |
| **Import** | 2 | 1s | 8s | +/- 25% |
| **Storage (read/write)** | 5 | 500ms | 16s | +/- 25% |
| **Notification** | 3 | 1s | 16s | +/- 25% |

### 28.2 Backoff Schedule Example (OCR)

| Attempt | Delay (base) | With Jitter (example) |
|---------|-------------|----------------------|
| 1 | 2s | 1.5s - 2.5s |
| 2 | 4s | 3.0s - 5.0s |
| 3 | 8s | 6.0s - 10.0s |
| Exhausted | → DLQ + `failed` | |

### 28.3 Retry Behavior

- **Transient errors** (timeouts, 5xx, network blips): retry with backoff.
- **Permanent errors** (invalid file, unsupported format, malware): no retry, immediate failure.
- **Partial success** (some pages OCR'd, some failed): continue with successful pages, flag failures for review.
- **Exhausted retries**: move job to Dead Letter Queue, set `textbooks.status = failed`, notify user.

---

## 29. Transaction Boundaries

Each pipeline stage commits its work in small, focused transactions. No stage wraps the entire pipeline in a single transaction.

### 29.1 Transaction Map

```
Transaction 1: Upload Registration
  INSERT textbooks (status = uploaded)
  INSERT audit_logs (textbook.uploaded)
  INSERT usage (upload counted)
  COMMIT
  → enqueue OCR job (after commit)

─────────────────────────────────────

Transaction 2: OCR Results (per page batch)
  INSERT textbook_assets (raw text blocks, per batch of pages)
  COMMIT
  (repeated per batch)

Transaction 3: OCR Completion
  UPDATE textbooks SET status = 'ocr', pages = N
  INSERT audit_logs (ocr.completed)
  COMMIT
  → enqueue Parser job (after commit)

─────────────────────────────────────

Transaction 4: Structural Parse
  INSERT textbook_assets (paragraph, exercise, table, diagram, ...)
  UPDATE textbooks SET status = 'parsed'
  INSERT audit_logs (parsing.completed)
  COMMIT
  → enqueue Classifier job (after commit)

─────────────────────────────────────

Transaction 5: Classification
  UPDATE textbook_assets (classification attributes on question candidates)
  COMMIT
  → enqueue Embedding job (after commit)

─────────────────────────────────────

Transaction 6: Embedding Storage
  INSERT question_embeddings (batched)
  UPDATE textbooks SET status = 'embedded'
  INSERT audit_logs (embedding.completed)
  COMMIT
  → enqueue Import job (after commit)

─────────────────────────────────────

Transaction 7: Question Import (per batch)
  INSERT question_bank (batch of questions, status = draft)
  COMMIT
  (repeated per batch; a bad row fails its batch, not the whole import)

Transaction 8: Import Completion
  INSERT usage (questions imported, embeddings generated)
  INSERT audit_logs (import.completed)
  COMMIT
  → enqueue Notification job (after commit)
```

### 29.2 Transaction Rules

| Rule | Rationale |
|------|-----------|
| No LLM/network calls inside a transaction | External calls may timeout, holding locks |
| Enqueue after commit | Jobs must never reference data that a rollback erased |
| Batch inserts per transaction | Large imports use batches (default 100) to limit lock duration |
| Stages commit independently | A crash between stages resumes from the last committed status |

---

## 30. Idempotency Strategy

Every job is designed to be safely re-executed without creating duplicates or corrupting state.

| Job | Idempotency Key | Strategy |
|-----|----------------|----------|
| **OCR Job** | `textbook_id` | If `status >= ocr`, skip OCR. Assets are upserted on `(textbook_id, page, block_index)`. |
| **Parser Job** | `textbook_id` | If `status >= parsed`, skip parsing. Assets upserted on `(textbook_id, kind, page, position)`. |
| **Classifier Job** | `textbook_id` | Classification is deterministic for the same input; re-running overwrites the same attributes. |
| **Embedding Job** | `question_id + model` | Embedding upserted on `(question_id, model, checksum)`. If checksum matches, no re-embed. |
| **Import Job** | `textbook_id + content_hash` | `content_hash` uniqueness prevents duplicate question_bank rows. |
| **Notification Job** | `textbook_id + event_type` | Dedup key prevents double-sending the same notification. |
| **Reprocess** | `textbook_id + parser_version` | New version creates new assets alongside old ones; dedup prevents duplicate questions. |

### Replay Safety

A textbook re-enqueued via retry or manual replay:
1. Reads the current `textbooks.status` to determine the resume point.
2. Skips stages that have already committed (status is at or past their checkpoint).
3. Re-runs only the failed or incomplete stage.
4. Assets from a previous partial run are retained and reused (no orphan cleanup needed).

---

## 31. Data Retention

| Data Category | Retention | Rationale |
|--------------|-----------|-----------|
| **Original uploads** (source files) | 5 years | Legal/audit compliance; enables reprocessing |
| **Extracted page images** | 1 year | Can be regenerated from source; space optimization |
| **OCR output** (raw text blocks) | Lifetime of textbook | Foundation for all downstream assets |
| **Textbook assets** | Lifetime of textbook | Structural parse results needed for provenance |
| **Imported questions** | Indefinite | Core platform data; never auto-deleted |
| **Question embeddings** | Indefinite (soft-delete on model migration) | Current embeddings active; old versions soft-deleted |
| **Processing logs** | 180 days | Operational troubleshooting |
| **Temporary artifacts** (intermediate OCR, preprocessed images) | 24 hours | Cleaned up by the Cleanup Worker |
| **Failed job records** (DLQ) | 30 days | Operator inspection and replay window |
| **Audit logs** | 7 years | Compliance and audit trail (BR-11) |

### Lifecycle Policies

- Object storage lifecycle rules automatically expire temporary artifacts after 24 hours.
- Cleanup Worker reaps orphaned uploads (signed URL issued but upload never confirmed) after `CLEANUP_ORPHAN_HOURS`.
- Soft-deleted textbooks and their assets are permanently purged after 90 days by a scheduled cleanup job.

---

## 32. Versioning Strategy

Processing engine versions are tracked so reprocessing can be triggered selectively when an engine improves.

### 32.1 Version Registry

| Component | Version Field | Current | Storage |
|-----------|--------------|---------|---------|
| **OCR Engine** | `ocr_version` | Tracked in job metadata | Stored in `textbook_assets.data` JSONB |
| **Parser** | `parser_version` | Tracked in job metadata | Stored in `textbook_assets.data` JSONB |
| **Classifier** | `classifier_version` | Tracked in job metadata | Stored in `textbook_assets.data` JSONB |
| **Embedding Model** | `model` column | *(Gemini embedding model, from config)* | Stored in `question_embeddings.model` |
| **Prompt Version** | `prompt_version_id` | Active version | Stored in `prompt_versions` table |

### 32.2 Reprocessing Triggers

Reprocessing occurs when any of the following change:

| Trigger | Scope | Effect |
|---------|-------|--------|
| OCR engine upgraded | Full pipeline re-run | Re-OCR → re-parse → re-classify → re-embed → re-import |
| Parser upgraded | From parsing onward | Re-parse → re-classify → re-embed → re-import |
| Classifier upgraded | From classification onward | Re-classify → re-embed → re-import |
| Embedding model changed | Embedding only | Re-embed all questions (background migration job) |
| Manual reprocess request | From specified stage | Teacher or admin triggers via `POST /textbooks/:id/reprocess` |

### 32.3 Reprocessing Safety

- Deduplication (§12) prevents duplicate questions on reprocess.
- New assets are created alongside old ones; the `is_current` flag distinguishes active versions.
- Previous embeddings are preserved with `is_current = false` for rollback capability.

---

## 33. Storage Layout

Object storage is organized by tenant and entity for DevOps clarity and lifecycle policy targeting.

```
bucket (private, no public access)
│
├── org/{orgId}/
│   └── textbooks/
│       └── {textbookId}/
│           ├── raw/
│           │   └── source.pdf              # Original uploaded file
│           │
│           ├── pages/
│           │   ├── page_001.png            # Rasterized page image (for OCR)
│           │   ├── page_002.png
│           │   └── ...
│           │
│           ├── ocr/
│           │   ├── page_001.json           # Per-page OCR output (text + confidence)
│           │   ├── page_002.json
│           │   └── ...
│           │
│           ├── assets/
│           │   ├── diagrams/
│           │   │   ├── {assetId}.png       # Extracted diagram images
│           │   │   └── ...
│           │   └── tables/
│           │       ├── {assetId}.json      # Extracted table data
│           │       └── ...
│           │
│           └── metadata/
│               └── processing.json         # Processing metadata and version info
```

### Storage Rules

| Rule | Implementation |
|------|---------------|
| Every object namespaced by `orgId` | Path scoping mirrors database row scoping |
| Bucket is private | No public objects; all access via signed URLs |
| Raw uploads immutable | Source files are never modified after upload |
| Lifecycle policies per prefix | `pages/` expires after 1 year; `ocr/` retained with textbook |
| Signed URL scoping | URLs are generated with path-specific permissions |

---

## 34. Resource Usage

Understanding resource intensity per stage enables proper infrastructure sizing and autoscaling.

| Stage | CPU | Memory | Storage | Network | GPU |
|-------|-----|--------|---------|---------|-----|
| **Upload** | Low | Low | High (raw file) | High (upload bandwidth) | None |
| **OCR** | **Very High** | High (page rendering) | Medium (page images) | Medium (OCR API calls) | Optional |
| **Parsing** | Medium | Medium | Low | Low | None |
| **Classification** | Medium | Low | Low | Medium (LLM calls) | None |
| **Embedding** | Low | Medium (batch vectors) | Low | **High** (embedding API) | None |
| **Import** | Low | Low | Low | Low (DB writes) | None |

### Bottleneck Analysis

| Bottleneck | Stage | Mitigation |
|-----------|-------|------------|
| CPU saturation | OCR | Horizontal scaling of OCR workers; concurrency cap per instance |
| Memory pressure | OCR (large pages), Embedding (batch vectors) | Per-worker concurrency limits; batch size tuning |
| Network bandwidth | Upload, Embedding | Direct-to-storage uploads; batched embedding calls |
| API rate limits | Embedding, Classification (LLM) | Queue-based throttling; provider rate limit awareness |

---

## 35. Scalability Strategy

Workers scale independently per queue. Autoscaling is driven by queue depth and job age.

### 35.1 Worker Scaling

| Worker | Baseline Replicas | Max Replicas | Scale-Up Trigger | Scale-Down Trigger |
|--------|------------------|-------------|-----------------|-------------------|
| **OCR Worker** | 2 | 10 | Queue depth > 5 or oldest job > 2 min | Queue empty for 5 min |
| **Parser Worker** | 2 | 5 | Queue depth > 10 | Queue empty for 5 min |
| **Classifier Worker** | 2 | 5 | Queue depth > 10 | Queue empty for 5 min |
| **Embedding Worker** | 2 | 8 | Queue depth > 5 | Queue empty for 5 min |
| **Import Worker** | 1 | 3 | Queue depth > 20 | Queue empty for 10 min |

### 35.2 Per-Worker Concurrency

| Worker | Max Concurrent Jobs | Rationale |
|--------|-------------------|-----------|
| OCR | 2 | CPU/memory bound; large page rendering |
| Parser | 5 | Moderate CPU; fast per document |
| Classifier | 3 | LLM rate limits |
| Embedding | 5 | IO-bound (API calls); batched |
| Import | 3 | DB write throughput |

### 35.3 Scaling Architecture

```
Redis Queue (per stage)
  |
  +--> OCR Workers (2-10 replicas, 2 concurrent each)
  |      ↓
  +--> Parser Workers (2-5 replicas, 5 concurrent each)
  |      ↓
  +--> Classifier Workers (2-5 replicas, 3 concurrent each)
  |      ↓
  +--> Embedding Workers (2-8 replicas, 5 concurrent each)
  |      ↓
  +--> Import Workers (1-3 replicas, 3 concurrent each)
```

---

## 36. Security Threat Model

Ingestion handles untrusted file uploads from external users. Every threat has an explicit mitigation.

| Threat | Attack Vector | Impact | Mitigation |
|--------|--------------|--------|-----------|
| **Zip bomb / decompression bomb** | Malicious PDF with extreme compression ratio | Memory exhaustion, worker crash | Maximum decompressed size check; page count cap (CN-6) |
| **Malware PDF** | Embedded executable or exploit in PDF | Compromise of worker infrastructure | Virus scan before OCR; quarantine + reject infected files (EH-4) |
| **Path traversal** | Crafted filename with `../` or absolute paths | File overwrite or data leakage | Random UUID object keys; filenames never used as storage paths |
| **Server-Side Request Forgery (SSRF)** | Malicious URL in PDF metadata or links | Internal network probing | OCR workers have no outbound access except OCR provider and storage |
| **Unauthorized access** | Direct object storage URL guessing | Cross-tenant data access | Signed URLs with expiry; private bucket; no public objects |
| **Cross-tenant leakage** | Worker processes another org's textbook | Data breach | `org_id` in every query; RLS enforced at DB level; worker loads org context from job payload |
| **Resource exhaustion (DoS)** | Uploading many large files simultaneously | Worker starvation, queue saturation | Per-org upload rate limiting; quota enforcement; queue depth alerts |
| **Prompt injection via content** | Malicious text in textbook designed to manipulate AI | Incorrect classification or extraction | Input sanitization; classification prompts use structured context, not raw text concatenation |
| **Data exfiltration** | Extracting embedded sensitive data from textbooks | Privacy violation | Textbook content stays within tenant scope; no cross-tenant retrieval |

---

## 37. Disaster Recovery

Recovery procedures for each failure mode, ensuring no data loss and minimal downtime.

### 37.1 Worker Crash

```
OCR/Parser/Embedding worker crashes
  ↓
In-flight job is NOT acknowledged
  ↓
Job returns to Redis queue automatically
  ↓
Another worker instance picks it up
  ↓
Job resumes from last committed checkpoint
  (idempotent - no duplicate data)
```

### 37.2 Object Storage Unavailable

```
Object storage becomes unreachable
  ↓
Upload requests: API returns 503 with retry guidance
  ↓
Processing jobs: retry with backoff (up to 5 attempts)
  ↓
Still unavailable: jobs pause in queue (not failed)
  ↓
Storage recovers: jobs resume automatically
  ↓
No data loss (source files already stored)
```

### 37.3 Database Unavailable

```
PostgreSQL connection lost
  ↓
New uploads: API returns 503
  ↓
Processing jobs: retry with backoff
  ↓
Connection pool recovers: automatic resume
  ↓
Prolonged outage: jobs accumulate in queue
  ↓
Database recovers: backlog processes in order
  ↓
No data loss (queue persists in Redis)
```

### 37.4 Redis Queue Unavailable

```
Redis becomes unreachable
  ↓
New uploads: API accepts but cannot enqueue
  ↓
Fallback: record pending job in DB (textbooks.status = uploaded)
  ↓
Redis recovers: sweeper re-enqueues any textbooks stuck in uploaded
  ↓
Processing continues from recorded status
```

### 37.5 OCR Provider Outage

```
Primary OCR provider (Mathpix) unavailable
  ↓
OCR jobs retry with backoff
  ↓
Retries exhausted: status = failed
  ↓
Notify user with guidance (NT-2)
  ↓
Provider recovers: user clicks reprocess or operator replays DLQ
  ↓
Pipeline resumes from OCR stage
```

---

## 38. Assumptions

These assumptions underpin the architecture. If any assumption is invalidated, the affected design must be revisited.

| Assumption | Impact if Invalid |
|-----------|------------------|
| OCR provider is available with >99.9% uptime | OCR becomes the single point of failure; add fallback provider |
| Redis is available for queue and cache | All async processing stops; need persistent queue fallback |
| PostgreSQL is available with connection pooling | All reads and writes stop; need read replica failover |
| Upload bandwidth is sufficient for 500MB files | Large uploads timeout; need resumable upload support |
| Uploaded files are educational material (textbooks, workbooks) | Extraction heuristics produce garbage on non-educational content |
| Textbooks follow standard formatting conventions | Chapter detection and exercise detection accuracy degrades on unconventional layouts |
| Teachers upload one textbook at a time per subject | Concurrent uploads of the same subject may create conflicting chapter mappings |
| Embedding provider supports batch requests | Without batching, embedding latency increases linearly with question count |
| Network latency to object storage is low (<50ms) | Page-by-page reads from storage become a bottleneck; need pre-fetching |

---

## 39. Limitations

Known limitations that users and operators should be aware of.

| Limitation | Impact | Workaround |
|-----------|--------|-----------|
| **Handwritten notes unsupported** | Handwritten content is not OCR'd accurately | Type or digitize handwritten content before upload |
| **Formula OCR quality varies by source** | Mathpix handles printed mathematical notation well; unusual or handwritten notation still degrades | Confidence flags route low-scoring pages to manual review; a human correction UI covers the residual |
| **Tables may require manual review** | Complex multi-level tables may confuse the structural parser | Flag complex tables for human review |
| **Low-quality scans reduce accuracy** | Blurry, skewed, or low-DPI scans produce low-confidence OCR | Use high-quality scans (300+ DPI); preprocessing helps but cannot fully compensate |
| **Multi-column layouts may confuse parser** | Newspaper-style column layouts may result in jumbled text order | Single-column layouts are strongly preferred |
| **Image-only PDFs require full OCR** | No native text layer means full OCR cost and latency | Use digitally-created PDFs when possible |
| **Diagrams are captured, not understood** | Diagram images are stored but their content is not semantically analyzed | Diagram understanding is a future enhancement (§22) |
| **Maximum 1000 pages per textbook** | Very large textbooks must be split | Upload as separate volumes per chapter range |
| **Single language per textbook** | Mixed-language textbooks may produce inconsistent classification | Upload separate files per language section |
| **No real-time processing** | Ingestion is async; results are not available immediately | Progress tracking via SSE; notifications on completion |

---

## 40. Design Decisions

Key architectural decisions and their rationale. These save future developers from re-litigating choices.

| # | Decision | Alternatives Considered | Rationale |
|---|---------|----------------------|-----------|
| 1 | **Direct-to-storage upload** (signed URLs) | Upload through API | A 500MB file would block an API thread for minutes; signed URLs keep the API stateless and fast |
| 2 | **Celery on Redis** | In-process async, RabbitMQ, SQS, arq | Redis is already deployed for cache; Celery gives task retry, DLQ, priority, chords for multi-step chains, and mature monitoring. `arq` is lighter and natively async but has a far smaller production track record (ADR-006). |
| 3 | **Staged pipeline** (OCR → parse → classify → embed → import) | Single monolithic job | Stages are independently retryable, scalable, and observable; a failure in classification doesn't waste OCR work |
| 4 | **No vectors at MVP; PostgreSQL/pgvector when adopted** | Pinecone, Weaviate, Qdrant, or pgvector from day one | After the hard metadata filter the candidate set is small enough that lexical ranking matches ANN (ADR-007). When a measured paraphrase-recall gap appears, pgvector wins on transactional consistency with questions and on having no second datastore to operate. |
| 5 | **Content-hash deduplication** | Title matching, fuzzy string matching | Deterministic, collision-resistant, fast; combined with vector similarity for near-duplicates |
| 6 | **Questions import as draft** (never auto-approve) | Auto-approve high-confidence extractions | Preserves human-in-the-loop principle; AI is a tool, not a decision-maker (BR-8, BR-11) |
| 7 | **Per-page OCR with native text detection** | OCR everything, or never OCR | Native text is faster and more accurate; OCR only where needed saves cost and time |
| 8 | **Idempotent jobs** keyed on `textbook_id` | Non-idempotent with dedup in DB | Replay safety is critical for a pipeline with retries; idempotency at the job level is simpler than at the DB level |
| 9 | **Versioned embeddings** with `is_current` flag | Replace in place, or separate history table | Version chain supports rollback; `is_current` filter is cheap; history aids debugging |
| 10 | **Separate worker pools per stage** | One worker pool for all stages | OCR is CPU-heavy, embedding is IO-heavy — different scaling profiles require different pools |

---

## 41. Testing Strategy

### 41.1 Unit Tests

| Component | What is Tested | Mocked Dependencies |
|-----------|---------------|-------------------|
| **Parser** | Segment detection (heading, paragraph, exercise, table, formula) | None (pure text analysis) |
| **Classifier** | Question type detection, marks parsing, Bloom verb analysis | LLM provider (for AI-assisted classification) |
| **Deduplicator** | Content hash computation, similarity threshold comparison | Question repository |
| **Embedding service** | Batch chunking, checksum computation, version chain management | Embedding provider |
| **State machine** | Status transitions, invalid transition rejection | Database |

### 41.2 Integration Tests

| Flow | What is Tested | Real Dependencies |
|------|---------------|-------------------|
| **Upload flow** | Signed URL generation, metadata persistence, job enqueue | Object Storage, PostgreSQL, Redis |
| **OCR pipeline** | Native text detection, OCR fallback, confidence scoring | OCR provider (test instance), PostgreSQL |
| **Parse + classify** | End-to-end extraction from sample textbook pages | PostgreSQL |
| **Embedding pipeline** | Batch embedding, version chain, HNSW index query | Embedding provider (test key), PostgreSQL + pgvector |
| **Deduplication** | Exact + near-duplicate detection across textbooks | PostgreSQL + pgvector |

### 41.3 End-to-End Tests

| Scenario | Steps | Assertions |
|---------|-------|-----------|
| **Happy path** | Upload PDF → OCR → parse → classify → embed → import | Textbook status = embedded; questions in bank with correct provenance |
| **Reprocess** | Upload → complete → reprocess | No duplicate questions; new assets created; old assets preserved |
| **Failure + retry** | Upload → OCR fails → retry → succeeds | Status transitions correctly; partial assets retained; no data loss |
| **Duplicate detection** | Upload same textbook twice | Second import produces zero new questions; provenance enriched |
| **Manual review** | Upload low-quality scan → low confidence | Questions flagged for review; not auto-imported |

### 41.4 Performance Tests

| Test | Metric | Target |
|------|--------|--------|
| OCR throughput | Pages per minute per worker | Baseline established and monitored |
| Embedding throughput | Questions per minute per worker | Baseline established |
| Import throughput | Questions per second (batch insert) | > 100/s |
| Queue processing | End-to-end pipeline time for a 100-page textbook | < 10 min |

---

## 42. Operational Runbook

### 42.1 OCR Queue Stuck

```
Symptom: OCR queue depth growing, no jobs completing
  ↓
1. Check OCR worker logs for errors
  ↓
2. Check the OCR provider's status page (Mathpix, or the configured primary)
  ↓
3. If provider down:
   - Wait for recovery (jobs remain queued)
   - OR switch to fallback provider (config change)
  ↓
4. If worker crash:
   - Restart OCR workers
   - Jobs auto-resume from queue
  ↓
5. If persistent failures:
   - Check DLQ for error patterns
   - Fix root cause (e.g., corrupt file)
   - Replay DLQ entries
```

### 42.2 Embedding Queue Stuck

```
Symptom: Embedding queue depth growing
  ↓
1. Check embedding provider rate limits
  ↓
2. If rate limited:
   - Reduce EMBEDDING_BATCH_SIZE
   - Add delay between batches
   - Scale down concurrent workers
  ↓
3. If provider error:
   - Check API key validity
   - Check provider status page
   - Wait for recovery or switch provider
```

### 42.3 Textbook Stuck in Processing

```
Symptom: Textbook status has not advanced for > 30 min
  ↓
1. Check textbooks.status to identify stuck stage
  ↓
2. Check the corresponding queue for the job
  ↓
3. If job missing from queue:
   - Re-enqueue from the stuck stage
   - POST /textbooks/:id/reprocess?fromStage=...
  ↓
4. If job in DLQ:
   - Inspect error details
   - Fix root cause
   - Replay the DLQ entry
```

### 42.4 High DLQ Volume

```
Symptom: DLQ contains > 10 messages
  ↓
1. Categorize DLQ entries by error type
  ↓
2. If consistent error (e.g., same OCR provider error):
   - Investigate provider issue
   - Fix and replay all DLQ entries in batch
  ↓
3. If varied errors:
   - Triage individually
   - File-specific issues: mark textbook as failed, notify user
   - Infrastructure issues: resolve and replay
```

### 42.5 Storage Full / Approaching Limits

```
Symptom: Storage usage alert firing
  ↓
1. Run orphan cleanup (Cleanup Worker)
  ↓
2. Verify lifecycle policies are executing
  ↓
3. Check for abnormally large textbooks
  ↓
4. If lifecycle policies not running:
   - Re-apply policies
   - Manual cleanup of expired artifacts
  ↓
5. If legitimate growth:
   - Expand storage capacity
   - Review retention policies
```
