"""P6 export contracts, all-type PDF/DOCX rendering, and storage keys."""

from __future__ import annotations

import base64
import copy
import re
import struct
import uuid
import zlib
from zipfile import ZipFile
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from io import BytesIO

from docx import Document
from pypdf import PdfReader

from app.models.question import QUESTION_TYPES
from app.modules.ai.evaluation import load_curriculum_bank_golden_set
from app.modules.assembly.projection import project_version_pair
from app.modules.exports.contracts import (
    ExportRenderRequest,
    ExportRenderer,
    RenderedArtifact,
    RendererRegistry,
)
from app.modules.exports.html import compose_paper_html
from app.modules.exports.docx_renderer import DocxRenderer
from app.modules.exports.pdf_renderer import PlaywrightPdfRenderer
from app.modules.exports.schemas import ExportCreateIn
from app.modules.exports.service import export_object_key


def _layout() -> dict:
    return {
        "schemaVersion": 1,
        "page": {
            "size": "A4",
            "orientation": "portrait",
            "marginsMm": {"top": 14, "right": 13, "bottom": 18, "left": 13},
        },
        "branding": {
            "primaryColor": "#0F766E",
            "secondaryColor": "#F59E0B",
            "instituteName": "PaperGen Verification Academy",
            "logo": {"url": None, "placement": "left", "maxHeightMm": 18},
            "watermark": {"text": "VERIFIED", "opacity": 0.05},
        },
        "typography": {
            "fontFamily": "Arial",
            "bodySizePt": 10,
            "headingScale": 1.3,
            "lineHeight": 1.35,
        },
        "spacing": {"sectionGapMm": 7, "questionGapMm": 3},
        "header": {
            "enabled": True,
            "showLogo": False,
            "showInstituteName": True,
            "titleAlignment": "center",
            "metadata": ["examName", "duration", "totalMarks"],
        },
        "footer": {
            "enabled": True,
            "text": "P6 export renderer evidence",
            "pageNumbering": {
                "enabled": True,
                "format": "Page {page} of {pages}",
                "alignment": "right",
            },
        },
    }


def all_type_request() -> ExportRenderRequest:
    by_type = {}
    for case in load_curriculum_bank_golden_set():
        by_type.setdefault(case.question.qtype, case.question.model_dump(mode="json"))
    assert set(by_type) == set(QUESTION_TYPES) - {"diagram_based"}

    diagram = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="500" height="180">'
        '<rect width="500" height="180" fill="#ecfeff"/>'
        '<circle cx="100" cy="90" r="45" fill="#0f766e"/>'
        '<path d="M150 90 H350" stroke="#f59e0b" stroke-width="8"/>'
        '<polygon points="350,70 400,90 350,110" fill="#f59e0b"/>'
        '<text x="35" y="165" font-size="20">Cell A to Cell B</text></svg>'
    )
    data_uri = "data:image/svg+xml;base64," + base64.b64encode(diagram.encode()).decode()
    by_type["diagram_based"] = {
        "qtype": "diagram_based",
        "marks": "2",
        "stem": "Identify the process shown in the labelled diagram.",
        "options": {"assetDataUri": data_uri, "alt": "Cell A transfers material to Cell B"},
        "answer": {"text": "Diffusion"},
        "rubric": {"labels": ["Cell A", "Cell B"]},
    }
    by_type["numerical"]["stem"] = (
        r"Use $E=mc^2$ and evaluate $$\frac{1}{2}mv^2$$ for the given values."
    )
    questions = []
    answer_entries = []
    for index, qtype in enumerate(QUESTION_TYPES, start=1):
        question = dict(by_type[qtype])
        question.update(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"p6-t2:{qtype}")),
            slotId=f"slot-{index:02d}",
            sectionId="all-types",
        )
        version_question, answer_entry = project_version_pair(question)
        questions.append(version_question)
        answer_entries.append(answer_entry)
    answer_key = {"questions": answer_entries}
    paper_id = uuid.uuid4()
    return ExportRenderRequest(
        format="pdf",
        paper_id=paper_id,
        paper_version_id=uuid.uuid4(),
        template_id=uuid.uuid4(),
        title="All 13 Question Types - Full Paper",
        version_label="Verification Set A",
        version_created_at=datetime(2026, 9, 26, tzinfo=timezone.utc),
        duration_minutes=180,
        total_marks=Decimal(sum(Decimal(str(item["marks"])) for item in questions)),
        content={
            "schemaVersion": 1,
            "sections": [
                {
                    "id": "all-types",
                    "title": "Canonical Golden-Bank Type Coverage",
                    "marks": str(sum(Decimal(str(item["marks"])) for item in questions)),
                    "questions": questions,
                }
            ],
            "assembly": {"engine": "cp-sat", "solverOutcome": "feasible"},
        },
        template_layout=_layout(),
        artifact_type="paper",
        answer_key=answer_key,
    )


def _docx_request(*, artifact_type: str) -> ExportRenderRequest:
    request = all_type_request()
    content = copy.deepcopy(request.content)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    width, height = 120, 40
    rows = b"".join(b"\x00" + bytes((15, 118, 110)) * width for _ in range(height))
    png_bytes = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )
    png = base64.b64encode(png_bytes).decode("ascii")
    for section in content["sections"]:
        for question in section["questions"]:
            if question["qtype"] == "diagram_based":
                question["options"]["assetDataUri"] = "data:image/png;base64," + png
    return replace(
        request,
        format="docx",
        artifact_type=artifact_type,
        content=content,
    )


def _document_text(document) -> str:
    values = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        values.extend(cell.text for row in table.rows for cell in row.cells)
    return "\n".join(values)


def test_html_composer_covers_exact_canonical_question_type_list() -> None:
    html, footer = compose_paper_html(all_type_request())
    rendered_types = re.findall(r'data-question-type="([^"]+)"', html)
    assert rendered_types == list(QUESTION_TYPES)
    assert "math-placeholder" in html
    assert "Cell A transfers material to Cell B" in html
    assert "Biological magnification (biomagnification)." not in html
    assert "Ohm's law is not valid when temperature changes" not in html
    assert "pageNumber" in footer and "totalPages" in footer


def test_external_template_logo_is_rejected_instead_of_silently_fetching() -> None:
    request = all_type_request()
    layout = copy.deepcopy(request.template_layout)
    layout["branding"]["logo"]["url"] = "http://169.254.169.254/latest/meta-data/"
    layout["header"]["showLogo"] = True
    request = replace(request, template_layout=layout)
    try:
        compose_paper_html(request)
    except ValueError as exc:
        assert "embedded data:image URI" in str(exc)
    else:  # pragma: no cover - assertion aid
        raise AssertionError("network logo should have been rejected")


def test_playwright_pdf_is_real_and_katex_is_static() -> None:
    artifact = PlaywrightPdfRenderer(timeout_seconds=120).render(all_type_request())
    assert artifact.content.startswith(b"%PDF-")
    assert len(artifact.content) > 25_000
    assert artifact.rendered_html is not None
    assert 'class="katex"' in artifact.rendered_html
    assert "math-placeholder" not in artifact.rendered_html
    assert "<script" not in artifact.rendered_html
    assert artifact.rendered_html.count("data-question-type=") == 13

    reader = PdfReader(BytesIO(artifact.content))
    assert len(reader.pages) >= 2
    extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
    upper_extracted = extracted.upper()
    for label in (
        "Multiple Choice",
        "True / False",
        "One Word",
        "Fill in the Blank",
        "Short Answer",
        "Long Answer",
        "Assertion-Reason",
        "Case Study",
        "Numerical",
        "Diagram Based",
        "Match the Columns",
        "Very Short Answer",
        "Source Based",
    ):
        assert label.upper() in upper_extracted
    assert "E = mc" in extracted


def test_renderer_registry_accepts_docx_without_contract_or_orchestration_change() -> None:
    class FakeDocxRenderer(ExportRenderer):
        @property
        def format(self) -> str:
            return "docx"

        def render(self, request: ExportRenderRequest) -> RenderedArtifact:
            return RenderedArtifact(
                format="docx",
                media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                extension="docx",
                content=b"PK fake",
            )

    request = replace(all_type_request(), format="docx")
    result = RendererRegistry((FakeDocxRenderer(),)).for_format("docx").render(request)
    assert result.format == "docx"


def test_export_create_defaults_artifact_type_to_paper() -> None:
    payload = ExportCreateIn(templateId=uuid.uuid4(), format="docx")
    assert payload.artifact_type == "paper"


def test_docx_paper_covers_all_types_with_native_omml_and_structure() -> None:
    artifact = DocxRenderer().render(_docx_request(artifact_type="paper"))
    assert artifact.content.startswith(b"PK")
    assert artifact.artifact_type == "paper"
    document = Document(BytesIO(artifact.content))
    extracted = _document_text(document).upper()
    assert "P6 export renderer evidence" in "\n".join(
        paragraph.text for paragraph in document.sections[0].footer.paragraphs
    )
    for qtype, label in (
        ("mcq", "Multiple Choice"),
        ("true_false", "True / False"),
        ("one_word", "One Word"),
        ("fill_blank", "Fill in the Blank"),
        ("short_answer", "Short Answer"),
        ("long_answer", "Long Answer"),
        ("assertion_reason", "Assertion-Reason"),
        ("case_study", "Case Study"),
        ("numerical", "Numerical"),
        ("diagram_based", "Diagram Based"),
        ("match", "Match the Columns"),
        ("very_short_answer", "Very Short Answer"),
        ("source_based", "Source Based"),
    ):
        assert label.upper() in extracted
        assert any(
            paragraph.style.name == f"Question Type {qtype}"
            for paragraph in document.paragraphs
        )
    assert any(
        table.cell(0, 0).text == "Column A" and table.cell(0, 1).text == "Column B"
        for table in document.tables
    )
    assert len(document.inline_shapes) == 1
    assert "BIOLOGICAL MAGNIFICATION (BIOMAGNIFICATION)." not in extracted
    assert "OHM'S LAW IS NOT VALID WHEN TEMPERATURE CHANGES" not in extracted
    assert sum(paragraph.style.name == "Answer Line" for paragraph in document.paragraphs) >= 20
    with ZipFile(BytesIO(artifact.content)) as package:
        document_xml = package.read("word/document.xml")
    assert b"<m:oMath" in document_xml
    assert b"<m:f>" in document_xml
    assert b"<m:sSup>" in document_xml


def test_docx_answer_key_is_separate_and_uses_persisted_answers() -> None:
    paper = DocxRenderer().render(_docx_request(artifact_type="paper"))
    answer_key = DocxRenderer().render(_docx_request(artifact_type="answer_key"))
    assert answer_key.artifact_type == "answer_key"
    assert answer_key.content != paper.content
    document = Document(BytesIO(answer_key.content))
    extracted = _document_text(document)
    assert "Answer Key" in extracted
    assert "Diffusion" in extracted
    assert "Biological magnification (biomagnification)." in extracted
    assert "Ohm's law is not valid when temperature changes" in extracted
    assert "Value: 5.0; Units: Ω" in extracted
    assert "Rubric" in extracted
    assert not any(
        paragraph.style.name == "Answer Line" for paragraph in document.paragraphs
    )


def test_export_key_uses_existing_tenant_namespace_and_immutable_ids() -> None:
    class Job:
        org_id = uuid.UUID("10000000-0000-4000-8000-000000000001")
        paper_version_id = uuid.UUID("20000000-0000-4000-8000-000000000002")
        id = uuid.UUID("30000000-0000-4000-8000-000000000003")
        format = "pdf"
        artifact_type = "paper"

    assert export_object_key(Job()) == (
        "10000000-0000-4000-8000-000000000001/exports/"
        "20000000-0000-4000-8000-000000000002/"
        "paper/30000000-0000-4000-8000-000000000003.pdf"
    )
