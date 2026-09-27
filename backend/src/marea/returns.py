"""Retorno total diario desde cierre crudo, dividendo y split. PURO.

Convención de Tiingo, verificada sobre TLT (dividendo del 2026-09-01): el
dividendo y el factor de split se informan en la fecha ex. Entonces

    r_t = (close_t + div_t) · split_t / close_{t-1} − 1

`split_t` escala el precio de hoy a la base de acciones de ayer (un 2:1 parte el
precio a la mitad y trae split=2). El dividendo se suma antes de escalar porque
se paga por acción nueva — y los dos eventos juntos en la misma fecha son
excepcionales.

Por qué no usar `adjClose`: se recalcula hacia atrás en cada dividendo, así que
una ingesta incremental mezcla bases de ajuste (ver `marea.data.tiingo`). Este
cálculo solo mira dos días consecutivos y es estable ante cualquier recálculo.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray


def total_returns(
    close: NDArray[np.float64],
    div_cash: NDArray[np.float64],
    split_factor: NDArray[np.float64],
) -> NDArray[np.float64]:
    """Retorno total simple por barra; el primero es NaN (no hay cierre previo)."""
    out = np.full(close.shape, np.nan)
    if close.size > 1:
        out[1:] = (close[1:] + div_cash[1:]) * split_factor[1:] / close[:-1] - 1.0
    return out
