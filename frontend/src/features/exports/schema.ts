import { z } from "zod";

export const exportFormSchema = z.object({
  templateId: z.string().uuid("Choose an active template."),
  format: z.enum(["pdf", "docx"]),
  contents: z.enum(["paper", "paper_and_answer_key"]),
});

export type ExportFormValues = z.infer<typeof exportFormSchema>;
