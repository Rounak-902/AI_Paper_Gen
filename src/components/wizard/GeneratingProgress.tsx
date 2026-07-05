import { useNavigate } from 'react-router-dom';
import type { SectionProgress } from '@/types/database';
import { Loader2, Check, AlertCircle, Clock, Sparkles } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface Props {
  paperTitle: string;
  sectionProgress: SectionProgress[];
  isGenerating: boolean;
  paperId: string | null;
  error: string | null;
}

export default function GeneratingProgress({
  paperTitle,
  sectionProgress,
  isGenerating,
  paperId,
  error,
}: Props) {
  const navigate = useNavigate();

  const completedCount = sectionProgress.filter((s) => s.status === 'done').length;
  const totalCount = sectionProgress.length;
  const progressPercent = totalCount > 0 ? (completedCount / totalCount) * 100 : 0;
  const allDone = completedCount === totalCount && !isGenerating;
  const totalQuestions = sectionProgress.reduce((s, p) => s + p.questionCount, 0);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-[#0F172A]/80 backdrop-blur-sm">
      <div className="mx-4 w-full max-w-lg rounded-2xl bg-white p-8 shadow-2xl">
        {/* Header */}
        <div className="mb-6 text-center">
          <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br from-[#4F46E5] to-purple-600">
            <Sparkles className="h-8 w-8 text-white" />
          </div>
          <h3 className="text-xl font-bold text-[#1E293B]">
            {allDone ? '✨ Paper Generated!' : 'Generating Paper...'}
          </h3>
          <p className="mt-1 text-sm text-[#64748B]">{paperTitle}</p>
        </div>

        {/* Progress bar */}
        <div className="mb-6">
          <div className="mb-2 flex justify-between text-xs text-[#64748B]">
            <span>
              {completedCount} / {totalCount} sections
            </span>
            <span>{Math.round(progressPercent)}%</span>
          </div>
          <div className="h-3 overflow-hidden rounded-full bg-gray-100">
            <div
              className="h-full rounded-full bg-gradient-to-r from-[#4F46E5] to-purple-500 transition-all duration-700"
              style={{ width: `${progressPercent}%` }}
            />
          </div>
        </div>

        {/* Section progress */}
        <div className="mb-6 space-y-3">
          {sectionProgress.map((section, i) => (
            <div
              key={i}
              className={`flex items-center justify-between rounded-lg px-4 py-3 ${
                section.status === 'done'
                  ? 'bg-emerald-50'
                  : section.status === 'generating'
                    ? 'bg-indigo-50'
                    : section.status === 'error'
                      ? 'bg-red-50'
                      : 'bg-gray-50'
              }`}
            >
              <div className="flex items-center gap-3">
                {section.status === 'waiting' && (
                  <Clock className="h-4 w-4 text-[#64748B]" />
                )}
                {section.status === 'generating' && (
                  <Loader2 className="h-4 w-4 animate-spin text-[#4F46E5]" />
                )}
                {section.status === 'done' && (
                  <Check className="h-4 w-4 text-[#10B981]" />
                )}
                {section.status === 'error' && (
                  <AlertCircle className="h-4 w-4 text-[#EF4444]" />
                )}
                <span
                  className={`text-sm font-medium ${
                    section.status === 'done'
                      ? 'text-[#10B981]'
                      : section.status === 'generating'
                        ? 'text-[#4F46E5]'
                        : section.status === 'error'
                          ? 'text-[#EF4444]'
                          : 'text-[#64748B]'
                  }`}
                >
                  {section.sectionName}
                </span>
              </div>
              <span className="text-xs text-[#64748B]">
                {section.status === 'waiting' && 'Waiting...'}
                {section.status === 'generating' && 'Generating...'}
                {section.status === 'done' && `${section.questionCount} Qs ✓`}
                {section.status === 'error' && (section.error ?? 'Failed')}
              </span>
            </div>
          ))}
        </div>

        {/* Error message */}
        {error && (
          <div className="mb-4 rounded-lg bg-red-50 px-4 py-3 text-sm text-[#EF4444]">
            {error}
          </div>
        )}

        {/* Action buttons */}
        {allDone && paperId && (
          <div className="space-y-3">
            <div className="rounded-lg bg-emerald-50 px-4 py-3 text-center">
              <p className="text-sm font-medium text-[#10B981]">
                🎉 {totalQuestions} questions generated successfully!
              </p>
            </div>
            <Button
              onClick={() => navigate(`/preview/${paperId}`)}
              className="h-11 w-full bg-[#4F46E5] text-white hover:bg-indigo-700"
            >
              View Paper →
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
