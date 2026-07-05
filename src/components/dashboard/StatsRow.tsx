import { FileText, Database, Calendar, Sparkles } from 'lucide-react';

interface Stats {
  totalPapers: number;
  totalQuestions: number;
  papersThisMonth: number;
  aiQuestions: number;
}

export default function StatsRow({ stats }: { stats: Stats }) {
  const cards = [
    {
      label: 'Total Papers Generated',
      value: stats.totalPapers,
      icon: FileText,
      color: 'from-indigo-500 to-purple-500',
      bgLight: 'bg-indigo-50',
    },
    {
      label: 'Questions in Bank',
      value: stats.totalQuestions,
      icon: Database,
      color: 'from-emerald-500 to-teal-500',
      bgLight: 'bg-emerald-50',
    },
    {
      label: 'Papers This Month',
      value: stats.papersThisMonth,
      icon: Calendar,
      color: 'from-amber-500 to-orange-500',
      bgLight: 'bg-amber-50',
    },
    {
      label: 'AI Questions Generated',
      value: stats.aiQuestions,
      icon: Sparkles,
      color: 'from-pink-500 to-rose-500',
      bgLight: 'bg-pink-50',
    },
  ];

  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      {cards.map((card) => (
        <div
          key={card.label}
          className="rounded-xl border border-gray-100 bg-white p-5 shadow-sm transition-shadow hover:shadow-md"
        >
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-[#64748B]">{card.label}</p>
              <p className="mt-1 text-3xl font-bold text-[#1E293B]">
                {card.value.toLocaleString()}
              </p>
            </div>
            <div
              className={`flex h-12 w-12 items-center justify-center rounded-xl bg-gradient-to-br ${card.color}`}
            >
              <card.icon className="h-6 w-6 text-white" />
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
