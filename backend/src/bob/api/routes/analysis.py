"""Lo que BOB tiene para decir de la última barra — Signal y Analysis pages.

Fuente de verdad: `ForecastRecord`, no el objeto en memoria del analista. Los
dos dicen lo mismo cuando el backend está sano, pero solo la DB sigue estando
después de un reinicio, y solo la DB responde en un proceso que corre con el
analista apagado. El estado del analista se **agrega** al lado, sin reemplazar
al registro: sirve para decir si lo que se muestra es de ahora o de antes.

Por qué el slider de leverage recalcula en el servidor
-------------------------------------------------------
La tentación es mandar sigma y precio al navegador y dibujar la liquidación en
TypeScript. Sería un segundo lugar donde vive la fórmula de liquidación —con
sus tiers de MMR y su buffer— y el día que uno de los dos cambie, el número
que el usuario mira antes de entrar apalancado sale de la copia vieja. Es
barato recalcular: `project_setup` es puro y son microsegundos.
"""

from __future__ import annotations

import json
from typing import Any, Literal

import numpy as np
from fastapi import APIRouter, HTTPException, Query, Request

from bob.api.routes._common import (
    SymbolQuery,
    TimeframeQuery,
    bars_since,
    in_thread,
    latest_forecast,
    resolve,
    with_session,
)
from bob.config import settings
from bob.data.binance_rest import INTERVAL_MS
from bob.db.models import ForecastRecord
from bob.models.experiment import FEATURE_SETS, ExperimentConfig, feature_family_index
from bob.models.projection import LeverageProfile, project_setup

router = APIRouter(tags=["analysis"])

#: Cuántas barras de atraso toleramos antes de marcar el pronóstico como viejo.
#: Dos: una es la barra en curso —el pronóstico de la anterior es lo normal— y
#: la segunda da margen al reajuste, que corre en background sin bloquear.
STALE_AFTER_BARS = 2


def _analyst_of(request: Request) -> Any | None:
    return getattr(request.app.state, "analyst", None)


def _live_config(request: Request) -> tuple[ExperimentConfig, str]:
    """La config con la que se está emitiendo, y de dónde salió.

    Con el analista arriba es la suya, literal. Sin analista se reconstruye de
    los settings — y se dice que fue reconstruida, porque un backend offline
    puede estar mirando registros que emitió otra configuración.
    """
    analyst = _analyst_of(request)
    if analyst is not None:
        cfg: ExperimentConfig = analyst.config
        return cfg, "analyst"
    feature_set = settings.bob_live_features
    flags = FEATURE_SETS.get(feature_set, FEATURE_SETS["price+deriv"])
    return (
        ExperimentConfig(
            use_derivatives=flags[0], use_book=flags[1], use_book_near=flags[2]
        ),
        "settings",
    )


def _record_dict(record: ForecastRecord, timeframe: str) -> dict[str, Any]:
    age = bars_since(record.open_time, timeframe)
    return {
        "forecast_id": record.forecast_id,
        "symbol": record.symbol,
        "timeframe": record.timeframe,
        "open_time": record.open_time,
        "emitted_at": record.emitted_at.isoformat(),
        "reference_price": float(record.reference_price),
        "sigma_forecast": record.sigma_forecast,
        "sigma_backward": record.sigma_backward,
        "sigma_ratio": (
            record.sigma_forecast / record.sigma_backward
            if record.sigma_backward > 0
            else None
        ),
        "barrier_sigma_source": record.barrier_sigma_source,
        "horizon_bars": record.horizon_bars,
        "cones": json.loads(record.cones_json),
        "projections": json.loads(record.projections_json),
        "probabilities": json.loads(record.probabilities_json),
        # Filtro de régimen (Fase 5-bis). Sale del registro y no se recalcula:
        # el umbral dependía de la referencia del bundle vigente al emitir, y
        # recomputarlo hoy contaría una decisión que nunca se tomó. Es None en
        # los pronósticos anteriores a la Fase 5-bis, y el frontend lo dice.
        "regime": json.loads(record.regime_json) if record.regime_json else None,
        # Regla 2, literal: el gate rechazó la discriminación del KPI 1, así
        # que este flag viaja en False y el frontend lo dibuja en gris. No es
        # un default perezoso — es el veredicto.
        "probability_calibrated": record.probability_calibrated,
        "model_version": record.model_version,
        "fit_through_ms": record.fit_through_ms,
        "n_train": record.n_train,
        "status": record.status,
        "realized_vol": record.realized_vol,
        "vol_ratio": record.vol_ratio,
        "realized_return": record.realized_return,
        "cone_hits": json.loads(record.cone_hits_json),
        # Frescura: sin esto el dashboard pinta un pronóstico de anteayer con
        # la misma cara que el de recién. Los dos bugs de la Fase 5 se veían
        # exactamente así.
        "age_bars": age,
        "is_stale": age > STALE_AFTER_BARS,
    }


@router.get("/analysis/latest")
async def latest(
    request: Request,
    symbol: str | None = SymbolQuery(),
    timeframe: str | None = TimeframeQuery(),
) -> dict[str, Any]:
    """Último pronóstico emitido + estado del analista que lo produce."""
    sym, tf = resolve(symbol, timeframe)
    record = await in_thread(lambda: with_session(lambda s: latest_forecast(s, sym, tf)))

    analyst = _analyst_of(request)
    return {
        "symbol": sym,
        "timeframe": tf,
        "forecast": _record_dict(record, tf) if record is not None else None,
        "analyst": analyst.status() if analyst is not None else None,
        "live_analyst_enabled": settings.bob_live_analyst and settings.bob_live_data,
    }


@router.get("/analysis/projection")
async def projection(
    request: Request,
    symbol: str | None = SymbolQuery(),
    timeframe: str | None = TimeframeQuery(),
    leverage: float = Query(1.0, gt=0.0, le=125.0),
    lang: Literal["es", "en"] = Query("es"),
) -> dict[str, Any]:
    """Reproyecta el último pronóstico con el leverage que pide el slider.

    Solo cambian las cantidades que dependen del leverage —margen, ROE, precio
    de liquidación, distancia en sigmas—. TP, SL, EV y la probabilidad son las
    mismas: el leverage no mueve el mercado, mueve lo que te pasa a ti.
    """
    sym, tf = resolve(symbol, timeframe)
    record = await in_thread(lambda: with_session(lambda s: latest_forecast(s, sym, tf)))
    if record is None:
        raise HTTPException(status_code=404, detail=f"sin pronósticos de {sym} {tf}")

    cfg, cfg_source = _live_config(request)
    if cfg.barrier.horizon_bars != record.horizon_bars:
        # El setup registrado no es el que produciría esta config. Reproyectar
        # daría números coherentes entre sí y ajenos al pronóstico que se está
        # mostrando, que es peor que no reproyectar.
        raise HTTPException(
            status_code=409,
            detail=(
                f"el horizonte del registro ({record.horizon_bars}) no coincide con "
                f"el de la config vigente ({cfg.barrier.horizon_bars}): el pronóstico "
                "es de otra configuración y reproyectarlo mezclaría dos setups"
            ),
        )

    sigma = (
        record.sigma_forecast
        if record.barrier_sigma_source == "forecast"
        else record.sigma_backward
    )
    if not np.isfinite(sigma) or sigma <= 0:
        raise HTTPException(status_code=409, detail=f"sigma no utilizable: {sigma}")

    probabilities: dict[str, float] = json.loads(record.probabilities_json)
    profile = LeverageProfile(leverage=leverage)
    tf_ms = INTERVAL_MS[tf]
    entry = float(record.reference_price)

    out: dict[str, Any] = {}
    for direction, prob in probabilities.items():
        out[direction] = project_setup(
            entry_price=entry,
            sigma_horizon=sigma,
            probability=float(np.clip(prob, 0.0, 1.0)),
            config=cfg.barrier,
            timeframe_ms=tf_ms,
            direction=direction,
            profile=profile,
            lang=lang,
        ).as_dict()

    return {
        "symbol": sym,
        "timeframe": tf,
        "open_time": record.open_time,
        "reference_price": entry,
        "leverage": leverage,
        "sigma_horizon": sigma,
        "barrier_sigma_source": record.barrier_sigma_source,
        # La proyección se recalcula, la probabilidad NO se recalibra: sigue
        # siendo la que registró el analista, y sigue sin pasar el gate.
        "probability_calibrated": record.probability_calibrated,
        "config_source": cfg_source,
        "projections": out,
    }


@router.get("/analysis/features")
async def features(
    request: Request,
    symbol: str | None = SymbolQuery(),
    timeframe: str | None = TimeframeQuery(),
) -> dict[str, Any]:
    """El vector de features del último pronóstico, agrupado por familia.

    Es el desglose de la Analysis page: qué está leyendo el modelo ahora mismo,
    familia por familia. Los valores son los que guardó la regla 10, sin
    reprocesar: si el desglose se recomputara acá podría no coincidir con el
    que produjo el pronóstico mostrado.
    """
    sym, tf = resolve(symbol, timeframe)
    record = await in_thread(lambda: with_session(lambda s: latest_forecast(s, sym, tf)))
    if record is None:
        raise HTTPException(status_code=404, detail=f"sin pronósticos de {sym} {tf}")

    values: dict[str, float] = json.loads(record.features_json)
    analyst = _analyst_of(request)
    feature_set = (
        analyst.feature_set if analyst is not None else settings.bob_live_features
    )
    index = await in_thread(lambda: feature_family_index(tf, feature_set))

    grouped: dict[str, list[dict[str, Any]]] = {}
    for name, value in values.items():
        family = index.get(name, "sin-familia")
        finite = isinstance(value, (int, float)) and bool(np.isfinite(value))
        grouped.setdefault(family, []).append(
            {"name": name, "value": float(value) if finite else None}
        )

    return {
        "symbol": sym,
        "timeframe": tf,
        "open_time": record.open_time,
        "feature_set": feature_set,
        "n_features": len(values),
        # Cuántas columnas llegaron sin dato. Un salto acá es la firma de una
        # familia que dejó de publicarse, que es lo que deja mudo al analista.
        "n_missing": sum(
            1 for items in grouped.values() for it in items if it["value"] is None
        ),
        "families": grouped,
        "age_bars": bars_since(record.open_time, tf),
    }
