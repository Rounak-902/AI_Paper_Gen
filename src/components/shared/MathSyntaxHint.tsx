import { useState, useEffect } from 'react';
import { Info, ChevronDown, ChevronUp } from 'lucide-react';
import MathRenderer from './MathRenderer';

const STORAGE_KEY = 'papergen-math-hint-dismissed';

const EXAMPLES = [
  { input: '$x^2$', label: 'Superscript' },
  { input: '$\\frac{d}{dx}$', label: 'Fraction' },
  { input: '$\\sqrt{x+1}$', label: 'Square root' },
  { input: '$\\sin(x)$', label: 'Trig function' },
  { input: '$x_1$', label: 'Subscript' },
  { input: '$\\int_0^1 x^2 \\, dx$', label: 'Integral' },
  { input: '$\\pi, \\theta$', label: 'Greek letters' },
];

export default function MathSyntaxHint() {
  const [dismissed, setDismissed] = useState(true);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (!stored) {
      setDismissed(false);
    }
  }, []);

  const handleDismiss = () => {
    setDismissed(true);
    localStorage.setItem(STORAGE_KEY, 'true');
  };

  // Always show the small link even when dismissed
  if (dismissed) {
    return (
      <button
        onClick={() => {
          setDismissed(false);
          setExpanded(true);
        }}
        className="mb-1 flex items-center gap-1 text-[10px] text-[#4F46E5] hover:text-indigo-700 transition-colors"
      >
        <Info className="h-3 w-3" />
        Math syntax help
      </button>
    );
  }

  return (
    <div className="mb-2 rounded-lg border border-indigo-100 bg-indigo-50/60 px-3 py-2">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5 text-xs text-indigo-700">
          <Info className="h-3.5 w-3.5 flex-shrink-0" />
          <span>Wrap math in <code className="rounded bg-indigo-100 px-1 py-0.5 text-[10px] font-mono">$...$</code> so it renders like a textbook</span>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={() => setExpanded(!expanded)}
            className="flex items-center gap-0.5 rounded px-1.5 py-0.5 text-[10px] font-medium text-indigo-600 hover:bg-indigo-100 transition-colors"
          >
            {expanded ? 'Hide' : 'Show examples'}
            {expanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
          </button>
          <button
            onClick={handleDismiss}
            className="rounded px-1.5 py-0.5 text-[10px] text-indigo-400 hover:text-indigo-600 hover:bg-indigo-100 transition-colors"
          >
            ✕
          </button>
        </div>
      </div>

      {expanded && (
        <div className="mt-2 overflow-hidden rounded border border-indigo-100 bg-white">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b bg-gray-50">
                <th className="px-3 py-1.5 text-left font-medium text-gray-500">Type this</th>
                <th className="px-3 py-1.5 text-left font-medium text-gray-500">Renders as</th>
              </tr>
            </thead>
            <tbody>
              {EXAMPLES.map((ex, i) => (
                <tr key={i} className="border-b last:border-0">
                  <td className="px-3 py-1.5">
                    <code className="rounded bg-gray-50 px-1 py-0.5 font-mono text-[10px] text-gray-700">{ex.input}</code>
                  </td>
                  <td className="px-3 py-1.5">
                    <MathRenderer content={ex.input} inline />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
