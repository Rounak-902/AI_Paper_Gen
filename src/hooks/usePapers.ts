import { useState, useCallback } from 'react';
import { supabase } from '@/lib/supabaseClient';
import { callGemini, parseGeminiJSON } from '@/lib/geminiClient';
import { buildSingleQuestionPrompt } from '@/lib/geminiPromptBuilder';
import type {
  GeneratedPaper,
  PaperQuestionWithDetails,
  PaperSection,
  GeminiQuestionResponse,
} from '@/types/database';

export function usePapers() {
  const [papers, setPapers] = useState<GeneratedPaper[]>([]);
  const [loading, setLoading] = useState(false);

  const fetchPapers = useCallback(async () => {
    setLoading(true);
    const { data } = await supabase
      .from('generated_papers')
      .select('*')
      .eq('is_deleted', false)
      .order('created_at', { ascending: false });
    setPapers((data as GeneratedPaper[]) ?? []);
    setLoading(false);
  }, []);

  const fetchPaperDetails = useCallback(
    async (
      paperId: string,
    ): Promise<{
      paper: GeneratedPaper;
      sections: PaperSection[];
    } | null> => {
      const { data: paper } = await supabase
        .from('generated_papers')
        .select('*')
        .eq('paper_id', paperId)
        .single();

      if (!paper) return null;

      const { data: pqData } = await supabase
        .from('paper_questions')
        .select('*, question:question_bank(*)')
        .eq('paper_id', paperId)
        .eq('is_replaced', false)
        .order('order_index');

      const paperQuestions = (pqData ?? []) as PaperQuestionWithDetails[];

      // Group by section
      const sectionMap = new Map<string, PaperQuestionWithDetails[]>();
      paperQuestions.forEach((pq) => {
        const name = pq.section_name ?? 'Section';
        if (!sectionMap.has(name)) sectionMap.set(name, []);
        sectionMap.get(name)!.push(pq);
      });

      // Get section config from paper
      const config = (paper as GeneratedPaper).paper_config ?? [];
      const sections: PaperSection[] = [];

      // Build sections in order of paper_config
      for (const cfg of config) {
        const sName = (cfg as { sectionName: string }).sectionName;
        const qs = sectionMap.get(sName) ?? [];
        sections.push({
          sectionName: sName,
          questionType: (cfg as { questionType: string }).questionType as PaperSection['questionType'],
          marksPerQuestion: (cfg as { marksPerQuestion: number }).marksPerQuestion,
          questions: qs,
        });
        sectionMap.delete(sName);
      }

      // Any remaining sections not in config
      sectionMap.forEach((qs, name) => {
        if (qs.length > 0) {
          sections.push({
            sectionName: name,
            questionType: qs[0].question.question_type,
            marksPerQuestion: qs[0].question.marks,
            questions: qs,
          });
        }
      });

      return { paper: paper as GeneratedPaper, sections };
    },
    [],
  );

  const renamePaper = useCallback(async (paperId: string, title: string) => {
    await supabase
      .from('generated_papers')
      .update({ paper_title: title })
      .eq('paper_id', paperId);
  }, []);

  const deletePaper = useCallback(async (paperId: string) => {
    await supabase
      .from('generated_papers')
      .update({ is_deleted: true })
      .eq('paper_id', paperId);
  }, []);

  const markFinal = useCallback(async (paperId: string) => {
    await supabase
      .from('generated_papers')
      .update({ status: 'final' })
      .eq('paper_id', paperId);
  }, []);

  const regenerateQuestion = useCallback(
    async (
      paperQuestionId: string,
      questionId: string,
      questionType: string,
      marks: number,
      subjectName: string,
      chapterName: string,
      className: string,
      boardName: string,
      currentText: string,
      userId: string,
      paperId: string,
      sectionName: string,
      orderIndex: number,
    ): Promise<boolean> => {
      try {
        const prompt = buildSingleQuestionPrompt(
          questionType,
          marks,
          subjectName,
          chapterName,
          className,
          boardName,
          currentText,
        );

        const raw = await callGemini(prompt);
        const newQ = parseGeminiJSON<GeminiQuestionResponse>(raw);

        // Insert new question into bank
        const { data: qData } = await supabase
          .from('question_bank')
          .insert({
            question_text: newQ.question_text,
            answer_text: newQ.answer_text,
            answer_hint: newQ.answer_hint,
            question_type: newQ.question_type || questionType,
            marks: newQ.marks || marks,
            difficulty: newQ.difficulty,
            options: newQ.options,
            correct_option: newQ.correct_option,
            blank_answer: newQ.blank_answer,
            match_left: newQ.match_left,
            match_right: newQ.match_right,
            match_answer: newQ.match_answer,
            assertion: newQ.assertion,
            reason: newQ.reason,
            solution_steps: newQ.solution_steps,
            source: 'ai_generated',
            times_used: 1,
            created_by: userId,
          })
          .select('question_id')
          .single();

        if (!qData) return false;

        // Mark old paper_question as replaced
        await supabase
          .from('paper_questions')
          .update({ is_replaced: true, replaced_at: new Date().toISOString() })
          .eq('id', paperQuestionId);

        // Insert new paper_question
        await supabase.from('paper_questions').insert({
          paper_id: paperId,
          question_id: qData.question_id,
          section_name: sectionName,
          order_index: orderIndex,
        });

        return true;
      } catch {
        return false;
      }
    },
    [],
  );

  const updateQuestion = useCallback(
    async (questionId: string, updates: { question_text: string; answer_text: string }) => {
      await supabase
        .from('question_bank')
        .update(updates)
        .eq('question_id', questionId);
    },
    [],
  );

  const removeQuestionFromPaper = useCallback(async (paperQuestionId: string) => {
    await supabase.from('paper_questions').delete().eq('id', paperQuestionId);
  }, []);

  return {
    papers,
    loading,
    fetchPapers,
    fetchPaperDetails,
    renamePaper,
    deletePaper,
    markFinal,
    regenerateQuestion,
    updateQuestion,
    removeQuestionFromPaper,
  };
}
