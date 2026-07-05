import { useEffect } from 'react';
import { useWizard } from '@/context/WizardContext';
import { useCascadingDropdowns } from '@/hooks/useCascadingDropdowns';
import LoadingSkeleton from '@/components/shared/LoadingSkeleton';
import { Check, Calculator, Atom, FlaskConical, Leaf, ScrollText, BookOpen, Globe, BarChart3, FileText } from 'lucide-react';

const SUBJECT_ICONS: Record<string, React.ElementType> = {
  math: Calculator,
  physics: Atom,
  chemistry: FlaskConical,
  bio: Leaf,
  history: ScrollText,
  english: BookOpen,
  geography: Globe,
  economics: BarChart3,
};

function getSubjectIcon(name: string): React.ElementType {
  const lower = name.toLowerCase();
  for (const [key, icon] of Object.entries(SUBJECT_ICONS)) {
    if (lower.includes(key)) return icon;
  }
  return FileText;
}

export default function StepSubject() {
  const { classId, subjectId, setSubject } = useWizard();
  const { subjects, loadingSubjects, fetchSubjects } = useCascadingDropdowns();

  useEffect(() => {
    if (classId) fetchSubjects(classId);
  }, [classId, fetchSubjects]);

  if (loadingSubjects) {
    return (
      <div>
        <h2 className="mb-2 text-2xl font-bold text-[#1E293B]">Select Subject</h2>
        <p className="mb-6 text-sm text-[#64748B]">Choose the subject for your question paper</p>
        <LoadingSkeleton variant="card" count={6} />
      </div>
    );
  }

  return (
    <div>
      <h2 className="mb-2 text-2xl font-bold text-[#1E293B]">Select Subject</h2>
      <p className="mb-6 text-sm text-[#64748B]">Choose the subject for your question paper</p>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {subjects.map((subject) => {
          const isSelected = subjectId === subject.subject_id;
          const Icon = getSubjectIcon(subject.subject_name);
          return (
            <button
              key={subject.subject_id}
              onClick={() => setSubject(subject.subject_id, subject.subject_name)}
              className={`group relative flex items-center gap-4 rounded-xl border-2 px-6 py-5 text-left transition-all duration-200 ${
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
              <div
                className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-xl ${
                  isSelected ? 'bg-[#4F46E5] text-white' : 'bg-gray-100 text-[#64748B]'
                } transition-colors`}
              >
                <Icon className="h-6 w-6" />
              </div>
              <span className="text-base font-semibold text-[#1E293B]">
                {subject.subject_name}
              </span>
            </button>
          );
        })}
      </div>

      {subjects.length === 0 && (
        <div className="rounded-xl border border-dashed border-gray-300 p-12 text-center">
          <p className="text-[#64748B]">No subjects found for this class</p>
        </div>
      )}
    </div>
  );
}
