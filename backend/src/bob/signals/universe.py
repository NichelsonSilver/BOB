"""Regla de pertenencia al universo del panel — PURA, sin I/O.

Es el módulo donde vive el lookahead más difícil de ver de toda la Fase 10.
El lookahead clásico —usar el precio de mañana en un feature de hoy— lo cazan
los tests que el proyecto ya tiene. El de un panel es otro:

> decidir **qué símbolos están en el universo hoy** usando información de
> mañana.

Un filtro de liquidez calculado sobre la muestra completa, o un "símbolos con
al menos dos años de historia" evaluado al final del backtest, seleccionan a
los sobrevivientes con el futuro. No falla nada, no hay NaN, y el resultado
sale mejor de lo que es. Por eso acá **toda** cuenta mira estrictamente hacia
atrás, y hay un test que lo verifica mutando el futuro.

La regla está pre-registrada en `docs/PREREGISTRO_PANEL_DIARIO.md` §3.1 y no
se ajusta después de ver resultados.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

__all__ = ["UniverseRule", "eligibility_mask", "trailing_dollar_volume", "prior_history"]


@dataclass(frozen=True)
class UniverseRule:
    """Los tres cortes de pertenencia, con los valores del pre-registro.

    `min_dollar_volume` se midió, no se eligió por gusto: con 5 millones de
    USD diarios el ancho mediano de la sección queda en 117 símbolos, que es
    la banda donde el panel tiene sección suficiente para rankear sin bajar a
    monedas cuyo precio se queda quieto días enteros (ver §6.3).
    """

    min_history_bars: int = 180
    min_dollar_volume: float = 5_000_000.0
    volume_lookback: int = 30

    def __post_init__(self) -> None:
        if self.min_history_bars < 1:
            raise ValueError("min_history_bars debe ser >= 1")
        if self.volume_lookback < 1:
            raise ValueError("volume_lookback debe ser >= 1")
        if self.min_dollar_volume < 0:
            raise ValueError("min_dollar_volume no puede ser negativo")


def prior_history(observed: np.ndarray) -> np.ndarray:
    """Cuántas velas **estrictamente anteriores** tiene cada símbolo en cada fecha.

    En la columna `j` cuenta las observaciones de `0 .. j-1`. Incluir `j`
    dejaría que el símbolo cuente su propia barra de hoy para decidir si es
    elegible hoy, que es exactamente la clase de circularidad que esta regla
    evita.
    """
    counts = np.cumsum(observed.astype(np.int64), axis=1)
    out = np.zeros_like(counts)
    out[:, 1:] = counts[:, :-1]
    return out


def trailing_dollar_volume(
    quote_volume: np.ndarray, lookback: int, *, observed: np.ndarray | None = None
) -> np.ndarray:
    """Mediana del volumen en USD de las `lookback` fechas anteriores a cada una.

    Mediana y no media: el volumen de cripto tiene picos de uno o dos días
    —un listado, una liquidación en cascada— que una media arrastra durante
    todo el mes siguiente y que harían entrar al universo a un símbolo que ya
    volvió a estar seco.

    La ventana termina en `j-1` inclusive. Las fechas sin dato no cuentan como
    volumen cero: cuentan como **nada**, porque un símbolo que todavía no
    listaba no es un símbolo sin volumen.
    """
    if lookback < 1:
        raise ValueError("lookback debe ser >= 1")
    values = np.asarray(quote_volume, dtype=np.float64)
    if observed is not None:
        values = np.where(observed, values, np.nan)

    n_sym, n_dates = values.shape
    out = np.full((n_sym, n_dates), np.nan, dtype=np.float64)
    for j in range(1, n_dates):
        lo = max(0, j - lookback)
        window = values[:, lo:j]
        # Una ventana entera de NaN devuelve NaN sin warning ruidoso: es el
        # caso normal de un símbolo que todavía no existía.
        empty = np.all(np.isnan(window), axis=1)
        if window.shape[1] == 0:
            continue
        med = np.full(n_sym, np.nan, dtype=np.float64)
        if not np.all(empty):
            med[~empty] = np.nanmedian(window[~empty], axis=1)
        out[:, j] = med
    return out


def eligibility_mask(
    close: np.ndarray,
    quote_volume: np.ndarray,
    rule: UniverseRule | None = None,
) -> np.ndarray:
    """Máscara `(S, T)`: ¿el símbolo `i` es operable en la fecha `j`?

    Tres condiciones, todas sobre el pasado estricto:

    1. hay vela en `j` (si no hay precio, no hay entrada posible)
    2. acumuló al menos `min_history_bars` velas antes de `j`
    3. su volumen mediano en USD de las `volume_lookback` fechas previas
       supera el corte

    **Un símbolo que se deslista no se borra hacia atrás.** Sale del universo
    cuando deja de tener velas, y no un día antes: quitarlo de las fechas en
    que sí cotizaba sería reintroducir el sesgo de supervivencia por la puerta
    de atrás, que es el error que la Fase 10 existe para evitar.
    """
    rule = rule or UniverseRule()
    close = np.asarray(close, dtype=np.float64)
    quote_volume = np.asarray(quote_volume, dtype=np.float64)
    if close.shape != quote_volume.shape:
        raise ValueError(
            f"close {close.shape} y quote_volume {quote_volume.shape} deben coincidir"
        )
    if close.ndim != 2:
        raise ValueError("el panel debe ser 2-D (símbolos x fechas)")

    observed = np.isfinite(close)
    has_history = prior_history(observed) >= rule.min_history_bars
    liquidity = trailing_dollar_volume(
        quote_volume, rule.volume_lookback, observed=observed
    )
    liquid_enough: np.ndarray = np.greater_equal(
        liquidity,
        rule.min_dollar_volume,
        where=np.isfinite(liquidity),
        out=np.zeros_like(liquidity, dtype=bool),
    )
    result: np.ndarray = observed & has_history & liquid_enough
    return result
