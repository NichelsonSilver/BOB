import { create } from "zustand";

import type { ConnStatusFrame, MarketTick } from "../types";

/**
 * Estado que NO viene de la API: lo que el usuario eligió mirar y lo que llega
 * por WebSocket a ritmo de tick.
 *
 * La división con react-query es deliberada. Los eventos lentos e importantes
 * —una vela cerrada, un pronóstico nuevo— invalidan la query y el dashboard
 * vuelve a leer de la DB, que es la fuente de verdad. Los eventos rápidos y
 * cosméticos —el precio en curso, una vez por segundo— viven acá y no tocan la
 * red. Sin esa división habría dos versiones del mismo pronóstico en pantalla
 * y ninguna forma de saber cuál está mirando el usuario.
 *
 * Las preferencias de vista (símbolo, timeframe, leverage, nivel del cono) se
 * guardan en localStorage y no en el backend. Es la contracara de que Settings
 * sea de solo lectura: lo que el usuario mueve no cambia ningún modelo, así
 * que tampoco tiene por qué viajar al servidor.
 */

const PREFS_KEY = "bob.prefs.v1";

type Prefs = {
  symbol: string | null;
  timeframe: string | null;
  leverage: number;
  coneAlpha: string;
};

const DEFAULT_PREFS: Prefs = {
  // null = "usar el default del backend". Fijar "ETHUSDT" acá haría que
  // cambiar la watchlist en el .env no se notara en el dashboard.
  symbol: null,
  timeframe: null,
  // 1x: el usuario es averso al riesgo y ya pasó por una liquidación. El
  // slider arranca donde no hay liquidación posible, y subirlo es un acto
  // deliberado suyo.
  leverage: 1,
  coneAlpha: "0.20",
};

function loadPrefs(): Prefs {
  try {
    const raw = localStorage.getItem(PREFS_KEY);
    if (!raw) return DEFAULT_PREFS;
    return { ...DEFAULT_PREFS, ...(JSON.parse(raw) as Partial<Prefs>) };
  } catch {
    return DEFAULT_PREFS;
  }
}

function savePrefs(prefs: Prefs): void {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
  } catch {
    /* modo privado o storage lleno: las prefs son un lujo, no se rompe nada */
  }
}

type AppState = Prefs & {
  wsConnected: boolean;
  lastTick: MarketTick | null;
  connStatus: ConnStatusFrame | null;
  analysisError: string | null;
  /** `open_time` del último pronóstico anunciado por WS. Solo para animar el
   *  aviso de "llegó uno nuevo": el dato en sí se relee de la API. */
  lastForecastOpenTime: number | null;

  setConnected: (v: boolean) => void;
  setTick: (t: MarketTick) => void;
  setConnStatus: (s: ConnStatusFrame) => void;
  setAnalysisError: (detail: string | null) => void;
  noteForecast: (openTime: number) => void;

  setSymbol: (s: string | null) => void;
  setTimeframe: (t: string | null) => void;
  setLeverage: (l: number) => void;
  setConeAlpha: (a: string) => void;
};

export const useAppStore = create<AppState>((set, get) => {
  const persist = () => {
    const { symbol, timeframe, leverage, coneAlpha } = get();
    savePrefs({ symbol, timeframe, leverage, coneAlpha });
  };

  return {
    ...loadPrefs(),
    wsConnected: false,
    lastTick: null,
    connStatus: null,
    analysisError: null,
    lastForecastOpenTime: null,

    setConnected: (v) => set({ wsConnected: v }),
    setTick: (t) => set({ lastTick: t }),
    setConnStatus: (s) => set({ connStatus: s }),
    setAnalysisError: (detail) => set({ analysisError: detail }),
    noteForecast: (openTime) =>
      set({ lastForecastOpenTime: openTime, analysisError: null }),

    setSymbol: (s) => {
      set({ symbol: s });
      persist();
    },
    setTimeframe: (t) => {
      set({ timeframe: t });
      persist();
    },
    setLeverage: (l) => {
      set({ leverage: l });
      persist();
    },
    setConeAlpha: (a) => {
      set({ coneAlpha: a });
      persist();
    },
  };
});

/** Precio en curso: kline si llegó, mark price si no. `null` si no hay tick. */
export function tickPrice(tick: MarketTick | null): number | null {
  if (!tick) return null;
  if (typeof tick.price === "number") return tick.price;
  if (typeof tick.mark_price === "number") return tick.mark_price;
  return null;
}
