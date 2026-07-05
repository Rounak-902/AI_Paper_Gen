import { useState } from 'react';
import type { PaperQuestionWithDetails } from '@/types/database';
import AnswerKeyPanel from './AnswerKeyPanel';
import { RefreshCw, Pencil, Trash2, Save, X, Loader2 } from 'lucide-react';
import { Textarea } from '@/components/ui/textarea';
import MathRenderer from '@/components/shared/MathRenderer';

interface Props {
  pq: PaperQuestionWithDetails;
  questionNumber: number;
  showAnswerKey: boolean;
  onRegenerate: (pq: PaperQuestionWithDetails) => Promise<void>;
  onEdit: (questionId: string, text: string, answer: string) => Promise<void>;
  onDelete: (paperQuestionId: string) => void;
}

export default function QuestionCard({
  pq,
  questionNumber,
  showAnswerKey,
  onRegenerate,
  onEdit,
  onDelete,
}: Props) {
  const [editing, setEditing] = useState(false);
  const [editText, setEditText] = useState(pq.question.question_text);
  const [editAnswer, setEditAnswer] = useState(pq.question.answer_text ?? '');
  const [regenerating, setRegenerating] = useState(false);

  const q = pq.question;

  const handleRegenerate = async () => {
    setRegenerating(true);
    await onRegenerate(pq);
    setRegenerating(false);
  };

  const handleSave = async () => {
    await onEdit(q.question_id, editText, editAnswer);
    setEditing(false);
  };

  return (
    <div className="group relative mb-4 rounded-lg border border-transparent px-4 py-3 transition-all hover:border-gray-200 hover:bg-gray-50/50">
      {/* Hover actions */}
      <div
        className="absolute -right-2 -top-2 hidden gap-1 group-hover:flex"
        data-no-print
      >
        <button
          onClick={handleRegenerate}
          disabled={regenerating}
          className="flex h-8 w-8 items-center justify-center rounded-lg bg-white text-[#4F46E5] shadow-md hover:bg-indigo-50"
          title="Regenerate"
        >
          {regenerating ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <RefreshCw className="h-3.5 w-3.5" />
          )}
        </button>
        <button
          onClick={() => setEditing(true)}
          className="flex h-8 w-8 items-center justify-center rounded-lg bg-white text-[#F59E0B] shadow-md hover:bg-amber-50"
          title="Edit"
        >
          <Pencil className="h-3.5 w-3.5" />
        </button>
        <button
          onClick={() => onDelete(pq.id)}
          className="flex h-8 w-8 items-center justify-center rounded-lg bg-white text-[#EF4444] shadow-md hover:bg-red-50"
          title="Delete"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </div>

      {editing ? (
        <div className="space-y-3">
          <Textarea
            value={editText}
            onChange={(e) => setEditText(e.target.value)}
            className="min-h-[80px]"
          />
          <Textarea
            value={editAnswer}
            onChange={(e) => setEditAnswer(e.target.value)}
            placeholder="Answer text"
            className="min-h-[60px]"
          />
          <div className="flex gap-2">
            <button
              onClick={handleSave}
              className="flex items-center gap-1 rounded-lg bg-[#10B981] px-3 py-1.5 text-xs text-white hover:bg-emerald-600"
            >
              <Save className="h-3 w-3" />
              Save
            </button>
            <button
              onClick={() => setEditing(false)}
              className="flex items-center gap-1 rounded-lg border px-3 py-1.5 text-xs text-[#64748B] hover:bg-gray-50"
            >
              <X className="h-3 w-3" />
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <>
          {/* Question text */}
          <div className="flex items-start justify-between gap-4">
            <div className="flex-1">
              <span className="font-semibold text-[#1E293B]">
                Q.{questionNumber}.{' '}
              </span>
              <span className="text-[#1E293B] whitespace-pre-wrap">
                <MathRenderer content={q.question_text} />
              </span>
            </div>
            <span className="shrink-0 text-sm font-medium text-[#64748B]">
              [{q.marks}]
            </span>
          </div>

          {/* MCQ Options */}
          {q.question_type === 'MCQ' && q.options && (
            <div className="mt-2 grid grid-cols-2 gap-x-8 gap-y-1 pl-8">
              {q.options.map((opt, i) => (
                <span key={i} className="text-sm text-[#1E293B]">
                  ({String.fromCharCode(65 + i)}) <MathRenderer content={opt} inline />
                </span>
              ))}
            </div>
          )}

          {/* True/False */}
          {q.question_type === 'True/False' && q.options && (
            <div className="mt-2 flex gap-6 pl-8">
              {q.options.map((opt, i) => (
                <span key={i} className="text-sm text-[#1E293B]">
                  ({String.fromCharCode(65 + i)}) {opt}
                </span>
              ))}
            </div>
          )}

          {/* Match the Following */}
          {q.question_type === 'Match the Following' &&
            q.match_left &&
            q.match_right && (
              <div className="mt-3 overflow-hidden rounded-lg border pl-8">
                <table className="w-full text-sm">
                  <thead className="bg-gray-50">
                    <tr>
                      <th className="px-4 py-2 text-left font-medium text-[#64748B]">
                        Column A
                      </th>
                      <th className="px-4 py-2 text-left font-medium text-[#64748B]">
                        Column B
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {q.match_left.map((left, i) => (
                      <tr key={i} className="border-t">
                        <td className="px-4 py-2 text-[#1E293B]">
                          {i + 1}. <MathRenderer content={left} inline />
                        </td>
                        <td className="px-4 py-2 text-[#1E293B]">
                          {String.fromCharCode(65 + i)}.{' '}
                          <MathRenderer content={q.match_right?.[i] ?? ''} inline />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

          {/* Answer key */}
          <AnswerKeyPanel
            answerText={q.answer_text}
            solutionSteps={q.solution_steps}
            correctOption={q.correct_option}
            blankAnswer={q.blank_answer}
            matchAnswer={q.match_answer}
            visible={showAnswerKey}
          />
        </>
      )}
    </div>
  );
}
