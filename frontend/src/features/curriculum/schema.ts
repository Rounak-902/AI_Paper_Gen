import { z } from "zod";

const nonBlank = (max: number) => z.string().min(1).max(max).refine((value) => /\S/.test(value), "Cannot be blank.");
const isoDate = z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "Use an ISO date such as 2026-04-01.");

const outcomeSchema = z
  .object({
    code: nonBlank(100),
    description: nonBlank(4000),
  })
  .strict();

const lineageSchema = z
  .object({
    academicYear: nonBlank(64),
    className: nonBlank(200),
    subjectName: nonBlank(200),
    chapterSeq: z.number().int(),
  })
  .strict();

const chapterSchema = z
  .object({
    seq: z.number().int(),
    title: nonBlank(500),
    derivedFrom: lineageSchema.optional(),
    learningOutcomes: z.array(outcomeSchema),
  })
  .strict();

const subjectSchema = z
  .object({
    name: nonBlank(200),
    chapters: z.array(chapterSchema).min(1),
  })
  .strict();

const classSchema = z
  .object({
    name: nonBlank(200),
    subjects: z.array(subjectSchema).min(1),
  })
  .strict();

const versionByIdSchema = z
  .object({
    id: z.string().uuid(),
    finalStatus: z.enum(["draft", "active", "superseded"]).optional(),
    supersedeActive: z.boolean().optional(),
  })
  .strict();

const versionByYearSchema = z
  .object({
    academicYear: nonBlank(64),
    label: z.string().max(200).nullable().optional(),
    effectiveFrom: isoDate.nullable().optional(),
    sourceRef: z.string().max(1000).nullable().optional(),
    finalStatus: z.enum(["draft", "active", "superseded"]).optional(),
    supersedeActive: z.boolean().optional(),
  })
  .strict();

const documentSchema = z
  .object({
    schemaVersion: z.literal(1),
    board: z
      .object({
        code: nonBlank(64),
        name: nonBlank(200),
        curriculumVersion: z.union([versionByIdSchema, versionByYearSchema]),
        classes: z.array(classSchema).min(1),
      })
      .strict(),
  })
  .strict();

export type CurriculumDocument = z.infer<typeof documentSchema>;

export type LocalValidationIssue = {
  path: string;
  message: string;
};

export type LocalValidation = {
  document: CurriculumDocument | null;
  issues: LocalValidationIssue[];
  counts: {
    classes: number;
    subjects: number;
    chapters: number;
    outcomes: number;
  };
};

export const SAMPLE_MANIFEST: CurriculumDocument = {
  schemaVersion: 1,
  board: {
    code: "SAMPLE-BOARD",
    name: "Sample National Curriculum Board",
    curriculumVersion: {
      academicYear: "2026-27",
      label: "Sample 2026–27 curriculum",
      effectiveFrom: "2026-04-01",
      sourceRef: "https://example.org/official-curriculum-source",
      finalStatus: "draft",
    },
    classes: [
      {
        name: "Grade 8",
        subjects: [
          {
            name: "Mathematics",
            chapters: [
              {
                seq: 1,
                title: "Rational numbers",
                learningOutcomes: [
                  {
                    code: "SAMPLE.G8.MATH.01.LO1",
                    description: "Represent and compare rational numbers using multiple forms.",
                  },
                  {
                    code: "SAMPLE.G8.MATH.01.LO2",
                    description: "Apply operations on rational numbers to solve contextual problems.",
                  },
                ],
              },
              {
                seq: 2,
                title: "Linear equations",
                learningOutcomes: [
                  {
                    code: "SAMPLE.G8.MATH.02.LO1",
                    description: "Form and solve linear equations in one variable.",
                  },
                ],
              },
            ],
          },
          {
            name: "Science",
            chapters: [
              {
                seq: 1,
                title: "Cells and living systems",
                learningOutcomes: [
                  {
                    code: "SAMPLE.G8.SCI.01.LO1",
                    description: "Explain how specialized cell structures support biological functions.",
                  },
                ],
              },
            ],
          },
        ],
      },
    ],
  },
};

export const SAMPLE_MANIFEST_TEXT = JSON.stringify(SAMPLE_MANIFEST, null, 2);

export function validateManifestText(text: string): LocalValidation {
  if (!text.trim()) {
    return emptyValidation([{ path: "$", message: "Choose a JSON manifest or start from the example." }]);
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (error) {
    return emptyValidation([
      {
        path: "$",
        message: error instanceof SyntaxError ? error.message : "The file is not valid JSON.",
      },
    ]);
  }

  const result = documentSchema.safeParse(parsed);
  if (!result.success) {
    return emptyValidation(
      result.error.issues.map((issue) => ({
        path: toJsonPath(issue.path),
        message: issue.message,
      })),
    );
  }

  const semanticIssues = validateSemantics(result.data);
  return {
    document: semanticIssues.length === 0 ? result.data : null,
    issues: semanticIssues,
    counts: countDocument(result.data),
  };
}

function validateSemantics(document: CurriculumDocument): LocalValidationIssue[] {
  const issues: LocalValidationIssue[] = [];
  const version = document.board.curriculumVersion;
  const finalStatus = version.finalStatus ?? "draft";
  if (version.supersedeActive && finalStatus === "draft") {
    issues.push({
      path: "$.board.curriculumVersion.supersedeActive",
      message: "Cannot supersede an active version while the target remains draft.",
    });
  }

  const targetYear = "academicYear" in version ? version.academicYear : null;
  const classNames = new Set<string>();
  document.board.classes.forEach((schoolClass, classIndex) => {
    const classPath = `$.board.classes[${classIndex}]`;
    if (classNames.has(schoolClass.name)) {
      issues.push({ path: `${classPath}.name`, message: "Class names must be unique within a board." });
    }
    classNames.add(schoolClass.name);

    const subjectNames = new Set<string>();
    schoolClass.subjects.forEach((subject, subjectIndex) => {
      const subjectPath = `${classPath}.subjects[${subjectIndex}]`;
      if (subjectNames.has(subject.name)) {
        issues.push({ path: `${subjectPath}.name`, message: "Subject names must be unique within this class." });
      }
      subjectNames.add(subject.name);

      const chapterSeqs = new Set<number>();
      subject.chapters.forEach((chapter, chapterIndex) => {
        const chapterPath = `${subjectPath}.chapters[${chapterIndex}]`;
        if (chapterSeqs.has(chapter.seq)) {
          issues.push({ path: `${chapterPath}.seq`, message: "Chapter sequence must be unique within this subject." });
        }
        chapterSeqs.add(chapter.seq);

        if (chapter.derivedFrom && targetYear && chapter.derivedFrom.academicYear === targetYear) {
          issues.push({
            path: `${chapterPath}.derivedFrom.academicYear`,
            message: "Lineage must point to a different, earlier curriculum version.",
          });
        }

        const outcomeCodes = new Set<string>();
        chapter.learningOutcomes.forEach((outcome, outcomeIndex) => {
          if (outcomeCodes.has(outcome.code)) {
            issues.push({
              path: `${chapterPath}.learningOutcomes[${outcomeIndex}].code`,
              message: "Learning-outcome codes must be unique within this chapter.",
            });
          }
          outcomeCodes.add(outcome.code);
        });
      });
    });
  });
  return issues;
}

function countDocument(document: CurriculumDocument) {
  const subjects = document.board.classes.flatMap((schoolClass) => schoolClass.subjects);
  const chapters = subjects.flatMap((subject) => subject.chapters);
  return {
    classes: document.board.classes.length,
    subjects: subjects.length,
    chapters: chapters.length,
    outcomes: chapters.reduce((total, chapter) => total + chapter.learningOutcomes.length, 0),
  };
}

function emptyValidation(issues: LocalValidationIssue[]): LocalValidation {
  return {
    document: null,
    issues,
    counts: { classes: 0, subjects: 0, chapters: 0, outcomes: 0 },
  };
}

function toJsonPath(path: PropertyKey[]) {
  return path.reduce<string>((result, part) => {
    if (typeof part === "number") {
      return `${result}[${part}]`;
    }
    return /^[A-Za-z_$][\w$]*$/.test(String(part))
      ? `${result}.${String(part)}`
      : `${result}[${JSON.stringify(String(part))}]`;
  }, "$" );
}
