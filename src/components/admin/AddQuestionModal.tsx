import { useState, useEffect } from 'react';
import { supabase } from '@/lib/supabaseClient';
import { useAuth } from '@/context/AuthContext';
import { useCascadingDropdowns } from '@/hooks/useCascadingDropdowns';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  X,
  Plus,
  Trash2,
  Loader2,
  CheckCircle2,
} from 'lucide-react';
import { toast } from 'sonner';

interface AddQuestionModalProps {
  open: boolean;
  onClose: () => void;
  onAdded?: () => void;
}

const SUGGESTED_TYPES = [
  'MCQ', 'Short Answer', 'Long Answer', 'Very Short Answer', 'One Word Answer',
  'Fill in the Blanks', 'True/False', 'Match the Following', 'Assertion-Reason',
  'Case Study', 'Numerical', 'Diagram Based', 'Distinguish Between',
  'Arrange in Chronological Order', 'Give Reasons', 'Answer in Brief',
  'Answer in Detail', 'Correct the Statement', 'Complete the Sentence',
  'Identify and Explain', 'Name the Following', 'Map Based',
];

export default function AddQuestionModal({ open, onClose, onAdded }: AddQuestionModalProps) {
  const { user } = useAuth();
  const { boards, classes, subjects, chapters, fetchClasses, fetchSubjects, fetchChapters } =
    useCascadingDropdowns();

  const [saving, setSaving] = useState(false);
  const [success, setSuccess] = useState(false);

  // Form fields
  const [boardId, setBoardId] = useState('');
  const [classId, setClassId] = useState('');
  const [subjectId, setSubjectId] = useState('');
  const [chapterId, setChapterId] = useState('');
  const [questionText, setQuestionText] = useState('');
  const [answerText, setAnswerText] = useState('');
  const [questionType, setQuestionType] = useState('');
  const [marks, setMarks] = useState(1);
  const [difficulty, setDifficulty] = useState('medium');
  const [source, setSource] = useState('manual');

  // MCQ-specific
  const [options, setOptions] = useState<string[]>(['', '', '', '']);
  const [correctOption, setCorrectOption] = useState('');

  // Fill in the Blanks
  const [blankAnswer, setBlankAnswer] = useState('');

  // True/False
  const [tfAnswer, setTfAnswer] = useState('');

  // Solution steps
  const [solutionSteps, setSolutionSteps] = useState<string[]>([]);

  // Book reference
  const [bookName, setBookName] = useState('');
  const [exerciseNo, setExerciseNo] = useState('');
  const [questionNo, setQuestionNo] = useState('');

  // Type suggestion dropdown
  const [typeOpen, setTypeOpen] = useState(false);
  const filteredTypes = questionType.trim()
    ? SUGGESTED_TYPES.filter((t) => t.toLowerCase().includes(questionType.toLowerCase()))
    : SUGGESTED_TYPES;

  // Cascading
  useEffect(() => {
    if (boardId) fetchClasses(boardId);
  }, [boardId]);
  useEffect(() => {
    if (classId) fetchSubjects(classId);
  }, [classId]);
  useEffect(() => {
    if (subjectId) fetchChapters(subjectId);
  }, [subjectId]);

  const resetForm = () => {
    setQuestionText('');
    setAnswerText('');
    setQuestionType('');
    setMarks(1);
    setDifficulty('medium');
    setOptions(['', '', '', '']);
    setCorrectOption('');
    setBlankAnswer('');
    setTfAnswer('');
    setSolutionSteps([]);
    setBookName('');
    setExerciseNo('');
    setQuestionNo('');
    setSuccess(false);
  };

  const handleSave = async () => {
    if (!questionText.trim()) {
      toast.error('Question text is required');
      return;
    }
    if (!questionType.trim()) {
      toast.error('Question type is required');
      return;
    }

    setSaving(true);

    const record: Record<string, unknown> = {
      board_id: boardId || null,
      class_id: classId || null,
      subject_id: subjectId || null,
      chapter_id: chapterId || null,
      question_text: questionText.trim(),
      answer_text: answerText.trim() || null,
      question_type: questionType.trim(),
      marks,
      difficulty,
      source,
      is_active: true,
      times_used: 0,
      created_by: user?.id ?? null,
    };

    // MCQ fields
    if (questionType === 'MCQ') {
      const filledOptions = options.filter((o) => o.trim());
      if (filledOptions.length >= 2) {
        record.options = filledOptions;
        record.correct_option = correctOption || null;
      }
    }

    // Fill in the Blanks
    if (questionType === 'Fill in the Blanks' && blankAnswer.trim()) {
      record.blank_answer = blankAnswer.trim();
    }

    // True/False
    if (questionType === 'True/False' && tfAnswer) {
      record.answer_text = tfAnswer;
    }

    // Solution steps
    const filledSteps = solutionSteps.filter((s) => s.trim());
    if (filledSteps.length > 0) {
      record.solution_steps = filledSteps;
    }

    // Book reference
    if (bookName.trim()) record.book_name = bookName.trim();
    if (exerciseNo.trim()) record.exercise_no = exerciseNo.trim();
    if (questionNo.trim()) record.question_no = questionNo.trim();

    const { error } = await supabase.from('question_bank').insert(record);

    setSaving(false);

    if (error) {
      toast.error(`Failed to save: ${error.message}`);
    } else {
      setSuccess(true);
      toast.success('Question added to bank!');
      onAdded?.();
    }
  };

  const handleAddAnother = () => {
    resetForm();
  };

  if (!open) return null;

  const isMCQ = questionType === 'MCQ';
  const isFillBlanks = questionType === 'Fill in the Blanks';
  const isTF = questionType === 'True/False';

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/50 p-4 backdrop-blur-sm">
      <div className="relative my-8 w-full max-w-3xl rounded-2xl border border-gray-200 bg-white shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b px-6 py-4">
          <div>
            <h2 className="text-xl font-bold text-[#1E293B]">Add Question</h2>
            <p className="text-sm text-[#64748B]">Manually add a question to the bank</p>
          </div>
          <button onClick={onClose} className="rounded-lg p-2 hover:bg-gray-100">
            <X className="h-5 w-5 text-[#64748B]" />
          </button>
        </div>

        {/* Success state */}
        {success ? (
          <div className="flex flex-col items-center gap-4 p-12">
            <CheckCircle2 className="h-16 w-16 text-[#10B981]" />
            <h3 className="text-xl font-semibold text-[#1E293B]">Question Added!</h3>
            <p className="text-sm text-[#64748B]">Your question has been saved to the bank.</p>
            <div className="flex gap-3">
              <Button variant="outline" onClick={onClose}>
                Close
              </Button>
              <Button onClick={handleAddAnother} className="bg-[#4F46E5] text-white hover:bg-indigo-700">
                <Plus className="mr-1 h-4 w-4" /> Add Another
              </Button>
            </div>
          </div>
        ) : (
          <div className="max-h-[70vh] overflow-y-auto p-6 space-y-5">
            {/* Row 1: Board, Class, Subject, Chapter */}
            <div className="grid gap-3 grid-cols-2 md:grid-cols-4">
              <div className="space-y-1">
                <Label className="text-xs">Board</Label>
                <Select value={boardId || undefined} onValueChange={(v) => { if (v) { setBoardId(v); setClassId(''); setSubjectId(''); setChapterId(''); } }}>
                  <SelectTrigger><SelectValue placeholder="Board" /></SelectTrigger>
                  <SelectContent>
                    {boards.map((b) => (
                      <SelectItem key={b.board_id} value={b.board_id}>{b.board_name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label className="text-xs">Class</Label>
                <Select value={classId || undefined} onValueChange={(v) => { if (v) { setClassId(v); setSubjectId(''); setChapterId(''); } }} disabled={!boardId}>
                  <SelectTrigger><SelectValue placeholder="Class" /></SelectTrigger>
                  <SelectContent>
                    {classes.map((c) => (
                      <SelectItem key={c.class_id} value={c.class_id}>Class {c.class_name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label className="text-xs">Subject</Label>
                <Select value={subjectId || undefined} onValueChange={(v) => { if (v) { setSubjectId(v); setChapterId(''); } }} disabled={!classId}>
                  <SelectTrigger><SelectValue placeholder="Subject" /></SelectTrigger>
                  <SelectContent>
                    {subjects.map((s) => (
                      <SelectItem key={s.subject_id} value={s.subject_id}>{s.subject_name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label className="text-xs">Chapter</Label>
                <Select value={chapterId || undefined} onValueChange={(v) => v && setChapterId(v)} disabled={!subjectId}>
                  <SelectTrigger><SelectValue placeholder="Chapter" /></SelectTrigger>
                  <SelectContent>
                    {chapters.map((c) => (
                      <SelectItem key={c.chapter_id} value={c.chapter_id}>{c.chapter_name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>

            {/* Question Text */}
            <div className="space-y-1">
              <Label>Question Text *</Label>
              <Textarea
                value={questionText}
                onChange={(e) => setQuestionText(e.target.value)}
                placeholder="Enter your question here..."
                rows={3}
                className="resize-y"
              />
            </div>

            {/* Row: Type, Marks, Difficulty, Source */}
            <div className="grid gap-3 grid-cols-2 md:grid-cols-4">
              {/* Question Type - free text with suggestions */}
              <div className="space-y-1 relative">
                <Label className="text-xs">Type *</Label>
                <input
                  type="text"
                  value={questionType}
                  placeholder="e.g., MCQ"
                  onFocus={() => setTypeOpen(true)}
                  onBlur={() => setTimeout(() => setTypeOpen(false), 150)}
                  onChange={(e) => { setQuestionType(e.target.value); setTypeOpen(true); }}
                  className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-xs transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                />
                {typeOpen && filteredTypes.length > 0 && (
                  <div className="absolute left-0 right-0 top-full z-50 mt-1 max-h-40 overflow-auto rounded-md border border-gray-200 bg-white py-1 shadow-lg">
                    {filteredTypes.map((t) => (
                      <button
                        key={t}
                        type="button"
                        onMouseDown={(e) => e.preventDefault()}
                        onClick={() => { setQuestionType(t); setTypeOpen(false); }}
                        className="flex w-full items-center px-3 py-1.5 text-left text-sm text-[#1E293B] hover:bg-indigo-50 hover:text-[#4F46E5]"
                      >
                        {t}
                      </button>
                    ))}
                  </div>
                )}
              </div>

              <div className="space-y-1">
                <Label className="text-xs">Marks</Label>
                <Input type="number" min={1} value={marks} onChange={(e) => setMarks(parseInt(e.target.value) || 1)} className="h-9" />
              </div>

              <div className="space-y-1">
                <Label className="text-xs">Difficulty</Label>
                <Select value={difficulty} onValueChange={(v) => v && setDifficulty(v)}>
                  <SelectTrigger className="h-9"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="easy">Easy</SelectItem>
                    <SelectItem value="medium">Medium</SelectItem>
                    <SelectItem value="hard">Hard</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-1">
                <Label className="text-xs">Source</Label>
                <Select value={source} onValueChange={(v) => v && setSource(v)}>
                  <SelectTrigger className="h-9"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="manual">Manual</SelectItem>
                    <SelectItem value="textbook">Textbook</SelectItem>
                    <SelectItem value="other">Other</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>

            {/* MCQ Options */}
            {isMCQ && (
              <div className="space-y-2 rounded-lg border border-indigo-100 bg-indigo-50/50 p-4">
                <Label className="text-xs font-medium text-[#4F46E5]">MCQ Options</Label>
                <div className="grid gap-2 sm:grid-cols-2">
                  {options.map((opt, i) => (
                    <div key={i} className="flex items-center gap-2">
                      <span className="text-xs font-semibold text-[#64748B] w-5">{String.fromCharCode(65 + i)}.</span>
                      <Input
                        value={opt}
                        onChange={(e) => {
                          const newOpts = [...options];
                          newOpts[i] = e.target.value;
                          setOptions(newOpts);
                        }}
                        placeholder={`Option ${String.fromCharCode(65 + i)}`}
                        className="h-8 text-sm"
                      />
                    </div>
                  ))}
                </div>
                <div className="flex items-center gap-2">
                  <Label className="text-xs">Correct Option:</Label>
                  <Select value={correctOption || undefined} onValueChange={(v) => v && setCorrectOption(v)}>
                    <SelectTrigger className="h-8 w-24"><SelectValue placeholder="—" /></SelectTrigger>
                    <SelectContent>
                      {options.map((_, i) => (
                        <SelectItem key={i} value={String.fromCharCode(65 + i)}>{String.fromCharCode(65 + i)}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => setOptions([...options, ''])}
                    className="ml-auto text-xs"
                  >
                    <Plus className="mr-1 h-3 w-3" /> Add Option
                  </Button>
                </div>
              </div>
            )}

            {/* Fill in the Blanks */}
            {isFillBlanks && (
              <div className="space-y-1">
                <Label className="text-xs">Blank Answer</Label>
                <Input value={blankAnswer} onChange={(e) => setBlankAnswer(e.target.value)} placeholder="The correct word/phrase for the blank" />
              </div>
            )}

            {/* True/False */}
            {isTF && (
              <div className="space-y-1">
                <Label className="text-xs">Correct Answer</Label>
                <Select value={tfAnswer || undefined} onValueChange={(v) => v && setTfAnswer(v)}>
                  <SelectTrigger className="h-9 w-32"><SelectValue placeholder="Select" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="True">True</SelectItem>
                    <SelectItem value="False">False</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            )}

            {/* Answer */}
            {!isTF && (
              <div className="space-y-1">
                <Label>Answer / Explanation</Label>
                <Textarea
                  value={answerText}
                  onChange={(e) => setAnswerText(e.target.value)}
                  placeholder="Enter the answer or detailed explanation..."
                  rows={3}
                  className="resize-y"
                />
              </div>
            )}

            {/* Solution Steps */}
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <Label className="text-xs">Solution Steps (optional)</Label>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => setSolutionSteps([...solutionSteps, ''])}
                  className="text-xs"
                >
                  <Plus className="mr-1 h-3 w-3" /> Add Step
                </Button>
              </div>
              {solutionSteps.map((step, i) => (
                <div key={i} className="flex items-center gap-2">
                  <span className="text-xs font-semibold text-[#64748B] w-5">{i + 1}.</span>
                  <Input
                    value={step}
                    onChange={(e) => {
                      const newSteps = [...solutionSteps];
                      newSteps[i] = e.target.value;
                      setSolutionSteps(newSteps);
                    }}
                    placeholder={`Step ${i + 1}`}
                    className="h-8 text-sm"
                  />
                  <button onClick={() => setSolutionSteps(solutionSteps.filter((_, j) => j !== i))} className="text-red-400 hover:text-red-600">
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              ))}
            </div>

            {/* Book Reference (collapsible) */}
            <details className="rounded-lg border border-gray-200 p-3">
              <summary className="cursor-pointer text-xs font-medium text-[#64748B]">
                📚 Book Reference (optional)
              </summary>
              <div className="mt-3 grid gap-3 grid-cols-3">
                <div className="space-y-1">
                  <Label className="text-xs">Book Name</Label>
                  <Input value={bookName} onChange={(e) => setBookName(e.target.value)} placeholder="e.g., NCERT" className="h-8 text-sm" />
                </div>
                <div className="space-y-1">
                  <Label className="text-xs">Exercise No.</Label>
                  <Input value={exerciseNo} onChange={(e) => setExerciseNo(e.target.value)} placeholder="e.g., Ex 3.2" className="h-8 text-sm" />
                </div>
                <div className="space-y-1">
                  <Label className="text-xs">Question No.</Label>
                  <Input value={questionNo} onChange={(e) => setQuestionNo(e.target.value)} placeholder="e.g., Q.5" className="h-8 text-sm" />
                </div>
              </div>
            </details>
          </div>
        )}

        {/* Footer */}
        {!success && (
          <div className="flex items-center justify-end gap-3 border-t px-6 py-4">
            <Button variant="outline" onClick={onClose} disabled={saving}>
              Cancel
            </Button>
            <Button
              onClick={handleSave}
              disabled={saving || !questionText.trim() || !questionType.trim()}
              className="bg-[#4F46E5] text-white hover:bg-indigo-700"
            >
              {saving ? (
                <><Loader2 className="mr-2 h-4 w-4 animate-spin" /> Saving...</>
              ) : (
                <><Plus className="mr-1 h-4 w-4" /> Add Question</>
              )}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
