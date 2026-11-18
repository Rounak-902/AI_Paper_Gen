"""Playwright/Chromium PDF backend with server-side KaTeX preprocessing."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from app.modules.exports.contracts import ExportRenderRequest, ExportRenderer, RenderedArtifact
from app.modules.exports.html import compose_paper_html


class PdfRenderError(RuntimeError):
    pass


class PlaywrightPdfRenderer(ExportRenderer):
    """Render static HTML through Playwright-controlled headless Chromium."""

    def __init__(self, *, node_binary: str = "node", timeout_seconds: int = 120) -> None:
        self._node_binary = node_binary
        self._timeout_seconds = timeout_seconds
        self._script = Path(__file__).resolve().parents[3] / "renderer" / "render-pdf.mjs"

    @property
    def format(self) -> str:
        return "pdf"

    def render(self, request: ExportRenderRequest) -> RenderedArtifact:
        if request.format != self.format:
            raise ValueError(f"PDF renderer cannot render {request.format!r}")
        source_html, footer_html = compose_paper_html(request)
        layout = request.template_layout["page"]
        with tempfile.TemporaryDirectory(prefix="papergen-export-") as temp:
            root = Path(temp)
            input_path = root / "render.json"
            pdf_path = root / "paper.pdf"
            html_path = root / "paper.final.html"
            input_path.write_text(
                json.dumps(
                    {
                        "html": source_html,
                        "footerHtml": footer_html,
                        "pageSize": layout["size"],
                        "landscape": layout["orientation"] == "landscape",
                    }
                ),
                encoding="utf-8",
            )
            try:
                completed = subprocess.run(
                    [
                        self._node_binary,
                        str(self._script),
                        "--input",
                        str(input_path),
                        "--output",
                        str(pdf_path),
                        "--html-output",
                        str(html_path),
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout_seconds,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise PdfRenderError(f"renderer process failed: {exc}") from exc
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout).strip()[-2000:]
                raise PdfRenderError(f"renderer exited {completed.returncode}: {detail}")
            pdf = pdf_path.read_bytes()
            if not pdf.startswith(b"%PDF-") or len(pdf) < 1000:
                raise PdfRenderError("Chromium did not produce a valid PDF artifact")
            rendered_html = html_path.read_text(encoding="utf-8")
            return RenderedArtifact(
                format="pdf",
                media_type="application/pdf",
                extension="pdf",
                content=pdf,
                rendered_html=rendered_html,
            )


__all__ = ["PdfRenderError", "PlaywrightPdfRenderer"]
