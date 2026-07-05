import { useState, useMemo } from 'react';
import type { PaperSection, PaperQuestionWithDetails, GeneratedPaper } from '@/types/database';
import PaperHeader from './PaperHeader';
import SectionBlock from './SectionBlock';
import { Textarea } from '@/components/ui/textarea';

const DEFAULT_INSTRUCTIONS = `1. Attempt all questions.
2. Marks are indicated against each question.
3. Write neatly and clearly.
4. Draw diagrams wherever necessary.`;

interface Props {
  paper: GeneratedPaper;
  sections: PaperSection[];
  boardName: string;
  subjectName: string;
  className: string;
  streamName?: string | null;
  showAnswerKey: boolean;
  onRegenerate: (pq: PaperQuestionWithDetails) => Promise<void>;
  onEdit: (questionId: string, text: string, answer: string) => Promise<void>;
  onDelete: (paperQuestionId: string) => void;
}

export default function PaperPreview({
  paper,
  sections,
  boardName,
  subjectName,
  className,
  streamName,
  showAnswerKey,
  onRegenerate,
  onEdit,
  onDelete,
}: Props) {
  const [instructions, setInstructions] = useState(DEFAULT_INSTRUCTIONS);
  const [editingInstructions, setEditingInstructions] = useState(false);

  // Precompute starting question numbers for each section
  const sectionStartNumbers = useMemo(() => {
    const starts: number[] = [];
    let counter = 1;
    for (const section of sections) {
      starts.push(counter);
      counter += section.questions.length;
    }
    return starts;
  }, [sections]);

  return (
    <div
      id="paper-preview"
      className="mx-auto max-w-4xl rounded-lg bg-white p-10 shadow-lg print:shadow-none print:p-0"
      style={{ fontFamily: "'Times New Roman', serif" }}
    >
      <PaperHeader
        schoolName=""
        boardName={boardName}
        subjectName={subjectName}
        className={className}
        streamName={streamName}
        totalMarks={paper.total_marks ?? 0}
        duration={paper.duration ?? ''}
      />

      {/* General Instructions */}
      <div className="mb-6 rounded-lg bg-gray-50 px-5 py-3 print:bg-transparent print:border print:border-gray-300">
        <div className="mb-2 flex items-center justify-between">
          <h4 className="text-sm font-bold text-[#1E293B]">General Instructions:</h4>
          {!editingInstructions && (
            <button
              onClick={() => setEditingInstructions(true)}
              className="text-xs text-[#4F46E5] hover:underline print:hidden"
              data-no-print
            >
              Edit
            </button>
          )}
        </div>
        {editingInstructions ? (
          <div data-no-print>
            <Textarea
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
              className="min-h-[100px] text-sm"
            />
            <button
              onClick={() => setEditingInstructions(false)}
              className="mt-2 rounded bg-[#4F46E5] px-3 py-1 text-xs text-white"
            >
              Done
            </button>
          </div>
        ) : (
          <pre className="whitespace-pre-wrap text-sm text-[#1E293B]">
            {instructions}
          </pre>
        )}
      </div>

      {/* Sections */}
      {sections.map((section, idx) => (
          <SectionBlock
            key={section.sectionName}
            section={section}
            startingNumber={sectionStartNumbers[idx]}
            showAnswerKey={showAnswerKey}
            onRegenerate={onRegenerate}
            onEdit={onEdit}
            onDelete={onDelete}
          />
      ))}

      {/* Footer */}
      <div className="mt-8 border-t-2 border-gray-800 pt-2 text-center text-sm text-[#64748B]">
        *** End of Question Paper ***
      </div>
    </div>
  );
}
