"""Simulación y medición de las reglas técnicas pre-registradas. PURO.

Las dos reglas (`docs/PREREGISTRO_TECNICO.md`):

- **Barrido de liquidez**: la vela perfora un pivote no tomado y cierra de
  vuelta adentro → se opera la reversión al `open` de la barra siguiente, con
  el stop detrás de la mecha del barrido.
- **Order block**: primer retorno del precio a la última vela contraria antes
  de un desplazamiento que rompió estructura → orden límite en el borde de la
  zona, stop detrás del otro borde.

Las dos con objetivo a `R` veces el riesgo y salida por tiempo a `H` barras.

**La métrica es el retorno neto en R**, o sea en unidades del riesgo
asumido. Es la que vive un operador que arriesga un porcentaje fijo de su
cuenta por trade — que es como hay que operar 500 USD —: un +0,10 R de
expectancy a 1% de riesgo es +0,10% de la cuenta por trade. Los costos
entran en cada trade antes de dividir, no se descuentan al final.

**Convenciones conservadoras, todas en la misma dirección.** Si una vela
toca stop y objetivo, cuenta el stop (no se sabe cuál fue primero). En la
vela en que se llena una límite solo se mira el stop. Un gap a través del
stop sale al `open`, peor que el stop. Un gap a través del objetivo NO paga
el gap. Cada una sesga contra la regla; si la regla pasa igual, pasa de
verdad.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

import bob.signals.numeric as nm
from bob.models.labeling import BarrierConfig
from bob.models.riskfilter import block_index_samples
from bob.signals.technical import LONG, SHORT, OrderBlockTouch, SweepEvent

_DEFAULT_BARRIERS = BarrierConfig()
#: Costo round-trip del gate: fees taker ida y vuelta + slippage. El mismo
#: número que usan todas las mediciones del proyecto.
DEFAULT_COST_PCT = _DEFAULT_BARRIERS.cost_pct

SWEEP = "sweep"
ORDER_BLOCK = "order_block"


@dataclass(frozen=True)
class RuleConfig:
    """Cómo se opera una señal. Defaults = configuración primaria (§3)."""

    r_multiple: float = 2.0
    horizon: int = 48
    cost_pct: float = DEFAULT_COST_PCT
    funding_pct_per_bar: float = 0.0
    stop_buffer_atr: float = 0.1
    #: Un stop más cerca que esto no es operable: el costo se comería más de
    #: la mitad del riesgo. Es parte de la regla, fijada de antemano.
    min_risk_pct: float = 2.0 * DEFAULT_COST_PCT
    #: Filtros exploratorios (§6). None = sin filtro.
    trend_ema: int | None = None
    rsi_gate: float | None = None
    min_levels: int = 1


@dataclass(frozen=True)
class Trade:
    signal_idx: int
    entry_idx: int
    direction: str
    entry: float
    stop: float
    target: float
    exit_idx: int
    exit_price: float
    exit_kind: str  # "target", "stop" o "time"
    risk_pct: float
    risk_atr: float
    net_pct: float

    @property
    def net_r(self) -> float:
        return self.net_pct / self.risk_pct


def simulate(
    direction: str,
    start: int,
    entry: float,
    stop: float,
    target: float,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    horizon: int,
    *,
    first_bar_stop_only: bool,
) -> tuple[int, float, str] | None:
    """Recorre `start .. start+horizon-1`. None si el horizonte no terminó."""
    n = close.size
    end = start + horizon
    if end > n:
        return None
    is_long = direction == LONG
    for t in range(start, end):
        if is_long:
            hit_stop = low[t] <= stop
            hit_target = high[t] >= target
        else:
            hit_stop = high[t] >= stop
            hit_target = low[t] <= target
        if hit_stop:
            gapped = (open_[t] < stop) if is_long else (open_[t] > stop)
            return t, float(open_[t]) if (gapped and t > start) else stop, "stop"
        if hit_target and not (first_bar_stop_only and t == start):
            return t, target, "target"
    return end - 1, float(close[end - 1]), "time"


def _passes_filters(
    direction: str,
    i: int,
    cfg: RuleConfig,
    close: np.ndarray,
    ema: np.ndarray | None,
    rsi: np.ndarray | None,
) -> bool:
    if ema is not None:
        above = close[i] > ema[i]
        if (direction == LONG) != above:
            return False
    if rsi is not None and cfg.rsi_gate is not None:
        r = rsi[i]
        if not np.isfinite(r):
            return False
        if direction == LONG and r > cfg.rsi_gate:
            return False
        if direction == SHORT and r < 100.0 - cfg.rsi_gate:
            return False
    return True


def _make_trade(
    signal_idx: int,
    start: int,
    direction: str,
    entry: float,
    stop: float,
    atr: float,
    cfg: RuleConfig,
    o: np.ndarray,
    h: np.ndarray,
    lo: np.ndarray,
    c: np.ndarray,
    *,
    first_bar_stop_only: bool,
) -> Trade | None:
    sign = 1.0 if direction == LONG else -1.0
    risk = sign * (entry - stop)
    if not (risk > 0 and np.isfinite(risk)):
        return None
    risk_pct = risk / entry
    if risk_pct < cfg.min_risk_pct:
        return None
    target = entry + sign * cfg.r_multiple * risk
    res = simulate(
        direction,
        start,
        entry,
        stop,
        target,
        o,
        h,
        lo,
        c,
        cfg.horizon,
        first_bar_stop_only=first_bar_stop_only,
    )
    if res is None:
        return None
    exit_idx, exit_price, kind = res
    bars = exit_idx - start + 1
    gross = sign * (exit_price - entry) / entry
    net = gross - cfg.cost_pct - cfg.funding_pct_per_bar * bars
    return Trade(
        signal_idx=signal_idx,
        entry_idx=start,
        direction=direction,
        entry=entry,
        stop=stop,
        target=target,
        exit_idx=exit_idx,
        exit_price=exit_price,
        exit_kind=kind,
        risk_pct=risk_pct,
        risk_atr=risk / atr if atr > 0 else float("nan"),
        net_pct=net,
    )


def _indicators(cfg: RuleConfig, close: np.ndarray) -> tuple[np.ndarray | None, np.ndarray | None]:
    ema = nm.ewma(close, cfg.trend_ema) if cfg.trend_ema else None
    rsi = nm.rsi(close, 14) if cfg.rsi_gate is not None else None
    return ema, rsi


def sweep_trades(
    sweeps: list[SweepEvent],
    o: np.ndarray,
    h: np.ndarray,
    lo: np.ndarray,
    c: np.ndarray,
    cfg: RuleConfig,
) -> list[Trade]:
    """Reversión tras barrido: entrada al open de la barra siguiente."""
    ema, rsi = _indicators(cfg, c)
    trades: list[Trade] = []
    for ev in sweeps:
        start = ev.idx + 1
        if start >= c.size or ev.n_levels < cfg.min_levels:
            continue
        if not _passes_filters(ev.direction, ev.idx, cfg, c, ema, rsi):
            continue
        buf = cfg.stop_buffer_atr * ev.atr
        stop = ev.extreme - buf if ev.direction == LONG else ev.extreme + buf
        t = _make_trade(
            ev.idx,
            start,
            ev.direction,
            float(o[start]),
            stop,
            ev.atr,
            cfg,
            o,
            h,
            lo,
            c,
            first_bar_stop_only=False,
        )
        if t is not None:
            trades.append(t)
    return trades


def order_block_trades(
    touches: list[OrderBlockTouch],
    o: np.ndarray,
    h: np.ndarray,
    lo: np.ndarray,
    c: np.ndarray,
    cfg: RuleConfig,
) -> list[Trade]:
    """Límite en el borde cercano de la zona; se llena en la barra del toque.

    El filtro de tendencia/RSI se evalúa con el cierre de la barra ANTERIOR
    al toque: la límite está puesta antes de que la barra del toque exista.
    """
    ema, rsi = _indicators(cfg, c)
    trades: list[Trade] = []
    for tc in touches:
        t0 = tc.idx
        if t0 < 1 or not _passes_filters(tc.direction, t0 - 1, cfg, c, ema, rsi):
            continue
        buf = cfg.stop_buffer_atr * tc.atr
        if tc.direction == LONG:
            entry = min(float(o[t0]), tc.top)
            stop = tc.bottom - buf
        else:
            entry = max(float(o[t0]), tc.bottom)
            stop = tc.top + buf
        t = _make_trade(
            t0, t0, tc.direction, entry, stop, tc.atr, cfg, o, h, lo, c, first_bar_stop_only=True
        )
        if t is not None:
            trades.append(t)
    return trades


# --------------------------------------------------------------------------- #
# Placebo: misma geometría, momento al azar
# --------------------------------------------------------------------------- #
def placebo_trades(
    trades: list[Trade],
    o: np.ndarray,
    h: np.ndarray,
    lo: np.ndarray,
    c: np.ndarray,
    atr: np.ndarray,
    cfg: RuleConfig,
    seed: int = 0,
) -> list[Trade]:
    """Cada trade se re-sortea en una barra al azar con su dirección y su
    riesgo en ATR. Rompe el vínculo con la estructura y deja todo lo demás.

    Es el control de plomería: si la regla "gana" igual con el momento al
    azar, lo que gana es el simulador, no la regla. Lo esperable por álgebra
    es ≈ −costo/riesgo.
    """
    rng = np.random.default_rng(seed)
    n = c.size
    out: list[Trade] = []
    hi_idx = n - cfg.horizon - 1
    if hi_idx <= 1:
        return out
    for tr in trades:
        i = int(rng.integers(1, hi_idx))
        a = atr[i]
        if not (np.isfinite(a) and a > 0 and np.isfinite(tr.risk_atr)):
            continue
        entry = float(o[i + 1])
        sign = 1.0 if tr.direction == LONG else -1.0
        stop = entry - sign * tr.risk_atr * a
        t = _make_trade(
            i, i + 1, tr.direction, entry, stop, a, cfg, o, h, lo, c, first_bar_stop_only=False
        )
        if t is not None:
            out.append(t)
    return out


# --------------------------------------------------------------------------- #
# Medición
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class TradeStats:
    n: int
    mean_r: float
    ci_low: float
    ci_high: float
    level: float
    win_rate: float
    breakeven_win_rate: float  # sin costos: 1 / (1 + R)
    profit_factor: float
    mean_net_pct: float
    median_risk_pct: float
    max_drawdown_r: float
    worst_streak: int
    exit_mix: dict[str, float]
    n_long: int
    n_short: int

    @property
    def passes(self) -> bool:
        return bool(np.isfinite(self.ci_low) and self.ci_low > 0)


def expectancy_interval(
    trades: list[Trade],
    n_bars: int,
    block: int,
    *,
    level: float,
    n_boot: int = 5_000,
    seed: int = 0,
    chunk: int = 200,
) -> tuple[float, float]:
    """IC de la expectancy en R por bootstrap de bloques móviles sobre BARRAS.

    Los trades se solapan —uno dura hasta `H` barras y la regla puede volver
    a disparar mientras tanto—, así que remuestrear trades sueltos los trata
    como independientes y encoge el intervalo. Se proyecta cada trade sobre
    su barra de señal y se remuestrean bloques de `H` barras: la misma
    convención que el filtro de régimen y el cono.
    """
    if not trades or n_bars == 0:
        return (float("nan"), float("nan"))
    sum_r = np.zeros(n_bars)
    cnt = np.zeros(n_bars)
    for tr in trades:
        sum_r[tr.signal_idx] += tr.net_r
        cnt[tr.signal_idx] += 1.0
    draws: list[np.ndarray] = []
    for idx in block_index_samples(n_bars, block, n_boot, seed, chunk):
        s = sum_r[idx].sum(axis=1)
        k = cnt[idx].sum(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            draws.append(np.where(k > 0, s / np.maximum(k, 1.0), np.nan))
    d = np.concatenate(draws)
    d = d[np.isfinite(d)]
    if d.size == 0:
        return (float("nan"), float("nan"))
    tail = (1.0 - level) / 2.0 * 100.0
    lo, hi = np.percentile(d, [tail, 100.0 - tail])
    return (float(lo), float(hi))


def trade_stats(
    trades: list[Trade],
    n_bars: int,
    cfg: RuleConfig,
    *,
    level: float,
    n_boot: int = 5_000,
    seed: int = 0,
) -> TradeStats:
    trades = sorted(trades, key=lambda t: t.signal_idx)
    r = np.array([t.net_r for t in trades], dtype=np.float64)
    n = int(r.size)
    if n == 0:
        nan = float("nan")
        return TradeStats(
            0, nan, nan, nan, level, nan, 1 / (1 + cfg.r_multiple), nan, nan, nan, nan, 0, {}, 0, 0
        )
    lo, hi = expectancy_interval(trades, n_bars, cfg.horizon, level=level, n_boot=n_boot, seed=seed)
    pos = r[r > 0].sum()
    neg = -r[r < 0].sum()
    equity = np.cumsum(r)
    dd = float((np.maximum.accumulate(np.concatenate([[0.0], equity]))[1:] - equity).max())
    streak = worst = 0
    for x in r:
        streak = streak + 1 if x < 0 else 0
        worst = max(worst, streak)
    kinds = [t.exit_kind for t in trades]
    return TradeStats(
        n=n,
        mean_r=float(r.mean()),
        ci_low=lo,
        ci_high=hi,
        level=level,
        win_rate=float((r > 0).mean()),
        breakeven_win_rate=1.0 / (1.0 + cfg.r_multiple),
        profit_factor=float(pos / neg) if neg > 0 else float("inf"),
        mean_net_pct=float(np.mean([t.net_pct for t in trades])),
        median_risk_pct=float(np.median([t.risk_pct for t in trades])),
        max_drawdown_r=dd,
        worst_streak=int(worst),
        exit_mix={k: kinds.count(k) / n for k in ("target", "stop", "time")},
        n_long=sum(t.direction == LONG for t in trades),
        n_short=sum(t.direction == SHORT for t in trades),
    )
