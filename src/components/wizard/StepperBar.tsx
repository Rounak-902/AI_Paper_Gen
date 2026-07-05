import { useWizard } from '@/context/WizardContext';
import { Check } from 'lucide-react';

const STEP_LABELS: Record<string, string> = {
  board: 'Board',
  class: 'Class',
  stream: 'Stream',
  subject: 'Subject',
  config: 'Configure',
  chapters: 'Chapters',
  pattern: 'Pattern',
  review: 'Review',
};

export default function StepperBar() {
  const { currentStep, steps } = useWizard();

  return (
    <div className="mb-8">
      <div className="flex items-center justify-between">
        {steps.map((stepName, index) => {
          const isActive = index === currentStep;
          const isCompleted = index < currentStep;
          const isLast = index === steps.length - 1;

          return (
            <div key={stepName} className="flex flex-1 items-center">
              <div className="flex flex-col items-center">
                <div
                  className={`flex h-10 w-10 items-center justify-center rounded-full text-sm font-semibold transition-all duration-300 ${
                    isCompleted
                      ? 'bg-[#10B981] text-white shadow-md shadow-emerald-200'
                      : isActive
                        ? 'bg-[#4F46E5] text-white shadow-lg shadow-indigo-200 ring-4 ring-indigo-100'
                        : 'bg-gray-100 text-[#64748B]'
                  }`}
                >
                  {isCompleted ? (
                    <Check className="h-5 w-5" />
                  ) : (
                    index + 1
                  )}
                </div>
                <span
                  className={`mt-2 text-xs font-medium ${
                    isActive
                      ? 'text-[#4F46E5]'
                      : isCompleted
                        ? 'text-[#10B981]'
                        : 'text-[#64748B]'
                  }`}
                >
                  {STEP_LABELS[stepName] ?? stepName}
                </span>
              </div>

              {!isLast && (
                <div className="mx-2 mb-6 h-0.5 flex-1">
                  <div
                    className={`h-full rounded-full transition-all duration-500 ${
                      isCompleted ? 'bg-[#10B981]' : 'bg-gray-200'
                    }`}
                  />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
