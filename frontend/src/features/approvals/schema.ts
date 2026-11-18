import { z } from "zod";

export const rejectionSchema = z.object({
  comment: z.string().trim().min(1, "A rejection comment is required.").max(10_000),
});

export const approvalCommentSchema = z.object({
  slotId: z.string().trim().max(200).optional(),
  body: z.string().trim().min(1, "Enter a review comment.").max(10_000),
});

export type RejectionValues = z.infer<typeof rejectionSchema>;
export type ApprovalCommentValues = z.infer<typeof approvalCommentSchema>;

