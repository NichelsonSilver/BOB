"""El filtro de régimen leído por un operador CON STOP. PURO.

El filtro de la Fase 5-bis se validó contra liquidaciones a 20x sin stop. La
página Riesgo lo muestra a alguien que opera con stop y a ≤5x, para quien la
liquidación no es el riesgo (a 5x no hubo un solo evento en dos años). Para
ese usuario, la UI afirma dos cosas que nadie había medido:

1. "En régimen elevado el mismo stop salta más seguido."
2. "P(el ruido toca el stop) ≈ X%", calculado con `projection.prob_touch`
   sobre la sigma pronosticada.

Este módulo las mide. Criterio en `docs/PREREGISTRO_FILTRO_STOP.md`.

**Un stop a distancia `d` es una liquidación a otro leverage.** El evento
"la excursión adversa llegó a `d`" es exactamente el que usa `riskfilter.py`
con `d = distancia de liquidación`. Por eso se reutiliza la misma excursión,
el mismo lift y el mismo bootstrap por bloques: lo único que cambia es el
umbral. Si se reescribiera, la diferencia entre las dos mediciones podría
venir del código y no del umbral.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from bob.models.projection import prob_touch
from bob.models.riskfilter import AdverseExcursion, block_index_samples


def stop_events(exc: AdverseExcursion, stop_pct: float) -> np.ndarray:
    """(n, 2) bool: ¿un stop a `stop_pct` saltó en el horizonte? (long, short).

    NaN cuenta como False, igual que `riskfilter.liquidates`.
    """
    if stop_pct <= 0:
        raise ValueError("la distancia del stop debe ser > 0")
    with np.errstate(invalid="ignore"):
        hit_l = np.nan_to_num(exc.long, nan=-np.inf) >= stop_pct
        hit_s = np.nan_to_num(exc.short, nan=-np.inf) >= stop_pct
    return np.column_stack([hit_l, hit_s])


def predicted_touch(sigma: np.ndarray, stop_pct: float) -> np.ndarray:
    """La probabilidad que muestra la calculadora, barra a barra.

    Es literalmente `projection.prob_touch`, la misma función que llama
    `sizing.size_position`: lo que se mide es el número que ve el usuario,
    no una reimplementación.
    """
    s = np.asarray(sigma, dtype=np.float64)
    out = np.full(s.shape, np.nan)
    ok = np.isfinite(s) & (s > 0)
    out[ok] = [prob_touch(stop_pct, float(v)) for v in s[ok]]
    return out


@dataclass(frozen=True)
class Calibration:
    """Frecuencia observada contra probabilidad predicha, en un subconjunto."""

    label: str
    n_bars: int
    predicted: float
    observed: float
    gap: float  # observado − predicho; > 0 = la calculadora subestima
    gap_ci95: tuple[float, float]
    ratio: float  # observado / predicho

    @property
    def gap_excludes_zero(self) -> bool:
        lo, hi = self.gap_ci95
        return bool(lo > 0 or hi < 0)


def calibration(
    predicted: np.ndarray,
    events: np.ndarray,
    mask: np.ndarray,
    block: int,
    *,
    label: str,
    n_boot: int = 5_000,
    seed: int = 0,
    chunk: int = 200,
) -> Calibration:
    """Calibración de `predicted` contra `events` en las barras de `mask`.

    `events` es (n, 2): cada barra aporta un setup long y uno short, y la
    probabilidad predicha es la misma para los dos (la fórmula no tiene
    dirección). El IC remuestrea bloques de `block` barras sobre la serie
    ENTERA y recalcula dentro de la máscara: los setups solapan 15 de 16
    barras, y remuestrear solo las barras de la máscara rompería esa
    dependencia, porque las barras elevadas vienen en rachas.
    """
    p = np.asarray(predicted, dtype=np.float64)
    m = np.asarray(mask, dtype=bool) & np.isfinite(p)
    e = np.asarray(events, dtype=bool)
    k = e.shape[1]
    n_sel = int(m.sum())
    if n_sel == 0:
        nan = float("nan")
        return Calibration(label, 0, nan, nan, nan, (nan, nan), nan)

    # Por barra: suma de (observado − predicho) sobre sus k setups, y conteo.
    p0 = np.where(m, p, 0.0)
    diff = np.where(m, e.sum(axis=1) - k * p0, 0.0)
    cnt = np.where(m, float(k), 0.0)
    obs = float(e[m].mean())
    pred = float(p[m].mean())

    draws: list[np.ndarray] = []
    for idx in block_index_samples(p.size, block, n_boot, seed, chunk):
        c = cnt[idx].sum(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            draws.append(np.where(c > 0, diff[idx].sum(axis=1) / np.maximum(c, 1.0), np.nan))
    d = np.concatenate(draws)
    d = d[np.isfinite(d)]
    lo, hi = np.percentile(d, [2.5, 97.5]) if d.size else (np.nan, np.nan)
    return Calibration(
        label=label,
        n_bars=n_sel,
        predicted=pred,
        observed=obs,
        gap=obs - pred,
        gap_ci95=(float(lo), float(hi)),
        ratio=obs / pred if pred > 0 else float("nan"),
    )


def hit_rate_ratio(events: np.ndarray, flag: np.ndarray, eligible: np.ndarray) -> float:
    """Tasa de stop saltado en barras marcadas / en barras no marcadas.

    Es la lectura directa de la frase de la UI ("salta más seguido"); el
    lift es la métrica que decide, esto es lo que se le dice al usuario.
    """
    e = np.asarray(events, dtype=bool)
    f = np.asarray(flag, dtype=bool) & eligible
    nf = ~np.asarray(flag, dtype=bool) & eligible
    if not f.any() or not nf.any():
        return float("nan")
    return float(e[f].mean() / e[nf].mean()) if e[nf].mean() > 0 else float("nan")


def bracket_outcomes(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    stop_pct: float,
    r_multiple: float,
    horizon: int,
    cost_pct: float,
) -> np.ndarray:
    """(n, 2) resultado neto en R de un trade long y uno short por barra.

    Entrada al open de `i+1`, stop a `stop_pct`, objetivo a `r_multiple`
    veces el riesgo, salida por tiempo al cierre de `i+horizon`. Usa el
    simulador de las reglas técnicas (`technical_rules.simulate`), con sus
    mismas convenciones conservadoras: si una vela toca los dos, cuenta el
    stop. NaN donde el horizonte no terminó.

    Sin edge direccional, el bruto esperado es 0 por álgebra (Fase 5,
    hallazgo (a)) y el neto, −costo/riesgo, en cualquier régimen. Esto mide
    si el régimen cambia eso — por ejemplo, por gaps a través del stop.
    """
    from bob.models.technical_rules import simulate

    o = np.asarray(open_, dtype=np.float64)
    h = np.asarray(high, dtype=np.float64)
    lo = np.asarray(low, dtype=np.float64)
    c = np.asarray(close, dtype=np.float64)
    n = c.size
    out = np.full((n, 2), np.nan)
    for i in range(n - horizon - 1):
        entry = o[i + 1]
        for j, (direction, sign) in enumerate((("long", 1.0), ("short", -1.0))):
            stop = entry * (1.0 - sign * stop_pct)
            target = entry * (1.0 + sign * r_multiple * stop_pct)
            res = simulate(
                direction,
                i + 1,
                entry,
                stop,
                target,
                o,
                h,
                lo,
                c,
                horizon,
                first_bar_stop_only=False,
            )
            if res is None:
                continue
            gross = sign * (res[1] - entry) / entry
            out[i, j] = (gross - cost_pct) / stop_pct
    return out


def masked_mean_interval(
    values: np.ndarray,
    mask: np.ndarray,
    block: int,
    *,
    n_boot: int = 2_000,
    seed: int = 0,
    chunk: int = 200,
) -> tuple[float, float, float]:
    """Media de `values` (n, k) en las barras de `mask`, con IC95% por bloques.

    Mismo esquema que `calibration`: se remuestrea la serie entera en bloques
    y se promedia dentro de la máscara.
    """
    v = np.asarray(values, dtype=np.float64)
    m = np.asarray(mask, dtype=bool)
    fin = np.isfinite(v) & m[:, None]
    s = np.where(fin, v, 0.0).sum(axis=1)
    c = fin.sum(axis=1).astype(np.float64)
    if c.sum() == 0:
        nan = float("nan")
        return (nan, nan, nan)
    draws: list[np.ndarray] = []
    for idx in block_index_samples(v.shape[0], block, n_boot, seed, chunk):
        cc = c[idx].sum(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            draws.append(np.where(cc > 0, s[idx].sum(axis=1) / np.maximum(cc, 1.0), np.nan))
    d = np.concatenate(draws)
    d = d[np.isfinite(d)]
    lo, hi = np.percentile(d, [2.5, 97.5])
    return float(s.sum() / c.sum()), float(lo), float(hi)
