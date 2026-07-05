import { useState, useEffect, useCallback } from 'react';
import { supabase } from '../lib/supabaseClient';
import type { Board, Stream, Class, Subject, Chapter, Topic } from '../types/database';

interface CascadingState {
  boards: Board[];
  streams: Stream[];
  classes: Class[];
  subjects: Subject[];
  chapters: Chapter[];
  topics: Topic[];
  loadingBoards: boolean;
  loadingStreams: boolean;
  loadingClasses: boolean;
  loadingSubjects: boolean;
  loadingChapters: boolean;
  loadingTopics: boolean;
  fetchBoards: () => Promise<void>;
  fetchStreams: (boardId: string) => Promise<void>;
  fetchClasses: (boardId: string) => Promise<void>;
  fetchSubjects: (classId: string) => Promise<void>;
  fetchChapters: (subjectId: string) => Promise<void>;
  fetchTopics: (chapterId: string) => Promise<void>;
}

export function useCascadingDropdowns(): CascadingState {
  const [boards, setBoards] = useState<Board[]>([]);
  const [streams, setStreams] = useState<Stream[]>([]);
  const [classes, setClasses] = useState<Class[]>([]);
  const [subjects, setSubjects] = useState<Subject[]>([]);
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [topics, setTopics] = useState<Topic[]>([]);

  const [loadingBoards, setLoadingBoards] = useState(false);
  const [loadingStreams, setLoadingStreams] = useState(false);
  const [loadingClasses, setLoadingClasses] = useState(false);
  const [loadingSubjects, setLoadingSubjects] = useState(false);
  const [loadingChapters, setLoadingChapters] = useState(false);
  const [loadingTopics, setLoadingTopics] = useState(false);

  const fetchBoards = useCallback(async () => {
    setLoadingBoards(true);
    const { data } = await supabase
      .from('boards')
      .select('*')
      .eq('is_active', true)
      .order('board_name');
    setBoards((data as Board[]) ?? []);
    setLoadingBoards(false);
  }, []);

  const fetchStreams = useCallback(async (boardId: string) => {
    setLoadingStreams(true);
    const { data } = await supabase
      .from('stream')
      .select('stream_id, "stream name", board_id, is_active, created_at')
      .eq('board_id', boardId)
      .eq('is_active', true);
    // Map "stream name" (with space) to stream_name for our interface
    const mapped = (data ?? []).map((row: Record<string, unknown>) => ({
      stream_id: row.stream_id,
      stream_name: row['stream name'],
      board_id: row.board_id,
      is_active: row.is_active,
      created_at: row.created_at,
    })) as Stream[];
    setStreams(mapped);
    setLoadingStreams(false);
  }, []);

  const fetchClasses = useCallback(async (boardId: string) => {
    setLoadingClasses(true);
    const { data } = await supabase
      .from('class')
      .select('*')
      .eq('board_id', boardId)
      .eq('is_active', true)
      .order('class_name');
    setClasses((data as Class[]) ?? []);
    setLoadingClasses(false);
  }, []);

  const fetchSubjects = useCallback(async (classId: string) => {
    setLoadingSubjects(true);
    const { data } = await supabase
      .from('subject')
      .select('*')
      .eq('class_id', classId)
      .eq('is_active', true)
      .order('subject_name');
    setSubjects((data as Subject[]) ?? []);
    setLoadingSubjects(false);
  }, []);

  const fetchChapters = useCallback(async (subjectId: string) => {
    setLoadingChapters(true);
    const { data } = await supabase
      .from('chapters')
      .select('*')
      .eq('subject_id', subjectId)
      .eq('is_active', true)
      .order('chapter_name');
    setChapters((data as Chapter[]) ?? []);
    setLoadingChapters(false);
  }, []);

  const fetchTopics = useCallback(async (chapterId: string) => {
    setLoadingTopics(true);
    const { data } = await supabase
      .from('topics')
      .select('*')
      .eq('chapter_id', chapterId)
      .order('topic_name');
    setTopics((data as Topic[]) ?? []);
    setLoadingTopics(false);
  }, []);

  // Auto-fetch boards on mount
  useEffect(() => {
    fetchBoards();
  }, [fetchBoards]);

  return {
    boards,
    streams,
    classes,
    subjects,
    chapters,
    topics,
    loadingBoards,
    loadingStreams,
    loadingClasses,
    loadingSubjects,
    loadingChapters,
    loadingTopics,
    fetchBoards,
    fetchStreams,
    fetchClasses,
    fetchSubjects,
    fetchChapters,
    fetchTopics,
  };
}
