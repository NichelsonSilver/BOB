import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { useAppStore } from "../stores/appStore";
import type {
  AnalysisErrorFrame,
  CandleFrame,
  ConnStatusFrame,
  ForecastFrame,
  MarketTick,
} from "../types";

type Frame = { event: string; data: unknown };

/**
 * Un solo WebSocket a `/api/ws`, con reconexión por backoff, que rutea los
 * frames del backend (`bob/api/ws.py`).
 *
 * La regla de ruteo: **los eventos lentos invalidan queries, los rápidos van
 * al store.** Una vela cerrada o un pronóstico nuevo llegan cada 15 minutos y
 * son la información que el usuario va a leer con cuidado: se invalida la
 * query y el dashboard vuelve a pedirle el dato a la DB, que es la fuente de
 * verdad y la que sabe si el pronóstico ya se resolvió. El precio en curso
 * llega una vez por segundo y solo sirve para el header: va al store y no
 * toca la red.
 *
 * Lo que se evita con eso es tener dos versiones del mismo pronóstico en
 * pantalla —una del frame y otra de la API— sin manera de saber cuál se está
 * mirando.
 */
export function useAppWebSocket() {
  const queryClient = useQueryClient();
  const setConnected = useAppStore((s) => s.setConnected);
  const setTick = useAppStore((s) => s.setTick);
  const setConnStatus = useAppStore((s) => s.setConnStatus);
  const setAnalysisError = useAppStore((s) => s.setAnalysisError);
  const noteForecast = useAppStore((s) => s.noteForecast);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let retryDelay = 1000;
    let cancelled = false;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;

    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${window.location.host}/api/ws`;

    const route = (frame: Frame) => {
      switch (frame.event) {
        case "market.tick":
          setTick(frame.data as MarketTick);
          break;

        case "market.candle": {
          const candle = frame.data as CandleFrame;
          setTick({
            symbol: candle.symbol,
            timeframe: candle.timeframe,
            open_time: candle.open_time,
            price: candle.close,
            high: candle.high,
            low: candle.low,
            volume: candle.volume,
            closed: true,
          });
          void queryClient.invalidateQueries({ queryKey: ["candles"] });
          break;
        }

        case "conn.status":
          setConnStatus(frame.data as ConnStatusFrame);
          break;

        case "analysis.forecast": {
          const forecast = frame.data as ForecastFrame;
          noteForecast(forecast.open_time);
          // El frame no trae `forecast_id` ni el resultado forward: eso lo
          // sabe la DB. Se releen las tres vistas que dependen del pronóstico.
          void queryClient.invalidateQueries({ queryKey: ["latest"] });
          void queryClient.invalidateQueries({ queryKey: ["projection"] });
          void queryClient.invalidateQueries({ queryKey: ["features"] });
          void queryClient.invalidateQueries({ queryKey: ["history"] });
          break;
        }

        case "analysis.error":
          setAnalysisError((frame.data as AnalysisErrorFrame).detail);
          break;

        default:
          break; // evento de una fase futura: se ignora, no se rompe
      }
    };

    const connect = () => {
      if (cancelled) return;
      ws = new WebSocket(url);

      ws.onopen = () => {
        setConnected(true);
        retryDelay = 1000;
        // Al reconectar pudimos habernos perdido barras enteras. El servidor
        // es la fuente de verdad y no reenvía historia: se relee todo.
        void queryClient.invalidateQueries();
      };

      ws.onmessage = (ev) => {
        try {
          route(JSON.parse(ev.data as string) as Frame);
        } catch {
          // frame malformado: se descarta sin tumbar la conexión
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
  }, [
    queryClient,
    setAnalysisError,
    setConnStatus,
    setConnected,
    setTick,
    noteForecast,
  ]);
}
