"""Native OOXML DOCX renderer for papers and separate answer keys."""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Mapping, Sequence
from io import BytesIO
from typing import Any
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Mm, Pt, RGBColor
from latex2mathml.converter import convert as latex_to_mathml

from app.models.question import QUESTION_TYPES
from app.modules.exports.contracts import ExportRenderRequest, ExportRenderer, RenderedArtifact
from app.modules.templates.schemas import TemplateLayoutV1


DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)

_MATH = re.compile(
    r"\$\$(.+?)\$\$|\\\[(.+?)\\\]|(?<!\\)\$(?!\$)(.+?)(?<!\\)\$|\\\((.+?)\\\)",
    re.DOTALL,
)
_DATA_IMAGE = re.compile(
    r"^data:(image/[A-Za-z0-9.+-]+);base64,([A-Za-z0-9+/=\r\n]+)$"
)

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


class DocxRenderError(RuntimeError):
    pass


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _omml_run(text: str) -> OxmlElement:
    run = OxmlElement("m:r")
    value = OxmlElement("m:t")
    value.text = text
    run.append(value)
    return run


def _append_mathml_children(target: OxmlElement, element: ElementTree.Element) -> None:
    if element.text and element.text.strip():
        target.append(_omml_run(element.text.strip()))
    for child in element:
        for converted in _mathml_to_omml(child):
            target.append(converted)
        if child.tail and child.tail.strip():
            target.append(_omml_run(child.tail.strip()))


def _script_element(name: str, children: list[ElementTree.Element]) -> OxmlElement:
    result = OxmlElement(f"m:{name}")
    slots = {
        "sSup": ("e", "sup"),
        "sSub": ("e", "sub"),
        "sSubSup": ("e", "sub", "sup"),
    }[name]
    for slot, child in zip(slots, children, strict=False):
        wrapper = OxmlElement(f"m:{slot}")
        _append_mathml_children(wrapper, child)
        result.append(wrapper)
    return result


def _mathml_to_omml(element: ElementTree.Element) -> list[OxmlElement]:
    name = _local_name(element.tag)
    children = list(element)
    if name in {"math", "mrow", "mstyle", "semantics", "mpadded", "menclose"}:
        container = OxmlElement("m:e")
        _append_mathml_children(container, element)
        return list(container)
    if name in {"mi", "mn", "mo", "mtext", "ms"}:
        return [_omml_run("".join(element.itertext()))]
    if name == "mspace":
        return [_omml_run(" ")]
    if name == "msup" and len(children) >= 2:
        return [_script_element("sSup", children[:2])]
    if name == "msub" and len(children) >= 2:
        return [_script_element("sSub", children[:2])]
    if name == "msubsup" and len(children) >= 3:
        return [_script_element("sSubSup", children[:3])]
    if name == "mfrac" and len(children) >= 2:
        fraction = OxmlElement("m:f")
        numerator = OxmlElement("m:num")
        denominator = OxmlElement("m:den")
        _append_mathml_children(numerator, children[0])
        _append_mathml_children(denominator, children[1])
        fraction.extend((numerator, denominator))
        return [fraction]
    if name in {"msqrt", "mroot"} and children:
        radical = OxmlElement("m:rad")
        degree = OxmlElement("m:deg")
        expression = OxmlElement("m:e")
        if name == "mroot" and len(children) >= 2:
            _append_mathml_children(degree, children[1])
        else:
            radical_properties = OxmlElement("m:radPr")
            hide_degree = OxmlElement("m:degHide")
            hide_degree.set(qn("m:val"), "1")
            radical_properties.append(hide_degree)
            radical.append(radical_properties)
        _append_mathml_children(expression, children[0])
        radical.extend((degree, expression))
        return [radical]
    if name == "mfenced":
        opening = element.attrib.get("open", "(")
        closing = element.attrib.get("close", ")")
        separator = element.attrib.get("separators", ",")
        result = [_omml_run(opening)]
        for index, child in enumerate(children):
            if index:
                result.append(_omml_run(separator))
            result.extend(_mathml_to_omml(child))
        result.append(_omml_run(closing))
        return result
    if name == "mtable":
        matrix = OxmlElement("m:m")
        for row_element in children:
            row = OxmlElement("m:mr")
            for cell_element in list(row_element):
                cell = OxmlElement("m:e")
                _append_mathml_children(cell, cell_element)
                row.append(cell)
            matrix.append(row)
        return [matrix]
    if children:
        container = OxmlElement("m:e")
        _append_mathml_children(container, element)
        return list(container)
    text = "".join(element.itertext())
    return [_omml_run(text)] if text else []


def _append_omml(paragraph, latex: str) -> None:
    try:
        mathml = latex_to_mathml(latex.strip())
        root = ElementTree.fromstring(mathml)
    except Exception as exc:  # noqa: BLE001 - conversion must fail closed
        raise DocxRenderError(f"could not convert LaTeX to native OMML: {exc}") from exc
    equation = OxmlElement("m:oMath")
    _append_mathml_children(equation, root)
    paragraph._p.append(equation)


def _set_font(run, name: str, size_pt: float | None = None) -> None:
    run.font.name = name
    if run._element.get_or_add_rPr().rFonts is None:
        run._element.get_or_add_rPr().append(OxmlElement("w:rFonts"))
    fonts = run._element.get_or_add_rPr().rFonts
    fonts.set(qn("w:ascii"), name)
    fonts.set(qn("w:hAnsi"), name)
    if size_pt is not None:
        run.font.size = Pt(size_pt)


def _append_rich_text(paragraph, value: Any, *, bold: bool = False, font: str = "Arial") -> None:
    text = "" if value is None else str(value)
    position = 0
    for match in _MATH.finditer(text):
        if match.start() > position:
            run = paragraph.add_run(text[position : match.start()].replace("\\$", "$"))
            run.bold = bold
            _set_font(run, font)
        expression = next(group for group in match.groups() if group is not None)
        _append_omml(paragraph, expression)
        position = match.end()
    if position < len(text):
        run = paragraph.add_run(text[position:].replace("\\$", "$"))
        run.bold = bold
        _set_font(run, font)


def _decode_data_image(value: Any) -> tuple[bytes, str] | None:
    if not isinstance(value, str):
        return None
    match = _DATA_IMAGE.fullmatch(value.strip())
    if not match:
        return None
    try:
        return base64.b64decode(match.group(2), validate=True), match.group(1)
    except ValueError as exc:
        raise DocxRenderError("embedded data:image URI has invalid base64") from exc


def _style_document(document, layout: TemplateLayoutV1) -> None:
    typography = layout.typography
    normal = document.styles["Normal"]
    normal.font.name = typography.font_family
    normal.font.size = Pt(typography.body_size_pt)
    normal.font.color.rgb = RGBColor(0, 0, 0)
    normal.paragraph_format.space_after = Pt(4)
    normal.paragraph_format.line_spacing = typography.line_height
    for name in ("Title", "Heading 1", "Heading 2"):
        style = document.styles[name]
        style.font.name = typography.font_family
        style.font.color.rgb = RGBColor(0, 0, 0)
    document.styles["Title"].font.size = Pt(
        max(18, typography.body_size_pt * typography.heading_scale * 1.3)
    )
    document.styles["Heading 1"].font.size = Pt(
        typography.body_size_pt * typography.heading_scale
    )
    for qtype in QUESTION_TYPES:
        style = document.styles.add_style(
            f"Question Type {qtype}", WD_STYLE_TYPE.PARAGRAPH
        )
        style.base_style = normal
        style.font.name = typography.font_family
        style.font.size = Pt(max(8, typography.body_size_pt * 0.8))
        style.font.bold = True
        style.font.color.rgb = RGBColor.from_string(
            layout.branding.secondary_color.removeprefix("#")
        )
        style.paragraph_format.space_before = Mm(layout.spacing.question_gap_mm)
        style.paragraph_format.space_after = Pt(4)
        style.paragraph_format.keep_with_next = True
    answer_line = document.styles.add_style("Answer Line", WD_STYLE_TYPE.PARAGRAPH)
    answer_line.base_style = normal
    answer_line.paragraph_format.space_after = Pt(6)
    answer_line.paragraph_format.line_spacing = 1


def _configure_page(document, layout: TemplateLayoutV1) -> None:
    section = document.sections[0]
    if layout.page.size == "A4":
        width, height = Mm(210), Mm(297)
    else:
        width, height = Inches(8.5), Inches(11)
    if layout.page.orientation == "landscape":
        section.orientation = WD_ORIENT.LANDSCAPE
        width, height = height, width
    section.page_width = width
    section.page_height = height
    section.top_margin = Mm(layout.page.margins_mm.top)
    section.right_margin = Mm(layout.page.margins_mm.right)
    section.bottom_margin = Mm(layout.page.margins_mm.bottom)
    section.left_margin = Mm(layout.page.margins_mm.left)


def _set_paragraph_bottom_border(paragraph, *, color: str = "A8B0B8") -> None:
    properties = paragraph._p.get_or_add_pPr()
    borders = properties.find(qn("w:pBdr"))
    if borders is None:
        borders = OxmlElement("w:pBdr")
        properties.append(borders)
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "dotted")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "2")
    bottom.set(qn("w:color"), color)
    borders.append(bottom)


def _add_response_lines(document, count: int) -> None:
    for _ in range(count):
        paragraph = document.add_paragraph(style="Answer Line")
        paragraph.add_run(" ")
        _set_paragraph_bottom_border(paragraph)


def _set_cell_shading(cell, color: str) -> None:
    shading = OxmlElement("w:shd")
    shading.set(qn("w:fill"), color)
    cell._tc.get_or_add_tcPr().append(shading)


def _repeat_table_header(row) -> None:
    properties = row._tr.get_or_add_trPr()
    header = OxmlElement("w:tblHeader")
    header.set(qn("w:val"), "true")
    properties.append(header)


def _safe_image_source(value: Any) -> tuple[bytes, str] | None:
    if not isinstance(value, str):
        return None
    if value.startswith("data:image/"):
        decoded = _decode_data_image(value)
        if decoded is None:
            raise DocxRenderError("embedded image must use a base64 data:image URI")
        return decoded
    return None


def _add_picture(document, image: tuple[bytes, str], *, alt: str, width: float) -> None:
    data, media_type = image
    if media_type == "image/svg+xml":
        raise DocxRenderError(
            "DOCX native image embedding does not accept SVG; snapshot a PNG data:image URI"
        )
    paragraph = document.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    try:
        inline_shape = paragraph.add_run().add_picture(BytesIO(data), width=Inches(width))
    except Exception as exc:  # noqa: BLE001 - reject invalid snapshot bytes
        raise DocxRenderError(f"embedded image could not be decoded: {exc}") from exc
    inline_shape._inline.docPr.set("descr", alt)
    caption = document.add_paragraph(alt)
    caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
    caption.runs[0].italic = True


def _add_missing_diagram(document, reference: Any = None) -> None:
    table = document.add_table(rows=1, cols=1)
    table.style = "Table Grid"
    text = "Diagram unavailable in immutable snapshot"
    if reference:
        text += f" - reference {reference}"
    cell = table.cell(0, 0)
    cell.text = text
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    _set_cell_shading(cell, "F3F4F6")


def _option_text(option: Any) -> Any:
    return option.get("text", "") if isinstance(option, Mapping) else option


def _add_choices(
    document, choices: Sequence[Any], font: str, *, keep_together: bool = False
) -> None:
    for index, choice in enumerate(choices):
        paragraph = document.add_paragraph()
        paragraph.paragraph_format.left_indent = Mm(8)
        paragraph.paragraph_format.keep_with_next = keep_together and index < len(choices) - 1
        label = paragraph.add_run(f"{chr(65 + index)}. ")
        label.bold = True
        _set_font(label, font)
        _append_rich_text(paragraph, _option_text(choice), font=font)


def _add_subquestions(document, options: Mapping[str, Any], font: str) -> None:
    items = options.get("sub_questions") or options.get("subQuestions") or []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, Mapping):
            continue
        paragraph = document.add_paragraph()
        paragraph.paragraph_format.left_indent = Mm(8)
        run = paragraph.add_run(f"{index}. ")
        run.bold = True
        _set_font(run, font)
        _append_rich_text(paragraph, item.get("stem", ""), font=font)
        if item.get("marks") is not None:
            marks = paragraph.add_run(f" [{item['marks']}]")
            marks.bold = True
            _set_font(marks, font)
        _add_response_lines(document, 2)


def _render_question_body(document, question: Mapping[str, Any], font: str) -> None:
    qtype = str(question.get("qtype", ""))
    raw_options = question.get("options")
    options = raw_options if isinstance(raw_options, Mapping) else raw_options or []
    if qtype == "mcq":
        _add_choices(document, options, font)
    elif qtype == "true_false":
        document.add_paragraph("\u2610 True        \u2610 False")
    elif qtype == "one_word":
        _add_response_lines(document, 1)
    elif qtype == "fill_blank":
        document.add_paragraph("Write the answer in the blank shown in the question.")
    elif qtype == "short_answer":
        _add_response_lines(document, 3)
    elif qtype == "long_answer":
        _add_response_lines(document, 6)
    elif qtype == "assertion_reason":
        reason = options.get("reason", "") if isinstance(options, Mapping) else ""
        if str(reason).lower().startswith("reason:"):
            reason = str(reason).split(":", 1)[1].lstrip()
        paragraph = document.add_paragraph()
        paragraph.paragraph_format.keep_with_next = True
        paragraph.add_run("Reason: ").bold = True
        _append_rich_text(paragraph, reason, font=font)
        relationship_options = (
            options.get("rel_options", []) if isinstance(options, Mapping) else []
        )
        _add_choices(
            document,
            relationship_options
            or [
                "Both are true and the reason explains the assertion",
                "Both are true but the reason does not explain the assertion",
                "Assertion is true but reason is false",
                "Assertion is false but reason is true",
            ],
            font,
            keep_together=True,
        )
    elif qtype in {"case_study", "source_based"}:
        _add_subquestions(document, options if isinstance(options, Mapping) else {}, font)
    elif qtype == "numerical":
        _add_response_lines(document, 4)
    elif qtype == "diagram_based":
        source = None
        alt = "Question diagram"
        if isinstance(options, Mapping):
            source = _safe_image_source(
                options.get("asset_data_uri") or options.get("assetDataUri")
            )
            alt = str(options.get("alt") or alt)
        if source is not None:
            _add_picture(document, source, alt=alt, width=5.2)
        else:
            _add_missing_diagram(
                document,
                options.get("asset_id") if isinstance(options, Mapping) else None,
            )
        _add_response_lines(document, 3)
    elif qtype == "match":
        left = options.get("column_a", []) if isinstance(options, Mapping) else []
        right = options.get("column_b", []) if isinstance(options, Mapping) else []
        table = document.add_table(rows=1, cols=2)
        table.style = "Table Grid"
        _repeat_table_header(table.rows[0])
        for index, label in enumerate(("Column A", "Column B")):
            cell = table.rows[0].cells[index]
            cell.text = label
            _set_cell_shading(cell, "DDEBF7")
            cell.paragraphs[0].runs[0].bold = True
        for index in range(max(len(left), len(right))):
            cells = table.add_row().cells
            cells[0].text = f"{index + 1}. {left[index] if index < len(left) else ''}"
            cells[1].text = (
                f"{chr(65 + index)}. {right[index] if index < len(right) else ''}"
            )
            for cell in cells:
                cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    elif qtype == "very_short_answer":
        _add_response_lines(document, 2)
    else:
        raise DocxRenderError(f"unsupported question type: {qtype!r}")


def _iter_questions(content: Mapping[str, Any]):
    if content.get("schemaVersion") != 1:
        raise DocxRenderError("only PaperVersion.content schemaVersion 1 is renderable")
    sections = content.get("sections")
    if not isinstance(sections, Sequence) or isinstance(sections, (str, bytes)):
        raise DocxRenderError("PaperVersion.content.sections must be a list")
    number = 1
    for section_index, section in enumerate(sections, start=1):
        if not isinstance(section, Mapping):
            raise DocxRenderError("each paper section must be an object")
        questions = section.get("questions")
        if not isinstance(questions, Sequence) or isinstance(questions, (str, bytes)):
            raise DocxRenderError("each paper section must have a questions list")
        yield "section", section_index, section
        for question in questions:
            if not isinstance(question, Mapping):
                continue
            qtype = str(question.get("qtype", ""))
            if qtype not in QUESTION_TYPES:
                raise DocxRenderError(f"paper content has unsupported question type: {qtype!r}")
            yield "question", number, question
            number += 1


def _add_field(paragraph, instruction: str) -> None:
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), instruction)
    field.append(OxmlElement("w:r"))
    paragraph._p.append(field)


def _render_header(document, request: ExportRenderRequest, layout: TemplateLayoutV1) -> None:
    header = layout.header
    branding = layout.branding
    font = layout.typography.font_family
    if header.show_logo and branding.logo.url:
        logo = _safe_image_source(branding.logo.url)
        if logo is None:
            raise DocxRenderError(
                "template logo must be an embedded data:image URI for deterministic, "
                "network-isolated export"
            )
        _add_picture(
            document,
            logo,
            alt="Institute logo",
            width=min(2.4, branding.logo.max_height_mm / 8),
        )
    if header.show_institute_name:
        institute = document.add_paragraph()
        institute.alignment = {
            "left": WD_ALIGN_PARAGRAPH.LEFT,
            "center": WD_ALIGN_PARAGRAPH.CENTER,
            "right": WD_ALIGN_PARAGRAPH.RIGHT,
        }[header.title_alignment]
        run = institute.add_run(branding.institute_name)
        run.bold = True
        _set_font(run, font, layout.typography.body_size_pt * layout.typography.heading_scale)
    title = request.title + (" Answer Key" if request.artifact_type == "answer_key" else "")
    title_paragraph = document.add_paragraph(title, style="Title")
    title_paragraph.alignment = {
        "left": WD_ALIGN_PARAGRAPH.LEFT,
        "center": WD_ALIGN_PARAGRAPH.CENTER,
        "right": WD_ALIGN_PARAGRAPH.RIGHT,
    }[header.title_alignment]
    metadata = {
        "examName": request.title,
        "date": (
            f"Date {request.version_created_at.date().isoformat()}"
            if request.version_created_at
            else ""
        ),
        "duration": (
            f"Duration {request.duration_minutes} minutes" if request.duration_minutes else ""
        ),
        "totalMarks": (
            f"Total marks {request.total_marks}" if request.total_marks is not None else ""
        ),
    }
    values = [metadata[item] for item in header.metadata if metadata[item]]
    if values:
        paragraph = document.add_paragraph("    |    ".join(values))
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for run in paragraph.runs:
            _set_font(run, font, max(8, layout.typography.body_size_pt - 1))


def _render_footer(document, layout: TemplateLayoutV1) -> None:
    if not layout.footer.enabled:
        return
    footer = document.sections[0].footer
    paragraph = footer.paragraphs[0]
    paragraph.alignment = {
        "left": WD_ALIGN_PARAGRAPH.LEFT,
        "center": WD_ALIGN_PARAGRAPH.CENTER,
        "right": WD_ALIGN_PARAGRAPH.RIGHT,
    }[layout.footer.page_numbering.alignment]
    if layout.footer.text:
        paragraph.add_run(layout.footer.text)
    numbering = layout.footer.page_numbering
    if numbering.enabled:
        if layout.footer.text:
            paragraph.add_run(" - ")
        parts = re.split(r"(\{page\}|\{pages\})", numbering.format)
        for part in parts:
            if part == "{page}":
                _add_field(paragraph, "PAGE")
            elif part == "{pages}":
                _add_field(paragraph, "NUMPAGES")
            elif part:
                paragraph.add_run(part)


def _render_paper(document, request: ExportRenderRequest, layout: TemplateLayoutV1) -> None:
    font = layout.typography.font_family
    for kind, number, item in _iter_questions(request.content):
        if kind == "section":
            section_id = str(item.get("id") or number)
            title = str(item.get("title") or f"Section {section_id}")
            heading = document.add_paragraph(style="Heading 1")
            heading.paragraph_format.space_before = Mm(layout.spacing.section_gap_mm)
            heading.add_run(title)
            if item.get("marks") not in {None, ""}:
                heading.add_run(f"    {item['marks']} marks")
            continue
        question = item
        qtype = str(question["qtype"])
        marks = str(question.get("marks", ""))
        label = document.add_paragraph(style=f"Question Type {qtype}")
        label.add_run(
            f"{number}.  {_TYPE_LABELS[qtype].upper()}    "
            f"[{marks} mark{'s' if marks != '1' else ''}]"
        )
        stem = document.add_paragraph()
        stem.paragraph_format.left_indent = Mm(5)
        stem.paragraph_format.keep_with_next = qtype in {"assertion_reason", "match"}
        _append_rich_text(stem, question.get("stem", ""), font=font)
        _render_question_body(document, question, font)


def _format_structured(value: Any) -> str:
    if value is None:
        return "Not provided"
    if isinstance(value, Mapping):
        return "; ".join(
            f"{str(key).replace('_', ' ').title()}: {_format_structured(item)}"
            for key, item in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return ", ".join(_format_structured(item) for item in value)
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return json.dumps(value, default=str, sort_keys=True)


def _answer_lookup(answer_key: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    if not isinstance(answer_key, Mapping):
        raise DocxRenderError("answer-key export requires PaperVersion.answer_key")
    questions = answer_key.get("questions")
    if not isinstance(questions, Sequence) or isinstance(questions, (str, bytes)):
        raise DocxRenderError("PaperVersion.answer_key.questions must be a list")
    result: dict[str, Mapping[str, Any]] = {}
    for item in questions:
        if not isinstance(item, Mapping):
            continue
        for key in (item.get("slotId"), item.get("questionId")):
            if key:
                result[str(key)] = item
    return result


def _render_multipart_answers(
    document,
    *,
    question: Mapping[str, Any],
    answer: Mapping[str, Any],
    font: str,
) -> None:
    options = question.get("options")
    content_items = (
        options.get("sub_questions") or options.get("subQuestions")
        if isinstance(options, Mapping)
        else None
    )
    answer_items = answer.get("subQuestions")
    if (
        not isinstance(content_items, Sequence)
        or isinstance(content_items, (str, bytes))
        or not isinstance(answer_items, Sequence)
        or isinstance(answer_items, (str, bytes))
        or len(content_items) != len(answer_items)
    ):
        raise DocxRenderError(
            "multipart answer key must match every persisted sub-question"
        )
    by_id = {
        str(item.get("subQuestionId")): item
        for item in answer_items
        if isinstance(item, Mapping) and item.get("subQuestionId")
    }
    for position, content_item in enumerate(content_items, start=1):
        if not isinstance(content_item, Mapping):
            raise DocxRenderError("multipart paper content has an invalid sub-question")
        subquestion_id = str(content_item.get("subQuestionId") or "")
        key_item = by_id.get(subquestion_id)
        if key_item is None:
            raise DocxRenderError(
                f"answer key has no entry for sub-question {subquestion_id or position}"
            )
        sub_stem = document.add_paragraph()
        sub_stem.paragraph_format.keep_with_next = True
        sub_stem.add_run(f"{position}. ").bold = True
        _append_rich_text(sub_stem, content_item.get("stem", ""), font=font)
        answer_paragraph = document.add_paragraph()
        answer_paragraph.paragraph_format.keep_with_next = (
            key_item.get("rubric") is not None
        )
        answer_run = answer_paragraph.add_run("Answer  ")
        answer_run.bold = True
        _set_font(answer_run, font)
        _append_rich_text(
            answer_paragraph,
            _format_structured(key_item.get("answer")),
            font=font,
        )
        if key_item.get("rubric") is not None:
            rubric_paragraph = document.add_paragraph()
            rubric_run = rubric_paragraph.add_run("Rubric  ")
            rubric_run.bold = True
            _set_font(rubric_run, font)
            _append_rich_text(
                rubric_paragraph,
                _format_structured(key_item.get("rubric")),
                font=font,
            )


def _render_answer_key(document, request: ExportRenderRequest, layout: TemplateLayoutV1) -> None:
    answers = _answer_lookup(request.answer_key)
    font = layout.typography.font_family
    for kind, number, item in _iter_questions(request.content):
        if kind == "section":
            section_id = str(item.get("id") or number)
            title = str(item.get("title") or f"Section {section_id}")
            document.add_paragraph(title, style="Heading 1")
            continue
        question = item
        qtype = str(question["qtype"])
        answer = answers.get(str(question.get("slotId"))) or answers.get(
            str(question.get("id"))
        )
        if answer is None:
            raise DocxRenderError(
                f"answer key has no entry for question {question.get('id') or number}"
            )
        label = document.add_paragraph(style=f"Question Type {qtype}")
        label.add_run(f"{number}.  {_TYPE_LABELS[qtype].upper()}")
        stem = document.add_paragraph()
        stem.paragraph_format.keep_with_next = True
        _append_rich_text(stem, question.get("stem", ""), font=font)
        if qtype in {"case_study", "source_based"}:
            _render_multipart_answers(
                document,
                question=question,
                answer=answer,
                font=font,
            )
            continue
        answer_paragraph = document.add_paragraph()
        answer_paragraph.paragraph_format.keep_with_next = True
        answer_run = answer_paragraph.add_run("Answer  ")
        answer_run.bold = True
        _set_font(answer_run, font)
        _append_rich_text(
            answer_paragraph,
            _format_structured(answer.get("answer")),
            font=font,
        )
        rubric_paragraph = document.add_paragraph()
        rubric_run = rubric_paragraph.add_run("Rubric  ")
        rubric_run.bold = True
        _set_font(rubric_run, font)
        _append_rich_text(
            rubric_paragraph,
            _format_structured(answer.get("rubric")),
            font=font,
        )


class DocxRenderer(ExportRenderer):
    """Render paper or answer-key DOCX from the same immutable version lineage."""

    @property
    def format(self) -> str:
        return "docx"

    def render(self, request: ExportRenderRequest) -> RenderedArtifact:
        if request.format != self.format:
            raise ValueError(f"DOCX renderer cannot render {request.format!r}")
        if request.artifact_type not in {"paper", "answer_key"}:
            raise ValueError(f"unsupported DOCX artifact type: {request.artifact_type!r}")
        layout = TemplateLayoutV1.model_validate(request.template_layout)
        document = Document()
        _configure_page(document, layout)
        _style_document(document, layout)
        document.core_properties.title = request.title
        document.core_properties.subject = request.artifact_type
        document.core_properties.identifier = str(request.paper_version_id)
        if layout.header.enabled:
            _render_header(document, request, layout)
        if request.artifact_type == "paper":
            _render_paper(document, request, layout)
        else:
            _render_answer_key(document, request, layout)
        _render_footer(document, layout)

        output = BytesIO()
        document.save(output)
        content = output.getvalue()
        try:
            with ZipFile(BytesIO(content)) as package:
                document_xml = package.read("word/document.xml")
        except (BadZipFile, KeyError) as exc:
            raise DocxRenderError("python-docx did not produce a valid OOXML package") from exc
        if b"<w:document" not in document_xml or len(content) < 1000:
            raise DocxRenderError("python-docx produced an incomplete OOXML document")
        return RenderedArtifact(
            format="docx",
            media_type=DOCX_MEDIA_TYPE,
            extension="docx",
            content=content,
            artifact_type=request.artifact_type,
        )


__all__ = ["DOCX_MEDIA_TYPE", "DocxRenderError", "DocxRenderer"]
