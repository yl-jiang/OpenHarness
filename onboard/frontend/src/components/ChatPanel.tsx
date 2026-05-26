import { FormEvent, useEffect, useMemo, useRef, useState } from 'react';

import type { AppName } from '../api/types';
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

export function ChatPanel({ appName }: { appName: AppName }) {
  const [messages, setMessages] = useState<ChatMessage[]>(() => messageCache.get(appName) ?? []);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(() => streamingCache.get(appName) ?? false);
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
        // Mark any streaming reasoning as complete before starting assistant text
        const updated = items.map((item) =>
          item.role === 'reasoning' && item.status === 'streaming' ? { ...item, status: 'complete' as const } : item
        );
        return [
          ...updated,
          createMessage({ role: 'assistant', label: 'assistant', status: 'streaming', content: message.content }),
        ];
      });
    } else if (message.type === 'tool_start') {
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
      setMessages((items) => {
        // Mark any remaining streaming reasoning as complete
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
      setMessages((items) => [
        ...items,
        createMessage({ role: 'tool', label: 'error', status: 'error', content: message.message }),
      ]);
    }
  });

  const canSend = useMemo(() => socket.connected && input.trim().length > 0, [socket.connected, input]);
  const displayName = appName === 'solo' ? 'Solo' : 'Wolo';

  // Sync messages to module-level cache for cross-navigation persistence
  useEffect(() => {
    messageCache.set(appName, messages);
  }, [appName, messages]);

  useEffect(() => {
    streamingCache.set(appName, streaming);
  }, [appName, streaming]);

  useEffect(() => {
    messagesRef.current?.scrollTo({ top: messagesRef.current.scrollHeight });
  }, [messages, streaming]);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!canSend) return;
    const content = input.trim();
    setMessages((items) => [...items, createMessage({ role: 'user', label: 'you', status: 'sent', content })]);
    setInput('');
    socket.send({ type: 'message', content });
  }

  return (
    <div className="flex flex-col h-[calc(100vh-120px)] border border-border rounded-lg bg-surface-1 overflow-hidden">
      {/* Header */}
      <header className="flex items-center justify-between px-5 py-3.5 border-b border-border bg-surface-2/50">
        <div>
          <span className="text-[11px] font-mono uppercase tracking-wider text-text-muted block mb-0.5">agent console</span>
          <h2 className="text-base font-serif text-text m-0">{displayName} Chat</h2>
        </div>
        <div className={`inline-flex items-center gap-2 text-[11px] font-mono px-2.5 py-1 rounded-md border ${
          socket.connected ? 'border-success/30 text-success' : 'border-warning/30 text-warning'
        }`}>
          <span className={`w-1.5 h-1.5 rounded-full ${socket.connected ? 'bg-success' : 'bg-warning animate-[pulse-dot_1.4s_ease-in-out_infinite]'}`} />
          {streaming ? 'streaming' : socket.connected ? 'connected' : 'connecting'}
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
          <div className="inline-flex items-center gap-1.5 px-3 py-2 rounded-md border border-border bg-surface-2 text-[11px] text-text-muted font-mono">
            <span className="w-1 h-1 rounded-full bg-accent-wolo animate-[pulse-dot_1s_ease-in-out_infinite]" />
            <span className="w-1 h-1 rounded-full bg-accent-wolo animate-[pulse-dot_1s_ease-in-out_infinite_0.15s]" />
            <span className="w-1 h-1 rounded-full bg-accent-wolo animate-[pulse-dot_1s_ease-in-out_infinite_0.3s]" />
          </div>
        ) : null}
      </section>

      {/* Input */}
      <form className="border-t border-border p-4 bg-surface-2/30" onSubmit={submit} aria-label="Chat composer">
        <div className="flex items-center justify-between mb-2 text-[11px] font-mono text-text-muted">
          <span>{socket.connected ? 'Ready' : 'Waiting for connection...'}</span>
          <kbd className="px-1.5 py-0.5 border border-border rounded text-[10px] bg-surface-2">Enter</kbd>
        </div>
        <div className="flex gap-2 items-end">
          <textarea
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder={socket.connected ? `Message ${displayName}...` : 'Connecting...'}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                submit(event);
              }
            }}
            className="flex-1 min-h-[80px] px-3.5 py-2.5 text-[13px] bg-surface-1 border border-border rounded-md text-text placeholder:text-text-muted outline-none focus:border-text-muted resize-y transition-colors"
          />
          <button
            type="submit"
            disabled={!canSend}
            className="px-4 py-2.5 text-[13px] font-medium rounded-md border border-accent-solo/30 bg-accent-solo-dim text-accent-solo cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed hover:bg-accent-solo/20 active:scale-[0.97] transition-all"
          >
            Send
          </button>
        </div>
      </form>
    </div>
  );
}
