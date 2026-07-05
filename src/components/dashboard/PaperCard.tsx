import { useNavigate } from 'react-router-dom';
import type { GeneratedPaper } from '@/types/database';
import { Badge } from '@/components/ui/badge';
import { Eye, Pencil, Trash2, Clock } from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';
import { useState } from 'react';
import { Input } from '@/components/ui/input';

interface Props {
  paper: GeneratedPaper;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
}

export default function PaperCard({ paper, onRename, onDelete }: Props) {
  const navigate = useNavigate();
  const [renaming, setRenaming] = useState(false);
  const [title, setTitle] = useState(paper.paper_title ?? '');

  const handleRename = () => {
    onRename(paper.paper_id, title);
    setRenaming(false);
  };

  const timeAgo = formatDistanceToNow(new Date(paper.created_at), {
    addSuffix: true,
  });

  return (
    <div className="group rounded-xl border border-gray-100 bg-white p-5 shadow-sm transition-all hover:border-indigo-100 hover:shadow-md">
      <div className="flex items-start justify-between">
        <div className="flex-1">
          {renaming ? (
            <div className="flex gap-2">
              <Input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                className="h-8"
                autoFocus
                onKeyDown={(e) => e.key === 'Enter' && handleRename()}
              />
              <button
                onClick={handleRename}
                className="rounded bg-[#4F46E5] px-3 text-xs text-white"
              >
                Save
              </button>
            </div>
          ) : (
            <h3 className="font-semibold text-[#1E293B]">
              {paper.paper_title ?? 'Untitled Paper'}
            </h3>
          )}

          <div className="mt-2 flex flex-wrap gap-2">
            <Badge variant={paper.status === 'final' ? 'default' : 'secondary'}>
              {paper.status === 'final' ? '✅ Final' : '📝 Draft'}
            </Badge>
            {paper.total_marks && (
              <Badge variant="outline">{paper.total_marks} Marks</Badge>
            )}
            {paper.difficulty && (
              <Badge variant="outline">{paper.difficulty}</Badge>
            )}
            {paper.duration && (
              <Badge variant="outline">{paper.duration}</Badge>
            )}
          </div>
        </div>
      </div>

      <div className="mt-3 flex items-center justify-between">
        <div className="flex items-center gap-1 text-xs text-[#64748B]">
          <Clock className="h-3 w-3" />
          {timeAgo}
        </div>

        <div className="flex gap-1 opacity-0 transition-opacity group-hover:opacity-100">
          <button
            onClick={() => navigate(`/preview/${paper.paper_id}`)}
            className="rounded-lg p-2 text-[#4F46E5] hover:bg-indigo-50"
            title="View"
          >
            <Eye className="h-4 w-4" />
          </button>
          <button
            onClick={() => setRenaming(true)}
            className="rounded-lg p-2 text-[#F59E0B] hover:bg-amber-50"
            title="Rename"
          >
            <Pencil className="h-4 w-4" />
          </button>
          <button
            onClick={() => onDelete(paper.paper_id)}
            className="rounded-lg p-2 text-[#EF4444] hover:bg-red-50"
            title="Delete"
          >
            <Trash2 className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
