import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { api } from '../api/client';
import type { AppName, ChatSession } from '../api/types';
import { useWebSocket } from '../hooks/useWebSocket';
import { EmptyState } from './EmptyState';
import { MarkdownView } from './MarkdownView';

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'tool' | 'reasoning';
  content: string;
  label: string;
  status: 'sent' | 'streaming' | 'running' | 'complete' | 'error';
  timestamp: Date;
  media?: string[];
}

interface PendingAttachment {
  file: File;
  diskPath: string;
  previewUrl: string;
  uploading: boolean;
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
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const messageCounter = useRef(0);
  const messagesRef = useRef<HTMLElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

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
    } else if (message.type === 'media') {
      setStreaming(true);
      setMessages((items) => [
        ...items,
        createMessage({ role: 'assistant', label: 'assistant', status: 'complete', content: '', media: message.paths }),
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

  const readyAttachments = attachments.filter((a) => a.diskPath && !a.uploading);
  const canSend = useMemo(() => socket.connected && (input.trim().length > 0 || readyAttachments.length > 0), [socket.connected, input, readyAttachments]);
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

  // Auto-grow textarea
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [input]);

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
        timestamp: new Date(m.timestamp),
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

  const isImageFile = (path: string) => /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(path);

  const uploadFiles = useCallback(async (files: FileList | File[]) => {
    const newAttachments: PendingAttachment[] = [];
    for (const file of Array.from(files)) {
      const att: PendingAttachment = { file, diskPath: '', previewUrl: '', uploading: true };
      if (file.type.startsWith('image/')) {
        att.previewUrl = URL.createObjectURL(file);
      }
      newAttachments.push(att);
    }
    setAttachments((prev) => [...prev, ...newAttachments]);

    const updated = [...newAttachments];
    for (let i = 0; i < updated.length; i++) {
      try {
        const result = await api.uploadChatFile(updated[i].file);
        updated[i] = { ...updated[i], diskPath: result.disk_path, uploading: false };
      } catch {
        updated[i] = { ...updated[i], uploading: false };
      }
    }
    setAttachments((prev) => {
      const names = new Set(updated.map((a) => a.file.name));
      const kept = prev.filter((a) => !names.has(a.file.name));
      return [...kept, ...updated];
    });
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    if (e.dataTransfer.files.length > 0) uploadFiles(e.dataTransfer.files);
  }, [uploadFiles]);

  const handlePaste = useCallback((e: React.ClipboardEvent) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    const files: File[] = [];
    for (const item of Array.from(items)) {
      if (item.kind === 'file') {
        const f = item.getAsFile();
        if (f) files.push(f);
      }
    }
    if (files.length > 0) {
      e.preventDefault();
      uploadFiles(files);
    }
  }, [uploadFiles]);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!canSend) return;
    const content = input.trim();
    const mediaPaths = readyAttachments.map((a) => a.diskPath);
    const mediaUrls = readyAttachments.map((a) => a.previewUrl || a.diskPath);
    setMessages((items) => [...items, createMessage({ role: 'user', label: 'you', status: 'sent', content, media: mediaUrls.length > 0 ? mediaUrls : undefined })]);
    setInput('');
    setAttachments([]);
    socket.send({ type: 'message', content, media: mediaPaths });
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
            <EmptyState
              icon={<span className="font-mono font-bold text-sm">?</span>}
              title="Start a conversation"
              description="Ask for a summary, search past records, or trigger a work-log action. Tool activity appears inline."
            />
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
                <span>{(() => {
                  const now = new Date();
                  const isToday = msg.timestamp.toDateString() === now.toDateString();
                  return isToday
                    ? msg.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
                    : msg.timestamp.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' ' + msg.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
                })()}</span>
              </div>
              <div className={`text-[13px] leading-relaxed ${msg.role === 'tool' ? 'text-text-muted whitespace-pre-wrap' : 'text-text-secondary'}`}>
                {msg.role === 'assistant' ? <MarkdownView content={msg.content} /> : msg.content}
              </div>
              {msg.media && msg.media.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-2">
                  {msg.media.map((path, i) =>
                    isImageFile(path) ? (
                      <a key={i} href={path} target="_blank" rel="noopener noreferrer">
                        <img src={path} alt="" className="max-h-48 max-w-xs rounded border border-border/50" />
                      </a>
                    ) : (
                      <a key={i} href={path} download className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-[11px] font-mono rounded border border-border bg-surface-1 text-text-secondary hover:text-text hover:border-text-muted transition-colors">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg>
                        {path.split('/').pop() || 'file'}
                      </a>
                    )
                  )}
                </div>
              )}
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
        <form
          className={`border-t border-border px-5 py-3 ${dragOver ? 'bg-accent-solo-dim/20 border-accent-solo/40' : ''}`}
          onSubmit={submit}
          aria-label="Chat composer"
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
        >
          {/* Pending attachments preview */}
          {attachments.length > 0 && (
            <div className="flex flex-wrap gap-2 mb-2">
              {attachments.map((att, i) => (
                <div key={i} className="relative group flex items-center gap-1.5 px-2 py-1 rounded border border-border bg-surface-1 text-[11px] font-mono">
                  {att.previewUrl ? (
                    <img src={att.previewUrl} alt="" className="w-8 h-8 rounded object-cover" />
                  ) : (
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-text-muted shrink-0"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                  )}
                  <span className="text-text-secondary truncate max-w-[120px]">{att.file.name}</span>
                  {att.uploading && <span className="w-1.5 h-1.5 rounded-full bg-warning animate-pulse" />}
                  <button
                    type="button"
                    onClick={() => setAttachments((prev) => prev.filter((_, j) => j !== i))}
                    className="ml-1 text-text-muted hover:text-danger transition-colors"
                  >
                    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3"><path d="M18 6L6 18M6 6l12 12"/></svg>
                  </button>
                </div>
              ))}
            </div>
          )}
          <div className="flex items-end gap-2">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="hidden"
              onChange={(e) => { if (e.target.files?.length) uploadFiles(e.target.files); e.target.value = ''; }}
            />
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className="shrink-0 p-2 text-text-muted hover:text-text transition-colors rounded"
              title="Attach file"
              aria-label="Attach file"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48"/></svg>
            </button>
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onPaste={handlePaste}
              placeholder={socket.connected ? `Message ${displayName}...` : 'Connecting...'}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault();
                  submit(event);
                }
              }}
              className="flex-1 min-h-[44px] max-h-[160px] px-3 py-2.5 text-[13px] bg-transparent border-none text-text placeholder:text-text-muted outline-none resize-none"
              rows={1}
              aria-label="Chat message"
            />
            <button
              type="submit"
              disabled={!canSend}
              className="shrink-0 p-2 text-text-muted hover:text-text disabled:opacity-30 disabled:hover:text-text-muted transition-colors rounded"
              title="Send"
              aria-label="Send message"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
            </button>
          </div>
          <div className="mt-1.5">
            <span className="text-[10px] text-text-muted/60">Enter to send · Shift+Enter for new line · Paste or drop files</span>
          </div>
        </form>
      </div>
    </div>
  );
}
