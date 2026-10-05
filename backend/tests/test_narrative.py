"""Lectura del mercado en lenguaje natural: plantillas, no opiniones."""

from __future__ import annotations

import math
import re
from typing import Any

import numpy as np

from bob.models.narrative import (
    DESCRIPTIVE,
    NO_EDGE,
    VALIDATED,
    MarketSnapshot,
    market_narrative,
)
from bob.signals.technical import technical_map


def _map(seed: int = 0) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    c = 2500.0 * np.exp(np.cumsum(rng.normal(0, 0.003, 800)))
    o = np.concatenate([[2500.0], c[:-1]])
    h = np.maximum(o, c) * 1.001
    lo = np.minimum(o, c) * 0.999
    t = np.arange(c.size, dtype=np.int64) * 900_000
    return technical_map(t, o, h, lo, c)


def _snap(**kw: Any) -> MarketSnapshot:
    m = _map()
    base: dict[str, Any] = dict(
        symbol="ETHUSDT",
        price=float(m["last_close"]),
        horizon_hours=4.0,
        sigma_horizon=0.0076,
        sigma_ratio=1.0,
        regime_percentile=19.0,
        regime_elevated=False,
        cone80=(2650.55, 2730.72),
        cone95=None,
        change_24h=-0.0041,
        map_15m=m,
        map_1h=m,
        features={
            "oi_chg_24h": math.log(1.0068),
            "funding_bps": 0.3,
            "ls_ratio_log": math.log(2.55),
            "top_vs_crowd": -0.4,
            "taker_ratio_mean_4h": 0.0,
        },
        technical_mean_r={"sweep": -0.353, "order_block": -0.297},
    )
    base.update(kw)
    return MarketSnapshot(**base)


def _all_text(n: dict[str, Any]) -> str:
    return n["headline"] + " " + " ".join(s["text"] for s in n["sections"])


def test_cada_seccion_declara_su_respaldo() -> None:
    n = market_narrative(_snap())
    basis = {s["title"]: s["basis"] for s in n["sections"]}
    assert basis["Volatilidad y riesgo"] == VALIDATED
    assert basis["Precio y tendencia"] == DESCRIPTIVE
    assert basis["Estructura y liquidez"] == NO_EDGE
    assert basis["Derivados y posicionamiento"] == DESCRIPTIVE
    assert n["sections"][-1]["title"] == "Lo que esto no dice"
    assert n["sections"][-1]["basis"] == "alcance"


def test_las_cifras_del_texto_salen_del_snapshot() -> None:
    txt = _all_text(market_narrative(_snap()))
    assert "±0,76%" in txt and "percentil 19" in txt
    assert "2.650,55" in txt and "2.730,72" in txt
    assert "cae 0,41%" in txt
    assert "+0,68%" in txt  # interés abierto
    assert "2,55 cuentas en largo" in txt
    assert "−0,35 R" in txt and "−0,30 R" in txt


def test_nunca_pronostica_direccion() -> None:
    """El texto puede describir el pasado; no puede prometer el futuro."""
    prohibido = re.compile(
        r"\b(va a subir|va a bajar|subirá|bajará|debería subir|debería bajar|"
        r"señal de compra|señal de venta|compr[aá] ahora|vend[eé] ahora)\b",
        re.IGNORECASE,
    )
    for kw in (
        {},
        {"regime_elevated": True, "regime_percentile": 93.0},
        {"change_24h": 0.05},
        {"sigma_ratio": 1.6},
        {"sigma_ratio": 0.5},
    ):
        assert not prohibido.search(_all_text(market_narrative(_snap(**kw))))


def test_regimen_elevado_cambia_titular_y_tono() -> None:
    n = market_narrative(_snap(regime_elevated=True, regime_percentile=91.0))
    assert "ELEVADO" in n["headline"]
    vol = n["sections"][0]
    assert vol["tone"] == "warn" and "ELEVADO" in vol["text"]


def test_lectura_del_flujo_oi_contra_precio() -> None:
    def oi_text(oi: float, chg: float) -> str:
        n = market_narrative(_snap(change_24h=chg, features={"oi_chg_24h": math.log(1 + oi)}))
        return next(s["text"] for s in n["sections"] if s["title"].startswith("Derivados"))

    assert "posiciones compradoras" in oi_text(0.02, 0.01)
    assert "posiciones vendedoras" in oi_text(0.02, -0.01)
    assert "cierre de cortos" in oi_text(-0.02, 0.01)
    assert "cierre de largos" in oi_text(-0.02, -0.01)
    assert "casi no cambió" in oi_text(0.001, 0.01)


def test_sin_derivados_no_inventa_la_seccion() -> None:
    n = market_narrative(_snap(features={}))
    assert all(not s["title"].startswith("Derivados") for s in n["sections"])
