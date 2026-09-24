"""El puente entre el experimento del panel y la emisión diaria — PURO, sin I/O.

`panel_experiment.py` entrena un modelo por fold y lo tira: su producto son
métricas. Para emitir hace falta **un** ajuste consultable por fecha, que es
lo que hace `fit_panel_bundle` — el mismo papel que `production.fit_bundle`
cumple para el vivo de 15m.

**Las últimas `H` fechas quedan fuera del train solas.** Su etiqueta no
terminó de ocurrir: el retorno relativo de la fecha `t` necesita el cierre de
`t+H`. Incluirlas sería entrenar con un resultado que todavía no existe, que
es el lookahead de la regla 5 en su forma más directa.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from bob.models.forecast import BarrierProbabilityModel
from bob.models.panel_experiment import PanelConfig
from bob.signals.cross_section import (
    PanelFeatures,
    cross_sectional_rank,
    forward_path_return,
    relative_target,
)

__all__ = ["PanelBundle", "fit_panel_bundle", "PanelDatePrediction", "predict_date"]

#: Versión del bundle del panel. Viaja a cada `PanelForecastRecord` para que
#: una muestra forward con dos versiones adentro sea detectable en vez de
#: promediarse en silencio (lección del tracker de la Fase 5).
PANEL_MODEL_VERSION = "bob-panel-0.1.0"


@dataclass
class PanelBundle:
    """Un ajuste del panel, consultable fecha a fecha."""

    model: BarrierProbabilityModel
    feature_names: list[str]
    config: PanelConfig
    n_train: int
    #: `open_time` de la última fecha cuya etiqueta ya había ocurrido al
    #: ajustar. Es el dato que permite auditar después si un pronóstico se
    #: emitió con un modelo que ya conocía su propio resultado.
    fit_through_ms: int
    model_version: str = field(default="")

    def __post_init__(self) -> None:
        if not self.model_version:
            self.model_version = f"{PANEL_MODEL_VERSION}+{self.config.model_kind}"


@dataclass(frozen=True)
class PanelDatePrediction:
    """Lo que el panel emite para una fecha: un ordenamiento, no una dirección."""

    date_index: int
    open_time: int
    symbols: tuple[str, ...]
    probability: np.ndarray
    rank: np.ndarray
    reference_close: np.ndarray

    @property
    def width(self) -> int:
        return len(self.symbols)


def _trainable_mask(
    features: PanelFeatures,
    eligible: np.ndarray,
    y_grid: np.ndarray,
    horizon: int,
    n_dates: int,
) -> np.ndarray:
    """Filas con etiqueta ya ocurrida, elegibles y con todas sus features."""
    resolved = np.zeros((1, n_dates), dtype=bool)
    # La fecha `t` necesita el cierre de `t+H`; la última utilizable es
    # `n_dates - 1 - H`. El `-1` extra sale de que se entra al open de `t+1`.
    last = n_dates - horizon - 1
    if last >= 0:
        resolved[0, : last + 1] = True
    mask: np.ndarray = eligible & features.valid & (y_grid >= 0) & resolved
    return mask


def fit_panel_bundle(
    features: PanelFeatures,
    open_: np.ndarray,
    close: np.ndarray,
    eligible: np.ndarray,
    config: PanelConfig | None = None,
) -> PanelBundle:
    """Ajusta UN modelo sobre toda la historia con etiqueta resuelta.

    A diferencia del experimento no hay folds: el producto no son métricas,
    es un modelo que después responde por fecha. La calibración isotónica
    interna de `BarrierProbabilityModel` sigue usando su walk-forward purgado,
    así que el mapa de calibración no se ajusta sobre predicciones in-sample.
    """
    cfg = config or PanelConfig()
    n_sym, n_dates, _ = features.shape

    forward, _ = forward_path_return(open_, close, cfg.horizon)
    y_grid, _ = relative_target(forward, eligible, cut=cfg.target_cut)

    usable = _trainable_mask(features, eligible, y_grid, cfg.horizon, n_dates)
    sym_i, date_j = np.nonzero(usable)
    if sym_i.size == 0:
        raise ValueError("no hay filas con etiqueta resuelta para ajustar el panel")

    # Orden por (fecha, símbolo): con las filas ordenadas por fecha, la purga
    # interna por índice de fila ES una purga por fecha. Misma razón que en
    # `panel_experiment`.
    order = np.lexsort((sym_i, date_j))
    sym_i, date_j = sym_i[order], date_j[order]

    X = features.X[sym_i, date_j, :]
    y = y_grid[sym_i, date_j].astype(np.int64)

    last_row_of_date = np.searchsorted(date_j, np.arange(n_dates + 1), side="right") - 1
    horizon_date = np.minimum(date_j + cfg.horizon, n_dates - 1)
    span = last_row_of_date[horizon_date].astype(np.int64)

    model = BarrierProbabilityModel(kind=cfg.model_kind, seed=cfg.seed)
    model.fit(X, y, np.arange(y.size), span)

    return PanelBundle(
        model=model,
        feature_names=list(features.names),
        config=cfg,
        n_train=int(y.size),
        fit_through_ms=int(features.open_time[int(date_j.max())]),
    )


def predict_date(
    bundle: PanelBundle,
    features: PanelFeatures,
    close: np.ndarray,
    eligible: np.ndarray,
    date_index: int,
) -> PanelDatePrediction:
    """Probabilidades del panel para UNA fecha, sobre los símbolos elegibles.

    El rango transversal se calcula **solo entre los símbolos de esa fecha**,
    que es la definición del target: superar la mediana de la sección propia.
    Rankear contra otra cosa mediría otra pregunta.
    """
    if list(features.names) != bundle.feature_names:
        raise ValueError(
            "el bundle se ajustó con otro set de features — "
            f"{len(bundle.feature_names)} columnas contra {len(features.names)}"
        )
    n_sym, n_dates, _ = features.shape
    if not 0 <= date_index < n_dates:
        raise IndexError(f"fecha {date_index} fuera del panel ({n_dates} fechas)")

    rows = np.flatnonzero(eligible[:, date_index] & features.valid[:, date_index])
    if rows.size < 3:
        raise ValueError(
            f"la sección de {features.open_time[date_index]} tiene {rows.size} "
            "símbolos utilizables; hacen falta al menos 3 para que la mediana "
            "defina arriba y abajo"
        )

    proba = bundle.model.predict_proba(features.X[rows, date_index, :])

    # El rango se computa con la misma función del experimento, sobre una
    # columna de una sola fecha: así el número que se persiste es el mismo
    # objeto que el backtest usó para armar los deciles.
    column = np.full((n_sym, 1), np.nan)
    column[rows, 0] = proba
    ranks = cross_sectional_rank(column)[rows, 0]

    return PanelDatePrediction(
        date_index=int(date_index),
        open_time=int(features.open_time[date_index]),
        symbols=tuple(features.symbols[i] for i in rows),
        probability=np.asarray(proba, dtype=np.float64),
        rank=np.asarray(ranks, dtype=np.float64),
        reference_close=close[rows, date_index],
    )
