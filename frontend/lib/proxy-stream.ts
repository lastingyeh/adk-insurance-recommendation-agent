import { Dispatch, SetStateAction } from 'react';
import { TimelineEvent, SessionRecord, ChatMessage } from './mock-data';

export type ProxyStreamEnvelope =
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

export function formatClock() {
  // 強制使用 24 小時制並確保格式為 HH:mm:ss
  const now = new Date();
  const pad = (n: number) => n.toString().padStart(2, '0');
  return `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
}

export function parseProxyEnvelope(frame: string) {
  const dataLines = frame
    .split(/\r?\n/)
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.slice(5).trimStart())
    .filter(Boolean);

  if (dataLines.length === 0) {
    return null;
  }

  try {
    return JSON.parse(dataLines.join('\n')) as ProxyStreamEnvelope;
  } catch {
    return null;
  }
}

export async function consumeProxyStream({
  response,
  seed,
  sessionId,
  fallbackEventId,
  setSessions,
  setSelectedEventId,
  setRuntimeNotice,
  onEnvelope,
}: {
  response: Response;
  seed: number;
  sessionId: string;
  fallbackEventId: string;
  setSessions: Dispatch<SetStateAction<SessionRecord[]>>;
  setSelectedEventId: Dispatch<SetStateAction<string>>;
  setRuntimeNotice: Dispatch<SetStateAction<string | null>>;
  onEnvelope: () => void;
}) {
  const streamMessageId = `msg-agent-stream-${seed}`;
  const decoder = new TextDecoder();
  const reader = response.body?.getReader();

  if (!reader) {
    throw new Error('Proxy stream body is unavailable');
  }

  let buffer = '';
  let latestEventId = fallbackEventId;
  let sawDone = false;

  setSessions((currentSessions) =>
    currentSessions.map((session) =>
      session.id === sessionId ? { ...session, status: 'pending' } : session,
    ),
  );

  const upsertStreamMessage = (
    messages: ChatMessage[],
    updater: (prev: ChatMessage | null) => ChatMessage,
  ): ChatMessage[] => {
    if (messages.some((message) => message.id === streamMessageId)) {
      return messages.map((message) =>
        message.id === streamMessageId ? updater(message) : message,
      );
    }
    return [...messages, updater(null)];
  };

  const finalizeStreamMessage = (
    messages: ChatMessage[],
    finalText: string,
  ): ChatMessage[] => {
    const exists = messages.some((message) => message.id === streamMessageId);
    if (!exists && !finalText) {
      return messages;
    }
    return upsertStreamMessage(messages, (prev) => ({
      id: streamMessageId,
      role: 'agent',
      text: finalText || prev?.text || '',
      timestamp: formatClock(),
      status: 'final',
    }));
  };

  try {
    while (true) {
      const { done, value } = await reader.read();

      if (done) {
        buffer += decoder.decode();
        break;
      }

      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split(/\r?\n\r?\n/);
      buffer = frames.pop() ?? '';

      for (const frame of frames) {
        const envelope = parseProxyEnvelope(frame);

        if (!envelope) {
          continue;
        }

        onEnvelope();

        if (envelope.type === 'meta') {
          setRuntimeNotice(envelope.notice);
          continue;
        }

        if (envelope.type === 'timeline') {
          latestEventId = envelope.event.id;
          setSessions((currentSessions) =>
            currentSessions.map((session) => {
              if (session.id !== sessionId) {
                return session;
              }

              if (
                session.events.some((event) => event.id === envelope.event.id)
              ) {
                return session;
              }

              return {
                ...session,
                updatedAt: '剛剛',
                events: [...session.events, envelope.event],
              };
            }),
          );
          setSelectedEventId(envelope.event.id);
          continue;
        }

        if (envelope.type === 'state') {
          setSessions((currentSessions) =>
            currentSessions.map((session) => {
              if (session.id !== sessionId) {
                return session;
              }

              return {
                ...session,
                updatedAt: '剛剛',
                state: {
                  ...session.state,
                  ...envelope.patch,
                },
              };
            }),
          );
          continue;
        }

        if (envelope.type === 'message') {
          setSessions((currentSessions) =>
            currentSessions.map((session) => {
              if (session.id !== sessionId) {
                return session;
              }

              return {
                ...session,
                updatedAt: '剛剛',
                messages: upsertStreamMessage(session.messages, (prev) => ({
                  id: streamMessageId,
                  role: 'agent',
                  text: !prev
                    ? envelope.text
                    : envelope.mode === 'append'
                      ? `${prev.text}${envelope.text}`
                      : prev.text.endsWith(envelope.text)
                        ? prev.text
                        : `${prev.text}${envelope.text}`,
                  timestamp: formatClock(),
                  status: 'streaming',
                })),
              };
            }),
          );
          continue;
        }

        if (envelope.type === 'error') {
          setRuntimeNotice(`SSE proxy 發生錯誤：${envelope.message}`);
          continue;
        }

        if (envelope.type === 'done') {
          sawDone = true;
          setSessions((currentSessions) =>
            currentSessions.map((session) => {
              if (session.id !== sessionId) {
                return session;
              }

              return {
                ...session,
                status: 'live',
                updatedAt: '剛剛',
                state: {
                  ...session.state,
                  ...envelope.state,
                },
                messages: finalizeStreamMessage(
                  session.messages,
                  envelope.finalText,
                ),
              };
            }),
          );
          setSelectedEventId(latestEventId);
        }
      }
    }

    if (buffer.trim()) {
      const envelope = parseProxyEnvelope(buffer);

      if (envelope?.type === 'done') {
        sawDone = true;
        setSessions((currentSessions) =>
          currentSessions.map((session) => {
            if (session.id !== sessionId) {
              return session;
            }

            return {
              ...session,
              status: 'live',
              updatedAt: '剛剛',
              state: {
                ...session.state,
                ...envelope.state,
              },
              messages: finalizeStreamMessage(
                session.messages,
                envelope.finalText,
              ),
            };
          }),
        );
      }
    }

    if (!sawDone) {
      setSessions((currentSessions) =>
        currentSessions.map((session) => {
          if (session.id !== sessionId) {
            return session;
          }

          return {
            ...session,
            status: 'live',
            updatedAt: '剛剛',
            messages: session.messages.map((message) =>
              message.id === streamMessageId
                ? {
                    ...message,
                    timestamp: formatClock(),
                    status: 'final',
                  }
                : message,
            ),
          };
        }),
      );
      setSelectedEventId(latestEventId);
    }
  } finally {
    reader.releaseLock();
  }
}
