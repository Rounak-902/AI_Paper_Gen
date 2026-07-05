import { useEffect, useMemo } from 'react';
import { useWizard, classNeedsStream } from '@/context/WizardContext';
import { useCascadingDropdowns } from '@/hooks/useCascadingDropdowns';
import LoadingSkeleton from '@/components/shared/LoadingSkeleton';
import { Check } from 'lucide-react';

export default function StepClass() {
  const { boardId, classId, className, setClass } = useWizard();
  const { classes, loadingClasses, fetchClasses } = useCascadingDropdowns();

  useEffect(() => {
    if (boardId) fetchClasses(boardId);
  }, [boardId, fetchClasses]);

  // Deduplicate classes by class_name (11 and 12 appear multiple times for each stream)
  const uniqueClasses = useMemo(() => {
    const seen = new Map<string, typeof classes[0]>();
    for (const cls of classes) {
      if (!seen.has(cls.class_name)) {
        seen.set(cls.class_name, cls);
      }
    }
    // Sort numerically
    return Array.from(seen.values()).sort((a, b) => {
      const numA = parseInt(a.class_name) || 99;
      const numB = parseInt(b.class_name) || 99;
      return numA - numB;
    });
  }, [classes]);

  if (loadingClasses) {
    return (
      <div>
        <h2 className="mb-2 text-2xl font-bold text-[#1E293B]">Select Class</h2>
        <p className="mb-6 text-sm text-[#64748B]">Choose the class for your question paper</p>
        <LoadingSkeleton variant="card" count={6} />
      </div>
    );
  }

  return (
    <div>
      <h2 className="mb-2 text-2xl font-bold text-[#1E293B]">Select Class</h2>
      <p className="mb-6 text-sm text-[#64748B]">Choose the class for your question paper</p>

      <div className="grid gap-4 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4">
        {uniqueClasses.map((cls) => {
          // For classes without streams, match by class_id
          // For 11/12, match by class_name since the actual class_id depends on stream
          const isSelected = classNeedsStream(cls.class_name)
            ? className === cls.class_name
            : classId === cls.class_id;

          return (
            <button
              key={cls.class_id}
              onClick={() =>
                setClass(cls.class_id, cls.class_name, classNeedsStream(cls.class_name))
              }
              className={`group relative flex items-center justify-center rounded-xl border-2 px-6 py-8 transition-all duration-200 ${
                isSelected
                  ? 'border-[#4F46E5] bg-indigo-50 shadow-lg shadow-indigo-100'
                  : 'border-gray-200 bg-white hover:border-indigo-200 hover:shadow-md'
              }`}
            >
              {isSelected && (
                <div className="absolute right-3 top-3 flex h-6 w-6 items-center justify-center rounded-full bg-[#4F46E5]">
                  <Check className="h-4 w-4 text-white" />
                </div>
              )}
              <span className="text-lg font-semibold text-[#1E293B]">
                Class {cls.class_name}
              </span>
            </button>
          );
        })}
      </div>

      {uniqueClasses.length === 0 && (
        <div className="rounded-xl border border-dashed border-gray-300 p-12 text-center">
          <p className="text-[#64748B]">No classes found for this board</p>
        </div>
      )}
    </div>
  );
}
