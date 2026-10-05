"""Medición de potencia del ranking macro — Fase M3, paso 1. PURO.

Antes de construir el ranking entre clases de activo hay que saber si la
muestra puede distinguir un ranking útil del azar. El panel cripto pasó su
gate con 659 símbolos; acá son 23 ETFs muy correlacionados dentro de cada
bloque (`docs/REESTRUCTURACION_MAREA.md` §5). Si no alcanza, **el ranking no
se construye** y ese desenlace es legítimo de antemano.

CRITERIO, FIJADO ANTES DE CORRER (2026-10-05)
=============================================

Configuración primaria: los 23 ETFs del catálogo, historia común desde
2014-07 (REET), rebalanceo semanal al cierre del último día hábil de cada
semana, horizonte **H = 4 semanas** (el centro de "semanas a meses").

Métrica: el **IC mínimo detectable** (MDE) de la media del IC de Spearman por
fecha, con α = 0,05 a dos colas y potencia 80%:

    MDE = (z_0,975 + z_0,80) · SE = 2,80 · SE

`SE` es el error estándar de la media del IC por fecha, por bootstrap de
bloques móviles de largo H (los horizontes se solapan; mismo objeto que
`bob.paper.forward_eval.coverage_interval`). La dispersión del IC por fecha se
mide sobre dos señales triviales —momentum 12-1 y reversión a 4 semanas— y se
toma **la mayor** de las dos: una señal real carga sobre los mismos pocos
factores que ellas, no sobre una permutación al azar, y por eso dispersa más
que el nulo analítico 1/√(N−1), que se reporta solo como referencia.

Veredicto sobre la configuración primaria:

| MDE | lectura | consecuencia |
|---|---|---|
| ≤ 0,05 | potencia holgada | se pre-registra el gate |
| 0,05 – 0,10 | potencia solo para un efecto grande | decide Nichelson, con este número a la vista |
| > 0,10 | **sin potencia** | el ranking no se construye; MAREA macro queda como monitoreo |

El listón de 0,10 sale del propio repo: es del orden del IC offline del panel
cripto (0,107), medido sobre un universo mucho más favorable (659 símbolos,
reversión de corto plazo). Un ranking macro que solo pueda detectar algo más
grande que eso no puede detectar nada plausible.

Las variantes —menos ETFs con más historia, otros horizontes— se reportan para
informar el pre-registro del paso 2. No cambian el veredicto de la primaria.

**Ciego a resultados**: el reporte NO imprime la media del IC de las señales
triviales, solo su dispersión. Ver cuál le gana a cuál antes del pre-registro
contaminaría la elección de baselines y de features del gate.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np
from numpy.typing import NDArray
from scipy.stats import rankdata

Z_ALPHA_TWO_SIDED = 1.959963984540054  # α = 0,05
Z_POWER = 0.8416212335729143  # potencia 80%
MDE_FACTOR = Z_ALPHA_TWO_SIDED + Z_POWER

MDE_AMPLE = 0.05
MDE_MAX = 0.10


def weekly_grid(days: Sequence[date]) -> list[int]:
    """Índice del último día hábil de cada semana ISO (cierre de la semana)."""
    out: list[int] = []
    for i, d in enumerate(days):
        if i + 1 == len(days) or days[i + 1].isocalendar()[:2] != d.isocalendar()[:2]:
            out.append(i)
    return out


def compound(daily: NDArray[np.float64], ends: Sequence[int]) -> NDArray[np.float64]:
    """Retorno total entre cierres semanales consecutivos.

    `daily` es (días, activos) con el retorno simple de cada día; la fila 0 del
    resultado es el retorno de la semana que termina en `ends[1]`.
    """
    log_cum = np.vstack([np.zeros(daily.shape[1]), np.cumsum(np.log1p(daily), axis=0)])
    idx = np.asarray(ends) + 1
    return np.asarray(np.expm1(np.diff(log_cum[idx], axis=0)), dtype=np.float64)


def demean_rows(x: NDArray[np.float64]) -> NDArray[np.float64]:
    return np.asarray(x - np.nanmean(x, axis=1, keepdims=True), dtype=np.float64)


@dataclass(frozen=True)
class EffectiveN:
    n_assets: int
    #: (Σλ)² / Σλ²: cuántos factores de igual peso explicarían la misma varianza.
    participation_ratio: float
    #: Componentes necesarios para explicar el 80% / 90% de la varianza.
    n80: int
    n90: int
    pc1_share: float


def effective_n(returns: NDArray[np.float64]) -> EffectiveN:
    """Número efectivo de apuestas independientes, por PCA de la correlación."""
    corr = np.corrcoef(returns, rowvar=False)
    lam = np.clip(np.linalg.eigvalsh(corr)[::-1], 0.0, None)
    share = lam / lam.sum()
    cum = np.cumsum(share)
    return EffectiveN(
        n_assets=returns.shape[1],
        participation_ratio=float(lam.sum() ** 2 / np.sum(lam**2)),
        n80=int(np.searchsorted(cum, 0.80) + 1),
        n90=int(np.searchsorted(cum, 0.90) + 1),
        pc1_share=float(share[0]),
    )


def forward_returns(weekly: NDArray[np.float64], horizon: int) -> NDArray[np.float64]:
    """Retorno compuesto de las `horizon` semanas SIGUIENTES a cada cierre.

    Fila t = semanas t+1..t+H del arreglo semanal; NaN donde no terminó.
    """
    n = weekly.shape[0]
    out = np.full(weekly.shape, np.nan)
    log_w = np.log1p(weekly)
    cum = np.vstack([np.zeros(weekly.shape[1]), np.cumsum(log_w, axis=0)])
    for t in range(n - horizon):
        out[t] = np.expm1(cum[t + 1 + horizon] - cum[t + 1])
    return out


def momentum(weekly: NDArray[np.float64], lookback: int = 52, skip: int = 4) -> NDArray[np.float64]:
    """Retorno de t−lookback+1 .. t−skip (12-1 en semanas), conocido al cierre t."""
    n = weekly.shape[0]
    cum = np.vstack([np.zeros(weekly.shape[1]), np.cumsum(np.log1p(weekly), axis=0)])
    out = np.full(weekly.shape, np.nan)
    for t in range(lookback - 1, n):
        out[t] = cum[t + 1 - skip] - cum[t + 1 - lookback]
    return out


def reversal(weekly: NDArray[np.float64], lookback: int = 4) -> NDArray[np.float64]:
    """Menos el retorno de las últimas `lookback` semanas, conocido al cierre t."""
    n = weekly.shape[0]
    cum = np.vstack([np.zeros(weekly.shape[1]), np.cumsum(np.log1p(weekly), axis=0)])
    out = np.full(weekly.shape, np.nan)
    for t in range(lookback - 1, n):
        out[t] = -(cum[t + 1] - cum[t + 1 - lookback])
    return out


def ic_by_date(signal: NDArray[np.float64], fwd: NDArray[np.float64]) -> NDArray[np.float64]:
    """IC de Spearman entre señal y retorno futuro, una fecha por fila.

    Solo filas con todos los activos presentes: un IC calculado sobre una
    sección que cambia de tamaño mezcla dispersiones distintas.
    """
    out = np.full(signal.shape[0], np.nan)
    for t in range(signal.shape[0]):
        s, f = signal[t], fwd[t]
        if not (np.all(np.isfinite(s)) and np.all(np.isfinite(f))):
            continue
        rs, rf = rankdata(s), rankdata(f)
        if np.std(rs) == 0 or np.std(rf) == 0:
            continue
        out[t] = float(np.corrcoef(rs, rf)[0, 1])
    return out


def block_se(x: NDArray[np.float64], block: int, n_boot: int = 20_000, seed: int = 0) -> float:
    """Error estándar de la media por bootstrap de bloques móviles."""
    v = x[np.isfinite(x)]
    n = v.size
    if n < 2:
        return float("nan")
    block = max(1, min(int(block), n))
    rng = np.random.default_rng(seed)
    n_blocks = int(np.ceil(n / block))
    starts = rng.integers(0, n - block + 1, size=(n_boot, n_blocks))
    sample = v[starts[:, :, None] + np.arange(block)[None, None, :]]
    means = sample.reshape(n_boot, -1)[:, :n].mean(axis=1)
    return float(np.std(means, ddof=1))


def verdict(mde: float) -> str:
    if not np.isfinite(mde):
        return "sin dato"
    if mde <= MDE_AMPLE:
        return "potencia holgada"
    if mde <= MDE_MAX:
        return "potencia solo para un efecto grande"
    return "sin potencia"
