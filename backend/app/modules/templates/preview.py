"""HTML-only template preview over fixed, non-paper sample content."""

from __future__ import annotations

from html import escape
from typing import Any


def render_preview_html(layout: dict[str, Any]) -> str:
    page = layout["page"]
    margins = page["marginsMm"]
    branding = layout["branding"]
    typography = layout["typography"]
    spacing = layout["spacing"]
    header = layout["header"]
    footer = layout["footer"]
    logo = branding["logo"]
    watermark = branding["watermark"]

    width, height = ((216, 279) if page["size"] == "Letter" else (210, 297))
    if page["orientation"] == "landscape":
        width, height = height, width

    logo_html = ""
    if header["showLogo"] and logo.get("url"):
        logo_html = (
            f'<img class="logo {escape(logo["placement"])}" '
            f'src="{escape(logo["url"], quote=True)}" alt="Organisation logo">'
        )
    institute_html = (
        f'<div class="institute">{escape(branding["instituteName"])}</div>'
        if header["showInstituteName"]
        else ""
    )
    metadata_values = {
        "examName": "Mid-Term Examination",
        "date": "15 October 2026",
        "duration": "90 minutes",
        "totalMarks": "40 marks",
    }
    metadata_html = "".join(
        f"<span>{escape(metadata_values[key])}</span>" for key in header["metadata"]
    )
    watermark_html = (
        f'<div class="watermark">{escape(watermark["text"])}</div>'
        if watermark.get("text")
        else ""
    )
    numbering = footer["pageNumbering"]
    page_number = (
        numbering["format"].replace("{page}", "1").replace("{pages}", "1")
        if numbering["enabled"]
        else ""
    )
    header_display = "block" if header["enabled"] else "none"
    footer_display = "flex" if footer["enabled"] else "none"

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Template preview</title>
<style>
*{{box-sizing:border-box}} html,body{{margin:0;background:#e5e7eb;color:#111827}}
body{{padding:24px;font-family:{escape(typography['fontFamily'])},sans-serif}}
.paper{{position:relative;overflow:hidden;margin:auto;width:{width}mm;min-height:{height}mm;background:#fff;
padding:{margins['top']}mm {margins['right']}mm {margins['bottom']}mm {margins['left']}mm;
font-size:{typography['bodySizePt']}pt;line-height:{typography['lineHeight']};box-shadow:0 12px 32px #0002}}
.header{{display:{header_display};border-bottom:2px solid {branding['primaryColor']};padding-bottom:5mm;text-align:{header['titleAlignment']}}}
.logo{{display:block;max-height:{logo['maxHeightMm']}mm;max-width:42mm;margin-bottom:2mm}}
.logo.center{{margin-left:auto;margin-right:auto}} .logo.right{{margin-left:auto}}
.institute{{color:{branding['primaryColor']};font-size:calc({typography['bodySizePt']}pt * {typography['headingScale']});font-weight:700}}
h1{{margin:4mm 0 2mm;color:{branding['primaryColor']};font-size:calc({typography['bodySizePt']}pt * {typography['headingScale']})}}
.meta{{display:flex;flex-wrap:wrap;justify-content:space-between;gap:2mm 6mm;border-top:1px solid #d1d5db;padding-top:3mm;font-size:.88em}}
.instructions{{margin:6mm 0;padding:3mm 4mm;border-left:3px solid {branding['secondaryColor']};background:#f8fafc}}
.section{{margin-top:{spacing['sectionGapMm']}mm}} .section-title{{display:flex;justify-content:space-between;color:{branding['primaryColor']};font-weight:700;border-bottom:1px solid {branding['primaryColor']};padding-bottom:1mm}}
.question{{display:grid;grid-template-columns:1fr auto;gap:5mm;margin-top:{spacing['questionGapMm']}mm}}
.marks{{font-weight:700;color:{branding['primaryColor']}}}
.footer{{display:{footer_display};position:absolute;left:{margins['left']}mm;right:{margins['right']}mm;bottom:6mm;justify-content:space-between;border-top:1px solid #d1d5db;padding-top:2mm;font-size:.8em;color:#4b5563}}
.page-number{{text-align:{numbering['alignment']}}}
.watermark{{position:absolute;inset:45% 0 auto;text-align:center;transform:rotate(-28deg);font-size:42pt;font-weight:800;color:{branding['primaryColor']};opacity:{watermark['opacity']};pointer-events:none}}
</style></head><body><main class="paper">{watermark_html}
<header class="header">{logo_html}{institute_html}<h1>Science Assessment</h1><div class="meta">{metadata_html}</div></header>
<section class="instructions"><strong>Instructions</strong><br>Answer all questions. Show your working for numerical answers.</section>
<section class="section"><div class="section-title"><span>Section A · Objective questions</span><span>10 marks</span></div>
<div class="question"><span>1. Which process converts light energy into chemical energy?</span><span class="marks">[1]</span></div>
<div class="question"><span>2. State whether the following is true or false: sound travels through a vacuum.</span><span class="marks">[1]</span></div></section>
<section class="section"><div class="section-title"><span>Section B · Short answers</span><span>30 marks</span></div>
<div class="question"><span>3. Explain two factors that affect the rate of photosynthesis.</span><span class="marks">[4]</span></div>
<div class="question"><span>4. A force of 20 N moves an object 3 m. Calculate the work done.</span><span class="marks">[3]</span></div></section>
<footer class="footer"><span>{escape(footer['text'])}</span><span class="page-number">{escape(page_number)}</span></footer>
</main></body></html>"""

