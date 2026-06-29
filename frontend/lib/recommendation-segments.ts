import type { InsuranceRecommendationData } from '../components/InsuranceCard';

export type MessageSegment =
  | { type: 'markdown'; text: string }
  | { type: 'card'; data: InsuranceRecommendationData }
  | { type: 'card-loading' };

const REC_TOKEN = '"insurance_recommendation"';

// Restore smart/full-width punctuation the LLM sometimes emits in structural
// positions so JSON.parse succeeds. All replacements are 1-char → 1-char, so
// indices on the normalized string map back onto the original string.
function normalizeJsonPunctuation(input: string): string {
  return input
    .replace(/[“”„‟″‶]/g, '"')
    .replace(/[‘’‚‛′‵]/g, "'")
    .replace(/，/g, ',')
    .replace(/：/g, ':')
    .replace(/｛/g, '{')
    .replace(/｝/g, '}')
    .replace(/［/g, '[')
    .replace(/］/g, ']');
}

// Returns the index of the '}' that closes the object opening at `start`,
// respecting string literals and escapes. -1 if the object never closes.
function findJsonObjectEnd(text: string, start: number): number {
  let depth = 0;
  let inStr = false;
  let escape = false;
  for (let i = start; i < text.length; i++) {
    const ch = text[i];
    if (inStr) {
      if (escape) escape = false;
      else if (ch === '\\') escape = true;
      else if (ch === '"') inStr = false;
      continue;
    }
    if (ch === '"') inStr = true;
    else if (ch === '{') depth += 1;
    else if (ch === '}') {
      depth -= 1;
      if (depth === 0) return i;
    }
  }
  return -1;
}

const FENCE_BEFORE = /```(?:json)?[ \t]*\r?\n?[ \t]*$/;
const FENCE_AFTER = /^[ \t]*\r?\n?[ \t]*```/;

/**
 * Split an agent message into ordered segments of markdown and insurance
 * recommendation cards. Handles three shapes the agent produces:
 *   1) ```json fenced blocks   2) ``` fenced blocks (no lang)   3) bare JSON
 * Non-recommendation JSON and prose are left as markdown, in original order.
 */
export function splitRecommendationSegments(text: string): MessageSegment[] {
  if (!text) return [];

  const normalized = normalizeJsonPunctuation(text);
  const segments: MessageSegment[] = [];
  let cursor = 0; // consumed up to here (index shared by text & normalized)
  let searchIdx = 0;

  while (true) {
    const tokenIdx = normalized.indexOf(REC_TOKEN, searchIdx);
    if (tokenIdx === -1) break;

    // Walk back to the object's opening brace.
    let start = -1;
    for (let i = tokenIdx; i >= cursor; i--) {
      if (normalized[i] === '{') {
        start = i;
        break;
      }
    }
    if (start === -1) {
      searchIdx = tokenIdx + REC_TOKEN.length;
      continue;
    }

    const end = findJsonObjectEnd(normalized, start);
    if (end === -1) {
      // 推薦 JSON 未閉合（串流中或被截斷）：隱藏不完整 JSON，改放卡片佔位。
      let spanStart = start;
      const fenceBefore = text.slice(cursor, spanStart).match(FENCE_BEFORE);
      if (fenceBefore) spanStart -= fenceBefore[0].length;
      const gap = text.slice(cursor, spanStart).trim();
      if (gap) segments.push({ type: 'markdown', text: gap });
      segments.push({ type: 'card-loading' });
      cursor = text.length;
      break;
    }

    let parsed: InsuranceRecommendationData | null = null;
    try {
      parsed = JSON.parse(normalized.slice(start, end + 1));
    } catch {
      parsed = null;
    }
    if (!parsed || parsed.type !== 'insurance_recommendation') {
      searchIdx = end + 1; // leave this object for markdown, keep scanning
      continue;
    }

    // Absorb any code fence wrapping the object so it doesn't render as an
    // empty/broken code block in the surrounding markdown.
    let spanStart = start;
    let spanEnd = end + 1;
    const fenceBefore = text.slice(cursor, spanStart).match(FENCE_BEFORE);
    if (fenceBefore) spanStart -= fenceBefore[0].length;
    const fenceAfter = text.slice(spanEnd).match(FENCE_AFTER);
    if (fenceAfter) spanEnd += fenceAfter[0].length;

    const gap = text.slice(cursor, spanStart).trim();
    if (gap) segments.push({ type: 'markdown', text: gap });
    segments.push({ type: 'card', data: parsed });

    cursor = spanEnd;
    searchIdx = spanEnd;
  }

  const tail = text.slice(cursor).trim();
  if (tail) segments.push({ type: 'markdown', text: tail });

  return segments;
}
