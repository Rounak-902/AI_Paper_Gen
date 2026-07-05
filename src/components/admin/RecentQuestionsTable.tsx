import { useEffect, useState, useCallback } from 'react';
import { supabase } from '@/lib/supabaseClient';
import { useAuth } from '@/context/AuthContext';
import type { QuestionBankItem } from '@/types/database';
import { Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import MathRenderer from '@/components/shared/MathRenderer';

/** Truncate text to ~maxLen chars without cutting a $...$ expression in half */
function smartTruncate(text: string, maxLen: number): string {
  if (text.length <= maxLen) return text;
  const truncated = text.slice(0, maxLen);
  // Count unmatched $ signs — if odd, we cut inside a math expression
  const dollarCount = (truncated.match(/\$/g) || []).length;
  if (dollarCount % 2 !== 0) {
    // Find the last $ and cut before it
    const lastDollar = truncated.lastIndexOf('$');
    if (lastDollar > 0) {
      return truncated.slice(0, lastDollar) + '...';
    }
  }
  return truncated + '...';
}

export default function RecentQuestionsTable({
  onLoadQuestion,
}: {
  onLoadQuestion: (q: QuestionBankItem) => void;
}) {
  const { user } = useAuth();
  const [questions, setQuestions] = useState<QuestionBankItem[]>([]);

  const loadRecent = useCallback(async () => {
    if (!user) return;

    const today = new Date();
    today.setHours(0, 0, 0, 0);

    const { data } = await supabase
      .from('question_bank')
      .select('*')
      .eq('created_by', user.id)
      .eq('is_active', true)
      .gte('created_at', today.toISOString())
      .order('created_at', { ascending: false })
      .limit(10);

    setQuestions((data as QuestionBankItem[]) ?? []);
  }, [user]);

  useEffect(() => {
    loadRecent();
  }, [loadRecent]);

  const handleDelete = async (id: string) => {
    await supabase
      .from('question_bank')
      .update({ is_active: false })
      .eq('question_id', id);
    toast.success('Question deactivated');
    loadRecent();
  };

  return (
    <div className="mt-6">
      <h4 className="mb-3 text-sm font-semibold text-[#1E293B]">
        Added Today ({questions.length})
      </h4>

      {questions.length === 0 ? (
        <p className="text-xs text-[#64748B]">No questions added today yet</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border">
          <table className="w-full text-xs">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-3 py-2 text-left font-medium text-[#64748B]">#</th>
                <th className="px-3 py-2 text-left font-medium text-[#64748B]">Question</th>
                <th className="px-3 py-2 text-left font-medium text-[#64748B]">Type</th>
                <th className="px-3 py-2 text-left font-medium text-[#64748B]">Marks</th>
                <th className="px-3 py-2 text-left font-medium text-[#64748B]"></th>
              </tr>
            </thead>
            <tbody>
              {questions.map((q, i) => (
                <tr
                  key={q.question_id}
                  className="cursor-pointer border-t hover:bg-gray-50"
                  onClick={() => onLoadQuestion(q)}
                >
                  <td className="px-3 py-2 text-[#64748B]">{i + 1}</td>
                  <td className="max-w-[200px] truncate px-3 py-2 text-[#1E293B]">
                    <MathRenderer content={smartTruncate(q.question_text, 60)} inline />
                  </td>
                  <td className="px-3 py-2 text-[#64748B]">{q.question_type}</td>
                  <td className="px-3 py-2 text-[#64748B]">{q.marks}</td>
                  <td className="px-3 py-2">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDelete(q.question_id);
                      }}
                      className="text-[#EF4444] hover:text-red-600"
                    >
                      <Trash2 className="h-3 w-3" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
