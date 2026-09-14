/**
 * Cliente REST tipado del backend de BOB.
 *
 * Vite proxea `/api` → el backend, así que todo se pide con rutas relativas y
 * funciona igual en dev y servido desde el mismo origen.
 *
 * Toda esta capa es de **lectura**. No hay un solo POST ni PATCH acá, y no es
 * un pendiente: mientras corre la validación forward, un endpoint que
 * escribiera cambiaría la muestra que se está midiendo. Ver
 * `backend/src/bob/api/routes/_common.py`.
 */

import type {
  CandlesResponse,
  CoverageResponse,
  FeaturesResponse,
  ForwardArtifact,
  GateResponse,
  HealthResponse,
  HistoryResponse,
  LatestResponse,
  ProjectionResponse,
  RiskFilterEvidence,
  RunDetail,
  RunsResponse,
  SettingsResponse,
} from "../types";

/** Error de la API con su status, para poder distinguir 404 de "se cayó". */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
    readonly path: string,
  ) {
    super(`${status} en ${path}: ${detail}`);
    this.name = "ApiError";
  }
}

async function request<T>(path: string): Promise<T> {
  const res = await fetch(path, { headers: { Accept: "application/json" } });
  if (!res.ok) {
    // El backend responde {error, detail, path} en sus handlers; si algo
    // devolviera otra cosa (un proxy, por ejemplo), se usa el texto crudo.
    let detail = await res.text().catch(() => "");
    try {
      const body = JSON.parse(detail) as { detail?: unknown };
      if (body.detail !== undefined) detail = String(body.detail);
    } catch {
      /* no era JSON: queda el texto */
    }
    throw new ApiError(res.status, detail, path);
  }
  return (await res.json()) as T;
}

/** `?symbol=&timeframe=` solo cuando hay valor: el backend tiene defaults. */
function query(params: Record<string, string | number | boolean | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") search.set(key, String(value));
  }
  const s = search.toString();
  return s ? `?${s}` : "";
}

type Target = { symbol?: string; timeframe?: string };

export const api = {
  health: () => request<HealthResponse>("/api/health"),

  candles: (t: Target & { limit?: number }) =>
    request<CandlesResponse>(`/api/market/candles${query(t)}`),

  latest: (t: Target) => request<LatestResponse>(`/api/analysis/latest${query(t)}`),

  projection: (t: Target & { leverage: number }) =>
    request<ProjectionResponse>(`/api/analysis/projection${query(t)}`),

  features: (t: Target) => request<FeaturesResponse>(`/api/analysis/features${query(t)}`),

  history: (t: Target & { limit?: number; offset?: number; status?: string }) =>
    request<HistoryResponse>(`/api/history/forecasts${query(t)}`),

  coverage: (t: Target & { baselines?: boolean }) =>
    request<CoverageResponse>(`/api/history/coverage${query(t)}`),

  /** URL del export — se abre en el navegador, no se descarga por fetch. */
  csvUrl: (t: Target & { status?: string }) =>
    `/api/history/forecasts.csv${query(t)}`,

  runs: () => request<RunsResponse>("/api/backtest/runs"),

  run: (runId: string) =>
    request<RunDetail>(`/api/backtest/runs/${encodeURIComponent(runId)}`),

  gate: (params: { variant?: string; vol_kind?: string } = {}) =>
    request<GateResponse>(`/api/backtest/gate${query(params)}`),

  forward: () => request<ForwardArtifact>("/api/backtest/forward"),

  riskFilter: () => request<RiskFilterEvidence>("/api/backtest/riskfilter"),

  settings: () => request<SettingsResponse>("/api/settings"),
};
