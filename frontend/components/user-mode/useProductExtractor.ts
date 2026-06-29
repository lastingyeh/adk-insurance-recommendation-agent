import { useCallback, useRef, useState } from 'react';
import { Product } from './types';

const PRODUCT_SEARCH_TOOLS = new Set([
  'search_medical_products',
  'search_accident_products',
  'search_family_protection_products',
  'search_income_protection_products',
  'search_products_by_name',
  'get_product_detail',
  'get_product_details',
  'get_product_by_name',
]);

function isProductLike(value: unknown): value is Product {
  if (!value || typeof value !== 'object') return false;
  return 'product_name' in (value as Record<string, unknown>);
}

function coerceProducts(raw: unknown, sourceTool: string): Product[] {
  const tag = (p: Product): Product => ({ ...p, source_tool: sourceTool });

  if (Array.isArray(raw)) {
    return raw.filter(isProductLike).map(tag);
  }
  if (raw && typeof raw === 'object') {
    const obj = raw as Record<string, unknown>;
    if (Array.isArray(obj.result)) {
      return obj.result.filter(isProductLike).map(tag);
    }
    if (Array.isArray(obj.products)) {
      return obj.products.filter(isProductLike).map(tag);
    }
    if (Array.isArray(obj.items)) {
      return obj.items.filter(isProductLike).map(tag);
    }
    if (isProductLike(obj)) {
      return [tag(obj as Product)];
    }
  }
  return [];
}

function parseSseToolResultPayload(payload: string[] | undefined): unknown {
  if (!payload || payload.length === 0) return null;
  const responseLine = payload.find((entry) =>
    entry.startsWith('response:'),
  );
  if (!responseLine) return null;
  const jsonText = responseLine.slice('response:'.length).trim();
  if (!jsonText || jsonText === '{}') return null;
  try {
    return JSON.parse(jsonText);
  } catch {
    return null;
  }
}

function dedupe(products: Product[]): Product[] {
  const seen = new Map<string, Product>();
  for (const product of products) {
    const key = String(product.product_id ?? product.product_name);
    if (!seen.has(key)) seen.set(key, product);
  }
  return Array.from(seen.values());
}

export interface ProductExtractorAPI {
  products: Product[];
  /** messageId → 該 message 抽出來的商品 (chat 顯示時用來判斷要不要簡寫) */
  productsByMessage: Record<string, Product[]>;
  reset: () => void;
  ingestSseTimelineEvent: (event: {
    kind?: string;
    title?: string;
    payload?: string[];
  }) => void;
  ingestAdkEvent: (event: {
    content?: { parts?: any[] };
  }) => void;
  ingestAgentMessage: (params: {
    id: string;
    text: string;
    status?: string;
  }) => void;
}

interface InsuranceRecommendationBlock {
  type?: string;
  productName?: string;
  reason?: string;
  budgetFit?: string;
  reminders?: string;
  terms?: string;
  rules?: string;
}

/**
 * 把 LLM 常見的「智慧引號 / 全形標點」還原成 ASCII，這樣 JSON.parse 才不會炸。
 *   " " → "   ' ' → '   ， → ,   ： → :   ｛｝ → {}
 */
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

/**
 * 從 agent 文字中找出所有 insurance_recommendation JSON 區塊。
 * 寬容處理：1) 帶 ```json fence  2) 帶 ``` fence (無 lang)  3) 裸 JSON 物件
 * 並會先 normalize 引號避免智慧引號讓 JSON.parse 失敗
 */
function extractRecommendationBlocks(
  text: string,
): InsuranceRecommendationBlock[] {
  if (!text) return [];
  const blocks: InsuranceRecommendationBlock[] = [];
  const seen = new Set<string>();

  const pushIfRecommendation = (raw: string) => {
    if (!raw.includes('insurance_recommendation')) return;
    const candidates = [raw.trim(), normalizeJsonPunctuation(raw).trim()];
    for (const candidate of candidates) {
      try {
        const parsed = JSON.parse(candidate);
        if (parsed && parsed.type === 'insurance_recommendation') {
          // 用 productName 當邏輯 id 去重（同名只算一張，避免同一段 JSON 被多次抽出）
          const key = (parsed.productName as string) || JSON.stringify(parsed);
          if (!seen.has(key)) {
            seen.add(key);
            blocks.push(parsed);
          }
          return;
        }
      } catch {
        // try next candidate
      }
    }
  };

  // 1) ```json fenced blocks (含完整 closing fence)
  for (const match of text.matchAll(/```(?:json)?\s*([\s\S]*?)```/g)) {
    pushIfRecommendation(match[1]);
  }

  // 2) 裸 JSON 掃描 — 只在 pass 1 沒抓到時才跑，避免 fence 內的 block 被重複抽
  if (blocks.length > 0) return blocks;

  const normalized = normalizeJsonPunctuation(text);
  let idx = 0;
  while ((idx = normalized.indexOf('"insurance_recommendation"', idx)) !== -1) {
    let start = -1;
    for (let i = idx; i >= 0; i--) {
      if (normalized[i] === '{') {
        start = i;
        break;
      }
    }
    if (start === -1) {
      idx += 1;
      continue;
    }
    let depth = 0;
    let inStr = false;
    let escape = false;
    let end = -1;
    for (let i = start; i < normalized.length; i++) {
      const ch = normalized[i];
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
        if (depth === 0) {
          end = i;
          break;
        }
      }
    }
    if (end === -1) break;
    pushIfRecommendation(normalized.slice(start, end + 1));
    idx = end + 1;
  }

  return blocks;
}

/** 給 UI 顯示前剝掉 JSON 區塊 (對應上面三種狀況) */
export function stripRecommendationJson(text: string): string {
  if (!text) return text;
  let result = text;

  // 1) 移除 ```...``` fenced 區塊（含 json lang 與否）
  result = result.replace(/```(?:json)?\s*[\s\S]*?```/g, '');

  // 2) 移除尚未閉合的 ```json ... 開頭區塊（streaming 中）
  result = result.replace(/```(?:json)?\s*[\s\S]*$/g, '');

  // 3) 移除裸 JSON 物件（含 insurance_recommendation）
  // 在 normalize 過的字串上掃描位置（length 不變所以 index 共用），實際 slice 原字串
  for (let safety = 0; safety < 5; safety++) {
    const normalized = normalizeJsonPunctuation(result);
    const idx = normalized.indexOf('"insurance_recommendation"');
    if (idx === -1) break;
    let start = -1;
    for (let i = idx; i >= 0; i--) {
      if (normalized[i] === '{') {
        start = i;
        break;
      }
    }
    if (start === -1) break;
    let depth = 0;
    let inStr = false;
    let escape = false;
    let end = -1;
    for (let i = start; i < normalized.length; i++) {
      const ch = normalized[i];
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
        if (depth === 0) {
          end = i;
          break;
        }
      }
    }
    if (end === -1) {
      // 區塊還沒收完整（streaming 中），把從 start 之後全砍掉
      result = result.slice(0, start);
      break;
    }
    result = result.slice(0, start) + result.slice(end + 1);
  }

  return result.replace(/\n{3,}/g, '\n\n').trim();
}

const NAME_TO_TYPE: Array<[RegExp, Product['product_type']]> = [
  [/醫療|住院|手術/, 'medical'],
  [/意外|職業/, 'accident'],
  [/壽險|身故|家庭/, 'family_protection'],
  [/失能|收入|工作能力/, 'income_protection'],
];

function inferProductType(
  productName: string,
  rules?: string,
): Product['product_type'] {
  const haystack = `${productName ?? ''} ${rules ?? ''}`;
  for (const [pattern, type] of NAME_TO_TYPE) {
    if (pattern.test(haystack)) return type;
  }
  return 'medical';
}

function recommendationToProduct(
  block: InsuranceRecommendationBlock,
  messageId: string,
  index: number,
): Product | null {
  if (!block.productName) return null;
  return {
    product_id: `${messageId}-${index}`,
    product_name: block.productName,
    product_type: inferProductType(block.productName, block.rules),
    coverage_focus: block.reason,
    coverage_summary: block.terms,
    exclusions: block.reminders,
    budget_fit_text: block.budgetFit,
    reason: block.reason,
    reminders: block.reminders,
    terms: block.terms,
    rules: block.rules,
    source_tool: 'insurance_recommendation',
  };
}

export function useProductExtractor(): ProductExtractorAPI {
  const [products, setProducts] = useState<Product[]>([]);
  const [productsByMessage, setProductsByMessage] = useState<
    Record<string, Product[]>
  >({});

  const merge = useCallback((incoming: Product[]) => {
    if (incoming.length === 0) return;
    setProducts((prev) => dedupe([...prev, ...incoming]));
  }, []);

  const reset = useCallback(() => {
    setProducts([]);
    setProductsByMessage({});
  }, []);

  // agent 漏吐 / 吐壞推薦 JSON 時的 fallback 來源：
  // 它一定會呼叫 save_last_recommendation（帶商品名），記下來供 ingestAgentMessage 保底。
  const lastSavedRef = useRef<{ name: string; id: string } | null>(null);

  const ingestSseTimelineEvent = useCallback<
    ProductExtractorAPI['ingestSseTimelineEvent']
  >(
    (event) => {
      if (!event.title) return;
      if (/save_last_recommendation/.test(event.title)) {
        const raw = parseSseToolResultPayload(event.payload);
        if (raw && typeof raw === 'object') {
          const o = raw as Record<string, unknown>;
          const name = typeof o.product_name === 'string' ? o.product_name : '';
          if (name) {
            lastSavedRef.current = { name, id: String(o.product_id ?? name) };
          }
        }
        return;
      }
      if (event.kind !== 'tool-result') return;
      const toolName = event.title.replace(/\s+result$/, '');
      if (!PRODUCT_SEARCH_TOOLS.has(toolName)) return;
      const raw = parseSseToolResultPayload(event.payload);
      merge(coerceProducts(raw, toolName));
    },
    [merge],
  );

  const ingestAdkEvent = useCallback<ProductExtractorAPI['ingestAdkEvent']>(
    (event) => {
      const parts = event?.content?.parts;
      if (!Array.isArray(parts)) return;
      for (const part of parts) {
        const fr = part?.functionResponse ?? part?.function_response;
        if (!fr?.name || !PRODUCT_SEARCH_TOOLS.has(fr.name)) continue;
        merge(coerceProducts(fr.response, fr.name));
      }
    },
    [merge],
  );

  const seenMessagesRef = useRef<Map<string, number>>(new Map());

  const ingestAgentMessage = useCallback<
    ProductExtractorAPI['ingestAgentMessage']
  >(
    ({ id, text, status }) => {
      const blocks = extractRecommendationBlocks(text);
      if (blocks.length > 0) {
        lastSavedRef.current = null; // 有可解析的推薦 JSON，不需 fallback
        const previousCount = seenMessagesRef.current.get(id) ?? 0;
        if (blocks.length === previousCount) return;
        const fresh = blocks
          .slice(previousCount)
          .map((block, idx) =>
            recommendationToProduct(block, id, previousCount + idx),
          )
          .filter((p): p is Product => Boolean(p));
        seenMessagesRef.current.set(id, blocks.length);
        if (fresh.length > 0) {
          merge(fresh);
          setProductsByMessage((prev) => ({
            ...prev,
            [id]: [...(prev[id] ?? []), ...fresh],
          }));
        }
        return;
      }

      // 沒有可解析的推薦 JSON（漏吐或格式壞）：串流結束時用 save_last_recommendation 保底，
      // 至少讓右側顯示被推薦的商品名，不再空白。
      if (
        status === 'final' &&
        lastSavedRef.current &&
        !seenMessagesRef.current.has(id)
      ) {
        const saved = lastSavedRef.current;
        lastSavedRef.current = null;
        seenMessagesRef.current.set(id, 1);
        const product: Product = {
          product_id: saved.id,
          product_name: saved.name,
          product_type: inferProductType(saved.name),
          reason: '顧問已為您推薦此方案，詳細內容請參考左側對話說明。',
          source_tool: 'save_last_recommendation',
        };
        merge([product]);
        setProductsByMessage((prev) => ({
          ...prev,
          [id]: [...(prev[id] ?? []), product],
        }));
      }
    },
    [merge],
  );

  const resetCombined = useCallback(() => {
    seenMessagesRef.current.clear();
    reset();
  }, [reset]);

  return {
    products,
    productsByMessage,
    reset: resetCombined,
    ingestSseTimelineEvent,
    ingestAdkEvent,
    ingestAgentMessage,
  };
}
