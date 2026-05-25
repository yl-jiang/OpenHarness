import { FormEvent, useEffect, useMemo, useRef, useState } from 'react';

import type { AppName } from '../api/types';
import { useWebSocket } from '../hooks/useWebSocket';
import { MarkdownView } from './MarkdownView';

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'tool';
  content: string;
  label: string;
  status: 'sent' | 'streaming' | 'running' | 'complete' | 'error';
  timestamp: Date;
}

export function ChatPanel({ appName }: { appName: AppName }) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
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
    if (message.type === 'delta') {
      setStreaming(true);
      setMessages((items) => {
        const last = items.at(-1);
        if (last?.role === 'assistant' && last.status === 'streaming') {
          return [
            ...items.slice(0, -1),
            { ...last, content: `${last.content}${message.content}` },
          ];
        }
        return [
          ...items,
          createMessage({
            role: 'assistant',
            label: 'assistant',
            status: 'streaming',
            content: message.content,
          }),
        ];
      });
    } else if (message.type === 'tool_start') {
      setMessages((items) => [
        ...items,
        createMessage({
          role: 'tool',
          label: 'tool start',
          status: 'running',
          content: message.tool,
        }),
      ]);
    } else if (message.type === 'tool_complete') {
      setMessages((items) => [
        ...items,
        createMessage({
          role: 'tool',
          label: 'tool complete',
          status: 'complete',
          content: `${message.tool}${message.result ? `\n${message.result}` : ''}`,
        }),
      ]);
    } else if (message.type === 'complete') {
      setStreaming(false);
      if (!message.content.trim()) {
        return;
      }
      setMessages((items) => {
        const last = items.at(-1);
        if (last?.role === 'assistant' && last.status === 'streaming') {
          return [
            ...items.slice(0, -1),
            { ...last, content: message.content, status: 'complete' },
          ];
        }
        return [
          ...items,
          createMessage({
            role: 'assistant',
            label: 'assistant',
            status: 'complete',
            content: message.content,
          }),
        ];
      });
    } else if (message.type === 'error') {
      setStreaming(false);
      setMessages((items) => [
        ...items,
        createMessage({
          role: 'tool',
          label: 'error',
          status: 'error',
          content: message.message,
        }),
      ]);
    }
  });
  const canSend = useMemo(() => socket.connected && input.trim().length > 0, [socket.connected, input]);
  const displayName = appName === 'solo' ? 'Solo' : 'Wolo';
  const statusLabel = socket.connected ? 'linked' : 'connecting';
  const statusText = streaming ? 'streaming' : statusLabel;

  useEffect(() => {
    messagesRef.current?.scrollTo({ top: messagesRef.current.scrollHeight });
  }, [messages, streaming]);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!canSend) {
      return;
    }
    const content = input.trim();
    setMessages((items) => [
      ...items,
      createMessage({
        role: 'user',
        label: 'you',
        status: 'sent',
        content,
      }),
    ]);
    setInput('');
    socket.send({ type: 'message', content });
  }

  return (
    <div className="chat-panel">
      <header className="chat-console-header">
        <div>
          <span className="chat-kicker">local agent console</span>
          <h2>{displayName} chat</h2>
        </div>
        <div className={`chat-link-status ${socket.connected ? 'is-linked' : ''}`}>
          <span className="chat-status-dot" />
          {statusText}
        </div>
      </header>
      <div className="chat-console-body">
        <aside className="chat-session-rail" aria-label="Chat session details">
          <div>
            <span>session</span>
            <strong>{appName}</strong>
          </div>
          <div>
            <span>events</span>
            <strong>{messages.length}</strong>
          </div>
          <div>
            <span>mode</span>
            <strong>local ws</strong>
          </div>
        </aside>
        <section ref={messagesRef} className="chat-messages" aria-live="polite">
          {messages.length === 0 ? (
            <div className="chat-empty-state">
              <span className="chat-empty-glyph">NB</span>
              <div>
                <h3>Open a local run loop.</h3>
                <p>
                  Ask for a summary, search past records, or trigger a work-log action.
                  Tool activity will appear as compact status events.
                </p>
              </div>
            </div>
          ) : null}
          {messages.map((message, index) => (
            <article
              key={message.id}
              className={`message message-${message.role} message-${message.status}`}
              style={{ animationDelay: `${Math.min(index, 6) * 28}ms` }}
            >
              <div className="message-meta">
                <span className="message-label">{message.label}</span>
                <span className="message-time">
                  {message.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                </span>
              </div>
              <div className="message-content">
                {message.role === 'assistant' ? <MarkdownView content={message.content} /> : message.content}
              </div>
            </article>
          ))}
          {streaming ? (
            <div className="stream-indicator">
              <span />
              <span />
              <span />
            </div>
          ) : null}
        </section>
      </div>
      <form className="chat-input" onSubmit={submit} aria-label="Chat composer">
        <div className="chat-input-toolbar">
          <span>{socket.connected ? 'Ready for next instruction' : 'Waiting for websocket'}</span>
          <kbd>Cmd/Ctrl Enter</kbd>
        </div>
        <textarea
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder={socket.connected ? `Message ${displayName}...` : 'Connecting...'}
          onKeyDown={(event) => {
            if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
              submit(event);
            }
          }}
        />
        <button type="submit" disabled={!canSend}>
          Send
        </button>
      </form>
    </div>
  );
}
