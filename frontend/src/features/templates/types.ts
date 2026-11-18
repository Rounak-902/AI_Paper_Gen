import type { z } from "zod";
import { templateLayoutSchema } from "@/features/templates/schema";

export type TemplateStatus = "draft" | "active" | "archived";

export type TemplateLayout = z.infer<typeof templateLayoutSchema>;

export type PaperTemplate = {
  id: string;
  orgId: string;
  name: string;
  layout: TemplateLayout;
  status: TemplateStatus;
  isDefault: boolean;
  sourceTemplateId: string | null;
  createdBy: string | null;
  rowVersion: number;
  createdAt: string;
  updatedAt: string;
};

export type TemplatePreview = {
  templateId: string;
  sampleContentVersion: "template-preview-v1";
  fidelity: "html-approximation";
  html: string;
};

export const DEFAULT_TEMPLATE_LAYOUT: TemplateLayout = {
  schemaVersion: 1,
  page: { size: "A4", orientation: "portrait", marginsMm: { top: 16, right: 14, bottom: 16, left: 14 } },
  branding: {
    primaryColor: "#0F766E",
    secondaryColor: "#F59E0B",
    instituteName: "PaperGen Academy",
    logo: { url: null, placement: "left", maxHeightMm: 18 },
    watermark: { text: null, opacity: 0.08 },
  },
  typography: { fontFamily: "Arial", bodySizePt: 11, headingScale: 1.35, lineHeight: 1.45 },
  spacing: { sectionGapMm: 8, questionGapMm: 4 },
  header: {
    enabled: true,
    showLogo: true,
    showInstituteName: true,
    titleAlignment: "center",
    metadata: ["examName", "date", "duration", "totalMarks"],
  },
  footer: {
    enabled: true,
    text: "",
    pageNumbering: { enabled: true, format: "Page {page} of {pages}", alignment: "right" },
  },
};
