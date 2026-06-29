'use client';

import {
  Fragment,
  FormEvent,
  KeyboardEvent,
  ReactNode,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useSession } from 'next-auth/react';
import { Product, UserModeView } from './types';
import { ProductCard } from './ProductCard';
import { useUserChat } from './useUserChat';
import {
  stripRecommendationJson,
  useProductExtractor,
} from './useProductExtractor';
import type { ChatMessage, TimelineEvent } from '../../lib/mock-data';
import { ModeSwitch, type AppMode } from '../ModeSwitch';
import {
  getStoredUserModeSessionId,
  loadSessionHistory,
  pruneSessionHistory,
  removeSessionHistory,
  saveSessionHistory,
  setStoredUserModeSessionId,
  subscribeSessionHistory,
} from '../../lib/session-storage';

const APP_NAME = process.env.NEXT_PUBLIC_APP_NAME ?? 'app';

const SUGGESTION_PROMPTS = [
  '我 30 歲，年度預算一萬五，想加強住院醫療保障',
  '幫我看意外險，工作偶爾要爬高',
  '家裡剛迎接寶寶，主要經濟來源該怎麼規劃壽險？',
];

const TYPE_LABEL: Record<string, string> = {
  medical: '醫療保障',
  accident: '意外保障',
  family_protection: '家庭責任',
  income_protection: '收入保障',
  critical_illness: '重大疾病',
  life: '壽險保障',
};

const BUDGET_FIT_LABEL: Record<string, string> = {
  fully_within_budget: '完全在預算內',
  entry_affordable: '基本可負擔',
  over_budget: '高於預算',
};

const BUDGET_FIT_TONE: Record<string, 'ok' | 'soft' | 'warn'> = {
  fully_within_budget: 'ok',
  entry_affordable: 'soft',
  over_budget: 'warn',
};

const SOURCE_TOOL_LABEL: Record<string, string> = {
  search_medical_products: '醫療商品庫',
  search_accident_products: '意外商品庫',
  search_family_protection_products: '家庭保障商品庫',
  search_income_protection_products: '收入保障商品庫',
  search_products_by_name: '名稱搜尋',
  get_product_detail: '單一商品查詢',
  get_product_details: '單一商品查詢',
  get_product_by_name: '名稱直接帶入',
  insurance_recommendation: 'AI 顧問彙整',
};

/** Tool-call 進度 chip 顯示的中文標籤（呼叫中 / 完成 共用） */
const TOOL_PROGRESS_LABEL: Record<string, string> = {
  search_medical_products: '搜尋醫療商品',
  search_accident_products: '搜尋意外商品',
  search_family_protection_products: '搜尋家庭保障商品',
  search_income_protection_products: '搜尋收入保障商品',
  search_products_by_name: '搜尋特定商品',
  get_product_detail: '查詢商品詳情',
  get_product_details: '查詢商品詳情',
  get_product_by_name: '取得商品資料',
};

function makeSessionId() {
  if (
    typeof crypto !== 'undefined' &&
    typeof (crypto as any).randomUUID === 'function'
  ) {
    return `user-${(crypto as any).randomUUID()}`;
  }
  return `user-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/**
 * 便宜的內容簽章：只看「會被持久化的東西」(final 訊息數 / events 數 / 最後一則 id+長度)。
 * 用來在存檔前判斷內容是否真的有變 —— 既省掉重複寫入，也擋掉 cross-tab 套用遠端後
 * 又把同樣內容寫回去、兩個分頁互相觸發的無限迴圈。
 */
function historySignature(
  messages: ChatMessage[],
  events: TimelineEvent[],
): string {
  const finalMsgs = messages.filter((m) => m.status === 'final');
  const last = finalMsgs.at(-1);
  return `${finalMsgs.length}:${events.length}:${last?.id ?? ''}:${last?.text.length ?? 0}`;
}

function formatPremium(min?: number, max?: number) {
  if (min == null && max == null) return '—';
  const fmt = (n: number) => `NT$ ${n.toLocaleString('zh-TW')}`;
  if (min != null && max != null) {
    if (min === max) return fmt(min);
    return `${fmt(min)} – ${fmt(max)}`;
  }
  return fmt((min ?? max) as number);
}

async function createAdkSession(
  userId: string,
  sessionId: string,
  accessToken: string | undefined,
): Promise<void> {
  if (userId === 'anonymous') return;
  try {
    await fetch(
      `/api/apps/${APP_NAME}/users/${encodeURIComponent(userId)}/sessions`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
        },
        body: JSON.stringify({
          sessionId,
          state: {
            // 跟 Dev Mode 同款命名，使用者第一則訊息送出後 subtitle 會被覆寫成那句話
            _ui_title: '新對話',
            _ui_subtitle: '開始新的對話',
          },
        }),
      },
    );
  } catch {
    // ADK 離線 — 容忍
  }
}

export interface UserModeProps {
  mode?: AppMode;
  onSwitchMode?: (next: AppMode) => void;
}

export function UserMode({ mode, onSwitchMode }: UserModeProps = {}) {
  const { data: session } = useSession();
  const accessToken = (session as any)?.accessToken as string | undefined;
  const userId = useMemo(
    () => session?.user?.name || 'anonymous',
    [session?.user?.name],
  );

  // 初次 mount 決定 sessionId：localStorage > 新建。同步 hydrate 對應歷史訊息。
  const [bootstrap] = useState<{
    sessionId: string;
    messages: ChatMessage[];
    isNew: boolean;
  }>(() => {
    if (typeof window === 'undefined') {
      return { sessionId: makeSessionId(), messages: [], isNew: true };
    }
    const stored = getStoredUserModeSessionId();
    if (stored) {
      const history = loadSessionHistory(stored);
      return {
        sessionId: stored,
        messages: history?.messages ?? [],
        isNew: false,
      };
    }
    const fresh = makeSessionId();
    setStoredUserModeSessionId(fresh);
    return { sessionId: fresh, messages: [], isNew: true };
  });

  const [sessionId, setSessionId] = useState(bootstrap.sessionId);

  // Lazy ADK 建立：只在使用者真的送出第一則訊息時才 POST /sessions，
  // 避免每次 reload / 切 mode / 點「新對話」都在後端產生一個空殼 session
  // 若 bootstrap 是從 LS hydrate 來的（已有訊息），假設後端 session 已存在不再 POST
  const adkSessionCreatedRef = useRef<boolean>(
    bootstrap.messages.length > 0,
  );

  const [selectedProduct, setSelectedProduct] = useState<Product | null>(null);
  const [error, setError] = useState<string | null>(null);
  // grid 目前在看「哪一組推薦」(以該 agent message id 為準)。null = 跟著最新那組。
  const [viewedBatchId, setViewedBatchId] = useState<string | null>(null);
  const lastHandledRecommendationMessageRef = useRef<string | null>(null);
  // 從 localStorage hydrate 出來的歷史訊息不該觸發 auto-detail（user 重整時想看到剛離開的畫面）
  const isHydratingRef = useRef<boolean>(bootstrap.messages.length > 0);

  const extractor = useProductExtractor();
  const {
    messages,
    events,
    pending,
    send: rawSend,
    reset: resetChat,
  } = useUserChat({
    sessionId,
    userId,
    initialMessages: bootstrap.messages,
    onTimelineEvent: (event) => extractor.ingestSseTimelineEvent(event),
    onError: setError,
  });

  // 包一層：第一次送訊息前先確保 ADK session 存在（lazy create）
  const send = useCallback(
    async (prompt: string) => {
      if (!adkSessionCreatedRef.current && userId !== 'anonymous') {
        adkSessionCreatedRef.current = true;
        await createAdkSession(userId, sessionId, accessToken);
      }
      return rawSend(prompt);
    },
    [accessToken, rawSend, sessionId, userId],
  );

  // Detail CTA: start an AI-driven qualifying interview about this product.
  // 不清掉 selectedProduct — chat 在左邊推進訪談，spec 在右邊可對照。
  // 訪談會由 agent 一次問一題、累積條件，最後給出「是否適合」結論
  const startInterview = useCallback(
    (product: Product) => {
      send(
        `我對「${product.product_name}」有興趣，但想確認是否真的適合我。請以保險顧問的角色幫我做一個簡短評估，依序問我 3-5 個必要問題（例如：年齡、健康狀況、家庭責任、預算彈性、既有保障），每次只問一個並等我回答後再問下一個。問完後，請給出明確的「適合 / 部分適合 / 不適合」結論，並建議下一步該怎麼做。`,
      );
    },
    [send],
  );

  // Scan finalized agent messages for ```json insurance_recommendation blocks
  useEffect(() => {
    for (const message of messages) {
      if (message.role !== 'agent' || !message.text) continue;
      // Try parsing even mid-stream — the extractor skips malformed JSON,
      // and once the block is complete it will populate the grid in real time.
      extractor.ingestAgentMessage({
        id: message.id,
        text: message.text,
        status: message.status,
      });
    }
  }, [messages, extractor]);

  // 找出「最新一則帶推薦的 agent message」（時間序最後一個有 product 的）
  const latestRecommendationMessageId = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const id = messages[i].id;
      if (extractor.productsByMessage[id]?.length) return id;
    }
    return null;
  }, [messages, extractor.productsByMessage]);

  // 新一則推薦進來時 reset 右邊 panel：
  //   - 1 個新方案 → 直接展開 detail
  //   - 多個新方案 → 退回 grid 讓使用者挑
  // 同一則 message 只處理一次，避免「按返回又被打開」的迴圈
  // hydration 期間（首次從 LS 載回歷史）skip auto-select，使用者重整時不該被強制展開
  useEffect(() => {
    if (!latestRecommendationMessageId) return;
    if (
      lastHandledRecommendationMessageRef.current ===
      latestRecommendationMessageId
    ) {
      return;
    }
    lastHandledRecommendationMessageRef.current = latestRecommendationMessageId;
    if (isHydratingRef.current) {
      isHydratingRef.current = false;
      return;
    }
    const newProducts =
      extractor.productsByMessage[latestRecommendationMessageId] ?? [];
    if (newProducts.length === 1) {
      setSelectedProduct(newProducts[0]);
    } else if (newProducts.length > 1) {
      setSelectedProduct(null);
    }
  }, [latestRecommendationMessageId, extractor.productsByMessage]);

  // 訊息 / events 變動時節流存到 localStorage（每 500ms 一次）。
  // 用 throttle (不是 debounce) 是為了「streaming 期間切到 Dev Mode 也能看到大致進度」。
  // refs 確保 trailing save 拿的是最新狀態，而不是 closure 抓住的舊值。
  const saveStateRef = useRef({ sessionId, messages, events });
  saveStateRef.current = { sessionId, messages, events };

  const lastSaveTsRef = useRef(0);
  const pendingSaveTimerRef = useRef<number | null>(null);
  // 上次實際寫進 LS 的內容簽章；用來在存檔前 dedup（含擋 cross-tab echo 迴圈）。
  // 初始化成 bootstrap 內容，避免 hydrate 完馬上又把同樣東西寫回去一次。
  const lastSavedSigRef = useRef<string>(
    historySignature(bootstrap.messages, []),
  );

  useEffect(() => {
    if (messages.length === 0 && events.length === 0) return;

    const doSave = () => {
      pendingSaveTimerRef.current = null;
      const s = saveStateRef.current;
      const sig = historySignature(s.messages, s.events);
      // 內容沒變就別寫（節流視窗內多次 render / cross-tab 套用遠端後的回寫都會落在這）
      if (sig === lastSavedSigRef.current) return;
      saveSessionHistory(s.sessionId, s.messages, s.events);
      lastSavedSigRef.current = sig;
      lastSaveTsRef.current = Date.now();
    };

    const elapsed = Date.now() - lastSaveTsRef.current;
    if (elapsed >= 500) {
      doSave();
    } else if (pendingSaveTimerRef.current === null) {
      pendingSaveTimerRef.current = window.setTimeout(doSave, 500 - elapsed);
    }
  }, [sessionId, messages, events]);

  // unmount 時把最後狀態 flush 進去（避免切 mode / 關頁面剛好錯過 throttle 視窗）
  useEffect(() => {
    return () => {
      if (pendingSaveTimerRef.current !== null) {
        window.clearTimeout(pendingSaveTimerRef.current);
      }
      const s = saveStateRef.current;
      const sig = historySignature(s.messages, s.events);
      if (
        (s.messages.length > 0 || s.events.length > 0) &&
        sig !== lastSavedSigRef.current
      ) {
        saveSessionHistory(s.sessionId, s.messages, s.events);
        lastSavedSigRef.current = sig;
      }
    };
  }, []);

  // 進場時清掉過期 / 超量的舊對話歷史，避免 localStorage 無限累積最後撐爆 quota。
  // 保護當前 session 不被淘汰。
  useEffect(() => {
    pruneSessionHistory([sessionId]);
    // 只在首次 mount 跑一次即可
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Cross-tab 同步：同一個 session 在別的分頁被更新時，閒置（非 pending）的這個分頁跟上。
  // 只有當對方的 updatedTs 比我們最後一次存的新時才套用，避免把自己較新的進度蓋回舊狀態。
  useEffect(() => {
    return subscribeSessionHistory(sessionId, (history) => {
      if (!history || pending) return;
      if (history.updatedTs <= lastSaveTsRef.current) return;
      lastSaveTsRef.current = history.updatedTs;
      // 把簽章同步成遠端內容：套用後 resetChat 觸發的存檔會比對到相同簽章而跳過，
      // 不會又寫回去、害兩個分頁互相觸發。
      lastSavedSigRef.current = historySignature(
        history.messages,
        history.events,
      );
      // 跟 hydration 一樣，不要讓 cross-tab 更新強制彈開 detail panel
      isHydratingRef.current = true;
      lastHandledRecommendationMessageRef.current = null;
      resetChat({ messages: history.messages, events: history.events });
    });
  }, [sessionId, pending, resetChat]);

  // 所有「帶推薦的 agent message」依時間序 = 多組推薦批次，供「看先前推薦」用。
  const recommendationBatches = useMemo<
    { messageId: string; products: Product[] }[]
  >(() => {
    const batches: { messageId: string; products: Product[] }[] = [];
    for (const m of messages) {
      const products = extractor.productsByMessage[m.id];
      if (products?.length) batches.push({ messageId: m.id, products });
    }
    return batches;
  }, [messages, extractor.productsByMessage]);

  // 新一組推薦進來時，grid 自動跳到最新那組（使用者通常想看剛問到的結果）。
  useEffect(() => {
    if (latestRecommendationMessageId) {
      setViewedBatchId(latestRecommendationMessageId);
    }
  }, [latestRecommendationMessageId]);

  // grid 當前在看的批次 index（找不到就退回最新一組）。
  const viewedBatchIndex = useMemo(() => {
    if (recommendationBatches.length === 0) return -1;
    const i = viewedBatchId
      ? recommendationBatches.findIndex((b) => b.messageId === viewedBatchId)
      : -1;
    return i === -1 ? recommendationBatches.length - 1 : i;
  }, [recommendationBatches, viewedBatchId]);

  // grid 顯示的商品 = 當前所看批次的商品（不一定是最新那組）。
  const latestProducts = useMemo<Product[]>(() => {
    if (viewedBatchIndex < 0) return [];
    return recommendationBatches[viewedBatchIndex]?.products ?? [];
  }, [recommendationBatches, viewedBatchIndex]);

  const handlePrevBatch = useMemo(() => {
    if (viewedBatchIndex <= 0) return undefined;
    return () =>
      setViewedBatchId(recommendationBatches[viewedBatchIndex - 1].messageId);
  }, [recommendationBatches, viewedBatchIndex]);

  const handleNextBatch = useMemo(() => {
    if (
      viewedBatchIndex < 0 ||
      viewedBatchIndex >= recommendationBatches.length - 1
    ) {
      return undefined;
    }
    return () =>
      setViewedBatchId(recommendationBatches[viewedBatchIndex + 1].messageId);
  }, [recommendationBatches, viewedBatchIndex]);

  const view: UserModeView = useMemo(() => {
    if (messages.length === 0 && latestProducts.length === 0) return 'empty';
    if (selectedProduct) return 'detail';
    return 'browsing';
  }, [messages.length, latestProducts.length, selectedProduct]);

  // 當前在 detail 時，找出該商品在 latestProducts 中的 index，
  // 推出 prev/next 是否可用（沒 sibling 就是 undefined → DetailView 不顯示該按鈕）
  const selectedSiblingIndex = useMemo(() => {
    if (!selectedProduct) return -1;
    return latestProducts.findIndex(
      (p) => String(p.product_id) === String(selectedProduct.product_id),
    );
  }, [latestProducts, selectedProduct]);

  const handlePrevSibling = useMemo(() => {
    if (selectedSiblingIndex <= 0) return undefined;
    return () => setSelectedProduct(latestProducts[selectedSiblingIndex - 1]);
  }, [latestProducts, selectedSiblingIndex]);

  const handleNextSibling = useMemo(() => {
    if (
      selectedSiblingIndex < 0 ||
      selectedSiblingIndex >= latestProducts.length - 1
    ) {
      return undefined;
    }
    return () => setSelectedProduct(latestProducts[selectedSiblingIndex + 1]);
  }, [latestProducts, selectedSiblingIndex]);

  const handleNewSession = useCallback(() => {
    // 清掉舊 session 在 LS 的痕跡，避免下次又被 hydrate
    removeSessionHistory(sessionId);
    const fresh = makeSessionId();
    setStoredUserModeSessionId(fresh);
    setSessionId(fresh);
    resetChat();
    extractor.reset();
    setSelectedProduct(null);
    setError(null);
    lastHandledRecommendationMessageRef.current = null;
    isHydratingRef.current = false;
    // 新 session 在「使用者真的送出第一則訊息」時才 POST 給 ADK，
    // 避免按一下「新對話」就在 backend 留下一個空殼
    adkSessionCreatedRef.current = false;
  }, [extractor, resetChat, sessionId]);

  // Auto-dismiss error after 6s
  useEffect(() => {
    if (!error) return;
    const timer = window.setTimeout(() => setError(null), 6000);
    return () => window.clearTimeout(timer);
  }, [error]);

  return (
    <main className="um" aria-label="使用者模式">
      <header className="um__topbar">
        <div className="um__brand">
          <div className="um__brand-mark" aria-hidden>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M12 2L3 6.5v5c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12v-5L12 2z" />
              <path d="M9 12l2 2 4-4" />
            </svg>
          </div>
          <div className="um__brand-text">
            <span className="um__brand-eyebrow">INSURANCE AGENT</span>
            <span className="um__brand-name">為你找對的保險</span>
          </div>
        </div>
        <div className="um__topbar-actions">
          {view !== 'empty' && (
            <button
              type="button"
              className="um__new-chat"
              onClick={handleNewSession}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" aria-hidden>
                <path d="M12 5v14M5 12h14" />
              </svg>
              新對話
            </button>
          )}
          {mode && onSwitchMode && (
            <ModeSwitch
              mode={mode}
              onSwitch={onSwitchMode}
              variant="inline"
            />
          )}
        </div>
      </header>

      {error && (
        <div className="um__error" role="alert">
          <span>{error}</span>
          <button
            type="button"
            className="um__error-close"
            onClick={() => setError(null)}
            aria-label="關閉錯誤訊息"
          >
            ×
          </button>
        </div>
      )}

      {view === 'empty' && (
        <EmptyState
          pending={pending}
          onSubmit={send}
          onSuggestion={send}
        />
      )}

      {view !== 'empty' && (
        <div className="um__split">
          <ChatPane
            messages={messages}
            events={events}
            pending={pending}
            onSubmit={send}
            productsByMessage={extractor.productsByMessage}
          />
          <section className="um__pane um__pane--right">
            {view === 'detail' && selectedProduct ? (
              <DetailView
                key={String(selectedProduct.product_id)}
                product={selectedProduct}
                onBack={() => setSelectedProduct(null)}
                onStartInterview={startInterview}
                onPrev={handlePrevSibling}
                onNext={handleNextSibling}
                position={
                  latestProducts.length > 1
                    ? {
                        index: selectedSiblingIndex,
                        total: latestProducts.length,
                      }
                    : undefined
                }
              />
            ) : (
              <GridView
                products={latestProducts}
                pending={pending}
                onSelect={setSelectedProduct}
                batchPosition={
                  recommendationBatches.length > 1
                    ? {
                        index: viewedBatchIndex,
                        total: recommendationBatches.length,
                      }
                    : undefined
                }
                onPrevBatch={handlePrevBatch}
                onNextBatch={handleNextBatch}
              />
            )}
          </section>
        </div>
      )}
    </main>
  );
}

interface EmptyStateProps {
  pending: boolean;
  onSubmit: (text: string) => void;
  onSuggestion: (text: string) => void;
}

function EmptyState({ pending, onSubmit, onSuggestion }: EmptyStateProps) {
  return (
    <section className="um-empty">
      <div className="um-empty__inner">
        <span className="um-empty__eyebrow">智慧保險顧問</span>
        <h1 className="um-empty__title">
          找到對的保險，<br />從一句話開始。
        </h1>
        <p className="um-empty__lede">
          告訴我你的年齡、預算與想加強的保障，
          我會即時推薦適合的商品並解釋原因。
        </p>

        <div className="um-empty__suggestions">
          {SUGGESTION_PROMPTS.map((prompt) => (
            <button
              key={prompt}
              type="button"
              className="um-empty__suggestion"
              onClick={() => onSuggestion(prompt)}
              disabled={pending}
            >
              <span className="um-empty__suggestion-icon" aria-hidden>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
                  <path d="M9 18l6-6-6-6" />
                </svg>
              </span>
              {prompt}
            </button>
          ))}
        </div>

        <div className="um-empty__composer">
          <Composer
            placeholder="想找哪種保險？例：我 30 歲、預算 12000，想保醫療"
            pending={pending}
            onSubmit={onSubmit}
            autoFocus
          />
        </div>
      </div>
    </section>
  );
}

interface ChatPaneProps {
  messages: ChatMessage[];
  events: TimelineEvent[];
  pending: boolean;
  onSubmit: (text: string) => void;
  productsByMessage: Record<string, Product[]>;
}

interface ToolProgressEntry {
  /** 不含 " result" 後綴的 tool name */
  tool: string;
  /** 從 tool-call event 拿到的 id (用作 React key) */
  id: string;
  /** 是否已收到對應 tool-result event */
  done: boolean;
}

/** 把 events 依「user 事件邊界」切群，並把每群內的 tool-call/result 摺成一條進度條目 */
function buildToolProgressByUser(
  events: TimelineEvent[],
): Record<string, ToolProgressEntry[]> {
  const result: Record<string, ToolProgressEntry[]> = {};
  let currentKey: string | null = null;
  for (const evt of events) {
    if (evt.kind === 'user') {
      // event.id 形如 'evt-user-msg-u-xxxx' → 對應的 user message id 是去掉 'evt-user-' 前綴
      currentKey = evt.id.replace(/^evt-user-/, '');
      result[currentKey] = [];
      continue;
    }
    if (!currentKey) continue;
    if (evt.kind === 'tool-call') {
      const tool = (evt.title ?? '').trim();
      if (!tool) continue;
      result[currentKey].push({ tool, id: evt.id, done: false });
    } else if (evt.kind === 'tool-result') {
      const tool = (evt.title ?? '').replace(/\s+result$/, '').trim();
      if (!tool) continue;
      // 找最後一筆同名 tool-call 標 done；找不到就當作獨立 done 條目
      const list = result[currentKey];
      const pending = [...list].reverse().find((e) => e.tool === tool && !e.done);
      if (pending) {
        pending.done = true;
      } else {
        list.push({ tool, id: evt.id, done: true });
      }
    }
  }
  return result;
}

/** 把 agent 偶爾塞進來的 HTML tag (e.g. <br>, <p>) 換成換行 */
function cleanHtmlArtifacts(text: string): string {
  return text
    .replace(/<br\s*\/?\s*>/gi, '\n')
    .replace(/<\/p>/gi, '\n')
    .replace(/<[^>]+>/g, '')
    .replace(/\n{3,}/g, '\n\n');
}

function formatChatText(text: string): string {
  return cleanHtmlArtifacts(stripRecommendationJson(text)).trim();
}

function summarizeForChat(products: Product[]): string {
  if (products.length === 0) return '';
  if (products.length === 1) {
    return `我推薦了「${products[0].product_name}」，右側可看完整方案。`;
  }
  const names = products.map((p) => `「${p.product_name}」`).join('、');
  return `我為你篩選了 ${products.length} 個方案：${names}，右側可看完整內容。`;
}

/**
 * 把 **bold** 標記轉成 <strong>，其餘保留文字。
 * 不支援巢狀；遇到未閉合 ** 直接當作純文字渲染（streaming 中常見）。
 */
function renderInlineMd(text: string, keyPrefix: string): ReactNode[] {
  if (!text) return [];
  const out: ReactNode[] = [];
  const parts = text.split(/(\*\*[^*\n]+?\*\*)/g);
  parts.forEach((part, i) => {
    if (!part) return;
    if (part.startsWith('**') && part.endsWith('**') && part.length >= 4) {
      out.push(
        <strong key={`${keyPrefix}-b-${i}`}>{part.slice(2, -2)}</strong>,
      );
    } else {
      out.push(<Fragment key={`${keyPrefix}-t-${i}`}>{part}</Fragment>);
    }
  });
  return out;
}

const BULLET_LINE_RE = /^\s*[*\-•]\s+/;

/**
 * 把 agent 輸出的 markdown 子集（**bold** + bullet 清單 + 段落）轉成 React nodes。
 * 純 React 元素組合，輸入是已 strip HTML 的純文字，因此沒有 XSS 風險。
 */
function renderChatMarkdown(text: string): ReactNode {
  if (!text) return null;
  const blocks = text.split(/\n{2,}/);
  return blocks.map((block, blockIdx) => {
    const lines = block.split('\n');
    const nonEmptyLines = lines.filter((l) => l.trim() !== '');
    const isBulletList =
      nonEmptyLines.length > 0 &&
      nonEmptyLines.every((l) => BULLET_LINE_RE.test(l));

    if (isBulletList) {
      return (
        <ul key={`b-${blockIdx}`} className="um-md__list">
          {nonEmptyLines.map((line, i) => {
            const content = line.replace(BULLET_LINE_RE, '').trim();
            return (
              <li key={i}>{renderInlineMd(content, `${blockIdx}-${i}`)}</li>
            );
          })}
        </ul>
      );
    }

    return (
      <p key={`p-${blockIdx}`} className="um-md__p">
        {lines.map((line, i) => (
          <Fragment key={i}>
            {renderInlineMd(line, `${blockIdx}-${i}`)}
            {i < lines.length - 1 && <br />}
          </Fragment>
        ))}
      </p>
    );
  });
}

function ChatPane({
  messages,
  events,
  pending,
  onSubmit,
  productsByMessage,
}: ChatPaneProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const toolProgressByUser = useMemo(
    () => buildToolProgressByUser(events),
    [events],
  );

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [messages, toolProgressByUser]);

  return (
    <section className="um__pane um__pane--left">
      <div className="um-chat" ref={scrollRef}>
        {messages.map((m) => {
          let display: string;
          if (m.role === 'user') {
            display = m.text;
          } else {
            const matched = productsByMessage[m.id];
            display =
              matched && matched.length > 0
                ? summarizeForChat(matched)
                : formatChatText(m.text);
          }
          const toolProgress =
            m.role === 'user' ? toolProgressByUser[m.id] : undefined;
          return (
            <Fragment key={m.id}>
              <div
                className={`um-msg um-msg--${m.role} ${m.status === 'streaming' ? 'um-msg--streaming' : ''}`}
              >
                <span className="um-msg__meta">
                  {m.role === 'user' ? '你' : '顧問'} · {m.timestamp}
                </span>
                <div className="um-msg__text">
                  {m.role === 'agent' && display ? (
                    renderChatMarkdown(display)
                  ) : display ? (
                    <p className="um-md__p">{display}</p>
                  ) : m.status === 'streaming' ? (
                    <p className="um-md__p um-md__p--placeholder">思考中…</p>
                  ) : null}
                </div>
              </div>
              {toolProgress && toolProgress.length > 0 && (
                <ToolProgressList entries={toolProgress} />
              )}
            </Fragment>
          );
        })}
      </div>
      <div className="um-chat__composer">
        <Composer
          placeholder="繼續提問或補充條件…"
          pending={pending}
          onSubmit={onSubmit}
          compact
        />
      </div>
    </section>
  );
}

function ToolProgressList({ entries }: { entries: ToolProgressEntry[] }) {
  return (
    <ul className="um-tools" aria-label="顧問正在執行的查詢">
      {entries.map((entry) => {
        const label = TOOL_PROGRESS_LABEL[entry.tool] ?? entry.tool;
        return (
          <li
            key={entry.id}
            className={`um-tool-chip ${entry.done ? 'is-done' : 'is-pending'}`}
            aria-live="polite"
          >
            <span className="um-tool-chip__icon" aria-hidden>
              {entry.done ? (
                <svg
                  width="11"
                  height="11"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="3"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M5 12l5 5L20 7" />
                </svg>
              ) : (
                <span className="um-tool-chip__spinner" />
              )}
            </span>
            <span className="um-tool-chip__label">
              {entry.done ? label : `${label}…`}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

interface ComposerProps {
  placeholder: string;
  pending: boolean;
  onSubmit: (text: string) => void;
  autoFocus?: boolean;
  compact?: boolean;
}

function Composer({
  placeholder,
  pending,
  onSubmit,
  autoFocus,
  compact,
}: ComposerProps) {
  const [value, setValue] = useState('');

  const submit = (e?: FormEvent) => {
    e?.preventDefault();
    const text = value.trim();
    if (!text || pending) return;
    onSubmit(text);
    setValue('');
  };

  const handleKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <form
      className={`um-composer ${compact ? 'um-composer--compact' : ''}`}
      onSubmit={submit}
    >
      <textarea
        value={value}
        autoComplete="off"
        data-1p-ignore=""
        data-lpignore="true"
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={handleKey}
        placeholder={placeholder}
        rows={compact ? 2 : 3}
        autoFocus={autoFocus}
        disabled={pending}
        aria-label="輸入你的需求"
      />
      <button
        type="submit"
        className="um-composer__send"
        disabled={pending || value.trim().length === 0}
        aria-label="送出訊息"
      >
        {pending ? (
          <span className="um-composer__dots" aria-hidden>
            <span />
            <span />
            <span />
          </span>
        ) : (
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <path d="M5 12l14-7-5 14-2-7-7-0z" />
          </svg>
        )}
      </button>
    </form>
  );
}

interface GridViewProps {
  products: Product[];
  pending: boolean;
  onSelect: (product: Product) => void;
  /** 多組推薦時的批次位置，僅當 total > 1 時顯示「看先前推薦」分頁 */
  batchPosition?: { index: number; total: number };
  onPrevBatch?: () => void;
  onNextBatch?: () => void;
}

function GridView({
  products,
  pending,
  onSelect,
  batchPosition,
  onPrevBatch,
  onNextBatch,
}: GridViewProps) {
  if (products.length === 0) {
    return (
      <div className="um-grid um-grid--placeholder">
        <div className="um-placeholder">
          <div className="um-placeholder__icon" aria-hidden>
            <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
              <circle cx="11" cy="11" r="7" />
              <path d="M21 21l-4.3-4.3" />
            </svg>
          </div>
          <h2 className="um-placeholder__title">
            {pending ? '正在為你尋找商品…' : '尚未取得商品建議'}
          </h2>
          <p className="um-placeholder__hint">
            告訴顧問你的年齡、預算與保障需求，這裡會即時顯示對應商品。
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="um-grid" aria-live="polite">
      <header className="um-grid__head">
        <div className="um-grid__headline">
          <h2 className="um-grid__title">為你篩選的 {products.length} 個商品</h2>
          <span className="um-grid__hint">點卡片看詳情</span>
        </div>
        {batchPosition && (
          <div className="um-grid__pager" role="group" aria-label="切換推薦批次">
            <button
              type="button"
              className="um-grid__pager-btn"
              onClick={onPrevBatch}
              disabled={!onPrevBatch}
              aria-label="看先前一組推薦"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M15 18l-6-6 6-6" />
              </svg>
            </button>
            <span className="um-grid__pager-label">
              第 {batchPosition.index + 1} / {batchPosition.total} 組
              {batchPosition.index < batchPosition.total - 1 && (
                <span className="um-grid__pager-tag">先前推薦</span>
              )}
            </span>
            <button
              type="button"
              className="um-grid__pager-btn"
              onClick={onNextBatch}
              disabled={!onNextBatch}
              aria-label="看下一組推薦"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M9 18l6-6-6-6" />
              </svg>
            </button>
          </div>
        )}
      </header>
      <div className="um-grid__list">
        {products.map((product, idx) => (
          <ProductCard
            key={`${product.product_id}-${idx}`}
            product={product}
            onSelect={onSelect}
            index={idx}
          />
        ))}
      </div>
    </div>
  );
}

interface DetailViewProps {
  product: Product;
  onBack: () => void;
  /** 啟動 AI 適合度訪談（agent 引導式 Q&A） */
  onStartInterview?: (product: Product) => void;
  onPrev?: () => void;
  onNext?: () => void;
  /** 當前商品在同一批推薦中的位置，僅當 total > 1 時顯示 */
  position?: { index: number; total: number };
}

function formatMonthly(min?: number, max?: number): string | null {
  if (min == null && max == null) return null;
  const round = (n: number) => Math.round(n / 12);
  const fmt = (n: number) => `NT$ ${round(n).toLocaleString('zh-TW')}`;
  if (min != null && max != null && min !== max) {
    return `${fmt(min)} – ${fmt(max)} / 月`;
  }
  return `${fmt((min ?? max) as number)} / 月`;
}

function formatAgeRange(min?: number, max?: number): string | null {
  if (min == null && max == null) return null;
  if (min != null && max != null) {
    if (min === max) return `${min} 歲`;
    return `${min} – ${max} 歲`;
  }
  if (min != null) return `${min} 歲起`;
  return `~ ${max} 歲`;
}

const COVERAGE_SPLIT = /\s*[、,/+＋／]\s*/;

function parseCoverageTags(focus?: string): string[] {
  if (!focus) return [];
  return focus
    .split(COVERAGE_SPLIT)
    .map((s) => s.trim())
    .filter((s) => s.length > 0 && s.length <= 14);
}

function DetailView({
  product,
  onBack,
  onStartInterview,
  onPrev,
  onNext,
  position,
}: DetailViewProps) {
  const detailRef = useRef<HTMLElement>(null);
  const specRef = useRef<HTMLElement>(null);
  const [activeSection, setActiveSection] = useState<string | null>(null);
  const [contactFormOpen, setContactFormOpen] = useState(false);

  // Measure sticky spec height so section scroll-anchoring lands below it,
  // not behind it. Re-measure on resize because some rows are responsive.
  useLayoutEffect(() => {
    const root = detailRef.current;
    const spec = specRef.current;
    if (!root || !spec) return;
    const update = () => {
      root.style.setProperty('--spec-height', `${spec.offsetHeight + 12}px`);
    };
    update();
    window.addEventListener('resize', update);
    return () => window.removeEventListener('resize', update);
  }, []);

  // Active section detection — highlight matching ToC chip as user scrolls
  useEffect(() => {
    const root = detailRef.current;
    if (!root) return;
    const targets = Array.from(
      root.querySelectorAll<HTMLElement>('[data-section]'),
    );
    if (targets.length === 0) return;

    const specHeight = specRef.current?.offsetHeight ?? 280;
    // 把觸發區壓在 sticky spec 之下，並且偏上方一點才算「進入」
    // top: -(spec + 12px gap)、bottom: -50% 讓視野中央的 section 觸發
    const observer = new IntersectionObserver(
      (entries) => {
        // 取目前最靠近視野頂端（但已經進入觸發區）的 section
        const inView = entries
          .filter((e) => e.isIntersecting)
          .map((e) => ({
            key: (e.target as HTMLElement).dataset.section!,
            top: e.boundingClientRect.top,
          }));
        if (inView.length === 0) return;
        inView.sort((a, b) => a.top - b.top);
        setActiveSection(inView[0].key);
      },
      {
        root,
        rootMargin: `-${specHeight + 12}px 0px -55% 0px`,
        threshold: 0,
      },
    );
    targets.forEach((t) => observer.observe(t));
    return () => observer.disconnect();
  }, [product.product_id]);

  const scrollToSection = useCallback((key: string) => {
    const root = detailRef.current;
    if (!root) return;
    const target = root.querySelector<HTMLElement>(`[data-section="${key}"]`);
    target?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    // Optimistic：先把 active 設過去，等 observer 校正
    setActiveSection(key);
  }, []);

  const typeLabel = TYPE_LABEL[product.product_type] ?? '保險商品';
  const fitTone = product.budget_fit
    ? BUDGET_FIT_TONE[product.budget_fit] ?? 'soft'
    : null;
  const fitLabel = product.budget_fit
    ? BUDGET_FIT_LABEL[product.budget_fit] ?? null
    : null;

  const hasPremiumNumber =
    product.annual_premium_min != null || product.annual_premium_max != null;
  const annualDisplay = hasPremiumNumber
    ? formatPremium(product.annual_premium_min, product.annual_premium_max)
    : product.budget_fit_text ?? null;
  const monthlyDisplay = hasPremiumNumber
    ? formatMonthly(product.annual_premium_min, product.annual_premium_max)
    : null;
  const ageRange = formatAgeRange(product.target_age_min, product.target_age_max);
  const coverageTags = parseCoverageTags(product.coverage_focus);
  const showFocusParagraph =
    product.coverage_focus && coverageTags.length === 0;
  const sourceLabel = product.source_tool
    ? SOURCE_TOOL_LABEL[product.source_tool] ?? product.source_tool
    : null;

  type SectionTone = 'reason' | 'caution' | 'meta' | undefined;
  const sections: Array<{
    key: string;
    label: string;
    title: string;
    body: string;
    tone?: SectionTone;
  }> = [];
  if (product.reason) {
    sections.push({
      key: 'reason',
      label: 'WHY · 推薦原因',
      title: '為什麼這個方案適合你',
      body: product.reason,
      tone: 'reason',
    });
  }
  if (product.coverage_summary) {
    sections.push({
      key: 'coverage',
      label: 'COVERAGE · 保障範圍',
      title: '保障內容重點',
      body: product.coverage_summary,
    });
  }
  if (product.terms) {
    sections.push({
      key: 'terms',
      label: 'TERMS · 條款摘要',
      title: '主要條款',
      body: product.terms,
    });
  }
  if (product.reminders) {
    sections.push({
      key: 'reminders',
      label: 'NOTICE · 注意事項',
      title: '投保前提醒',
      body: product.reminders,
      tone: 'caution',
    });
  } else if (product.exclusions) {
    sections.push({
      key: 'exclusions',
      label: 'EXCLUSIONS · 除外條款',
      title: '不在保障範圍內',
      body: product.exclusions,
      tone: 'caution',
    });
  }
  if (product.rules) {
    sections.push({
      key: 'rules',
      label: 'METHODOLOGY · 推薦依據',
      title: 'AI 顧問判斷依據',
      body: product.rules,
      tone: 'meta',
    });
  }

  return (
    <article className="um-detail" ref={detailRef}>
      <button
        type="button"
        className="um-detail__back"
        onClick={onBack}
        aria-label="返回商品列表"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" aria-hidden>
          <path d="M19 12H5" />
          <path d="M11 18l-6-6 6-6" />
        </svg>
        返回列表
      </button>

      <header className="um-detail__header">
        <div className="um-detail__meta-row">
          <span className="um-detail__type">{typeLabel}</span>
          <span className="um-detail__code">
            No. {String(product.product_id).padStart(4, '0')}
          </span>
        </div>
        <h1 className="um-detail__title">{product.product_name}</h1>
        {showFocusParagraph && (
          <p className="um-detail__focus">{product.coverage_focus}</p>
        )}
        {coverageTags.length > 0 && (
          <ul className="um-detail__tags" aria-label="保障重點">
            {coverageTags.map((tag) => (
              <li key={tag} className="um-detail__tag">
                {tag}
              </li>
            ))}
          </ul>
        )}
      </header>

      <section className="um-spec" aria-label="商品規格摘要" ref={specRef}>
        {annualDisplay && (
          <div className="um-spec__row">
            <span className="um-spec__label">年保費</span>
            <span
              className={`um-spec__value ${hasPremiumNumber ? 'um-spec__value--num' : ''}`}
            >
              {annualDisplay}
            </span>
          </div>
        )}
        {monthlyDisplay && (
          <div className="um-spec__row um-spec__row--sub">
            <span className="um-spec__label">每月攤提（估算）</span>
            <span className="um-spec__value um-spec__value--num um-spec__value--muted">
              {monthlyDisplay}
            </span>
          </div>
        )}
        {fitLabel && (
          <div className="um-spec__row">
            <span className="um-spec__label">預算評估</span>
            <span className="um-spec__value">
              <span className={`um-spec__badge um-spec__badge--${fitTone}`}>
                <span className="um-spec__badge-dot" aria-hidden />
                {fitLabel}
              </span>
            </span>
          </div>
        )}
        {product.waiting_period_days != null && (
          <div className="um-spec__row">
            <span className="um-spec__label">等待期</span>
            <span className="um-spec__value um-spec__value--num">
              {product.waiting_period_days}
              <span className="um-spec__unit"> 天</span>
            </span>
          </div>
        )}
        {ageRange && (
          <div className="um-spec__row">
            <span className="um-spec__label">適合投保年齡</span>
            <span className="um-spec__value um-spec__value--num">
              {ageRange}
            </span>
          </div>
        )}
        <div className="um-spec__row">
          <span className="um-spec__label">商品類別</span>
          <span className="um-spec__value">{typeLabel}</span>
        </div>
        {sourceLabel && (
          <div className="um-spec__row um-spec__row--sub">
            <span className="um-spec__label">資料來源</span>
            <span className="um-spec__value um-spec__value--muted">
              {sourceLabel}
            </span>
          </div>
        )}
      </section>

      {sections.length >= 2 && (
        <nav className="um-detail__toc" aria-label="章節導覽">
          {sections.map((sec, i) => {
            const tocLabel =
              sec.label.split(' · ').slice(-1)[0] ?? sec.label;
            const isActive = activeSection === sec.key;
            return (
              <button
                key={sec.key}
                type="button"
                className={`um-detail__toc-chip ${isActive ? 'is-active' : ''}`}
                onClick={() => scrollToSection(sec.key)}
                aria-current={isActive ? 'true' : undefined}
              >
                <span className="um-detail__toc-num" aria-hidden>
                  {String(i + 1).padStart(2, '0')}
                </span>
                {tocLabel}
              </button>
            );
          })}
        </nav>
      )}

      {sections.length > 0 && (
        <div className="um-detail__sections">
          {sections.map((sec, i) => (
            <section
              key={sec.key}
              data-section={sec.key}
              className={`um-detail__section ${sec.tone ? `um-detail__section--${sec.tone}` : ''}`}
            >
              <span className="um-detail__sec-num" aria-hidden>
                {String(i + 1).padStart(2, '0')}
              </span>
              <div className="um-detail__sec-titles">
                <span className="um-detail__sec-label">{sec.label}</span>
                <h2 className="um-detail__sec-title">{sec.title}</h2>
              </div>
              <p className="um-detail__sec-body">{sec.body}</p>
            </section>
          ))}
        </div>
      )}

      <footer className="um-detail__footer">
        <div className="um-detail__actions">
          {onStartInterview && (
            <button
              type="button"
              className="um-detail__cta um-detail__cta--primary"
              onClick={() => onStartInterview(product)}
            >
              <svg
                width="16"
                height="16"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden
              >
                <path d="M12 3l2.09 4.26L19 8l-3.5 3.41L16.18 17 12 14.27 7.82 17l.68-5.59L5 8l4.91-.74L12 3z" />
              </svg>
              讓 AI 顧問評估是否適合你
            </button>
          )}
          <button
            type="button"
            className="um-detail__cta um-detail__cta--secondary"
            onClick={() => setContactFormOpen((v) => !v)}
            aria-expanded={contactFormOpen}
            aria-controls="um-contact-form"
          >
            <svg
              width="15"
              height="15"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden
            >
              <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.91.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92z" />
            </svg>
            {contactFormOpen ? '收起聯繫表單' : '預約真人顧問聯繫'}
          </button>
        </div>

        {contactFormOpen && (
          <ContactRequestForm
            productName={product.product_name}
            productCode={String(product.product_id).padStart(4, '0')}
            onClose={() => setContactFormOpen(false)}
          />
        )}

        {position && position.total > 1 && (
          <nav className="um-detail__pager" aria-label="切換到同批的其他方案">
            <button
              type="button"
              className="um-detail__pager-btn"
              onClick={onPrev}
              disabled={!onPrev}
              aria-label="上一個方案"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <path d="M15 18l-6-6 6-6" />
              </svg>
              <span>上一個</span>
            </button>
            <span className="um-detail__pager-count" aria-live="polite">
              <span className="um-detail__pager-current">
                {String(position.index + 1).padStart(2, '0')}
              </span>
              <span className="um-detail__pager-sep">/</span>
              <span className="um-detail__pager-total">
                {String(position.total).padStart(2, '0')}
              </span>
            </span>
            <button
              type="button"
              className="um-detail__pager-btn"
              onClick={onNext}
              disabled={!onNext}
              aria-label="下一個方案"
            >
              <span>下一個</span>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <path d="M9 18l6-6-6-6" />
              </svg>
            </button>
          </nav>
        )}
        <div className="um-detail__disclaimer" role="note">
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden
          >
            <circle cx="12" cy="12" r="9" />
            <path d="M12 8h.01" />
            <path d="M11 12h1v4h1" />
          </svg>
          <span>
            <strong>本資訊由 AI 顧問依你提供的條件即時整理</strong>
            ，僅供初步參考。實際保障範圍、保費級距與承保結果，依保險公司核保與正式契約為準。
          </span>
        </div>
      </footer>
    </article>
  );
}

interface ContactRequestFormProps {
  productName: string;
  productCode: string;
  onClose: () => void;
}

const CONTACT_TIME_OPTIONS = [
  { value: 'morning', label: '上班日上午 (09:00–12:00)' },
  { value: 'afternoon', label: '上班日下午 (13:00–18:00)' },
  { value: 'evening', label: '上班日晚間 (19:00–21:00)' },
  { value: 'weekend', label: '假日皆可' },
  { value: 'any', label: '隨時都可以' },
];

function ContactRequestForm({
  productName,
  productCode,
  onClose,
}: ContactRequestFormProps) {
  const [name, setName] = useState('');
  const [phone, setPhone] = useState('');
  const [email, setEmail] = useState('');
  const [contactTime, setContactTime] = useState('any');
  const [notes, setNotes] = useState('');
  const [touched, setTouched] = useState<{ name?: boolean; phone?: boolean }>({});
  const [status, setStatus] = useState<'idle' | 'submitting' | 'submitted'>(
    'idle',
  );

  const phoneTrim = phone.trim();
  const nameTrim = name.trim();
  const nameError = touched.name && nameTrim.length < 2 ? '請填寫您的稱呼' : null;
  const phoneError =
    touched.phone && !/^[0-9+\-\s()]{8,}$/.test(phoneTrim)
      ? '請輸入有效的聯絡電話'
      : null;
  const formInvalid =
    nameTrim.length < 2 || !/^[0-9+\-\s()]{8,}$/.test(phoneTrim);

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    setTouched({ name: true, phone: true });
    if (formInvalid || status === 'submitting') return;
    setStatus('submitting');
    // 模擬送出 — 真實情境會 POST 到 CRM / lead-capture endpoint
    window.setTimeout(() => setStatus('submitted'), 700);
  };

  if (status === 'submitted') {
    return (
      <section
        id="um-contact-form"
        className="um-contact um-contact--submitted"
        aria-live="polite"
      >
        <div className="um-contact__success-icon" aria-hidden>
          <svg
            width="22"
            height="22"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.4"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M5 12l5 5L20 7" />
          </svg>
        </div>
        <h3 className="um-contact__success-title">已送出聯繫請求</h3>
        <p className="um-contact__success-body">
          指定顧問將於 24 小時內主動聯繫您，討論「{productName}」的細節。
          您也會收到一封確認信件，請留意信箱。
        </p>
        <button
          type="button"
          className="um-contact__close-btn"
          onClick={onClose}
        >
          知道了
        </button>
      </section>
    );
  }

  return (
    <section
      id="um-contact-form"
      className="um-contact"
      aria-label="聯絡業務顧問表單"
    >
      <header className="um-contact__head">
        <div>
          <span className="um-contact__eyebrow">REQUEST CALLBACK</span>
          <h3 className="um-contact__title">預約真人顧問聯繫</h3>
        </div>
        <button
          type="button"
          className="um-contact__close"
          onClick={onClose}
          aria-label="關閉表單"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" aria-hidden>
            <path d="M18 6L6 18M6 6l12 12" />
          </svg>
        </button>
      </header>

      <div className="um-contact__product">
        <span className="um-contact__product-label">您正在詢問</span>
        <span className="um-contact__product-name">{productName}</span>
        <span className="um-contact__product-code">No. {productCode}</span>
      </div>

      <form className="um-contact__form" onSubmit={handleSubmit} noValidate>
        <label className="um-contact__field">
          <span className="um-contact__label">
            稱呼<span aria-hidden className="um-contact__required">*</span>
          </span>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onBlur={() => setTouched((t) => ({ ...t, name: true }))}
            autoComplete="name"
            placeholder="王小明"
            aria-invalid={!!nameError}
            aria-describedby={nameError ? 'um-contact-name-error' : undefined}
          />
          {nameError && (
            <span
              id="um-contact-name-error"
              className="um-contact__error"
              role="alert"
            >
              {nameError}
            </span>
          )}
        </label>

        <label className="um-contact__field">
          <span className="um-contact__label">
            聯絡電話<span aria-hidden className="um-contact__required">*</span>
          </span>
          <input
            type="tel"
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
            onBlur={() => setTouched((t) => ({ ...t, phone: true }))}
            autoComplete="tel"
            placeholder="09xx-xxx-xxx"
            inputMode="tel"
            aria-invalid={!!phoneError}
            aria-describedby={phoneError ? 'um-contact-phone-error' : undefined}
          />
          {phoneError && (
            <span
              id="um-contact-phone-error"
              className="um-contact__error"
              role="alert"
            >
              {phoneError}
            </span>
          )}
        </label>

        <label className="um-contact__field">
          <span className="um-contact__label">Email（選填）</span>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="email"
            placeholder="you@example.com"
          />
        </label>

        <label className="um-contact__field">
          <span className="um-contact__label">偏好聯絡時段</span>
          <select
            value={contactTime}
            onChange={(e) => setContactTime(e.target.value)}
          >
            {CONTACT_TIME_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </label>

        <label className="um-contact__field um-contact__field--full">
          <span className="um-contact__label">想優先了解的部分（選填）</span>
          <textarea
            value={notes}
            autoComplete="off"
            data-1p-ignore=""
            data-lpignore="true"
            onChange={(e) => setNotes(e.target.value)}
            rows={3}
            placeholder="例如：實支實付理賠範圍、家族病史是否影響核保…"
          />
        </label>

        <div className="um-contact__footer">
          <p className="um-contact__privacy">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M12 2L3 6.5v5c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12v-5L12 2z" />
              <path d="M9 12l2 2 4-4" />
            </svg>
            您的資料僅用於本次聯繫，不會分享給第三方。
          </p>
          <button
            type="submit"
            className="um-contact__submit"
            disabled={status === 'submitting'}
          >
            {status === 'submitting' ? '送出中…' : '送出聯繫請求'}
          </button>
        </div>
      </form>
    </section>
  );
}
