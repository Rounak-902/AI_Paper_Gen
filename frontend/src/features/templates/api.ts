import { apiClient, apiClientWithMeta } from "@/services/api-client";
import type { PaperTemplate, TemplateLayout, TemplatePreview, TemplateStatus } from "@/features/templates/types";

const base = (orgId: string) => `/api/v1/orgs/${orgId}/templates`;

export function listTemplates(orgId: string, page: number, pageSize: number, status?: TemplateStatus, signal?: AbortSignal) {
  const query = new URLSearchParams({ page: String(page), pageSize: String(pageSize) });
  if (status) query.set("status", status);
  return apiClientWithMeta<PaperTemplate[]>(`${base(orgId)}?${query}`, { signal });
}

export function createTemplate(orgId: string, name: string, layout: TemplateLayout) {
  return apiClient<PaperTemplate>(base(orgId), { method: "POST", body: { name, layout } });
}

export function getTemplate(orgId: string, templateId: string, signal?: AbortSignal) {
  return apiClient<PaperTemplate>(`${base(orgId)}/${templateId}`, { signal });
}

export function updateTemplate(orgId: string, template: PaperTemplate, name: string, layout: TemplateLayout) {
  return apiClient<PaperTemplate>(`${base(orgId)}/${template.id}`, {
    method: "PATCH",
    body: { rowVersion: template.rowVersion, name, layout },
  });
}

export function activateTemplate(orgId: string, template: PaperTemplate) {
  return action(orgId, template, "activate");
}

export function archiveTemplate(orgId: string, template: PaperTemplate) {
  return action(orgId, template, "archive");
}

export function setDefaultTemplate(orgId: string, template: PaperTemplate) {
  return action(orgId, template, "default");
}

export function previewTemplate(orgId: string, templateId: string) {
  return apiClient<TemplatePreview>(`${base(orgId)}/${templateId}/preview`, { method: "POST" });
}

function action(orgId: string, template: PaperTemplate, actionName: string) {
  return apiClient<PaperTemplate>(`${base(orgId)}/${template.id}/${actionName}`, {
    method: "POST",
    body: { rowVersion: template.rowVersion },
  });
}
