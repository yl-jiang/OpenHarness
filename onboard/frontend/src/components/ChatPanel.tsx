import { FormEvent, useMemo, useState } from 'react';

import type { AppName } from '../api/types';
import { useWebSocket } from '../hooks/useWebSocket';
import { MarkdownView } from './MarkdownView';

interface ChatMessage {
  role: 'user' | 'assistant' | 'tool';
  content: string;
}

export function ChatPanel({ appName }: { appName: AppName }) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const socket = useWebSocket(appName, (message) => {
    if (message.type === 'delta') {
      setStreaming(true);
      setMessages((items) => [...items, { role: 'assistant', content: message.content }]);
    } else if (message.type === 'tool_start') {
      setMessages((items) => [...items, { role: 'tool', content: message.tool }]);
    } else if (message.type === 'complete') {
      setStreaming(false);
      setMessages((items) => [...items, { role: 'assistant', content: message.content }]);
    } else if (message.type === 'error') {
      setStreaming(false);
      setMessages((items) => [...items, { role: 'tool', content: message.message }]);
    }
  });
  const canSend = useMemo(() => socket.connected && input.trim().length > 0, [socket.connected, input]);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!canSend) {
      return;
    }
    const content = input.trim();
    setMessages((items) => [...items, { role: 'user', content }]);
    setInput('');
    socket.send({ type: 'message', content });
  }

  return (
    <div className="chat-panel glass-card">
      <div className="chat-messages">
        {messages.length === 0 ? <div className="empty-state">Start a local {appName} chat.</div> : null}
        {messages.map((message, index) => (
          <div key={index} className={`message message-${message.role}`}>
            {message.role === 'assistant' ? <MarkdownView content={message.content} /> : message.content}
          </div>
        ))}
        {streaming ? <span className="cursor">|</span> : null}
      </div>
      <form className="chat-input" onSubmit={submit}>
        <textarea
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder={socket.connected ? `Message ${appName}...` : 'Connecting...'}
          onKeyDown={(event) => {
            if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
              submit(event);
            }
          }}
        />
        <button disabled={!canSend}>Send</button>
      </form>
    </div>
  );
}
