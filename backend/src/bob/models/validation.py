"""Validación temporal sin fuga — PURO, sin I/O.

El K-Fold estándar de scikit-learn es **inválido** en series financieras con
labels solapados, por dos razones que se suman:

1. Mezcla el orden temporal: entrena con el futuro para predecir el pasado.
2. Aunque respetes el orden, el label de la barra i cubre las barras
   i+1..i+H. Si i queda en train y i+3 en test, el modelo ya vio buena parte
   del futuro que le vas a pedir predecir. La métrica sale inflada y el
   modelo se cae en producción.

La solución (López de Prado, *Advances in Financial ML*, cap. 7) es
**purga + embargo**:

- **Purga**: se eliminan de train las muestras cuyo label se solapa
  temporalmente con el periodo de test.
- **Embargo**: se elimina además una banda de muestras inmediatamente
  posterior al test, porque la autocorrelación serial filtra información
  aunque los labels no se solapen formalmente.

`purged_walk_forward` es el que manda para reportar resultados: replica lo
que BOB puede hacer en vivo (entrenar con pasado, predecir el siguiente
tramo). `purged_kfold` se usa para investigación de features, donde se
acepta entrenar con futuro a cambio de más folds.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Split:
    """Un fold: índices de train y de test, ya purgados."""

    train_idx: np.ndarray
    test_idx: np.ndarray
    name: str

    def __post_init__(self) -> None:
        if np.intersect1d(self.train_idx, self.test_idx).size:
            raise ValueError(f"fold {self.name}: train y test se solapan")

    @property
    def n_train(self) -> int:
        return int(self.train_idx.size)

    @property
    def n_test(self) -> int:
        return int(self.test_idx.size)


def _purge(
    candidate_idx: np.ndarray,
    span: np.ndarray,
    test_start: int,
    test_end: int,
    embargo: int,
) -> np.ndarray:
    """Quita de `candidate_idx` todo lo que toque el test por label o por embargo.

    `span[i]` es la última barra que influye el label de i. Una muestra i se
    solapa con el test si su intervalo [i, span[i]] intersecta
    [test_start, test_end].
    """
    if candidate_idx.size == 0:
        return candidate_idx
    ends = span[candidate_idx]
    # Muestras sin label (span < 0) no aportan y ya vienen filtradas arriba;
    # se les da un end == índice propio para que la lógica sea uniforme.
    ends = np.where(ends < 0, candidate_idx, ends)

    overlaps = (ends >= test_start) & (candidate_idx <= test_end)
    in_embargo = (candidate_idx > test_end) & (candidate_idx <= test_end + embargo)
    return candidate_idx[~(overlaps | in_embargo)]


def purged_walk_forward(
    usable_idx: np.ndarray,
    span: np.ndarray,
    n_splits: int = 6,
    *,
    min_train_frac: float = 0.35,
    embargo_frac: float = 0.01,
    expanding: bool = True,
) -> list[Split]:
    """Walk-forward anclado o rodante, con purga y embargo.

    `usable_idx` son los índices con features y label válidos, en orden
    temporal. Los folds parten el tramo posterior al train mínimo en
    `n_splits` bloques contiguos; el fold k entrena con todo lo anterior al
    bloque k (purgado) y testea sobre el bloque k.

    `expanding=True` (anclado) usa todo el pasado disponible. `False`
    (rodante) mantiene el train de largo constante — útil para detectar si
    el mercado cambió tanto que los datos viejos estorban.
    """
    if n_splits < 1:
        raise ValueError("n_splits debe ser >= 1")
    usable_idx = np.sort(np.asarray(usable_idx, dtype=np.int64))
    n = usable_idx.size
    if n == 0:
        return []

    start_pos = int(n * min_train_frac)
    remaining = n - start_pos
    if remaining < n_splits:
        raise ValueError(
            f"muestras insuficientes: {n} utilizables, {remaining} tras el train mínimo, "
            f"{n_splits} folds pedidos"
        )

    embargo = max(1, int(n * embargo_frac))
    block = remaining // n_splits
    train_len = start_pos

    splits: list[Split] = []
    for k in range(n_splits):
        test_from = start_pos + k * block
        test_to = start_pos + (k + 1) * block if k < n_splits - 1 else n
        test_idx = usable_idx[test_from:test_to]
        if test_idx.size == 0:
            continue

        if expanding:
            train_pool = usable_idx[:test_from]
        else:
            train_pool = usable_idx[max(0, test_from - train_len) : test_from]

        train_idx = _purge(
            train_pool, span, int(test_idx[0]), int(test_idx[-1]), embargo
        )
        if train_idx.size == 0:
            continue
        splits.append(Split(train_idx=train_idx, test_idx=test_idx, name=f"fold_{k + 1}"))

    return splits


def purged_kfold(
    usable_idx: np.ndarray,
    span: np.ndarray,
    n_splits: int = 5,
    *,
    embargo_frac: float = 0.01,
) -> list[Split]:
    """K-Fold con bloques contiguos, purga y embargo a ambos lados.

    Solo para investigación de features: entrena con futuro respecto del
    test, así que sus métricas NO se reportan como desempeño esperado.
    """
    usable_idx = np.sort(np.asarray(usable_idx, dtype=np.int64))
    n = usable_idx.size
    if n_splits < 2 or n < n_splits:
        raise ValueError("n_splits debe estar en [2, n_muestras]")

    embargo = max(1, int(n * embargo_frac))
    bounds = np.linspace(0, n, n_splits + 1, dtype=int)

    splits: list[Split] = []
    for k in range(n_splits):
        test_idx = usable_idx[bounds[k] : bounds[k + 1]]
        if test_idx.size == 0:
            continue
        pool = np.concatenate([usable_idx[: bounds[k]], usable_idx[bounds[k + 1] :]])
        train_idx = _purge(pool, span, int(test_idx[0]), int(test_idx[-1]), embargo)
        if train_idx.size == 0:
            continue
        splits.append(Split(train_idx=train_idx, test_idx=test_idx, name=f"kfold_{k + 1}"))
    return splits


def iter_oos_predictions(
    splits: list[Split],
) -> Iterator[tuple[np.ndarray, np.ndarray]]:
    """Azúcar para recorrer folds devolviendo (train_idx, test_idx)."""
    for split in splits:
        yield split.train_idx, split.test_idx


def assert_no_leakage(splits: list[Split], span: np.ndarray) -> None:
    """Verificación defensiva: ningún label de train alcanza su test.

    Se llama en los tests (regla 5 de CLAUDE.md: los tests deben cazar el
    lookahead) y es barata de correr también en producción.
    """
    for split in splits:
        if split.train_idx.size == 0 or split.test_idx.size == 0:
            continue
        test_start = int(split.test_idx[0])
        test_end = int(split.test_idx[-1])
        ends = span[split.train_idx]
        ends = np.where(ends < 0, split.train_idx, ends)
        bad = (ends >= test_start) & (split.train_idx <= test_end)
        if bad.any():
            offenders = split.train_idx[bad][:5]
            raise AssertionError(
                f"fuga en {split.name}: {int(bad.sum())} muestras de train con label "
                f"que alcanza el test (ej. índices {offenders.tolist()})"
            )
