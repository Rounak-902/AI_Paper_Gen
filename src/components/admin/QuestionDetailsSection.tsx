import { Label } from '@/components/ui/label';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import type { QuestionType, Difficulty } from '@/types/database';
import { Calculator } from 'lucide-react';
import MathSyntaxHint from '@/components/shared/MathSyntaxHint';
import InsertMathPopover from '@/components/shared/InsertMathPopover';
import { useRef } from 'react';

const ALL_TYPES: QuestionType[] = [
  'MCQ', 'Short Answer', 'Long Answer', 'Very Short Answer', 'Numerical',
  'Fill in the Blanks', 'True/False', 'Assertion-Reason', 'Case Study',
  'Match the Following', 'One Word Answer', 'Diagram Based',
];

const MATHS_TYPES: QuestionType[] = ['Numerical', 'MCQ', 'Fill in the Blanks'];

interface Props {
  questionType: QuestionType;
  marks: number;
  difficulty: Difficulty;
  questionText: string;
  answerText: string;
  answerHint: string;
  isMaths: boolean;
  onTypeChange: (type: QuestionType) => void;
  onMarksChange: (marks: number) => void;
  onDifficultyChange: (diff: Difficulty) => void;
  onQuestionTextChange: (text: string) => void;
  onAnswerTextChange: (text: string) => void;
  onAnswerHintChange: (hint: string) => void;
  onQuestionTextBlur: () => void;
}

export default function QuestionDetailsSection({
  questionType,
  marks,
  difficulty,
  questionText,
  answerText,
  answerHint,
  isMaths,
  onTypeChange,
  onMarksChange,
  onDifficultyChange,
  onQuestionTextChange,
  onAnswerTextChange,
  onAnswerHintChange,
  onQuestionTextBlur,
}: Props) {
  const types = isMaths ? MATHS_TYPES : ALL_TYPES;
  const questionTextareaRef = useRef<HTMLTextAreaElement>(null);
  const answerTextareaRef = useRef<HTMLTextAreaElement>(null);
  const hintTextareaRef = useRef<HTMLTextAreaElement>(null);

  const insertAtCursor = (
    ref: React.RefObject<HTMLTextAreaElement | null>,
    snippet: string,
    currentValue: string,
    onChange: (val: string) => void,
  ) => {
    const el = ref.current;
    if (!el) {
      onChange(currentValue + snippet);
      return;
    }
    const start = el.selectionStart ?? currentValue.length;
    const end = el.selectionEnd ?? currentValue.length;
    const newValue = currentValue.slice(0, start) + snippet + currentValue.slice(end);
    onChange(newValue);
    // Restore cursor position after React re-render
    requestAnimationFrame(() => {
      el.selectionStart = el.selectionEnd = start + snippet.length;
      el.focus();
    });
  };

  return (
    <div className="space-y-4">
      <h3 className="text-sm font-semibold uppercase text-[#64748B]">Question Details</h3>

      {isMaths && (
        <div className="flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          <Calculator className="h-4 w-4 text-[#F59E0B]" />
          Mathematics: only numerical/calculation-based types
        </div>
      )}

      {/* Question Type */}
      <div className="space-y-1.5">
        <Label>Question Type *</Label>
        <Select value={questionType} onValueChange={(v) => onTypeChange(v as QuestionType)}>
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {types.map((t) => (
              <SelectItem key={t} value={t}>{t}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* Marks + Difficulty row */}
      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-1.5">
          <Label>Marks *</Label>
          <Input
            type="number"
            min={1}
            value={marks}
            onChange={(e) => onMarksChange(parseInt(e.target.value) || 1)}
            className="h-9"
          />
        </div>
        <div className="space-y-1.5">
          <Label>Difficulty *</Label>
          <RadioGroup
            value={difficulty}
            onValueChange={(v) => onDifficultyChange(v as Difficulty)}
            className="flex gap-3 pt-2"
          >
            {(['easy', 'medium', 'hard'] as Difficulty[]).map((d) => (
              <div key={d} className="flex items-center gap-1">
                <RadioGroupItem value={d} id={`diff-${d}`} />
                <Label htmlFor={`diff-${d}`} className="text-xs capitalize">{d}</Label>
              </div>
            ))}
          </RadioGroup>
        </div>
      </div>

      {/* Question Text */}
      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <Label>Question Text *</Label>
          <div className="flex items-center gap-2">
            <span className="text-xs text-[#64748B]">{questionText.length} chars</span>
            <InsertMathPopover
              textareaRef={questionTextareaRef}
              onInsert={(snippet) => insertAtCursor(questionTextareaRef, snippet, questionText, onQuestionTextChange)}
            />
          </div>
        </div>
        <MathSyntaxHint />
        <Textarea
          ref={questionTextareaRef}
          value={questionText}
          onChange={(e) => onQuestionTextChange(e.target.value)}
          onBlur={onQuestionTextBlur}
          placeholder="Enter the question text..."
          className="min-h-[120px]"
        />
      </div>

      {/* Answer Text */}
      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <Label>Answer Text *</Label>
          <InsertMathPopover
            textareaRef={answerTextareaRef}
            onInsert={(snippet) => insertAtCursor(answerTextareaRef, snippet, answerText, onAnswerTextChange)}
          />
        </div>
        <Textarea
          ref={answerTextareaRef}
          value={answerText}
          onChange={(e) => onAnswerTextChange(e.target.value)}
          placeholder="Complete answer or solution..."
          className="min-h-[100px]"
        />
      </div>

      {/* Answer Hint */}
      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <Label className="text-[#64748B]">Answer Hint (optional)</Label>
          <InsertMathPopover
            textareaRef={hintTextareaRef}
            onInsert={(snippet) => insertAtCursor(hintTextareaRef, snippet, answerHint, onAnswerHintChange)}
          />
        </div>
        <Textarea
          ref={hintTextareaRef}
          value={answerHint}
          onChange={(e) => onAnswerHintChange(e.target.value)}
          placeholder="Short key points..."
          className="min-h-[60px]"
        />
      </div>
    </div>
  );
}
