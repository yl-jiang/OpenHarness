import { useCallback, useEffect, useRef, useState } from 'react';

import type { AppName, WsClientMessage, WsServerMessage } from '../api/types';

type Listener = (message: WsServerMessage) => void;

interface PersistentSocket {
  ws: WebSocket;
  listeners: Set<Listener>;
  connected: boolean;
  sessionKey: string | null;
}

// Module-level persistent connections keyed by app — survive component unmount
const sockets = new Map<AppName, PersistentSocket>();
// Track which session_key was assigned by the server
const sessionKeys = new Map<AppName, string>();

function createSocket(app: AppName, sessionKeyOverride?: string): PersistentSocket {
  // Close existing if any
  const existing = sockets.get(app);
  if (existing && existing.ws.readyState <= WebSocket.OPEN) {
    existing.ws.close();
  }
  sockets.delete(app);

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  let url = `${protocol}//${window.location.host}/ws/chat/${app}`;
  if (sessionKeyOverride) {
    url += `?session=${encodeURIComponent(sessionKeyOverride)}`;
  }
  const ws = new WebSocket(url);
  const entry: PersistentSocket = { ws, listeners: new Set(), connected: false, sessionKey: sessionKeyOverride ?? null };

  ws.onopen = () => {
    entry.connected = true;
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
    // Track session_key from server
    if (msg.type === 'session_key') {
      entry.sessionKey = msg.session_key;
      sessionKeys.set(app, msg.session_key);
    }
    for (const listener of entry.listeners) {
      listener(msg);
    }
  };

  sockets.set(app, entry);
  return entry;
}

function getOrCreate(app: AppName): PersistentSocket {
  const existing = sockets.get(app);
  if (existing && existing.ws.readyState <= WebSocket.OPEN) {
    return existing;
  }
  return createSocket(app);
}

export function useWebSocket(app: AppName, onMessage: (message: WsServerMessage) => void) {
  const [connected, setConnected] = useState(() => sockets.get(app)?.connected ?? false);
  const [sessionKey, setSessionKey] = useState<string | null>(() => sessionKeys.get(app) ?? null);
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;

  useEffect(() => {
    const entry = getOrCreate(app);
    setConnected(entry.connected);
    if (entry.sessionKey) setSessionKey(entry.sessionKey);

    const listener: Listener = (msg) => {
      if ((msg as unknown as { type: string }).type === '_connected') {
        setConnected(true);
        return;
      }
      if ((msg as unknown as { type: string }).type === '_disconnected') {
        setConnected(false);
        return;
      }
      if (msg.type === 'session_key') {
        setSessionKey(msg.session_key);
      }
      onMessageRef.current(msg);
    };

    entry.listeners.add(listener);
    return () => {
      entry.listeners.delete(listener);
    };
  }, [app]);

  const reconnect = useCallback((newSessionKey?: string) => {
    const entry = createSocket(app, newSessionKey);
    setConnected(false);
    if (newSessionKey) setSessionKey(newSessionKey);

    const listener: Listener = (msg) => {
      if ((msg as unknown as { type: string }).type === '_connected') {
        setConnected(true);
        return;
      }
      if ((msg as unknown as { type: string }).type === '_disconnected') {
        setConnected(false);
        return;
      }
      if (msg.type === 'session_key') {
        setSessionKey(msg.session_key);
      }
      onMessageRef.current(msg);
    };
    entry.listeners.add(listener);
  }, [app]);

  return {
    connected,
    sessionKey,
    reconnect,
    send: (message: WsClientMessage) => {
      const entry = sockets.get(app);
      if (entry?.ws.readyState === WebSocket.OPEN) {
        entry.ws.send(JSON.stringify(message));
      }
    },
  };
}
