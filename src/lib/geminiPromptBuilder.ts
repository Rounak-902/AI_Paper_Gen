import type { SectionPromptConfig, SectionConfig } from '../types/database';

export interface FullPaperPromptConfig {
  boardName: string;
  className: string;
  streamName: string | null;
  subjectName: string;
  chapterNames: string[];
  difficulty: string;
  sections: SectionConfig[];
  existingQuestions: string[];
  isMaths: boolean;
}

export function buildFullPaperPrompt(config: FullPaperPromptConfig): string {
  const {
    boardName,
    className,
    streamName,
    subjectName,
    chapterNames,
    difficulty,
    sections,
    existingQuestions,
    isMaths,
  } = config;

  const seed = crypto.randomUUID();

  let prompt = `You are an expert ${boardName} board examiner for Class ${className}${streamName ? ` (${streamName} stream)` : ''}.

Subject: ${subjectName}
Chapters to cover: ${chapterNames.join(', ')}
Difficulty: ${difficulty}
Seed (ignore): ${seed}

Generate a COMPLETE question paper with the following sections:
`;

  sections.forEach((s, i) => {
    prompt += `
${i + 1}. "${s.sectionName}" — ${s.numQuestions} questions of type "${s.questionType}", ${s.marksPerQuestion} mark${s.marksPerQuestion > 1 ? 's' : ''} each`;
  });

  if (existingQuestions.length > 0) {
    prompt += `

PREVIOUSLY GENERATED — DO NOT REPEAT OR CLOSELY PARAPHRASE:
${existingQuestions.slice(0, 8).map((q, i) => `${i + 1}. ${q}`).join('\n')}`;
  }

  prompt += `

MATHEMATICAL NOTATION RULES (MANDATORY):
- Write ALL math using LaTeX in $...$ for inline, $$...$$ for display.
- Examples: $(3x-2)^5$ NOT (3x-2)^5, $\\frac{d}{dx}$ NOT d/dx, $\\sin(x)$ NOT sin(x), $\\sqrt{x}$ NOT sqrt(x).
- Apply to question_text, answer_text, answer_hint, solution_steps, options.
- Plain English stays as normal text.

STRICT RULES:`;

  if (isMaths) {
    prompt += `
- ALL questions MUST be purely numerical/calculation-based.
- NO theory, NO definitions, NO descriptive answers.`;
  }

  // Add type-specific rules for each unique question type
  const uniqueTypes = [...new Set(sections.map((s) => s.questionType))];
  for (const qt of uniqueTypes) {
    if (qt === 'MCQ') {
      prompt += `
- MCQ: exactly 4 options A-D, one correct, include correct_option field.`;
    }
    if (qt === 'True/False') {
      prompt += `
- True/False: options ["True","False"], include correct_option.`;
    }
    if (qt === 'Assertion-Reason') {
      prompt += `
- Assertion-Reason: include assertion and reason fields.`;
    }
    if (qt === 'Match the Following') {
      prompt += `
- Match: include match_left, match_right arrays and match_answer object.`;
    }
    if (qt === 'Numerical') {
      prompt += `
- Numerical: include solution_steps array with step-by-step working.`;
    }
  }

  prompt += `
- Distribute across all provided chapters evenly.
- Vary wording, sentence structure, and angle.
- Follow ${boardName} Class ${className} syllabus strictly.
- Do NOT number the questions.

Return ONLY a valid raw JSON object (no markdown, no backticks, no explanation) with this EXACT structure — keys are section names:

{`;

  sections.forEach((s, i) => {
    prompt += `
  "${s.sectionName}": [
    {
      "question_text": "...",
      "answer_text": "...",
      "answer_hint": "...",
      "question_type": "${s.questionType}",
      "marks": ${s.marksPerQuestion},
      "difficulty": "easy|medium|hard",
      "chapter_name": "...",
      "options": ["A","B","C","D"] or null,
      "correct_option": "..." or null,
      "blank_answer": "..." or null,
      "match_left": [...] or null,
      "match_right": [...] or null,
      "match_answer": {...} or null,
      "assertion": "..." or null,
      "reason": "..." or null,
      "solution_steps": [...] or null
    }
  ]${i < sections.length - 1 ? ',' : ''}`;
  });

  prompt += `
}

Generate EXACTLY ${sections.map((s) => `${s.numQuestions} questions for "${s.sectionName}"`).join(', ')}.`;

  return prompt;
}

export function buildSectionPrompt(config: SectionPromptConfig): string {
  const {
    boardName,
    className,
    streamName,
    subjectName,
    chapterNames,
    sectionName,
    questionType,
    numQuestions,
    marksPerQuestion,
    difficulty,
    existingQuestions,
    isMaths,
  } = config;

  const seed = crypto.randomUUID();

  let prompt = `You are an expert ${boardName} board examiner for Class ${className}${streamName ? ` (${streamName} stream)` : ''}.

Subject: ${subjectName}
Chapters to cover: ${chapterNames.join(', ')}
Section: ${sectionName}
Question Type: ${questionType}
Number of Questions: ${numQuestions}
Marks Per Question: ${marksPerQuestion}
Difficulty: ${difficulty}
Seed (ignore): ${seed}
`;

  if (existingQuestions.length > 0) {
    prompt += `
PREVIOUSLY GENERATED — DO NOT REPEAT OR CLOSELY PARAPHRASE:
${existingQuestions.map((q, i) => `${i + 1}. ${q}`).join('\n')}
`;
  }

  prompt += `

MATHEMATICAL NOTATION RULES (MANDATORY):
- Write ALL mathematical expressions, equations, and formulas using LaTeX syntax wrapped in single dollar signs for inline math: $...$
- Use double dollar signs $$...$$ only for standalone display equations that should appear on their own centered line.
- Examples of REQUIRED formatting:
  • Power/exponent: $(3x - 2)^5$ NOT (3x - 2)^5
  • Nested exponent: $e^{x^2}$ NOT e^(x^2)
  • Fraction: $\\frac{d}{dx}$ NOT d/dx
  • Multiplication: $x \\cdot \\ln(x)$ NOT x * ln(x)
  • Square root: $\\sqrt{x+1}$ NOT sqrt(x+1)
  • Subscript: $x_1, x_2$ NOT x_1, x_2 as plain text
  • Trig functions: $\\sin(x)$, $\\cos(x)$, $\\tan(x)$ — always use backslash commands
  • Integral: $\\int_0^1 x^2 \\, dx$
  • Limits: $\\lim_{x \\to 0} \\frac{\\sin x}{x}$
  • Greek letters: $\\theta$, $\\pi$, $\\alpha$, $\\beta$
  • Vectors: $\\vec{a}$, $\\vec{b}$
  • Matrices: use \\begin{pmatrix}...\\end{pmatrix} inside $$...$$
- Apply this to question_text, answer_text, answer_hint, solution_steps, and options (for MCQ).
- Plain English sentences around the math stay as normal text — only wrap the actual mathematical notation in $ or $$.
- Example full question_text: "Find $\\frac{d}{dx}$ of $(3x - 2)^5$ with respect to $x$."
- For Numerical/Maths questions this is critical — every formula, equation, and expression must use correct LaTeX, zero plain-text math symbols allowed.

STRICT RULES:`;

  if (isMaths) {
    prompt += `
- ALL questions MUST be purely numerical/calculation-based.
- NO theory, NO definitions, NO descriptive answers.
- Every question requires actual mathematical computation.`;
  }

  if (marksPerQuestion === 1) {
    prompt += `
- Questions must be very concise — single line, MCQ or fill-blank style.`;
  } else if (marksPerQuestion === 2) {
    prompt += `
- Questions require brief 2-3 line answers or short working.`;
  } else if (marksPerQuestion >= 3 && marksPerQuestion <= 4) {
    prompt += `
- Questions require medium-length answers — 4-6 lines or moderate working.`;
  } else if (marksPerQuestion >= 5) {
    prompt += `
- Questions require detailed long-form answers — full explanation, all steps, complete solution.`;
  }

  if (questionType === 'MCQ') {
    prompt += `
- Each MCQ must have exactly 4 options: A, B, C, D.
- Only one correct. Distractors must be plausible.
- Include correct_option field with exact correct option text.`;
  }

  if (questionType === 'True/False') {
    prompt += `
- Question must be a clear factual statement.
- options: ["True", "False"]
- Include correct_option: "True" or "False"`;
  }

  if (questionType === 'Assertion-Reason') {
    prompt += `
- Include Assertion (A) and Reason (R) statements.
- question_text = "Assertion (A): ...\\nReason (R): ..."`;
  }

  if (questionType === 'Match the Following') {
    prompt += `
- Include match_left (array of terms) and match_right (array of definitions).
- Include match_answer object mapping each left to correct right.`;
  }

  if (questionType === 'Numerical') {
    prompt += `
- Include solution_steps array with step-by-step working.`;
  }

  prompt += `

- Generate exactly ${numQuestions} questions.
- Distribute across all provided chapters evenly.
- Vary wording, sentence structure, and angle every time.
- Follow ${boardName} Class ${className} syllabus strictly.
- Do NOT number the questions.

Return ONLY a valid raw JSON array. No markdown, no backticks, no explanation. Exact structure:

[
  {
    "question_text": "Full question text",
    "answer_text": "Complete answer or full solution",
    "answer_hint": "Short key points",
    "question_type": "${questionType}",
    "marks": ${marksPerQuestion},
    "difficulty": "easy|medium|hard",
    "chapter_name": "Chapter this belongs to",
    "options": ["A text", "B text", "C text", "D text"] or null,
    "correct_option": "correct option text" or null,
    "blank_answer": "answer" or null,
    "match_left": ["Term 1", "Term 2"] or null,
    "match_right": ["Def 1", "Def 2"] or null,
    "match_answer": {"Term 1": "Def 2"} or null,
    "assertion": "assertion text" or null,
    "reason": "reason text" or null,
    "solution_steps": ["Step 1...", "Step 2..."] or null
  }
]`;

  return prompt;
}

export function buildSingleQuestionPrompt(
  questionType: string,
  marks: number,
  subjectName: string,
  chapterName: string,
  className: string,
  boardName: string,
  currentQuestionText: string,
): string {
  return `Generate ONE completely different ${questionType} question worth ${marks} marks for ${subjectName}, Chapter: ${chapterName}, Class ${className} ${boardName}.
Seed: ${crypto.randomUUID()}
DO NOT generate anything similar to: ${currentQuestionText}

MATHEMATICAL NOTATION RULES (MANDATORY):
- Write ALL mathematical expressions using LaTeX syntax wrapped in $...$ for inline math and $$...$$ for display math.
- Examples: $(3x-2)^5$ NOT (3x-2)^5, $\\frac{d}{dx}$ NOT d/dx, $\\sin(x)$ NOT sin(x), $\\sqrt{x}$ NOT sqrt(x).
- Apply to question_text, answer_text, answer_hint, solution_steps, and options.
- Plain English stays as normal text — only wrap actual math in $ or $$.

Return ONLY a single JSON object (not array) with this exact structure:
{
  "question_text": "Full question text",
  "answer_text": "Complete answer or full solution",
  "answer_hint": "Short key points",
  "question_type": "${questionType}",
  "marks": ${marks},
  "difficulty": "easy|medium|hard",
  "chapter_name": "${chapterName}",
  "options": ["A text", "B text", "C text", "D text"] or null,
  "correct_option": "correct option text" or null,
  "blank_answer": "answer" or null,
  "match_left": ["Term 1", "Term 2"] or null,
  "match_right": ["Def 1", "Def 2"] or null,
  "match_answer": {"Term 1": "Def 2"} or null,
  "assertion": "assertion text" or null,
  "reason": "reason text" or null,
  "solution_steps": ["Step 1...", "Step 2..."] or null
}`;
}

export function buildTopicSuggestionPrompt(
  subjectName: string,
  chapterName: string,
  questionText: string,
  existingTopics: string[],
): string {
  return `Given this question from ${subjectName}, Chapter: ${chapterName}:
Question: ${questionText}

Existing topics in this chapter:
${existingTopics.length > 0 ? existingTopics.join(', ') : 'None yet'}

If any existing topic matches, return that exact topic name.
If not, suggest the most appropriate topic in 3-5 words.
Return ONLY the topic name. No explanation.`;
}
