"""Safe HTML composition for immutable paper snapshots and template layout v1."""

from __future__ import annotations

import base64
import html
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlparse

from app.models.question import QUESTION_TYPES
from app.modules.exports.contracts import ExportRenderRequest
from app.modules.templates.schemas import TemplateLayoutV1


_DISPLAY_MATH = re.compile(r"\$\$(.+?)\$\$|\\\[(.+?)\\\]", re.DOTALL)
_INLINE_MATH = re.compile(r"(?<!\\)\$(?!\$)(.+?)(?<!\\)\$|\\\((.+?)\\\)", re.DOTALL)

_TYPE_LABELS = {
    "mcq": "Multiple Choice",
    "true_false": "True / False",
    "one_word": "One Word",
    "fill_blank": "Fill in the Blank",
    "short_answer": "Short Answer",
    "long_answer": "Long Answer",
    "assertion_reason": "Assertion-Reason",
    "case_study": "Case Study",
    "numerical": "Numerical",
    "diagram_based": "Diagram Based",
    "match": "Match the Columns",
    "very_short_answer": "Very Short Answer",
    "source_based": "Source Based",
}


def _math_placeholder(expression: str, *, display: bool) -> str:
    encoded = base64.urlsafe_b64encode(expression.strip().encode("utf-8")).decode("ascii")
    return (
        f'<span class="math-placeholder" data-katex-b64="{encoded}" '
        f'data-display="{str(display).lower()}"></span>'
    )


def render_rich_text(value: Any) -> str:
    """Escape prose and replace supported TeX delimiters with inert SSR markers."""

    text = "" if value is None else str(value)
    replacements: list[str] = []

    def display(match: re.Match[str]) -> str:
        replacements.append(_math_placeholder(match.group(1) or match.group(2), display=True))
        return f"\x00{len(replacements) - 1}\x00"

    def inline(match: re.Match[str]) -> str:
        replacements.append(_math_placeholder(match.group(1) or match.group(2), display=False))
        return f"\x00{len(replacements) - 1}\x00"

    protected = _DISPLAY_MATH.sub(display, text)
    protected = _INLINE_MATH.sub(inline, protected)
    escaped = html.escape(protected).replace("\n", "<br>")
    for index, replacement in enumerate(replacements):
        escaped = escaped.replace(f"\x00{index}\x00", replacement)
    return escaped


def _safe_image_source(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlparse(value)
    if parsed.scheme == "data" and value.startswith("data:image/"):
        return value
    return None


def _option_text(option: Any) -> Any:
    return option.get("text", "") if isinstance(option, Mapping) else option


def _answer_space(lines: int) -> str:
    return '<div class="answer-space">' + "".join("<span></span>" for _ in range(lines)) + "</div>"


def _subquestions(options: Mapping[str, Any]) -> str:
    items = options.get("sub_questions") or options.get("subQuestions") or []
    rendered = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, Mapping):
            continue
        marks = item.get("marks")
        marks_html = f'<span class="sub-marks">[{html.escape(str(marks))}]</span>' if marks is not None else ""
        rendered.append(
            '<li class="sub-question">'
            f'<span>{render_rich_text(item.get("stem", ""))}</span>{marks_html}'
            f'{_answer_space(2)}</li>'
        )
    return f'<ol class="sub-questions">{"".join(rendered)}</ol>'


def render_question_body(question: Mapping[str, Any]) -> str:
    qtype = str(question.get("qtype", ""))
    options = question.get("options")
    options = options if isinstance(options, Mapping) else options or []

    if qtype == "mcq":
        return '<ol class="choices" type="A">' + "".join(
            f"<li>{render_rich_text(_option_text(item))}</li>" for item in options
        ) + "</ol>"
    if qtype == "true_false":
        return '<div class="binary-choice">□ True&nbsp;&nbsp;&nbsp;&nbsp;□ False</div>'
    if qtype == "one_word":
        return _answer_space(1)
    if qtype == "fill_blank":
        return '<div class="response-hint">Write the answer(s) in the blank(s) above.</div>'
    if qtype == "short_answer":
        return _answer_space(3)
    if qtype == "long_answer":
        return _answer_space(6)
    if qtype == "assertion_reason":
        reason = options.get("reason", "") if isinstance(options, Mapping) else ""
        if str(reason).lower().startswith("reason:"):
            reason = str(reason).split(":", 1)[1].lstrip()
        relationship_options = options.get("rel_options", []) if isinstance(options, Mapping) else []
        standard = relationship_options or [
            "Both are true and the reason explains the assertion",
            "Both are true but the reason does not explain the assertion",
            "Assertion is true but reason is false",
            "Assertion is false but reason is true",
        ]
        return (
            f'<div class="reason"><strong>Reason:</strong> {render_rich_text(reason)}</div>'
            + '<ol class="choices" type="A">'
            + "".join(f"<li>{render_rich_text(item)}</li>" for item in standard)
            + "</ol>"
        )
    if qtype in {"case_study", "source_based"}:
        return _subquestions(options if isinstance(options, Mapping) else {})
    if qtype == "numerical":
        return _answer_space(4)
    if qtype == "diagram_based":
        source = None
        alt = "Question diagram"
        if isinstance(options, Mapping):
            source = _safe_image_source(
                options.get("asset_data_uri") or options.get("assetDataUri")
            )
            alt = str(options.get("alt") or alt)
        if source:
            return f'<figure class="diagram"><img src="{html.escape(source, quote=True)}" alt="{html.escape(alt, quote=True)}"><figcaption>{html.escape(alt)}</figcaption></figure>{_answer_space(3)}'
        asset_id = options.get("asset_id") if isinstance(options, Mapping) else None
        reference = f" — reference {html.escape(str(asset_id))}" if asset_id else ""
        return f'<div class="diagram missing">Diagram unavailable in immutable snapshot{reference}</div>{_answer_space(3)}'
    if qtype == "match":
        left = options.get("column_a", []) if isinstance(options, Mapping) else []
        right = options.get("column_b", []) if isinstance(options, Mapping) else []
        rows = max(len(left), len(right))
        body = "".join(
            "<tr>"
            f'<td>{index + 1}. {render_rich_text(left[index]) if index < len(left) else ""}</td>'
            f'<td>{chr(65 + index)}. {render_rich_text(right[index]) if index < len(right) else ""}</td>'
            "</tr>"
            for index in range(rows)
        )
        return f'<table class="match-table"><thead><tr><th>Column A</th><th>Column B</th></tr></thead><tbody>{body}</tbody></table>'
    if qtype == "very_short_answer":
        return _answer_space(2)
    return '<div class="unsupported">Unsupported question type</div>'


def _render_questions(questions: Sequence[Any], *, start: int) -> tuple[str, int]:
    rendered: list[str] = []
    number = start
    for question in questions:
        if not isinstance(question, Mapping):
            continue
        qtype = str(question.get("qtype", ""))
        if qtype not in QUESTION_TYPES:
            raise ValueError(f"paper content has unsupported question type: {qtype!r}")
        marks = html.escape(str(question.get("marks", "")))
        rendered.append(
            f'<article class="question question-{qtype}" data-question-type="{qtype}">'
            '<div class="question-heading">'
            f'<span class="question-number">{number}.</span>'
            f'<span class="question-type">{html.escape(_TYPE_LABELS[qtype])}</span>'
            f'<span class="marks">[{marks} mark{"s" if marks != "1" else ""}]</span>'
            "</div>"
            f'<div class="stem">{render_rich_text(question.get("stem", ""))}</div>'
            f'{render_question_body(question)}'
            "</article>"
        )
        number += 1
    return "".join(rendered), number


def _footer_template(layout: TemplateLayoutV1) -> str:
    if not layout.footer.enabled:
        return "<div></div>"
    numbering = layout.footer.page_numbering
    number_html = ""
    if numbering.enabled:
        number_html = html.escape(numbering.format)
        number_html = number_html.replace("{page}", '<span class="pageNumber"></span>')
        number_html = number_html.replace("{pages}", '<span class="totalPages"></span>')
    separator = " - " if layout.footer.text and number_html else ""
    children = f"{html.escape(layout.footer.text)}{separator}{number_html}"
    return (
        '<div style="box-sizing:border-box;width:100%;font-size:8pt;color:#4b5563;'
        f'padding:0 14mm;text-align:{numbering.alignment};font-family:Arial,sans-serif">'
        f'{children}</div>'
    )


def compose_paper_html(request: ExportRenderRequest) -> tuple[str, str]:
    layout = TemplateLayoutV1.model_validate(request.template_layout)
    content = request.content
    if content.get("schemaVersion") != 1:
        raise ValueError("only PaperVersion.content schemaVersion 1 is renderable")
    sections = content.get("sections")
    if not isinstance(sections, Sequence) or isinstance(sections, (str, bytes)):
        raise ValueError("PaperVersion.content.sections must be a list")

    rendered_sections: list[str] = []
    next_number = 1
    for index, section in enumerate(sections, start=1):
        if not isinstance(section, Mapping):
            raise ValueError("each paper section must be an object")
        questions = section.get("questions")
        if not isinstance(questions, Sequence) or isinstance(questions, (str, bytes)):
            raise ValueError("each paper section must have a questions list")
        body, next_number = _render_questions(questions, start=next_number)
        section_id = str(section.get("id") or index)
        section_title = str(section.get("title") or f"Section {section_id}")
        rendered_sections.append(
            '<section class="section">'
            f'<h2><span>{html.escape(section_title)}</span><span>{html.escape(str(section.get("marks", "")))} marks</span></h2>'
            f'{body}</section>'
        )

    page = layout.page
    margins = page.margins_mm
    branding = layout.branding
    typography = layout.typography
    spacing = layout.spacing
    header = layout.header
    logo_source = _safe_image_source(branding.logo.url)
    logo = ""
    if header.show_logo and branding.logo.url and not logo_source:
        raise ValueError(
            "template logo must be an embedded data:image URI for deterministic, "
            "network-isolated export"
        )
    if header.show_logo and logo_source:
        logo = f'<img class="logo" src="{html.escape(logo_source, quote=True)}" alt="Institute logo">'
    institute = (
        f'<div class="institute">{html.escape(branding.institute_name)}</div>'
        if header.show_institute_name else ""
    )
    meta_values = {
        "examName": request.title,
        "date": (
            f"Date: {request.version_created_at.date().isoformat()}"
            if request.version_created_at else ""
        ),
        "duration": f"Duration: {request.duration_minutes} minutes" if request.duration_minutes else "",
        "totalMarks": f"Total marks: {request.total_marks}" if request.total_marks is not None else "",
    }
    metadata = "".join(
        f"<span>{html.escape(meta_values[item])}</span>" for item in header.metadata if meta_values[item]
    )
    watermark = ""
    if branding.watermark.text:
        watermark = f'<div class="watermark">{html.escape(branding.watermark.text)}</div>'

    payload = html.escape(json.dumps({
        "paperVersionId": str(request.paper_version_id),
        "templateId": str(request.template_id),
        "questionTypes": list(QUESTION_TYPES),
    }, separators=(",", ":")), quote=True)
    header_html = ""
    if header.enabled:
        header_html = (
            f'<header class="paper-header">{logo}{institute}'
            f'<h1>{html.escape(request.title)}</h1><div class="metadata">{metadata}</div></header>'
        )
    orientation = "landscape" if page.orientation == "landscape" else "portrait"
    document = f'''<!doctype html>
<html><head><meta charset="utf-8"><style>{{{{KATEX_CSS}}}}</style><style>
@page {{ size: {page.size} {orientation}; margin: {margins.top}mm {margins.right}mm {margins.bottom}mm {margins.left}mm; }}
* {{ box-sizing: border-box; }}
html, body {{ margin:0; padding:0; color:#111827; font-family:{json.dumps(typography.font_family)},sans-serif; font-size:{typography.body_size_pt}pt; line-height:{typography.line_height}; -webkit-print-color-adjust:exact; print-color-adjust:exact; }}
body {{ position:relative; }}
.paper-header {{ text-align:{header.title_alignment}; border-bottom:2px solid {branding.primary_color}; padding-bottom:4mm; margin-bottom:5mm; }}
.logo {{ max-height:{branding.logo.max_height_mm}mm; display:block; margin-bottom:2mm; margin-left:{"auto" if branding.logo.placement in {"center", "right"} else "0"}; margin-right:{"auto" if branding.logo.placement in {"center", "left"} else "0"}; }}
.institute {{ color:{branding.primary_color}; font-weight:700; font-size:calc(1em * {typography.heading_scale}); }}
h1 {{ margin:2mm 0; font-size:calc(1em * {typography.heading_scale}); }}
.metadata {{ display:flex; flex-wrap:wrap; justify-content:space-between; gap:3mm; font-size:.9em; }}
.watermark {{ position:fixed; z-index:-1; top:43%; left:0; right:0; text-align:center; transform:rotate(-28deg); font-size:42pt; font-weight:800; color:{branding.primary_color}; opacity:{branding.watermark.opacity}; }}
.section {{ margin-top:{spacing.section_gap_mm}mm; break-inside:auto; }}
.section h2 {{ display:flex; justify-content:space-between; color:{branding.primary_color}; border-bottom:1px solid {branding.primary_color}; padding-bottom:1mm; font-size:calc(1em * 1.15); break-after:avoid; }}
.question {{ margin-top:{spacing.question_gap_mm}mm; break-inside:avoid; }}
.question-heading {{ display:grid; grid-template-columns:auto 1fr auto; gap:2mm; align-items:baseline; }}
.question-number,.marks {{ font-weight:700; }} .marks {{ color:{branding.primary_color}; }}
.question-type {{ color:{branding.secondary_color}; font-size:.76em; text-transform:uppercase; letter-spacing:.04em; }}
.stem,.reason {{ margin:1mm 0 0 7mm; }}
.choices,.sub-questions {{ margin:2mm 0 0 11mm; padding-left:5mm; columns:2; column-gap:10mm; }}
.choices li,.sub-questions li {{ break-inside:avoid; padding:0 0 1mm 1mm; }}
.sub-questions {{ columns:1; }} .sub-marks {{ float:right; font-weight:700; }}
.answer-space {{ margin:1mm 0 0 7mm; }} .answer-space span {{ display:block; height:6mm; border-bottom:1px dotted #94a3b8; }}
.response-hint {{ margin-left:7mm; color:#64748b; font-size:.82em; }}
.binary-choice {{ margin:2mm 0 0 7mm; }}
.diagram {{ margin:3mm auto; text-align:center; }} .diagram img {{ max-width:100%; max-height:70mm; }} .diagram figcaption {{ font-size:.8em; color:#64748b; }}
.diagram.missing {{ border:1px dashed #94a3b8; padding:8mm; color:#64748b; }}
.match-table {{ width:calc(100% - 7mm); margin:2mm 0 0 7mm; border-collapse:collapse; }} .match-table th,.match-table td {{ border:1px solid #cbd5e1; padding:2mm; text-align:left; }}
.katex-display {{ margin:.6em 0; overflow:hidden; }}
</style></head><body data-render-contract="paper-content-v1+template-layout-v1" data-lineage="{payload}">
{watermark}{header_html}<main>{''.join(rendered_sections)}</main>
</body></html>'''
    return document, _footer_template(layout)


__all__ = ["compose_paper_html", "render_question_body", "render_rich_text"]
