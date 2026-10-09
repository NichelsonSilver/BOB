"""Mapa técnico — página Mapa.

Zonas de liquidez, order blocks, soportes y resistencias, barridos recientes,
medias y RSI sobre las últimas velas, calculados con el **mismo** detector que
midió el backtest pre-registrado (`signals/technical.py`).

Cada respuesta trae al lado la evidencia de ese backtest para el timeframe
pedido, leída del artefacto y no escrita en el TSX: las dos reglas no pasaron
(`docs/PREREGISTRO_TECNICO.md`), y el §7 del pre-registro dice que el mapa se
dibuja igual pero con su expectancy medida al lado, no con flechas de entrada.

El 1h y el 4h se arman agregando 15m con la misma función del runner: si el
mapa agregara de otra forma, sus zonas no serían las que se midieron.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import numpy as np
from fastapi import APIRouter, HTTPException, Query

from bob.api.routes._common import (
    SymbolQuery,
    bars_since,
    in_thread,
    latest_forecast,
    resolve,
    with_session,
)
from bob.api.routes.market import _read_candles
from bob.backtest.technical_runner import ARTIFACT_DIR, resample
from bob.data.binance_rest import INTERVAL_MS
from bob.data.store import OHLCVSeries
from bob.models.narrative import MarketSnapshot, market_narrative
from bob.signals.technical import technical_map

router = APIRouter(tags=["technical"])

ARTIFACTS_DIR: Path = ARTIFACT_DIR
TECHNICAL_PREFIX = "technical-"

#: Velas de 15m que se leen por timeframe: lo justo para que la EMA200 tenga
#: warm-up y las zonas vivas (hasta 192 barras) estén completas.
_BASE_BARS = {"15m": 1_500, "1h": 6_000, "4h": 9_600}
#: Lo que el ajuste de 15m necesita llegar a ver para dibujar algo.
_MIN_BARS = 250


def _series_from_rows(symbol: str, rows: list[Any]) -> OHLCVSeries:
    f = np.array
    return OHLCVSeries(
        symbol=symbol,
        timeframe="15m",
        open_time=f([r.open_time for r in rows], dtype=np.int64),
        open=f([float(r.open) for r in rows]),
        high=f([float(r.high) for r in rows]),
        low=f([float(r.low) for r in rows]),
        close=f([float(r.close) for r in rows]),
        volume=f([float(r.volume) for r in rows]),
        quote_volume=f([float(r.quote_volume) for r in rows]),
        taker_buy_volume=f([float(r.taker_buy_volume) for r in rows]),
        n_trades=f([r.n_trades for r in rows], dtype=np.int64),
    )


def _evidence(tf: str) -> dict[str, Any] | None:
    paths = sorted(p for p in ARTIFACTS_DIR.glob(f"{TECHNICAL_PREFIX}*.json") if p.is_file())
    if not paths:
        return None
    raw = json.loads(paths[-1].read_text(encoding="utf-8"))
    rules: list[dict[str, Any]] = []
    for row in raw.get("confirmatory", []):
        if row.get("timeframe") == tf:
            st = row["stats"]
            rules.append(
                {
                    "rule": row["rule"],
                    "kind": "confirmatoria",
                    "verdict": row["verdict"],
                    "n": st["n"],
                    "mean_r": st["mean_r"],
                    "ci": [st["ci_low"], st["ci_high"]],
                    "ci_level": st["level"],
                    "win_rate": st["win_rate"],
                    "breakeven_win_rate": st["breakeven_win_rate"],
                    "placebo_mean_r": row["placebo"]["mean_r"],
                    "worst_streak": st["worst_streak"],
                }
            )
    if not rules:  # 4h: solo hay exploratorias
        for row in raw.get("exploratory", []):
            if row.get("timeframe") == tf and row.get("overrides") == {}:
                st = row["stats"]
                rules.append(
                    {
                        "rule": row["rule"],
                        "kind": "exploratoria",
                        "verdict": "EXPLORATORIA",
                        "n": st["n"],
                        "mean_r": st["mean_r"],
                        "ci": [st["ci_low"], st["ci_high"]],
                        "ci_level": st["level"],
                        "win_rate": st["win_rate"],
                        "breakeven_win_rate": st["breakeven_win_rate"],
                        "placebo_mean_r": None,
                        "worst_streak": st["worst_streak"],
                    }
                )
    return {
        "artifact_id": paths[-1].stem,
        "preregistration": raw.get("preregistration"),
        "cost_pct": raw.get("cost_pct"),
        "r_multiple": raw.get("rule_config", {}).get("r_multiple"),
        "horizon": raw.get("rule_config", {}).get("horizon"),
        "rules": rules,
    }


def _build(symbol: str, tf: str) -> dict[str, Any]:
    rows = with_session(lambda s: _read_candles(s, symbol, "15m", _BASE_BARS[tf]))
    if len(rows) < _MIN_BARS:
        raise HTTPException(status_code=404, detail=f"sin velas suficientes de {symbol} 15m")
    series = _series_from_rows(symbol, rows)
    gaps = len(series.gaps)
    bars = resample(series, tf)
    out = technical_map(bars["open_time"], bars["open"], bars["high"], bars["low"], bars["close"])
    return {"symbol": symbol, "tf": tf, "gaps_15m": gaps, **out, "evidence": _evidence(tf)}


@router.get("/analysis/technical")
async def technical(
    symbol: str | None = SymbolQuery(),
    tf: Literal["15m", "1h", "4h"] = Query("15m"),
) -> dict[str, Any]:
    sym, _ = resolve(symbol, None)
    return await in_thread(lambda: _build(sym, tf))


#: Más viejo que esto, el pronóstico ya no describe el mercado de ahora.
STALE_AFTER_BARS = 2


def _cone(cones: list[dict[str, Any]], nominal: float) -> tuple[float, float] | None:
    for c in cones:
        if abs(float(c.get("nominal", 0.0)) - nominal) < 1e-6:
            return float(c["price_lo"]), float(c["price_hi"])
    return None


def _narrative(symbol: str, tf: str, lang: str = "es") -> dict[str, Any]:
    record = with_session(lambda s: latest_forecast(s, symbol, tf))
    if record is None:
        raise HTTPException(status_code=404, detail=f"sin pronósticos de {symbol} {tf}")
    m15 = _build(symbol, "15m")
    try:
        m1h: dict[str, Any] | None = _build(symbol, "1h")
    except HTTPException:
        m1h = None
    closes = m15["series"]["close"]
    bars_24h = int(24 * 3_600_000 // INTERVAL_MS["15m"])
    change = closes[-1] / closes[-1 - bars_24h] - 1.0 if len(closes) > bars_24h else None
    regime = json.loads(record.regime_json) if record.regime_json else {}
    cones = json.loads(record.cones_json)
    ev = m15.get("evidence") or {}
    snap = MarketSnapshot(
        symbol=symbol,
        price=float(m15["last_close"]),
        horizon_hours=record.horizon_bars * INTERVAL_MS[tf] / 3_600_000,
        sigma_horizon=float(record.sigma_forecast),
        sigma_ratio=(
            record.sigma_forecast / record.sigma_backward if record.sigma_backward > 0 else None
        ),
        regime_percentile=regime.get("percentile"),
        regime_elevated=regime.get("elevated"),
        cone80=_cone(cones, 0.80),
        cone95=_cone(cones, 0.95),
        change_24h=change,
        map_15m=m15,
        map_1h=m1h,
        features=json.loads(record.features_json),
        technical_mean_r={r["rule"]: r["mean_r"] for r in ev.get("rules", [])},
    )
    age = bars_since(record.open_time, tf)
    return {
        "symbol": symbol,
        "timeframe": tf,
        "forecast_open_time": record.open_time,
        "map_last_time": m15["last_time"],
        "age_bars": age,
        "is_stale": age > STALE_AFTER_BARS,
        **market_narrative(snap, lang="en" if lang == "en" else "es"),
    }


@router.get("/analysis/narrative")
async def narrative(
    symbol: str | None = SymbolQuery(),
    lang: Literal["es", "en"] = Query("es"),
) -> dict[str, Any]:
    """Lectura del mercado en lenguaje natural, armada con plantillas.

    Sin LLM: cada frase sale de una cifra del pronóstico, del mapa técnico o
    del vector de features guardado (ver `models/narrative.py`).
    """
    sym, tf = resolve(symbol, None)
    return await in_thread(lambda: _narrative(sym, tf, lang))
