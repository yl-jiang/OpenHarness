import { useEffect, useRef, useState } from 'react';

import type { AppName, WsClientMessage, WsServerMessage } from '../api/types';

export function useWebSocket(app: AppName, onMessage: (message: WsServerMessage) => void) {
  const [connected, setConnected] = useState(false);
  const socketRef = useRef<WebSocket | null>(null);
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;

  useEffect(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const socket = new WebSocket(`${protocol}//${window.location.host}/ws/chat/${app}`);
    socketRef.current = socket;
    socket.onopen = () => setConnected(true);
    socket.onclose = () => setConnected(false);
    socket.onmessage = (event) => {
      onMessageRef.current(JSON.parse(event.data) as WsServerMessage);
    };
    return () => socket.close();
  }, [app]);

  return {
    connected,
    send: (message: WsClientMessage) => {
      if (socketRef.current?.readyState === WebSocket.OPEN) {
        socketRef.current.send(JSON.stringify(message));
      }
    },
  };
}
