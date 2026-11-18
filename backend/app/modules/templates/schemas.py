"""Validated v1 layout contract and API DTOs for templates."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal, Optional

from pydantic import ConfigDict, Field, model_validator

from app.modules.auth.schemas import AuthRequest, AuthResponse

TemplateStatus = Literal["draft", "active", "archived"]
HexColor = str


class LayoutRequest(AuthRequest):
    pass


class LayoutResponse(AuthResponse):
    pass


class MarginsMm(LayoutRequest):
    top: float = Field(default=16, ge=5, le=50)
    right: float = Field(default=14, ge=5, le=50)
    bottom: float = Field(default=16, ge=5, le=50)
    left: float = Field(default=14, ge=5, le=50)


class PageConfig(LayoutRequest):
    size: Literal["A4", "Letter"] = "A4"
    orientation: Literal["portrait", "landscape"] = "portrait"
    margins_mm: MarginsMm = Field(default_factory=MarginsMm)


class LogoConfig(LayoutRequest):
    url: Optional[str] = Field(default=None, max_length=2048)
    placement: Literal["left", "center", "right"] = "left"
    max_height_mm: float = Field(default=18, ge=5, le=40)


class WatermarkConfig(LayoutRequest):
    text: Optional[str] = Field(default=None, max_length=80)
    opacity: float = Field(default=0.08, ge=0, le=0.3)


class BrandingConfig(LayoutRequest):
    primary_color: HexColor = Field(default="#0F766E", pattern=r"^#[0-9A-Fa-f]{6}$")
    secondary_color: HexColor = Field(default="#F59E0B", pattern=r"^#[0-9A-Fa-f]{6}$")
    institute_name: str = Field(default="PaperGen Academy", min_length=1, max_length=160)
    logo: LogoConfig = Field(default_factory=LogoConfig)
    watermark: WatermarkConfig = Field(default_factory=WatermarkConfig)


class TypographyConfig(LayoutRequest):
    font_family: Literal["Arial", "Georgia", "Inter", "Times New Roman"] = "Arial"
    body_size_pt: float = Field(default=11, ge=8, le=16)
    heading_scale: float = Field(default=1.35, ge=1.1, le=2)
    line_height: float = Field(default=1.45, ge=1.1, le=2)


class SpacingConfig(LayoutRequest):
    section_gap_mm: float = Field(default=8, ge=2, le=24)
    question_gap_mm: float = Field(default=4, ge=1, le=16)


class HeaderConfig(LayoutRequest):
    enabled: bool = True
    show_logo: bool = True
    show_institute_name: bool = True
    title_alignment: Literal["left", "center", "right"] = "center"
    metadata: list[Literal["examName", "date", "duration", "totalMarks"]] = Field(
        default_factory=lambda: ["examName", "date", "duration", "totalMarks"],
        max_length=4,
    )


class PageNumberingConfig(LayoutRequest):
    enabled: bool = True
    format: str = Field(default="Page {page} of {pages}", min_length=1, max_length=80)
    alignment: Literal["left", "center", "right"] = "right"

    @model_validator(mode="after")
    def validate_tokens(self) -> "PageNumberingConfig":
        if "{page}" not in self.format:
            raise ValueError("page numbering format must contain {page}")
        return self


class FooterConfig(LayoutRequest):
    enabled: bool = True
    text: str = Field(default="", max_length=240)
    page_numbering: PageNumberingConfig = Field(default_factory=PageNumberingConfig)


class TemplateLayoutV1(LayoutRequest):
    schema_version: Literal[1] = 1
    page: PageConfig = Field(default_factory=PageConfig)
    branding: BrandingConfig = Field(default_factory=BrandingConfig)
    typography: TypographyConfig = Field(default_factory=TypographyConfig)
    spacing: SpacingConfig = Field(default_factory=SpacingConfig)
    header: HeaderConfig = Field(default_factory=HeaderConfig)
    footer: FooterConfig = Field(default_factory=FooterConfig)


class TemplateCreateIn(AuthRequest):
    name: str = Field(min_length=1, max_length=200)
    layout: TemplateLayoutV1 = Field(default_factory=TemplateLayoutV1)


class TemplateUpdateIn(AuthRequest):
    row_version: int = Field(ge=1)
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    layout: Optional[TemplateLayoutV1] = None

    @model_validator(mode="after")
    def require_change(self) -> "TemplateUpdateIn":
        if self.name is None and self.layout is None:
            raise ValueError("at least one of name or layout must be provided")
        return self


class TemplateActionIn(AuthRequest):
    row_version: int = Field(ge=1)


class TemplateOut(AuthResponse):
    model_config = ConfigDict(from_attributes=True, alias_generator=AuthResponse.model_config["alias_generator"], populate_by_name=True)

    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    layout: TemplateLayoutV1
    status: TemplateStatus
    is_default: bool
    source_template_id: Optional[uuid.UUID] = None
    created_by: Optional[uuid.UUID] = None
    row_version: int
    created_at: dt.datetime
    updated_at: dt.datetime


class TemplatePreviewOut(AuthResponse):
    template_id: uuid.UUID
    sample_content_version: Literal["template-preview-v1"] = "template-preview-v1"
    fidelity: Literal["html-approximation"] = "html-approximation"
    html: str

