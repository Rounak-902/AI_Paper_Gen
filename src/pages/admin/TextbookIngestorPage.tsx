import { useState, useEffect, useMemo, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '@/context/AuthContext';
import { useCascadingDropdowns } from '@/hooks/useCascadingDropdowns';
import { useTextbookIngestor } from '@/hooks/useTextbookIngestor';
import MathRenderer from '@/components/shared/MathRenderer';
import ConfirmDialog from '@/components/shared/ConfirmDialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Checkbox } from '@/components/ui/checkbox';
import { Textarea } from '@/components/ui/textarea';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { toast } from 'sonner';
import {
  BookOpen,
  Upload,
  FileText,
  X,
  Loader2,
  Sparkles,
  Search,
  ChevronDown,
  ChevronUp,
  ChevronLeft,
  ChevronRight,
  Trash2,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  Plus,
  Minus,
} from 'lucide-react';
import type {
  QuestionType,
  Difficulty,
  TextbookSourceType,
  TextbookExtractedQuestion,
  ExtractionSettings,
} from '@/types/database';

// ── Constants ───────────────────────────────────────────────

const ALL_QUESTION_TYPES: QuestionType[] = [
  'MCQ', 'Short Answer', 'Long Answer', 'Very Short Answer', 'Numerical',
  'Fill in the Blanks', 'True/False', 'Assertion-Reason', 'Case Study',
  'Match the Following', 'One Word Answer', 'Diagram Based',
];

const ALL_DIFFICULTIES: Difficulty[] = ['easy', 'medium', 'hard'];

const SOURCE_COLORS: Record<TextbookSourceType, string> = {
  exercise: 'bg-blue-100 text-blue-700',
  miscellaneous: 'bg-purple-100 text-purple-700',
  solved_example: 'bg-emerald-100 text-emerald-700',
  examples: 'bg-amber-100 text-amber-700',
  intext: 'bg-cyan-100 text-cyan-700',
};

const SOURCE_LABELS: Record<TextbookSourceType, string> = {
  exercise: 'Exercise',
  miscellaneous: 'Miscellaneous',
  solved_example: 'Solved Example',
  examples: 'In-text Example',
  intext: 'In-text Question',
};

const DIFFICULTY_COLORS: Record<Difficulty, string> = {
  easy: 'bg-green-100 text-green-700',
  medium: 'bg-yellow-100 text-yellow-700',
  hard: 'bg-red-100 text-red-700',
};

const PAGE_SIZE = 50;

/** Truncate text without cutting $...$ expressions */
function smartTruncate(text: string, maxLen: number): string {
  if (text.length <= maxLen) return text;
  const truncated = text.slice(0, maxLen);
  const dollarCount = (truncated.match(/\$/g) || []).length;
  if (dollarCount % 2 !== 0) {
    const lastDollar = truncated.lastIndexOf('$');
    if (lastDollar > 0) return truncated.slice(0, lastDollar) + '...';
  }
  return truncated + '...';
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// ── Main Component ──────────────────────────────────────────

export default function TextbookIngestorPage() {
  const navigate = useNavigate();
  const { user } = useAuth();

  // Step state: 1=context, 2=upload, 3=settings, 4=extract/verify
  const [step, setStep] = useState(1);

  // Context selections
  const {
    boards, classes, subjects, chapters,
    fetchClasses, fetchSubjects, fetchChapters,
  } = useCascadingDropdowns();

  const [boardId, setBoardId] = useState('');
  const [boardName, setBoardName] = useState('');
  const [classId, setClassId] = useState('');
  const [className, setClassName] = useState('');
  const [subjectId, setSubjectId] = useState('');
  const [subjectName, setSubjectName] = useState('');
  const [chapterId, setChapterId] = useState('');
  const [chapterName, setChapterName] = useState('');
  const [bookName, setBookName] = useState('');
  const [edition, setEdition] = useState('');

  // Ingestor hook
  const ingestor = useTextbookIngestor();

  // Verification table state
  const [expandedRow, setExpandedRow] = useState<number | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [filterTab, setFilterTab] = useState<'all' | TextbookSourceType | 'needs_review'>('all');
  const [tablePage, setTablePage] = useState(1);
  const [confirmSave, setConfirmSave] = useState(false);

  // Bulk action state
  const [bulkTypeChange, setBulkTypeChange] = useState<QuestionType | ''>('');
  const [bulkDifficultyChange, setBulkDifficultyChange] = useState<Difficulty | ''>('');
  const [bulkSourceChange, setBulkSourceChange] = useState<TextbookSourceType | ''>('');

  // Drag & drop
  const dropRef = useRef<HTMLDivElement>(null);
  const [isDragging, setIsDragging] = useState(false);

  // ── Cascading dropdown effects ────────────────────────────

  useEffect(() => {
    if (boardId) {
      fetchClasses(boardId);
    }
  }, [boardId, fetchClasses]);

  useEffect(() => {
    if (classId) fetchSubjects(classId);
  }, [classId, fetchSubjects]);

  useEffect(() => {
    if (subjectId) fetchChapters(subjectId);
  }, [subjectId, fetchChapters]);



  // ── Filtered + paginated questions ────────────────────────

  const filteredQuestions = useMemo(() => {
    let questions = ingestor.extractedQuestions;

    if (filterTab !== 'all') {
      if (filterTab === 'needs_review') {
        questions = questions.filter((q) => ingestor.getQuestionStatus(q) === 'needs_review');
      } else {
        questions = questions.filter((q) => q.textbook_source_type === filterTab);
      }
    }

    if (searchQuery.trim()) {
      const query = searchQuery.toLowerCase();
      questions = questions.filter(
        (q) =>
          q.question_text.toLowerCase().includes(query) ||
          q.exercise_name.toLowerCase().includes(query) ||
          q.question_number.toLowerCase().includes(query),
      );
    }

    return questions;
  }, [ingestor.extractedQuestions, filterTab, searchQuery, ingestor.getQuestionStatus]);

  // Map filtered indices back to original indices
  const filteredIndices = useMemo(() => {
    const originalQuestions = ingestor.extractedQuestions;
    return filteredQuestions.map((q) => originalQuestions.indexOf(q));
  }, [filteredQuestions, ingestor.extractedQuestions]);

  const totalPages = Math.ceil(filteredQuestions.length / PAGE_SIZE);
  const paginatedQuestions = filteredQuestions.slice(
    (tablePage - 1) * PAGE_SIZE,
    tablePage * PAGE_SIZE,
  );
  const paginatedIndices = filteredIndices.slice(
    (tablePage - 1) * PAGE_SIZE,
    tablePage * PAGE_SIZE,
  );

  // Counts
  const needsReviewCount = ingestor.extractedQuestions.filter(
    (q) => ingestor.getQuestionStatus(q) === 'needs_review',
  ).length;
  const selectedCount = ingestor.selectedIds.size;

  // ── Handlers ──────────────────────────────────────────────

  const handleBoardChange = (id: string) => {
    const board = boards.find((b) => b.board_id === id);
    setBoardId(id);
    setBoardName(board?.board_name ?? '');
    setClassId('');
    setClassName('');
    setStreamId(null);
    setSubjectId('');
    setSubjectName('');
    setChapterId('');
    setChapterName('');
  };

  const handleClassChange = (id: string) => {
    const cls = classes.find((c) => c.class_id === id);
    setClassId(id);
    setClassName(cls?.class_name ?? '');
    setStreamId(null);
    setSubjectId('');
    setSubjectName('');
    setChapterId('');
    setChapterName('');
  };

  const handleSubjectChange = (id: string) => {
    const sub = subjects.find((s) => s.subject_id === id);
    setSubjectId(id);
    setSubjectName(sub?.subject_name ?? '');
    setChapterId('');
    setChapterName('');
  };

  const handleChapterChange = (id: string) => {
    const ch = chapters.find((c) => c.chapter_id === id);
    setChapterId(id);
    setChapterName(ch?.chapter_name ?? '');
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    if (e.dataTransfer.files) {
      ingestor.addFiles(e.dataTransfer.files);
    }
  };

  const handleFileInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      ingestor.addFiles(e.target.files);
    }
  };

  const handleExtract = async () => {
    setStep(4);
    await ingestor.extractQuestions({
      boardId,
      boardName,
      classId,
      className,
      streamId,
      subjectId,
      subjectName,
      chapterId,
      chapterName,
      bookName,
      edition,
    });
  };

  const handleSave = async () => {
    setConfirmSave(false);
    if (!user) return;

    try {
      const count = await ingestor.saveSelectedQuestions(
        {
          boardId,
          boardName,
          classId,
          className,
          streamId,
          subjectId,
          subjectName,
          chapterId,
          chapterName,
          bookName,
          edition,
        },
        user.id,
      );
      toast.success(`${count} questions saved to question bank!`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Save failed';
      toast.error(msg);
    }
  };

  const handleDeleteSelected = () => {
    const indices = Array.from(ingestor.selectedIds);
    ingestor.deleteQuestions(indices);
    toast.success(`${indices.length} questions removed`);
  };

  const handleBulkTypeChange = (type: QuestionType) => {
    ingestor.selectedIds.forEach((idx) => {
      ingestor.updateQuestion(idx, { question_type: type });
    });
    setBulkTypeChange('');
    toast.success(`Type updated for ${ingestor.selectedIds.size} questions`);
  };

  const handleBulkDifficultyChange = (diff: Difficulty) => {
    ingestor.selectedIds.forEach((idx) => {
      ingestor.updateQuestion(idx, { difficulty: diff });
    });
    setBulkDifficultyChange('');
    toast.success(`Difficulty updated for ${ingestor.selectedIds.size} questions`);
  };

  const handleBulkSourceChange = (source: TextbookSourceType) => {
    ingestor.selectedIds.forEach((idx) => {
      ingestor.updateQuestion(idx, { textbook_source_type: source });
    });
    setBulkSourceChange('');
    toast.success(`Source type updated for ${ingestor.selectedIds.size} questions`);
  };

  const handleReset = () => {
    ingestor.resetExtraction();
    setStep(1);
    setSearchQuery('');
    setFilterTab('all');
    setTablePage(1);
    setExpandedRow(null);
  };

  // ── Validation ────────────────────────────────────────────

  const canProceedToStep2 = boardId && classId && subjectId && chapterId;
  const canProceedToStep3 = ingestor.uploadedFiles.length > 0;
  const canExtract = canProceedToStep2 && canProceedToStep3;

  // ── Step indicator ────────────────────────────────────────

  const steps = [
    { num: 1, label: 'Context' },
    { num: 2, label: 'Upload' },
    { num: 3, label: 'Settings' },
    { num: 4, label: 'Verify & Save' },
  ];

  // ── RENDER ────────────────────────────────────────────────

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-[#1E293B]">Textbook Ingestor</h1>
          <p className="mt-1 flex items-center gap-2 text-sm text-[#64748B]">
            <BookOpen className="h-4 w-4" />
            Extract questions from textbook PDFs
          </p>
        </div>
      </div>

      {/* Step indicator */}
      <div className="flex items-center gap-2">
        {steps.map((s, i) => (
          <div key={s.num} className="flex items-center gap-2">
            <button
              onClick={() => {
                if (s.num < step) setStep(s.num);
              }}
              disabled={s.num > step}
              className={`flex items-center gap-2 rounded-full px-4 py-2 text-sm font-medium transition-all ${
                s.num === step
                  ? 'bg-[#4F46E5] text-white shadow-lg shadow-indigo-500/25'
                  : s.num < step
                    ? 'bg-emerald-100 text-emerald-700 hover:bg-emerald-200 cursor-pointer'
                    : 'bg-gray-100 text-gray-400 cursor-not-allowed'
              }`}
            >
              <span className={`flex h-6 w-6 items-center justify-center rounded-full text-xs font-bold ${
                s.num === step
                  ? 'bg-white/20 text-white'
                  : s.num < step
                    ? 'bg-emerald-500 text-white'
                    : 'bg-gray-300 text-white'
              }`}>
                {s.num < step ? '✓' : s.num}
              </span>
              {s.label}
            </button>
            {i < steps.length - 1 && (
              <div className={`h-0.5 w-8 ${s.num < step ? 'bg-emerald-300' : 'bg-gray-200'}`} />
            )}
          </div>
        ))}
      </div>

      {/* ── STEP 1: Context Selection ──────────────────────── */}
      {step === 1 && (
        <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm space-y-5">
          <h2 className="text-lg font-semibold text-[#1E293B]">Chapter Context</h2>
          <p className="text-sm text-[#64748B]">
            Select the board, class, subject, and chapter for the questions you're about to extract.
          </p>

          <div className="grid gap-4 md:grid-cols-2">
            {/* Board */}
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-[#64748B]">Board *</label>
              <Select value={boardId || undefined} onValueChange={(v) => v && handleBoardChange(v)}>
                <SelectTrigger>
                  <SelectValue placeholder="Select Board" />
                </SelectTrigger>
                <SelectContent>
                  {boards.map((b) => (
                    <SelectItem key={b.board_id} value={b.board_id}>{b.board_name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* Class */}
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-[#64748B]">Class *</label>
              <Select
                value={classId || undefined}
                onValueChange={(v) => v && handleClassChange(v)}
                disabled={!boardId}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Select Class" />
                </SelectTrigger>
                <SelectContent>
                  {classes.map((c) => (
                    <SelectItem key={c.class_id} value={c.class_id}>Class {c.class_name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>



            {/* Subject */}
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-[#64748B]">Subject *</label>
              <Select
                value={subjectId || undefined}
                onValueChange={(v) => v && handleSubjectChange(v)}
                disabled={!classId}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Select Subject" />
                </SelectTrigger>
                <SelectContent>
                  {subjects.map((s) => (
                    <SelectItem key={s.subject_id} value={s.subject_id}>{s.subject_name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* Chapter */}
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-[#64748B]">Chapter *</label>
              <Select
                value={chapterId || undefined}
                onValueChange={(v) => v && handleChapterChange(v)}
                disabled={!subjectId}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Select Chapter" />
                </SelectTrigger>
                <SelectContent>
                  {chapters.map((c) => (
                    <SelectItem key={c.chapter_id} value={c.chapter_id}>{c.chapter_name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* Book Name */}
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-[#64748B]">Book Name</label>
              <Input
                value={bookName}
                onChange={(e) => setBookName(e.target.value)}
                placeholder="e.g. NCERT Mathematics Part I"
              />
            </div>

            {/* Edition */}
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-[#64748B]">Edition</label>
              <Input
                value={edition}
                onChange={(e) => setEdition(e.target.value)}
                placeholder="e.g. 2024-25"
              />
            </div>
          </div>

          <div className="flex justify-end pt-2">
            <Button
              onClick={() => setStep(2)}
              disabled={!canProceedToStep2}
              className="gap-2 bg-[#4F46E5] text-white hover:bg-indigo-700"
            >
              Next: Upload PDFs
              <ChevronRight className="h-4 w-4" />
            </Button>
          </div>
        </div>
      )}

      {/* ── STEP 2: PDF Upload ─────────────────────────────── */}
      {step === 2 && (
        <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm space-y-5">
          <h2 className="text-lg font-semibold text-[#1E293B]">Upload PDF Files</h2>
          <p className="text-sm text-[#64748B]">
            Upload one or more textbook PDF files. Each file can be a chapter section (e.g. Exercise 6.1, Miscellaneous).
          </p>

          {/* Drop zone */}
          <div
            ref={dropRef}
            onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
            onDragLeave={() => setIsDragging(false)}
            onDrop={handleDrop}
            className={`flex flex-col items-center justify-center rounded-xl border-2 border-dashed p-12 transition-all ${
              isDragging
                ? 'border-[#4F46E5] bg-indigo-50'
                : 'border-gray-300 bg-gray-50 hover:border-gray-400'
            }`}
          >
            <Upload className={`h-10 w-10 ${isDragging ? 'text-[#4F46E5]' : 'text-gray-400'}`} />
            <p className="mt-3 text-sm font-medium text-[#1E293B]">
              {isDragging ? 'Drop PDF files here' : 'Drag & drop PDF files here'}
            </p>
            <p className="mt-1 text-xs text-[#64748B]">or</p>
            <label className="mt-2 cursor-pointer rounded-lg bg-[#4F46E5] px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-700">
              Browse Files
              <input
                type="file"
                accept=".pdf,application/pdf"
                multiple
                className="hidden"
                onChange={handleFileInput}
              />
            </label>
            <p className="mt-3 text-xs text-[#64748B]">PDF files only</p>
          </div>

          {/* File list */}
          {ingestor.uploadedFiles.length > 0 && (
            <div className="space-y-2">
              <h3 className="text-sm font-medium text-[#1E293B]">
                {ingestor.uploadedFiles.length} file{ingestor.uploadedFiles.length > 1 ? 's' : ''} selected
              </h3>
              {ingestor.uploadedFiles.map((file, i) => (
                <div
                  key={`${file.name}-${i}`}
                  className="flex items-center justify-between rounded-lg border border-gray-200 bg-gray-50 px-4 py-3"
                >
                  <div className="flex items-center gap-3">
                    <FileText className="h-5 w-5 text-[#EF4444]" />
                    <div>
                      <p className="text-sm font-medium text-[#1E293B]">{file.name}</p>
                      <p className="text-xs text-[#64748B]">{formatFileSize(file.size)}</p>
                    </div>
                  </div>
                  <button
                    onClick={() => ingestor.removeFile(i)}
                    className="rounded-lg p-1.5 text-gray-400 transition-colors hover:bg-red-50 hover:text-[#EF4444]"
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>
              ))}
            </div>
          )}

          <div className="flex justify-between pt-2">
            <Button variant="outline" onClick={() => setStep(1)}>
              <ChevronLeft className="mr-1 h-4 w-4" /> Back
            </Button>
            <Button
              onClick={() => setStep(3)}
              disabled={!canProceedToStep3}
              className="gap-2 bg-[#4F46E5] text-white hover:bg-indigo-700"
            >
              Next: Settings
              <ChevronRight className="h-4 w-4" />
            </Button>
          </div>
        </div>
      )}

      {/* ── STEP 3: Extraction Settings ────────────────────── */}
      {step === 3 && (
        <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm space-y-5">
          <h2 className="text-lg font-semibold text-[#1E293B]">Extraction Settings</h2>
          <p className="text-sm text-[#64748B]">
            Choose what types of questions to extract and set default marks.
          </p>

          {/* Source type checkboxes */}
          <div className="space-y-3">
            <h3 className="text-sm font-medium text-[#1E293B]">What to extract</h3>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {([
                { key: 'extractExercise' as const, label: 'Exercise Questions', icon: '📝' },
                { key: 'extractMiscellaneous' as const, label: 'Miscellaneous Questions', icon: '📚' },
                { key: 'extractSolvedExamples' as const, label: 'Solved Examples', icon: '✅' },
                { key: 'extractIntextExamples' as const, label: 'In-text Examples', icon: '💡' },
                { key: 'extractIntextQuestions' as const, label: 'In-text Questions', icon: '❓' },
              ]).map((item) => (
                <label
                  key={item.key}
                  className={`flex items-center gap-3 rounded-lg border px-4 py-3 transition-all cursor-pointer ${
                    ingestor.extractionSettings[item.key]
                      ? 'border-[#4F46E5] bg-indigo-50'
                      : 'border-gray-200 hover:border-gray-300'
                  }`}
                >
                  <Checkbox
                    checked={ingestor.extractionSettings[item.key]}
                    onCheckedChange={(checked) =>
                      ingestor.setExtractionSettings({
                        ...ingestor.extractionSettings,
                        [item.key]: Boolean(checked),
                      })
                    }
                  />
                  <span className="text-lg">{item.icon}</span>
                  <span className="text-sm font-medium text-[#1E293B]">{item.label}</span>
                </label>
              ))}
            </div>
          </div>

          {/* Default marks */}
          <div className="space-y-3">
            <h3 className="text-sm font-medium text-[#1E293B]">Default marks per question type</h3>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {(Object.entries(ingestor.extractionSettings.defaultMarks) as [string, number][]).map(
                ([type, marks]) => (
                  <div key={type} className="space-y-1.5">
                    <label className="text-xs font-medium text-[#64748B]">{type}</label>
                    <Input
                      type="number"
                      min={1}
                      max={20}
                      value={marks}
                      onChange={(e) =>
                        ingestor.setExtractionSettings({
                          ...ingestor.extractionSettings,
                          defaultMarks: {
                            ...ingestor.extractionSettings.defaultMarks,
                            [type]: parseInt(e.target.value) || 1,
                          },
                        })
                      }
                    />
                  </div>
                ),
              )}
            </div>
          </div>

          <div className="flex justify-between pt-2">
            <Button variant="outline" onClick={() => setStep(2)}>
              <ChevronLeft className="mr-1 h-4 w-4" /> Back
            </Button>
            <Button
              onClick={handleExtract}
              disabled={!canExtract}
              className="gap-2 bg-gradient-to-r from-[#4F46E5] to-purple-600 text-white hover:from-indigo-700 hover:to-purple-700 shadow-lg shadow-indigo-500/25"
            >
              <Sparkles className="h-4 w-4" />
              Extract Questions
            </Button>
          </div>
        </div>
      )}

      {/* ── STEP 4: Extraction Progress & Verification ──── */}
      {step === 4 && (
        <>
          {/* Extraction progress overlay */}
          {ingestor.isExtracting && (
            <div className="fixed inset-0 z-50 flex items-center justify-center bg-[#0F172A]/80 backdrop-blur-sm">
              <div className="mx-4 w-full max-w-lg rounded-2xl bg-white p-8 shadow-2xl">
                <div className="mb-6 text-center">
                  <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br from-[#4F46E5] to-purple-600">
                    <Sparkles className="h-8 w-8 text-white" />
                  </div>
                  <h3 className="text-xl font-bold text-[#1E293B]">Extracting Questions...</h3>
                  <p className="mt-1 text-sm text-[#64748B]">
                    Processing file {ingestor.extractionProgress.current} of {ingestor.extractionProgress.total}
                  </p>
                </div>

                {/* Progress bar */}
                <div className="mb-4">
                  <div className="mb-2 flex justify-between text-xs text-[#64748B]">
                    <span>
                      {ingestor.extractionProgress.current} / {ingestor.extractionProgress.total} files
                    </span>
                    <span>
                      {Math.round(
                        ingestor.extractionProgress.total > 0
                          ? (ingestor.extractionProgress.current / ingestor.extractionProgress.total) * 100
                          : 0,
                      )}%
                    </span>
                  </div>
                  <div className="h-3 overflow-hidden rounded-full bg-gray-100">
                    <div
                      className="h-full rounded-full bg-gradient-to-r from-[#4F46E5] to-purple-500 transition-all duration-700"
                      style={{
                        width: `${
                          ingestor.extractionProgress.total > 0
                            ? (ingestor.extractionProgress.current / ingestor.extractionProgress.total) * 100
                            : 0
                        }%`,
                      }}
                    />
                  </div>
                </div>

                <div className="rounded-lg bg-indigo-50 px-4 py-3 text-center">
                  <p className="text-sm font-medium text-[#4F46E5]">
                    {ingestor.extractionProgress.questionsFound} questions found so far...
                  </p>
                </div>

                {/* File list status */}
                <div className="mt-4 space-y-2">
                  {ingestor.uploadedFiles.map((file, i) => (
                    <div
                      key={`progress-${file.name}-${i}`}
                      className={`flex items-center justify-between rounded-lg px-4 py-2 ${
                        i + 1 < ingestor.extractionProgress.current
                          ? 'bg-emerald-50'
                          : i + 1 === ingestor.extractionProgress.current
                            ? 'bg-indigo-50'
                            : 'bg-gray-50'
                      }`}
                    >
                      <div className="flex items-center gap-2">
                        {i + 1 < ingestor.extractionProgress.current && (
                          <CheckCircle2 className="h-4 w-4 text-[#10B981]" />
                        )}
                        {i + 1 === ingestor.extractionProgress.current && (
                          <Loader2 className="h-4 w-4 animate-spin text-[#4F46E5]" />
                        )}
                        {i + 1 > ingestor.extractionProgress.current && (
                          <div className="h-4 w-4 rounded-full border-2 border-gray-300" />
                        )}
                        <span className="text-sm text-[#1E293B]">{file.name}</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* Verification UI (shows after extraction) */}
          {!ingestor.isExtracting && ingestor.extractedQuestions.length > 0 && (
            <div className="space-y-4">
              {/* Error banner */}
              {ingestor.extractionError && (
                <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-[#EF4444]">
                  {ingestor.extractionError}
                </div>
              )}

              {/* Summary bar */}
              <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <h2 className="text-lg font-semibold text-[#1E293B]">
                      {ingestor.extractedQuestions.length} questions extracted from{' '}
                      {ingestor.uploadedFiles.length} file{ingestor.uploadedFiles.length > 1 ? 's' : ''}
                    </h2>
                    <p className="text-sm text-[#64748B]">
                      {selectedCount} selected • {needsReviewCount} need review
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <Button variant="outline" size="sm" onClick={ingestor.selectAll}>
                      Select All
                    </Button>
                    <Button variant="outline" size="sm" onClick={ingestor.deselectAll}>
                      Deselect All
                    </Button>
                    <Button
                      onClick={() => setConfirmSave(true)}
                      disabled={selectedCount === 0 || ingestor.isSaving}
                      className="gap-2 bg-[#10B981] text-white hover:bg-emerald-600"
                    >
                      {ingestor.isSaving && <Loader2 className="h-4 w-4 animate-spin" />}
                      Save Selected ({selectedCount})
                    </Button>
                  </div>
                </div>

                {/* Filter tabs */}
                <div className="flex flex-wrap items-center gap-2">
                  {[
                    { key: 'all' as const, label: 'All' },
                    { key: 'exercise' as const, label: 'Exercise' },
                    { key: 'miscellaneous' as const, label: 'Miscellaneous' },
                    { key: 'solved_example' as const, label: 'Solved Examples' },
                    { key: 'examples' as const, label: 'In-text Examples' },
                    { key: 'intext' as const, label: 'In-text Questions' },
                    { key: 'needs_review' as const, label: `Needs Review (${needsReviewCount})` },
                  ].map((tab) => (
                    <button
                      key={tab.key}
                      onClick={() => { setFilterTab(tab.key); setTablePage(1); }}
                      className={`rounded-full px-3 py-1.5 text-xs font-medium transition-all ${
                        filterTab === tab.key
                          ? 'bg-[#4F46E5] text-white'
                          : 'bg-gray-100 text-[#64748B] hover:bg-gray-200'
                      }`}
                    >
                      {tab.label}
                    </button>
                  ))}
                </div>

                {/* Search */}
                <div className="relative">
                  <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[#64748B]" />
                  <Input
                    value={searchQuery}
                    onChange={(e) => { setSearchQuery(e.target.value); setTablePage(1); }}
                    placeholder="Search questions..."
                    className="pl-10"
                  />
                </div>
              </div>

              {/* Question table */}
              <div className="rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden">
                <table className="w-full text-sm">
                  <thead className="bg-gray-50 border-b">
                    <tr>
                      <th className="w-10 px-3 py-3">
                        <Checkbox
                          checked={
                            paginatedIndices.length > 0 &&
                            paginatedIndices.every((idx) => ingestor.selectedIds.has(idx))
                          }
                          onCheckedChange={(checked) => {
                            paginatedIndices.forEach((idx) => {
                              if (checked && !ingestor.selectedIds.has(idx)) {
                                ingestor.toggleQuestion(idx);
                              } else if (!checked && ingestor.selectedIds.has(idx)) {
                                ingestor.toggleQuestion(idx);
                              }
                            });
                          }}
                        />
                      </th>
                      <th className="w-10 px-2 py-3 text-left font-medium text-[#64748B]">#</th>
                      <th className="px-3 py-3 text-left font-medium text-[#64748B]">Source</th>
                      <th className="px-3 py-3 text-left font-medium text-[#64748B]">Exercise</th>
                      <th className="w-16 px-3 py-3 text-left font-medium text-[#64748B]">Q.No</th>
                      <th className="px-3 py-3 text-left font-medium text-[#64748B]">Question</th>
                      <th className="px-3 py-3 text-left font-medium text-[#64748B]">Type</th>
                      <th className="px-3 py-3 text-left font-medium text-[#64748B]">Diff.</th>
                      <th className="w-16 px-3 py-3 text-left font-medium text-[#64748B]">Marks</th>
                      <th className="px-3 py-3 text-left font-medium text-[#64748B]">Answer</th>
                      <th className="w-10 px-3 py-3 text-left font-medium text-[#64748B]">Status</th>
                      <th className="w-8 px-2 py-3" />
                    </tr>
                  </thead>
                  <tbody>
                    {paginatedQuestions.map((q, pageIdx) => {
                      const originalIdx = paginatedIndices[pageIdx];
                      const status = ingestor.getQuestionStatus(q);
                      const isExpanded = expandedRow === originalIdx;

                      return (
                        <QuestionRow
                          key={originalIdx}
                          question={q}
                          originalIdx={originalIdx}
                          displayNum={(tablePage - 1) * PAGE_SIZE + pageIdx + 1}
                          isSelected={ingestor.selectedIds.has(originalIdx)}
                          isExpanded={isExpanded}
                          status={status}
                          onToggleSelect={() => ingestor.toggleQuestion(originalIdx)}
                          onToggleExpand={() =>
                            setExpandedRow(isExpanded ? null : originalIdx)
                          }
                          onUpdate={(changes) => ingestor.updateQuestion(originalIdx, changes)}
                        />
                      );
                    })}
                  </tbody>
                </table>

                {filteredQuestions.length === 0 && (
                  <div className="p-12 text-center text-[#64748B]">
                    No questions match your filters
                  </div>
                )}
              </div>

              {/* Pagination */}
              {totalPages > 1 && (
                <div className="flex items-center justify-between">
                  <span className="text-sm text-[#64748B]">
                    Page {tablePage} of {totalPages} ({filteredQuestions.length} questions)
                  </span>
                  <div className="flex gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={tablePage <= 1}
                      onClick={() => setTablePage((p) => p - 1)}
                    >
                      <ChevronLeft className="h-4 w-4" />
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={tablePage >= totalPages}
                      onClick={() => setTablePage((p) => p + 1)}
                    >
                      <ChevronRight className="h-4 w-4" />
                    </Button>
                  </div>
                </div>
              )}

              {/* Bulk actions bar */}
              {selectedCount > 0 && (
                <div className="sticky bottom-0 rounded-xl border border-gray-200 bg-white/95 p-4 shadow-lg backdrop-blur-sm">
                  <div className="flex flex-wrap items-center gap-3">
                    <span className="text-sm font-medium text-[#1E293B]">
                      {selectedCount} selected:
                    </span>

                    <Button
                      variant="outline"
                      size="sm"
                      onClick={handleDeleteSelected}
                      className="gap-1 text-[#EF4444] hover:bg-red-50"
                    >
                      <Trash2 className="h-3.5 w-3.5" /> Delete
                    </Button>

                    <Select
                      value={bulkTypeChange || undefined}
                      onValueChange={(v) => handleBulkTypeChange(v as QuestionType)}
                    >
                      <SelectTrigger className="w-[160px] h-8 text-xs">
                        <SelectValue placeholder="Change Type" />
                      </SelectTrigger>
                      <SelectContent>
                        {ALL_QUESTION_TYPES.map((t) => (
                          <SelectItem key={t} value={t}>{t}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>

                    <Select
                      value={bulkDifficultyChange || undefined}
                      onValueChange={(v) => handleBulkDifficultyChange(v as Difficulty)}
                    >
                      <SelectTrigger className="w-[140px] h-8 text-xs">
                        <SelectValue placeholder="Change Difficulty" />
                      </SelectTrigger>
                      <SelectContent>
                        {ALL_DIFFICULTIES.map((d) => (
                          <SelectItem key={d} value={d} className="capitalize">{d}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>

                    <Select
                      value={bulkSourceChange || undefined}
                      onValueChange={(v) => handleBulkSourceChange(v as TextbookSourceType)}
                    >
                      <SelectTrigger className="w-[160px] h-8 text-xs">
                        <SelectValue placeholder="Change Source" />
                      </SelectTrigger>
                      <SelectContent>
                        {Object.entries(SOURCE_LABELS).map(([key, label]) => (
                          <SelectItem key={key} value={key}>{label}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </div>
              )}

              {/* Post-save actions */}
              <div className="flex items-center justify-between rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
                <Button variant="outline" onClick={handleReset} className="gap-2">
                  <BookOpen className="h-4 w-4" />
                  Extract Another Chapter
                </Button>
                <Button
                  variant="outline"
                  onClick={() => navigate('/question-bank')}
                  className="gap-2"
                >
                  View in Question Bank
                  <ChevronRight className="h-4 w-4" />
                </Button>
              </div>
            </div>
          )}

          {/* Empty state if extraction produced nothing */}
          {!ingestor.isExtracting && ingestor.extractedQuestions.length === 0 && (
            <div className="rounded-xl border border-gray-200 bg-white p-12 text-center shadow-sm">
              {ingestor.extractionError ? (
                <>
                  <XCircle className="mx-auto h-12 w-12 text-[#EF4444]" />
                  <h3 className="mt-3 text-lg font-semibold text-[#1E293B]">Extraction Failed</h3>
                  <p className="mt-1 text-sm text-[#64748B]">{ingestor.extractionError}</p>
                  <Button onClick={() => setStep(3)} className="mt-4 bg-[#4F46E5] text-white hover:bg-indigo-700">
                    Try Again
                  </Button>
                </>
              ) : (
                <>
                  <FileText className="mx-auto h-12 w-12 text-gray-300" />
                  <h3 className="mt-3 text-lg font-semibold text-[#1E293B]">No Questions Found</h3>
                  <p className="mt-1 text-sm text-[#64748B]">
                    The extraction didn't find any questions. Try different settings or a different PDF.
                  </p>
                  <Button onClick={() => setStep(3)} className="mt-4 bg-[#4F46E5] text-white hover:bg-indigo-700">
                    Go Back to Settings
                  </Button>
                </>
              )}
            </div>
          )}
        </>
      )}

      {/* Confirm save dialog */}
      <ConfirmDialog
        open={confirmSave}
        title="Save Questions"
        message={`You are about to save ${selectedCount} questions to the question bank. ${
          needsReviewCount > 0 ? `${needsReviewCount} questions have warnings.` : ''
        } Proceed?`}
        confirmLabel="Save Questions"
        variant="warning"
        onConfirm={handleSave}
        onCancel={() => setConfirmSave(false)}
      />
    </div>
  );
}

// ── Question Row Sub-component ──────────────────────────────

interface QuestionRowProps {
  question: TextbookExtractedQuestion;
  originalIdx: number;
  displayNum: number;
  isSelected: boolean;
  isExpanded: boolean;
  status: 'ready' | 'needs_review' | 'error';
  onToggleSelect: () => void;
  onToggleExpand: () => void;
  onUpdate: (changes: Partial<TextbookExtractedQuestion>) => void;
}

function QuestionRow({
  question: q,
  originalIdx,
  displayNum,
  isSelected,
  isExpanded,
  status,
  onToggleSelect,
  onToggleExpand,
  onUpdate,
}: QuestionRowProps) {
  return (
    <>
      <tr
        className={`border-b transition-colors ${
          isSelected ? 'bg-indigo-50/50' : 'hover:bg-gray-50'
        }`}
      >
        {/* Checkbox */}
        <td className="px-3 py-3">
          <Checkbox
            checked={isSelected}
            onCheckedChange={onToggleSelect}
          />
        </td>

        {/* Row number */}
        <td className="px-2 py-3 text-xs text-[#64748B]">{displayNum}</td>

        {/* Source badge */}
        <td className="px-3 py-3">
          <Badge className={`text-[10px] ${SOURCE_COLORS[q.textbook_source_type]}`}>
            {SOURCE_LABELS[q.textbook_source_type]}
          </Badge>
        </td>

        {/* Exercise name (editable) */}
        <td className="px-3 py-3">
          <input
            className="w-28 rounded border border-transparent bg-transparent px-1 py-0.5 text-xs text-[#1E293B] hover:border-gray-300 focus:border-[#4F46E5] focus:outline-none"
            value={q.exercise_name}
            onChange={(e) => onUpdate({ exercise_name: e.target.value })}
            onClick={(e) => e.stopPropagation()}
          />
        </td>

        {/* Question number (editable) */}
        <td className="px-3 py-3">
          <input
            className="w-16 rounded border border-transparent bg-transparent px-1 py-0.5 text-xs text-[#1E293B] hover:border-gray-300 focus:border-[#4F46E5] focus:outline-none"
            value={q.question_number}
            onChange={(e) => onUpdate({ question_number: e.target.value })}
            onClick={(e) => e.stopPropagation()}
          />
        </td>

        {/* Question preview */}
        <td className="max-w-[200px] px-3 py-3 cursor-pointer" onClick={onToggleExpand}>
          <div className="truncate text-xs text-[#1E293B]">
            <MathRenderer content={smartTruncate(q.question_text, 100)} inline />
          </div>
        </td>

        {/* Type badge (dropdown) */}
        <td className="px-3 py-3">
          <Select
            value={q.question_type}
            onValueChange={(v) => onUpdate({ question_type: v as QuestionType })}
          >
            <SelectTrigger className="h-7 w-[120px] border-0 bg-transparent p-0 text-xs shadow-none">
              <Badge variant="outline" className="text-[10px]">{q.question_type}</Badge>
            </SelectTrigger>
            <SelectContent>
              {ALL_QUESTION_TYPES.map((t) => (
                <SelectItem key={t} value={t} className="text-xs">{t}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </td>

        {/* Difficulty badge (dropdown) */}
        <td className="px-3 py-3">
          <Select
            value={q.difficulty}
            onValueChange={(v) => onUpdate({ difficulty: v as Difficulty })}
          >
            <SelectTrigger className="h-7 w-[90px] border-0 bg-transparent p-0 text-xs shadow-none">
              <Badge className={`text-[10px] capitalize ${DIFFICULTY_COLORS[q.difficulty]}`}>
                {q.difficulty}
              </Badge>
            </SelectTrigger>
            <SelectContent>
              {ALL_DIFFICULTIES.map((d) => (
                <SelectItem key={d} value={d} className="capitalize text-xs">{d}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </td>

        {/* Marks (editable) */}
        <td className="px-3 py-3">
          <input
            type="number"
            min={1}
            max={20}
            className="w-12 rounded border border-transparent bg-transparent px-1 py-0.5 text-center text-xs font-medium text-[#F59E0B] hover:border-gray-300 focus:border-[#4F46E5] focus:outline-none"
            value={q.marks}
            onChange={(e) => onUpdate({ marks: parseInt(e.target.value) || 1 })}
            onClick={(e) => e.stopPropagation()}
          />
        </td>

        {/* Answer preview */}
        <td className="max-w-[120px] px-3 py-3 cursor-pointer" onClick={onToggleExpand}>
          <span className="truncate text-xs text-[#64748B]">
            {q.answer_text ? smartTruncate(q.answer_text, 60) : '—'}
          </span>
        </td>

        {/* Status icon */}
        <td className="px-3 py-3">
          {status === 'ready' && <span title="Ready"><CheckCircle2 className="h-4 w-4 text-[#10B981]" /></span>}
          {status === 'needs_review' && (
            <span title="Needs Review"><AlertTriangle className="h-4 w-4 text-[#F59E0B]" /></span>
          )}
          {status === 'error' && <span title="Error"><XCircle className="h-4 w-4 text-[#EF4444]" /></span>}
        </td>

        {/* Expand chevron */}
        <td className="px-2 py-3 cursor-pointer" onClick={onToggleExpand}>
          {isExpanded ? (
            <ChevronUp className="h-4 w-4 text-[#64748B]" />
          ) : (
            <ChevronDown className="h-4 w-4 text-[#64748B]" />
          )}
        </td>
      </tr>

      {/* Expanded row */}
      {isExpanded && (
        <tr>
          <td colSpan={12} className="bg-gray-50 px-6 py-5">
            <ExpandedQuestionForm question={q} onUpdate={onUpdate} />
          </td>
        </tr>
      )}
    </>
  );
}

// ── Expanded Form Sub-component ─────────────────────────────

interface ExpandedFormProps {
  question: TextbookExtractedQuestion;
  onUpdate: (changes: Partial<TextbookExtractedQuestion>) => void;
}

function ExpandedQuestionForm({ question: q, onUpdate }: ExpandedFormProps) {
  return (
    <div className="space-y-4">
      {/* Question text + live preview */}
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-[#64748B]">Question Text</label>
          <Textarea
            value={q.question_text}
            onChange={(e) => onUpdate({ question_text: e.target.value })}
            rows={5}
            className="font-mono text-sm"
          />
        </div>
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-[#64748B]">Live Preview</label>
          <div className="min-h-[120px] rounded-lg border border-gray-200 bg-white p-3 text-sm">
            <MathRenderer content={q.question_text} />
          </div>
        </div>
      </div>

      {/* Answer text */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium text-[#64748B]">Answer Text</label>
        <Textarea
          value={q.answer_text ?? ''}
          onChange={(e) => onUpdate({ answer_text: e.target.value || null })}
          rows={3}
          className="font-mono text-sm"
        />
        {q.answer_text && (
          <div className="rounded-lg bg-emerald-50 p-3">
            <p className="text-[10px] font-semibold text-[#10B981] mb-1">Preview:</p>
            <MathRenderer content={q.answer_text} className="text-sm" />
          </div>
        )}
      </div>

      {/* MCQ options */}
      {q.question_type === 'MCQ' && (
        <div className="space-y-2">
          <label className="text-xs font-medium text-[#64748B]">Options</label>
          <div className="grid gap-2 sm:grid-cols-2">
            {['A', 'B', 'C', 'D'].map((letter, i) => (
              <div key={letter} className="flex items-center gap-2">
                <span
                  className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-bold ${
                    q.correct_option === letter
                      ? 'bg-[#10B981] text-white'
                      : 'bg-gray-200 text-gray-600'
                  }`}
                >
                  {letter}
                </span>
                <Input
                  value={q.options?.[i] ?? ''}
                  onChange={(e) => {
                    const newOptions = [...(q.options ?? ['', '', '', ''])];
                    newOptions[i] = e.target.value;
                    onUpdate({ options: newOptions });
                  }}
                  className="text-sm"
                />
              </div>
            ))}
          </div>
          <div className="flex items-center gap-2">
            <label className="text-xs font-medium text-[#64748B]">Correct Option:</label>
            <Select
              value={q.correct_option ?? undefined}
              onValueChange={(v) => onUpdate({ correct_option: v })}
            >
              <SelectTrigger className="w-20 h-8">
                <SelectValue placeholder="—" />
              </SelectTrigger>
              <SelectContent>
                {['A', 'B', 'C', 'D'].map((l) => (
                  <SelectItem key={l} value={l}>{l}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
      )}

      {/* Solution steps */}
      {(q.question_type === 'Numerical' || q.question_type === 'Long Answer' || q.textbook_source_type === 'solved_example') && (
        <div className="space-y-2">
          <label className="text-xs font-medium text-[#64748B]">Solution Steps</label>
          {(q.solution_steps ?? []).map((step, i) => (
            <div key={i} className="flex items-start gap-2">
              <span className="mt-2 text-xs font-medium text-[#64748B]">{i + 1}.</span>
              <Textarea
                value={step}
                onChange={(e) => {
                  const newSteps = [...(q.solution_steps ?? [])];
                  newSteps[i] = e.target.value;
                  onUpdate({ solution_steps: newSteps });
                }}
                rows={2}
                className="flex-1 font-mono text-sm"
              />
              <button
                onClick={() => {
                  const newSteps = (q.solution_steps ?? []).filter((_, j) => j !== i);
                  onUpdate({ solution_steps: newSteps.length > 0 ? newSteps : null });
                }}
                className="mt-1.5 rounded p-1 text-gray-400 hover:bg-red-50 hover:text-[#EF4444]"
              >
                <Minus className="h-4 w-4" />
              </button>
            </div>
          ))}
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              const newSteps = [...(q.solution_steps ?? []), ''];
              onUpdate({ solution_steps: newSteps });
            }}
            className="gap-1 text-xs"
          >
            <Plus className="h-3 w-3" /> Add Step
          </Button>
        </div>
      )}

      {/* Match the Following */}
      {q.question_type === 'Match the Following' && (
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-2">
            <label className="text-xs font-medium text-[#64748B]">Left Column</label>
            {(q.match_left ?? []).map((item, i) => (
              <Input
                key={i}
                value={item}
                onChange={(e) => {
                  const newLeft = [...(q.match_left ?? [])];
                  newLeft[i] = e.target.value;
                  onUpdate({ match_left: newLeft });
                }}
                className="text-sm"
              />
            ))}
          </div>
          <div className="space-y-2">
            <label className="text-xs font-medium text-[#64748B]">Right Column</label>
            {(q.match_right ?? []).map((item, i) => (
              <Input
                key={i}
                value={item}
                onChange={(e) => {
                  const newRight = [...(q.match_right ?? [])];
                  newRight[i] = e.target.value;
                  onUpdate({ match_right: newRight });
                }}
                className="text-sm"
              />
            ))}
          </div>
        </div>
      )}

      {/* Metadata row */}
      <div className="grid gap-3 sm:grid-cols-4">
        <div className="space-y-1">
          <label className="text-xs font-medium text-[#64748B]">Source Type</label>
          <Select
            value={q.textbook_source_type}
            onValueChange={(v) => onUpdate({ textbook_source_type: v as TextbookSourceType })}
          >
            <SelectTrigger className="h-8 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {Object.entries(SOURCE_LABELS).map(([key, label]) => (
                <SelectItem key={key} value={key} className="text-xs">{label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <label className="text-xs font-medium text-[#64748B]">Exercise Name</label>
          <Input
            value={q.exercise_name}
            onChange={(e) => onUpdate({ exercise_name: e.target.value })}
            className="h-8 text-xs"
          />
        </div>
        <div className="space-y-1">
          <label className="text-xs font-medium text-[#64748B]">Question Number</label>
          <Input
            value={q.question_number}
            onChange={(e) => onUpdate({ question_number: e.target.value })}
            className="h-8 text-xs"
          />
        </div>
        <div className="space-y-1">
          <label className="text-xs font-medium text-[#64748B]">Marks</label>
          <Input
            type="number"
            min={1}
            max={20}
            value={q.marks}
            onChange={(e) => onUpdate({ marks: parseInt(e.target.value) || 1 })}
            className="h-8 text-xs"
          />
        </div>
      </div>
    </div>
  );
}
