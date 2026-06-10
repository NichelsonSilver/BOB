import { useEffect } from "react";

import type { BotStatus } from "../lib/api";
import { useBotsStore } from "../stores/botsStore";

type Frame = { event: string; data: unknown };

/**
 * Opens a single WebSocket to /api/ws and routes frames into the global
 * store. Reconnects with a simple backoff; the server is the source of
 * truth, so on reconnect we just start receiving fresh snapshots again.
 */
export function useBotsWebSocket() {
  const setSnapshot = useBotsStore((s) => s.setSnapshot);
  const setConnected = useBotsStore((s) => s.setConnected);

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
          if (frame.event === "bots.snapshot") {
            setSnapshot(frame.data as BotStatus[]);
          }
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
  }, [setSnapshot, setConnected]);
}
