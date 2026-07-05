import { Fragment, useEffect, useState } from 'react';
import { useQuestionBank } from '@/hooks/useQuestionBank';
import { useCascadingDropdowns } from '@/hooks/useCascadingDropdowns';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import LoadingSkeleton from '@/components/shared/LoadingSkeleton';
import AddQuestionModal from '@/components/admin/AddQuestionModal';
import MathRenderer from '@/components/shared/MathRenderer';
import { Search, ChevronLeft, ChevronRight, Library, ChevronDown, ChevronUp, X, Plus } from 'lucide-react';
import type { QuestionType, Difficulty, Source } from '@/types/database';

/** Truncate text to ~maxLen chars without cutting a $...$ expression in half */
function smartTruncate(text: string, maxLen: number): string {
  if (text.length <= maxLen) return text;
  const truncated = text.slice(0, maxLen);
  const dollarCount = (truncated.match(/\$/g) || []).length;
  if (dollarCount % 2 !== 0) {
    const lastDollar = truncated.lastIndexOf('$');
    if (lastDollar > 0) {
      return truncated.slice(0, lastDollar) + '...';
    }
  }
  return truncated + '...';
}

const ALL_TYPES: QuestionType[] = [
  'MCQ', 'Short Answer', 'Long Answer', 'Very Short Answer', 'Numerical',
  'Fill in the Blanks', 'True/False', 'Assertion-Reason', 'Case Study',
  'Match the Following', 'One Word Answer', 'Diagram Based',
];

const EMPTY_FILTERS = {
  board_id: '',
  class_id: '',
  subject_id: '',
  chapter_id: '',
  question_type: '',
  marks: 0,
  difficulty: '',
  source: '',
  search: '',
};

export default function QuestionBankPage() {
  const { questions, total, loading, page, setPage, fetchQuestions } = useQuestionBank();
  const { boards, classes, subjects, chapters, fetchClasses, fetchSubjects, fetchChapters } =
    useCascadingDropdowns();

  const [filters, setFilters] = useState({ ...EMPTY_FILTERS });
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [showAddModal, setShowAddModal] = useState(false);

  useEffect(() => {
    fetchQuestions(filters, 1);
  }, []);

  useEffect(() => {
    if (filters.board_id) fetchClasses(filters.board_id);
  }, [filters.board_id]);

  useEffect(() => {
    if (filters.class_id) fetchSubjects(filters.class_id);
  }, [filters.class_id]);

  useEffect(() => {
    if (filters.subject_id) fetchChapters(filters.subject_id);
  }, [filters.subject_id]);

  const applyFilters = () => {
    fetchQuestions(filters, 1);
  };

  const clearFilters = () => {
    setFilters({ ...EMPTY_FILTERS });
    fetchQuestions(EMPTY_FILTERS, 1);
  };

  const hasFilters = filters.board_id || filters.class_id || filters.question_type ||
    filters.difficulty || filters.source || filters.search;

  const totalPages = Math.ceil(total / 20);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-[#1E293B]">Question Bank</h1>
          <p className="mt-1 flex items-center gap-2 text-sm text-[#64748B]">
            <Library className="h-4 w-4" />
            {total.toLocaleString()} questions in bank
          </p>
        </div>
        <Button
          onClick={() => setShowAddModal(true)}
          className="gap-2 bg-[#4F46E5] text-white hover:bg-indigo-700"
        >
          <Plus className="h-4 w-4" />
          Add Question
        </Button>
      </div>

      <AddQuestionModal
        open={showAddModal}
        onClose={() => setShowAddModal(false)}
        onAdded={() => fetchQuestions(filters, page)}
      />

      {/* Filters */}
      <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm space-y-3">
        {/* Search bar - full width */}
        <div className="relative">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[#64748B]" />
          <Input
            value={filters.search}
            onChange={(e) => setFilters({ ...filters, search: e.target.value })}
            onKeyDown={(e) => e.key === 'Enter' && applyFilters()}
            placeholder="Search questions by text..."
            className="pl-10"
          />
        </div>

        {/* Filter dropdowns - 2 rows */}
        <div className="grid gap-3 grid-cols-2 md:grid-cols-4">
          {/* Board */}
          <Select
            value={filters.board_id || undefined}
            onValueChange={(v) => v && setFilters({ ...filters, board_id: v, class_id: '', subject_id: '', chapter_id: '' })}
          >
            <SelectTrigger>
              <SelectValue placeholder="All Boards" />
            </SelectTrigger>
            <SelectContent>
              {boards.map((b) => (
                <SelectItem key={b.board_id} value={b.board_id}>{b.board_name}</SelectItem>
              ))}
            </SelectContent>
          </Select>

          {/* Class */}
          <Select
            value={filters.class_id || undefined}
            onValueChange={(v) => v && setFilters({ ...filters, class_id: v, subject_id: '', chapter_id: '' })}
            disabled={!filters.board_id}
          >
            <SelectTrigger>
              <SelectValue placeholder="All Classes" />
            </SelectTrigger>
            <SelectContent>
              {classes.map((c) => (
                <SelectItem key={c.class_id} value={c.class_id}>Class {c.class_name}</SelectItem>
              ))}
            </SelectContent>
          </Select>

          {/* Subject */}
          <Select
            value={filters.subject_id || undefined}
            onValueChange={(v) => v && setFilters({ ...filters, subject_id: v, chapter_id: '' })}
            disabled={!filters.class_id}
          >
            <SelectTrigger>
              <SelectValue placeholder="All Subjects" />
            </SelectTrigger>
            <SelectContent>
              {subjects.map((s) => (
                <SelectItem key={s.subject_id} value={s.subject_id}>{s.subject_name}</SelectItem>
              ))}
            </SelectContent>
          </Select>

          {/* Chapter */}
          <Select
            value={filters.chapter_id || undefined}
            onValueChange={(v) => v && setFilters({ ...filters, chapter_id: v })}
            disabled={!filters.subject_id}
          >
            <SelectTrigger>
              <SelectValue placeholder="All Chapters" />
            </SelectTrigger>
            <SelectContent>
              {chapters.map((c) => (
                <SelectItem key={c.chapter_id} value={c.chapter_id}>{c.chapter_name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {/* Second row of filters + actions */}
        <div className="flex flex-wrap items-center gap-3">
          {/* Type */}
          <Select
            value={filters.question_type || undefined}
            onValueChange={(v) => v && setFilters({ ...filters, question_type: v })}
          >
            <SelectTrigger className="w-[160px]">
              <SelectValue placeholder="All Types" />
            </SelectTrigger>
            <SelectContent>
              {ALL_TYPES.map((t) => (
                <SelectItem key={t} value={t}>{t}</SelectItem>
              ))}
            </SelectContent>
          </Select>

          {/* Difficulty */}
          <Select
            value={filters.difficulty || undefined}
            onValueChange={(v) => v && setFilters({ ...filters, difficulty: v })}
          >
            <SelectTrigger className="w-[140px]">
              <SelectValue placeholder="All Levels" />
            </SelectTrigger>
            <SelectContent>
              {(['easy', 'medium', 'hard'] as Difficulty[]).map((d) => (
                <SelectItem key={d} value={d} className="capitalize">{d}</SelectItem>
              ))}
            </SelectContent>
          </Select>

          {/* Source */}
          <Select
            value={filters.source || undefined}
            onValueChange={(v) => v && setFilters({ ...filters, source: v })}
          >
            <SelectTrigger className="w-[150px]">
              <SelectValue placeholder="All Sources" />
            </SelectTrigger>
            <SelectContent>
              {(['ai_generated', 'textbook', 'manual', 'other'] as Source[]).map((s) => (
                <SelectItem key={s} value={s} className="capitalize">{s.replace('_', ' ')}</SelectItem>
              ))}
            </SelectContent>
          </Select>

          <div className="flex-1" />

          {hasFilters && (
            <Button variant="ghost" size="sm" onClick={clearFilters} className="text-[#64748B]">
              <X className="mr-1 h-3 w-3" /> Clear
            </Button>
          )}

          <Button onClick={applyFilters} className="bg-[#4F46E5] text-white hover:bg-indigo-700">
            Apply Filters
          </Button>
        </div>
      </div>

      {/* Table */}
      {loading ? (
        <LoadingSkeleton variant="row" count={5} />
      ) : (
        <div className="rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b">
              <tr>
                <th className="px-4 py-3 text-left font-medium text-[#64748B]">Question</th>
                <th className="px-4 py-3 text-left font-medium text-[#64748B]">Type</th>
                <th className="px-4 py-3 text-left font-medium text-[#64748B]">Marks</th>
                <th className="px-4 py-3 text-left font-medium text-[#64748B]">Difficulty</th>
                <th className="px-4 py-3 text-left font-medium text-[#64748B]">Source</th>
                <th className="px-4 py-3 text-left font-medium text-[#64748B]">Used</th>
                <th className="px-4 py-3 text-left font-medium text-[#64748B]"></th>
              </tr>
            </thead>
            <tbody>
              {questions.map((q) => (
                <Fragment key={q.question_id}>
                  <tr
                    className="border-b cursor-pointer hover:bg-gray-50"
                    onClick={() =>
                      setExpandedId(expandedId === q.question_id ? null : q.question_id)
                    }
                  >
                    <td className="max-w-[300px] truncate px-4 py-3 text-[#1E293B]">
                      <MathRenderer content={smartTruncate(q.question_text, 80)} inline />
                    </td>
                    <td className="px-4 py-3">
                      <Badge variant="outline" className="text-xs">{q.question_type}</Badge>
                    </td>
                    <td className="px-4 py-3 text-[#F59E0B] font-medium">{q.marks}</td>
                    <td className="px-4 py-3">
                      <Badge
                        className={`text-xs ${
                          q.difficulty === 'easy' ? 'bg-green-100 text-green-700' :
                          q.difficulty === 'medium' ? 'bg-yellow-100 text-yellow-700' :
                          'bg-red-100 text-red-700'
                        }`}
                      >
                        {q.difficulty}
                      </Badge>
                    </td>
                    <td className="px-4 py-3 text-xs text-[#64748B] capitalize">
                      {q.source.replace('_', ' ')}
                    </td>
                    <td className="px-4 py-3 text-[#64748B]">{q.times_used}</td>
                    <td className="px-4 py-3">
                      {expandedId === q.question_id ? (
                        <ChevronUp className="h-4 w-4 text-[#64748B]" />
                      ) : (
                        <ChevronDown className="h-4 w-4 text-[#64748B]" />
                      )}
                    </td>
                  </tr>
                  {expandedId === q.question_id && (
                    <tr key={`${q.question_id}-expanded`}>
                      <td colSpan={7} className="bg-gray-50 px-6 py-4">
                        <div className="space-y-2">
                          <div className="whitespace-pre-wrap text-sm text-[#1E293B]">
                            <MathRenderer content={q.question_text} />
                          </div>
                          {q.options && (
                            <div className="flex gap-4">
                              {(q.options as string[]).map((opt, i) => (
                                <span key={i} className="text-sm">
                                  ({String.fromCharCode(65 + i)}) <MathRenderer content={opt} inline />
                                </span>
                              ))}
                            </div>
                          )}
                          {q.answer_text && (
                            <div className="rounded-lg bg-emerald-50 p-3">
                              <p className="text-xs font-semibold text-[#10B981]">Answer:</p>
                              <div className="text-sm text-[#1E293B] whitespace-pre-wrap">
                                <MathRenderer content={q.answer_text} />
                              </div>
                            </div>
                          )}
                          {q.solution_steps && (
                            <div className="text-sm">
                              <p className="font-semibold text-[#64748B]">Solution Steps:</p>
                              <ol className="ml-4 list-decimal">
                                {(q.solution_steps as string[]).map((step, i) => (
                                  <li key={i}><MathRenderer content={step} /></li>
                                ))}
                              </ol>
                            </div>
                          )}
                          <div className="flex gap-2 text-xs text-[#64748B]">
                            {q.book_name && <span>📚 {q.book_name}</span>}
                            {q.exercise_no && <span>• {q.exercise_no}</span>}
                            {q.question_no && <span>• {q.question_no}</span>}
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>

          {questions.length === 0 && (
            <div className="p-12 text-center text-[#64748B]">
              No questions found matching your filters
            </div>
          )}
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between">
          <span className="text-sm text-[#64748B]">
            Page {page} of {totalPages}
          </span>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={page <= 1}
              onClick={() => {
                setPage(page - 1);
                fetchQuestions(filters, page - 1);
              }}
            >
              <ChevronLeft className="h-4 w-4" />
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={page >= totalPages}
              onClick={() => {
                setPage(page + 1);
                fetchQuestions(filters, page + 1);
              }}
            >
              <ChevronRight className="h-4 w-4" />
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
