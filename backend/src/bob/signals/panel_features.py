"""Ensamblado de la matriz de features del panel — PURO, sin I/O.

Toma un `PanelData` y devuelve el cubo `(S, T, F)` que consume el modelo,
combinando tres bloques (§3.3 del pre-registro):

1. **serie de tiempo por símbolo** — las 53 features diarias de
   `features.build_features`, corridas símbolo por símbolo. Se reutiliza el
   motor existente en vez de reescribirlo: ya tiene los tests de causalidad
   y de adimensionalidad, y reescribirlo sería duplicar la superficie donde
   puede esconderse un bug de lookahead.
2. **contexto de mercado** — el factor común de la fecha, su volatilidad, y
   el beta rodante de cada símbolo contra él.
3. **transversales** — el rango percentil de cada feature del bloque 1
   dentro de la sección de su fecha.

**Los huecos cortan la serie, no se puentean.** Las ventanas de `features.py`
cuentan **barras, no tiempo**: a partir de un hueco, toda feature de contexto
describiría una ventana que no existió. Así que un símbolo con huecos
interiores se parte en segmentos contiguos y cada segmento paga su propio
warm-up. Es la misma regla que `_assert_tail_contiguous` aplica en vivo,
trasladada al backtest.
"""

from __future__ import annotations

import numpy as np
from loguru import logger

from bob.data.panel import PanelData
from bob.data.store import OHLCVSeries
from bob.signals import numeric as nm
from bob.signals.cross_section import (
    PanelFeatures,
    cross_sectional_rank,
    market_factor,
    panel_log_returns,
    rolling_beta,
)
from bob.signals.features import build_features

__all__ = ["assemble_panel_features", "MARKET_FEATURES", "BETA_WINDOW"]

#: Ventana del beta rodante, en barras diarias (~3 meses de mercado).
BETA_WINDOW: int = 60

#: Nombres del bloque de contexto de mercado.
MARKET_FEATURES: tuple[str, ...] = (
    "mkt_ret_1d",
    "mkt_ret_5d",
    "mkt_vol_20d",
    "beta_60d",
    "ret_resid_1d",
)

#: Sufijo de las columnas transversales. Se separa con `__` para que sea
#: imposible confundir `mom_5d` con su rango `mom_5d__xs`.
XS_SUFFIX: str = "__xs"


def _contiguous_segments(observed: np.ndarray, step_ok: np.ndarray) -> list[tuple[int, int]]:
    """Tramos `[inicio, fin)` donde el símbolo tiene velas consecutivas."""
    segments: list[tuple[int, int]] = []
    start: int | None = None
    for j, ok in enumerate(observed):
        if not ok:
            if start is not None:
                segments.append((start, j))
                start = None
            continue
        if start is None:
            start = j
        elif not step_ok[j]:
            # hay dato pero el eje de fechas saltó: el hueco es del panel
            segments.append((start, j))
            start = j
    if start is not None:
        segments.append((start, len(observed)))
    return segments


def _symbol_series(panel: PanelData, i: int, lo: int, hi: int) -> OHLCVSeries:
    return OHLCVSeries(
        symbol=panel.symbols[i],
        timeframe=panel.timeframe,
        open_time=panel.open_time[lo:hi],
        open=panel.open[i, lo:hi],
        high=panel.high[i, lo:hi],
        low=panel.low[i, lo:hi],
        close=panel.close[i, lo:hi],
        volume=panel.volume[i, lo:hi],
        quote_volume=panel.quote_volume[i, lo:hi],
        taker_buy_volume=panel.taker_buy_volume[i, lo:hi],
        n_trades=np.nan_to_num(panel.n_trades[i, lo:hi]).astype(np.int64),
    )


def assemble_panel_features(
    panel: PanelData,
    eligible: np.ndarray,
    *,
    min_segment: int = 2,
    add_cross_sectional: bool = True,
) -> PanelFeatures:
    """Arma el cubo `(S, T, F)` del panel.

    `eligible` es la máscara de `signals.universe.eligibility_mask`: se usa
    para el factor de mercado y para los rangos transversales, de modo que un
    símbolo ilíquido no arrastre ni la mediana de la fecha ni los percentiles.

    `add_cross_sectional=False` produce el **baseline secundario** del
    pre-registro —el mismo modelo sin el bloque 3—, que es lo que aísla
    cuánto aporta la sección transversal frente a la serie de tiempo sola.
    """
    n_sym, n_dates = panel.close.shape
    step = panel.interval_ms
    step_ok = np.ones(n_dates, dtype=bool)
    step_ok[1:] = np.diff(panel.open_time) == step

    base_names: list[str] | None = None
    cube: np.ndarray | None = None
    observed_all = panel.observed

    for i in range(n_sym):
        for lo, hi in _contiguous_segments(observed_all[i], step_ok):
            if hi - lo < min_segment:
                continue
            fs = build_features(_symbol_series(panel, i, lo, hi))
            if base_names is None:
                base_names = list(fs.names)
                cube = np.full((n_sym, n_dates, len(base_names)), np.nan, dtype=np.float64)
            assert cube is not None
            if list(fs.names) != base_names:  # pragma: no cover — defensivo
                raise ValueError(
                    f"{panel.symbols[i]}: el motor devolvió otro set de features"
                )
            # Las filas de warm-up del segmento quedan NaN a propósito.
            cube[i, lo + fs.valid_from : hi, :] = fs.X[fs.valid_from :, :]

    if cube is None or base_names is None:
        return PanelFeatures(
            X=np.zeros((n_sym, n_dates, 0)),
            names=[],
            symbols=panel.symbols,
            open_time=panel.open_time,
            valid=np.zeros((n_sym, n_dates), dtype=bool),
        )

    # Las features que dependen de columnas que el panel no persiste salen
    # todas-NaN. Se descartan acá, nombradas, en vez de dejarlas envenenando
    # la máscara de filas válidas: una columna sin un solo valor finito hace
    # que NINGUNA fila sea utilizable, y el síntoma —"el panel no tiene datos"—
    # no se parece en nada a la causa.
    finite_any = np.isfinite(cube).any(axis=(0, 1))
    if not finite_any.all():
        dropped = [n for n, keep in zip(base_names, finite_any, strict=True) if not keep]
        logger.info("panel: {} features sin datos, descartadas: {}", len(dropped), dropped)
        cube = cube[:, :, finite_any]
        base_names = [n for n, keep in zip(base_names, finite_any, strict=True) if keep]

    names = list(base_names)
    n_base = len(base_names)

    # La matriz final se **preasigna y se llena en su lugar**. La versión
    # anterior armaba los tres bloques como arrays sueltos y los concatenaba,
    # lo que en el pico sostiene cada número dos veces: sobre el panel real
    # —659 símbolos × 2.571 fechas × 111 columnas— son ~3 GB en vez de ~1,5, y
    # eso alcanzó para que el sistema matara la corrida. El resultado es
    # idéntico: solo cambia dónde se escriben los mismos valores.
    n_total = n_base + len(MARKET_FEATURES) + (n_base if add_cross_sectional else 0)
    X = np.empty((n_sym, n_dates, n_total), dtype=np.float64)
    X[:, :, :n_base] = cube

    # ---------------------------------------------------------------- #
    # Bloque 2: contexto de mercado
    # ---------------------------------------------------------------- #
    returns = panel_log_returns(panel.close)
    factor = market_factor(returns, eligible)
    # Acumulado y volatilidad del factor con las primitivas causales del
    # proyecto. `rolling_sum` termina en la barra actual inclusive, que es lo
    # correcto: el retorno de mercado de hoy es observable al cierre de hoy.
    factor_5d = nm.rolling_sum(np.nan_to_num(factor, nan=0.0), 5)
    factor_vol = nm.rolling_std(np.nan_to_num(factor, nan=0.0), 20)
    beta = rolling_beta(returns, factor, BETA_WINDOW)

    X[:, :, n_base + 0] = factor[None, :]
    X[:, :, n_base + 1] = factor_5d[None, :]
    X[:, :, n_base + 2] = factor_vol[None, :]
    X[:, :, n_base + 3] = beta
    X[:, :, n_base + 4] = returns - beta * factor[None, :]
    names.extend(MARKET_FEATURES)
    del returns, beta

    # ---------------------------------------------------------------- #
    # Bloque 3: rangos transversales
    # ---------------------------------------------------------------- #
    if add_cross_sectional:
        offset = n_base + len(MARKET_FEATURES)
        for f in range(n_base):
            X[:, :, offset + f] = cross_sectional_rank(cube[:, :, f], eligible)
        names.extend(f"{n}{XS_SUFFIX}" for n in base_names)

    del cube
    valid = np.all(np.isfinite(X), axis=2)
    return PanelFeatures(
        X=X,
        names=names,
        symbols=panel.symbols,
        open_time=panel.open_time,
        valid=valid,
    )
