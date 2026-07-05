import { useState, useRef, useEffect } from 'react';
import { useWizard } from '@/context/WizardContext';
import { Input } from '@/components/ui/input';
import { Plus, Trash2, ArrowUp, ArrowDown, AlertTriangle, Calculator } from 'lucide-react';
import type { SectionConfig, QuestionType } from '@/types/database';

// Common question type suggestions (user can also type anything custom)
const SUGGESTED_TYPES = [
  'MCQ',
  'Short Answer',
  'Long Answer',
  'Very Short Answer',
  'One Word Answer',
  'Fill in the Blanks',
  'True/False',
  'Match the Following',
  'Assertion-Reason',
  'Case Study',
  'Numerical',
  'Diagram Based',
  'Distinguish Between',
  'Arrange in Chronological Order',
  'Give Reasons',
  'Answer in Brief',
  'Answer in Detail',
  'Correct the Statement',
  'Complete the Sentence',
  'Identify and Explain',
  'Name the Following',
  'State Whether True or False with Reason',
  'Map Based',
  'Observe the Diagram and Answer',
];

// Free-text input with autocomplete suggestions
function QuestionTypeInput({
  value,
  onChange,
}: {
  value: string;
  onChange: (val: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [highlightIndex, setHighlightIndex] = useState(-1);
  const containerRef = useRef<HTMLDivElement>(null);

  const filtered = value.trim()
    ? SUGGESTED_TYPES.filter((o) =>
        o.toLowerCase().includes(value.toLowerCase()),
      )
    : SUGGESTED_TYPES;

  // Close on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  useEffect(() => {
    setHighlightIndex(-1);
  }, [value]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (!open) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setHighlightIndex((i) => Math.min(i + 1, filtered.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setHighlightIndex((i) => Math.max(i - 1, -1));
    } else if (e.key === 'Enter' && highlightIndex >= 0 && filtered[highlightIndex]) {
      e.preventDefault();
      onChange(filtered[highlightIndex]);
      setOpen(false);
    } else if (e.key === 'Escape') {
      setOpen(false);
    }
  };

  return (
    <div ref={containerRef} className="relative">
      <input
        type="text"
        value={value}
        placeholder="e.g., Distinguish Between"
        onFocus={() => setOpen(true)}
        onChange={(e) => {
          onChange(e.target.value);
          if (!open) setOpen(true);
        }}
        onKeyDown={handleKeyDown}
        className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-xs transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
      />
      {open && filtered.length > 0 && (
        <div className="absolute left-0 right-0 top-full z-50 mt-1 max-h-48 overflow-auto rounded-md border border-gray-200 bg-white py-1 shadow-lg">
          {filtered.map((opt, i) => (
            <button
              key={opt}
              type="button"
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => {
                onChange(opt);
                setOpen(false);
              }}
              className={`flex w-full items-center px-3 py-1.5 text-left text-sm transition-colors ${
                i === highlightIndex
                  ? 'bg-indigo-50 text-[#4F46E5]'
                  : 'text-[#1E293B] hover:bg-gray-50'
              }`}
            >
              {opt}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default function StepPattern() {
  const { sections, setSections, subjectName, totalMarks } = useWizard();

  const isMaths = (subjectName ?? '').toLowerCase().includes('math');


  const addSection = () => {
    const defaultType = isMaths ? 'Numerical' : 'MCQ';
    const newSection: SectionConfig = {
      id: crypto.randomUUID(),
      sectionName: `Section ${String.fromCharCode(65 + sections.length)}`,
      questionType: defaultType as QuestionType,
      numQuestions: 5,
      marksPerQuestion: 1,
    };
    setSections([...sections, newSection]);
  };

  const updateSection = (id: string, updates: Partial<SectionConfig>) => {
    setSections(
      sections.map((s) => (s.id === id ? { ...s, ...updates } : s)),
    );
  };

  const removeSection = (id: string) => {
    setSections(sections.filter((s) => s.id !== id));
  };

  const moveSection = (index: number, direction: 'up' | 'down') => {
    const newSections = [...sections];
    const swapIndex = direction === 'up' ? index - 1 : index + 1;
    if (swapIndex < 0 || swapIndex >= newSections.length) return;
    [newSections[index], newSections[swapIndex]] = [
      newSections[swapIndex],
      newSections[index],
    ];
    setSections(newSections);
  };

  const sectionsTotal = sections.reduce(
    (sum, s) => sum + s.numQuestions * s.marksPerQuestion,
    0,
  );
  const mismatch = sections.length > 0 && sectionsTotal !== totalMarks;

  return (
    <div>
      <h2 className="mb-2 text-2xl font-bold text-[#1E293B]">Paper Pattern</h2>
      <p className="mb-6 text-sm text-[#64748B]">
        Build your paper structure by adding sections
      </p>

      {isMaths && (
        <div className="mb-6 flex items-center gap-3 rounded-xl border border-amber-200 bg-amber-50 px-5 py-3">
          <Calculator className="h-5 w-5 text-[#F59E0B]" />
          <span className="text-sm text-amber-800">
            Mathematics papers contain only numerical and calculation-based questions
          </span>
        </div>
      )}

      {/* Sections */}
      <div className="space-y-3">
        {sections.map((section, index) => (
          <div
            key={section.id}
            className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm"
          >
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
              {/* Section Name */}
              <div className="space-y-1">
                <label className="text-xs font-medium text-[#64748B]">
                  Section Name
                </label>
                <Input
                  value={section.sectionName}
                  onChange={(e) =>
                    updateSection(section.id, { sectionName: e.target.value })
                  }
                  placeholder="Section A"
                  className="h-9"
                />
              </div>

              {/* Question Type - Free text with suggestions */}
              <div className="space-y-1">
                <label className="text-xs font-medium text-[#64748B]">
                  Question Type
                </label>
                <QuestionTypeInput
                  value={section.questionType}
                  onChange={(val) =>
                    updateSection(section.id, {
                      questionType: val as QuestionType,
                    })
                  }
                />
              </div>

              {/* No. of Questions */}
              <div className="space-y-1">
                <label className="text-xs font-medium text-[#64748B]">
                  No. of Qs
                </label>
                <Input
                  type="number"
                  min={1}
                  value={section.numQuestions}
                  onChange={(e) =>
                    updateSection(section.id, {
                      numQuestions: parseInt(e.target.value) || 1,
                    })
                  }
                  className="h-9"
                />
              </div>

              {/* Marks per Q */}
              <div className="space-y-1">
                <label className="text-xs font-medium text-[#64748B]">
                  Marks/Q
                </label>
                <Input
                  type="number"
                  min={1}
                  value={section.marksPerQuestion}
                  onChange={(e) =>
                    updateSection(section.id, {
                      marksPerQuestion: parseInt(e.target.value) || 1,
                    })
                  }
                  className="h-9"
                />
              </div>

              {/* Section Total + Actions */}
              <div className="flex items-end gap-2">
                <div className="flex-1 space-y-1">
                  <label className="text-xs font-medium text-[#64748B]">
                    Total
                  </label>
                  <div className="flex h-9 items-center rounded-md bg-gray-50 px-3 text-sm font-semibold text-[#1E293B]">
                    {section.numQuestions * section.marksPerQuestion}
                  </div>
                </div>
                <div className="flex gap-1">
                  <button
                    onClick={() => moveSection(index, 'up')}
                    disabled={index === 0}
                    className="rounded p-1.5 text-[#64748B] hover:bg-gray-100 disabled:opacity-30"
                  >
                    <ArrowUp className="h-4 w-4" />
                  </button>
                  <button
                    onClick={() => moveSection(index, 'down')}
                    disabled={index === sections.length - 1}
                    className="rounded p-1.5 text-[#64748B] hover:bg-gray-100 disabled:opacity-30"
                  >
                    <ArrowDown className="h-4 w-4" />
                  </button>
                  <button
                    onClick={() => removeSection(section.id)}
                    className="rounded p-1.5 text-[#EF4444] hover:bg-red-50"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Add Section button */}
      <button
        onClick={addSection}
        className="mt-4 flex w-full items-center justify-center gap-2 rounded-xl border-2 border-dashed border-gray-300 py-4 text-sm font-medium text-[#64748B] transition-colors hover:border-[#4F46E5] hover:text-[#4F46E5]"
      >
        <Plus className="h-4 w-4" />
        Add Section
      </button>

      {/* Running total */}
      <div className="mt-6 flex items-center justify-between rounded-xl bg-gray-50 px-5 py-4">
        <div className="flex items-center gap-4">
          <span className="text-sm text-[#64748B]">Sections Total:</span>
          <span className="text-2xl font-bold text-[#1E293B]">{sectionsTotal}</span>
          <span className="text-sm text-[#64748B]">/ {totalMarks} marks</span>
        </div>
        {mismatch && (
          <div className="flex items-center gap-2 text-[#F59E0B]">
            <AlertTriangle className="h-4 w-4" />
            <span className="text-sm font-medium">
              Total doesn't match paper marks
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
