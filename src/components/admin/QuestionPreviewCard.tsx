import { Badge } from '@/components/ui/badge';
import { Pin, ChevronDown, ChevronUp } from 'lucide-react';
import type { QuestionFormData } from '@/types/database';
import { useState } from 'react';
import MathRenderer from '@/components/shared/MathRenderer';

interface Props {
  form: QuestionFormData;
  subjectName: string;
  chapterName: string;
  topicName?: string;
}

export default function QuestionPreviewCard({
  form,
  subjectName,
  chapterName,
  topicName,
}: Props) {
  const [showAnswer, setShowAnswer] = useState(false);

  if (!form.questionText) {
    return (
      <div className="flex h-64 items-center justify-center rounded-xl border-2 border-dashed border-gray-200 bg-gray-50">
        <p className="text-sm text-[#64748B]">Fill the form to see a live preview here</p>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
      {/* Header */}
      <div className="mb-3 flex items-center justify-between">
        <div className="text-xs text-[#64748B]">
          {subjectName} › {chapterName}
        </div>
        <div className="flex gap-2">
          <Badge variant="outline">{form.questionType}</Badge>
          <Badge variant="secondary">{form.marks} Mark{form.marks > 1 ? 's' : ''}</Badge>
        </div>
      </div>

      {/* Difficulty & Source badges */}
      <div className="mb-3 flex gap-2">
        <Badge
          className={
            form.difficulty === 'easy'
              ? 'bg-green-100 text-green-700'
              : form.difficulty === 'medium'
                ? 'bg-yellow-100 text-yellow-700'
                : 'bg-red-100 text-red-700'
          }
        >
          {form.difficulty}
        </Badge>
        {form.source !== 'ai_generated' && (
          <Badge variant="outline">{form.bookName || form.source}</Badge>
        )}
      </div>

      {/* Question text */}
      <div className="mb-3 whitespace-pre-wrap text-sm font-medium text-[#1E293B]">
        <MathRenderer content={form.questionText} />
      </div>

      {/* MCQ options */}
      {form.questionType === 'MCQ' && form.options.some((o) => o) && (
        <div className="mb-3 grid grid-cols-2 gap-1 pl-4">
          {form.options.map((opt, i) => (
            <span key={i} className="text-sm text-[#1E293B]">
              ({String.fromCharCode(65 + i)}) <MathRenderer content={opt} inline />
            </span>
          ))}
        </div>
      )}

      {/* Assertion-Reason */}
      {form.questionType === 'Assertion-Reason' && (
        <div className="mb-3 space-y-1 pl-4 text-sm">
          <p><strong>Assertion (A):</strong> <MathRenderer content={form.assertion} inline /></p>
          <p><strong>Reason (R):</strong> <MathRenderer content={form.reason} inline /></p>
        </div>
      )}

      {/* Match the Following */}
      {form.questionType === 'Match the Following' && form.matchLeft.length > 0 && (
        <div className="mb-3 overflow-hidden rounded-lg border text-sm">
          <table className="w-full">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-3 py-1.5 text-left text-xs text-[#64748B]">Column A</th>
                <th className="px-3 py-1.5 text-left text-xs text-[#64748B]">Column B</th>
              </tr>
            </thead>
            <tbody>
              {form.matchLeft.map((left, i) => (
                <tr key={i} className="border-t">
                  <td className="px-3 py-1.5"><MathRenderer content={left} inline /></td>
                  <td className="px-3 py-1.5"><MathRenderer content={form.matchRight[i] ?? ''} inline /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Topic */}
      {topicName && (
        <div className="mb-3 flex items-center gap-1">
          <Pin className="h-3 w-3 text-[#4F46E5]" />
          <span className="text-xs font-medium text-[#4F46E5]">{topicName}</span>
        </div>
      )}

      {/* Answer collapsible */}
      {form.answerText && (
        <div className="border-t pt-2">
          <button
            onClick={() => setShowAnswer(!showAnswer)}
            className="flex items-center gap-1 text-xs font-medium text-[#10B981] hover:text-emerald-600"
          >
            {showAnswer ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
            {showAnswer ? 'Hide Answer' : 'Show Answer'}
          </button>
          {showAnswer && (
            <div className="mt-2 text-sm text-[#1E293B] whitespace-pre-wrap">
              <MathRenderer content={form.answerText} />
              {form.solutionSteps.length > 0 && (
                <div className="mt-2">
                  <p className="text-xs font-semibold text-[#64748B]">Steps:</p>
                  <ol className="ml-4 list-decimal">
                    {form.solutionSteps.map((step, i) => (
                      <li key={i}><MathRenderer content={step} /></li>
                    ))}
                  </ol>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
