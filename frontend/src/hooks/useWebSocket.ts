import { useEffect } from "react";

import { useAppStore } from "../stores/appStore";

type Frame = { event: string; data: unknown };

/**
 * Opens a single WebSocket to /api/ws and routes frames into the global
 * store. Reconnects with a simple backoff; the server is the source of
 * truth, so on reconnect we just start receiving fresh events again.
 *
 * Eventos del backend (ver backend/src/bob/api/ws.py): "signal.new",
 * "signal.update", "market.tick", "paper.outcome", "conn.status".
 * El routing por evento se implementa en Fase 6.
 */
export function useAppWebSocket() {
  const setConnected = useAppStore((s) => s.setConnected);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let retryDelay = 1000;
    let cancelled = false;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;

    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${window.location.host}/api/ws`;

    const connect = () => {
      if (cancelled) return;
      ws = new WebSocket(url);

      ws.onopen = () => {
        setConnected(true);
        retryDelay = 1000;
      };

      ws.onmessage = (ev) => {
        try {
          const frame = JSON.parse(ev.data) as Frame;
          void frame; // Fase 6: routear por frame.event a los stores
        } catch {
          // ignore malformed frames
        }
      };

      ws.onclose = () => {
        setConnected(false);
        if (cancelled) return;
        retryTimer = setTimeout(connect, retryDelay);
        retryDelay = Math.min(retryDelay * 2, 15_000);
      };

      ws.onerror = () => {
        ws?.close();
      };
    };

    connect();

    return () => {
      cancelled = true;
      if (retryTimer) clearTimeout(retryTimer);
      ws?.close();
    };
  }, [setConnected]);
}
