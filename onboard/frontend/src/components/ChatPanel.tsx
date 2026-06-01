import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { api } from '../api/client';
import type { AppName, ChatSession } from '../api/types';
import { useWebSocket } from '../hooks/useWebSocket';
import { MarkdownView } from './MarkdownView';

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'tool' | 'reasoning';
  content: string;
  label: string;
  status: 'sent' | 'streaming' | 'running' | 'complete' | 'error';
  timestamp: Date;
}

// Module-level cache so messages survive component unmount during SPA navigation
const messageCache = new Map<AppName, ChatMessage[]>();
const streamingCache = new Map<AppName, boolean>();

function formatSessionTime(dateStr: string): string {
  const d = new Date(dateStr);
  const now = new Date();
  const diff = now.getTime() - d.getTime();
  if (diff < 60_000) return 'just now';
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  if (diff < 604_800_000) return `${Math.floor(diff / 86_400_000)}d ago`;
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

export function ChatPanel({ appName }: { appName: AppName }) {
  const [messages, setMessages] = useState<ChatMessage[]>(() => messageCache.get(appName) ?? []);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(() => streamingCache.get(appName) ?? false);
  const [progressText, setProgressText] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [loadingSessions, setLoadingSessions] = useState(false);
  const [loadingSession, setLoadingSession] = useState<string | null>(null);
  const messageCounter = useRef(0);
  const messagesRef = useRef<HTMLElement | null>(null);

  function createMessage(message: Omit<ChatMessage, 'id' | 'timestamp'>): ChatMessage {
    messageCounter.current += 1;
    return {
      ...message,
      id: `${Date.now()}-${messageCounter.current}`,
      timestamp: new Date(),
    };
  }

  const socket = useWebSocket(appName, (message) => {
    if (message.type === 'session_key') return; // handled by hook
    if (message.type === 'progress') {
      setStreaming(true);
      setProgressText(message.content);
      return;
    }
    if (message.type === 'reasoning') {
      setStreaming(true);
      setMessages((items) => {
        const last = items.at(-1);
        if (last?.role === 'reasoning' && last.status === 'streaming') {
          return [
            ...items.slice(0, -1),
            { ...last, content: `${last.content}${message.content}` },
          ];
        }
        return [
          ...items,
          createMessage({ role: 'reasoning', label: 'thinking', status: 'streaming', content: message.content }),
        ];
      });
    } else if (message.type === 'delta') {
      setStreaming(true);
      setMessages((items) => {
        const last = items.at(-1);
        if (last?.role === 'assistant' && last.status === 'streaming') {
          return [
            ...items.slice(0, -1),
            { ...last, content: `${last.content}${message.content}` },
          ];
        }
        const updated = items.map((item) =>
          item.role === 'reasoning' && item.status === 'streaming' ? { ...item, status: 'complete' as const } : item
        );
        return [
          ...updated,
          createMessage({ role: 'assistant', label: 'assistant', status: 'streaming', content: message.content }),
        ];
      });
    } else if (message.type === 'tool_start') {
      setProgressText(null);
      setMessages((items) => {
        const updated = items.map((item) =>
          item.role === 'reasoning' && item.status === 'streaming' ? { ...item, status: 'complete' as const } : item
        );
        return [
          ...updated,
          createMessage({ role: 'tool', label: 'tool', status: 'running', content: message.tool }),
        ];
      });
    } else if (message.type === 'tool_complete') {
      setMessages((items) => [
        ...items,
        createMessage({ role: 'tool', label: 'tool', status: 'complete', content: `${message.tool}${message.result ? `\n${message.result}` : ''}` }),
      ]);
    } else if (message.type === 'complete') {
      setStreaming(false);
      setProgressText(null);
      setMessages((items) => {
        let updated = items.map((item) =>
          item.role === 'reasoning' && item.status === 'streaming' ? { ...item, status: 'complete' as const } : item
        );
        if (!message.content.trim()) return updated;
        const last = updated.at(-1);
        if (last?.role === 'assistant' && last.status === 'streaming') {
          return [...updated.slice(0, -1), { ...last, content: message.content, status: 'complete' as const }];
        }
        return [...updated, createMessage({ role: 'assistant', label: 'assistant', status: 'complete', content: message.content })];
      });
    } else if (message.type === 'error') {
      setStreaming(false);
      setProgressText(null);
      setMessages((items) => [
        ...items,
        createMessage({ role: 'tool', label: 'error', status: 'error', content: message.message }),
      ]);
    }
  });

  const canSend = useMemo(() => socket.connected && input.trim().length > 0, [socket.connected, input]);
  const displayName = appName === 'solo' ? 'Solo' : 'Wolo';

  // Reset streaming state when WebSocket disconnects mid-stream
  useEffect(() => {
    if (!socket.connected && streaming) {
      setStreaming(false);
      setProgressText(null);
      streamingCache.set(appName, false);
      // Mark any streaming messages as complete
      setMessages((items) => items.map((item) =>
        item.status === 'streaming' ? { ...item, status: 'complete' as const } : item
      ));
    }
  }, [socket.connected, streaming, appName]);

  // Sync messages to module-level cache
  useEffect(() => { messageCache.set(appName, messages); }, [appName, messages]);
  useEffect(() => { streamingCache.set(appName, streaming); }, [appName, streaming]);
  useEffect(() => { messagesRef.current?.scrollTo({ top: messagesRef.current.scrollHeight }); }, [messages, streaming]);

  // Load session list
  const loadSessions = useCallback(async (search?: string) => {
    setLoadingSessions(true);
    try {
      const params: Record<string, string> = {};
      if (search) params.search = search;
      const data = await api.chatSessions(appName, params);
      setSessions(data);
    } catch { /* ignore */ }
    setLoadingSessions(false);
  }, [appName]);

  useEffect(() => {
    if (showHistory) loadSessions(searchQuery || undefined);
  }, [showHistory, loadSessions, searchQuery]);

  // Refresh session list when streaming completes (new conversation saved)
  useEffect(() => {
    if (!streaming && messages.length > 0 && showHistory) {
      loadSessions(searchQuery || undefined);
    }
  }, [streaming]); // eslint-disable-line react-hooks/exhaustive-deps

  // Resume a previous session
  const resumeSession = useCallback(async (sessionKey: string) => {
    setLoadingSession(sessionKey);
    try {
      const detail = await api.chatSession(appName, sessionKey);
      // Convert loaded messages to ChatMessage format
      const loaded: ChatMessage[] = detail.messages.map((m, i) => ({
        id: `loaded-${i}`,
        role: m.role as 'user' | 'assistant',
        content: m.content,
        label: m.role === 'user' ? 'you' : 'assistant',
        status: 'complete' as const,
        timestamp: new Date(),
      }));
      setMessages(loaded);
      messageCache.set(appName, loaded);
      setStreaming(false);
      streamingCache.set(appName, false);
      // Reconnect WebSocket with this session key
      socket.reconnect(sessionKey);
      setShowHistory(false);
    } catch { /* ignore */ }
    setLoadingSession(null);
  }, [appName, socket]);

  // Delete a session
  const deleteSession = useCallback(async (sessionKey: string) => {
    try {
      await api.deleteChatSession(appName, sessionKey);
      setSessions((s) => s.filter((x) => x.session_key !== sessionKey));
    } catch { /* ignore */ }
  }, [appName]);

  // Start new conversation
  const newConversation = useCallback(() => {
    setMessages([]);
    messageCache.set(appName, []);
    setStreaming(false);
    streamingCache.set(appName, false);
    socket.reconnect();
    setShowHistory(false);
  }, [appName, socket]);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!canSend) return;
    const content = input.trim();
    setMessages((items) => [...items, createMessage({ role: 'user', label: 'you', status: 'sent', content })]);
    setInput('');
    socket.send({ type: 'message', content });
  }

  return (
    <div className="flex h-full overflow-hidden">
      {/* Session History Sidebar */}
      {showHistory && (
        <aside className="w-72 border-r border-border bg-surface-2/50 flex flex-col shrink-0">
          <div className="px-3 py-3 border-b border-border space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-mono uppercase tracking-wider text-text-muted">History</span>
              <button
                onClick={() => setShowHistory(false)}
                className="p-1 text-text-muted hover:text-text rounded transition-colors"
                title="Close"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M18 6L6 18M6 6l12 12"/></svg>
              </button>
            </div>
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search conversations..."
              className="w-full px-2.5 py-1.5 text-[12px] bg-surface-1 border border-border rounded text-text placeholder:text-text-muted outline-none focus:border-text-muted transition-colors"
            />
            <button
              onClick={newConversation}
              className="w-full px-2.5 py-1.5 text-[11px] font-medium rounded border border-accent-solo/30 bg-accent-solo-dim text-accent-solo hover:bg-accent-solo/20 transition-colors"
            >
              + New Conversation
            </button>
          </div>
          <div className="flex-1 overflow-y-auto">
            {loadingSessions ? (
              <div className="flex items-center justify-center py-8">
                <span className="text-[11px] text-text-muted font-mono">Loading...</span>
              </div>
            ) : sessions.length === 0 ? (
              <div className="flex items-center justify-center py-8">
                <span className="text-[11px] text-text-muted font-mono">No conversations found</span>
              </div>
            ) : (
              sessions.map((s) => (
                <div
                  key={s.session_key}
                  className={`group px-3 py-2.5 border-b border-border/50 hover:bg-surface-1/50 transition-colors cursor-pointer ${
                    loadingSession === s.session_key ? 'opacity-50' : ''
                  }`}
                  onClick={() => resumeSession(s.session_key)}
                >
                  <div className="flex items-start justify-between gap-2">
                    <p className="text-[12px] text-text-secondary leading-snug line-clamp-2 m-0 flex-1">
                      {s.preview || 'Empty conversation'}
                    </p>
                    <div className="flex items-center gap-1 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
                      <a
                        href={api.exportChatMarkdown(appName, s.session_key)}
                        onClick={(e) => e.stopPropagation()}
                        className="p-0.5 text-text-muted hover:text-text transition-colors"
                        title="Export Markdown"
                        download
                      >
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg>
                      </a>
                      <button
                        onClick={(e) => { e.stopPropagation(); deleteSession(s.session_key); }}
                        className="p-0.5 text-text-muted hover:text-danger transition-colors"
                        title="Delete"
                      >
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 6h18M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>
                      </button>
                    </div>
                  </div>
                  <div className="flex items-center gap-2 mt-1">
                    <span className="text-[10px] text-text-muted font-mono">{s.message_count} msgs</span>
                    <span className="text-[10px] text-text-muted font-mono">{formatSessionTime(s.updated_at)}</span>
                  </div>
                </div>
              ))
            )}
          </div>
        </aside>
      )}

      {/* Main Chat Area */}
      <div className="flex flex-col flex-1 min-w-0">
        {/* Header */}
        <header className="flex items-center justify-between px-5 py-3.5 border-b border-border bg-surface-2/50">
          <div className="flex items-center gap-3">
            <button
              onClick={() => setShowHistory(!showHistory)}
              className={`p-1.5 rounded transition-colors ${showHistory ? 'bg-accent-solo-dim text-accent-solo' : 'text-text-muted hover:text-text hover:bg-surface-2'}`}
              title="Chat history"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
            </button>
            <div>
              <span className="text-[11px] font-mono uppercase tracking-wider text-text-muted block mb-0.5">agent console</span>
              <h2 className="text-base font-serif text-text m-0">{displayName} Chat</h2>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {socket.sessionKey && (
              <span className="text-[10px] font-mono text-text-muted hidden sm:inline" title={`Session: ${socket.sessionKey}`}>
                {socket.sessionKey.slice(0, 12)}…
              </span>
            )}
            <button
              onClick={newConversation}
              className="inline-flex items-center gap-1.5 text-[11px] font-mono px-2.5 py-1 rounded-md border border-border text-text-muted hover:text-text hover:border-text-muted transition-colors"
              title="New conversation"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 5v14M5 12h14"/></svg>
              New
            </button>
            <div className={`inline-flex items-center gap-2 text-[11px] font-mono px-2.5 py-1 rounded-md border ${
              socket.connected ? 'border-success/30 text-success' : 'border-warning/30 text-warning'
            }`}>
              <span className={`w-1.5 h-1.5 rounded-full ${socket.connected ? 'bg-success' : 'bg-warning animate-[pulse-dot_1.4s_ease-in-out_infinite]'}`} />
              {streaming ? 'streaming' : socket.connected ? 'connected' : 'connecting'}
            </div>
          </div>
        </header>

        {/* Messages */}
        <section ref={messagesRef} className="flex-1 overflow-y-auto p-5 space-y-3" aria-live="polite">
          {messages.length === 0 ? (
            <div className="flex items-start gap-4 max-w-lg border border-dashed border-border rounded-lg p-5 text-text-secondary">
              <span className="shrink-0 w-10 h-10 grid place-items-center rounded-md bg-accent-solo-dim text-accent-solo font-mono font-bold text-sm">?</span>
              <div>
                <h3 className="text-sm font-medium text-text m-0">Start a conversation</h3>
                <p className="text-[13px] mt-1.5 leading-relaxed m-0">
                  Ask for a summary, search past records, or trigger a work-log action. Tool activity appears inline.
                </p>
              </div>
            </div>
          ) : null}

          {messages.map((msg) => (
            msg.role === 'reasoning' ? (
              <details
                key={msg.id}
                className="max-w-[720px] rounded-md border border-border/50 bg-surface-2/30 animate-[fade-in_0.2s_ease-out_both] group"
                open={msg.status === 'streaming'}
              >
                <summary className="flex items-center gap-2 px-4 py-2 cursor-pointer text-[11px] font-mono text-text-muted select-none hover:text-text-secondary transition-colors">
                  <span className={`w-1.5 h-1.5 rounded-full ${msg.status === 'streaming' ? 'bg-purple-400 animate-[pulse-dot_1s_ease-in-out_infinite]' : 'bg-purple-400/50'}`} />
                  <span>thinking</span>
                  {msg.status === 'streaming' && <span className="text-[10px] opacity-60">streaming…</span>}
                </summary>
                <div className="px-4 pb-3 text-[12px] leading-relaxed text-text-muted/80 whitespace-pre-wrap font-mono max-h-[200px] overflow-y-auto">
                  {msg.content}
                </div>
              </details>
            ) : (
            <article
              key={msg.id}
              className={`max-w-[720px] rounded-md px-4 py-3 animate-[fade-in_0.2s_ease-out_both] ${
                msg.role === 'user'
                  ? 'ml-auto bg-accent-solo-dim border border-accent-solo/20'
                  : msg.role === 'tool'
                  ? 'bg-surface-2 border border-border font-mono text-xs'
                  : 'bg-surface-2 border border-border'
              } ${msg.status === 'error' ? 'border-danger/40 bg-danger/5' : ''}`}
            >
              <div className="flex items-center justify-between mb-1.5 text-[11px] font-mono text-text-muted">
                <span className="flex items-center gap-1.5">
                  <span className={`w-1.5 h-1.5 rounded-full ${
                    msg.role === 'user' ? 'bg-accent-solo' : msg.status === 'error' ? 'bg-danger' : msg.status === 'running' ? 'bg-warning' : 'bg-accent-wolo'
                  }`} />
                  {msg.label}
                </span>
                <span>{msg.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
              </div>
              <div className={`text-[13px] leading-relaxed ${msg.role === 'tool' ? 'text-text-muted whitespace-pre-wrap' : 'text-text-secondary'}`}>
                {msg.role === 'assistant' ? <MarkdownView content={msg.content} /> : msg.content}
              </div>
            </article>
            )
          ))}

          {streaming ? (
            <div className="inline-flex items-center gap-2 px-3 py-2 rounded-md border border-border bg-surface-2 text-[11px] text-text-muted font-mono max-w-sm animate-[fade-in_0.15s_ease-out_both]">
              <span className="w-1.5 h-1.5 rounded-full bg-accent-wolo shrink-0 animate-[pulse-dot_1s_ease-in-out_infinite]" />
              {progressText ? (
                <span className="truncate">{progressText}</span>
              ) : (
                <>
                  <span className="w-1 h-1 rounded-full bg-accent-wolo animate-[pulse-dot_1s_ease-in-out_infinite_0.15s]" />
                  <span className="w-1 h-1 rounded-full bg-accent-wolo animate-[pulse-dot_1s_ease-in-out_infinite_0.3s]" />
                </>
              )}
            </div>
          ) : null}
        </section>

        {/* Input */}
        <form className="border-t border-border px-5 py-3" onSubmit={submit} aria-label="Chat composer">
          <textarea
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder={socket.connected ? `Message ${displayName}... (Enter to send)` : 'Connecting...'}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                submit(event);
              }
            }}
            className="w-full min-h-[44px] max-h-[160px] px-3 py-2.5 text-[13px] bg-transparent border-none text-text placeholder:text-text-muted outline-none resize-none"
            rows={1}
          />
        </form>
      </div>
    </div>
  );
}
