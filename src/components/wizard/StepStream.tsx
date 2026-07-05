import { useEffect } from 'react';
import { useWizard } from '@/context/WizardContext';
import { useCascadingDropdowns } from '@/hooks/useCascadingDropdowns';
import LoadingSkeleton from '@/components/shared/LoadingSkeleton';
import { Check } from 'lucide-react';

export default function StepStream() {
  const { boardId, className, streamId, setStream, setClass } = useWizard();
  const { streams, classes, loadingStreams, fetchStreams, fetchClasses } = useCascadingDropdowns();

  useEffect(() => {
    if (boardId) {
      fetchStreams(boardId);
      fetchClasses(boardId);
    }
  }, [boardId, fetchStreams, fetchClasses]);

  const handleStreamSelect = (stream: typeof streams[0]) => {
    setStream(stream.stream_id, stream.stream_name);

    // Now find the correct class_id for this className + stream combination
    // e.g., Class "11" + Stream "Science" → the class row with class_name="11" and stream_id matching
    const matchingClass = classes.find(
      (cls) => cls.class_name === className && cls.stream_id === stream.stream_id
    );
    if (matchingClass) {
      // Update the classId to the correct one for this stream
      setClass(matchingClass.class_id, matchingClass.class_name, true);
      // Re-set the stream since setClass clears it
      setTimeout(() => setStream(stream.stream_id, stream.stream_name), 0);
    }
  };

  if (loadingStreams) {
    return (
      <div>
        <h2 className="mb-2 text-2xl font-bold text-[#1E293B]">Select Stream</h2>
        <p className="mb-6 text-sm text-[#64748B]">Choose the academic stream for Class {className}</p>
        <LoadingSkeleton variant="card" count={3} />
      </div>
    );
  }

  return (
    <div>
      <h2 className="mb-2 text-2xl font-bold text-[#1E293B]">Select Stream</h2>
      <p className="mb-6 text-sm text-[#64748B]">Choose the academic stream for Class {className}</p>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {streams.map((stream) => {
          const isSelected = streamId === stream.stream_id;
          const emoji =
            stream.stream_name === 'Science' ? '🔬' :
            stream.stream_name === 'Commerce' ? '📊' :
            stream.stream_name === 'Arts' ? '🎨' : '📚';

          return (
            <button
              key={stream.stream_id}
              onClick={() => handleStreamSelect(stream)}
              className={`group relative flex flex-col items-center justify-center gap-2 rounded-xl border-2 px-6 py-10 transition-all duration-200 ${
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
              <span className="text-2xl">{emoji}</span>
              <span className="text-lg font-semibold text-[#1E293B]">
                {stream.stream_name}
              </span>
            </button>
          );
        })}
      </div>

      {streams.length === 0 && (
        <div className="rounded-xl border border-dashed border-gray-300 p-12 text-center">
          <p className="text-[#64748B]">No streams found for this board</p>
        </div>
      )}
    </div>
  );
}
