import { useNavigate } from 'react-router-dom';
import { FileText, ArrowRight } from 'lucide-react';
import { Button } from '@/components/ui/button';

export default function EmptyState() {
  const navigate = useNavigate();

  return (
    <div className="flex flex-col items-center justify-center rounded-xl border-2 border-dashed border-gray-200 py-16 text-center">
      <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-indigo-50">
        <FileText className="h-8 w-8 text-[#4F46E5]" />
      </div>
      <h3 className="mb-2 text-lg font-semibold text-[#1E293B]">No papers yet</h3>
      <p className="mb-6 text-sm text-[#64748B]">
        Create your first AI-generated question paper
      </p>
      <Button
        onClick={() => navigate('/wizard')}
        className="gap-2 bg-[#4F46E5] text-white hover:bg-indigo-700"
      >
        Create Paper
        <ArrowRight className="h-4 w-4" />
      </Button>
    </div>
  );
}
