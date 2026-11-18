# ADR-014: Math-first OCR — Mathpix primary, behind a `DocumentExtractor` interface

- **Status:** Accepted — resolves conflict #3
- **Date:** 2026-08-13
- **Affects:** `05_Textbook_Ingestion.md`, `06_AI_Architecture.md`, `09_Deployment.md`, Blueprint §7/§12

## Context

`05_Textbook_Ingestion.md` defaulted to Cloud Vision or Tesseract, with formula recognition listed as a
**future enhancement** (§22). The Blueprint specified Mathpix or a vision LLM producing LaTeX as the
**primary** path, arguing that forms-oriented OCR mangles mathematics.

The stakes are higher than an engine preference. The target market is STEM coaching — physics,
chemistry, and mathematics textbooks that are dense with equations, subscripts, fractions, integrals,
and labelled diagrams. General-purpose OCR was built for documents and forms; on an equation it
produces plausible-looking character soup. A question stem reading `x2 + 5x — 6 = O` is not a
formatting problem. It is unusable content: it cannot be retrieved meaningfully, cannot be rendered,
and cannot be verified.

That makes corpus quality the gate on everything downstream. Retrieval-first generation (ADR-005) is
only as good as the bank; execute-and-verify (ADR-010) cannot run on a garbled expression. A cheaper
OCR engine that produces an unusable corpus is not a saving.

## Decision

**Math-first OCR: Mathpix is the primary path, not the fallback. All engines sit behind one
`DocumentExtractor` interface.**

### Routing by page content

| Page content | Engine | Output |
|---|---|---|
| Equation- or diagram-dense (the STEM default) | **Mathpix — primary** | LaTeX + layout-aware diagram crops |
| Mathpix unavailable, or a page it scores poorly | **Gemini Pro-tier vision** | LaTeX + text, lower determinism |
| Plain prose and tables | Tesseract / cloud OCR | Plain text, table structure |

Native text-layer detection still runs first: a PDF page with a reliable embedded text layer is read
directly and skips OCR entirely, which is faster, cheaper, and more accurate than any engine.

### The interface

```
DocumentExtractor:
  extract(page) → { latex, text, assets, confidence }
```

Provider choice and routing policy are both configuration (`OCR_PROVIDER`,
`OCR_FALLBACK_PROVIDER`, `OCR_PLAINTEXT_PROVIDER`). Changing the default is a config change plus a
reprocess, not a pipeline rewrite.

### Confidence and review

Per-block confidence drives routing to manual review (FR-2.9) rather than silent trust. Low-confidence
pages are flagged, not dropped, and a human correction path covers the residual. `ocr_version` is
recorded in `textbook_assets.data` so an engine upgrade can trigger selective reprocessing (§32).

### De-risking

**Spike S-1 in Phase 0** evaluates Mathpix against a vision LLM and against Tesseract on 2–3 real STEM
textbooks, producing a LaTeX-and-diagram quality scorecard as its exit artifact. The interface exists so
that spike's outcome can change the default without touching the pipeline. Formula recognition moves
out of "future enhancements" and into the MVP path.

## Consequences

**Positive.** The corpus is usable for the market the product targets. Extracted mathematics is
retrievable, renderable (KaTeX), and verifiable (ADR-010). Diagram crops arrive with layout awareness
rather than as whole-page images. The interface keeps the vendor decision reversible.

**Negative / accepted.** Mathpix is a paid per-page API, so ingestion carries a real unit cost —
concentrated in one-time corpus building and tenant onboarding rather than steady-state operation, and
it is exactly the "seed the corpus" spend the cold-start analysis already budgets for. It is also an
external dependency with rate limits and outage risk: mitigated by the `ocr.mathpix` kill switch, which
degrades to queuing ingestion rather than failing it, and by the vision-LLM fallback.

Three engines mean three code paths and three sets of failure behaviour behind one interface. The
routing decision — is this page equation-dense? — is itself a heuristic that can be wrong, which is why
confidence scoring and manual review are part of the design rather than an afterthought.

**Handwritten content remains unsupported.** That limitation stands regardless of engine and is
documented in §39.

## Alternatives considered

- **Cloud Vision / Tesseract as primary** (doc 05's original). Cheap, mature, and adequate for prose.
  Rejected as the *default* because it mangles mathematics, and STEM textbooks are the target market —
  it would produce a corpus that undermines the retrieval-first thesis the whole architecture rests on.
  Retained as the correct engine for plain-text and table pages, where it is genuinely better value.
- **Vision LLM as primary, no Mathpix.** One fewer vendor, and native to the Gemini provider already in
  the stack. Rejected as the default on determinism and cost-per-page at volume: a specialised OCR model
  gives more consistent LaTeX for the same page than a general vision model, and consistency matters
  when the output is a permanent corpus. Retained as the fallback — and Phase 0 may overturn this.
- **Tesseract plus a LaTeX post-processor.** Avoids a paid API. Rejected: reconstructing structured
  mathematics from mangled character output is harder than recognising it correctly in the first place.
- **Defer math OCR; ingest prose only at MVP.** Rejected: it would ship an ingestion pipeline that
  cannot ingest the material the target market actually has.
