"""Baselines y bandas de error del reporte forward. PURO: recibe arrays.

Existe para tapar dos huecos que el reporte de cobertura tenía y que hacían
que sus dos números principales se leyeran mal:

**1. `R2_vs_base` salía NaN.** `regression_metrics` acepta un baseline y el
tracker nunca le pasaba ninguno, así que la única comparación disponible era
el R² contra la media de la propia muestra. Pero el R² se mide contra la
dispersión del target *en esa ventana*, y una corrida forward de tres días
tiene una fracción de la dispersión de dos años de walk-forward: el R² cae
por aritmética aunque el modelo esté idéntico. Medido sobre las primeras 286
barras resueltas de ETHUSDT, los tres baselines dan R² **negativo** en esa
ventana —son peores que la media constante— mientras el modelo queda en
+0.243. Comparar +0.243 contra el +0.400 del gate y concluir "se degradó"
habría sido exactamente el error inverso al real: contra EWMA, el mismo
modelo mejora de +0.367 a +0.550.

**2. La cobertura del cono se leía sin su incertidumbre.** Los pronósticos
**se solapan**: con H=16, dos consecutivos comparten 15 de 16 barras de
horizonte, así que 286 registros son ~18 observaciones independientes, no
286. Tratarlos como independientes encoge el error estándar por un factor de
4 y convierte ruido en hallazgo. `coverage_interval` hace bootstrap por
bloques móviles de largo H, que es la corrección estándar para dependencia
serial y la única que respeta la estructura del solapamiento.
"""

from __future__ import annotations

import numpy as np

import bob.signals.numeric as nm
from bob.models.baselines import (
    EWMAVolForecaster,
    GarchVolForecaster,
    HARVolForecaster,
)
from bob.models.labeling import forward_volatility


def baseline_predictions(
    close: np.ndarray, idx: np.ndarray, horizon: int, timeframe_ms: int
) -> dict[str, np.ndarray]:
    """EWMA, GARCH(1,1) y HAR-RV evaluados en `idx`, ajustados solo con el pasado.

    `idx` son las posiciones —dentro de la serie de velas— de los pronósticos
    resueltos. Los baselines que necesitan ajuste se ajustan con lo anterior a
    `idx.min()`, que es el mismo trato que recibió el bundle: entrenar con el
    pasado y medirse sobre la ventana forward. EWMA no se ajusta (λ fijo de
    RiskMetrics) y ya es causal barra a barra.
    """
    close = np.asarray(close, dtype=np.float64)
    idx = np.asarray(idx, dtype=np.int64)
    if idx.size == 0:
        raise ValueError("no hay índices que evaluar")
    returns = nm.log_returns(close)
    i0 = int(idx.min())

    ewma = EWMAVolForecaster(horizon=horizon).predict_from_returns(returns)[idx]

    garch_model = GarchVolForecaster(horizon=horizon).fit(returns[:i0])
    garch = garch_model.predict_from_returns(returns)[idx]

    # Mismas ventanas diaria/semanal/mensual que usa `models/experiment.py`.
    w_d = max(2, int(round(4 * 3_600_000 / timeframe_ms)))
    w_w = max(3, int(round(24 * 3_600_000 / timeframe_ms)))
    w_m = max(4, int(round(168 * 3_600_000 / timeframe_ms)))
    design = HARVolForecaster.design_matrix(
        np.nan_to_num(nm.realized_vol(returns, w_d), nan=nm.EPS),
        np.nan_to_num(nm.realized_vol(returns, w_w), nan=nm.EPS),
        np.nan_to_num(nm.realized_vol(returns, w_m), nan=nm.EPS),
    )
    # El target del HAR es la volatilidad forward, que en el pasado ya ocurrió.
    y_vol = forward_volatility(close, horizon)
    har = HARVolForecaster().fit(design[:i0], y_vol[:i0]).predict(design[idx])

    return {"ewma": ewma, "garch": garch, "har": har}


def coverage_interval(
    hits: np.ndarray,
    block: int,
    *,
    n_boot: int = 20_000,
    seed: int = 0,
    level: float = 0.95,
) -> tuple[float, float]:
    """IC de la cobertura empírica por bootstrap de bloques móviles.

    `block` debe ser el horizonte en barras: es el largo del solapamiento, o
    sea la distancia a la que dos aciertos dejan de compartir información.
    Determinista por `seed`, para que el artefacto sea reproducible.
    """
    h = np.asarray(hits, dtype=np.float64)
    n = h.size
    if n == 0:
        return (float("nan"), float("nan"))
    block = max(1, min(int(block), n))
    rng = np.random.default_rng(seed)
    n_blocks = int(np.ceil(n / block))
    starts = rng.integers(0, n - block + 1, size=(n_boot, n_blocks))
    # (n_boot, n_blocks, block) → concatena y recorta al largo original.
    offsets = np.arange(block)
    sample = h[starts[:, :, None] + offsets[None, None, :]]
    means = sample.reshape(n_boot, -1)[:, :n].mean(axis=1)
    tail = (1.0 - level) / 2.0 * 100.0
    lo, hi = np.percentile(means, [tail, 100.0 - tail])
    return (float(lo), float(hi))


def effective_blocks(n: int, horizon: int) -> float:
    """Observaciones independientes aproximadas bajo solapamiento de `horizon`."""
    return float(n) / float(max(1, horizon))
