import { useState, useCallback } from 'react';
import { supabase } from '@/lib/supabaseClient';
import type { QuestionBankItem } from '@/types/database';

interface Filters {
  board_id?: string;
  class_id?: string;
  subject_id?: string;
  chapter_id?: string;
  question_type?: string;
  marks?: number;
  difficulty?: string;
  source?: string;
  search?: string;
}

interface UseQuestionBankResult {
  questions: QuestionBankItem[];
  total: number;
  loading: boolean;
  page: number;
  setPage: (p: number) => void;
  fetchQuestions: (filters: Filters, page?: number) => Promise<void>;
}

const PAGE_SIZE = 20;

export function useQuestionBank(): UseQuestionBankResult {
  const [questions, setQuestions] = useState<QuestionBankItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);

  const fetchQuestions = useCallback(async (filters: Filters, pg: number = 1) => {
    setLoading(true);
    setPage(pg);

    let query = supabase
      .from('question_bank')
      .select('*', { count: 'exact' })
      .eq('is_active', true)
      .order('created_at', { ascending: false })
      .range((pg - 1) * PAGE_SIZE, pg * PAGE_SIZE - 1);

    if (filters.board_id) query = query.eq('board_id', filters.board_id);
    if (filters.class_id) query = query.eq('class_id', filters.class_id);
    if (filters.subject_id) query = query.eq('subject_id', filters.subject_id);
    if (filters.chapter_id) query = query.eq('chapter_id', filters.chapter_id);
    if (filters.question_type) query = query.eq('question_type', filters.question_type);
    if (filters.marks) query = query.eq('marks', filters.marks);
    if (filters.difficulty) query = query.eq('difficulty', filters.difficulty);
    if (filters.source) query = query.eq('source', filters.source);
    if (filters.search) query = query.ilike('question_text', `%${filters.search}%`);

    const { data, count } = await query;
    setQuestions((data as QuestionBankItem[]) ?? []);
    setTotal(count ?? 0);
    setLoading(false);
  }, []);

  return { questions, total, loading, page, setPage, fetchQuestions };
}
