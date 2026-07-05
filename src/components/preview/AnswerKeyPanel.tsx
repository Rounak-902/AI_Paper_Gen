import MathRenderer from '@/components/shared/MathRenderer';

interface Props {
  answerText: string | null;
  solutionSteps: string[] | null;
  correctOption: string | null;
  blankAnswer: string | null;
  matchAnswer: Record<string, string> | null;
  visible: boolean;
}

export default function AnswerKeyPanel({
  answerText,
  solutionSteps,
  correctOption,
  blankAnswer,
  matchAnswer,
  visible,
}: Props) {
  if (!visible) return null;

  return (
    <div
      data-answer-key
      className="mt-2 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3"
    >
      <p className="mb-1 text-xs font-semibold uppercase text-[#10B981]">
        Answer Key
      </p>

      {correctOption && (
        <p className="text-sm text-[#1E293B]">
          <strong>Correct:</strong> <MathRenderer content={correctOption} inline />
        </p>
      )}

      {blankAnswer && (
        <p className="text-sm text-[#1E293B]">
          <strong>Answer:</strong> <MathRenderer content={blankAnswer} inline />
        </p>
      )}

      {matchAnswer && Object.keys(matchAnswer).length > 0 && (
        <div className="text-sm text-[#1E293B]">
          <strong>Matches:</strong>
          <ul className="ml-4 list-disc">
            {Object.entries(matchAnswer).map(([left, right]) => (
              <li key={left}>
                <MathRenderer content={left} inline /> → <MathRenderer content={right} inline />
              </li>
            ))}
          </ul>
        </div>
      )}

      {answerText && (
        <p className="mt-1 text-sm text-[#1E293B] whitespace-pre-wrap">
          <MathRenderer content={answerText} />
        </p>
      )}

      {solutionSteps && solutionSteps.length > 0 && (
        <div className="mt-2">
          <p className="text-xs font-semibold text-[#10B981]">Solution Steps:</p>
          <ol className="ml-4 list-decimal text-sm text-[#1E293B]">
            {solutionSteps.map((step, i) => (
              <li key={i} className="mt-0.5">
                <MathRenderer content={step} />
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}
