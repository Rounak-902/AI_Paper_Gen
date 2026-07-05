import { useState, useCallback, useRef } from 'react';
import { callGemini } from '@/lib/geminiClient';
import { buildTopicSuggestionPrompt } from '@/lib/geminiPromptBuilder';
import { supabase } from '@/lib/supabaseClient';

interface TopicSuggestion {
  suggestedTopic: string | null;
  isLoading: boolean;
  error: string | null;
  suggest: (
    subjectName: string,
    chapterName: string,
    chapterId: string,
    questionText: string,
  ) => void;
  acceptTopic: (chapterId: string) => Promise<string | null>;
  rejectTopic: () => void;
}

export function useTopicSuggestion(): TopicSuggestion {
  const [suggestedTopic, setSuggestedTopic] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const contextRef = useRef<{ chapterId: string; questionText: string }>({
    chapterId: '',
    questionText: '',
  });

  const suggest = useCallback(
    (
      subjectName: string,
      chapterName: string,
      chapterId: string,
      questionText: string,
    ) => {
      if (!questionText.trim() || questionText.length < 20) return;

      contextRef.current = { chapterId, questionText };

      if (debounceRef.current) clearTimeout(debounceRef.current);

      debounceRef.current = setTimeout(async () => {
        setIsLoading(true);
        setError(null);

        try {
          // Fetch existing topics for this chapter
          const { data: existingTopics } = await supabase
            .from('topics')
            .select('topic_name')
            .eq('chapter_id', chapterId);

          const topicNames = (existingTopics ?? []).map(
            (t: { topic_name: string }) => t.topic_name,
          );

          const prompt = buildTopicSuggestionPrompt(
            subjectName,
            chapterName,
            questionText,
            topicNames,
          );

          const response = await callGemini(prompt);
          const topicName = response.trim().replace(/^["']|["']$/g, '');

          setSuggestedTopic(topicName);
        } catch (err: unknown) {
          const msg = err instanceof Error ? err.message : 'Failed';
          setError(msg);
        } finally {
          setIsLoading(false);
        }
      }, 500);
    },
    [],
  );

  const acceptTopic = useCallback(
    async (chapterId: string): Promise<string | null> => {
      if (!suggestedTopic) return null;

      // Check if topic already exists
      const { data: existing } = await supabase
        .from('topics')
        .select('topic_id')
        .eq('chapter_id', chapterId)
        .ilike('topic_name', suggestedTopic)
        .limit(1);

      if (existing && existing.length > 0) {
        return existing[0].topic_id;
      }

      // Create new topic
      const { data: newTopic } = await supabase
        .from('topics')
        .insert({
          topic_name: suggestedTopic,
          chapter_id: chapterId,
          topic_context: contextRef.current.questionText.slice(0, 100),
        })
        .select('topic_id')
        .single();

      return newTopic?.topic_id ?? null;
    },
    [suggestedTopic],
  );

  const rejectTopic = useCallback(() => {
    setSuggestedTopic(null);
  }, []);

  return {
    suggestedTopic,
    isLoading,
    error,
    suggest,
    acceptTopic,
    rejectTopic,
  };
}
