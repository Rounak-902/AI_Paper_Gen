import { Label } from '@/components/ui/label';
import { Input } from '@/components/ui/input';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import type { Source } from '@/types/database';

const BOOK_CHIPS = ['NCERT', 'RD Sharma', 'HC Verma', 'Selina', 'S.L. Arora', 'DC Pandey'];

interface Props {
  source: Source;
  bookName: string;
  exerciseNo: string;
  questionNo: string;
  pageNo: number | null;
  onSourceChange: (source: Source) => void;
  onBookNameChange: (name: string) => void;
  onExerciseNoChange: (no: string) => void;
  onQuestionNoChange: (no: string) => void;
  onPageNoChange: (page: number | null) => void;
}

export default function SourceSection({
  source,
  bookName,
  exerciseNo,
  questionNo,
  pageNo,
  onSourceChange,
  onBookNameChange,
  onExerciseNoChange,
  onQuestionNoChange,
  onPageNoChange,
}: Props) {
  return (
    <div className="space-y-4">
      <h3 className="text-sm font-semibold uppercase text-[#64748B]">Source Info</h3>

      {/* Source type */}
      <div className="space-y-1.5">
        <Label>Source *</Label>
        <RadioGroup
          value={source === 'ai_generated' ? 'textbook' : source}
          onValueChange={(v) => onSourceChange(v as Source)}
          className="flex gap-4"
        >
          {(['textbook', 'manual', 'other'] as const).map((s) => (
            <div key={s} className="flex items-center gap-1">
              <RadioGroupItem value={s} id={`src-${s}`} />
              <Label htmlFor={`src-${s}`} className="text-xs capitalize">{s}</Label>
            </div>
          ))}
        </RadioGroup>
      </div>

      {/* Book name with chips */}
      <div className="space-y-1.5">
        <Label>Book Name</Label>
        <Input
          value={bookName}
          onChange={(e) => onBookNameChange(e.target.value)}
          placeholder="e.g., NCERT"
          className="h-9"
        />
        <div className="flex flex-wrap gap-1.5">
          {BOOK_CHIPS.map((chip) => (
            <button
              key={chip}
              onClick={() => onBookNameChange(chip)}
              className={`rounded-full px-2.5 py-1 text-xs transition-colors ${
                bookName === chip
                  ? 'bg-[#4F46E5] text-white'
                  : 'bg-gray-100 text-[#64748B] hover:bg-gray-200'
              }`}
            >
              {chip}
            </button>
          ))}
        </div>
      </div>

      {/* Exercise, Question, Page */}
      <div className="grid grid-cols-3 gap-3">
        <div className="space-y-1">
          <Label className="text-xs">Exercise No.</Label>
          <Input
            value={exerciseNo}
            onChange={(e) => onExerciseNoChange(e.target.value)}
            placeholder="Ex 3.1"
            className="h-8"
          />
        </div>
        <div className="space-y-1">
          <Label className="text-xs">Question No.</Label>
          <Input
            value={questionNo}
            onChange={(e) => onQuestionNoChange(e.target.value)}
            placeholder="Q.4"
            className="h-8"
          />
        </div>
        <div className="space-y-1">
          <Label className="text-xs">Page No.</Label>
          <Input
            type="number"
            value={pageNo ?? ''}
            onChange={(e) =>
              onPageNoChange(e.target.value ? parseInt(e.target.value) : null)
            }
            placeholder="42"
            className="h-8"
          />
        </div>
      </div>
    </div>
  );
}
