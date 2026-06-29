'use client';

import {
  Dispatch,
  FormEvent,
  SetStateAction,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';

import { useLiveAgent, type LiveStatus } from '../hooks/useLiveAgent';
import { useSession, signOut } from 'next-auth/react';

import {
  initialSessions,
  simulateAgentTurn,
  type ChatMessage,
  type SessionRecord,
  type TimelineEvent,
} from '../lib/mock-data';
import { renderMarkdown } from '../lib/markdown';
import { consumeProxyStream, formatClock } from '../lib/proxy-stream';
import {
  getStoredUserModeSessionId,
  loadSessionHistory,
  pruneSessionHistory,
  removeSessionHistory,
  saveSessionHistory,
  setStoredUserModeSessionId,
} from '../lib/session-storage';
import { handleAuthExpired, isUnauthorized } from '../lib/auth-recovery';
import { ModeSwitch, type AppMode } from './ModeSwitch';
import { parseStateValue, StateTreeNode } from './StateTree';
import {
  groupStreamEvents,
  StateGroupNode,
  StreamGroupNode,
  TranscriptionGroupNode,
} from './TimelineNodes';
import { WaveformVisualizer } from './WaveformVisualizer';
import { CameraPreview } from './CameraPreview';
import { ScreenPreview } from './ScreenPreview';
import { InsuranceCard } from './InsuranceCard';
import { splitRecommendationSegments } from '../lib/recommendation-segments';

type InspectorTab = 'events' | 'state';

const APP_NAME = process.env.NEXT_PUBLIC_APP_NAME ?? 'app';

function getOrCreateUserId(): string {
  const key = 'ins-agent:userId';
  try {
    const stored = localStorage.getItem(key);
    if (stored) return stored;
    const id = `user-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
    localStorage.setItem(key, id);
    return id;
  } catch {
    return 'anonymous';
  }
}

type ChatItem =
  | { kind: 'message'; message: ChatMessage }
  | { kind: 'tool-event'; event: TimelineEvent };

interface ProxyRunResponse {
  events: TimelineEvent[];
  finalText: string;
  state: Record<string, string>;
}

type ProxyStreamEnvelope =
  | {
      type: 'meta';
      transport: 'proxy';
      notice: string;
    }
  | {
      type: 'timeline';
      event: TimelineEvent;
    }
  | {
      type: 'state';
      patch: Record<string, string>;
    }
  | {
      type: 'message';
      text: string;
      mode: 'append' | 'replace';
      final: boolean;
    }
  | {
      type: 'done';
      finalText: string;
      state: Record<string, string>;
    }
  | {
      type: 'error';
      message: string;
    };

export interface AdkWorkbenchProps {
  mode?: AppMode;
  onSwitchMode?: (next: AppMode) => void;
}

export function AdkWorkbench({ mode, onSwitchMode }: AdkWorkbenchProps = {}) {
  const { data: session } = useSession();
  const accessToken = (session as any)?.accessToken;

  const [sessions, setSessions] = useState(initialSessions);
  const [activeSessionId, setActiveSessionId] = useState(initialSessions[0].id);
  const [selectedEventId, setSelectedEventId] = useState(
    initialSessions[0].events[0].id,
  );
  const [draft, setDraft] = useState('');
  const [attachedImage, setAttachedImage] = useState<{
    base64: string;
    type: string;
  } | null>(null);
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>('events');
  const [pending, setPending] = useState(false);
  const [transportMode, setTransportMode] = useState<
    'proxy' | 'mock' | 'standby'
  >('standby');
  const [runtimeNotice, setRuntimeNotice] = useState<string | null>(null);

  const [sidebarW, setSidebarW] = useState(240);
  const [inspectorW, setInspectorW] = useState(320);

  const [userId, setUserId] = useState(session?.user?.name || 'anonymous');

  const inputTransAccumulator = useRef('');
  const outputTransAccumulator = useRef('');

  useEffect(() => {
    if (session?.user?.name) {
      setUserId(session.user.name);
    }
  }, [session]);

  const [isLiveModeEnabled, setIsLiveModeEnabled] = useState(false);
  const [proactivity, setProactivity] = useState(false);
  const [affectiveDialog, setAffectiveDialog] = useState(false);

  const onLiveEvent = useCallback(
    (event: any) => {
      // 處理來自 Live API 的事件
      const seed = `${Date.now()}-${Math.random().toString(36).substring(2, 9)}`;
      const timestamp = formatClock();

      // 輔助函式：移除中文字符之間的空格（STT 轉錄常見問題）
      const cleanTranscription = (text: string) => {
        return text.replace(/([\u4e00-\u9fa5])\s+([\u4e00-\u9fa5])/g, '$1$2');
      };

      if (event.content?.parts) {
        const textParts = event.content.parts
          .filter((p: any) => p.text)
          .map((p: any) => p.text);
        if (textParts.length > 0) {
          // 採用累積策略：對 content.parts 進行累加顯示
          const combinedText = cleanTranscription(textParts.join(''));
          setSessions((currentSessions) =>
            currentSessions.map((session) => {
              if (session.id !== activeSessionId) return session;

              const lastMsg = session.messages.at(-1);
              if (
                lastMsg &&
                lastMsg.role === 'agent' &&
                lastMsg.status === 'streaming'
              ) {
                return {
                  ...session,
                  messages: session.messages.map((m, idx) =>
                    idx === session.messages.length - 1
                      ? { ...m, text: m.text + combinedText } // 累加方式
                      : m,
                  ),
                };
              } else {
                return {
                  ...session,
                  messages: [
                    ...session.messages,
                    {
                      id: `msg-agent-live-${seed}`,
                      role: 'agent',
                      text: combinedText,
                      timestamp,
                      status: 'streaming',
                    },
                  ],
                };
              }
            }),
          );
        }
      }

      if (event.turnComplete) {
        const inputToFlush = inputTransAccumulator.current;
        inputTransAccumulator.current = '';
        const outputToFlush = outputTransAccumulator.current;
        outputTransAccumulator.current = '';

        setSessions((currentSessions) =>
          currentSessions.map((session) => {
            if (session.id !== activeSessionId) return session;

            let nextEvents = [...session.events];
            if (inputToFlush) {
              nextEvents.push({
                id: `evt-user-trans-flush-${seed}`,
                kind: 'user',
                title: 'input_transcription',
                summary: inputToFlush,
                timestamp,
                payload: ['source: voice (final)'],
              });
            }
            if (outputToFlush) {
              nextEvents.push({
                id: `evt-agent-trans-flush-${seed}`,
                kind: 'agent',
                title: 'output_transcription',
                summary: outputToFlush,
                timestamp,
                payload: ['source: model voice (final)'],
              });
            }

            return {
              ...session,
              messages: session.messages.map((m) => {
                if (m.status === 'streaming') {
                  if (m.role === 'agent') {
                    return { ...m, text: outputToFlush || m.text, status: 'final' };
                  } else if (m.role === 'user') {
                    return { ...m, text: inputToFlush ? `${inputToFlush}` : m.text, status: 'final' };
                  }
                }
                return m;
              }),
              events: nextEvents,
            };
          }),
        );
      }

      // 處理轉錄 (Transcription)
      if (event.inputTranscription?.text) {
        const transText = cleanTranscription(event.inputTranscription.text);
        const isFinished = event.inputTranscription.finished === true;

        if (isFinished) {
          inputTransAccumulator.current = transText;
        } else {
          inputTransAccumulator.current += transText;
        }

        setSessions((currentSessions) =>
          currentSessions.map((session) => {
            if (session.id !== activeSessionId) return session;

            const lastMsg = session.messages.at(-1);
            if (
              lastMsg &&
              lastMsg.role === 'user' &&
              lastMsg.id.startsWith('msg-user-trans-')
            ) {
              return {
                ...session,
                messages: session.messages.map((m, idx) =>
                  idx === session.messages.length - 1
                    ? { ...m, text: isFinished ? `${transText}` : m.text + transText }
                    : m,
                ),
              };
            } else {
              return {
                ...session,
                messages: [
                  ...session.messages,
                  {
                    id: `msg-user-trans-${seed}`,
                    role: 'user',
                    text: `${transText}`,
                    timestamp,
                    status: isFinished ? 'final' : 'streaming',
                  },
                ],
              };
            }
          }),
        );
      }

      if (event.outputTranscription?.text) {
        const transText = cleanTranscription(event.outputTranscription.text);
        const isFinished = event.outputTranscription.finished === true;

        if (isFinished) {
          outputTransAccumulator.current = transText;
        } else {
          outputTransAccumulator.current += transText;
        }

        setSessions((currentSessions) =>
          currentSessions.map((session) => {
            if (session.id !== activeSessionId) return session;

            const lastMsg = session.messages.at(-1);
            if (
              lastMsg &&
              lastMsg.role === 'agent' &&
              lastMsg.status === 'streaming'
            ) {
              return {
                ...session,
                messages: session.messages.map((m, idx) =>
                  idx === session.messages.length - 1
                    ? { ...m, text: isFinished ? transText : m.text + transText }
                    : m,
                ),
              };
            } else {
              return {
                ...session,
                messages: [
                  ...session.messages,
                  {
                    id: `msg-agent-live-trans-${seed}`,
                    role: 'agent',
                    text: transText,
                    timestamp,
                    status: 'streaming',
                  },
                ],
              };
            }
          }),
        );
      }

      // 處理工具呼叫
      const functionCalls =
        event.content?.parts?.map((p: any) => p.functionCall).filter(Boolean) ||
        [];
      if (functionCalls.length > 0) {
        const inputToFlush = inputTransAccumulator.current;
        inputTransAccumulator.current = '';

        setSessions((currentSessions) =>
          currentSessions.map((session) => {
            if (session.id !== activeSessionId) return session;

            let nextEvents = [...session.events];
            if (inputToFlush) {
              nextEvents.push({
                id: `evt-user-trans-flush-call-${seed}`,
                kind: 'user',
                title: 'input_transcription',
                summary: inputToFlush,
                timestamp,
                payload: ['source: voice (final)'],
              });
            }

            const callEvents: TimelineEvent[] = functionCalls.map(
              (fc: any, i: number) => ({
                id: `evt-call-${seed}-${i}`,
                kind: 'tool-call',
                title: fc.name,
                summary: `[Live] Agent 請求執行工具 ${fc.name}`,
                timestamp,
                payload: [`args: ${JSON.stringify(fc.args)}`],
              }),
            );

            return {
              ...session,
              events: [...nextEvents, ...callEvents],
            };
          }),
        );
      }

      // 處理工具結果
      const functionResponses =
        event.content?.parts
          ?.map((p: any) => p.functionResponse)
          .filter(Boolean) || [];
      if (functionResponses.length > 0) {
        setSessions((currentSessions) =>
          currentSessions.map((session) => {
            if (session.id !== activeSessionId) return session;
            const newEvents: TimelineEvent[] = functionResponses.map(
              (fr: any, i: number) => ({
                id: `evt-resp-${seed}-${i}`,
                kind: 'tool-result',
                title: `${fr.name} result`,
                summary: `[Live] 工具執行結果已回傳`,
                timestamp,
                payload: [`response: ${JSON.stringify(fr.response)}`],
              }),
            );
            return {
              ...session,
              events: [...session.events, ...newEvents],
            };
          }),
        );
      }

      // 處理狀態更新
      if (event.actions?.stateDelta) {
        setSessions((currentSessions) =>
          currentSessions.map((session) => {
            if (session.id !== activeSessionId) return session;
            return {
              ...session,
              state: { ...session.state, ...event.actions.stateDelta },
              events: [
                ...session.events,
                {
                  id: `evt-state-${seed}`,
                  kind: 'state',
                  title: 'stateDelta',
                  summary: '[Live] Session 狀態已更新',
                  timestamp,
                  payload: Object.entries(event.actions.stateDelta).map(
                    ([k, v]) => `${k}: ${v}`,
                  ),
                },
              ],
            };
          }),
        );
      }
    },
    [activeSessionId, setSessions],
  );

  const {
    status: liveStatus,
    connect: connectLive,
    disconnect: disconnectLive,
    sendText: sendLiveText,
    sendImage: sendLiveImage,
    isMicEnabled,
    setIsMicEnabled,
    isCameraEnabled,
    setIsCameraEnabled,
    isScreenEnabled,
    setIsScreenEnabled,
    toggleScreen,
    isSpeaking: agentIsSpeaking,
    cameraStream,
    screenStream,
    initPlayback,
  } = useLiveAgent({
    sessionId: activeSessionId,
    userId,
    proactivity,
    affectiveDialog,
    onEvent: onLiveEvent,
    onError: (err) => setRuntimeNotice(`Live Mode Error: ${err}`),
  });

  const [showCameraNotice, setShowCameraNotice] = useState(false);

  useEffect(() => {
    if (isCameraEnabled) {
      setShowCameraNotice(true);
      const timer = setTimeout(() => setShowCameraNotice(false), 3000);
      return () => clearTimeout(timer);
    }
  }, [isCameraEnabled]);

  useEffect(() => {
    if (isLiveModeEnabled) {
      connectLive();
    } else {
      disconnectLive();
      // 當 Live Mode 關閉時，強制將 Proactivity 與 Affective Dialog 設為 false
      setProactivity(false);
      setAffectiveDialog(false);
    }
  }, [isLiveModeEnabled, connectLive, disconnectLive]);

  useEffect(() => {
    setSidebarW(
      Math.round(Math.max(180, Math.min(400, window.innerWidth * 0.18))),
    );
    setInspectorW(
      Math.round(Math.max(200, Math.min(480, window.innerWidth * 0.24))),
    );
  }, []);

  // 從 ADK 載入 session 清單，合併 localStorage 的歷史訊息
  useEffect(() => {
    if (userId === 'anonymous') return;
    fetch(
      `/api/apps/${APP_NAME}/users/${encodeURIComponent(userId)}/sessions`,
      {
        cache: 'no-store',
        headers: accessToken ? { 'Authorization': `Bearer ${accessToken}` } : {},
      },
    )
      .then((res) => {
        if (isUnauthorized(res.status)) handleAuthExpired();
        return res.ok ? res.json() : Promise.reject();
      })
      .then((data: { sessions: SessionRecord[] }) => {
        if (!Array.isArray(data.sessions) || data.sessions.length === 0) return;
        const merged = data.sessions.map((s) => {
          const history = loadSessionHistory(s.id);
          return history
            ? {
                ...s,
                messages: history.messages,
                events: history.events,
                _lsTs: history.updatedTs,
              }
            : { ...s, _lsTs: 0 };
        });
        // 以 localStorage updatedTs 重新排序（同為 0 時保持後端順序）
        merged.sort((a, b) => b._lsTs - a._lsTs);
        const sorted = merged.map(
          ({ _lsTs: _, ...rest }) => rest,
        ) as SessionRecord[];
        setSessions(sorted);

        // 若 User Mode 正在用某個 session，優先選那個（讓「User Mode 對話到一半切過來」直接看到同一段）
        const userModeSessionId = getStoredUserModeSessionId();
        const target = userModeSessionId
          ? sorted.find((s) => s.id === userModeSessionId) ?? sorted[0]
          : sorted[0];
        setActiveSessionId(target.id);
        setSelectedEventId(target.events.at(-1)?.id ?? '');
      })
      .catch(() => {
        // ADK 離線 — 保留 initialSessions 作為示範資料
      });
  }, [userId]);

  // 進場時清掉過期 / 超量的舊對話歷史。保護 User Mode 當前用的那個 session 不被淘汰。
  useEffect(() => {
    const userModeSessionId = getStoredUserModeSessionId();
    pruneSessionHistory(userModeSessionId ? [userModeSessionId] : []);
    // 只在首次 mount 跑一次即可
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // sessions 更新且非處理中時，自動持久化歷史訊息到 localStorage。
  // 用便宜的簽章 (final 訊息數 / events 數 / 最後一則 id+長度) 比對，只重寫真的有變動的那筆，
  // 避免每次 render 都把所有 session 全量 JSON.stringify + setItem（會卡主執行緒）。
  const savedSigRef = useRef<Map<string, string>>(new Map());
  useEffect(() => {
    if (pending) return;
    sessions.forEach((s) => {
      if (s.messages.length === 0 && s.events.length === 0) return;
      const finalMsgs = s.messages.filter((m) => m.status === 'final');
      const last = finalMsgs.at(-1);
      const sig = `${finalMsgs.length}:${s.events.length}:${last?.id ?? ''}:${last?.text.length ?? 0}`;
      if (savedSigRef.current.get(s.id) === sig) return;
      savedSigRef.current.set(s.id, sig);
      saveSessionHistory(s.id, s.messages, s.events);
    });
  }, [sessions, pending]);
  const dragging = useRef<'left' | 'right' | null>(null);
  const startX = useRef(0);
  const startW = useRef(0);
  const chatEndRef = useRef<HTMLDivElement>(null);

  const onDragStart = useCallback(
    (side: 'left' | 'right') => (e: React.MouseEvent) => {
      e.preventDefault();
      dragging.current = side;
      startX.current = e.clientX;
      startW.current = side === 'left' ? sidebarW : inspectorW;

      const onMove = (ev: MouseEvent) => {
        const delta = ev.clientX - startX.current;
        if (dragging.current === 'left') {
          setSidebarW(Math.max(180, Math.min(500, startW.current + delta)));
        } else {
          setInspectorW(Math.max(200, Math.min(600, startW.current - delta)));
        }
      };

      const onUp = () => {
        dragging.current = null;
        window.removeEventListener('mousemove', onMove);
        window.removeEventListener('mouseup', onUp);
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
      };

      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
      window.addEventListener('mousemove', onMove);
      window.addEventListener('mouseup', onUp);
    },
    [sidebarW, inspectorW],
  );

  const activeSession = useMemo(
    () =>
      sessions.find((session) => session.id === activeSessionId) ?? sessions[0],
    [activeSessionId, sessions],
  );

  const selectedEvent =
    activeSession.events.find((event) => event.id === selectedEventId) ??
    activeSession.events.at(-1) ??
    null;

  const chatItems = useMemo((): ChatItem[] => {
    const toolEvents = activeSession.events.filter(
      (e) => e.kind === 'tool-call' || e.kind === 'tool-result',
    );

    if (toolEvents.length === 0) {
      return activeSession.messages.map((m) => ({
        kind: 'message' as const,
        message: m,
      }));
    }

    const items: ChatItem[] = [];
    let eventIdx = 0;

    for (const msg of activeSession.messages) {
      // Find all events that happened before this message
      while (eventIdx < toolEvents.length) {
        const evt = toolEvents[eventIdx];
        // If they have the same timestamp, we prefer showing the user message first,
        // then the tool events, and finally the agent message.
        const shouldPushEventBeforeMessage =
          evt.timestamp < msg.timestamp ||
          (evt.timestamp === msg.timestamp && msg.role === 'agent');

        if (shouldPushEventBeforeMessage) {
          items.push({ kind: 'tool-event', event: evt });
          eventIdx++;
        } else {
          break;
        }
      }
      items.push({ kind: 'message', message: msg });
    }

    // Push remaining events
    while (eventIdx < toolEvents.length) {
      items.push({ kind: 'tool-event', event: toolEvents[eventIdx] });
      eventIdx++;
    }

    return items;
  }, [activeSession.messages, activeSession.events]);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [chatItems, pending]);

  function handleNewSession() {
    const seed = Date.now();
    const newSession: SessionRecord = {
      id: `session-${seed}`,
      title: '新對話',
      subtitle: '開始新的對話',
      status: 'idle',
      updatedAt: '剛剛',
      messages: [],
      events: [],
      state: {},
    };
    setSessions((prev) => [newSession, ...prev]);
    setActiveSessionId(newSession.id);
    setSelectedEventId('');

    // 點選新增對話時，預設關閉 Live Mode 與麥克風、Proactivity 與 Affective Dialog
    setIsLiveModeEnabled(false);
    setIsMicEnabled(false);
    setProactivity(false);
    setAffectiveDialog(false);

    // 在 ADK 建立 session（fire and forget）
    fetch(
      `/api/apps/${APP_NAME}/users/${encodeURIComponent(userId)}/sessions`,
      {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          ...(accessToken ? { 'Authorization': `Bearer ${accessToken}` } : {}),
        },
        body: JSON.stringify({
          sessionId: newSession.id,
          state: {
            _ui_title: newSession.title,
            _ui_subtitle: newSession.subtitle,
          },
        }),
      },
    ).catch(() => {
      /* ADK 離線，靜默忽略 */
    });
  }

  function handleSessionChange(sessionId: string) {
    const nextSession = sessions.find((session) => session.id === sessionId);

    if (!nextSession) {
      return;
    }

    // 點選其他對話時，預設關閉 Live Mode 與麥克風
    setIsLiveModeEnabled(false);
    setIsMicEnabled(false);

    // 若 in-memory 是空的，嘗試從 localStorage 還原歷史訊息
    if (nextSession.messages.length === 0) {
      const history = loadSessionHistory(sessionId);
      if (history && history.messages.length > 0) {
        setSessions((prev) =>
          prev.map((s) =>
            s.id === sessionId
              ? { ...s, messages: history.messages, events: history.events }
              : s,
          ),
        );
        setActiveSessionId(sessionId);
        setSelectedEventId(history.events.at(-1)?.id ?? '');
        return;
      }
    }

    setActiveSessionId(sessionId);
    setSelectedEventId(
      nextSession.events.at(-1)?.id ?? nextSession.events[0]?.id ?? '',
    );
  }

  function handleDeleteSession(e: React.MouseEvent, sessionId: string) {
    e.stopPropagation();

    // 刪除對話且可能切換對話時，關閉 Live Mode 與麥克風
    if (sessionId === activeSessionId || sessions.length === 1) {
      setIsLiveModeEnabled(false);
      setIsMicEnabled(false);
    }

    setSessions((prev) => {
      const next = prev.filter((s) => s.id !== sessionId);

      // 如果刪除後列表變為空，則需要建立新對話
      if (next.length === 0) {
        const seed = Date.now();
        const newSession: SessionRecord = {
          id: `session-${seed}`,
          title: '新對話',
          subtitle: '開始新的對話',
          status: 'idle',
          updatedAt: '剛剛',
          messages: [],
          events: [],
          state: {},
        };

        // 在 setSessions 外部處理副作用
        setTimeout(() => {
          setActiveSessionId(newSession.id);
          setSelectedEventId('');
          fetch(
            `/api/apps/${APP_NAME}/users/${encodeURIComponent(userId)}/sessions`,
            {
              method: 'POST',
              headers: { 
                'Content-Type': 'application/json',
                ...(accessToken ? { 'Authorization': `Bearer ${accessToken}` } : {}),
              },
              body: JSON.stringify({
                sessionId: newSession.id,
                state: {
                  _ui_title: newSession.title,
                  _ui_subtitle: newSession.subtitle,
                },
              }),
            },
          ).catch(() => {});
        }, 0);

        return [newSession];
      }

      // 如果刪除的是當前活躍對話，切換到列表中的第一個
      if (sessionId === activeSessionId) {
        const fallback = next[0];
        setActiveSessionId(fallback.id);
        setSelectedEventId(
          fallback.events.at(-1)?.id ?? fallback.events[0]?.id ?? '',
        );
      }
      return next;
    });
    // 清除 localStorage 歷史紀錄
    removeSessionHistory(sessionId);
    // 從 ADK 刪除 session（fire and forget）
    fetch(
      `/api/apps/${APP_NAME}/users/${encodeURIComponent(userId)}/sessions/${encodeURIComponent(sessionId)}`,
      {
        method: 'DELETE',
        headers: accessToken ? { 'Authorization': `Bearer ${accessToken}` } : {},
      },
    ).catch(() => {
      /* ADK 離線，靜默忽略 */
    });
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const prompt = draft.trim();

    if ((!prompt && !attachedImage) || pending) {
      return;
    }

    const seed = Date.now();
    const timestamp = formatClock();

    const userMessage: ChatMessage = {
      id: `msg-user-${seed}`,
      role: 'user',
      text: prompt,
      timestamp,
      status: 'final',
      image: attachedImage?.base64,
      imageType: attachedImage?.type,
    };

    const userEvent: TimelineEvent = {
      id: `evt-user-${seed}`,
      kind: 'user',
      title: 'user_message',
      summary: prompt || '上傳圖片',
      timestamp,
      payload: [
        'author: user',
        `invocation_id: inv-${seed}`,
        attachedImage ? `image: ${attachedImage.type}` : 'image: none',
      ],
    };

    setDraft('');
    const currentImage = attachedImage;
    setAttachedImage(null);
    setPending(true);
    setRuntimeNotice(null);

    setSessions((currentSessions) =>
      currentSessions.map((session) => {
        if (session.id !== activeSessionId) {
          return session;
        }

        return {
          ...session,
          status: 'pending',
          updatedAt: '剛剛',
          subtitle:
            prompt.length > 40
              ? `${prompt.slice(0, 40)}…`
              : prompt || '上傳圖片',
          messages: [...session.messages, userMessage],
          events: [...session.events, userEvent],
        };
      }),
    );
    setSelectedEventId(userEvent.id);

    if (isLiveModeEnabled && liveStatus === 'connected') {
      if (currentImage) {
        sendLiveImage(currentImage.base64, currentImage.type, prompt || '請幫我分析這張圖片內容');
      } else if (prompt) {
        sendLiveText(prompt);
      }
      setPending(false);
      return;
    }

    let streamedAnyEnvelope = false;

    try {
      const response = await fetch('/api/agent/run', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(accessToken ? { 'Authorization': `Bearer ${accessToken}` } : {}),
        },
        body: JSON.stringify({
          prompt: prompt || '請幫我分析這張圖片內容',
          sessionId: activeSessionId,
          userId,
          sessionState: {
            ...activeSession.state,
            'config:proactive_enabled': String(proactivity),
            'config:affective_enabled': String(affectiveDialog),
            _ui_title: activeSession.title,
            _ui_subtitle:
              prompt.length > 40
                ? `${prompt.slice(0, 40)}…`
                : prompt || '上傳圖片',
          },
          image: currentImage?.base64,
          imageType: currentImage?.type,
        }),
      });

      if (!response.ok) {
        if (isUnauthorized(response.status)) {
          handleAuthExpired();
          throw new Error('登入已過期，正在帶你重新登入…');
        }
        const payload = (await response.json().catch(() => null)) as {
          error?: string;
        } | null;

        throw new Error(payload?.error ?? 'ADK proxy request failed');
      }

      const contentType = response.headers.get('content-type') ?? '';

      if (!contentType.includes('text/event-stream') || !response.body) {
        const payload = (await response.json()) as ProxyRunResponse;
        const agentEvent = payload.events.at(-1);

        setSessions((currentSessions) =>
          currentSessions.map((session) => {
            if (session.id !== activeSessionId) {
              return session;
            }

            return {
              ...session,
              status: 'live',
              updatedAt: '剛剛',
              state: {
                ...session.state,
                ...payload.state,
              },
              messages: [
                ...session.messages,
                {
                  id: `msg-agent-${seed}`,
                  role: 'agent',
                  text:
                    payload.finalText ||
                    'ADK runtime 已完成執行，請查看右側 event history。',
                  timestamp: formatClock(),
                  status: 'final',
                },
              ],
              events: [...session.events, ...payload.events],
            };
          }),
        );

        setTransportMode('proxy');
        setRuntimeNotice('目前由 Next.js API route 代理到 ADK API server。');
        setSelectedEventId(agentEvent?.id ?? userEvent.id);
        setPending(false);
        return;
      }

      setTransportMode('proxy');
      await consumeProxyStream({
        response,
        seed,
        sessionId: activeSessionId,
        fallbackEventId: userEvent.id,
        setSessions,
        setSelectedEventId,
        setRuntimeNotice,
        onEnvelope: () => {
          streamedAnyEnvelope = true;
        },
      });

      setPending(false);
      return;
    } catch (error) {
      if (streamedAnyEnvelope) {
        setPending(false);
        setRuntimeNotice(
          error instanceof Error
            ? `SSE 串流中途中斷，已保留目前收到的事件。${error.message}`
            : 'SSE 串流中途中斷，已保留目前收到的事件。',
        );
        return;
      }

      setTransportMode('mock');
      setRuntimeNotice(
        error instanceof Error
          ? `ADK proxy 無法連線，已改用 mock fallback。${error.message}`
          : 'ADK proxy 無會連線，已改用 mock fallback。',
      );
    }

    const simulated = simulateAgentTurn(
      prompt || '上傳圖片',
      activeSession.state,
    );
    const toolCallEvent: TimelineEvent = {
      id: `evt-call-${seed}`,
      kind: 'tool-call',
      title: simulated.toolName,
      summary: `Agent 以結構化方式請求 ${simulated.toolName}`,
      timestamp,
      payload: [`args: ${JSON.stringify(simulated.toolArgs)}`],
    };

    const streamMessageId = `msg-agent-stream-${seed}`;
    const streamEventId = `evt-stream-${seed}`;
    const resultEventId = `evt-result-${seed}`;
    const stateEventId = `evt-state-${seed}`;
    const finalEventId = `evt-final-${seed}`;

    setSessions((currentSessions) =>
      currentSessions.map((session) => {
        if (session.id !== activeSessionId) {
          return session;
        }

        return {
          ...session,
          status: 'pending',
          updatedAt: '剛剛',
          events: [...session.events, toolCallEvent],
        };
      }),
    );
    setSelectedEventId(toolCallEvent.id);

    window.setTimeout(() => {
      setSessions((currentSessions) =>
        currentSessions.map((session) => {
          if (session.id !== activeSessionId) {
            return session;
          }

          return {
            ...session,
            status: 'pending',
            messages: [
              ...session.messages,
              {
                id: streamMessageId,
                role: 'agent',
                text: simulated.streamText,
                timestamp: formatClock(),
                status: 'streaming',
              },
            ],
            events: [
              ...session.events,
              {
                id: resultEventId,
                kind: 'tool-result',
                title: `${simulated.toolName} result`,
                summary: simulated.toolSummary,
                timestamp: formatClock(),
                payload: [
                  `top_product: ${simulated.productName}`,
                  `state_patch_keys: ${Object.keys(simulated.statePatch).join(', ')}`,
                ],
              },
              {
                id: streamEventId,
                kind: 'stream',
                title: 'partial_response',
                summary: '模型開始串流回覆內容',
                timestamp: formatClock(),
                payload: ['partial: true', simulated.streamText],
              },
            ],
          };
        }),
      );
      setSelectedEventId(streamEventId);
    }, 420);

    window.setTimeout(() => {
      setSessions((currentSessions) =>
        currentSessions.map((session) => {
          if (session.id !== activeSessionId) {
            return session;
          }

          return {
            ...session,
            status: 'live',
            updatedAt: '剛剛',
            state: {
              ...session.state,
              ...simulated.statePatch,
            },
            messages: session.messages.map((message) =>
              message.id === streamMessageId
                ? {
                    ...message,
                    text: simulated.finalText,
                    status: 'final',
                    timestamp: formatClock(),
                  }
                : message,
            ),
            events: [
              ...session.events,
              {
                id: stateEventId,
                kind: 'state',
                title: 'state_delta',
                summary: '將本輪整理出的條件與最近一次推薦寫回 session',
                timestamp: formatClock(),
                payload: Object.entries(simulated.statePatch).map(
                  ([key, value]) => `${key}: ${value}`,
                ),
              },
              {
                id: finalEventId,
                kind: 'agent',
                title: 'final_response',
                summary: '完成可顯示的 agent response',
                timestamp: formatClock(),
                payload: ['is_final_response: true', 'turn_complete: true'],
              },
            ],
          };
        }),
      );

      setSelectedEventId(finalEventId);
      setPending(false);
    }, 980);
  }

  return (
    <main className='workbench'>
      <div className='workbench__frame'>
        {showCameraNotice && (
          <div className='camera-notice'>
            <div className='camera-notice__content'>
              <svg
                viewBox='0 0 24 24'
                fill='none'
                stroke='currentColor'
                strokeWidth='2'
                width='20'
                height='20'
              >
                <path d='M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z' />
                <circle cx='12' cy='13' r='4' />
              </svg>
              <span>Opening camera...</span>
            </div>
          </div>
        )}

        {isCameraEnabled && cameraStream && (
          <div className='camera-preview-overlay'>
            <CameraPreview
              stream={cameraStream}
              onConfirm={(base64, type) => {
                setAttachedImage({ base64, type });
                setIsCameraEnabled(false);
              }}
              onCancel={() => setIsCameraEnabled(false)}
            />
          </div>
        )}

        {isScreenEnabled && screenStream && (
          <ScreenPreview
            stream={screenStream}
            onStop={() => {
              toggleScreen();
            }}
          />
        )}

        <aside
          className='pane sidebar'
          style={{ width: sidebarW, minWidth: sidebarW }}
        >
          <div className='brand'>
            <div className='brand__header'>
              <div className='brand__logo' aria-hidden='true'>
                <svg
                  viewBox='0 0 24 24'
                  fill='none'
                  xmlns='http://www.w3.org/2000/svg'
                >
                  <path
                    d='M12 2L3 6.5v5c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12v-5L12 2z'
                    fill='currentColor'
                    opacity='0.18'
                  />
                  <path
                    d='M12 2L3 6.5v5c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12v-5L12 2z'
                    stroke='currentColor'
                    strokeWidth='1.5'
                    strokeLinejoin='round'
                  />
                  <path
                    d='M9 12l2 2 4-4'
                    stroke='currentColor'
                    strokeWidth='1.5'
                    strokeLinecap='round'
                    strokeLinejoin='round'
                  />
                </svg>
              </div>
              <h1>保險智能顧問</h1>
            </div>
            {mode && onSwitchMode && (
              <ModeSwitch
                mode={mode}
                onSwitch={(next) => {
                  // 切到 User Mode 前，把目前 dev session 寫進共享 key，
                  // 讓 User Mode 載入同一段對話（補上 Dev→User 方向）。
                  if (next === 'user' && activeSessionId) {
                    setStoredUserModeSessionId(activeSessionId);
                  }
                  onSwitchMode(next);
                }}
                variant='inline'
              />
            )}
          </div>

          <div
            style={{
              flex: 1,
              minHeight: 0,
              display: 'flex',
              flexDirection: 'column',
            }}
          >
            <div className='section-label'>
              <span>對話列表 ({sessions.length})</span>
              <button
                type='button'
                className='new-session-btn'
                onClick={handleNewSession}
                title='新增對話'
                aria-label='新增對話'
              >
                <svg
                  viewBox='0 0 16 16'
                  fill='none'
                  xmlns='http://www.w3.org/2000/svg'
                  aria-hidden='true'
                  className='new-session-btn__icon'
                >
                  <path
                    d='M8 2v12M2 8h12'
                    stroke='currentColor'
                    strokeWidth='1.8'
                    strokeLinecap='round'
                  />
                </svg>
                新增對話
              </button>
            </div>
            <div className='session-list'>
              {sessions.map((session) => (
                <div key={session.id} className='session-card-wrap'>
                  <button
                    type='button'
                    className={`session-card ${session.id === activeSessionId ? 'session-card--active' : ''}`}
                    onClick={() => handleSessionChange(session.id)}
                  >
                    <div className='session-card__header'>
                      <h3 className='session-card__title'>{session.title}</h3>
                      <span className={`badge badge--${session.status}`}>
                        {session.status === 'live'
                          ? '進行中'
                          : session.status === 'pending'
                            ? '處理中'
                            : '閒置'}
                      </span>
                    </div>
                    <p className='session-card__subtitle'>{session.subtitle}</p>
                    <div className='section-label'>
                      <span>更新</span>
                      <span>{session.updatedAt}</span>
                    </div>
                  </button>
                  <button
                    type='button'
                    className='session-delete-btn'
                    onClick={(e) => handleDeleteSession(e, session.id)}
                    title='刪除對話'
                    aria-label='刪除對話'
                    disabled={false}
                  >
                    <svg
                      viewBox='0 0 14 14'
                      fill='none'
                      xmlns='http://www.w3.org/2000/svg'
                      aria-hidden='true'
                    >
                      <path
                        d='M2.5 3.5h9M5.5 3.5V2.5h3v1M6 6v4M8 6v4M3.5 3.5l.5 8h6l.5-8'
                        stroke='currentColor'
                        strokeWidth='1.3'
                        strokeLinecap='round'
                        strokeLinejoin='round'
                      />
                    </svg>
                  </button>
                </div>
              ))}
            </div>
          </div>

          <footer className='sidebar__footer'>
            <div className='user-profile'>
              <div className='user-profile__info'>
                <div className='user-profile__user'>
                  <svg
                    viewBox='0 0 24 24'
                    fill='none'
                    stroke='currentColor'
                    strokeWidth='2'
                    strokeLinecap='round'
                    strokeLinejoin='round'
                    className='user-profile__icon'
                  >
                    <path d='M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2' />
                    <circle cx='12' cy='7' r='4' />
                  </svg>
                  <span className='user-profile__name'>{userId}</span>
                </div>
                <button
                  type='button'
                  className='logout-btn'
                  onClick={() => signOut()}
                  title='登出'
                >
                  登出
                </button>
              </div>
            </div>
            <div className='section-label'>
              <span>
                {transportMode === 'proxy'
                  ? '代理模式'
                  : transportMode === 'mock'
                    ? '模擬模式'
                    : '待機中'}
              </span>
              <span className='sidebar__footer-env'>開發環境</span>
            </div>
            <p className='section-copy'>
              {runtimeNotice ??
                '若 ADK API server 可用，將自動切換為代理模式。'}
            </p>
          </footer>
        </aside>

        <div className='drag-handle' onMouseDown={onDragStart('left')} />

        <section className='pane main'>
          <header className='header'>
            <div className='header__topline'>
              <div>
                <h3 className='header__title'>{activeSession.title}</h3>
                <p className='header__subtext'>{activeSession.subtitle}</p>
              </div>
              <div className='header__pills'>
                <button
                  type='button'
                  className={`pill ${
                    liveStatus === 'error'
                      ? 'pill--error'
                      : isLiveModeEnabled
                        ? 'pill--ok'
                        : 'pill--soft'
                  }`}
                  onClick={() => {
                    const nextValue = !isLiveModeEnabled;
                    if (!isLiveModeEnabled) {
                      initPlayback().catch(console.error);
                    }
                    setIsLiveModeEnabled(nextValue);
                  }}
                >
                  Live Mode: {isLiveModeEnabled ? 'ON' : 'OFF'}
                  {isLiveModeEnabled && (
                    <span
                      className={
                        liveStatus === 'error' ? 'status-dot--error' : ''
                      }
                      style={{
                        display: 'inline-block',
                        width: 8,
                        height: 8,
                        borderRadius: '50%',
                        backgroundColor:
                          liveStatus === 'connected'
                            ? '#10b981'
                            : liveStatus === 'error'
                              ? 'var(--danger)'
                              : '#f59e0b',
                        marginLeft: 6,
                        boxShadow:
                          liveStatus === 'connected'
                            ? '0 0 8px #10b981'
                            : liveStatus === 'error'
                              ? '0 0 8px var(--danger)'
                              : 'none',
                      }}
                    />
                  )}
                </button>
                <div
                  className={`pill ${proactivity ? 'pill--ok' : 'pill--soft'}`}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                    transition: 'all 200ms ease',
                    opacity: !isLiveModeEnabled ? 0.6 : 1,
                    pointerEvents: !isLiveModeEnabled ? 'none' : 'auto',
                  }}
                >
                  <span
                    style={{
                      fontSize: '12px',
                      color: proactivity ? 'var(--accent)' : 'var(--muted)',
                      fontWeight: 500,
                    }}
                  >
                    Proactivity
                  </span>
                  <label
                    className="switch"
                    style={{
                      ['--switch-checked-bg' as any]: (liveStatus === 'connected' || liveStatus === 'reconnecting') ? '#10b981' : 'var(--accent)'
                    }}
                  >
                    <input
                      type='checkbox'
                      checked={proactivity}
                      onChange={(e) => setProactivity(e.target.checked)}
                      disabled={!isLiveModeEnabled}
                    />
                    <span className="switch__slider"></span>
                  </label>
                </div>
                <div
                  className={`pill ${affectiveDialog ? 'pill--ok' : 'pill--soft'}`}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                    transition: 'all 200ms ease',
                    opacity: !isLiveModeEnabled ? 0.6 : 1,
                    pointerEvents: !isLiveModeEnabled ? 'none' : 'auto',
                  }}
                >
                  <span
                    style={{
                      fontSize: '12px',
                      color: affectiveDialog ? 'var(--accent)' : 'var(--muted)',
                      fontWeight: 500,
                    }}
                  >
                    Affective Dialog
                  </span>
                  <label
                    className="switch"
                    style={{
                      ['--switch-checked-bg' as any]: (liveStatus === 'connected' || liveStatus === 'reconnecting') ? '#10b981' : 'var(--accent)'
                    }}
                  >
                    <input
                      type='checkbox'
                      checked={affectiveDialog}
                      onChange={(e) => setAffectiveDialog(e.target.checked)}
                      disabled={!isLiveModeEnabled}
                    />
                    <span className="switch__slider"></span>
                  </label>
                </div>
                {isLiveModeEnabled && liveStatus === 'connected' && (
                  <WaveformVisualizer
                    isSpeaking={agentIsSpeaking}
                    isListening={isMicEnabled}
                  />
                )}
                <span className='pill pill--ok'>
                  session: {activeSession.id}
                </span>
                <span
                  className={`pill ${
                    transportMode === 'proxy'
                      ? 'pill--ok'
                      : transportMode === 'mock'
                        ? 'pill--signal'
                        : 'pill--soft'
                  }`}
                >
                  {transportMode === 'proxy'
                    ? '代理模式'
                    : transportMode === 'mock'
                      ? '模擬模式'
                      : '待機中'}
                </span>
                <span className='pill pill--soft'>
                  {activeSession.events.length} 個事件
                </span>
                <span className='pill pill--soft'>
                  {activeSession.messages.length} 則訊息
                </span>
              </div>
            </div>
          </header>

          <div className='chat'>
            {liveStatus === 'error' && runtimeNotice && (
              <div className='error-banner'>
                <div className='error-banner__icon'>
                  <svg
                    viewBox='0 0 24 24'
                    fill='none'
                    stroke='currentColor'
                    strokeWidth='2'
                    width='18'
                    height='18'
                  >
                    <circle cx='12' cy='12' r='10' />
                    <line x1='12' y1='8' x2='12' y2='12' />
                    <line x1='12' y1='16' x2='12.01' y2='16' />
                  </svg>
                </div>
                <span>{runtimeNotice}</span>
                <button
                  type='button'
                  style={{
                    marginLeft: 'auto',
                    background: 'transparent',
                    border: 'none',
                    color: 'inherit',
                    cursor: 'pointer',
                    padding: '4px',
                    display: 'flex',
                  }}
                  onClick={() => setRuntimeNotice(null)}
                >
                  <svg
                    viewBox='0 0 24 24'
                    fill='none'
                    stroke='currentColor'
                    strokeWidth='2'
                    width='14'
                    height='14'
                  >
                    <path d='M18 6L6 18M6 6l12 12' />
                  </svg>
                </button>
              </div>
            )}
            {chatItems.length === 0 && !pending && (
              <div className='chat-empty'>
                <div className='chat-empty__icon' aria-hidden='true'>
                  <svg
                    viewBox='0 0 48 48'
                    fill='none'
                    xmlns='http://www.w3.org/2000/svg'
                  >
                    <path
                      d='M24 4L6 13v10c0 11.1 7.68 21.48 18 24 10.32-2.52 18-12.9 18-24V13L24 4z'
                      fill='currentColor'
                      opacity='0.1'
                    />
                    <path
                      d='M24 4L6 13v10c0 11.1 7.68 21.48 18 24 10.32-2.52 18-12.9 18-24V13L24 4z'
                      stroke='currentColor'
                      strokeWidth='1.8'
                      strokeLinejoin='round'
                    />
                    <path
                      d='M17 24l5 5 9-9'
                      stroke='currentColor'
                      strokeWidth='2'
                      strokeLinecap='round'
                      strokeLinejoin='round'
                    />
                  </svg>
                </div>
                <h2 className='chat-empty__title'>新對話開始</h2>
                <p className='chat-empty__hint'>
                  描述你的保障需求，例如年齡、預算與保障目標，智能顧問將為你分析並推薦適合的保障方案。
                </p>
                <div className='chat-empty__prompts'>
                  {[
                    '我 30 歲，年預算 15000，想加強醫療保障',
                    '有家庭要負擔，想了解家庭責任保障',
                    '月收六萬，想評估失能風險覆蓋的空間',
                  ].map((prompt) => (
                    <button
                      key={prompt}
                      type='button'
                      className='chat-empty__prompt-btn'
                      onClick={() => setDraft(prompt)}
                    >
                      {prompt}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {chatItems.map((item) => {
              if (item.kind === 'message') {
                const message = item.message;
                return (
                  <article
                    key={message.id}
                    className={`message message--${message.role} ${message.status === 'streaming' ? 'message--streaming' : ''}`}
                  >
                    <div className='message__meta'>
                      {message.role === 'user' ? 'user' : 'agent'} •{' '}
                      {message.timestamp?.slice(0, 5)}
                      {message.status === 'streaming' ? ' • partial' : ''}
                    </div>
                    <div className='message__content'>
                      {message.image && (
                        <div className='message__image-wrap'>
                          <img
                            src={`data:${message.imageType};base64,${message.image}`}
                            alt='Uploaded'
                            className='message__image'
                          />
                        </div>
                      )}
                      {message.role === 'agent' ? (
                        <div className='message__text message__markdown'>
                          {(() => {
                            const segments = splitRecommendationSegments(message.text);
                            const hasSpecial = segments.some(
                              (seg) =>
                                seg.type === 'card' || seg.type === 'card-loading',
                            );
                            if (!hasSpecial) {
                              return (
                                <div
                                  dangerouslySetInnerHTML={{
                                    __html: renderMarkdown(message.text),
                                  }}
                                />
                              );
                            }

                            return (
                              <>
                                {segments.map((seg, i) =>
                                  seg.type === 'card' ? (
                                    <InsuranceCard key={`card-${i}`} data={seg.data} />
                                  ) : seg.type === 'card-loading' ? (
                                    <div
                                      key={`card-loading-${i}`}
                                      style={{
                                        margin: '8px 0',
                                        opacity: 0.6,
                                        fontStyle: 'italic',
                                      }}
                                    >
                                      推薦卡片整理中…
                                    </div>
                                  ) : (
                                    <div
                                      key={`md-${i}`}
                                      dangerouslySetInnerHTML={{ __html: renderMarkdown(seg.text) }}
                                      style={{ margin: '8px 0' }}
                                    />
                                  ),
                                )}
                              </>
                            );
                          })()}
                        </div>
                      ) : (
                        <p className='message__text'>{message.text}</p>
                      )}
                    </div>
                  </article>
                );
              }

              const evt = item.event;
              return (
                <details key={evt.id} className='tool-event'>
                    <summary className='tool-event__summary'>
                      <span className={`event__kind event__kind--${evt.kind}`}>
                        {evt.kind}
                      </span>
                      <span className='tool-event__title'>{evt.title}</span>
                      <span className='event__timestamp'>
                        {evt.timestamp?.slice(0, 5)}
                      </span>
                    </summary>
                  <pre className='tool-event__payload'>
                    {evt.payload.join('\n')}
                  </pre>
                </details>
              );
            })}

            {pending &&
              !activeSession.messages.some((m) => m.status === 'streaming') && (
              <article className='message message--agent typing-indicator'>
                <div className='message__meta'>agent • thinking</div>
                <div className='typing-dots'>
                  <span />
                  <span />
                  <span />
                </div>
              </article>
            )}
            <div ref={chatEndRef} />
          </div>

          <div className='composer'>
            <form onSubmit={handleSubmit}>
              {attachedImage && (
                <div className='composer__image-preview'>
                  <img
                    src={`data:${attachedImage.type};base64,${attachedImage.base64}`}
                    alt='Preview'
                  />
                  <button
                    type='button'
                    className='remove-image-btn'
                    onClick={() => setAttachedImage(null)}
                    title='移除圖片'
                  >
                    <svg
                      viewBox='0 0 24 24'
                      fill='none'
                      stroke='currentColor'
                      strokeWidth='2'
                      width='14'
                      height='14'
                    >
                      <path d='M18 6L6 18M6 6l12 12' />
                    </svg>
                  </button>
                </div>
              )}
              <textarea
                value={draft}
                autoComplete="off"
                data-1p-ignore=""
                data-lpignore="true"
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (
                    event.key === 'Enter' &&
                    !event.shiftKey &&
                    !event.nativeEvent.isComposing
                  ) {
                    event.preventDefault();
                    const form = event.currentTarget.form;
                    if (form) form.requestSubmit();
                  }
                }}
                placeholder='輸入保險需求，例如：我 30 歲，年預算 15000，想加強醫療保障（Shift+Enter 換行）'
              />
              <div className='composer__footer'>
                <div className='composer__actions'>
                  {isLiveModeEnabled && (
                    <>
                      <button
                        type='button'
                        className={`action-btn ${isMicEnabled ? 'action-btn--active' : ''}`}
                        onClick={() => setIsMicEnabled(!isMicEnabled)}
                        title={isMicEnabled ? '關閉麥克風' : '開啟麥克風'}
                      >
                        <svg
                          viewBox='0 0 24 24'
                          fill='none'
                          stroke='currentColor'
                          strokeWidth='2'
                          strokeLinecap='round'
                          strokeLinejoin='round'
                          width='18'
                          height='18'
                        >
                          <path d='M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z' />
                          <path d='M19 10v2a7 7 0 0 1-14 0v-2' />
                          <line x1='12' y1='19' x2='12' y2='23' />
                          <line x1='8' y1='23' x2='16' y2='23' />
                        </svg>
                      </button>
                    </>
                  )}
                  {isLiveModeEnabled && (
                    <>
                      <button
                        type='button'
                        className={`action-btn ${
                          isCameraEnabled ? 'action-btn--active' : ''
                        }`}
                        onClick={() => {
                          setIsCameraEnabled(!isCameraEnabled);
                        }}
                        title={isCameraEnabled ? '關閉鏡頭' : '開啟鏡頭'}
                      >
                        <svg
                          viewBox='0 0 24 24'
                          fill='none'
                          stroke='currentColor'
                          strokeWidth='2'
                          strokeLinecap='round'
                          strokeLinejoin='round'
                          width='18'
                          height='18'
                        >
                          <path d='M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z' />
                          <circle cx='12' cy='13' r='4' />
                        </svg>
                      </button>
                      <button
                        type='button'
                        className={`action-btn ${
                          isScreenEnabled ? 'action-btn--active' : ''
                        }`}
                        onClick={() => {
                          toggleScreen();
                        }}
                        title={isScreenEnabled ? '停止分享桌面' : '分享桌面'}
                      >
                        <svg
                          viewBox='0 0 24 24'
                          fill='none'
                          stroke='currentColor'
                          strokeWidth='2'
                          strokeLinecap='round'
                          strokeLinejoin='round'
                          width='18'
                          height='18'
                        >
                          <rect x="2" y="3" width="20" height="14" rx="2" ry="2" />
                          <line x1="8" y1="21" x2="16" y2="21" />
                          <line x1="12" y1="17" x2="12" y2="21" />
                        </svg>
                      </button>
                    </>
                  )}
                  <button
                    type='button'
                    className='action-btn'
                    onClick={() =>
                      document.getElementById('image-upload')?.click()
                    }
                    title='附件上傳'
                  >
                    <svg
                      viewBox='0 0 24 24'
                      fill='none'
                      stroke='currentColor'
                      strokeWidth='2'
                      strokeLinecap='round'
                      strokeLinejoin='round'
                      width='18'
                      height='18'
                    >
                      <path d='M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48' />
                    </svg>
                  </button>
                  <input
                    id='image-upload'
                    type='file'
                    accept='image/*'
                    style={{ display: 'none' }}
                    onChange={async (e) => {
                      const file = e.target.files?.[0];
                      if (file) {
                        const reader = new FileReader();
                        reader.onload = (event) => {
                          const base64 = (event.target?.result as string).split(
                            ',',
                          )[1];
                          setAttachedImage({
                            base64,
                            type: file.type,
                          });
                        };
                        reader.readAsDataURL(file);
                      }
                      // Reset value so same file can be uploaded again
                      e.target.value = '';
                    }}
                  />
                </div>
                <div className='composer__hint'>
                  {transportMode === 'proxy'
                    ? 'Proxy 模式，實時串流回覆'
                    : 'ADK 離線，使用模擬資料'}
                </div>
                <button className='button' type='submit' disabled={pending}>
                  {pending ? '回覆中…' : '傳送'}
                </button>
              </div>
            </form>
          </div>
        </section>

        <div className='drag-handle' onMouseDown={onDragStart('right')} />

        <aside
          className='pane inspector'
          style={{ width: inspectorW, minWidth: inspectorW }}
        >
          <div className='inspector__header'>
            <div className='section-label'>
              <span>Inspector</span>
              <span className='inspector__updated'>
                {activeSession.updatedAt}
              </span>
            </div>
            <div className='tab-group'>
              <button
                type='button'
                className={`tab ${inspectorTab === 'events' ? 'tab--active' : ''}`}
                onClick={() => setInspectorTab('events')}
              >
                <svg
                  viewBox='0 0 14 14'
                  fill='none'
                  xmlns='http://www.w3.org/2000/svg'
                  aria-hidden='true'
                  className='tab__icon'
                >
                  <circle cx='2' cy='4' r='1.2' fill='currentColor' />
                  <line
                    x1='5'
                    y1='4'
                    x2='12'
                    y2='4'
                    stroke='currentColor'
                    strokeWidth='1.4'
                    strokeLinecap='round'
                  />
                  <circle cx='2' cy='8' r='1.2' fill='currentColor' />
                  <line
                    x1='5'
                    y1='8'
                    x2='12'
                    y2='8'
                    stroke='currentColor'
                    strokeWidth='1.4'
                    strokeLinecap='round'
                  />
                  <circle cx='2' cy='12' r='1.2' fill='currentColor' />
                  <line
                    x1='5'
                    y1='12'
                    x2='9'
                    y2='12'
                    stroke='currentColor'
                    strokeWidth='1.4'
                    strokeLinecap='round'
                  />
                </svg>
                事件歷程
              </button>
              <button
                type='button'
                className={`tab ${inspectorTab === 'state' ? 'tab--active' : ''}`}
                onClick={() => setInspectorTab('state')}
              >
                <svg
                  viewBox='0 0 14 14'
                  fill='none'
                  xmlns='http://www.w3.org/2000/svg'
                  aria-hidden='true'
                  className='tab__icon'
                >
                  <rect
                    x='1'
                    y='1'
                    width='5'
                    height='5'
                    rx='1.2'
                    stroke='currentColor'
                    strokeWidth='1.3'
                  />
                  <rect
                    x='8'
                    y='1'
                    width='5'
                    height='5'
                    rx='1.2'
                    stroke='currentColor'
                    strokeWidth='1.3'
                  />
                  <rect
                    x='1'
                    y='8'
                    width='5'
                    height='5'
                    rx='1.2'
                    stroke='currentColor'
                    strokeWidth='1.3'
                  />
                  <rect
                    x='8'
                    y='8'
                    width='5'
                    height='5'
                    rx='1.2'
                    stroke='currentColor'
                    strokeWidth='1.3'
                  />
                </svg>
                User State
              </button>
            </div>
          </div>

          <div className='inspector__body'>
            {inspectorTab === 'events' ? (
              <div className='timeline'>
                {groupStreamEvents(activeSession.events).map((node) => {
                  if (node.kind === 'stream-group') {
                    return (
                      <StreamGroupNode
                        key={node.events[0].id}
                        events={node.events}
                        isLast={node.isLast}
                      />
                    );
                  }
                  if (node.kind === 'state-group') {
                    return (
                      <StateGroupNode
                        key={node.events[0].id}
                        events={node.events}
                        isLast={node.isLast}
                      />
                    );
                  }
                  if (node.kind === 'transcription-group') {
                    return (
                      <TranscriptionGroupNode
                        key={node.events[0].id}
                        events={node.events}
                        isLast={node.isLast}
                        title={node.title}
                      />
                    );
                  }
                  if (node.kind !== 'single') return null;
                  const event = node.event;
                  return (
                    <details key={event.id} className='timeline__node'>
                      <summary className='timeline__header'>
                        <span
                          className='timeline__dot'
                          data-kind={event.kind}
                        />
                        {!node.isLast && <span className='timeline__line' />}
                        <span
                          className={`event__kind event__kind--${event.kind}`}
                        >
                          {event.kind}
                        </span>
                        <span className='timeline__title'>{event.title}</span>
                        <span className='event__timestamp'>
                          {event.timestamp?.slice(0, 5)}
                        </span>
                      </summary>
                      <div className='timeline__detail'>
                        <p className='timeline__summary'>{event.summary}</p>
                        {event.payload.length > 0 && (
                          <ul className='timeline__payload'>
                            {event.payload.map((line: string) => (
                              <li key={line}>{line}</li>
                            ))}
                          </ul>
                        )}
                      </div>
                    </details>
                  );
                })}
                {activeSession.events.length === 0 && (
                  <div className='empty-state'>尚無事件紀錄。</div>
                )}
              </div>
            ) : (
              <div className='state-tree'>
                {(() => {
                  const entries = Object.entries(activeSession.state).filter(
                    ([k]) => !k.startsWith('_ui_'),
                  );
                  return entries.length > 0 ? (
                    entries.map(([key, value]) => (
                      <StateTreeNode
                        key={key}
                        nodeKey={key}
                        value={parseStateValue(value)}
                        depth={0}
                      />
                    ))
                  ) : (
                    <div className='empty-state'>尚無 state 資料。</div>
                  );
                })()}
              </div>
            )}
          </div>
        </aside>
      </div>
    </main>
  );
}
