"""SQLModel models — persistencia del asistente.

Convención heredada del build anterior: los valores monetarios/precios se
guardan como `str` para no perder precisión Decimal en SQLite. Convertir a
Decimal en los bordes (API / servicios), nunca operar con float sobre precios.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC)


class CandleRecord(SQLModel, table=True):
    """Kline persistida para replay offline del backtest (data/store.py).

    Única por (symbol, timeframe, open_time). open_time/close_time en epoch ms
    UTC, igual que Binance — evita ambigüedades de zona horaria.
    """

    id: int | None = Field(default=None, primary_key=True)
    symbol: str = Field(index=True)
    timeframe: str = Field(index=True)  # "5m" | "15m" | "1h" | ...
    open_time: int = Field(index=True)  # epoch ms UTC
    close_time: int
    open: str
    high: str
    low: str
    close: str
    volume: str
    quote_volume: str = "0"
    taker_buy_volume: str = "0"  # feature de microestructura, viene en la kline
    n_trades: int = 0


class Signal(SQLModel, table=True):
    """Una señal emitida por el asistente (en vivo o en backtest).

    Regla 10 de CLAUDE.md: features_json guarda el feature vector COMPLETO
    con el que se computó la señal — sin eso no hay post-mortem posible.
    """

    id: int | None = Field(default=None, primary_key=True)
    signal_id: str = Field(index=True, unique=True)  # ej: "ETHUSDT-15m-1755600000"
    source: str = "live"  # "live" | "backtest"
    symbol: str = Field(index=True)
    timeframe: str
    direction: str  # "long" | "short"

    # KPI 1 — Seguridad
    probability: str  # P(TP antes que SL), calibrada, "0".."1"
    calibrated: bool = False  # False => se muestra como "experimental"
    regime: str = ""  # régimen Markov/HMM al momento de la señal

    # KPI 2 — Proyección
    entry_price: str
    take_profit: str
    stop_loss: str
    expected_value_pct: str = "0"  # EV neto de fees+funding, sin leverage
    horizon_bars: int = 0  # H del triple-barrier, en barras del timeframe

    # KPI 3 — Duración estimada de régimen (en barras; None si indefinida)
    expected_regime_bars: str | None = None

    features_json: str = "{}"  # feature vector completo
    model_version: str = ""  # para saber qué modelo/calibración la produjo

    emitted_at: datetime = Field(default_factory=_utcnow, index=True)


class PaperTrade(SQLModel, table=True):
    """Outcome simulado de una señal emitida en vivo (paper/tracker.py).

    BOB nunca ejecuta: esto es el seguimiento forward que valida el KPI.
    """

    id: int | None = Field(default=None, primary_key=True)
    signal_id: str = Field(index=True, unique=True)
    status: str = "open"  # "open" | "tp_hit" | "sl_hit" | "expired"
    exit_price: str | None = None
    pnl_pct: str | None = None  # neto de fees estimados, sin leverage
    bars_held: int = 0

    opened_at: datetime = Field(default_factory=_utcnow)
    closed_at: datetime | None = None


class BacktestRun(SQLModel, table=True):
    """Un run de backtest walk-forward con sus métricas agregadas."""

    id: int | None = Field(default=None, primary_key=True)
    run_id: str = Field(index=True, unique=True)
    symbol: str
    timeframe: str
    date_from: str  # ISO date
    date_to: str
    config_json: str = "{}"  # TP/SL/H, umbral, params del modelo

    # Métricas agregadas (metrics.py)
    n_signals: int = 0
    win_rate: str | None = None
    profit_factor: str | None = None
    max_drawdown_pct: str | None = None
    expectancy_pct: str | None = None
    calibration_error_pp: str | None = None  # error medio por bucket, en puntos %
    buckets_json: str = "{}"  # curva de fiabilidad: predicho vs observado por bucket

    status: str = "pending"  # "pending" | "running" | "done" | "failed"
    created_at: datetime = Field(default_factory=_utcnow)
    finished_at: datetime | None = None


class SentimentSnapshot(SQLModel, table=True):
    """Snapshot periódico de sentimiento/contexto (APScheduler, Fase 7)."""

    id: int | None = Field(default=None, primary_key=True)
    source: str = Field(index=True)  # "fear_greed" | "coingecko" | "outliers"
    metric: str = Field(index=True)  # "fear_greed_index" | "btc_dominance" | ...
    value: str
    raw_json: str = "{}"
    captured_at: datetime = Field(default_factory=_utcnow, index=True)
