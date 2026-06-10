/**
 * Typed REST client for the BOB backend.
 *
 * Vite proxies /api → http://localhost:8000 in dev, so we can call
 * relative paths here and have it work both in dev and when the FE is
 * served from the same origin as the API.
 */

export type BotStatus = {
  bot_id: string;
  symbol: string;
  direction: string;
  mode: string;
  state: string;
  n_grids: number;
  price_low: string;
  price_high: string;
  investment_usdt: string;
  leverage: number;
  last_price: string | null;
  realized_pnl?: string;
  grid_trades_count?: number;
  total_volume?: string;
  live_orders_count?: number;
};

export type Fill = {
  bot_id: string;
  client_order_id: string;
  level_index: number;
  side: string;
  price: string;
  quantity: string;
  fee: string;
  pnl: string;
  mode: string;
  filled_at: string;
};

export type PnlBreakdown = {
  realized_pnl: string;
  total_fees: string;
  gross_volume: string;
  fill_count: number;
};

export type SettingsResponse = {
  env: string;
  trading_account_id: string;
  limits: {
    max_total_capital: number;
    max_concurrent_bots: number;
    max_leverage: number;
  };
  runtime: {
    active_bots: number;
    total_bots: number;
  };
};

export type PointsSummary = {
  gross_volume: string;
  maker_rebates: string;
  taker_fees: string;
  fill_count: number;
  estimated_points: string;
};

export type BotPreview = {
  qty_per_grid: string;
  avg_price: string;
  notional_per_grid: string;
  total_notional: string;
  levels_count: number;
  max_grids_allowed: number;
  profit_per_grid_usdt: string;
  liquidation_price: string;
  margin_required: string;
  inversion_per_grid: string;
  tick_size: string;
  lot_size: string;
  min_notional: string;
  warnings: string[];
};

export type Instrument = {
  symbol: string;
  tick_size: string | null;
  lot_size: string | null;
  min_notional: string | null;
  base: string | null;
  quote: string | null;
};

export type CreateBotBody = {
  bot_id: string;
  symbol: string;
  direction: "long" | "short" | "neutral";
  price_low: string;
  price_high: string;
  n_grids: number;
  investment_usdt: string;
  leverage: number;
  spacing?: "arithmetic" | "geometric";
  stop_loss_pct?: string;
  take_profit_pct?: string;
  out_of_range_action?: "pause" | "stop";
  tick_size?: string;
  lot_size?: string;
  maker_fee?: string;
  mode: "paper" | "live";
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!r.ok) {
    const text = await r.text();
    throw new Error(`${r.status} ${r.statusText} — ${text}`);
  }
  if (r.status === 204) return undefined as T;
  return r.json() as Promise<T>;
}

export const api = {
  health: () => request<{ status: string }>("/api/health"),
  healthGrvt: () => request<Record<string, unknown>>("/api/health/grvt"),

  getSettings: () => request<SettingsResponse>("/api/settings"),

  listBots: () => request<BotStatus[]>("/api/bots"),
  getBot: (id: string) => request<BotStatus>(`/api/bots/${id}`),
  createBot: (body: CreateBotBody) =>
    request<{ status: string; bot_id: string; mode: string; preview: BotPreview }>(
      "/api/bots",
      { method: "POST", body: JSON.stringify(body) },
    ),
  previewBot: (body: CreateBotBody) =>
    request<BotPreview>("/api/bots/preview", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  pauseBot: (id: string) =>
    request<{ status: string }>(`/api/bots/${id}/pause`, { method: "POST" }),
  resumeBot: (id: string) =>
    request<{ status: string }>(`/api/bots/${id}/resume`, { method: "POST" }),
  stopBot: (id: string) =>
    request<{ status: string }>(`/api/bots/${id}/stop`, { method: "POST" }),
  killSwitch: () =>
    request<{ status: string; bots_stopped: number }>("/api/bots/kill-switch", {
      method: "POST",
    }),

  listFills: (params?: {
    bot_id?: string;
    symbol?: string;
    limit?: number;
    offset?: number;
  }) => {
    const qs = new URLSearchParams();
    if (params?.bot_id) qs.set("bot_id", params.bot_id);
    if (params?.symbol) qs.set("symbol", params.symbol);
    if (params?.limit) qs.set("limit", String(params.limit));
    if (params?.offset) qs.set("offset", String(params.offset));
    return request<Fill[]>(`/api/history/fills${qs.toString() ? `?${qs}` : ""}`);
  },
  getPnl: (bot_id?: string) =>
    request<PnlBreakdown>(
      `/api/history/pnl${bot_id ? `?bot_id=${bot_id}` : ""}`,
    ),
  getEquityCurve: (bot_id?: string) =>
    request<{ timestamp: string; cumulative_pnl: string }[]>(
      `/api/history/equity-curve${bot_id ? `?bot_id=${bot_id}` : ""}`,
    ),
  getDailyPnl: (bot_id?: string) =>
    request<({ date: string } & PnlBreakdown)[]>(
      `/api/history/pnl/daily${bot_id ? `?bot_id=${bot_id}` : ""}`,
    ),

  getPoints: () => request<PointsSummary>("/api/points"),
  getPointsByBot: () =>
    request<Record<string, PointsSummary>>("/api/points/by-bot"),

  getSymbols: () =>
    request<{ symbols: string[]; instruments: Instrument[] }>(
      "/api/markets/symbols",
    ),
  getInstrument: (symbol: string) =>
    request<Instrument & { raw: Record<string, unknown> }>(
      `/api/markets/instrument/${symbol}`,
    ),
  getTicker: (symbol: string) =>
    request<Record<string, unknown>>(`/api/markets/ticker/${symbol}`),
  getFundingRate: (symbol: string, limit = 20) =>
    request<Record<string, unknown>>(
      `/api/markets/funding/${symbol}?limit=${limit}`,
    ),
  getRangeSuggestion: (params: {
    symbol: string;
    days?: number;
    mode?: "percentile" | "minmax" | "atr";
    investment_usdt?: number;
    leverage?: number;
  }) => {
    const qs = new URLSearchParams();
    if (params.days != null) qs.set("days", String(params.days));
    if (params.mode) qs.set("mode", params.mode);
    if (params.investment_usdt != null)
      qs.set("investment_usdt", String(params.investment_usdt));
    if (params.leverage != null) qs.set("leverage", String(params.leverage));
    return request<RangeSuggestion>(
      `/api/markets/range-suggestion/${params.symbol}?${qs}`,
    );
  },
};

export type RangeSuggestion = {
  symbol: string;
  mode: "percentile" | "minmax" | "atr";
  days: number;
  sample_size: number;
  price_low: string;
  price_high: string;
  atr: string;
  volatility_pct: string;
  suggested_n_grids: number;
  tick_size: string;
  min_notional: string;
};
