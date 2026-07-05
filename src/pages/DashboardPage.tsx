import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '@/context/AuthContext';
import { usePapers } from '@/hooks/usePapers';
import { supabase } from '@/lib/supabaseClient';
import StatsRow from '@/components/dashboard/StatsRow';
import PaperCard from '@/components/dashboard/PaperCard';
import EmptyState from '@/components/dashboard/EmptyState';
import LoadingSkeleton from '@/components/shared/LoadingSkeleton';
import ConfirmDialog from '@/components/shared/ConfirmDialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Plus } from 'lucide-react';
import { toast } from 'sonner';

export default function DashboardPage() {
  const { profile } = useAuth();
  const { papers, loading, fetchPapers, renamePaper, deletePaper } = usePapers();
  const navigate = useNavigate();

  const [stats, setStats] = useState({
    totalPapers: 0,
    totalQuestions: 0,
    papersThisMonth: 0,
    aiQuestions: 0,
  });
  const [deleteId, setDeleteId] = useState<string | null>(null);

  const loadStats = async () => {
    const { count: totalPapers } = await supabase
      .from('generated_papers')
      .select('*', { count: 'exact', head: true })
      .eq('is_deleted', false);

    const { count: totalQuestions } = await supabase
      .from('question_bank')
      .select('*', { count: 'exact', head: true })
      .eq('is_active', true);

    const startOfMonth = new Date();
    startOfMonth.setDate(1);
    startOfMonth.setHours(0, 0, 0, 0);

    const { count: papersThisMonth } = await supabase
      .from('generated_papers')
      .select('*', { count: 'exact', head: true })
      .eq('is_deleted', false)
      .gte('created_at', startOfMonth.toISOString());

    const { count: aiQuestions } = await supabase
      .from('question_bank')
      .select('*', { count: 'exact', head: true })
      .eq('source', 'ai_generated')
      .eq('is_active', true);

    setStats({
      totalPapers: totalPapers ?? 0,
      totalQuestions: totalQuestions ?? 0,
      papersThisMonth: papersThisMonth ?? 0,
      aiQuestions: aiQuestions ?? 0,
    });
  };

  useEffect(() => {
    fetchPapers();
    loadStats();
  }, [fetchPapers]);

  const handleRename = async (id: string, title: string) => {
    await renamePaper(id, title);
    toast.success('Paper renamed');
    fetchPapers();
  };

  const handleDelete = async () => {
    if (!deleteId) return;
    await deletePaper(deleteId);
    toast.success('Paper deleted');
    setDeleteId(null);
    fetchPapers();
    loadStats();
  };

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-[#1E293B]">
            Welcome back, {profile?.full_name ?? 'User'} 👋
          </h1>
          <div className="mt-1 flex items-center gap-2">
            <Badge
              variant="secondary"
              className={
                profile?.role === 'admin'
                  ? 'bg-amber-100 text-amber-700'
                  : 'bg-indigo-100 text-indigo-700'
              }
            >
              {profile?.role === 'admin' ? '🔑 Admin' : '👩‍🏫 Teacher'}
            </Badge>
          </div>
        </div>
        <Button
          onClick={() => navigate('/wizard')}
          className="gap-2 bg-[#4F46E5] text-white hover:bg-indigo-700"
        >
          <Plus className="h-4 w-4" />
          Create New Paper
        </Button>
      </div>

      {/* Stats */}
      <StatsRow stats={stats} />

      {/* Papers */}
      <div>
        <h2 className="mb-4 text-lg font-semibold text-[#1E293B]">My Papers</h2>
        {loading ? (
          <LoadingSkeleton variant="card" count={3} />
        ) : papers.length === 0 ? (
          <EmptyState />
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {papers.map((paper) => (
              <PaperCard
                key={paper.paper_id}
                paper={paper}
                onRename={handleRename}
                onDelete={(id) => setDeleteId(id)}
              />
            ))}
          </div>
        )}
      </div>

      {/* Delete confirmation */}
      <ConfirmDialog
        open={!!deleteId}
        title="Delete Paper"
        message="Are you sure you want to delete this paper? This action cannot be undone."
        confirmLabel="Delete"
        onConfirm={handleDelete}
        onCancel={() => setDeleteId(null)}
      />
    </div>
  );
}
