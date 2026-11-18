# ADR-003: Keep questions as the core domain entity

- **Status:** Accepted
- **Date:** 2026-08-13
- **Affects:** `03_Database_Design.md`, `06_AI_Architecture.md`, `01_Product_Requirements.md`

## Context

The product's visible artifact is a question paper, so the obvious modelling instinct is to make the
paper the unit of record: a paper owns its questions, and generating a paper creates them.

That instinct is wrong here, and expensively so. The platform's actual asset — the thing that
compounds in value, that a coaching centre would refuse to lose, and that makes the retrieval-first
thesis work at all — is the **question bank**. A question authored once should be findable, reusable,
version-tracked, deduplicated, and attributable across every paper that ever used it. If questions live
inside papers, none of that is possible: the same question is re-entered per paper, duplicates
multiply, provenance fragments, and the corpus never accumulates.

## Decision

**`question_bank` is the core entity. Papers are projections of selected questions.**

Concretely:

- A single unified `question_bank` table with a `source` discriminator
  (`institute_bank` / `textbook` / `intext` / `previous_year` / `ai_generated`) rather than five
  near-identical tables — so retrieval is one query path regardless of origin.
- Version lineage lives *inside* the table: `root_question_id`, `previous_version_id`,
  `version_number`, `is_current`, with a partial unique index enforcing exactly one current version per
  root (BR-8). Approved questions are immutable; an edit forks a new version.
- `paper_versions.content` holds ordered **references** to questions plus an inlined snapshot, so a
  paper remains reproducible even after its questions are versioned onward.
- Every question carries provenance: `source`, and for AI-generated items the `prompt_version_id` that
  produced it (BR-4, AI-8).
- Deduplication is a first-class property of the bank (`content_hash` for exact, trigram or embedding
  similarity for near), not a per-paper concern.

## Consequences

**Positive.** The corpus is the moat: it grows across papers, tenants (for global-scope content), and
time. Retrieval-first generation becomes possible, which is what keeps AI cost bounded and quality high
(ADR-005). Analytics such as question reuse, coverage gaps, and per-question quality are queries
against one table rather than aggregations across paper blobs.

**Negative / accepted.** Papers become indirection: rendering one requires resolving references. The
inlined snapshot in `paper_versions.content` mitigates this and guarantees a past paper renders
identically even if the underlying question has moved on — at the cost of some duplication between the
snapshot and the bank. That duplication is deliberate: reproducibility beats normalisation here.

Question lineage adds real complexity to every write path. The rule "approved questions are immutable,
edits fork" must be enforced in one place (the repository) or it will be violated somewhere.

## Alternatives considered

- **Papers own their questions.** Simpler to write, catastrophic to live with: no reuse, no dedup, no
  cross-paper analytics, and a corpus that never accumulates. Rejected outright.
- **Five separate source tables** (`textbook_questions`, `intext_questions`, …). Matches the domain
  vocabulary and was the shape implied by the original prompt. Rejected because retrieval would have to
  union five tables on every slot, and every new source would mean a new table plus a new branch in the
  retrieval path. The domain vocabulary is preserved as **views** over the unified table instead, which
  costs nothing.
- **Immutable questions with no versioning** (edit = new independent row). Simpler, but it destroys the
  lineage that makes "why does this old paper differ?" answerable, and it silently multiplies
  near-duplicates.
