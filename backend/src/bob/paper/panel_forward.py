"""Validación forward del panel transversal — la capa de I/O de la Fase 10-bis.

    # una vez por día: baja klines, predice la última fecha cerrada, persiste
    uv run python -m bob.paper.panel_forward --emit

    # resuelve lo que ya cumplió su horizonte y reporta
    uv run python -m bob.paper.panel_forward --report

Criterio pre-registrado en `docs/PREREGISTRO_PANEL_FORWARD.md` (commit
`99532e8`): corte primario a **180 días**, métrica confirmatoria el **IC de
rango medio** con IC95% por bloques de fecha. El spread neto se reporta y
**no** puede aprobar ni reprobar — no tiene potencia en este plazo (§2.2).

**Por qué esto no es un backtest disfrazado.** El panel usa solo klines, que
Binance sirve para siempre, así que cualquier fecha se puede reconstruir
después. Lo único que separa esta corrida de un backtest es *cuándo* se
computó cada pronóstico, y por eso el registro guarda `emitted_at` y el
reporte lo verifica fila por fila en vez de confiar en que el comando corrió
a tiempo.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from loguru import logger
from sqlmodel import Session, col, select

from bob.backtest.panel_runner import build_panel
from bob.data.panel import PanelData
from bob.db.models import PanelForecastRecord
from bob.db.session import get_session, init_db
from bob.models.panel_experiment import PanelConfig, _spearman
from bob.models.panel_production import PanelBundle, fit_panel_bundle, predict_date
from bob.paper.forward_eval import coverage_interval
from bob.signals.cross_section import PanelFeatures
from bob.signals.panel_features import assemble_panel_features
from bob.signals.universe import UniverseRule, eligibility_mask
from bob.utils.console import enable_utf8_stdout

_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent.parent
ARTIFACTS_DIR = _BACKEND_DIR / "artifacts"

DAY_MS = 86_400_000

#: Corte primario del pre-registro, en días con pronóstico válido.
PRIMARY_CUTOFF_DAYS = 180

#: Gracia para que un pronóstico cuente como forward, medida desde el cierre
#: de su barra de decisión.
#:
#: **Esto endurece el pre-registro, y la corrección se hizo antes de emitir
#: un solo dato.** §5 decía "antes del cierre de la barra `t+1`", o sea 24 h.
#: Está mal: la entrada es al **open** de `t+1`, así que computar 20 h después
#: significa conocer ya el 80% del primer día del horizonte — lookahead
#: parcial sobre uno de los cinco días que se miden. Con 2 h la exposición
#: queda por debajo del 2% del horizonte y sigue siendo operable con una
#: corrida diaria programada.
#:
#: `emitted_at` se guarda exacto, así que el umbral es recalculable: quien
#: quiera leer el resultado con la regla original de 24 h puede hacerlo sin
#: volver a correr nada.
FORWARD_GRACE_MS = 2 * 3_600_000


def _utc(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def _fmt(ms: int) -> str:
    return _utc(ms).strftime("%Y-%m-%d")


def forecast_id(open_time: int, symbol: str) -> str:
    return f"PANEL-{open_time}-{symbol}"


def is_backfill(open_time: int, emitted_at: datetime) -> bool:
    """¿Se computó tarde como para no ser evidencia forward?

    La barra de decisión `t` cierra en `open_time + 1 día`; desde ahí corre la
    gracia. Antes de ese cierre el pronóstico es **imposible** (falta el dato),
    así que solo el borde superior discrimina.
    """
    deadline = open_time + DAY_MS + FORWARD_GRACE_MS
    return int(emitted_at.timestamp() * 1000) > deadline


# --------------------------------------------------------------------------- #
# Emisión
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class EmitReport:
    open_time: int
    written: int
    section_width: int
    backfill: bool
    model_version: str
    n_train: int


def emit_date(
    panel: PanelData,
    bundle: PanelBundle,
    eligible: np.ndarray,
    features: PanelFeatures,
    date_index: int,
    *,
    session: Session | None = None,
    now_ms: int | None = None,
) -> EmitReport:
    """Predice una fecha y persiste una fila por símbolo elegible."""
    owns = session is None
    if owns:
        init_db()
        session = get_session()
    assert session is not None

    pred = predict_date(bundle, features, panel.close, eligible, date_index)
    emitted = _utc(now_ms) if now_ms is not None else datetime.now(UTC)
    backfill = is_backfill(pred.open_time, emitted)
    cfg = bundle.config

    try:
        existing = set(
            session.exec(
                select(PanelForecastRecord.forecast_id).where(
                    PanelForecastRecord.open_time == pred.open_time
                )
            )
        )
        written = 0
        for k, symbol in enumerate(pred.symbols):
            fid = forecast_id(pred.open_time, symbol)
            if fid in existing:
                continue  # idempotente: recorrer la fecha no duplica ni infla
            row_idx = panel.index_of(symbol)
            vector = {
                name: float(features.X[row_idx, date_index, f])
                for f, name in enumerate(features.names)
            }
            session.add(
                PanelForecastRecord(
                    forecast_id=fid,
                    symbol=symbol,
                    timeframe=panel.timeframe,
                    open_time=pred.open_time,
                    probability=float(pred.probability[k]),
                    cross_section_rank=float(pred.rank[k]),
                    section_width=pred.width,
                    reference_close=str(pred.reference_close[k]),
                    features_json=json.dumps(vector),
                    model_version=bundle.model_version,
                    fit_through_ms=bundle.fit_through_ms,
                    n_train=bundle.n_train,
                    horizon_bars=cfg.horizon,
                    emitted_at=emitted,
                    is_backfill=backfill,
                )
            )
            written += 1
        session.commit()
    finally:
        if owns:
            session.close()

    return EmitReport(
        open_time=pred.open_time,
        written=written,
        section_width=pred.width,
        backfill=backfill,
        model_version=bundle.model_version,
        n_train=bundle.n_train,
    )


# --------------------------------------------------------------------------- #
# Resolución
# --------------------------------------------------------------------------- #


def resolve_mature(
    panel: PanelData,
    horizon: int,
    *,
    session: Session | None = None,
) -> dict[str, int]:
    """Cierra los pronósticos cuyo horizonte ya pasó.

    El retorno **relativo** se calcula contra la mediana de los símbolos que
    resolvieron **esa misma fecha**, no contra una mediana global: es la
    definición del target de la Fase 10 y la única contra la que el IC
    significa lo que dice.
    """
    owns = session is None
    if owns:
        init_db()
        session = get_session()
    assert session is not None

    counts = {"resolved": 0, "gap": 0, "delisted": 0, "open": 0}
    idx_of_date = {int(t): j for j, t in enumerate(panel.open_time)}

    try:
        pending = list(
            session.exec(
                select(PanelForecastRecord).where(PanelForecastRecord.status == "open")
            )
        )
        by_date: dict[int, list[PanelForecastRecord]] = {}
        for row in pending:
            by_date.setdefault(row.open_time, []).append(row)

        for open_time, rows in sorted(by_date.items()):
            j = idx_of_date.get(open_time)
            if j is None or j + horizon >= panel.n_dates:
                counts["open"] += len(rows)
                continue

            resolved: list[tuple[PanelForecastRecord, float, float, float, int, str]] = []
            for row in rows:
                try:
                    i = panel.index_of(row.symbol)
                except ValueError:  # pragma: no cover — símbolo fuera del panel
                    counts["gap"] += 1
                    row.status = "gap"
                    row.closed_at = datetime.now(UTC)
                    continue
                entry = panel.open[i, j + 1]
                window = panel.close[i, j + 1 : j + horizon + 1]
                seen = np.isfinite(window)
                if not np.isfinite(entry) or not seen.any():
                    row.status = "gap"
                    row.closed_at = datetime.now(UTC)
                    counts["gap"] += 1
                    continue
                last = int(np.flatnonzero(seen)[-1])
                exit_px = float(window[last])
                status = "delisted" if last < horizon - 1 else "resolved"
                ret = float(np.log(exit_px / float(entry)))
                resolved.append((row, float(entry), exit_px, ret, j + 1 + last, status))

            if not resolved:
                continue
            median = float(np.median([r[3] for r in resolved]))
            for row, entry, exit_px, ret, through, status in resolved:
                row.entry_price = str(entry)
                row.exit_price = str(exit_px)
                row.realized_return = ret
                row.relative_return = ret - median
                row.resolved_through = int(panel.open_time[through])
                row.status = status
                row.closed_at = datetime.now(UTC)
                counts[status] += 1

        session.commit()
    finally:
        if owns:
            session.close()
    return counts


# --------------------------------------------------------------------------- #
# Reporte
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ForwardReport:
    n_dates: int
    n_rows: int
    date_from: int
    date_to: int
    ic_per_date: np.ndarray
    spread_per_date: np.ndarray
    ic_mean: float
    ic_ci: tuple[float, float]
    spread_mean: float
    spread_ci: tuple[float, float]
    backfill_dates: int
    gap_rows: int
    delisted_rows: int
    model_versions: list[str]
    horizon: int
    pair_cost: float

    @property
    def confirmed(self) -> bool:
        """El criterio pre-registrado: IC positivo con IC95% que excluye 0."""
        return bool(self.ic_mean > 0.0 and self.ic_ci[0] > 0.0)

    @property
    def reached_cutoff(self) -> bool:
        return self.n_dates >= PRIMARY_CUTOFF_DAYS


def build_report(
    *,
    quantile: float = 0.10,
    pair_cost: float = 0.0028,
    horizon: int = 5,
    n_boot: int = 20_000,
    seed: int = 0,
    include_backfill: bool = False,
    session: Session | None = None,
) -> ForwardReport:
    """Calcula el IC y el spread por fecha sobre los pronósticos resueltos."""
    owns = session is None
    if owns:
        init_db()
        session = get_session()
    assert session is not None

    try:
        stmt = select(PanelForecastRecord).where(
            col(PanelForecastRecord.status).in_(["resolved", "delisted"])
        )
        rows = list(session.exec(stmt))
        gap_rows = len(
            list(
                session.exec(
                    select(PanelForecastRecord.id).where(
                        PanelForecastRecord.status == "gap"
                    )
                )
            )
        )
    finally:
        if owns:
            session.close()

    by_date: dict[int, list[PanelForecastRecord]] = {}
    backfill_dates: set[int] = set()
    versions: set[str] = set()
    delisted = 0
    for row in rows:
        versions.add(row.model_version)
        if row.status == "delisted":
            delisted += 1
        if row.is_backfill:
            backfill_dates.add(row.open_time)
            if not include_backfill:
                continue
        by_date.setdefault(row.open_time, []).append(row)

    dates, ics, spreads, n_rows = [], [], [], 0
    for open_time in sorted(by_date):
        group = by_date[open_time]
        if len(group) < 3:
            continue
        score = np.array([r.probability for r in group], dtype=np.float64)
        actual = np.array([r.relative_return or 0.0 for r in group], dtype=np.float64)
        dates.append(open_time)
        n_rows += len(group)
        ics.append(_spearman(score, actual))
        n_side = max(1, int(round(score.size * quantile)))
        order = np.argsort(score, kind="stable")
        spreads.append(
            float(actual[order[-n_side:]].mean() - actual[order[:n_side]].mean())
        )

    ic_arr = np.asarray(ics, dtype=np.float64)
    sp_arr = np.asarray(spreads, dtype=np.float64) - pair_cost
    ic_fin = ic_arr[np.isfinite(ic_arr)]
    sp_fin = sp_arr[np.isfinite(sp_arr)]

    return ForwardReport(
        n_dates=len(dates),
        n_rows=n_rows,
        date_from=min(dates) if dates else 0,
        date_to=max(dates) if dates else 0,
        ic_per_date=ic_arr,
        spread_per_date=sp_arr,
        ic_mean=float(ic_fin.mean()) if ic_fin.size else float("nan"),
        ic_ci=coverage_interval(ic_fin, horizon, n_boot=n_boot, seed=seed)
        if ic_fin.size
        else (float("nan"), float("nan")),
        spread_mean=float(sp_fin.mean()) if sp_fin.size else float("nan"),
        spread_ci=coverage_interval(sp_fin, horizon, n_boot=n_boot, seed=seed)
        if sp_fin.size
        else (float("nan"), float("nan")),
        backfill_dates=len(backfill_dates),
        gap_rows=gap_rows,
        delisted_rows=delisted,
        model_versions=sorted(versions),
        horizon=horizon,
        pair_cost=pair_cost,
    )


#: Referencia offline de la Fase 10, para contrastar sin copiar cifras a mano.
OFFLINE_ARTIFACT = "panel-1d-H5-xs-20260922204554.json"


def _offline_reference() -> dict[str, float]:
    path = ARTIFACTS_DIR / OFFLINE_ARTIFACT
    if not path.exists():  # pragma: no cover — artefacto versionado
        return {}
    d = json.loads(path.read_text(encoding="utf-8"))
    return {"ic": float(d["model_mean_ic"]), "net": float(d["model_spread_net"])}


def render_report(rep: ForwardReport) -> str:
    out: list[str] = []
    w = out.append
    ref = _offline_reference()

    w("=" * 78)
    w("VALIDACIÓN FORWARD DEL PANEL TRANSVERSAL")
    w("=" * 78)
    w("pre-registro : docs/PREREGISTRO_PANEL_FORWARD.md (commit 99532e8)")
    w(f"corte primario: {PRIMARY_CUTOFF_DAYS} días con pronóstico válido")
    w("")
    if rep.n_dates == 0:
        w("todavía no hay ninguna fecha resuelta.")
        return "\n".join(out)

    w(f"fechas resueltas : {rep.n_dates}  ({_fmt(rep.date_from)} .. {_fmt(rep.date_to)})")
    w(f"filas            : {rep.n_rows:,}")
    w(f"horizonte        : {rep.horizon} días")
    w(f"model_version    : {', '.join(rep.model_versions)}")
    if len(rep.model_versions) > 1:
        w("  ⚠ MUESTRA MIXTA: conviven dos versiones del modelo en la misma muestra")
    w("")
    w("-" * 78)
    w("CRITERIO CONFIRMATORIO — IC de rango (el único con potencia)")
    w("-" * 78)
    lo, hi = rep.ic_ci
    w(f"  IC forward       : {rep.ic_mean:+.5f}  IC95% por bloques [{lo:+.5f}, {hi:+.5f}]")
    if "ic" in ref:
        dentro = lo <= ref["ic"] <= hi
        estado = "DENTRO" if dentro else "FUERA"
        w(f"  IC offline       : {ref['ic']:+.5f}  -> {estado} del intervalo")
        if not dentro:
            w("     el efecto se degradó de forma medible, aunque el criterio pase")
    w(f"  excluye 0        : {lo > 0.0}")
    w("")
    w("-" * 78)
    w("SIN POTENCIA — se reporta, NO decide (§2.2 del pre-registro)")
    w("-" * 78)
    slo, shi = rep.spread_ci
    w(
        f"  spread neto      : {rep.spread_mean * 100:+.4f}%  "
        f"IC95% [{slo * 100:+.4f}%, {shi * 100:+.4f}%]"
    )
    if "net" in ref:
        w(f"  offline          : {ref['net'] * 100:+.4f}%")
    w("  Confirmar el spread pediría ~302 días a efecto completo y ~1.208 si")
    w("  cae a la mitad. Leer este número como veredicto sería leer ruido.")
    w("")
    w("-" * 78)
    w("HIGIENE DE LA MUESTRA")
    w("-" * 78)
    w(f"  fechas marcadas backfill (fuera del IC) : {rep.backfill_dates}")
    w(f"  filas sin resolver por hueco            : {rep.gap_rows:,}")
    w(f"  filas resueltas al último precio (deslistado): {rep.delisted_rows:,}")
    w("")
    w("=" * 78)
    if not rep.reached_cutoff:
        falta = PRIMARY_CUTOFF_DAYS - rep.n_dates
        w(f"LECTURA DESCRIPTIVA — faltan {falta} fechas para el corte primario.")
        w("Sin veredicto: el pre-registro declara las miradas intermedias")
        w("descriptivas, y parar cuando el número gusta es el sesgo que evita.")
    else:
        w(f"VEREDICTO: {'CONFIRMADO' if rep.confirmed else 'NO CONFIRMADO'} forward")
    w("=" * 78)
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def write_artifact(rep: ForwardReport, run_id: str) -> tuple[Path, Path]:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    txt = ARTIFACTS_DIR / f"{run_id}.txt"
    js = ARTIFACTS_DIR / f"{run_id}.json"
    txt.write_text(render_report(rep), encoding="utf-8")
    js.write_text(
        json.dumps(
            {
                "n_dates": rep.n_dates,
                "n_rows": rep.n_rows,
                "date_from": rep.date_from,
                "date_to": rep.date_to,
                "horizon": rep.horizon,
                "pair_cost": rep.pair_cost,
                "ic_mean": rep.ic_mean,
                "ic_ci": list(rep.ic_ci),
                "spread_mean": rep.spread_mean,
                "spread_ci": list(rep.spread_ci),
                "backfill_dates": rep.backfill_dates,
                "gap_rows": rep.gap_rows,
                "delisted_rows": rep.delisted_rows,
                "model_versions": rep.model_versions,
                "confirmed": rep.confirmed,
                "reached_cutoff": rep.reached_cutoff,
                "cutoff_days": PRIMARY_CUTOFF_DAYS,
                "per_date": {
                    "ic": [float(x) for x in rep.ic_per_date],
                    "spread_net": [float(x) for x in rep.spread_per_date],
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return txt, js


def _prepare(
    until: str | None, min_volume: float
) -> tuple[PanelData, np.ndarray, PanelFeatures]:
    """Panel + máscara de elegibilidad + cubo de features, una sola vez."""
    panel = build_panel("1d", min_months=6, until=until)
    rule = UniverseRule(min_dollar_volume=min_volume)
    eligible = eligibility_mask(panel.close, panel.quote_volume, rule)
    features = assemble_panel_features(panel, eligible)
    return panel, eligible, features


def main() -> None:
    enable_utf8_stdout()
    defaults = PanelConfig()
    p = argparse.ArgumentParser(description="Validación forward del panel diario")
    p.add_argument("--emit", action="store_true", help="predice la última fecha cerrada")
    p.add_argument("--report", action="store_true", help="resuelve lo maduro y reporta")
    p.add_argument("--artifact", action="store_true", help="congela el reporte en artefacto")
    p.add_argument("--until", default=None, help="última fecha (YYYY-MM-DD) — reproducibilidad")
    p.add_argument("--min-volume", type=float, default=UniverseRule().min_dollar_volume)
    p.add_argument("--quantile", type=float, default=defaults.portfolio_quantile)
    p.add_argument("--horizon", type=int, default=defaults.horizon)
    p.add_argument(
        "--include-backfill",
        action="store_true",
        help="mete en el IC los pronósticos emitidos tarde (NO es la muestra confirmatoria)",
    )
    args = p.parse_args()

    if not (args.emit or args.report):
        p.print_help()
        return

    t0 = time.perf_counter()
    panel, eligible, features = _prepare(args.until, args.min_volume)
    logger.info(
        "panel: {} símbolos x {} fechas, última {}",
        panel.n_symbols,
        panel.n_dates,
        _fmt(int(panel.open_time[-1])),
    )

    cfg = PanelConfig(horizon=args.horizon, portfolio_quantile=args.quantile)

    if args.emit:
        bundle = fit_panel_bundle(features, panel.open, panel.close, eligible, cfg)
        logger.info(
            "bundle ajustado: {} filas, etiquetas hasta {}",
            bundle.n_train,
            _fmt(bundle.fit_through_ms),
        )
        emitted = emit_date(panel, bundle, eligible, features, panel.n_dates - 1)
        marca = (
            "  ⚠ BACKFILL (fuera de la muestra confirmatoria)"
            if emitted.backfill
            else ""
        )
        print(
            f"{_fmt(emitted.open_time)}: {emitted.written} filas nuevas, "
            f"sección de {emitted.section_width} símbolos{marca}"
        )

    if args.report:
        counts = resolve_mature(panel, cfg.horizon)
        print(f"resolución: {counts}")
        rep = build_report(
            quantile=cfg.portfolio_quantile,
            pair_cost=cfg.pair_cost,
            horizon=cfg.horizon,
            include_backfill=args.include_backfill,
        )
        print()
        print(render_report(rep))
        if args.artifact:
            run_id = (
                f"panelfwd-n{rep.n_dates}-"
                f"{datetime.now(tz=UTC).strftime('%Y%m%d%H%M%S')}"
            )
            txt, js = write_artifact(rep, run_id)
            print(f"\nartefactos: {txt}\n            {js}")

    print(f"runtime: {time.perf_counter() - t0:.0f}s")


if __name__ == "__main__":
    main()
