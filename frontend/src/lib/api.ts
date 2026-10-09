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
  FreshnessReport,
  ForwardArtifact,
  GateResponse,
  HealthResponse,
  HistoryResponse,
  LabExperiments,
  LatestResponse,
  LiquidityReport,
  RankingVerdict,
  StrengthReport,
  NarrativeResponse,
  PanelStatus,
  Panorama,
  ProjectionResponse,
  RiskFilterEvidence,
  RunDetail,
  RunsResponse,
  SettingsResponse,
  SizingResponse,
  StopFilterEvidence,
  TechnicalResponse,
  TechnicalTf,
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

  stopFilter: () => request<StopFilterEvidence>("/api/backtest/stopfilter"),

  settings: () => request<SettingsResponse>("/api/settings"),

  /** MAREA: el último reporte guardado por `python -m marea.monitor`. */
  liquidity: () => request<LiquidityReport>("/api/macro/liquidity"),

  /** MAREA: fuerza relativa por clase. Descriptiva: no es flujo ni pronóstico. */
  strength: (weeks: number) => request<StrengthReport>(`/api/macro/strength${query({ weeks })}`),

  /** MAREA: veredicto del gate M3, leído del artefacto versionado. */
  rankingVerdict: () => request<RankingVerdict>("/api/macro/ranking-verdict"),

  /** MAREA: antigüedad de cada fuente, para el encabezado. */
  freshness: () => request<FreshnessReport>("/api/macro/freshness"),

  /** MAREA: ciclo y cripto de la M5. ~4 s la primera vez; después, caché del servidor. */
  panorama: () => request<Panorama>("/api/macro/panorama"),

  /** Laboratorio: cada experimento con su veredicto, leído de su artefacto (M6.4). */
  labExperiments: () => request<LabExperiments>("/api/lab/experiments"),

  /** Forward del panel diario (10-bis): estado operativo, nunca el IC. */
  panelStatus: () => request<PanelStatus>("/api/macro/panel-status"),

  narrative: (t: { symbol?: string }) =>
    request<NarrativeResponse>(`/api/analysis/narrative${query(t)}`),

  technical: (p: { symbol?: string; tf: TechnicalTf }) =>
    request<TechnicalResponse>(`/api/analysis/technical${query(p)}`),

  /** Aritmética pura sobre lo que tipea el usuario: no lee la DB. */
  sizing: (p: {
    capital: number;
    risk_pct: number;
    entry: number;
    stop: number;
    leverage: number;
    target?: number;
    maker_entry?: boolean;
    sigma_horizon?: number;
    regime_elevated?: boolean;
  }) => request<SizingResponse>(`/api/risk/sizing${query(p)}`),
};
