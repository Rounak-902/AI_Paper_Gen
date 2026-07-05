import { useState, useCallback } from 'react';
import { supabase } from '@/lib/supabaseClient';
import { callGemini, parseGeminiJSON } from '@/lib/geminiClient';
import { buildFullPaperPrompt } from '@/lib/geminiPromptBuilder';
import type {
  WizardState,
  SectionProgress,
  GeminiQuestionResponse,
} from '@/types/database';

interface GenerateResult {
  paperId: string | null;
  sectionProgress: SectionProgress[];
  isGenerating: boolean;
  error: string | null;
  generatePaper: (state: WizardState, userId: string) => Promise<string | null>;
}

export function useGeminiGenerate(): GenerateResult {
  const [paperId, setPaperId] = useState<string | null>(null);
  const [sectionProgress, setSectionProgress] = useState<SectionProgress[]>([]);
  const [isGenerating, setIsGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const updateProgress = (index: number, update: Partial<SectionProgress>) => {
    setSectionProgress((prev) =>
      prev.map((p, i) => (i === index ? { ...p, ...update } : p)),
    );
  };

  const setAllProgress = (status: SectionProgress['status'], sections: WizardState['sections']) => {
    setSectionProgress(
      sections.map((s) => ({ sectionName: s.sectionName, status, questionCount: 0 })),
    );
  };

  const generatePaper = useCallback(
    async (state: WizardState, userId: string): Promise<string | null> => {
      setIsGenerating(true);
      setError(null);

      // Init progress — all waiting
      setAllProgress('waiting', state.sections);

      try {
        // 1. Insert paper metadata
        const { data: paperData, error: paperError } = await supabase
          .from('generated_papers')
          .insert({
            created_by: userId,
            board_id: state.boardId,
            class_id: state.classId,
            stream_id: state.streamId,
            subject_id: state.subjectId,
            paper_title: state.paperTitle,
            total_marks: state.totalMarks,
            duration: state.duration,
            difficulty: state.difficulty,
            paper_config: state.sections,
            selected_chapters: state.selectedChapterIds,
            status: 'draft',
          })
          .select('paper_id')
          .single();

        if (paperError || !paperData) {
          throw new Error(paperError?.message ?? 'Failed to create paper');
        }

        const newPaperId = paperData.paper_id;
        setPaperId(newPaperId);

        const isMaths = (state.subjectName ?? '').toLowerCase().includes('math');

        // 2. Fetch existing questions to avoid repeats
        let existingQuestions: string[] = [];
        const { data: existing } = await supabase
          .from('question_bank')
          .select('question_text')
          .in('chapter_id', state.selectedChapterIds)
          .order('created_at', { ascending: false })
          .limit(10);

        if (existing) {
          existingQuestions = existing.map((q: { question_text: string }) => q.question_text);
        }

        // 3. Build ONE prompt for ALL sections
        const prompt = buildFullPaperPrompt({
          boardName: state.boardName ?? '',
          className: state.className ?? '',
          streamName: state.streamName,
          subjectName: state.subjectName ?? '',
          chapterNames: state.selectedChapterNames,
          difficulty: state.difficulty,
          sections: state.sections,
          existingQuestions,
          isMaths,
        });

        // Mark all sections as generating
        state.sections.forEach((_, i) => updateProgress(i, { status: 'generating' }));

        // 4. ONE Gemini API call for the entire paper
        let rawResponse = await callGemini(prompt);
        let paperResult: Record<string, GeminiQuestionResponse[]>;

        try {
          paperResult = parseGeminiJSON<Record<string, GeminiQuestionResponse[]>>(rawResponse);
        } catch {
          // Retry once
          const retryPrompt = `Your previous response was not valid JSON. Return ONLY the raw JSON object with section names as keys, nothing else.\n\n${prompt}`;
          rawResponse = await callGemini(retryPrompt);
          try {
            paperResult = parseGeminiJSON<Record<string, GeminiQuestionResponse[]>>(rawResponse);
          } catch {
            throw new Error('Failed to parse AI response after retry');
          }
        }

        // 5. Process each section from the combined response
        for (let i = 0; i < state.sections.length; i++) {
          const section = state.sections[i];

          try {
            // Find questions for this section — try exact key match, then fuzzy
            let questions = paperResult[section.sectionName];
            if (!questions || !Array.isArray(questions)) {
              // Try case-insensitive key match
              const matchedKey = Object.keys(paperResult).find(
                (k) => k.toLowerCase() === section.sectionName.toLowerCase(),
              );
              if (matchedKey) {
                questions = paperResult[matchedKey];
              }
            }

            if (!questions || !Array.isArray(questions) || questions.length === 0) {
              updateProgress(i, {
                status: 'error',
                error: `No questions generated for this section`,
              });
              continue;
            }

            // Insert each question into question_bank and paper_questions
            for (let j = 0; j < questions.length; j++) {
              const q = questions[j];

              // Find matching chapter_id from chapter_name
              let matchedChapterId = state.selectedChapterIds[0]; // fallback
              if (q.chapter_name) {
                const matchIndex = state.selectedChapterNames.findIndex(
                  (name) => name.toLowerCase() === q.chapter_name.toLowerCase(),
                );
                if (matchIndex >= 0) {
                  matchedChapterId = state.selectedChapterIds[matchIndex];
                }
              }

              const { data: qData, error: qError } = await supabase
                .from('question_bank')
                .insert({
                  board_id: state.boardId,
                  class_id: state.classId,
                  stream_id: state.streamId,
                  subject_id: state.subjectId,
                  chapter_id: matchedChapterId,
                  question_text: q.question_text,
                  answer_text: q.answer_text,
                  answer_hint: q.answer_hint,
                  question_type: q.question_type || section.questionType,
                  marks: q.marks || section.marksPerQuestion,
                  difficulty: q.difficulty,
                  options: q.options,
                  correct_option: q.correct_option,
                  blank_answer: q.blank_answer,
                  match_left: q.match_left,
                  match_right: q.match_right,
                  match_answer: q.match_answer,
                  assertion: q.assertion,
                  reason: q.reason,
                  solution_steps: q.solution_steps,
                  source: 'ai_generated',
                  times_used: 1,
                  created_by: userId,
                })
                .select('question_id')
                .single();

              if (qError || !qData) continue;

              await supabase.from('paper_questions').insert({
                paper_id: newPaperId,
                question_id: qData.question_id,
                section_name: section.sectionName,
                order_index: j + 1,
              });
            }

            updateProgress(i, {
              status: 'done',
              questionCount: questions.length,
            });
          } catch (sectionError: unknown) {
            const errMsg = sectionError instanceof Error ? sectionError.message : 'Unknown error';
            updateProgress(i, {
              status: 'error',
              error: errMsg,
            });
          }
        }

        setIsGenerating(false);
        return newPaperId;
      } catch (err: unknown) {
        const errMsg = err instanceof Error ? err.message : 'Generation failed';
        setError(errMsg);
        setIsGenerating(false);
        return null;
      }
    },
    [],
  );

  return {
    paperId,
    sectionProgress,
    isGenerating,
    error,
    generatePaper,
  };
}
