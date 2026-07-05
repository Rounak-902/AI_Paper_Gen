import { useState, useCallback } from 'react';
import { supabase } from '@/lib/supabaseClient';
import { callGeminiWithMedia, parseGeminiJSON, callGemini } from '@/lib/geminiClient';
import type {
  TextbookExtractedQuestion,
  ExtractionSettings,
  ExtractionProgress,
  QuestionVerificationStatus,
  QuestionType,
  Difficulty,
  TextbookSourceType,
} from '@/types/database';

// ── Helpers ─────────────────────────────────────────────────

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result as string;
      // Strip the data:application/pdf;base64, prefix
      const base64 = result.split(',')[1];
      resolve(base64);
    };
    reader.onerror = () => reject(new Error('Failed to read file'));
    reader.readAsDataURL(file);
  });
}

function getVerificationStatus(q: TextbookExtractedQuestion): QuestionVerificationStatus {
  if (!q.question_text || q.question_text.trim().length < 10) return 'needs_review';
  if (!q.question_type) return 'needs_review';
  if (q.question_type === 'MCQ' && (!q.options || q.options.length < 4)) return 'needs_review';
  if (q.textbook_source_type === 'solved_example' && !q.answer_text) return 'needs_review';
  return 'ready';
}

const VALID_QUESTION_TYPES: QuestionType[] = [
  'MCQ', 'Short Answer', 'Long Answer', 'Very Short Answer', 'Numerical',
  'Fill in the Blanks', 'True/False', 'Assertion-Reason', 'Case Study',
  'Match the Following', 'One Word Answer', 'Diagram Based',
];

const VALID_SOURCE_TYPES: TextbookSourceType[] = [
  'exercise', 'miscellaneous', 'solved_example', 'examples', 'intext',
];

const VALID_DIFFICULTIES: Difficulty[] = ['easy', 'medium', 'hard'];

function sanitizeQuestion(raw: Record<string, unknown>): TextbookExtractedQuestion {
  const rawType = String(raw.question_type ?? 'Short Answer');
  const matchedType = VALID_QUESTION_TYPES.find(
    (t) => t.toLowerCase() === rawType.toLowerCase(),
  ) ?? 'Short Answer';

  const rawSource = String(raw.textbook_source_type ?? 'exercise');
  const matchedSource = VALID_SOURCE_TYPES.find(
    (s) => s.toLowerCase() === rawSource.toLowerCase(),
  ) ?? 'exercise';

  const rawDifficulty = String(raw.difficulty ?? 'medium').toLowerCase();
  const matchedDifficulty = VALID_DIFFICULTIES.find(
    (d) => d === rawDifficulty,
  ) ?? 'medium';

  return {
    question_text: String(raw.question_text ?? ''),
    question_type: matchedType,
    textbook_source_type: matchedSource,
    exercise_name: String(raw.exercise_name ?? ''),
    question_number: String(raw.question_number ?? ''),
    answer_text: raw.answer_text ? String(raw.answer_text) : null,
    solution_steps: Array.isArray(raw.solution_steps) ? raw.solution_steps.map(String) : null,
    options: Array.isArray(raw.options) ? raw.options.map(String) : null,
    correct_option: raw.correct_option ? String(raw.correct_option) : null,
    match_left: Array.isArray(raw.match_left) ? raw.match_left.map(String) : null,
    match_right: Array.isArray(raw.match_right) ? raw.match_right.map(String) : null,
    difficulty: matchedDifficulty,
    marks: typeof raw.marks === 'number' ? raw.marks : 1,
  };
}

// ── Prompt Builder ──────────────────────────────────────────

interface PromptContext {
  boardName: string;
  className: string;
  subjectName: string;
  chapterName: string;
  bookName: string;
  extractionSettings: ExtractionSettings;
}

function buildExtractionPrompt(ctx: PromptContext): string {
  const sourceTypes: string[] = [];
  if (ctx.extractionSettings.extractExercise) sourceTypes.push('exercise');
  if (ctx.extractionSettings.extractMiscellaneous) sourceTypes.push('miscellaneous');
  if (ctx.extractionSettings.extractSolvedExamples) sourceTypes.push('solved_example');
  if (ctx.extractionSettings.extractIntextExamples) sourceTypes.push('examples');
  if (ctx.extractionSettings.extractIntextQuestions) sourceTypes.push('intext');

  return `You are analyzing a textbook PDF for question extraction.

Context:
- Board: ${ctx.boardName}
- Class: ${ctx.className}
- Subject: ${ctx.subjectName}
- Chapter: ${ctx.chapterName}
- Book: ${ctx.bookName}
- Source types to extract: ${sourceTypes.join(', ')}

Instructions:
1. Scan the ENTIRE PDF thoroughly, page by page.
2. Extract EVERY question you find — do not skip any.
3. Only extract questions matching these source types: ${sourceTypes.join(', ')}
4. For each question, return a JSON object with these fields:
   - "question_text": Full question text. Preserve ALL mathematical notation using LaTeX: use $...$ for inline math and $$...$$ for display math.
   - "question_type": One of: "MCQ", "Short Answer", "Long Answer", "Very Short Answer", "Numerical", "Fill in the Blanks", "Assertion-Reason", "Match the Following", "One Word Answer", "True/False", "Case Study", "Diagram Based"
   - "textbook_source_type": One of: "exercise", "miscellaneous", "solved_example", "examples", "intext"
   - "exercise_name": e.g. "Exercise 6.1", "Miscellaneous", "Example 5"
   - "question_number": e.g. "Q1", "Q3(b)", "1(i)", "Example 5"
   - "answer_text": Full answer/solution text if present in the PDF, null otherwise
   - "solution_steps": Array of step-by-step solution strings if it's a solved example or numerical, null otherwise
   - "options": Array of 4 option strings if MCQ (e.g. ["A. option1", "B. option2", "C. option3", "D. option4"]), null otherwise
   - "correct_option": "A", "B", "C", or "D" if MCQ and answer is known, null otherwise
   - "match_left": Array of left-column items if Match the Following, null otherwise
   - "match_right": Array of right-column items if Match the Following, null otherwise
   - "difficulty": "Easy", "Medium", or "Hard" based on complexity
   - "marks": Best guess marks based on question type and complexity. Defaults: MCQ=${ctx.extractionSettings.defaultMarks.MCQ}, Short Answer=${ctx.extractionSettings.defaultMarks['Short Answer']}, Long Answer=${ctx.extractionSettings.defaultMarks['Long Answer']}, Numerical=${ctx.extractionSettings.defaultMarks.Numerical}

5. Return ONLY a raw JSON array. No markdown fences, no explanation, no extra text.
   Each element = one question object with all fields above.
   Example: [{"question_text": "...", "question_type": "MCQ", ...}, ...]`;
}

function buildContinuationPrompt(lastQuestionNumber: string, ctx: PromptContext): string {
  return `You were extracting questions from a textbook PDF but your response was cut off.
Continue extracting from after question "${lastQuestionNumber}".
Extract all remaining questions from the PDF.

Context:
- Board: ${ctx.boardName}
- Class: ${ctx.className}
- Subject: ${ctx.subjectName}
- Chapter: ${ctx.chapterName}
- Book: ${ctx.bookName}

Return ONLY a raw JSON array of the remaining questions (same format as before).
No markdown fences, no explanation.`;
}

// ── Hook ────────────────────────────────────────────────────

interface TextbookIngestorContext {
  boardId: string;
  boardName: string;
  classId: string;
  className: string;
  streamId: string | null;
  subjectId: string;
  subjectName: string;
  chapterId: string;
  chapterName: string;
  bookName: string;
  edition: string;
}

interface UseTextbookIngestorReturn {
  uploadedFiles: File[];
  extractionSettings: ExtractionSettings;
  extractedQuestions: TextbookExtractedQuestion[];
  selectedIds: Set<number>;
  isExtracting: boolean;
  isSaving: boolean;
  extractionProgress: ExtractionProgress;
  extractionError: string | null;
  addFiles: (files: FileList | File[]) => void;
  removeFile: (index: number) => void;
  setExtractionSettings: (settings: ExtractionSettings) => void;
  extractQuestions: (context: TextbookIngestorContext) => Promise<void>;
  saveSelectedQuestions: (context: TextbookIngestorContext, userId: string) => Promise<number>;
  toggleQuestion: (index: number) => void;
  selectAll: () => void;
  deselectAll: () => void;
  updateQuestion: (index: number, changes: Partial<TextbookExtractedQuestion>) => void;
  deleteQuestions: (indices: number[]) => void;
  getQuestionStatus: (q: TextbookExtractedQuestion) => QuestionVerificationStatus;
  resetExtraction: () => void;
}

const DEFAULT_SETTINGS: ExtractionSettings = {
  extractExercise: true,
  extractMiscellaneous: true,
  extractSolvedExamples: true,
  extractIntextExamples: false,
  extractIntextQuestions: false,
  defaultMarks: {
    MCQ: 1,
    'Short Answer': 2,
    'Long Answer': 5,
    Numerical: 3,
  },
};

export function useTextbookIngestor(): UseTextbookIngestorReturn {
  const [uploadedFiles, setUploadedFiles] = useState<File[]>([]);
  const [extractionSettings, setExtractionSettings] = useState<ExtractionSettings>(DEFAULT_SETTINGS);
  const [extractedQuestions, setExtractedQuestions] = useState<TextbookExtractedQuestion[]>([]);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [isExtracting, setIsExtracting] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [extractionProgress, setExtractionProgress] = useState<ExtractionProgress>({
    current: 0,
    total: 0,
    questionsFound: 0,
  });
  const [extractionError, setExtractionError] = useState<string | null>(null);

  const addFiles = useCallback((files: FileList | File[]) => {
    const pdfFiles = Array.from(files).filter((f) => f.type === 'application/pdf');
    setUploadedFiles((prev) => [...prev, ...pdfFiles]);
  }, []);

  const removeFile = useCallback((index: number) => {
    setUploadedFiles((prev) => prev.filter((_, i) => i !== index));
  }, []);

  const extractQuestions = useCallback(
    async (context: TextbookIngestorContext) => {
      if (uploadedFiles.length === 0) return;

      setIsExtracting(true);
      setExtractionError(null);
      setExtractedQuestions([]);
      setSelectedIds(new Set());
      setExtractionProgress({ current: 0, total: uploadedFiles.length, questionsFound: 0 });

      const allQuestions: TextbookExtractedQuestion[] = [];
      const errors: string[] = [];

      const promptCtx: PromptContext = {
        boardName: context.boardName,
        className: context.className,
        subjectName: context.subjectName,
        chapterName: context.chapterName,
        bookName: context.bookName,
        extractionSettings,
      };

      for (let i = 0; i < uploadedFiles.length; i++) {
        const file = uploadedFiles[i];
        setExtractionProgress((prev) => ({
          ...prev,
          current: i + 1,
        }));

        try {
          // Convert PDF to base64
          const base64 = await fileToBase64(file);

          // Build extraction prompt
          const prompt = buildExtractionPrompt(promptCtx);

          // Call Gemini with PDF
          let rawResponse = await callGeminiWithMedia(prompt, base64, 'application/pdf');

          let questions: TextbookExtractedQuestion[];

          try {
            const parsed = parseGeminiJSON<Record<string, unknown>[]>(rawResponse);
            questions = parsed.map(sanitizeQuestion);
          } catch {
            // Retry once with JSON correction prompt
            const retryPrompt = `Your previous response was not valid JSON. Return ONLY a valid JSON array of question objects, nothing else.\n\n${prompt}`;
            rawResponse = await callGeminiWithMedia(retryPrompt, base64, 'application/pdf');
            try {
              const parsed = parseGeminiJSON<Record<string, unknown>[]>(rawResponse);
              questions = parsed.map(sanitizeQuestion);
            } catch {
              throw new Error(`Failed to parse JSON from Gemini for file: ${file.name}`);
            }
          }

          // Check for truncation — if we got 30+ questions and the JSON seemed cut off
          if (questions.length >= 30) {
            try {
              const lastQ = questions[questions.length - 1];
              const continuationPrompt = buildContinuationPrompt(
                lastQ.question_number || `question ${questions.length}`,
                promptCtx,
              );
              const contResponse = await callGemini(continuationPrompt);
              try {
                const contParsed = parseGeminiJSON<Record<string, unknown>[]>(contResponse);
                const contQuestions = contParsed.map(sanitizeQuestion);
                if (contQuestions.length > 0) {
                  questions = [...questions, ...contQuestions];
                }
              } catch {
                // Continuation failed — use what we have
                console.warn('Continuation parse failed, using initial extraction');
              }
            } catch {
              // Continuation request failed — use what we have
              console.warn('Continuation request failed, using initial extraction');
            }
          }

          allQuestions.push(...questions);
          setExtractedQuestions([...allQuestions]);
          setExtractionProgress((prev) => ({
            ...prev,
            questionsFound: allQuestions.length,
          }));
        } catch (err) {
          const msg = err instanceof Error ? err.message : 'Unknown error';
          errors.push(`${file.name}: ${msg}`);
          console.error(`Extraction failed for ${file.name}:`, err);
        }
      }

      // Select all valid questions by default
      const allIds = new Set<number>();
      allQuestions.forEach((q, idx) => {
        if (getVerificationStatus(q) === 'ready') {
          allIds.add(idx);
        }
      });
      setSelectedIds(allIds);

      if (errors.length > 0) {
        setExtractionError(`Errors in ${errors.length} file(s):\n${errors.join('\n')}`);
      }

      setIsExtracting(false);
    },
    [uploadedFiles, extractionSettings],
  );

  const saveSelectedQuestions = useCallback(
    async (context: TextbookIngestorContext, userId: string): Promise<number> => {
      const selected = extractedQuestions.filter((_, i) => selectedIds.has(i));
      if (selected.length === 0) return 0;

      setIsSaving(true);

      try {
        const insertRows = selected.map((q) => ({
          board_id: context.boardId,
          class_id: context.classId,
          stream_id: context.streamId,
          subject_id: context.subjectId,
          chapter_id: context.chapterId,
          topic_id: null,
          question_text: q.question_text,
          answer_text: q.answer_text,
          question_type: q.question_type,
          marks: q.marks,
          difficulty: q.difficulty,
          options: q.options,
          correct_option: q.correct_option,
          match_left: q.match_left,
          match_right: q.match_right,
          solution_steps: q.solution_steps,
          source: 'textbook' as const,
          book_name: context.bookName || null,
          exercise_no: q.exercise_name || null,
          question_no: q.question_number || null,
          textbook_source_type: q.textbook_source_type,
          edition: context.edition || null,
          times_used: 0,
          is_active: true,
          created_by: userId,
        }));

        const { error } = await supabase
          .from('question_bank')
          .insert(insertRows);

        if (error) {
          throw new Error(error.message);
        }

        return selected.length;
      } finally {
        setIsSaving(false);
      }
    },
    [extractedQuestions, selectedIds],
  );

  const toggleQuestion = useCallback((index: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(index)) {
        next.delete(index);
      } else {
        next.add(index);
      }
      return next;
    });
  }, []);

  const selectAll = useCallback(() => {
    setSelectedIds(new Set(extractedQuestions.map((_, i) => i)));
  }, [extractedQuestions]);

  const deselectAll = useCallback(() => {
    setSelectedIds(new Set());
  }, []);

  const updateQuestion = useCallback((index: number, changes: Partial<TextbookExtractedQuestion>) => {
    setExtractedQuestions((prev) =>
      prev.map((q, i) => (i === index ? { ...q, ...changes } : q)),
    );
  }, []);

  const deleteQuestions = useCallback((indices: number[]) => {
    const indexSet = new Set(indices);
    setExtractedQuestions((prev) => prev.filter((_, i) => !indexSet.has(i)));
    setSelectedIds((prev) => {
      const next = new Set<number>();
      let offset = 0;
      for (let i = 0; i < extractedQuestions.length; i++) {
        if (indexSet.has(i)) {
          offset++;
          continue;
        }
        if (prev.has(i)) {
          next.add(i - offset);
        }
      }
      return next;
    });
  }, [extractedQuestions.length]);

  const resetExtraction = useCallback(() => {
    setUploadedFiles([]);
    setExtractedQuestions([]);
    setSelectedIds(new Set());
    setExtractionProgress({ current: 0, total: 0, questionsFound: 0 });
    setExtractionError(null);
  }, []);

  return {
    uploadedFiles,
    extractionSettings,
    extractedQuestions,
    selectedIds,
    isExtracting,
    isSaving,
    extractionProgress,
    extractionError,
    addFiles,
    removeFile,
    setExtractionSettings,
    extractQuestions,
    saveSelectedQuestions,
    toggleQuestion,
    selectAll,
    deselectAll,
    updateQuestion,
    deleteQuestions,
    getQuestionStatus: getVerificationStatus,
    resetExtraction,
  };
}
