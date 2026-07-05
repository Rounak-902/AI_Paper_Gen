import { useEffect } from 'react';
import { useWizard } from '@/context/WizardContext';
import { useCascadingDropdowns } from '@/hooks/useCascadingDropdowns';
import { Checkbox } from '@/components/ui/checkbox';
import LoadingSkeleton from '@/components/shared/LoadingSkeleton';

export default function StepChapters() {
  const {
    subjectId,
    selectedChapterIds,
    selectedChapterNames,
    setChapters,
  } = useWizard();
  const { chapters, loadingChapters, fetchChapters } = useCascadingDropdowns();

  useEffect(() => {
    if (subjectId) fetchChapters(subjectId);
  }, [subjectId, fetchChapters]);

  const toggleChapter = (id: string, name: string) => {
    const idx = selectedChapterIds.indexOf(id);
    if (idx > -1) {
      setChapters(
        selectedChapterIds.filter((_, i) => i !== idx),
        selectedChapterNames.filter((_, i) => i !== idx),
      );
    } else {
      setChapters([...selectedChapterIds, id], [...selectedChapterNames, name]);
    }
  };

  const selectAll = () => {
    setChapters(
      chapters.map((c) => c.chapter_id),
      chapters.map((c) => c.chapter_name),
    );
  };

  const deselectAll = () => {
    setChapters([], []);
  };

  if (loadingChapters) {
    return (
      <div>
        <h2 className="mb-2 text-2xl font-bold text-[#1E293B]">Select Chapters</h2>
        <p className="mb-6 text-sm text-[#64748B]">Choose chapters to include in your paper</p>
        <LoadingSkeleton variant="row" count={6} />
      </div>
    );
  }

  const allSelected = chapters.length > 0 && selectedChapterIds.length === chapters.length;

  return (
    <div>
      <h2 className="mb-2 text-2xl font-bold text-[#1E293B]">Select Chapters</h2>
      <p className="mb-6 text-sm text-[#64748B]">
        Choose chapters to include in your paper (min. 1 chapter)
      </p>

      {/* Select All / Deselect All */}
      <div className="mb-4 flex items-center gap-4">
        <button
          onClick={allSelected ? deselectAll : selectAll}
          className="text-sm font-medium text-[#4F46E5] hover:text-indigo-700"
        >
          {allSelected ? 'Deselect All' : 'Select All'}
        </button>
        <span className="text-sm text-[#64748B]">
          {selectedChapterIds.length} of {chapters.length} selected
        </span>
      </div>

      {/* Chapter list */}
      <div className="space-y-2">
        {chapters.map((chapter, index) => {
          const isChecked = selectedChapterIds.includes(chapter.chapter_id);
          return (
            <label
              key={chapter.chapter_id}
              className={`flex cursor-pointer items-center gap-4 rounded-xl border-2 px-5 py-4 transition-all ${
                isChecked
                  ? 'border-[#4F46E5] bg-indigo-50'
                  : 'border-gray-200 bg-white hover:border-gray-300'
              }`}
            >
              <Checkbox
                checked={isChecked}
                onCheckedChange={() =>
                  toggleChapter(chapter.chapter_id, chapter.chapter_name)
                }
              />
              <span className="mr-3 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-gray-100 text-xs font-semibold text-[#64748B]">
                {index + 1}
              </span>
              <span className="text-sm font-medium text-[#1E293B]">
                {chapter.chapter_name}
              </span>
            </label>
          );
        })}
      </div>

      {chapters.length === 0 && (
        <div className="rounded-xl border border-dashed border-gray-300 p-12 text-center">
          <p className="text-[#64748B]">No chapters found for this subject</p>
        </div>
      )}
    </div>
  );
}
