// ── Database row types ──────────────────────────────────────

export interface Board {
  board_id: string;
  board_name: string;
  code: string;
  is_active: boolean;
  created_at: string;
}

export interface Stream {
  stream_id: string;
  stream_name: string;
  board_id: string;
  is_active: boolean;
  created_at: string;
}

export interface Class {
  class_id: string;
  class_name: string;
  board_id: string;
  stream_id: string | null;
  is_active: boolean;
  created_at: string;
}

export interface Subject {
  subject_id: string;
  subject_name: string;
  class_id: string;
  is_active: boolean;
  created_at: string;
}

export interface Chapter {
  chapter_id: string;
  chapter_name: string;
  subject_id: string;
  is_active: boolean;
  created_at: string;
}

export interface Topic {
  topic_id: string;
  topic_name: string;
  topic_context: string | null;
  chapter_id: string;
  created_at: string;
}

export interface Profile {
  id: string;
  full_name: string | null;
  role: 'teacher' | 'admin';
  created_at: string;
}

export type QuestionType =
  | 'MCQ'
  | 'Short Answer'
  | 'Long Answer'
  | 'Very Short Answer'
  | 'Numerical'
  | 'Fill in the Blanks'
  | 'True/False'
  | 'Assertion-Reason'
  | 'Case Study'
  | 'Match the Following'
  | 'One Word Answer'
  | 'Diagram Based';

export type Difficulty = 'easy' | 'medium' | 'hard';
export type Source = 'ai_generated' | 'textbook' | 'manual' | 'other';
export type TextbookSourceType = 'exercise' | 'miscellaneous' | 'solved_example' | 'examples' | 'intext';

export interface QuestionBankItem {
  question_id: string;
  board_id: string | null;
  class_id: string | null;
  stream_id: string | null;
  subject_id: string | null;
  chapter_id: string | null;
  topic_id: string | null;
  question_text: string;
  answer_text: string | null;
  answer_hint: string | null;
  question_type: QuestionType;
  marks: number;
  difficulty: Difficulty | null;
  options: string[] | null;
  correct_option: string | null;
  blank_answer: string | null;
  match_left: string[] | null;
  match_right: string[] | null;
  match_answer: Record<string, string> | null;
  assertion: string | null;
  reason: string | null;
  solution_steps: string[] | null;
  diagram_url: string | null;
  source: Source;
  book_name: string | null;
  exercise_no: string | null;
  question_no: string | null;
  page_no: number | null;
  textbook_source_type: TextbookSourceType | null;
  edition: string | null;
  times_used: number;
  is_active: boolean;
  created_at: string;
  created_by: string | null;
}

export interface GeneratedPaper {
  paper_id: string;
  created_at: string;
  created_by: string;
  board_id: string | null;
  class_id: string | null;
  stream_id: string | null;
  subject_id: string | null;
  paper_title: string | null;
  total_marks: number | null;
  duration: string | null;
  difficulty: string | null;
  paper_config: SectionConfig[] | null;
  selected_chapters: string[] | null;
  status: 'draft' | 'final';
  is_deleted: boolean;
}

export interface PaperQuestion {
  id: string;
  paper_id: string;
  question_id: string;
  section_name: string | null;
  order_index: number | null;
  is_replaced: boolean;
  replaced_at: string | null;
  created_at: string;
}

// ── Wizard types ────────────────────────────────────────────

export interface SectionConfig {
  id: string;
  sectionName: string;
  questionType: QuestionType;
  numQuestions: number;
  marksPerQuestion: number;
}

export interface WizardState {
  currentStep: number;
  boardId: string | null;
  boardName: string | null;
  classId: string | null;
  className: string | null;
  needsStream: boolean;
  streamId: string | null;
  streamName: string | null;
  subjectId: string | null;
  subjectName: string | null;
  paperTitle: string;
  totalMarks: number;
  duration: string;
  difficulty: string;
  selectedChapterIds: string[];
  selectedChapterNames: string[];
  sections: SectionConfig[];
}

// ── Gemini types ────────────────────────────────────────────

export interface GeminiQuestionResponse {
  question_text: string;
  answer_text: string | null;
  answer_hint: string | null;
  question_type: string;
  marks: number;
  difficulty: string;
  chapter_name: string;
  options: string[] | null;
  correct_option: string | null;
  blank_answer: string | null;
  match_left: string[] | null;
  match_right: string[] | null;
  match_answer: Record<string, string> | null;
  assertion: string | null;
  reason: string | null;
  solution_steps: string[] | null;
}

export interface SectionPromptConfig {
  boardName: string;
  className: string;
  streamName: string | null;
  subjectName: string;
  chapterNames: string[];
  sectionName: string;
  questionType: string;
  numQuestions: number;
  marksPerQuestion: number;
  difficulty: string;
  existingQuestions: string[];
  isMaths: boolean;
}

export interface SectionProgress {
  sectionName: string;
  status: 'waiting' | 'generating' | 'done' | 'error';
  questionCount: number;
  error?: string;
}

// ── Paper preview types ─────────────────────────────────────

export interface PaperQuestionWithDetails extends PaperQuestion {
  question: QuestionBankItem;
}

export interface PaperSection {
  sectionName: string;
  questionType: QuestionType;
  marksPerQuestion: number;
  questions: PaperQuestionWithDetails[];
}

// ── Admin form types ────────────────────────────────────────

export interface QuestionFormData {
  boardId: string;
  classId: string;
  streamId: string;
  subjectId: string;
  chapterId: string;
  topicId: string | null;
  questionType: QuestionType;
  marks: number;
  difficulty: Difficulty;
  questionText: string;
  answerText: string;
  answerHint: string;
  options: string[];
  correctOption: string;
  blankAnswer: string;
  matchLeft: string[];
  matchRight: string[];
  matchAnswer: Record<string, string>;
  assertion: string;
  reason: string;
  solutionSteps: string[];
  source: Source;
  bookName: string;
  exerciseNo: string;
  questionNo: string;
  pageNo: number | null;
}

// ── Textbook ingestor types ─────────────────────────────────

export interface TextbookExtractedQuestion {
  question_text: string;
  question_type: QuestionType;
  textbook_source_type: TextbookSourceType;
  exercise_name: string;
  question_number: string;
  answer_text: string | null;
  solution_steps: string[] | null;
  options: string[] | null;
  correct_option: string | null;
  match_left: string[] | null;
  match_right: string[] | null;
  difficulty: Difficulty;
  marks: number;
}

export type QuestionVerificationStatus = 'ready' | 'needs_review' | 'error';

export interface ExtractionSettings {
  extractExercise: boolean;
  extractMiscellaneous: boolean;
  extractSolvedExamples: boolean;
  extractIntextExamples: boolean;
  extractIntextQuestions: boolean;
  defaultMarks: {
    MCQ: number;
    'Short Answer': number;
    'Long Answer': number;
    Numerical: number;
  };
}

export interface ExtractionProgress {
  current: number;
  total: number;
  questionsFound: number;
}
