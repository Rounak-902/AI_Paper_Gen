"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Loader2, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { Controller, useForm } from "react-hook-form";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { createTemplate, getTemplate, updateTemplate } from "@/features/templates/api";
import { templateFormSchema, type TemplateFormValues } from "@/features/templates/schema";
import { DEFAULT_TEMPLATE_LAYOUT, type PaperTemplate } from "@/features/templates/types";
import { ApiError } from "@/services/api-client";

const METADATA = ["examName", "date", "duration", "totalMarks"] as const;

export function TemplateEditorDialog({ orgId, open, template, onOpenChange, onSaved }: {
  orgId: string;
  open: boolean;
  template: PaperTemplate | null;
  onOpenChange: (open: boolean) => void;
  onSaved: (template: PaperTemplate, outcome: "created" | "updated" | "cloned" | "conflict-copy") => void;
}) {
  const queryClient = useQueryClient();
  const [conflict, setConflict] = useState(false);
  const form = useForm<TemplateFormValues>({
    defaultValues: valuesFor(template),
    resolver: zodResolver(templateFormSchema),
  });

  useEffect(() => {
    if (!open) return;
    form.reset(valuesFor(template));
    setConflict(false);
  }, [form, open, template]);

  const saveMutation = useMutation({
    mutationFn: (values: TemplateFormValues) => template
      ? updateTemplate(orgId, template, values.name.trim(), values.layout)
      : createTemplate(orgId, values.name.trim(), values.layout),
    onSuccess: async (saved) => {
      await queryClient.invalidateQueries({ queryKey: [orgId, "templates"] });
      onOpenChange(false);
      onSaved(saved, template?.status === "active" ? "cloned" : template ? "updated" : "created");
    },
    onError: (error) => {
      if (isOptimisticConflict(error)) setConflict(true);
    },
  });
  const reloadMutation = useMutation({
    mutationFn: () => getTemplate(orgId, template!.id),
    onSuccess: (latest) => {
      form.reset(valuesFor(latest));
      setConflict(false);
    },
  });
  const copyMutation = useMutation({
    mutationFn: async (values: TemplateFormValues) => {
      const latest = await getTemplate(orgId, template!.id);
      if (latest.status === "active") {
        return updateTemplate(orgId, latest, values.name.trim(), values.layout);
      }
      return createTemplate(orgId, conflictCopyName(values.name), values.layout);
    },
    onSuccess: async (saved) => {
      await queryClient.invalidateQueries({ queryKey: [orgId, "templates"] });
      onOpenChange(false);
      onSaved(saved, "conflict-copy");
    },
  });
  const busy = saveMutation.isPending || reloadMutation.isPending || copyMutation.isPending;
  const errors = form.formState.errors;

  const submit = form.handleSubmit((values) => {
    setConflict(false);
    saveMutation.mutate(values);
  });

  return (
    <Dialog onOpenChange={(nextOpen) => !busy && onOpenChange(nextOpen)} open={open}>
      <DialogContent className="max-h-[94vh] max-w-4xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{dialogTitle(template)}</DialogTitle>
          <DialogDescription>
            {template?.status === "active"
              ? "This active version is immutable. Saving creates a linked draft and leaves the active template unchanged."
              : "Configure the stored layout and branding contract. Paper sections and question structure are intentionally outside this template."}
          </DialogDescription>
        </DialogHeader>

        <form className="space-y-6" onSubmit={submit}>
          {conflict ? (
            <Alert className="border-amber-500/50 bg-amber-500/10 text-amber-950" data-template-conflict>
              <AlertTriangle className="h-4 w-4" />
              <div className="space-y-3">
                <p className="font-medium">This template changed after you opened it.</p>
                <p className="text-sm">Your form is preserved. Reload the latest server version, or keep these values in a separate draft. No server value will be overwritten automatically.</p>
                <div className="flex flex-wrap gap-2">
                  <Button disabled={busy} onClick={() => reloadMutation.mutate()} size="sm" type="button" variant="outline">
                    {reloadMutation.isPending ? <Loader2 className="animate-spin" /> : <RefreshCw />} Reload server version
                  </Button>
                  <Button disabled={busy} onClick={form.handleSubmit((values) => copyMutation.mutate(values))} size="sm" type="button">
                    {copyMutation.isPending ? <Loader2 className="animate-spin" /> : null} Save my version as draft
                  </Button>
                </div>
              </div>
            </Alert>
          ) : null}

          <EditorSection description="How this reusable style is identified." title="Identity">
            <Field error={errors.name?.message} id="template-name" label="Template name">
              <Input id="template-name" maxLength={200} {...form.register("name")} />
            </Field>
            <Field error={errors.layout?.branding?.instituteName?.message} id="institute-name" label="Institute name">
              <Input id="institute-name" maxLength={160} {...form.register("layout.branding.instituteName")} />
            </Field>
          </EditorSection>

          <EditorSection description="Colours, logo, and watermark used by rendered papers." title="Branding">
            <ColourField error={errors.layout?.branding?.primaryColor?.message} form={form} label="Primary colour" name="layout.branding.primaryColor" />
            <ColourField error={errors.layout?.branding?.secondaryColor?.message} form={form} label="Accent colour" name="layout.branding.secondaryColor" />
            <Field error={errors.layout?.branding?.logo?.url?.message} id="logo-url" label="Logo URL" hint="Optional, up to 2,048 characters.">
              <Controller control={form.control} name="layout.branding.logo.url" render={({ field }) => <Input {...field} id="logo-url" maxLength={2048} onChange={(event) => field.onChange(event.target.value || null)} placeholder="https://…" value={field.value ?? ""} />} />
            </Field>
            <SelectField control={form.control} label="Logo placement" name="layout.branding.logo.placement" options={["left", "center", "right"]} />
            <NumberField error={errors.layout?.branding?.logo?.maxHeightMm?.message} form={form} label="Logo max height (mm)" max={40} min={5} name="layout.branding.logo.maxHeightMm" />
            <Field error={errors.layout?.branding?.watermark?.text?.message} id="watermark-text" label="Watermark text" hint="Optional, up to 80 characters.">
              <Controller control={form.control} name="layout.branding.watermark.text" render={({ field }) => <Input {...field} id="watermark-text" maxLength={80} onChange={(event) => field.onChange(event.target.value || null)} value={field.value ?? ""} />} />
            </Field>
            <NumberField error={errors.layout?.branding?.watermark?.opacity?.message} form={form} label="Watermark opacity" max={0.3} min={0} name="layout.branding.watermark.opacity" step={0.01} />
          </EditorSection>

          <EditorSection description="Paper dimensions and printable margins." title="Page">
            <SelectField control={form.control} label="Page size" name="layout.page.size" options={["A4", "Letter"]} />
            <SelectField control={form.control} label="Orientation" name="layout.page.orientation" options={["portrait", "landscape"]} />
            {(["top", "right", "bottom", "left"] as const).map((side) => (
              <NumberField error={errors.layout?.page?.marginsMm?.[side]?.message} form={form} key={side} label={`${capitalize(side)} margin (mm)`} max={50} min={5} name={`layout.page.marginsMm.${side}`} />
            ))}
          </EditorSection>

          <EditorSection description="Font metrics and spacing used throughout the paper." title="Typography & spacing">
            <SelectField control={form.control} label="Font family" name="layout.typography.fontFamily" options={["Arial", "Georgia", "Inter", "Times New Roman"]} />
            <NumberField error={errors.layout?.typography?.bodySizePt?.message} form={form} label="Body size (pt)" max={16} min={8} name="layout.typography.bodySizePt" />
            <NumberField error={errors.layout?.typography?.headingScale?.message} form={form} label="Heading scale" max={2} min={1.1} name="layout.typography.headingScale" step={0.05} />
            <NumberField error={errors.layout?.typography?.lineHeight?.message} form={form} label="Line height" max={2} min={1.1} name="layout.typography.lineHeight" step={0.05} />
            <NumberField error={errors.layout?.spacing?.sectionGapMm?.message} form={form} label="Section gap (mm)" max={24} min={2} name="layout.spacing.sectionGapMm" />
            <NumberField error={errors.layout?.spacing?.questionGapMm?.message} form={form} label="Question gap (mm)" max={16} min={1} name="layout.spacing.questionGapMm" />
          </EditorSection>

          <EditorSection description="Choose what appears above the fixed sample-content preview." title="Header">
            <ToggleField control={form.control} label="Enable header" name="layout.header.enabled" />
            <ToggleField control={form.control} label="Show logo" name="layout.header.showLogo" />
            <ToggleField control={form.control} label="Show institute name" name="layout.header.showInstituteName" />
            <SelectField control={form.control} label="Title alignment" name="layout.header.titleAlignment" options={["left", "center", "right"]} />
            <div className="space-y-3 rounded-md border p-4 sm:col-span-2">
              <p className="text-sm font-medium">Header metadata</p>
              <div className="grid gap-3 sm:grid-cols-2">
                {METADATA.map((item) => <MetadataToggle form={form} key={item} value={item} />)}
              </div>
            </div>
          </EditorSection>

          <EditorSection description="Footer copy and page-number presentation." title="Footer">
            <ToggleField control={form.control} label="Enable footer" name="layout.footer.enabled" />
            <ToggleField control={form.control} label="Show page numbers" name="layout.footer.pageNumbering.enabled" />
            <Field error={errors.layout?.footer?.text?.message} id="footer-text" label="Footer text">
              <Input id="footer-text" maxLength={240} {...form.register("layout.footer.text")} />
            </Field>
            <Field error={errors.layout?.footer?.pageNumbering?.format?.message} id="page-number-format" label="Page-number format" hint="Must include {page}; {pages} is optional.">
              <Input id="page-number-format" maxLength={80} {...form.register("layout.footer.pageNumbering.format")} />
            </Field>
            <SelectField control={form.control} label="Page-number alignment" name="layout.footer.pageNumbering.alignment" options={["left", "center", "right"]} />
          </EditorSection>

          {saveMutation.error && !conflict ? <InlineError message={errorMessage(saveMutation.error)} /> : null}
          {reloadMutation.error ? <InlineError message={errorMessage(reloadMutation.error)} /> : null}
          {copyMutation.error ? <InlineError message={errorMessage(copyMutation.error)} /> : null}

          <DialogFooter>
            <Button disabled={busy} onClick={() => onOpenChange(false)} type="button" variant="ghost">Cancel</Button>
            <Button disabled={busy} type="submit">
              {saveMutation.isPending ? <Loader2 className="animate-spin" /> : null}
              {template?.status === "active" ? "Create draft clone" : template ? "Save changes" : "Create draft"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function EditorSection({ children, description, title }: { children: React.ReactNode; description: string; title: string }) {
  return <section className="rounded-lg border bg-card p-4"><div className="mb-4"><h3 className="font-semibold">{title}</h3><p className="text-sm text-muted-foreground">{description}</p></div><div className="grid gap-4 sm:grid-cols-2">{children}</div></section>;
}

function Field({ children, error, hint, id, label }: { children: React.ReactNode; error?: string; hint?: string; id: string; label: string }) {
  return <div className="space-y-2"><Label htmlFor={id}>{label}</Label>{children}{hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}{error ? <p className="text-xs text-destructive" role="alert">{error}</p> : null}</div>;
}

type Form = ReturnType<typeof useForm<TemplateFormValues>>;

function ColourField({ error, form, label, name }: { error?: string; form: Form; label: string; name: "layout.branding.primaryColor" | "layout.branding.secondaryColor" }) {
  const id = name.replaceAll(".", "-");
  return <Field error={error} id={id} label={label}><div className="flex gap-2"><Input aria-label={`${label} picker`} className="w-16 p-1" type="color" {...form.register(name)} /><Input id={id} maxLength={7} {...form.register(name)} /></div></Field>;
}

function NumberField({ error, form, label, max, min, name, step = 0.1 }: { error?: string; form: Form; label: string; max: number; min: number; name: Parameters<Form["register"]>[0]; step?: number }) {
  const id = String(name).replaceAll(".", "-");
  return <Field error={error} id={id} label={label}><Input id={id} max={max} min={min} step={step} type="number" {...form.register(name, { valueAsNumber: true })} /></Field>;
}

function SelectField({ control, label, name, options }: { control: Form["control"]; label: string; name: Parameters<Form["register"]>[0]; options: string[] }) {
  const id = String(name).replaceAll(".", "-");
  return <Field id={id} label={label}><Controller control={control} name={name} render={({ field }) => <Select onValueChange={field.onChange} value={String(field.value)}><SelectTrigger id={id}><SelectValue /></SelectTrigger><SelectContent>{options.map((option) => <SelectItem key={option} value={option}>{capitalize(option)}</SelectItem>)}</SelectContent></Select>} /></Field>;
}

function ToggleField({ control, label, name }: { control: Form["control"]; label: string; name: Parameters<Form["register"]>[0] }) {
  const id = String(name).replaceAll(".", "-");
  return <Controller control={control} name={name} render={({ field }) => <label className="flex items-center gap-3 rounded-md border p-3 text-sm" htmlFor={id}><Checkbox checked={Boolean(field.value)} id={id} onCheckedChange={(checked) => field.onChange(checked === true)} /><span>{label}</span></label>} />;
}

function MetadataToggle({ form, value }: { form: Form; value: (typeof METADATA)[number] }) {
  const selected = form.watch("layout.header.metadata");
  const id = `metadata-${value}`;
  return <label className="flex items-center gap-2 text-sm" htmlFor={id}><Checkbox checked={selected.includes(value)} id={id} onCheckedChange={(checked) => form.setValue("layout.header.metadata", checked ? [...selected, value] : selected.filter((item) => item !== value), { shouldDirty: true, shouldValidate: true })} /><span>{metadataLabel(value)}</span></label>;
}

function InlineError({ message }: { message: string }) {
  return <p className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive" role="alert">{message}</p>;
}

function valuesFor(template: PaperTemplate | null): TemplateFormValues {
  return {
    name: template?.name ?? "",
    layout: structuredClone(template?.layout ?? DEFAULT_TEMPLATE_LAYOUT),
  };
}

function dialogTitle(template: PaperTemplate | null) {
  if (!template) return "Create layout template";
  return template.status === "active" ? `Clone ${template.name} to a draft` : `Edit ${template.name}`;
}

function conflictCopyName(name: string) {
  const suffix = ` (Conflict copy ${new Date().toISOString().slice(11, 19).replaceAll(":", "")})`;
  return `${name.trim().slice(0, 200 - suffix.length)}${suffix}`;
}

function isOptimisticConflict(error: unknown) {
  return error instanceof ApiError && error.status === 409 && error.code === "CONCURRENT_MODIFICATION";
}

function errorMessage(error: unknown) {
  if (isOptimisticConflict(error)) return "This template changed elsewhere. Your form has not been discarded.";
  if (error instanceof ApiError && error.status === 409) return error.message || "A template with that name already exists.";
  return error instanceof Error ? error.message : "The template could not be saved.";
}

function capitalize(value: string) {
  return value[0].toUpperCase() + value.slice(1);
}

function metadataLabel(value: (typeof METADATA)[number]) {
  return { examName: "Exam name", date: "Date", duration: "Duration", totalMarks: "Total marks" }[value];
}
