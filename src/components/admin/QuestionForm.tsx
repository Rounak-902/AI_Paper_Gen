import { useState, useCallback, useEffect } from 'react';
import { useAuth } from '@/context/AuthContext';
import { supabase } from '@/lib/supabaseClient';
import { useTopicSuggestion } from '@/hooks/useTopicSuggestion';
import ContextSection from './ContextSection';
import TopicSelector from './TopicSelector';
import QuestionDetailsSection from './QuestionDetailsSection';
import ConditionalFields from './ConditionalFields';
import SourceSection from './SourceSection';
import { Button } from '@/components/ui/button';
import { Separator } from '@/components/ui/separator';
import { toast } from 'sonner';
import { Save, Plus, Trash2, Loader2 } from 'lucide-react';
import type {
  QuestionFormData,
  QuestionType,
  Difficulty,
  Source,
  QuestionBankItem,
} from '@/types/database';

const INITIAL_FORM: QuestionFormData = {
  boardId: '',
  classId: '',
  streamId: '',
  subjectId: '',
  chapterId: '',
  topicId: null,
  questionType: 'MCQ' as QuestionType,
  marks: 1,
  difficulty: 'medium' as Difficulty,
  questionText: '',
  answerText: '',
  answerHint: '',
  options: ['', '', '', ''],
  correctOption: '',
  blankAnswer: '',
  matchLeft: ['', ''],
  matchRight: ['', ''],
  matchAnswer: {},
  assertion: '',
  reason: '',
  solutionSteps: [''],
  source: 'textbook' as Source,
  bookName: '',
  exerciseNo: '',
  questionNo: '',
  pageNo: null,
};

interface Props {
  onFormChange: (form: QuestionFormData) => void;
  onSubjectNameChange: (name: string) => void;
  onChapterNameChange: (name: string) => void;
  onTopicNameChange: (name: string) => void;
  onQuestionSaved: () => void;
  editQuestion?: QuestionBankItem | null;
}

export default function QuestionForm({
  onFormChange,
  onSubjectNameChange,
  onChapterNameChange,
  onQuestionSaved,
  editQuestion,
}: Props) {
  const { user } = useAuth();
  const [form, setForm] = useState<QuestionFormData>({ ...INITIAL_FORM });
  const [saving, setSaving] = useState(false);
  const [showStream, setShowStream] = useState(false);
  const [subjectName, setSubjectName] = useState('');
  const [chapterName, setChapterName] = useState('');

  const topic = useTopicSuggestion();

  const isMaths = subjectName.toLowerCase().includes('math');

  // Sync form to parent
  useEffect(() => {
    onFormChange(form);
  }, [form, onFormChange]);

  // Load edit question
  useEffect(() => {
    if (editQuestion) {
      setForm({
        boardId: editQuestion.board_id ?? '',
        classId: editQuestion.class_id ?? '',
        streamId: editQuestion.stream_id ?? '',
        subjectId: editQuestion.subject_id ?? '',
        chapterId: editQuestion.chapter_id ?? '',
        topicId: editQuestion.topic_id,
        questionType: editQuestion.question_type,
        marks: editQuestion.marks,
        difficulty: editQuestion.difficulty ?? 'medium',
        questionText: editQuestion.question_text,
        answerText: editQuestion.answer_text ?? '',
        answerHint: editQuestion.answer_hint ?? '',
        options: (editQuestion.options as string[]) ?? ['', '', '', ''],
        correctOption: editQuestion.correct_option ?? '',
        blankAnswer: editQuestion.blank_answer ?? '',
        matchLeft: (editQuestion.match_left as string[]) ?? ['', ''],
        matchRight: (editQuestion.match_right as string[]) ?? ['', ''],
        matchAnswer: (editQuestion.match_answer as Record<string, string>) ?? {},
        assertion: editQuestion.assertion ?? '',
        reason: editQuestion.reason ?? '',
        solutionSteps: (editQuestion.solution_steps as string[]) ?? [''],
        source: editQuestion.source === 'ai_generated' ? 'textbook' : editQuestion.source,
        bookName: editQuestion.book_name ?? '',
        exerciseNo: editQuestion.exercise_no ?? '',
        questionNo: editQuestion.question_no ?? '',
        pageNo: editQuestion.page_no,
      });
    }
  }, [editQuestion]);

  const updateField = useCallback((field: string, value: unknown) => {
    setForm((prev) => ({ ...prev, [field]: value }));
  }, []);

  const handleQuestionTextBlur = () => {
    if (form.questionText && form.chapterId && subjectName && chapterName) {
      topic.suggest(subjectName, chapterName, form.chapterId, form.questionText);
    }
  };

  const buildQuestionText = (): string => {
    if (form.questionType === 'Assertion-Reason' && form.assertion && form.reason) {
      return `Assertion (A): ${form.assertion}\nReason (R): ${form.reason}`;
    }
    return form.questionText;
  };

  const validateForm = (): boolean => {
    if (!form.boardId || !form.classId || !form.subjectId || !form.chapterId) {
      toast.error('Please select board, class, subject, and chapter');
      return false;
    }
    if (!form.questionText && form.questionType !== 'Assertion-Reason') {
      toast.error('Please enter question text');
      return false;
    }
    if (form.questionType === 'Assertion-Reason' && (!form.assertion || !form.reason)) {
      toast.error('Please enter both assertion and reason');
      return false;
    }
    if (!form.answerText) {
      toast.error('Please enter answer text');
      return false;
    }
    if (form.questionType === 'MCQ' && form.options.some((o) => !o)) {
      toast.error('Please fill all MCQ options');
      return false;
    }
    return true;
  };

  const saveQuestion = async (addAnother: boolean) => {
    if (!validateForm() || !user) return;

    setSaving(true);
    const questionText = buildQuestionText();

    const { error } = await supabase.from('question_bank').insert({
      board_id: form.boardId,
      class_id: form.classId,
      stream_id: form.streamId || null,
      subject_id: form.subjectId,
      chapter_id: form.chapterId,
      topic_id: form.topicId,
      question_text: questionText,
      answer_text: form.answerText,
      answer_hint: form.answerHint || null,
      question_type: form.questionType,
      marks: form.marks,
      difficulty: form.difficulty,
      options: form.questionType === 'MCQ' || form.questionType === 'True/False' ? form.options : null,
      correct_option: form.correctOption || null,
      blank_answer: form.blankAnswer || null,
      match_left: form.questionType === 'Match the Following' ? form.matchLeft : null,
      match_right: form.questionType === 'Match the Following' ? form.matchRight : null,
      match_answer: form.questionType === 'Match the Following' ? form.matchAnswer : null,
      assertion: form.assertion || null,
      reason: form.reason || null,
      solution_steps: form.solutionSteps.some((s) => s) ? form.solutionSteps.filter((s) => s) : null,
      source: form.source,
      book_name: form.bookName || null,
      exercise_no: form.exerciseNo || null,
      question_no: form.questionNo || null,
      page_no: form.pageNo,
      created_by: user.id,
    });

    setSaving(false);

    if (error) {
      toast.error(error.message);
      return;
    }

    toast.success('Question saved!');
    onQuestionSaved();

    if (addAnother) {
      // Clear question fields, keep context
      setForm((prev) => ({
        ...prev,
        topicId: null,
        questionText: '',
        answerText: '',
        answerHint: '',
        options: ['', '', '', ''],
        correctOption: '',
        blankAnswer: '',
        matchLeft: ['', ''],
        matchRight: ['', ''],
        matchAnswer: {},
        assertion: '',
        reason: '',
        solutionSteps: [''],
        exerciseNo: '',
        questionNo: '',
        pageNo: null,
      }));
      topic.rejectTopic();
    }
  };

  const clearAll = () => {
    setForm({ ...INITIAL_FORM });
    setSubjectName('');
    setChapterName('');
    topic.rejectTopic();
  };

  // Ctrl+Enter shortcut
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        saveQuestion(true);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [form, saveQuestion]);

  return (
    <div className="space-y-6">
      {/* Section 1: Context */}
      <ContextSection
        boardId={form.boardId}
        classId={form.classId}
        streamId={form.streamId}
        subjectId={form.subjectId}
        chapterId={form.chapterId}
        onBoardChange={(id) => updateField('boardId', id)}
        onClassChange={(id) => {
          updateField('classId', id);
        }}
        onStreamChange={(id) => updateField('streamId', id)}
        onSubjectChange={(id, name) => {
          updateField('subjectId', id);
          setSubjectName(name);
          onSubjectNameChange(name);
        }}
        onChapterChange={(id, name) => {
          updateField('chapterId', id);
          setChapterName(name);
          onChapterNameChange(name);
        }}
        showStream={showStream}
        onStreamNeeded={setShowStream}
      />

      <Separator />

      {/* Section 2: Topic */}
      {form.chapterId && (
        <>
          <TopicSelector
            chapterId={form.chapterId}
            suggestedTopic={topic.suggestedTopic}
            topicLoading={topic.isLoading}
            selectedTopicId={form.topicId}
            onAcceptSuggestion={() => topic.acceptTopic(form.chapterId)}
            onRejectSuggestion={topic.rejectTopic}
            onSelectTopic={(id) => updateField('topicId', id)}
          />
          <Separator />
        </>
      )}

      {/* Section 3: Question Details */}
      <QuestionDetailsSection
        questionType={form.questionType}
        marks={form.marks}
        difficulty={form.difficulty}
        questionText={form.questionText}
        answerText={form.answerText}
        answerHint={form.answerHint}
        isMaths={isMaths}
        onTypeChange={(t) => updateField('questionType', t)}
        onMarksChange={(m) => updateField('marks', m)}
        onDifficultyChange={(d) => updateField('difficulty', d)}
        onQuestionTextChange={(t) => updateField('questionText', t)}
        onAnswerTextChange={(t) => updateField('answerText', t)}
        onAnswerHintChange={(h) => updateField('answerHint', h)}
        onQuestionTextBlur={handleQuestionTextBlur}
      />

      {/* Conditional fields */}
      <ConditionalFields
        questionType={form.questionType}
        options={form.options}
        correctOption={form.correctOption}
        blankAnswer={form.blankAnswer}
        matchLeft={form.matchLeft}
        matchRight={form.matchRight}
        matchAnswer={form.matchAnswer}
        assertion={form.assertion}
        reason={form.reason}
        solutionSteps={form.solutionSteps}
        onChange={updateField}
      />

      <Separator />

      {/* Section 4: Source */}
      <SourceSection
        source={form.source}
        bookName={form.bookName}
        exerciseNo={form.exerciseNo}
        questionNo={form.questionNo}
        pageNo={form.pageNo}
        onSourceChange={(s) => updateField('source', s)}
        onBookNameChange={(n) => updateField('bookName', n)}
        onExerciseNoChange={(n) => updateField('exerciseNo', n)}
        onQuestionNoChange={(n) => updateField('questionNo', n)}
        onPageNoChange={(p) => updateField('pageNo', p)}
      />

      <Separator />

      {/* Submit buttons */}
      <div className="flex flex-wrap gap-3">
        <Button
          onClick={() => saveQuestion(true)}
          disabled={saving}
          className="gap-2 bg-[#4F46E5] text-white hover:bg-indigo-700"
        >
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
          Save & Add Another
        </Button>
        <Button
          onClick={() => saveQuestion(false)}
          disabled={saving}
          variant="outline"
          className="gap-2"
        >
          <Save className="h-4 w-4" />
          Save & Preview
        </Button>
        <Button
          onClick={clearAll}
          variant="outline"
          className="gap-2 text-[#EF4444] border-red-200 hover:bg-red-50"
        >
          <Trash2 className="h-4 w-4" />
          Clear All
        </Button>
      </div>

      <p className="text-xs text-[#64748B]">
        💡 Tip: Press Ctrl+Enter to save & add another
      </p>
    </div>
  );
}
