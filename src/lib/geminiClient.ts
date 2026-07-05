const GEMINI_API_KEY = import.meta.env.VITE_GEMINI_API_KEY as string;
const GEMINI_ENDPOINT =
  'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent';

if (!GEMINI_API_KEY) {
  throw new Error('Missing VITE_GEMINI_API_KEY environment variable');
}

/** Sleep helper */
function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Calls the Gemini API with automatic retry + exponential backoff
 * for rate-limit (429) and server (500/503) errors.
 */
export async function callGemini(
  prompt: string,
  maxRetries = 4,
): Promise<string> {
  const url = `${GEMINI_ENDPOINT}?key=${GEMINI_API_KEY}`;

  const body = {
    contents: [{ parts: [{ text: prompt }] }],
    generationConfig: {
      temperature: 1.0,
      topP: 0.95,
      topK: 64,
      maxOutputTokens: 65536,
    },
  };

  let lastError: Error | null = null;

  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try {
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });

      // Rate-limited or server error → retry with backoff
      if (res.status === 429 || res.status === 500 || res.status === 503) {
        const retryAfter = res.headers.get('retry-after');
        const waitMs = retryAfter
          ? parseInt(retryAfter) * 1000
          : Math.min(2000 * Math.pow(2, attempt), 30000); // 2s, 4s, 8s, 16s, 30s

        console.warn(
          `Gemini API ${res.status} — retrying in ${waitMs / 1000}s (attempt ${attempt + 1}/${maxRetries + 1})`,
        );
        await sleep(waitMs);
        continue;
      }

      if (!res.ok) {
        const errBody = await res.text();
        throw new Error(`Gemini API error ${res.status}: ${errBody}`);
      }

      const data = await res.json();

      const text: string | undefined =
        data?.candidates?.[0]?.content?.parts?.[0]?.text;

      if (!text) {
        throw new Error('Empty response from Gemini API');
      }

      return text;
    } catch (err) {
      lastError = err instanceof Error ? err : new Error(String(err));

      // Don't retry on non-retryable errors (like network or parse errors)
      if (
        lastError.message.includes('Gemini API error 4') &&
        !lastError.message.includes('429')
      ) {
        throw lastError;
      }

      // For network errors, retry with backoff
      if (attempt < maxRetries) {
        const waitMs = Math.min(2000 * Math.pow(2, attempt), 30000);
        console.warn(
          `Gemini call failed — retrying in ${waitMs / 1000}s (attempt ${attempt + 1}/${maxRetries + 1})`,
        );
        await sleep(waitMs);
      }
    }
  }

  throw lastError ?? new Error('Gemini API failed after all retries');
}

/**
 * Strips markdown fences and parses JSON from Gemini's response.
 * Handles ```json ... ``` wrapping and extra text around the JSON.
 */
export function parseGeminiJSON<T>(raw: string): T {
  let cleaned = raw.trim();

  // Strip markdown code fences
  if (cleaned.startsWith('```')) {
    cleaned = cleaned.replace(/^```(?:json)?\s*\n?/, '').replace(/\n?```\s*$/, '');
  }

  // Try direct parse first
  try {
    return JSON.parse(cleaned) as T;
  } catch {
    // If direct parse fails, try to extract JSON array or object from the response
    // This handles cases where Gemini wraps JSON with explanatory text
    const arrayMatch = cleaned.match(/\[[\s\S]*\]/);
    if (arrayMatch) {
      try {
        return JSON.parse(arrayMatch[0]) as T;
      } catch { /* fall through */ }
    }

    const objectMatch = cleaned.match(/\{[\s\S]*\}/);
    if (objectMatch) {
      try {
        return JSON.parse(objectMatch[0]) as T;
      } catch { /* fall through */ }
    }

    // Nothing worked — throw with the original text for debugging
    throw new Error(`Failed to parse JSON from Gemini response: ${cleaned.slice(0, 200)}`);
  }
}

/**
 * Calls the Gemini API with inline media (e.g. base64-encoded PDF) + text prompt.
 * Uses the same retry + exponential backoff strategy as callGemini().
 */
export async function callGeminiWithMedia(
  prompt: string,
  mediaBase64: string,
  mediaType: string,
  maxRetries = 4,
): Promise<string> {
  const url = `${GEMINI_ENDPOINT}?key=${GEMINI_API_KEY}`;

  const body = {
    contents: [
      {
        parts: [
          {
            inline_data: {
              mime_type: mediaType,
              data: mediaBase64,
            },
          },
          { text: prompt },
        ],
      },
    ],
    generationConfig: {
      temperature: 0.2,
      topP: 0.95,
      topK: 64,
      maxOutputTokens: 65536,
    },
  };

  let lastError: Error | null = null;

  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try {
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });

      // Rate-limited or server error → retry with backoff
      if (res.status === 429 || res.status === 500 || res.status === 503) {
        const retryAfter = res.headers.get('retry-after');
        const waitMs = retryAfter
          ? parseInt(retryAfter) * 1000
          : Math.min(2000 * Math.pow(2, attempt), 30000);

        console.warn(
          `Gemini API ${res.status} — retrying in ${waitMs / 1000}s (attempt ${attempt + 1}/${maxRetries + 1})`,
        );
        await sleep(waitMs);
        continue;
      }

      if (!res.ok) {
        const errBody = await res.text();
        throw new Error(`Gemini API error ${res.status}: ${errBody}`);
      }

      const data = await res.json();

      const text: string | undefined =
        data?.candidates?.[0]?.content?.parts?.[0]?.text;

      if (!text) {
        throw new Error('Empty response from Gemini API');
      }

      return text;
    } catch (err) {
      lastError = err instanceof Error ? err : new Error(String(err));

      // Don't retry on non-retryable errors
      if (
        lastError.message.includes('Gemini API error 4') &&
        !lastError.message.includes('429')
      ) {
        throw lastError;
      }

      if (attempt < maxRetries) {
        const waitMs = Math.min(2000 * Math.pow(2, attempt), 30000);
        console.warn(
          `Gemini media call failed — retrying in ${waitMs / 1000}s (attempt ${attempt + 1}/${maxRetries + 1})`,
        );
        await sleep(waitMs);
      }
    }
  }

  throw lastError ?? new Error('Gemini API failed after all retries');
}
