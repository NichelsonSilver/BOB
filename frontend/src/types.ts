/**
 * Espejo 1:1 de lo que devuelve la API (backend/src/bob/api/routes/).
 *
 * Los campos opcionales lo son porque el backend puede devolver `null` de
 * verdad, no por comodidad: `analyst: null` significa "el analista no está
 * corriendo en este proceso" y `realized_vol: null` significa "el horizonte
 * todavía no cerró". Ninguno de los dos es cero, y el tipo tiene que
 * obligar a distinguirlos donde se dibujan.
 */

// --------------------------------------------------------------------- //
// Mercado
// --------------------------------------------------------------------- //

export type Candle = {
  open_time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

export type Gap = {
  from_open_time: number;
  to_open_time: number;
  missing_bars: number;
};

export type CandlesResponse = {
  symbol: string;
  timeframe: string;
  interval_ms: number;
  n: number;
  candles: Candle[];
  gaps: Gap[];
};

// --------------------------------------------------------------------- //
// Pronóstico y proyección
// --------------------------------------------------------------------- //

export type ConeBand = {
  alpha: number;
  nominal: number;
  ret_lo: number;
  ret_hi: number;
  price_lo: number;
  price_hi: number;
};

export type SetupProjection = {
  direction: string;
  entry_price: number;
  take_profit: number;
  stop_loss: number;
  tp_pct: number;
  sl_pct: number;
  risk_reward: number;
  probability: number;
  breakeven_probability: number;
  edge_pp: number;
  gross_ev_pct: number;
  cost_pct: number;
  net_ev_pct: number;
  horizon_bars: number;
  horizon_hours: number;
  sigma_horizon: number;
  leverage: number;
  margin_pct: number;
  roe_pct: number;
  roe_tp_pct: number;
  roe_sl_pct: number;
  liquidation_price: number;
  liq_distance_pct: number;
  liq_distance_sigmas: number;
  prob_touch_liquidation: number;
  liquidation_before_stop: boolean;
  max_safe_leverage: number;
  is_actionable: boolean;
  /** Parte del resultado y no de la vista: si la liquidación llega antes que
   *  el stop, eso no es un detalle que un frontend pueda olvidar de dibujar. */
  warnings: string[];
};

export type ConeCoverage = Record<
  string,
  { n: number; empirical: number | null; alpha_t: number }
>;

export type AnalystStatus = {
  symbol: string;
  timeframe: string;
  feature_set: string;
  vol_kind: string;
  fitted: boolean;
  refitting: boolean;
  bars_since_fit: number;
  candle_repair_pending: boolean;
  n_train: number;
  fit_through_ms: number | null;
  model_version: string | null;
  last_forecast_open_time: number | null;
  last_reference_price: number | null;
  regime: RegimeState | null;
  cone_coverage: ConeCoverage;
};

/** Filtro de régimen (Fase 5-bis): lo único del stack que pasó su criterio
 *  como REGLA DE DECISIÓN. Dice "a este leverage, ahora no" — nunca hacia
 *  dónde entrar. */
export type RegimeState = {
  sigma: number;
  threshold: number;
  percentile: number;
  q: number;
  elevated: boolean;
  n_reference: number;
};

/** La evidencia medida del filtro, servida desde el artefacto. Va SIEMPRE al
 *  lado del estado: un aviso sin su magnitud es una alarma, no un dato. */
export type RiskFilterEvidence = {
  artifact_id: string;
  preregistration: string | null;
  viable: boolean;
  reasons: string[];
  leverage: number;
  quantile: number;
  n_bars: number;
  n_events: number;
  effective_events: number;
  lift: number;
  lift_ci95: [number, number];
  capture_rate: number;
  reject_rate: number;
  false_reject_rate: number;
  baseline_lift: number;
  baseline_capture_rate: number;
  diff_ci95: [number, number];
};

export type Forecast = {
  forecast_id: string;
  symbol: string;
  timeframe: string;
  open_time: number;
  emitted_at: string;
  reference_price: number;
  sigma_forecast: number;
  sigma_backward: number;
  sigma_ratio: number | null;
  barrier_sigma_source: string;
  horizon_bars: number;
  cones: ConeBand[];
  projections: Record<string, SetupProjection>;
  probabilities: Record<string, number>;
  /** null en pronósticos anteriores a la Fase 5-bis. */
  regime: RegimeState | null;
  /** Regla 2: viaja en false porque el gate rechazó la discriminación. */
  probability_calibrated: boolean;
  model_version: string;
  fit_through_ms: number;
  n_train: number;
  status: string;
  realized_vol: number | null;
  vol_ratio: number | null;
  realized_return: number | null;
  cone_hits: Record<string, boolean>;
  age_bars: number;
  is_stale: boolean;
};

export type LatestResponse = {
  symbol: string;
  timeframe: string;
  forecast: Forecast | null;
  analyst: AnalystStatus | null;
  live_analyst_enabled: boolean;
};

export type ProjectionResponse = {
  symbol: string;
  timeframe: string;
  open_time: number;
  reference_price: number;
  leverage: number;
  sigma_horizon: number;
  barrier_sigma_source: string;
  probability_calibrated: boolean;
  config_source: string;
  projections: Record<string, SetupProjection>;
};

export type FeatureItem = { name: string; value: number | null };

export type FeaturesResponse = {
  symbol: string;
  timeframe: string;
  open_time: number;
  feature_set: string;
  n_features: number;
  n_missing: number;
  families: Record<string, FeatureItem[]>;
  age_bars: number;
};

// --------------------------------------------------------------------- //
// Historial y cobertura forward
// --------------------------------------------------------------------- //

export type ForecastRow = {
  forecast_id: string;
  open_time: number;
  emitted_at: string;
  reference_price: number;
  sigma_forecast: number;
  sigma_backward: number;
  horizon_bars: number;
  status: string;
  realized_vol: number | null;
  vol_ratio: number | null;
  realized_return: number | null;
  cone_hits: Record<string, boolean>;
  model_version: string;
  resolved_through: number | null;
};

export type HistoryResponse = {
  symbol: string;
  timeframe: string;
  counts: Record<string, number>;
  total: number;
  limit: number;
  offset: number;
  forecasts: ForecastRow[];
};

export type RegressionMetrics = {
  n: number;
  rmse: number;
  mae: number;
  r2: number;
  r2_vs_baseline: number | null;
  qlike: number;
  mincer_zarnowitz_alpha: number;
  mincer_zarnowitz_beta: number;
};

export type DieboldMariano = {
  statistic: number;
  p_value: number;
  verdict: string;
} | null;

export type IntervalMetrics = {
  n: number;
  nominal_coverage: number;
  empirical_coverage: number;
  mean_width: number;
  median_width: number;
  winkler_score: number;
};

export type CoverageResponse = {
  symbol: string;
  timeframe: string;
  n_resolved: number;
  n_open: number;
  n_gap: number;
  date_from: string;
  date_to: string;
  volatility: RegressionMetrics | null;
  vol_ratio_mean: number;
  vol_ratio_median: number;
  baselines: Record<string, RegressionMetrics>;
  dm_vs_ewma: DieboldMariano;
  dm_vs_har: DieboldMariano;
  cones: Record<string, IntervalMetrics>;
  /** IC por bootstrap de bloques. Tratar los aciertos como independientes
   *  daba un falso hallazgo de bajo-cobertura con apariencia de rigor. */
  cone_ci: Record<string, [number, number]>;
  n_blocks: number;
  model_versions: string[];
  setups: Record<string, Record<string, number>>;
  cache_age_s: number;
  with_baselines: boolean;
  mixed_sample: boolean;
};

// --------------------------------------------------------------------- //
// Gate y artefactos
// --------------------------------------------------------------------- //

export type GateThresholds = {
  min_auc: number;
  min_bss: number;
  max_calibration_error_pp: number;
};

export type GateVerdict = {
  calibra: boolean;
  discrimina: boolean;
  habilitado: boolean;
  thresholds: GateThresholds;
};

export type DirectionMetrics = {
  auc: number;
  brier: number;
  bss: number;
  calibration_error_pp: number;
  n_samples: number;
  breakeven: number;
  dm_p: number;
  dm_stat: number;
};

export type VolatilityBlock = {
  model: RegressionMetrics | null;
  baseline_ewma: RegressionMetrics | null;
  baseline_garch: RegressionMetrics | null;
  baseline_har: RegressionMetrics | null;
  dm_vs_ewma: { p_value: number; verdict: string } | null;
  dm_vs_har: { p_value: number; verdict: string } | null;
};

export type RunSummary = {
  run_id: string;
  variant: string;
  vol_kind: string;
  vol_kind_declared: boolean;
  model_kind: string | null;
  n_features: number;
  n_bars: number;
  date_from: string;
  date_to: string;
  runtime_s: number | null;
  directions: Record<string, DirectionMetrics>;
  gate: GateVerdict;
  volatility: VolatilityBlock;
  intervals: Record<string, Record<string, IntervalMetrics>> | null;
  family_importance: Record<string, number>;
};

export type RunsResponse = {
  n: number;
  thresholds: GateThresholds;
  runs: RunSummary[];
  latest_by_variant: Record<string, string>;
};

export type GateResponse = {
  variant: string;
  vol_kind: string;
  live_feature_set: string;
  run_id: string;
  date_from: string;
  date_to: string;
  n_bars: number;
  directions: Record<string, DirectionMetrics>;
  gate: GateVerdict;
  volatility: VolatilityBlock;
  intervals: Record<string, Record<string, IntervalMetrics>> | null;
};

export type CalibrationBucket = {
  lower: number;
  upper: number;
  n: number;
  mean_predicted: number;
  observed_rate: number;
  error_pp: number;
};

export type RunDetail = {
  run_id: string;
  gate: GateVerdict | null;
  artifact: {
    symbol: string;
    timeframe: string;
    n_bars: number;
    n_features: number;
    date_from: string;
    date_to: string;
    config: Record<string, unknown>;
    directions: Record<
      string,
      {
        n_samples: number;
        breakeven_prob: number;
        resolution_mix: Record<string, number>;
        model: {
          auc: number;
          brier: number;
          brier_skill_score: number;
          base_rate: number;
          mean_calibration_error_pp: number;
          ece: number;
          buckets: CalibrationBucket[];
        };
        trading: Record<string, number>;
      }
    >;
    volatility: Record<string, RegressionMetrics | Record<string, unknown>>;
    intervals: Record<string, Record<string, IntervalMetrics>>;
    importance_top: [string, number][];
    family_importance: Record<string, number>;
    runtime_s: number;
  };
};

export type ForwardArtifact = {
  artifact_id: string;
  frozen: boolean;
  report: CoverageResponse;
};

// --------------------------------------------------------------------- //
// Settings (espejo read-only)
// --------------------------------------------------------------------- //

export type SettingsResponse = {
  editable: boolean;
  editable_reason: string;
  runtime: {
    watchlist: string[];
    default_timeframe: string;
    known_timeframes: string[];
    signal_threshold: number;
    live_data: boolean;
    live_analyst: boolean;
    feed_mode: string;
    live_features: string;
    feature_sets: string[];
    refit_every_bars: number;
    default_leverage: number;
    tracker_interval_min: number;
    snapshot_period: string;
    snapshot_interval_min: number;
  };
  model: {
    model_version: string;
    model_kind: string;
    vol_kind: string;
    directions: string[];
    conformal_alphas: number[];
    n_splits: number;
    embargo_frac: number;
    seed: number;
  };
  barrier: {
    tp_mult: number;
    sl_mult: number;
    horizon_bars: number;
    vol_window_bars: number;
    fee_roundtrip_pct: number;
    slippage_pct: number;
    funding_pct_per_8h: number;
  };
  gate: GateThresholds;
  leverage: { default_mmr: number; safety_buffer: number };
  venues: Record<
    string,
    {
      key: string;
      name: string;
      maker_fee: number;
      taker_fee: number;
      slippage_pct: number;
      funding_interval_hours: number;
      max_leverage: number;
      notes: string;
      margin_tiers: {
        notional_cap: number;
        maintenance_margin_rate: number;
        maintenance_amount: number;
      }[];
    }
  >;
  default_venue: string;
  integrations: { telegram_configured: boolean; outliers_configured: boolean };
};

// --------------------------------------------------------------------- //
// Health y WebSocket
// --------------------------------------------------------------------- //

export type FeedStatus = {
  source: string;
  connected: boolean;
  connected_since_ms: number | null;
  last_message_ms: number | null;
  reconnects: number;
  messages: number;
  last_error: string | null;
  subscribed: string[];
  stream_messages: Record<string, number>;
  mute_streams: string[];
};

export type HealthResponse = {
  status: string;
  service: string;
  mode: string;
  watchlist: string[];
  default_timeframe: string;
  signal_threshold: number;
  live_data: boolean;
  feed: FeedStatus | null;
  analyst: AnalystStatus | null;
};

/** Frames que emite `bob/api/ws.py`. El `data` de cada uno está tipado abajo. */
export type WsEvent =
  | "market.tick"
  | "market.candle"
  | "conn.status"
  | "analysis.forecast"
  | "analysis.error";

/**
 * `market.tick` llega de dos productores distintos y por eso casi todo es
 * opcional: el de kline trae `price` y el flujo taker; el de markPrice trae
 * `mark_price` y el funding. Tipar la unión como si fuera un solo objeto
 * completo haría creer que siempre están los dos, y el header mostraría
 * `undefined` como si fuera un precio.
 */
export type MarketTick = {
  symbol: string;
  event_time?: number;
  timeframe?: string;
  open_time?: number;
  price?: number;
  high?: number;
  low?: number;
  volume?: number;
  closed?: boolean;
  taker_buy_qty?: number;
  taker_sell_qty?: number;
  mark_price?: number;
  funding_rate?: number;
  next_funding_time?: number;
};

export type CandleFrame = {
  symbol: string;
  timeframe: string;
  open_time: number;
  close_time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  taker_buy_volume: number;
  n_trades: number;
  closed: true;
};

export type ConnStatusFrame = FeedStatus & { symbols: string[] };

/**
 * `analysis.forecast` es `MarketAnalysis.as_dict(include_features=False)` más
 * tres campos del analista. NO trae `forecast_id`, `horizon_bars` ni el
 * resultado forward: eso lo tiene la DB, y el frame es lo que se sabe al
 * emitir. Se declara campo por campo en vez de derivarlo de `Forecast` para
 * que la diferencia sea visible y no un `Omit` que envejece en silencio.
 */
export type ForecastFrame = {
  symbol: string;
  timeframe: string;
  open_time: number;
  reference_price: number;
  sigma_forecast: number;
  sigma_backward: number;
  sigma_ratio: number;
  barrier_sigma_source: string;
  cones: ConeBand[];
  projections: Record<string, SetupProjection>;
  probabilities: Record<string, number>;
  probability_matches_barriers: boolean;
  probability_calibrated: boolean;
  model_version: string;
  fit_through_ms: number;
  n_train: number;
  feature_set: string;
  bars_since_fit: number;
  cone_coverage: ConeCoverage;
};

export type AnalysisErrorFrame = { symbol: string; detail: string };

// --------------------------------------------------------------------- //
// Riesgo — calculadora de tamaño (/api/risk/sizing)
// --------------------------------------------------------------------- //

export type SizingWarning = {
  code: string;
  level: "danger" | "warn";
  message: string;
};

export type SizingResponse = {
  venue: string;
  venue_name: string;
  direction: "long" | "short";
  capital: number;
  risk_fraction: number;
  risk_budget: number;
  entry: number;
  stop: number;
  leverage: number;
  stop_pct: number;
  cost_pct: number;
  quantity: number;
  notional: number;
  margin: number;
  loss_at_stop: number;
  loss_at_stop_fraction: number;
  cost_usd: number;
  cost_share_of_risk: number;
  required_leverage: number;
  max_safe_leverage: number;
  liquidation_price: number;
  liquidation_pct: number;
  liquidation_to_stop: number;
  target: number | null;
  target_pct: number | null;
  gain_at_target: number | null;
  reward_risk_net: number | null;
  breakeven_win_rate: number | null;
  sigma_horizon: number | null;
  stop_sigmas: number | null;
  p_touch_stop: number | null;
  p_touch_target: number | null;
  regime_elevated: boolean | null;
  losing_streaks: Record<string, number>;
  warnings: SizingWarning[];
};

// --------------------------------------------------------------------- //
// Mapa técnico (/api/analysis/technical)
// --------------------------------------------------------------------- //

export type TechnicalTf = "15m" | "1h" | "4h";

export type LiquidityPool = {
  price: number;
  side: "high" | "low";
  pivot_time: number;
  equal: number;
  distance_pct: number;
};

export type OrderBlockZone = {
  direction: "long" | "short";
  top: number;
  bottom: number;
  candle_time: number;
  created_time: number;
  age_bars: number;
  expires_in_bars: number;
};

export type SRZone = {
  price: number;
  touches: number;
  kind: "support" | "resistance";
  last_time: number;
};

export type SweepMark = {
  time: number;
  direction: "long" | "short";
  level: number;
  extreme: number;
  n_levels: number;
};

export type RuleEvidence = {
  rule: "sweep" | "order_block";
  kind: "confirmatoria" | "exploratoria";
  verdict: string;
  n: number;
  mean_r: number;
  ci: [number, number];
  ci_level: number;
  win_rate: number;
  breakeven_win_rate: number;
  placebo_mean_r: number | null;
  worst_streak: number;
};

export type TechnicalResponse = {
  symbol: string;
  tf: TechnicalTf;
  gaps_15m: number;
  last_time: number;
  last_close: number;
  atr: number;
  atr_pct: number;
  rsi: number;
  emas: Record<string, number>;
  series: {
    time: number[];
    open: number[];
    high: number[];
    low: number[];
    close: number[];
    rsi: (number | null)[];
    ema20: number[];
    ema50: number[];
    ema200: number[];
  };
  pools: LiquidityPool[];
  order_blocks: OrderBlockZone[];
  sr: SRZone[];
  sweeps: SweepMark[];
  ob_touches: { time: number; direction: string; top: number; bottom: number }[];
  evidence: {
    artifact_id: string;
    preregistration: string;
    cost_pct: number;
    r_multiple: number;
    horizon: number;
    rules: RuleEvidence[];
  } | null;
};

// --------------------------------------------------------------------- //
// Lectura del mercado (/api/analysis/narrative)
// --------------------------------------------------------------------- //

export type NarrativeSection = {
  title: string;
  basis: "validado" | "descriptivo" | "sin ventaja medida" | "alcance";
  tone: "ok" | "warn" | "neutral" | "muted";
  text: string;
};

export type NarrativeResponse = {
  symbol: string;
  timeframe: string;
  forecast_open_time: number;
  map_last_time: number;
  age_bars: number;
  is_stale: boolean;
  headline: string;
  sections: NarrativeSection[];
};

/** El filtro leído con stop (PREREGISTRO_FILTRO_STOP.md), desde su artefacto. */
export type StopFilterEvidence = {
  artifact_id: string;
  preregistration: string | null;
  reproduces: boolean;
  c1_pass: boolean;
  stop_pct: number;
  horizon_bars: number;
  hit_rate_ratio: number;
  hit_rate_normal: number;
  hit_rate_elevated: number;
  lift: number;
  lift_ci95: [number, number];
  diff_vs_baseline_ci95: [number, number];
  calibration_gap: Record<string, number>;
  calibration_gap_ci95: Record<string, [number, number]>;
  calculator_needs_correction: string[];
  expectancy_r: Record<string, number>;
};

/** MAREA — monitor de liquidez (fase M2). Monitoreo, sin pronóstico. */
export type LiquidityLabel = "expansivo" | "neutro" | "contractivo";

export type LiquidityIndicator = {
  key: string;
  name: string;
  group: "nucleo" | "contexto" | "chile";
  in_composite: boolean;
  /** +1 subir es expansivo, −1 contractivo, 0 ambiguo (no se clasifica). */
  sign: number;
  kind: "log" | "diff";
  unit: string;
  /** "medido" = vintages de ALFRED; "declarado" = rezago fijado a mano (BCCh). */
  pit: "medido" | "declarado";
  caveat: string;
  obs_date: string | null;
  exact: boolean;
  level: number | null;
  chg_4w: number | null;
  chg_13w: number | null;
  pct: number | null;
  label: LiquidityLabel | null;
  label_13w_ago: LiquidityLabel | null;
  pct_from: string | null;
};

export type CotRow = {
  code: string;
  name: string;
  etf: string | null;
  report_date: string;
  available_date: string;
  net_pct_oi: number | null;
  net_pct_oi_4w: number | null;
  pct_3y: number | null;
};

export type LiquidityHistoryPoint = {
  date: string;
  composite_pct: number | null;
  label: LiquidityLabel | null;
  fed_net: number | null;
  global_cb: number | null;
  components: Record<string, number | null>;
};

export type LiquidityReport = {
  as_of: string;
  computed_at_utc: string;
  age_days: number;
  stale: boolean;
  method: {
    signal: string;
    short_change_weeks: number;
    percentile: string;
    min_history_weeks: number;
    composite: string;
    weights: string;
    forecast: false;
  };
  regime: {
    label: LiquidityLabel | null;
    composite_pct: number | null;
    composite_raw: number | null;
    label_4w_ago: LiquidityLabel | null;
    label_13w_ago: LiquidityLabel | null;
    since: string | null;
    available_from: string | null;
    persistence: {
      weeks: number;
      changes: number;
      median_run_weeks: number | null;
      max_run_weeks: number | null;
    };
  };
  indicators: LiquidityIndicator[];
  cot: CotRow[];
  history: LiquidityHistoryPoint[];
};

/** MAREA — fuerza relativa por clase (`marea.strength`). Descriptivo, no es flujo. */
export type StrengthClass = {
  block: string;
  label: string;
  members: string[];
  rel_window: number | null;
  rel_4s: number | null;
  rel_13s: number | null;
  rel_26s: number | null;
};

export type StrengthEtf = {
  ticker: string;
  block: string;
  name: string;
  caveat: string;
  rel_4s: number | null;
  rel_13s: number | null;
  rel_26s: number | null;
  ret_4s: number | null;
  ret_13s: number | null;
  ret_26s: number | null;
};

export type StrengthPoint = { day: string; median: number | null } & Record<string, number | null | string>;

export type StrengthReport = {
  evidence: string;
  as_of: string;
  age_days: number;
  stale: boolean;
  window_weeks: number;
  window_from: string;
  table_weeks: number[];
  median_window: number | null;
  median_by_weeks: Record<string, number | null>;
  excluded: string[];
  classes: StrengthClass[];
  etfs: StrengthEtf[];
  series: StrengthPoint[];
  method: { measure: string; class: string; reference: string; missing: string };
};

/** Veredicto de la M3, leído del artefacto: lo que va donde iría un pronóstico. */
export type RankingVerdict = {
  artifact: string;
  prereg_commit: string;
  viable: boolean;
  reading: string;
  test_from: string;
  test_to: string;
  n_test_dates: number;
  horizon_weeks: number;
  n_etfs: number;
  ic_mean: number;
  ic_ci95: [number, number];
  baseline: string;
  baseline_ic_mean: number;
  paired_diff: number;
  paired_ci95: [number, number];
  auc: number;
  placebo_ic_mean: number | null;
  liquidity_contribution: { diff_mean: number; ci95: [number, number]; excludes_zero: boolean } | null;
  ic_by_spy_sign: Record<string, { n: number; ic_mean: number }> | null;
};

/** MAREA — frescura de cada fuente (`/api/macro/freshness`). */
export type FreshnessReport = {
  sources: {
    source: string;
    last: string | null;
    age_days: number | null;
    /** Cada fuente tiene su ritmo: el COT semanal tolera más que el funding diario. */
    stale_after_days: number;
    stale: boolean;
  }[];
  max_age_days: number | null;
  stale: string[];
};

/**
 * MAREA — estado OPERATIVO del forward del panel diario (Fase 10-bis).
 * No trae el IC a propósito: la Enmienda 1 prohíbe mirarlo antes del interino.
 */
export type PanelStatus = {
  evidence: string;
  first_decision: string;
  last_decision: string;
  decision_dates: number;
  valid: number;
  backfill: string[];
  never_emitted: string[];
  valid_rate: number;
  cutoff_valid_days: number;
  interim_valid_days: number;
  eta_interim_at_current_rate: string | null;
  eta_at_current_rate: string | null;
  eta_if_every_day_valid: string;
  ic: string;
};

type BlockError = { error: string };

export type PanoramaRecession = {
  evidence: string;
  as_of: string;
  probability: number;
  target: string;
  spread: number;
  history_12m: { month: string; probability: number }[];
  /** Coeficientes del probit, importados de `marea/recession.py`. */
  model: { alpha: number; beta: number };
  peak_since_2022: { month: string; probability: number };
  caveat: string;
  sahm: { as_of: string; value: number } | null;
};

export type PanoramaCycle = {
  evidence: string;
  as_of: string;
  price: number;
  mayer: number;
  risk_pit: number | null;
  risk_full: number;
  zone: string | null;
  pit_vs_full: { correlation: number; mean_abs_diff: number; share_far: number };
  /** Valor en fechas históricas de referencia (techo de 2021), calculado. */
  references: { label: string; day: string; risk_pit: number | null; risk_full: number | null }[];
  history: { day: string; risk_pit: number | null; risk_full: number }[];
};

export type PanoramaBreadth = {
  evidence: string;
  as_of: string;
  above_sma200: number;
  above_sma20w: number;
  n_symbols: number;
  altseason: number | null;
  altseason_label: string;
  references: { label: string; day: string; above_sma200: number }[];
  history: { day: string; above_sma200: number | null; altseason: number | null }[];
};

export type PanoramaFunding = {
  evidence: string;
  as_of: string;
  btc_annual_pct: number;
  eth_annual_pct: number;
  spread_eth_btc_pct: number;
  spread_rank: number | null;
  btc_rank: number | null;
  rank_since: string;
};

export type OptionsSide = {
  captured_at: string;
  pc_oi: number;
  pc_volume: number;
  pc_oi_near: number;
  oi_usd: number;
  snapshots: number;
  first_snapshot: string;
};

export type PanoramaOptions = { evidence: string; btc: OptionsSide; eth: OptionsSide };

export type PanoramaStablecoins = {
  evidence: string;
  as_of: string;
  supply_usd: number;
  change_30d_usd: number | null;
  change_30d_pct: number | null;
  net_mints_7d_usd: number | null;
  ssr: number | null;
  ssr_rank_since_2020: number | null;
  history: { day: string; supply_usd: number }[];
};

/** MAREA — ciclo y cripto de la M5 (`/api/macro/panorama`). Monitoreo. */
export type Panorama = {
  as_of: string;
  recession: PanoramaRecession | BlockError;
  btc_cycle: PanoramaCycle | BlockError;
  breadth: PanoramaBreadth | BlockError;
  funding: PanoramaFunding | BlockError;
  options: PanoramaOptions | BlockError;
  stablecoins: PanoramaStablecoins | BlockError;
};

/** Un bloque del panorama que falló trae `error` en vez de sus datos. */
export function isBlockError(b: object): b is BlockError {
  return "error" in b;
}

/** Laboratorio ▸ Resumen (M6.4): un experimento con su veredicto, leído del artefacto. */
export type LabStatus = "pasa" | "no_pasa" | "control_ok" | "control_falla" | "informativo";

export type LabExperiment = {
  id: string;
  status: LabStatus;
  artifact_id: string;
  date: string | null;
  criterion_doc: string | null;
  criterion_commit: string | null;
  /** Cifras propias de cada experimento; la página las lee por id. */
  figures: Record<string, unknown>;
};

export type LabExperiments = {
  experiments: LabExperiment[];
  counts: Partial<Record<LabStatus, number>>;
};
