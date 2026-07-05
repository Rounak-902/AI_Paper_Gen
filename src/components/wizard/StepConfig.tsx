import { useWizard } from '@/context/WizardContext';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';

const DURATIONS = ['1hr', '1.5hr', '2hr', '2.5hr', '3hr', '3.5hr', 'Custom'];

export default function StepConfig() {
  const { paperTitle, totalMarks, duration, difficulty, setPaperConfig } = useWizard();

  return (
    <div>
      <h2 className="mb-2 text-2xl font-bold text-[#1E293B]">Paper Configuration</h2>
      <p className="mb-6 text-sm text-[#64748B]">Set up the basic details for your question paper</p>

      <div className="mx-auto max-w-2xl space-y-6">
        {/* Paper Title */}
        <div className="space-y-2">
          <Label htmlFor="paperTitle">Paper Title</Label>
          <Input
            id="paperTitle"
            value={paperTitle}
            onChange={(e) => setPaperConfig({ paperTitle: e.target.value })}
            placeholder="e.g., Mathematics Question Paper – Class 10 – CBSE"
            className="h-11"
          />
        </div>

        {/* Total Marks & Duration row */}
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="totalMarks">Total Marks *</Label>
            <Input
              id="totalMarks"
              type="number"
              min={10}
              value={totalMarks}
              onChange={(e) =>
                setPaperConfig({ totalMarks: parseInt(e.target.value) || 0 })
              }
              className="h-11"
            />
          </div>

          <div className="space-y-2">
            <Label>Duration</Label>
            <Select
              value={duration}
              onValueChange={(val) => val && setPaperConfig({ duration: val })}
            >
              <SelectTrigger className="h-11">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {DURATIONS.map((d) => (
                  <SelectItem key={d} value={d}>
                    {d}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        {/* Difficulty */}
        <div className="space-y-3">
          <Label>Difficulty Level</Label>
          <div className="flex flex-wrap gap-3">
            {['Easy', 'Medium', 'Hard', 'Mixed'].map((level) => (
              <button
                key={level}
                onClick={() => setPaperConfig({ difficulty: level })}
                className={`rounded-lg border-2 px-5 py-2.5 text-sm font-medium transition-all ${
                  difficulty === level
                    ? 'border-[#4F46E5] bg-indigo-50 text-[#4F46E5]'
                    : 'border-gray-200 text-[#64748B] hover:border-gray-300'
                }`}
              >
                {level === 'Easy' && '🟢 '}
                {level === 'Medium' && '🟡 '}
                {level === 'Hard' && '🔴 '}
                {level === 'Mixed' && '🔀 '}
                {level}
              </button>
            ))}
          </div>
        </div>

        {/* Summary card */}
        <div className="rounded-xl bg-gradient-to-r from-indigo-50 to-blue-50 p-5">
          <h4 className="mb-2 text-sm font-semibold text-[#4F46E5]">Paper Summary</h4>
          <div className="grid grid-cols-3 gap-4 text-sm">
            <div>
              <span className="text-[#64748B]">Total Marks</span>
              <p className="font-semibold text-[#1E293B]">{totalMarks}</p>
            </div>
            <div>
              <span className="text-[#64748B]">Duration</span>
              <p className="font-semibold text-[#1E293B]">{duration}</p>
            </div>
            <div>
              <span className="text-[#64748B]">Difficulty</span>
              <p className="font-semibold text-[#1E293B]">{difficulty}</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
