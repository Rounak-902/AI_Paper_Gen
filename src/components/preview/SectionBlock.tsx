import type { PaperSection, PaperQuestionWithDetails } from '@/types/database';
import QuestionCard from './QuestionCard';

interface Props {
  section: PaperSection;
  startingNumber: number;
  showAnswerKey: boolean;
  onRegenerate: (pq: PaperQuestionWithDetails) => Promise<void>;
  onEdit: (questionId: string, text: string, answer: string) => Promise<void>;
  onDelete: (paperQuestionId: string) => void;
}

export default function SectionBlock({
  section,
  startingNumber,
  showAnswerKey,
  onRegenerate,
  onEdit,
  onDelete,
}: Props) {
  const totalMarks = section.questions.length * section.marksPerQuestion;

  return (
    <div className="mb-8">
      {/* Section header */}
      <div className="mb-4 flex items-center justify-between border-b-2 border-gray-300 pb-2">
        <h3 className="text-base font-bold uppercase text-[#1E293B]">
          {section.sectionName} — {section.questionType}
        </h3>
        <span className="text-sm font-medium text-[#64748B]">
          [{section.marksPerQuestion} Mark{section.marksPerQuestion > 1 ? 's' : ''} Each
          × {section.questions.length} = {totalMarks} Mark{totalMarks > 1 ? 's' : ''}]
        </span>
      </div>

      {/* Questions */}
      {section.questions.map((pq, index) => (
        <QuestionCard
          key={pq.id}
          pq={pq}
          questionNumber={startingNumber + index}
          showAnswerKey={showAnswerKey}
          onRegenerate={onRegenerate}
          onEdit={onEdit}
          onDelete={onDelete}
        />
      ))}
    </div>
  );
}
