import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { Plus, Trash2 } from 'lucide-react';
import type { QuestionType } from '@/types/database';
import InsertMathPopover from '@/components/shared/InsertMathPopover';
import { useRef } from 'react';

interface Props {
  questionType: QuestionType;
  options: string[];
  correctOption: string;
  blankAnswer: string;
  matchLeft: string[];
  matchRight: string[];
  matchAnswer: Record<string, string>;
  assertion: string;
  reason: string;
  solutionSteps: string[];
  onChange: (field: string, value: unknown) => void;
}

export default function ConditionalFields({
  questionType,
  options,
  correctOption,
  blankAnswer,
  matchLeft,
  matchRight,
  assertion,
  reason,
  solutionSteps,
  onChange,
}: Props) {
  const assertionRef = useRef<HTMLTextAreaElement>(null);
  const reasonRef = useRef<HTMLTextAreaElement>(null);

  const insertAtCursor = (
    ref: React.RefObject<HTMLTextAreaElement | null>,
    snippet: string,
    currentValue: string,
    field: string,
  ) => {
    const el = ref.current;
    if (!el) {
      onChange(field, currentValue + snippet);
      return;
    }
    const start = el.selectionStart ?? currentValue.length;
    const end = el.selectionEnd ?? currentValue.length;
    const newValue = currentValue.slice(0, start) + snippet + currentValue.slice(end);
    onChange(field, newValue);
    requestAnimationFrame(() => {
      el.selectionStart = el.selectionEnd = start + snippet.length;
      el.focus();
    });
  };

  // MCQ
  if (questionType === 'MCQ') {
    return (
      <div className="space-y-3 rounded-lg border border-blue-100 bg-blue-50/50 p-4">
        <p className="text-xs font-semibold text-blue-600">MCQ Options</p>
        {['A', 'B', 'C', 'D'].map((letter, i) => (
          <div key={letter} className="space-y-1">
            <Label className="text-xs">Option {letter} *</Label>
            <Input
              value={options[i] ?? ''}
              onChange={(e) => {
                const newOpts = [...options];
                newOpts[i] = e.target.value;
                onChange('options', newOpts);
              }}
              placeholder={`Option ${letter}`}
              className="h-9"
            />
          </div>
        ))}
        <div className="space-y-1">
          <Label className="text-xs">Correct Option *</Label>
          <RadioGroup
            value={correctOption}
            onValueChange={(val) => onChange('correctOption', val)}
            className="flex gap-4"
          >
            {['A', 'B', 'C', 'D'].map((letter, i) => (
              <div key={letter} className="flex items-center gap-1">
                <RadioGroupItem value={options[i] ?? letter} id={`opt-${letter}`} />
                <Label htmlFor={`opt-${letter}`} className="text-xs">
                  {letter}
                </Label>
              </div>
            ))}
          </RadioGroup>
        </div>
      </div>
    );
  }

  // True/False
  if (questionType === 'True/False') {
    return (
      <div className="space-y-3 rounded-lg border border-blue-100 bg-blue-50/50 p-4">
        <p className="text-xs font-semibold text-blue-600">Correct Answer</p>
        <RadioGroup
          value={correctOption}
          onValueChange={(val) => onChange('correctOption', val)}
          className="flex gap-6"
        >
          <div className="flex items-center gap-2">
            <RadioGroupItem value="True" id="tf-true" />
            <Label htmlFor="tf-true">True</Label>
          </div>
          <div className="flex items-center gap-2">
            <RadioGroupItem value="False" id="tf-false" />
            <Label htmlFor="tf-false">False</Label>
          </div>
        </RadioGroup>
      </div>
    );
  }

  // Fill in the Blanks
  if (questionType === 'Fill in the Blanks') {
    return (
      <div className="space-y-3 rounded-lg border border-blue-100 bg-blue-50/50 p-4">
        <p className="text-xs font-semibold text-blue-600">Fill in the Blanks</p>
        <p className="text-xs text-[#64748B]">
          Use _______ in question text to mark the blank
        </p>
        <div className="space-y-1">
          <Label className="text-xs">Blank Answer *</Label>
          <Input
            value={blankAnswer}
            onChange={(e) => onChange('blankAnswer', e.target.value)}
            placeholder="Correct answer for the blank"
            className="h-9"
          />
        </div>
      </div>
    );
  }

  // Match the Following
  if (questionType === 'Match the Following') {
    const addPair = () => {
      onChange('matchLeft', [...matchLeft, '']);
      onChange('matchRight', [...matchRight, '']);
    };
    const removePair = (idx: number) => {
      onChange('matchLeft', matchLeft.filter((_, i) => i !== idx));
      onChange('matchRight', matchRight.filter((_, i) => i !== idx));
    };

    return (
      <div className="space-y-3 rounded-lg border border-blue-100 bg-blue-50/50 p-4">
        <p className="text-xs font-semibold text-blue-600">Match the Following</p>
        {matchLeft.map((_, i) => (
          <div key={i} className="flex items-center gap-2">
            <Input
              value={matchLeft[i]}
              onChange={(e) => {
                const newLeft = [...matchLeft];
                newLeft[i] = e.target.value;
                onChange('matchLeft', newLeft);
              }}
              placeholder={`Term ${i + 1}`}
              className="h-8 flex-1"
            />
            <span className="text-[#64748B]">↔</span>
            <Input
              value={matchRight[i]}
              onChange={(e) => {
                const newRight = [...matchRight];
                newRight[i] = e.target.value;
                onChange('matchRight', newRight);
              }}
              placeholder={`Definition ${i + 1}`}
              className="h-8 flex-1"
            />
            {matchLeft.length > 2 && (
              <button
                onClick={() => removePair(i)}
                className="text-[#EF4444] hover:text-red-600"
              >
                <Trash2 className="h-3 w-3" />
              </button>
            )}
          </div>
        ))}
        <button
          onClick={addPair}
          className="flex items-center gap-1 text-xs text-[#4F46E5] hover:text-indigo-700"
        >
          <Plus className="h-3 w-3" /> Add pair
        </button>
      </div>
    );
  }

  // Assertion-Reason
  if (questionType === 'Assertion-Reason') {
    return (
      <div className="space-y-3 rounded-lg border border-blue-100 bg-blue-50/50 p-4">
        <p className="text-xs font-semibold text-blue-600">Assertion-Reason</p>
        <div className="space-y-1">
          <div className="flex items-center justify-between">
            <Label className="text-xs">Assertion (A) *</Label>
            <InsertMathPopover
              textareaRef={assertionRef}
              onInsert={(snippet) => insertAtCursor(assertionRef, snippet, assertion, 'assertion')}
            />
          </div>
          <Textarea
            ref={assertionRef}
            value={assertion}
            onChange={(e) => onChange('assertion', e.target.value)}
            placeholder="Enter the assertion statement"
            className="min-h-[60px]"
          />
        </div>
        <div className="space-y-1">
          <div className="flex items-center justify-between">
            <Label className="text-xs">Reason (R) *</Label>
            <InsertMathPopover
              textareaRef={reasonRef}
              onInsert={(snippet) => insertAtCursor(reasonRef, snippet, reason, 'reason')}
            />
          </div>
          <Textarea
            ref={reasonRef}
            value={reason}
            onChange={(e) => onChange('reason', e.target.value)}
            placeholder="Enter the reason statement"
            className="min-h-[60px]"
          />
        </div>
      </div>
    );
  }

  // Numerical / Long Answer
  if (questionType === 'Numerical' || questionType === 'Long Answer') {
    const addStep = () => {
      onChange('solutionSteps', [...solutionSteps, '']);
    };
    const removeStep = (idx: number) => {
      onChange('solutionSteps', solutionSteps.filter((_, i) => i !== idx));
    };

    return (
      <div className="space-y-3 rounded-lg border border-blue-100 bg-blue-50/50 p-4">
        <p className="text-xs font-semibold text-blue-600">Solution Steps</p>
        {solutionSteps.map((step, i) => (
          <div key={i} className="flex items-start gap-2">
            <span className="mt-2 text-xs text-[#64748B]">{i + 1}.</span>
            <Textarea
              value={step}
              onChange={(e) => {
                const newSteps = [...solutionSteps];
                newSteps[i] = e.target.value;
                onChange('solutionSteps', newSteps);
              }}
              placeholder={`Step ${i + 1}`}
              className="min-h-[40px] flex-1"
            />
            {solutionSteps.length > 1 && (
              <button
                onClick={() => removeStep(i)}
                className="mt-2 text-[#EF4444] hover:text-red-600"
              >
                <Trash2 className="h-3 w-3" />
              </button>
            )}
          </div>
        ))}
        <button
          onClick={addStep}
          className="flex items-center gap-1 text-xs text-[#4F46E5] hover:text-indigo-700"
        >
          <Plus className="h-3 w-3" /> Add step
        </button>
      </div>
    );
  }

  return null;
}
