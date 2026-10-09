"""M6.8: el inglés de MAREA cubre todo lo que existe hoy y no toca una cifra."""

from __future__ import annotations

import copy
import json
from typing import Any

from marea import i18n
from marea.data.catalog import COT_MARKETS, ETFS
from marea.monitor import INDICATORS
from marea.strength import CLASS_LABEL


def test_toda_clave_de_hoy_tiene_ingles() -> None:
    """Un indicador, mercado COT, ETF o clase nuevo sin traducción se nota acá."""
    assert {i.key for i in INDICATORS} <= set(i18n.INDICATORS_EN)
    assert {m.code for m in COT_MARKETS} <= set(i18n.COT_EN)
    assert {e.ticker for e in ETFS} <= set(i18n.ETFS_EN)
    assert set(CLASS_LABEL) <= set(i18n.CLASSES_EN)
    # Una nota en español sin nota en inglés quedaría a medio traducir.
    for i in INDICATORS:
        assert bool(i.caveat) == bool(i18n.INDICATORS_EN[i.key][1]), i.key
    for e in ETFS:
        assert bool(e.caveat) == bool(i18n.ETFS_EN[e.ticker][1]), e.ticker


def _numbers(x: Any) -> list[Any]:
    """Todas las hojas numéricas del reporte, en orden."""
    if isinstance(x, dict):
        return [v for k in sorted(x) for v in _numbers(x[k])]
    if isinstance(x, list):
        return [v for item in x for v in _numbers(item)]
    return [x] if isinstance(x, (int, float)) and not isinstance(x, bool) else []


def test_traducir_no_mueve_cifras_ni_toca_el_original() -> None:
    rep = {
        "as_of": "2026-10-02",
        "regime": {"label": "contractivo", "composite_pct": 0.18},
        "indicators": [
            {"key": "m2", "name": "M2 EE.UU.", "caveat": "mensual", "unit": "miles de millones USD",
             "level": 21000.5, "pct": 0.4, "label": "neutro"},
            {"key": "tasa_real", "name": "Tasa real", "caveat": None, "unit": "%", "level": 1.8},
        ],
        "cot": [{"code": "088691", "name": "Oro", "net_pct_oi": 0.31}],
        "method": {"signal": "cambio a 13 semanas, orientado", "short_change_weeks": 4,
                   "min_history_weeks": 104, "forecast": False},
    }
    before = copy.deepcopy(rep)
    en = i18n.liquidity(rep, "en")
    assert rep == before
    assert _numbers(en) == _numbers(rep)
    assert en["indicators"][0]["name"] == "US M2"
    assert en["indicators"][1]["caveat"] is None
    assert en["indicators"][0]["unit"] == "billion USD"
    assert en["cot"][0]["name"] == "Gold"
    assert en["method"]["signal"].startswith("13-week change")
    assert en["regime"]["label"] == "contractivo"  # código, lo traduce quien dibuja
    assert i18n.liquidity(rep, "es") is rep


def test_veredicto_y_fuerza_relativa() -> None:
    v = {"reading": "no hay orden detectable a esta potencia",
         "ic_by_spy_sign": {"SPY sube": {"n": 352, "ic_mean": 0.09}}, "ic_mean": 0.036}
    en = i18n.ranking_verdict(v, "en")
    assert en["reading"] == "no detectable ranking at this power"
    assert en["ic_by_spy_sign"] == {"SPY up": {"n": 352, "ic_mean": 0.09}}
    rep = {"classes": [{"block": "divisas", "label": "Dólar", "rel_4s": 0.05}],
           "etfs": [{"ticker": "UUP", "name": "Dólar (futuros DXY)", "caveat": "fondo de futuros"}],
           "method": {"measure": "x"}}
    s = i18n.strength(rep, "en")
    assert s["classes"][0]["label"] == "US dollar" and s["etfs"][0]["caveat"] == "futures fund"
    assert json.dumps(_numbers(s)) == json.dumps(_numbers(rep))
