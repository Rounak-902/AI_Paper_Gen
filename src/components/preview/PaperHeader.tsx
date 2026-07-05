import { useState } from 'react';

interface Props {
  schoolName: string;
  boardName: string;
  subjectName: string;
  className: string;
  streamName?: string | null;
  totalMarks: number;
  duration: string;
}

export default function PaperHeader({
  schoolName: initialSchool,
  boardName,
  subjectName,
  className,
  streamName,
  totalMarks,
  duration,
}: Props) {
  const [schoolName, setSchoolName] = useState(initialSchool);
  const [editingSchool, setEditingSchool] = useState(false);

  const today = new Date().toLocaleDateString('en-IN', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  });

  return (
    <div className="mb-6 border-b-2 border-gray-800 pb-4 text-center">
      {/* School name */}
      {editingSchool ? (
        <input
          value={schoolName}
          onChange={(e) => setSchoolName(e.target.value)}
          onBlur={() => setEditingSchool(false)}
          onKeyDown={(e) => e.key === 'Enter' && setEditingSchool(false)}
          autoFocus
          className="mx-auto mb-1 w-full max-w-md border-b-2 border-[#4F46E5] bg-transparent text-center text-xl font-bold text-[#1E293B] outline-none"
        />
      ) : (
        <h1
          onClick={() => setEditingSchool(true)}
          className="mb-1 cursor-pointer text-xl font-bold uppercase tracking-wide text-[#1E293B] hover:text-[#4F46E5]"
          data-no-print
          title="Click to edit"
        >
          {schoolName || 'Click to add school name'}
        </h1>
      )}

      <div className="mb-2 text-sm text-[#64748B]">{boardName}</div>

      <h2 className="mb-3 text-lg font-bold text-[#1E293B]">
        {subjectName} Question Paper
      </h2>

      <div className="flex items-center justify-between text-sm text-[#1E293B]">
        <span>
          <strong>Class:</strong> {className}
          {streamName && ` (${streamName})`}
        </span>
        <span>
          <strong>Date:</strong> {today}
        </span>
      </div>
      <div className="mt-1 flex items-center justify-between text-sm text-[#1E293B]">
        <span>
          <strong>Max Marks:</strong> {totalMarks}
        </span>
        <span>
          <strong>Duration:</strong> {duration}
        </span>
      </div>
    </div>
  );
}
