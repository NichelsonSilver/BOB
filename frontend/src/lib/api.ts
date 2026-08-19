/**
 * Typed REST client for the BOB backend.
 *
 * Vite proxies /api → http://localhost:8000 in dev, so we can call
 * relative paths here and have it work both in dev and when the FE is
 * served from the same origin as the API.
 *
 * Los types de Signal/Backtest/Paper se agregan en Fase 6 junto con sus
 * endpoints — mantener espejo 1:1 con backend/src/bob/db/models.py.
 */

export type HealthResponse = {
  status: string;
  service: string;
  mode: string;
  watchlist: string[];
  default_timeframe: string;
  signal_threshold: number;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}: ${body}`);
  }
  return (await res.json()) as T;
}

export const api = {
  health: () => request<HealthResponse>("/api/health"),
};
