import { z } from "zod";

const alignmentSchema = z.enum(["left", "center", "right"]);
const hexColorSchema = z.string().regex(/^#[0-9A-Fa-f]{6}$/, "Use a six-digit hex colour, for example #0F766E.");

export const templateLayoutSchema = z.object({
  schemaVersion: z.literal(1),
  page: z.object({
    size: z.enum(["A4", "Letter"]),
    orientation: z.enum(["portrait", "landscape"]),
    marginsMm: z.object({
      top: z.number().min(5).max(50),
      right: z.number().min(5).max(50),
      bottom: z.number().min(5).max(50),
      left: z.number().min(5).max(50),
    }),
  }),
  branding: z.object({
    primaryColor: hexColorSchema,
    secondaryColor: hexColorSchema,
    instituteName: z.string().trim().min(1, "Institute name is required.").max(160),
    logo: z.object({
      url: z.string().max(2048).nullable(),
      placement: alignmentSchema,
      maxHeightMm: z.number().min(5).max(40),
    }),
    watermark: z.object({
      text: z.string().max(80).nullable(),
      opacity: z.number().min(0).max(0.3),
    }),
  }),
  typography: z.object({
    fontFamily: z.enum(["Arial", "Georgia", "Inter", "Times New Roman"]),
    bodySizePt: z.number().min(8).max(16),
    headingScale: z.number().min(1.1).max(2),
    lineHeight: z.number().min(1.1).max(2),
  }),
  spacing: z.object({
    sectionGapMm: z.number().min(2).max(24),
    questionGapMm: z.number().min(1).max(16),
  }),
  header: z.object({
    enabled: z.boolean(),
    showLogo: z.boolean(),
    showInstituteName: z.boolean(),
    titleAlignment: alignmentSchema,
    metadata: z.array(z.enum(["examName", "date", "duration", "totalMarks"])).max(4),
  }),
  footer: z.object({
    enabled: z.boolean(),
    text: z.string().max(240),
    pageNumbering: z.object({
      enabled: z.boolean(),
      format: z.string().min(1, "Page-number format is required.").max(80)
        .refine((value) => value.includes("{page}"), "Include the {page} token."),
      alignment: alignmentSchema,
    }),
  }),
});

export const templateFormSchema = z.object({
  name: z.string().trim().min(1, "Template name is required.").max(200),
  layout: templateLayoutSchema,
});

export type TemplateFormValues = z.infer<typeof templateFormSchema>;
