import { useEffect } from 'react';
import { useCascadingDropdowns } from '@/hooks/useCascadingDropdowns';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Label } from '@/components/ui/label';
import LoadingSkeleton from '@/components/shared/LoadingSkeleton';
import { classNeedsStream } from '@/context/WizardContext';

interface Props {
  boardId: string;
  classId: string;
  streamId: string;
  subjectId: string;
  chapterId: string;
  onBoardChange: (id: string) => void;
  onClassChange: (id: string, name: string) => void;
  onStreamChange: (id: string) => void;
  onSubjectChange: (id: string, name: string) => void;
  onChapterChange: (id: string, name: string) => void;
  showStream: boolean;
  onStreamNeeded: (needed: boolean) => void;
}

export default function ContextSection({
  boardId,
  classId,
  streamId,
  subjectId,
  chapterId,
  onBoardChange,
  onClassChange,
  onStreamChange,
  onSubjectChange,
  onChapterChange,
  showStream,
  onStreamNeeded,
}: Props) {
  const {
    boards,
    classes,
    streams,
    subjects,
    chapters,
    loadingBoards,
    loadingClasses,
    loadingStreams,
    loadingSubjects,
    loadingChapters,
    fetchClasses,
    fetchStreams,
    fetchSubjects,
    fetchChapters,
  } = useCascadingDropdowns();

  useEffect(() => {
    if (boardId) {
      fetchClasses(boardId);
      fetchStreams(boardId);
    }
  }, [boardId, fetchClasses, fetchStreams]);

  useEffect(() => {
    if (classId) fetchSubjects(classId);
  }, [classId, fetchSubjects]);

  useEffect(() => {
    if (subjectId) fetchChapters(subjectId);
  }, [subjectId, fetchChapters]);

  return (
    <div className="space-y-4">
      <h3 className="text-sm font-semibold uppercase text-[#64748B]">Context</h3>

      {/* Board */}
      <div className="space-y-1.5">
        <Label>Board *</Label>
        {loadingBoards ? (
          <LoadingSkeleton variant="text" count={1} />
        ) : (
          <Select value={boardId} onValueChange={(v) => v && onBoardChange(v)}>
            <SelectTrigger>
              <SelectValue placeholder="Select board" />
            </SelectTrigger>
            <SelectContent>
              {boards.map((b) => (
                <SelectItem key={b.board_id} value={b.board_id}>
                  {b.board_name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </div>

      {/* Class */}
      <div className="space-y-1.5">
        <Label>Class *</Label>
        {loadingClasses ? (
          <LoadingSkeleton variant="text" count={1} />
        ) : (
          <Select
            value={classId}
            onValueChange={(val) => {
              if (!val) return;
              const cls = classes.find((c) => c.class_id === val);
              const needs = cls ? classNeedsStream(cls.class_name) : false;
              onStreamNeeded(needs);
              onClassChange(val, cls?.class_name ?? '');
            }}
            disabled={!boardId}
          >
            <SelectTrigger>
              <SelectValue placeholder="Select class" />
            </SelectTrigger>
            <SelectContent>
              {classes.map((c) => (
                <SelectItem key={c.class_id} value={c.class_id}>
                  {c.class_name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </div>

      {/* Stream (conditional) */}
      {showStream && (
        <div className="space-y-1.5">
          <Label>Stream</Label>
          {loadingStreams ? (
            <LoadingSkeleton variant="text" count={1} />
          ) : (
            <Select value={streamId} onValueChange={(v) => v && onStreamChange(v)}>
              <SelectTrigger>
                <SelectValue placeholder="Select stream" />
              </SelectTrigger>
              <SelectContent>
                {streams.map((s) => (
                  <SelectItem key={s.stream_id} value={s.stream_id}>
                    {s.stream_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </div>
      )}

      {/* Subject */}
      <div className="space-y-1.5">
        <Label>Subject *</Label>
        {loadingSubjects ? (
          <LoadingSkeleton variant="text" count={1} />
        ) : (
          <Select
            value={subjectId}
            onValueChange={(val) => {
              if (!val) return;
              const subj = subjects.find((s) => s.subject_id === val);
              onSubjectChange(val, subj?.subject_name ?? '');
            }}
            disabled={!classId}
          >
            <SelectTrigger>
              <SelectValue placeholder="Select subject" />
            </SelectTrigger>
            <SelectContent>
              {subjects.map((s) => (
                <SelectItem key={s.subject_id} value={s.subject_id}>
                  {s.subject_name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </div>

      {/* Chapter */}
      <div className="space-y-1.5">
        <Label>Chapter *</Label>
        {loadingChapters ? (
          <LoadingSkeleton variant="text" count={1} />
        ) : (
          <Select
            value={chapterId}
            onValueChange={(val) => {
              if (!val) return;
              const ch = chapters.find((c) => c.chapter_id === val);
              onChapterChange(val, ch?.chapter_name ?? '');
            }}
            disabled={!subjectId}
          >
            <SelectTrigger>
              <SelectValue placeholder="Select chapter" />
            </SelectTrigger>
            <SelectContent>
              {chapters.map((c) => (
                <SelectItem key={c.chapter_id} value={c.chapter_id}>
                  {c.chapter_name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </div>
    </div>
  );
}
