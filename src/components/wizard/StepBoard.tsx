import { useWizard } from '@/context/WizardContext';
import { useCascadingDropdowns } from '@/hooks/useCascadingDropdowns';
import LoadingSkeleton from '@/components/shared/LoadingSkeleton';
import { Check } from 'lucide-react';

export default function StepBoard() {
  const { boardId, setBoard } = useWizard();
  const { boards, loadingBoards } = useCascadingDropdowns();

  if (loadingBoards) {
    return (
      <div>
        <h2 className="mb-2 text-2xl font-bold text-[#1E293B]">Select Board</h2>
        <p className="mb-6 text-sm text-[#64748B]">Choose the education board for your question paper</p>
        <LoadingSkeleton variant="card" count={4} />
      </div>
    );
  }

  return (
    <div>
      <h2 className="mb-2 text-2xl font-bold text-[#1E293B]">Select Board</h2>
      <p className="mb-6 text-sm text-[#64748B]">Choose the education board for your question paper</p>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {boards.map((board) => {
          const isSelected = boardId === board.board_id;
          return (
            <button
              key={board.board_id}
              onClick={() => setBoard(board.board_id, board.board_name)}
              className={`group relative flex flex-col items-start rounded-xl border-2 p-6 text-left transition-all duration-200 ${
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
              <span className="mb-2 text-lg font-semibold text-[#1E293B]">
                {board.board_name}
              </span>
              <span className="inline-flex rounded-full bg-gray-100 px-3 py-1 text-xs font-medium text-[#64748B]">
                {board.code}
              </span>
            </button>
          );
        })}
      </div>

      {boards.length === 0 && (
        <div className="rounded-xl border border-dashed border-gray-300 p-12 text-center">
          <p className="text-[#64748B]">No active boards found</p>
        </div>
      )}
    </div>
  );
}
