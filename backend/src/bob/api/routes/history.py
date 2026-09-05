"""History page — los pronósticos emitidos y cómo les fue de verdad.

Es la página que sostiene la regla 2: al lado de cualquier número que BOB
muestre tiene que estar su desempeño medido. Acá viven las dos mitades de eso
— la tabla de lo emitido y el reporte de cobertura agregado, que sale de
`paper/tracker.coverage_report`, o sea de **las mismas funciones de métrica
que usó el gate**. Traducir entre backtest y forward era la forma más fácil de
comparar dos cosas distintas creyendo que se comparaba una.

Ninguna ruta de acá resuelve pronósticos. Resolver escribe en la muestra que
se está midiendo, y una muestra que avanza cuando alguien abre el navegador no
es una muestra: el tracker resuelve en su loop, cada 5 minutos, y esto lee.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from fastapi import APIRouter, Query, Response
from sqlmodel import Session, col, func, select

from bob.api.routes._common import (
    SymbolQuery,
    TimeframeQuery,
    TTLCache,
    in_thread,
    resolve,
    with_session,
)
from bob.db.models import ForecastRecord
from bob.paper.tracker import CoverageReport, coverage_report

router = APIRouter(tags=["history"])

#: El reporte con baselines ajusta GARCH y HAR sobre las 69k velas para poder
#: comparar el forward contra los mismos números del gate. Son segundos de CPU
#: que el analista también quiere. Dos minutos de cache: la muestra crece una
#: fila cada 15 minutos, así que nada de lo que se ve puede quedar viejo.
_COVERAGE_TTL_S = 120.0
_coverage_cache = TTLCache(_COVERAGE_TTL_S)


def _row(record: ForecastRecord) -> dict[str, Any]:
    """Fila compacta: sin el vector de 81 features, que la tabla no muestra."""
    return {
        "forecast_id": record.forecast_id,
        "open_time": record.open_time,
        "emitted_at": record.emitted_at.isoformat(),
        "reference_price": float(record.reference_price),
        "sigma_forecast": record.sigma_forecast,
        "sigma_backward": record.sigma_backward,
        "horizon_bars": record.horizon_bars,
        "status": record.status,
        "realized_vol": record.realized_vol,
        "vol_ratio": record.vol_ratio,
        "realized_return": record.realized_return,
        "cone_hits": json.loads(record.cone_hits_json),
        "model_version": record.model_version,
        "resolved_through": record.resolved_through,
    }


def _query(
    session: Session, symbol: str, timeframe: str, status: str | None
) -> Any:
    stmt = select(ForecastRecord).where(
        ForecastRecord.symbol == symbol, ForecastRecord.timeframe == timeframe
    )
    if status:
        stmt = stmt.where(ForecastRecord.status == status)
    return stmt


def _counts(session: Session, symbol: str, timeframe: str) -> dict[str, int]:
    """Cuántos hay por estado. Se cuenta en SQL y no trayendo las filas: con
    la corrida apuntando a ~1.150 resueltos, contar en Python es traer la
    muestra entera para descartarla."""
    stmt = (
        select(ForecastRecord.status, func.count())
        .where(
            ForecastRecord.symbol == symbol, ForecastRecord.timeframe == timeframe
        )
        .group_by(col(ForecastRecord.status))
    )
    return {str(status): int(n) for status, n in session.exec(stmt).all()}


@router.get("/history/forecasts")
async def forecasts(
    symbol: str | None = SymbolQuery(),
    timeframe: str | None = TimeframeQuery(),
    limit: int = Query(100, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None, description='"open" | "resolved" | "gap"'),
) -> dict[str, Any]:
    """Página de pronósticos, del más reciente al más viejo."""
    sym, tf = resolve(symbol, timeframe)

    def read(session: Session) -> tuple[list[ForecastRecord], dict[str, int]]:
        stmt = (
            _query(session, sym, tf, status)
            .order_by(col(ForecastRecord.open_time).desc())
            .offset(offset)
            .limit(limit)
        )
        return list(session.exec(stmt).all()), _counts(session, sym, tf)

    rows, counts = await in_thread(lambda: with_session(read))
    return {
        "symbol": sym,
        "timeframe": tf,
        "counts": counts,
        "total": sum(counts.values()),
        "limit": limit,
        "offset": offset,
        "forecasts": [_row(r) for r in rows],
    }


@router.get("/history/coverage")
async def coverage(
    symbol: str | None = SymbolQuery(),
    timeframe: str | None = TimeframeQuery(),
    baselines: bool = Query(True, description="incluir EWMA/GARCH/HAR y Diebold-Mariano"),
) -> dict[str, Any]:
    """Lo medido forward, en el formato exacto en que lo dijo el backtest.

    `baselines=false` devuelve rápido y **sin** el único término que hace
    legible el R²: sobre una ventana corta el R² contra la media de la propia
    muestra cae por aritmética aunque el modelo esté idéntico —fue el error de
    lectura del primer reporte de n=288—. El frontend lo pide en True; el flag
    existe para un chequeo rápido, no para publicar el número solo.
    """
    sym, tf = resolve(symbol, timeframe)

    def produce() -> CoverageReport:
        return coverage_report(sym, tf, with_baselines=baselines)

    report, age_s = await _coverage_cache.get((sym, tf, baselines), produce)
    data = report.to_dict()
    data["cache_age_s"] = round(age_s, 1)
    data["with_baselines"] = baselines
    # Más de un model_version en la muestra la invalida: el reporte de texto lo
    # marca con "⚠ MUESTRA MIXTA" y la API tiene que decir lo mismo, no dejar
    # que el frontend lo deduzca (o lo olvide).
    data["mixed_sample"] = len(report.model_versions) > 1
    return data


@router.get("/history/forecasts.csv")
async def forecasts_csv(
    symbol: str | None = SymbolQuery(),
    timeframe: str | None = TimeframeQuery(),
    status: str | None = Query(None),
) -> Response:
    """Export completo a CSV — sin paginar, para analizar afuera."""
    sym, tf = resolve(symbol, timeframe)

    def read(session: Session) -> list[ForecastRecord]:
        stmt = _query(session, sym, tf, status).order_by(col(ForecastRecord.open_time))
        return list(session.exec(stmt).all())

    rows = await in_thread(lambda: with_session(read))

    # Los niveles del cono se descubren de los datos y no se hardcodean: son
    # `conformal_alphas` de la config y cambiarlos allá no debe romper acá.
    alphas: list[str] = []
    for record in rows:
        for alpha in json.loads(record.cone_hits_json):
            if alpha not in alphas:
                alphas.append(alpha)
    alphas.sort()

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        [
            "forecast_id",
            "open_time",
            "emitted_at",
            "reference_price",
            "sigma_forecast",
            "sigma_backward",
            "horizon_bars",
            "status",
            "realized_vol",
            "vol_ratio",
            "realized_return",
            *[f"cone_hit_{a}" for a in alphas],
            "model_version",
        ]
    )
    for record in rows:
        hits = json.loads(record.cone_hits_json)
        writer.writerow(
            [
                record.forecast_id,
                record.open_time,
                record.emitted_at.isoformat(),
                record.reference_price,
                record.sigma_forecast,
                record.sigma_backward,
                record.horizon_bars,
                record.status,
                record.realized_vol if record.realized_vol is not None else "",
                record.vol_ratio if record.vol_ratio is not None else "",
                record.realized_return if record.realized_return is not None else "",
                *[hits.get(a, "") for a in alphas],
                record.model_version,
            ]
        )

    return Response(
        content=buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="forecasts-{sym}-{tf}.csv"'
        },
    )
