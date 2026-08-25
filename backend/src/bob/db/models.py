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


class DerivativeSnapshot(SQLModel, table=True):
    """Punto de open interest y posicionamiento. Único por (symbol, period, timestamp).

    Dos fuentes escriben acá y el upsert las reconcilia:

    - `data/vision.py` — el archivo diario `futures/um/daily/metrics/` de
      data.binance.vision, con historia desde 2021-12-01. Es la fuente del
      histórico para entrenar.
    - `data/snapshots.py` — los endpoints `/futures/data/*`, que solo
      conservan ~30 días pero llegan en minutos. Es la fuente del tramo
      caliente, porque el archivo aparece con ~1 día de retraso.

    La ventana de 30 días es del **endpoint**, no del dato: ver
    docs/DATA_SOURCES.md.
    """

    id: int | None = Field(default=None, primary_key=True)
    symbol: str = Field(index=True)
    period: str = Field(index=True)  # "5m" | "15m" | "1h" | ...
    timestamp: int = Field(index=True)  # epoch ms UTC, el que reporta Binance

    open_interest: str | None = None  # contratos, moneda base
    open_interest_value: str | None = None  # notional en USDT
    long_short_ratio: str | None = None  # cuentas globales long/short
    long_account_pct: str | None = None
    short_account_pct: str | None = None
    taker_buy_sell_ratio: str | None = None  # volumen taker comprador/vendedor

    # Solo los trae el archivo `metrics/`; los snapshots REST los dejan en None.
    # "account" cuenta cabezas (una ballena pesa igual que un minorista),
    # "position" pesa notional — divergen justo cuando el posicionamiento se
    # concentra, que es la señal interesante.
    top_trader_account_ratio: str | None = None
    top_trader_position_ratio: str | None = None

    #: Funding cobrado cada 8h. Vive en su propia grilla (period="funding"),
    #: no en la de 5m: /fapi/v1/fundingRate SÍ tiene historia completa desde
    #: 2021 — el límite de 30 días es solo de /futures/data/*.
    funding_rate: str | None = None

    captured_at: datetime = Field(default_factory=_utcnow)


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


class BookDepthBar(SQLModel, table=True):
    """Profundidad del libro agregada a la grilla del timeframe (data/vision.py).

    El archivo `futures/um/daily/bookDepth/` trae un snapshot cada ~30s con la
    profundidad **acumulada** hasta ±0,2/1/2/3/4/5% del mid (12 filas por
    snapshot, verificado). Persistir eso crudo son ~2 GB por año y por símbolo,
    así que se promedia dentro de cada barra al ingerir y se guardan las sumas
    por lado — dimensionales, en USDT.

    La conversión a features adimensionales (imbalance, pendiente del libro)
    vive en `signals/microstructure.py`: la regla 3 de CLAUDE.md se sostiene
    guardando magnitudes crudas acá y ratios allá.

    El **esquema del archivo cambió**: el nivel ±0,2% existe solo desde
    ~2026-01-15. Las columnas correspondientes son nullables por eso.

    Único por (symbol, timeframe, open_time).
    """

    id: int | None = Field(default=None, primary_key=True)
    symbol: str = Field(index=True)
    timeframe: str = Field(index=True)
    open_time: int = Field(index=True)  # epoch ms UTC, alineado con CandleRecord

    # Notional acumulado hasta cada distancia del mid, promediado en la barra.
    # Tres niveles y no los doce del archivo: el near-touch (0,2%) es el que
    # mueve el precio en minutos, 1% es la liquidez que absorbe un impulso, y
    # 5% es el muro de fondo. Los intermedios (2/3/4%) son casi colineales.
    bid_notional_1pct: str = "0"
    ask_notional_1pct: str = "0"
    bid_notional_5pct: str = "0"
    ask_notional_5pct: str = "0"

    #: El near-touch (±0,2%) es NULL en el archivo anterior a ~2026-01-15, que
    #: es cuando Binance lo agregó. NULL y no "0": un libro sin dato y un libro
    #: vacío cerca del mid son cosas opuestas.
    bid_notional_02pct: str | None = None
    ask_notional_02pct: str | None = None

    #: Cuántos snapshots del archivo cayeron dentro de la barra. Con pocos, el
    #: promedio es ruido: el modelo debe poder descartar la barra.
    n_snapshots: int = 0
    #: Cuántos de ellos traían el near-touch. 0 = época sin ese nivel.
    n_snapshots_near: int = 0
