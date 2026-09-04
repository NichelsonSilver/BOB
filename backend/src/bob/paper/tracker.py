"""Seguimiento forward — ¿el pronóstico se pareció a lo que pasó?

Fase 5 tal como quedó tras la decisión del 2026-08-25. El paper tracking ya no
persigue una señal direccional: persigue **lo que pasó el gate**. Tres
preguntas, ninguna sobre hacia dónde va el precio:

1. ¿La sigma pronosticada se pareció a la volatilidad que efectivamente hubo?
2. ¿El cono conformal cubrió al precio en su nivel nominal?
3. ¿El EV que se proyectó se pareció al retorno neto que el setup habría dado?

BOB nunca ejecuta (regla 1): esto es simulación sobre precios reales
posteriores, no una orden.

Por qué reutiliza las métricas del backtest y no unas propias
--------------------------------------------------------------
`coverage_report` llama a `models/metrics.py` —`regression_metrics`,
`interval_metrics`, `qlike`— exactamente igual que `models/experiment.py`. El
objetivo declarado de la fase es comparar cobertura forward contra la del
backtest, y dos implementaciones de "cobertura" que difieran en un detalle
convierten esa comparación en ruido. Si divergen los números, tiene que ser
porque divergió el mercado.

Las tres convenciones que hacen honesto el número
--------------------------------------------------
* **La entrada se mide al open real de la barra siguiente**, no al precio de
  referencia con el que se dibujaron los niveles. BOB proyecta al cierre de la
  barra i con el close de i; el usuario entra en i+1. Medir contra el close de
  i regalaría el hueco de apertura, que es justo donde se pierde plata.
* **Los niveles son los que BOB mostró**, no unos recalculados con
  información posterior. Por eso viven en `projections_json` y se leen de ahí.
* **El empate intrabarra se resuelve contra el trader**, vía
  `labeling.resolve_setup_path` — la misma función y la misma regla que usó el
  etiquetado del backtest.

Un registro con huecos en su ventana se marca `gap` y **no** entra en la
cobertura. Rellenar la vela faltante para no perder la muestra sería inventar
el dato justo donde el dato no está.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from loguru import logger
from sqlmodel import Session, col, select

from bob.data.store import load_series
from bob.db.models import CandleRecord, ForecastRecord
from bob.db.session import get_session, init_db
from bob.models import metrics as mx
from bob.models.labeling import BarrierConfig, resolve_setup_path
from bob.models.production import OnlineConformalCone
from bob.paper import forward_eval as fe
from bob.utils.console import enable_utf8_stdout

#: Cada cuánto revisa el tracker si hay registros resolubles. Con velas de 15m
#: y H=16, un pronóstico madura en 4 horas: mirar cada 5 minutos es de sobra y
#: cuesta una query indexada.
DEFAULT_INTERVAL_S = 300.0

#: Cómo el loop consigue los conos vivos en cada vuelta. Ver `tracker_loop`.
ConesProvider = Callable[[], "dict[float, OnlineConformalCone] | None"]


@dataclass
class ResolvedForecast:
    """Un pronóstico ya contrastado contra lo que pasó."""

    forecast_id: str
    open_time: int
    sigma_forecast: float
    realized_vol: float
    realized_return: float
    cone_hits: dict[float, bool]
    cone_bands: dict[float, tuple[float, float]]
    outcomes: dict[str, dict[str, Any]]
    resolved_through: int

    @property
    def vol_ratio(self) -> float:
        if self.sigma_forecast <= 0:
            return float("nan")
        return self.realized_vol / self.sigma_forecast


def _dm_dict(dm: "mx.DieboldMariano | None") -> dict[str, Any] | None:
    if dm is None:
        return None
    return {
        "statistic": dm.statistic,
        "p_value": dm.p_value,
        "verdict": dm.verdict(),
    }


@dataclass
class CoverageReport:
    """Lo medido forward, en el mismo formato en que lo dijo el backtest."""

    symbol: str
    timeframe: str
    n_resolved: int
    n_open: int
    n_gap: int
    date_from: str
    date_to: str

    volatility: mx.RegressionMetrics | None = None
    #: Cociente realizada/pronosticada. 1.0 sería perfecto; <1 dice que el
    #: modelo pronosticó más movimiento del que hubo, y por lo tanto que los
    #: TP y SL salieron más anchos de lo necesario.
    vol_ratio_mean: float = float("nan")
    vol_ratio_median: float = float("nan")

    #: Los mismos baselines del gate sobre estas barras. Sin ellos, el R²
    #: forward solo se puede leer contra la media de su propia muestra, y
    #: eso confunde 'muestra corta' con 'modelo degradado'.
    baselines: dict[str, mx.RegressionMetrics] = field(default_factory=dict)
    dm_vs_ewma: mx.DieboldMariano | None = None
    dm_vs_har: mx.DieboldMariano | None = None

    cones: dict[float, mx.IntervalMetrics] = field(default_factory=dict)
    #: IC de la cobertura por bootstrap de bloques: los pronósticos se
    #: solapan y tratarlos como independientes encoge el error estándar.
    cone_ci: dict[float, tuple[float, float]] = field(default_factory=dict)
    #: Observaciones independientes aproximadas — n / horizonte.
    n_blocks: float = float("nan")
    #: Homogeneidad de la muestra. Más de uno invalida la comparación.
    model_versions: list[str] = field(default_factory=list)
    #: Por dirección: EV proyectado vs retorno neto realizado, y mezcla de
    #: resoluciones (tp / sl / vertical).
    setups: dict[str, dict[str, float]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict

        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "n_resolved": self.n_resolved,
            "n_open": self.n_open,
            "n_gap": self.n_gap,
            "date_from": self.date_from,
            "date_to": self.date_to,
            "volatility": asdict(self.volatility) if self.volatility else None,
            "vol_ratio_mean": self.vol_ratio_mean,
            "vol_ratio_median": self.vol_ratio_median,
            "baselines": {k: asdict(v) for k, v in self.baselines.items()},
            "dm_vs_ewma": _dm_dict(self.dm_vs_ewma),
            "dm_vs_har": _dm_dict(self.dm_vs_har),
            "cones": {f"{a:.2f}": asdict(m) for a, m in self.cones.items()},
            "cone_ci": {f"{a:.2f}": list(v) for a, v in self.cone_ci.items()},
            "n_blocks": self.n_blocks,
            "model_versions": self.model_versions,
            "setups": self.setups,
        }


# --------------------------------------------------------------------- #
# Resolución de un registro
# --------------------------------------------------------------------- #


def _horizon_candles(
    session: Session, record: ForecastRecord
) -> list[CandleRecord]:
    """Las velas del horizonte, en orden. Puede devolver menos de las pedidas."""
    stmt = (
        select(CandleRecord)
        .where(
            CandleRecord.symbol == record.symbol,
            CandleRecord.timeframe == record.timeframe,
            CandleRecord.open_time > record.open_time,
        )
        .order_by(col(CandleRecord.open_time))
        .limit(record.horizon_bars)
    )
    return list(session.exec(stmt).all())


def _is_contiguous(record: ForecastRecord, candles: list[CandleRecord]) -> bool:
    """¿Las velas del horizonte son consecutivas desde la barra de decisión?

    Se verifica contra la grilla y no contra "hay N filas": si el feed se cayó
    media hora, la query devuelve H velas igual, pero saltadas. Medir la
    volatilidad de una ventana con agujeros la subestima y la cobertura del
    cono queda inflada — exactamente el tipo de número que este proyecto no
    puede permitirse.
    """
    if len(candles) < record.horizon_bars:
        return False
    step = candles[0].close_time - candles[0].open_time + 1
    expected = record.open_time + step
    for candle in candles:
        if candle.open_time != expected:
            return False
        expected += step
    return True


def resolve_record(
    record: ForecastRecord, candles: list[CandleRecord], barrier: BarrierConfig
) -> ResolvedForecast:
    """Contrasta un pronóstico contra las velas que vinieron después. PURO.

    `candles` deben ser las H barras posteriores, contiguas y en orden.
    """
    entry_ref = float(record.reference_price)
    closes = np.array([entry_ref] + [float(c.close) for c in candles], dtype=np.float64)
    high = np.array([float(c.high) for c in candles], dtype=np.float64)
    low = np.array([float(c.low) for c in candles], dtype=np.float64)

    # Idéntico a `labeling.forward_volatility`: RMS de los retornos log de las
    # H barras posteriores, contando desde el cierre de la barra de decisión.
    r = np.diff(np.log(np.maximum(closes, 1e-12)))
    realized_vol = float(np.sqrt(np.sum(r * r)))
    realized_return = float(np.log(closes[-1] / closes[0]))

    bands: dict[float, tuple[float, float]] = {}
    hits: dict[float, bool] = {}
    for band in json.loads(record.cones_json):
        alpha = float(band["alpha"])
        lo, hi = float(band["ret_lo"]), float(band["ret_hi"])
        bands[alpha] = (lo, hi)
        hits[alpha] = bool(lo <= realized_return <= hi)

    # La entrada real es el open de la barra siguiente, no el precio de
    # referencia con el que se dibujaron los niveles (ver encabezado).
    entry = float(candles[0].open)
    tf_ms = candles[0].close_time - candles[0].open_time + 1

    outcomes: dict[str, dict[str, Any]] = {}
    for direction, proj in json.loads(record.projections_json).items():
        res = resolve_setup_path(
            high,
            low,
            closes[1:],
            entry_price=entry,
            tp_level=float(proj["take_profit"]),
            sl_level=float(proj["stop_loss"]),
            direction=direction,
            config=barrier,
            timeframe_ms=tf_ms,
        )
        outcomes[direction] = {
            "status": res.status,
            "bars_held": res.bars_held,
            "entry_price": entry,
            "exit_price": res.exit_price,
            "gross_return": res.gross_return,
            "net_return": res.net_return,
            "projected_ev_pct": float(proj["net_ev_pct"]),
            "projected_roe_pct": float(proj["roe_pct"]),
            "realized_roe_pct": res.net_return * float(proj["leverage"]),
        }

    return ResolvedForecast(
        forecast_id=record.forecast_id,
        open_time=record.open_time,
        sigma_forecast=record.sigma_forecast,
        realized_vol=realized_vol,
        realized_return=realized_return,
        cone_hits=hits,
        cone_bands=bands,
        outcomes=outcomes,
        resolved_through=candles[-1].open_time,
    )


def resolve_pending(
    symbol: str | None = None,
    timeframe: str | None = None,
    *,
    barrier: BarrierConfig | None = None,
    cones: dict[float, OnlineConformalCone] | None = None,
    session: Session | None = None,
    limit: int = 500,
) -> list[ResolvedForecast]:
    """Resuelve los pronósticos cuyo horizonte ya cerró. Escribe el resultado.

    `cones` es opcional y engancha el ACI del analista en vivo: cada cobertura
    observada mueve el alpha del cono, que es lo que le permite recuperar el
    nivel nominal bajo cambio de régimen. Sin eso el cono en vivo se queda con
    el alpha del ajuste y la garantía deja de adaptarse.
    """
    cfg = barrier or BarrierConfig()
    owns = session is None
    if owns:
        init_db()
        session = get_session()
    assert session is not None

    resolved: list[ResolvedForecast] = []
    try:
        # También se re-examinan los `gap`: un hueco del feed se cierra con
        # `download --repair`, y entonces ese registro vuelve a ser medible.
        # Marcarlo una vez y nunca volver a mirarlo regalaría justo las
        # muestras de alrededor de cada pausa del proceso.
        stmt = select(ForecastRecord).where(
            col(ForecastRecord.status).in_(("open", "gap"))
        )
        if symbol is not None:
            stmt = stmt.where(ForecastRecord.symbol == symbol)
        if timeframe is not None:
            stmt = stmt.where(ForecastRecord.timeframe == timeframe)
        records = list(
            session.exec(stmt.order_by(col(ForecastRecord.open_time)).limit(limit)).all()
        )

        for record in records:
            candles = _horizon_candles(session, record)
            if len(candles) < record.horizon_bars:
                continue  # sigue abierto: todavía no pasó el horizonte
            if not _is_contiguous(record, candles):
                if record.status == "gap":
                    continue  # ya estaba marcado y el hueco sigue abierto
                record.status = "gap"
                record.closed_at = datetime.now(UTC)
                session.add(record)
                logger.warning(
                    "tracker: {} tiene huecos en su horizonte — marcado gap, "
                    "no entra en la cobertura",
                    record.forecast_id,
                )
                continue

            out = resolve_record(record, candles, cfg)
            record.status = "resolved"
            record.realized_vol = out.realized_vol
            record.vol_ratio = out.vol_ratio
            record.realized_return = out.realized_return
            record.cone_hits_json = json.dumps(
                {f"{a:.2f}": v for a, v in out.cone_hits.items()}
            )
            record.outcomes_json = json.dumps(out.outcomes)
            record.resolved_through = out.resolved_through
            record.closed_at = datetime.now(UTC)
            session.add(record)
            resolved.append(out)

            if cones:
                for alpha, cone in cones.items():
                    band = out.cone_bands.get(alpha)
                    if band is not None:
                        cone.observe(out.realized_return, band[0], band[1])

        session.commit()
    finally:
        if owns:
            session.close()

    if resolved:
        logger.info("tracker: {} pronóstico(s) resueltos", len(resolved))
    return resolved


# --------------------------------------------------------------------- #
# Reporte de cobertura forward
# --------------------------------------------------------------------- #


def _baselines_for(
    symbol: str,
    timeframe: str,
    done: list[ForecastRecord],
    horizon: int,
) -> dict[str, np.ndarray] | None:
    """Los baselines del gate sobre las barras resueltas. None si no se puede.

    Necesitan la serie completa de velas —GARCH y HAR se ajustan con el
    pasado anterior a la ventana forward—, así que esto hace I/O y vive
    acá y no en `forward_eval`, que es puro. Un fallo degrada el reporte,
    no lo tumba: sin baselines se sigue imprimiendo todo lo demás.
    """
    try:
        series = load_series(symbol, timeframe)
        close = np.asarray(series.close, dtype=np.float64)
        open_time = np.asarray(series.open_time, dtype=np.int64)
        if close.size < 2:
            return None
        pos = {int(t): i for i, t in enumerate(open_time)}
        if any(int(r.open_time) not in pos for r in done):
            logger.warning(
                "tracker: hay pronósticos sin vela en la serie — se omiten "
                "los baselines para no comparar sobre muestras distintas"
            )
            return None
        idx = np.array([pos[int(r.open_time)] for r in done], dtype=np.int64)
        tf_ms = int(open_time[1] - open_time[0])
        return fe.baseline_predictions(close, idx, horizon, tf_ms)
    except Exception as exc:  # pragma: no cover — degradación deliberada
        logger.warning("tracker: no se pudieron calcular los baselines: {}", exc)
        return None


def coverage_report(
    symbol: str,
    timeframe: str,
    session: Session | None = None,
    *,
    with_baselines: bool = True,
) -> CoverageReport:
    """Agrega lo resuelto hasta ahora, con las métricas del backtest."""
    owns = session is None
    if owns:
        init_db()
        session = get_session()
    assert session is not None

    try:
        stmt = select(ForecastRecord).where(
            ForecastRecord.symbol == symbol, ForecastRecord.timeframe == timeframe
        )
        records = list(session.exec(stmt.order_by(col(ForecastRecord.open_time))).all())
    finally:
        if owns:
            session.close()

    done = [r for r in records if r.status == "resolved"]
    report = CoverageReport(
        symbol=symbol,
        timeframe=timeframe,
        n_resolved=len(done),
        n_open=sum(1 for r in records if r.status == "open"),
        n_gap=sum(1 for r in records if r.status == "gap"),
        date_from=_ms_to_iso(records[0].open_time) if records else "-",
        date_to=_ms_to_iso(records[-1].open_time) if records else "-",
    )
    if not done:
        return report

    report.model_versions = sorted({r.model_version for r in done})
    horizon = int(done[0].horizon_bars)
    report.n_blocks = fe.effective_blocks(len(done), horizon)

    y_true = np.array([r.realized_vol for r in done], dtype=np.float64)
    y_pred = np.array([r.sigma_forecast for r in done], dtype=np.float64)

    base = _baselines_for(symbol, timeframe, done, horizon) if with_baselines else None
    if base is None:
        report.volatility = mx.regression_metrics(y_true, y_pred)
    else:
        report.volatility = mx.regression_metrics(y_true, y_pred, base["ewma"])
        report.baselines = {
            "ewma": mx.regression_metrics(y_true, base["ewma"]),
            "garch": mx.regression_metrics(y_true, base["garch"], base["ewma"]),
            "har": mx.regression_metrics(y_true, base["har"], base["ewma"]),
        }
        loss = mx.squared_error(y_true, y_pred)
        report.dm_vs_ewma = mx.diebold_mariano(
            loss, mx.squared_error(y_true, base["ewma"]), horizon=horizon
        )
        report.dm_vs_har = mx.diebold_mariano(
            loss, mx.squared_error(y_true, base["har"]), horizon=horizon
        )
    ratios = np.array(
        [r.vol_ratio for r in done if r.vol_ratio is not None], dtype=np.float64
    )
    if ratios.size:
        report.vol_ratio_mean = float(np.mean(ratios))
        report.vol_ratio_median = float(np.median(ratios))

    returns = np.array([r.realized_return for r in done], dtype=np.float64)
    by_alpha: dict[float, list[tuple[float, float]]] = {}
    for rec in done:
        for band in json.loads(rec.cones_json):
            by_alpha.setdefault(float(band["alpha"]), []).append(
                (float(band["ret_lo"]), float(band["ret_hi"]))
            )
    for alpha, pairs in sorted(by_alpha.items()):
        if len(pairs) != returns.size:
            continue  # un nivel que no está en todos los registros no se agrega
        lo = np.array([p[0] for p in pairs], dtype=np.float64)
        hi = np.array([p[1] for p in pairs], dtype=np.float64)
        report.cones[alpha] = mx.interval_metrics(returns, lo, hi, 1.0 - alpha)
        hits = (returns >= lo) & (returns <= hi)
        report.cone_ci[alpha] = fe.coverage_interval(hits, horizon)

    per_direction: dict[str, list[dict[str, Any]]] = {}
    for rec in done:
        for direction, out in json.loads(rec.outcomes_json).items():
            per_direction.setdefault(direction, []).append(out)
    for direction, outs in sorted(per_direction.items()):
        realized = np.array([o["net_return"] for o in outs], dtype=np.float64)
        projected = np.array([o["projected_ev_pct"] for o in outs], dtype=np.float64)
        statuses = [o["status"] for o in outs]
        report.setups[direction] = {
            "n": float(len(outs)),
            "projected_ev_pct": float(np.mean(projected)),
            "realized_net_return_pct": float(np.mean(realized)),
            "win_rate": float(np.mean([s == "tp_hit" for s in statuses])),
            "tp": float(np.mean([s == "tp_hit" for s in statuses])),
            "sl": float(np.mean([s == "sl_hit" for s in statuses])),
            "vertical": float(np.mean([s == "expired" for s in statuses])),
            "mean_bars_held": float(np.mean([o["bars_held"] for o in outs])),
        }
    return report


def replay_cone_state(
    symbol: str,
    timeframe: str,
    cones: dict[float, OnlineConformalCone],
    session: Session | None = None,
) -> int:
    """Reconstruye el estado del ACI desde los registros ya resueltos.

    `OnlineConformalCone.alpha_t` vive en memoria, así que un reinicio lo
    devolvería al alpha nominal y el cono perdería toda la adaptación que
    había acumulado — invisible, porque el intervalo se sigue dibujando igual
    de lindo. Como la DB ya guarda el cono emitido y el retorno realizado de
    cada registro, el estado no hace falta persistirlo aparte: se **deriva**
    reproduciendo las observaciones en orden.

    Eso hace que reiniciar el proceso (o pausar el equipo) sea gratis para el
    cono. Devuelve cuántas observaciones se reprodujeron.
    """
    owns = session is None
    if owns:
        init_db()
        session = get_session()
    assert session is not None
    try:
        stmt = (
            select(ForecastRecord)
            .where(
                ForecastRecord.symbol == symbol,
                ForecastRecord.timeframe == timeframe,
                ForecastRecord.status == "resolved",
            )
            .order_by(col(ForecastRecord.open_time))
        )
        records = list(session.exec(stmt).all())
    finally:
        if owns:
            session.close()

    n = 0
    for record in records:
        if record.realized_return is None:
            continue
        for band in json.loads(record.cones_json):
            cone = cones.get(float(band["alpha"]))
            if cone is not None:
                cone.observe(
                    record.realized_return, float(band["ret_lo"]), float(band["ret_hi"])
                )
                n += 1
    if n:
        logger.info(
            "tracker: estado del ACI reconstruido con {} observación(es) de {} registro(s)",
            n,
            len(records),
        )
    return n


def render_coverage(report: CoverageReport) -> str:
    """Reporte legible. La cobertura empírica se muestra siempre con su n."""
    lines: list[str] = [
        "=" * 72,
        f"PAPER TRACKING — {report.symbol} {report.timeframe}",
        f"{report.date_from} .. {report.date_to}",
        "=" * 72,
        f"pronósticos: {report.n_resolved} resueltos, {report.n_open} abiertos, "
        f"{report.n_gap} descartados por huecos",
    ]
    if report.model_versions:
        marca = " ⚠ MUESTRA MIXTA" if len(report.model_versions) > 1 else ""
        lines.append(
            f"model_version: {', '.join(report.model_versions)}{marca}"
        )
    lines.append("")
    if report.n_resolved == 0:
        lines.append("Todavía no hay nada resuelto: el horizonte no cerró.")
        return "\n".join(lines)

    lines += ["VOLATILIDAD PRONOSTICADA vs REALIZADA", "-" * 72]
    if report.volatility is not None:
        lines.append(f"  {report.volatility.summary()}")
    lines.append(
        f"  razón realizada/pronosticada: media {report.vol_ratio_mean:.3f}  "
        f"mediana {report.vol_ratio_median:.3f}"
    )
    lines.append(
        "  (>1 = hubo más movimiento del pronosticado, o sea TP y SL quedaron"
        " cortos)"
    )
    if report.baselines:
        lines += ["", "  vs los mismos baselines del gate:"]
        lines.append(
            f"    {'':<8}{'RMSE':>10}{'R2':>9}{'R2_vs_ewma':>12}{'QLIKE':>10}"
        )
        rows: list[tuple[str, mx.RegressionMetrics | None]] = [
            ("modelo", report.volatility)
        ]
        rows += list(report.baselines.items())
        for name, reg in rows:
            if reg is None:
                continue
            lines.append(
                f"    {name:<8}{reg.rmse:>10.5f}{reg.r2:>+9.3f}"
                f"{reg.r2_vs_baseline:>+12.3f}{reg.qlike:>10.4f}"
            )
        for label, dm in (("EWMA", report.dm_vs_ewma), ("HAR", report.dm_vs_har)):
            if dm is not None:
                lines.append(
                    f"    Diebold-Mariano vs {label}: {dm.statistic:+.3f}  "
                    f"p={dm.p_value:.4f}  {dm.verdict()}"
                )
        lines.append(
            "    R2 se mide contra la media de ESTA muestra y encoge cuando la"
        )
        lines.append(
            "    ventana es corta; R2_vs_ewma es el número comparable al gate."
        )

    lines += ["", "CONO CONFORMAL — ¿contuvo al precio?", "-" * 72]
    for alpha, m in report.cones.items():
        lines.append(f"  alpha={alpha:.2f}  {m.summary()}")
        ci = report.cone_ci.get(alpha)
        if ci is not None and np.isfinite(ci[0]):
            nominal = 1.0 - alpha
            dentro = ci[0] <= nominal <= ci[1]
            lines.append(
                f"            IC95% por bloques [{ci[0]:.1%}, {ci[1]:.1%}] — el "
                f"nominal cae {'DENTRO' if dentro else 'FUERA'}"
            )
    lines.append(
        f"  n={report.n_resolved} pronósticos solapados = ~{report.n_blocks:.0f} "
        "observaciones independientes: el IC se calcula sobre eso, no sobre n."
    )
    lines.append(
        "  comparar contra la cobertura del walk-forward: si divergen mucho,"
    )
    lines.append("  es bandera de sobreajuste, no de mala suerte.")

    if report.setups:
        lines += ["", "SETUPS PROYECTADOS — EV vs realizado", "-" * 72]
        for direction, s in report.setups.items():
            lines.append(
                f"  {direction:5s} n={int(s['n']):4d}  "
                f"EV proyectado {s['projected_ev_pct']:+.4%}  "
                f"realizado {s['realized_net_return_pct']:+.4%}"
            )
            lines.append(
                f"         tp {s['tp']:.1%} / sl {s['sl']:.1%} / vertical "
                f"{s['vertical']:.1%}   barras medias {s['mean_bars_held']:.1f}"
            )
        lines.append("")
        lines.append(
            "  El EV proyectado se calcula con la probabilidad del KPI 1, que NO"
        )
        lines.append(
            "  pasó el gate (regla 2). Se muestra para poder medirlo, no para"
        )
        lines.append("  operar contra él.")
    return "\n".join(lines)


def write_artifact(
    report: CoverageReport, text: str, directory: str = "artifacts"
) -> list[Path]:
    """Congela el reporte forward en disco, en el mismo formato que el gate.

    `.txt` legible y `.json` con las cifras crudas. Existe por la misma
    razón que los artefactos de la Fase 4: el veredicto de una fase tiene
    que salir de un archivo versionado y no de un resumen escrito a mano,
    porque un resumen no se puede volver a verificar. El nombre lleva la
    fecha del último pronóstico y el n, que es lo que identifica al corte.
    """
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    stem = (
        f"forward-{report.symbol}-{report.timeframe}-n{report.n_resolved}-{stamp}"
    )
    txt = out / f"{stem}.txt"
    js = out / f"{stem}.json"
    txt.write_text(text + "\n", encoding="utf-8")
    js.write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return [txt, js]


def _ms_to_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M")


# --------------------------------------------------------------------- #
# Loop y CLI
# --------------------------------------------------------------------- #


async def tracker_loop(
    symbol: str,
    timeframe: str,
    interval_s: float = DEFAULT_INTERVAL_S,
    *,
    stop: asyncio.Event | None = None,
    cones_provider: ConesProvider | None = None,
) -> None:
    """Resuelve pronósticos periódicamente mientras el backend esté arriba.

    Los conos llegan por **proveedor** y no como dict fijo a propósito: el
    analista reemplaza el bundle entero en cada reajuste, así que una
    referencia capturada al arrancar terminaría realimentando conos que ya no
    emiten nada, y el ACI del cono vivo se quedaría congelado sin que nadie
    lo note.
    """
    stop = stop or asyncio.Event()
    while not stop.is_set():
        try:
            cones = cones_provider() if cones_provider is not None else None
            await asyncio.to_thread(
                resolve_pending, symbol, timeframe, cones=cones
            )
        except Exception as exc:  # noqa: BLE001 — el tracker no tumba el backend
            logger.exception("tracker: fallo al resolver pendientes: {}", exc)
        with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval_s)


def main(argv: list[str] | None = None) -> int:
    enable_utf8_stdout()
    parser = argparse.ArgumentParser(
        description="Resuelve pronósticos maduros y reporta la cobertura forward."
    )
    parser.add_argument("--symbol", default="ETHUSDT")
    parser.add_argument("--timeframe", default="15m")
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="No resuelve nada: solo imprime lo que ya está medido.",
    )
    parser.add_argument(
        "--no-baselines",
        action="store_true",
        help="Omite EWMA/GARCH/HAR (no carga la serie completa de velas).",
    )
    parser.add_argument(
        "--artifact",
        nargs="?",
        const="artifacts",
        default=None,
        help=(
            "Congela el reporte en un .txt + .json versionados, como los del "
            "gate. Sin esto el veredicto de la fase sería un resumen a mano."
        ),
    )
    args = parser.parse_args(argv)

    symbol = args.symbol.upper()
    if not args.report_only:
        resolve_pending(symbol, args.timeframe)
    report = coverage_report(
        symbol, args.timeframe, with_baselines=not args.no_baselines
    )
    text = render_coverage(report)
    print(text)
    if args.artifact is not None:
        for path in write_artifact(report, text, args.artifact):
            print(f"  → {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
