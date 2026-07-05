import { useWizard } from '@/context/WizardContext';
import { Badge } from '@/components/ui/badge';
import { Separator } from '@/components/ui/separator';
import {
  BookOpen,
  GraduationCap,
  Layers,
  FileText,
  Settings,
  List,
  LayoutGrid,
} from 'lucide-react';

export default function StepReview() {
  const {
    boardName,
    className,
    streamName,
    subjectName,
    paperTitle,
    totalMarks,
    duration,
    difficulty,
    selectedChapterNames,
    sections,
    needsStream,
  } = useWizard();

  const sectionsTotal = sections.reduce(
    (sum, s) => sum + s.numQuestions * s.marksPerQuestion,
    0,
  );

  const totalQuestions = sections.reduce((sum, s) => sum + s.numQuestions, 0);

  return (
    <div>
      <h2 className="mb-2 text-2xl font-bold text-[#1E293B]">Review & Generate</h2>
      <p className="mb-6 text-sm text-[#64748B]">
        Review your selections before generating the question paper
      </p>

      <div className="mx-auto max-w-3xl space-y-6">
        {/* Paper Info */}
        <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
          <div className="mb-4 flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-indigo-100">
              <FileText className="h-5 w-5 text-[#4F46E5]" />
            </div>
            <div>
              <h3 className="font-semibold text-[#1E293B]">{paperTitle}</h3>
              <div className="flex gap-2 mt-1">
                <Badge variant="secondary">{totalMarks} Marks</Badge>
                <Badge variant="secondary">{duration}</Badge>
                <Badge variant="secondary">{difficulty}</Badge>
              </div>
            </div>
          </div>

          <Separator className="my-4" />

          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <div className="flex items-center gap-2">
              <BookOpen className="h-4 w-4 text-[#64748B]" />
              <div>
                <p className="text-xs text-[#64748B]">Board</p>
                <p className="text-sm font-medium text-[#1E293B]">{boardName}</p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <GraduationCap className="h-4 w-4 text-[#64748B]" />
              <div>
                <p className="text-xs text-[#64748B]">Class</p>
                <p className="text-sm font-medium text-[#1E293B]">{className}</p>
              </div>
            </div>
            {needsStream && streamName && (
              <div className="flex items-center gap-2">
                <Layers className="h-4 w-4 text-[#64748B]" />
                <div>
                  <p className="text-xs text-[#64748B]">Stream</p>
                  <p className="text-sm font-medium text-[#1E293B]">{streamName}</p>
                </div>
              </div>
            )}
            <div className="flex items-center gap-2">
              <Settings className="h-4 w-4 text-[#64748B]" />
              <div>
                <p className="text-xs text-[#64748B]">Subject</p>
                <p className="text-sm font-medium text-[#1E293B]">{subjectName}</p>
              </div>
            </div>
          </div>
        </div>

        {/* Chapters */}
        <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
          <div className="mb-3 flex items-center gap-2">
            <List className="h-5 w-5 text-[#4F46E5]" />
            <h4 className="font-semibold text-[#1E293B]">
              Chapters ({selectedChapterNames.length})
            </h4>
          </div>
          <div className="flex flex-wrap gap-2">
            {selectedChapterNames.map((name, i) => (
              <Badge key={i} variant="outline" className="bg-gray-50">
                {name}
              </Badge>
            ))}
          </div>
        </div>

        {/* Sections */}
        <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
          <div className="mb-4 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <LayoutGrid className="h-5 w-5 text-[#4F46E5]" />
              <h4 className="font-semibold text-[#1E293B]">
                Paper Pattern ({sections.length} sections)
              </h4>
            </div>
            <span className="text-sm font-medium text-[#64748B]">
              {totalQuestions} questions • {sectionsTotal} marks
            </span>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-xs text-[#64748B]">
                  <th className="pb-2 font-medium">Section</th>
                  <th className="pb-2 font-medium">Type</th>
                  <th className="pb-2 font-medium text-center">Questions</th>
                  <th className="pb-2 font-medium text-center">Marks/Q</th>
                  <th className="pb-2 font-medium text-right">Total</th>
                </tr>
              </thead>
              <tbody>
                {sections.map((section) => (
                  <tr key={section.id} className="border-b last:border-0">
                    <td className="py-3 font-medium text-[#1E293B]">
                      {section.sectionName}
                    </td>
                    <td className="py-3 text-[#64748B]">{section.questionType}</td>
                    <td className="py-3 text-center">{section.numQuestions}</td>
                    <td className="py-3 text-center">{section.marksPerQuestion}</td>
                    <td className="py-3 text-right font-semibold text-[#1E293B]">
                      {section.numQuestions * section.marksPerQuestion}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
