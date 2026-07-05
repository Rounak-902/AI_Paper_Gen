import { useEffect, useState, useCallback } from 'react';
import { useParams } from 'react-router-dom';
import { useAuth } from '@/context/AuthContext';
import { usePapers } from '@/hooks/usePapers';
import { supabase } from '@/lib/supabaseClient';
import PaperPreview from '@/components/preview/PaperPreview';
import ExportButtons from '@/components/preview/ExportButtons';
import LoadingSkeleton from '@/components/shared/LoadingSkeleton';
import type { GeneratedPaper, PaperSection, PaperQuestionWithDetails } from '@/types/database';
import { toast } from 'sonner';
import { Badge } from '@/components/ui/badge';

export default function PreviewPage() {
  const { paper_id } = useParams<{ paper_id: string }>();
  const { user } = useAuth();
  const {
    fetchPaperDetails,
    markFinal,
    regenerateQuestion,
    updateQuestion,
    removeQuestionFromPaper,
  } = usePapers();

  const [paper, setPaper] = useState<GeneratedPaper | null>(null);
  const [sections, setSections] = useState<PaperSection[]>([]);
  const [showAnswerKey, setShowAnswerKey] = useState(false);
  const [loading, setLoading] = useState(true);

  // Get names from supabase for display
  const [boardName, setBoardName] = useState('');
  const [subjectName, setSubjectName] = useState('');
  const [className, setClassName] = useState('');
  const [streamName, setStreamName] = useState<string | null>(null);

  const loadPaper = useCallback(async () => {
    if (!paper_id) return;
    setLoading(true);

    const result = await fetchPaperDetails(paper_id);
    if (result) {
      setPaper(result.paper);
      setSections(result.sections);

      // Fetch display names
      if (result.paper.board_id) {
        const { data } = await supabase
          .from('boards')
          .select('board_name')
          .eq('board_id', result.paper.board_id)
          .single();
        if (data) setBoardName(data.board_name);
      }
      if (result.paper.subject_id) {
        const { data } = await supabase
          .from('subject')
          .select('subject_name')
          .eq('subject_id', result.paper.subject_id)
          .single();
        if (data) setSubjectName(data.subject_name);
      }
      if (result.paper.class_id) {
        const { data } = await supabase
          .from('class')
          .select('class_name')
          .eq('class_id', result.paper.class_id)
          .single();
        if (data) setClassName(data.class_name);
      }
      if (result.paper.stream_id) {
        const { data } = await supabase
          .from('stream')
          .select('stream_name')
          .eq('stream_id', result.paper.stream_id)
          .single();
        if (data) setStreamName(data.stream_name);
      }
    }
    setLoading(false);
  }, [paper_id, fetchPaperDetails]);

  useEffect(() => {
    loadPaper();
  }, [loadPaper]);

  const handleRegenerate = async (pq: PaperQuestionWithDetails) => {
    if (!paper || !user) return;

    // Get chapter name
    let chapterName = '';
    if (pq.question.chapter_id) {
      const { data } = await supabase
        .from('chapters')
        .select('chapter_name')
        .eq('chapter_id', pq.question.chapter_id)
        .single();
      if (data) chapterName = data.chapter_name;
    }

    const success = await regenerateQuestion(
      pq.id,
      pq.question.question_id,
      pq.question.question_type,
      pq.question.marks,
      subjectName,
      chapterName,
      className,
      boardName,
      pq.question.question_text,
      user.id,
      paper.paper_id,
      pq.section_name ?? '',
      pq.order_index ?? 0,
    );

    if (success) {
      toast.success('Question regenerated');
      await loadPaper();
    } else {
      toast.error('Failed to regenerate question');
    }
  };

  const handleEdit = async (questionId: string, text: string, answer: string) => {
    await updateQuestion(questionId, { question_text: text, answer_text: answer });
    toast.success('Question updated');
    await loadPaper();
  };

  const handleDelete = async (paperQuestionId: string) => {
    await removeQuestionFromPaper(paperQuestionId);
    toast.success('Question removed');
    await loadPaper();
  };

  const handleMarkFinal = async () => {
    if (!paper) return;
    await markFinal(paper.paper_id);
    toast.success('Paper marked as final');
    await loadPaper();
  };

  if (loading) {
    return (
      <div className="space-y-6">
        <LoadingSkeleton variant="text" count={3} />
        <LoadingSkeleton variant="card" count={2} />
      </div>
    );
  }

  if (!paper) {
    return (
      <div className="flex h-[50vh] items-center justify-center">
        <p className="text-[#64748B]">Paper not found</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Title bar */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-[#1E293B]">
            {paper.paper_title}
          </h1>
          <div className="mt-1 flex items-center gap-2">
            <Badge variant={paper.status === 'final' ? 'default' : 'secondary'}>
              {paper.status === 'final' ? '✅ Final' : '📝 Draft'}
            </Badge>
          </div>
        </div>
      </div>

      {/* Export toolbar */}
      <ExportButtons
        status={paper.status}
        showAnswerKey={showAnswerKey}
        onToggleAnswerKey={() => setShowAnswerKey(!showAnswerKey)}
        onMarkFinal={handleMarkFinal}
        paperTitle={paper.paper_title ?? 'question-paper'}
      />

      {/* Paper preview */}
      <PaperPreview
        paper={paper}
        sections={sections}
        boardName={boardName}
        subjectName={subjectName}
        className={className}
        streamName={streamName}
        showAnswerKey={showAnswerKey}
        onRegenerate={handleRegenerate}
        onEdit={handleEdit}
        onDelete={handleDelete}
      />
    </div>
  );
}
