"""Walk-forward purgado **por fecha** para el panel — PURO, sin I/O.

La diferencia con `models/validation.py` no es cosmética. Aquel parte una
serie de un símbolo por índice de fila; acá una fila es un par
`(símbolo, fecha)` y **la frontera del fold tiene que ser una fecha**, la
misma para todos los símbolos.

Partir por fila dejaría el mismo día en train para unos símbolos y en test
para otros. Con una correlación transversal de retornos crudos de **+0,422**
—medida sobre 238 perpetuos × 2.456 fechas— eso no es una fuga sutil: es
entrenar con el resultado del día que se está evaluando, contado por sus
vecinos. Y no fallaría nada: las métricas saldrían mejores.

Igual que en el gate, se purga el solapamiento del label (`horizon` fechas a
cada lado) y se agrega un embargo.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

__all__ = ["DateSplit", "purged_walk_forward_by_date", "rows_for_dates", "assert_no_date_leakage"]


@dataclass(frozen=True)
class DateSplit:
    """Un fold expresado en **índices de fecha**, no de fila."""

    train_dates: np.ndarray
    test_dates: np.ndarray
    name: str

    def __post_init__(self) -> None:
        if np.intersect1d(self.train_dates, self.test_dates).size:
            raise ValueError(f"fold {self.name}: train y test comparten fechas")

    @property
    def n_train_dates(self) -> int:
        return int(self.train_dates.size)

    @property
    def n_test_dates(self) -> int:
        return int(self.test_dates.size)


def purged_walk_forward_by_date(
    n_dates: int,
    horizon: int,
    *,
    n_splits: int = 6,
    min_train_frac: float = 0.35,
    embargo_frac: float = 0.01,
) -> list[DateSplit]:
    """Folds anclados sobre el eje de fechas, con purga y embargo.

    El fold `k` entrena con todas las fechas anteriores a su bloque de test,
    menos las `horizon` que se solapan con él por label y menos el embargo.
    """
    if n_splits < 1:
        raise ValueError("n_splits debe ser >= 1")
    if horizon < 1:
        raise ValueError("horizon debe ser >= 1")
    if not 0.0 < min_train_frac < 1.0:
        raise ValueError("min_train_frac debe estar en (0, 1)")

    start = int(n_dates * min_train_frac)
    remaining = n_dates - start
    if remaining < n_splits:
        raise ValueError(
            f"quedan {remaining} fechas tras el train mínimo para {n_splits} folds"
        )

    embargo = max(1, int(n_dates * embargo_frac))
    block = remaining // n_splits
    splits: list[DateSplit] = []

    for k in range(n_splits):
        test_from = start + k * block
        test_to = start + (k + 1) * block if k < n_splits - 1 else n_dates
        test = np.arange(test_from, test_to, dtype=np.int64)
        # El label de la fecha `t` mira hasta `t + horizon`, así que cualquier
        # fecha de train a menos de `horizon` del test comparte futuro con él.
        train_end = test_from - horizon - embargo
        if train_end <= 0:
            continue
        train = np.arange(0, train_end, dtype=np.int64)
        splits.append(DateSplit(train_dates=train, test_dates=test, name=f"fold{k + 1}"))

    if not splits:
        raise ValueError("ningún fold quedó con train utilizable tras purgar")
    return splits


def rows_for_dates(date_idx: np.ndarray, selected_dates: np.ndarray) -> np.ndarray:
    """Máscara de filas cuyo índice de fecha cae en `selected_dates`."""
    return np.isin(date_idx, selected_dates)


def assert_no_date_leakage(splits: list[DateSplit], horizon: int) -> None:
    """Verifica que ningún train toque el futuro que su test está evaluando.

    Es el gemelo de `validation.assert_no_leakage`. Se llama desde el runner
    en cada corrida y no solo desde los tests: una fuga acá invalida el
    resultado entero, y el costo de comprobarlo es microsegundos.
    """
    for split in splits:
        if not split.train_dates.size or not split.test_dates.size:
            continue
        last_train = int(split.train_dates.max())
        first_test = int(split.test_dates.min())
        if last_train + horizon >= first_test:
            raise AssertionError(
                f"{split.name}: el train termina en {last_train} y su label alcanza "
                f"{last_train + horizon}, que toca el test desde {first_test}"
            )
