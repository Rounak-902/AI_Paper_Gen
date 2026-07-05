import { useState, useRef, useEffect } from 'react';
import { FunctionSquare } from 'lucide-react';

interface InsertMathPopoverProps {
  textareaRef: React.RefObject<HTMLTextAreaElement | null>;
  onInsert: (snippet: string) => void;
}

const SYMBOLS = [
  { label: 'x²', snippet: '$x^2$', title: 'Superscript' },
  { label: '√', snippet: '$\\sqrt{}$', title: 'Square root' },
  { label: 'd/dx', snippet: '$\\frac{d}{dx}$', title: 'Derivative' },
  { label: 'a/b', snippet: '$\\frac{}{}$', title: 'Fraction' },
  { label: '∑', snippet: '$\\sum_{i=1}^{n}$', title: 'Summation' },
  { label: '∫', snippet: '$\\int_{}^{}$', title: 'Integral' },
  { label: 'π', snippet: '$\\pi$', title: 'Pi' },
  { label: 'θ', snippet: '$\\theta$', title: 'Theta' },
  { label: '≤', snippet: '$\\leq$', title: 'Less than or equal' },
  { label: '≥', snippet: '$\\geq$', title: 'Greater than or equal' },
  { label: '≠', snippet: '$\\neq$', title: 'Not equal' },
  { label: '∞', snippet: '$\\infty$', title: 'Infinity' },
  { label: 'sin', snippet: '$\\sin()$', title: 'Sine' },
  { label: 'cos', snippet: '$\\cos()$', title: 'Cosine' },
  { label: 'log', snippet: '$\\log()$', title: 'Logarithm' },
  { label: 'lim', snippet: '$\\lim_{x \\to }$', title: 'Limit' },
  { label: 'α', snippet: '$\\alpha$', title: 'Alpha' },
  { label: 'β', snippet: '$\\beta$', title: 'Beta' },
  { label: 'x₁', snippet: '$x_1$', title: 'Subscript' },
  { label: '→', snippet: '$\\vec{}$', title: 'Vector' },
];

export default function InsertMathPopover({
  onInsert,
}: InsertMathPopoverProps) {
  const [open, setOpen] = useState(false);
  const popoverRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (popoverRef.current && !popoverRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    if (open) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [open]);

  return (
    <div className="relative inline-block" ref={popoverRef}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1 rounded-md border border-gray-200 bg-white px-2 py-1 text-[10px] font-medium text-[#4F46E5] shadow-sm hover:bg-indigo-50 hover:border-indigo-200 transition-colors"
        title="Insert math symbol"
      >
        <FunctionSquare className="h-3 w-3" />
        Insert Math
      </button>

      {open && (
        <div className="absolute right-0 top-full z-50 mt-1 w-64 rounded-lg border border-gray-200 bg-white p-2 shadow-lg">
          <p className="mb-1.5 text-[10px] font-medium text-gray-400 uppercase">
            Click to insert at cursor
          </p>
          <div className="grid grid-cols-5 gap-1">
            {SYMBOLS.map((sym) => (
              <button
                key={sym.title}
                type="button"
                onClick={() => {
                  onInsert(sym.snippet);
                  setOpen(false);
                }}
                className="flex h-8 items-center justify-center rounded-md border border-gray-100 bg-gray-50 text-xs font-medium text-gray-700 hover:bg-indigo-50 hover:border-indigo-200 hover:text-indigo-700 transition-colors"
                title={sym.title}
              >
                {sym.label}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
