/* MANDATORY: This component must be used for ALL question-related
   text rendering anywhere in this app, including future screens.
   Never render question_text, answer_text, options, or solution_steps
   as raw {value} — always wrap with <MathRenderer content={value} />.

   See README.md "Math Rendering" section for the full rule. */

import { useMemo } from 'react';
import katex from 'katex';

interface MathRendererProps {
  content: string;
  className?: string;
  /** If true, never renders display math (block) even for $$ — used for compact contexts like table cells */
  inline?: boolean;
}

/** Regex to split text into plain text and math segments.
 *  Handles both $$...$$ (display) and $...$ (inline).
 *  $$ is checked first since $ is a substring of $$. */
const MATH_REGEX = /(\$\$[\s\S]+?\$\$|\$[^\$]+?\$)/g;

interface Segment {
  type: 'text' | 'inline-math' | 'block-math';
  content: string;
}

function parseContent(input: string): Segment[] {
  const segments: Segment[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  // Reset regex state
  MATH_REGEX.lastIndex = 0;

  while ((match = MATH_REGEX.exec(input)) !== null) {
    // Add plain text before this match
    if (match.index > lastIndex) {
      segments.push({
        type: 'text',
        content: input.slice(lastIndex, match.index),
      });
    }

    const raw = match[0];
    if (raw.startsWith('$$')) {
      segments.push({
        type: 'block-math',
        content: raw.slice(2, -2).trim(),
      });
    } else {
      segments.push({
        type: 'inline-math',
        content: raw.slice(1, -1).trim(),
      });
    }

    lastIndex = match.index + raw.length;
  }

  // Remaining text after last match
  if (lastIndex < input.length) {
    segments.push({
      type: 'text',
      content: input.slice(lastIndex),
    });
  }

  return segments;
}

function renderKatex(latex: string, displayMode: boolean): string | null {
  try {
    return katex.renderToString(latex, {
      displayMode,
      throwOnError: false,
      strict: false,
      trust: true,
      output: 'html',
    });
  } catch {
    return null;
  }
}

export default function MathRenderer({
  content,
  className,
  inline = false,
}: MathRendererProps) {
  const rendered = useMemo(() => {
    if (!content) return null;

    const segments = parseContent(content);

    // Fast path: no math delimiters at all — render as plain text
    if (segments.length === 1 && segments[0].type === 'text') {
      return null; // signal to render plain
    }

    return segments.map((seg, i) => {
      if (seg.type === 'text') {
        return (
          <span key={i}>{seg.content}</span>
        );
      }

      const isDisplay = seg.type === 'block-math' && !inline;
      const html = renderKatex(seg.content, isDisplay);

      if (html === null) {
        // Fallback: show raw text with a subtle indicator
        return (
          <span
            key={i}
            className="inline-flex items-center gap-0.5"
            title="Formatting issue — showing raw text"
          >
            <span className="text-gray-400 text-[10px]">*</span>
            <span>{seg.content}</span>
          </span>
        );
      }

      if (isDisplay) {
        return (
          <div
            key={i}
            className="my-2"
            dangerouslySetInnerHTML={{ __html: html }}
          />
        );
      }

      return (
        <span
          key={i}
          dangerouslySetInnerHTML={{ __html: html }}
        />
      );
    });
  }, [content, inline]);

  if (!content) return null;

  // Fast path: plain text with no math
  if (rendered === null) {
    return <span className={className}>{content}</span>;
  }

  return (
    <span className={className}>
      {rendered}
    </span>
  );
}
