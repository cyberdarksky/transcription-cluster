import { useEffect, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useWSStore } from '@/store/websocket';

export function useDashboardWebSocket() {
  const wsRef = useRef<WebSocket | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const { setConnected, handleMessage } = useWSStore();
  const queryClient = useQueryClient();
  const invalidatedAt = useWSStore(s => s.invalidatedAt);
  const invalidateScope = useWSStore(s => s.invalidateScope);

  // Debounced, scoped invalidation — avoids refetch storms during bursts
  useEffect(() => {
    if (invalidatedAt === 0) return;
    const timer = setTimeout(() => {
      if (invalidateScope === 'workers') {
        queryClient.invalidateQueries({ queryKey: ['workers'] });
      } else {
        queryClient.invalidateQueries({ queryKey: ['jobs'] });
      }
      queryClient.invalidateQueries({ queryKey: ['stats'] });
    }, 400);
    return () => clearTimeout(timer);
  }, [invalidatedAt, invalidateScope, queryClient]);

  useEffect(() => {
    let dead = false;

    function connect() {
      if (dead) return;
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const ws = new WebSocket(`${protocol}//${window.location.host}/ws/dashboard`);
      wsRef.current = ws;

      ws.onopen = () => {
        if (dead) { ws.close(); return; }
        setConnected(true);
      };

      ws.onclose = () => {
        setConnected(false);
        if (!dead) {
          timerRef.current = setTimeout(connect, 3000);
        }
      };

      ws.onerror = () => {
        ws.close();
      };

      ws.onmessage = (e: MessageEvent) => {
        try {
          const msg = JSON.parse(e.data as string);
          handleMessage(msg);
          // Reply to ping
          if (msg.type === 'heartbeat') {
            if (ws.readyState === WebSocket.OPEN) {
              ws.send(JSON.stringify({ type: 'ping' }));
            }
          }
        } catch { /* ignore malformed */ }
      };
    }

    connect();

    return () => {
      dead = true;
      if (timerRef.current) clearTimeout(timerRef.current);
      wsRef.current?.close();
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
}
