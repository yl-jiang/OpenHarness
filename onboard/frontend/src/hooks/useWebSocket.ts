import { useEffect, useRef, useState } from 'react';

import type { AppName, WsClientMessage, WsServerMessage } from '../api/types';

type Listener = (message: WsServerMessage) => void;

interface PersistentSocket {
  ws: WebSocket;
  listeners: Set<Listener>;
  connected: boolean;
}

// Module-level persistent connections keyed by app — survive component unmount
const sockets = new Map<AppName, PersistentSocket>();

function getOrCreate(app: AppName): PersistentSocket {
  const existing = sockets.get(app);
  if (existing && existing.ws.readyState <= WebSocket.OPEN) {
    return existing;
  }

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(`${protocol}//${window.location.host}/ws/chat/${app}`);
  const entry: PersistentSocket = { ws, listeners: new Set(), connected: false };

  ws.onopen = () => {
    entry.connected = true;
    // Notify listeners of state change by sending a synthetic event
    for (const listener of entry.listeners) {
      listener({ type: '_connected' } as unknown as WsServerMessage);
    }
  };
  ws.onclose = () => {
    entry.connected = false;
    sockets.delete(app);
    for (const listener of entry.listeners) {
      listener({ type: '_disconnected' } as unknown as WsServerMessage);
    }
  };
  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data) as WsServerMessage;
    for (const listener of entry.listeners) {
      listener(msg);
    }
  };

  sockets.set(app, entry);
  return entry;
}

export function useWebSocket(app: AppName, onMessage: (message: WsServerMessage) => void) {
  const [connected, setConnected] = useState(() => sockets.get(app)?.connected ?? false);
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;

  useEffect(() => {
    const entry = getOrCreate(app);
    setConnected(entry.connected);

    const listener: Listener = (msg) => {
      // Handle synthetic connection events
      if ((msg as unknown as { type: string }).type === '_connected') {
        setConnected(true);
        return;
      }
      if ((msg as unknown as { type: string }).type === '_disconnected') {
        setConnected(false);
        return;
      }
      onMessageRef.current(msg);
    };

    entry.listeners.add(listener);
    return () => {
      entry.listeners.delete(listener);
      // Do NOT close the socket — keep it alive for when user navigates back
    };
  }, [app]);

  return {
    connected,
    send: (message: WsClientMessage) => {
      const entry = sockets.get(app);
      if (entry?.ws.readyState === WebSocket.OPEN) {
        entry.ws.send(JSON.stringify(message));
      }
    },
  };
}
