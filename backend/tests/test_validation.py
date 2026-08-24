"""Tests de la validación temporal purgada.

Estos tests son la red de seguridad de la regla 5 de CLAUDE.md. Si la purga
falla, todas las métricas del proyecto quedan infladas y nadie se entera:
el reporte se ve mejor, no peor. Por eso se verifica la propiedad
directamente (ninguna muestra de train alcanza el test) en vez de confiar en
que el código "parece" correcto.
"""

from __future__ import annotations

import numpy as np
import pytest

from bob.models.validation import (
    Split,
    assert_no_leakage,
    iter_oos_predictions,
    purged_kfold,
    purged_walk_forward,
)


@pytest.fixture
def setup() -> tuple[np.ndarray, np.ndarray]:
    """1000 muestras con labels que abarcan 10 barras cada una."""
    n = 1000
    usable = np.arange(n)
    span = np.minimum(usable + 10, n - 1)
    return usable, span


class TestSplit:
    def test_rechaza_solapamiento_entre_train_y_test(self) -> None:
        with pytest.raises(ValueError, match="se solapan"):
            Split(train_idx=np.array([1, 2, 3]), test_idx=np.array([3, 4]), name="malo")

    def test_conteos(self) -> None:
        s = Split(train_idx=np.arange(10), test_idx=np.arange(10, 15), name="ok")
        assert s.n_train == 10 and s.n_test == 5


class TestPurgedWalkForward:
    def test_genera_el_numero_de_folds_pedido(self, setup) -> None:
        usable, span = setup
        splits = purged_walk_forward(usable, span, n_splits=5)
        assert len(splits) == 5

    def test_train_siempre_precede_al_test(self, setup) -> None:
        usable, span = setup
        for split in purged_walk_forward(usable, span, n_splits=5):
            assert split.train_idx.max() < split.test_idx.min()

    def test_sin_fuga_por_solapamiento_de_labels(self, setup) -> None:
        usable, span = setup
        splits = purged_walk_forward(usable, span, n_splits=5)
        assert_no_leakage(splits, span)

    def test_la_purga_elimina_muestras_al_borde(self, setup) -> None:
        """Sin purga, las últimas muestras de train alcanzarían el test."""
        usable, span = setup
        splits = purged_walk_forward(usable, span, n_splits=4)
        split = splits[0]
        test_start = int(split.test_idx[0])
        # El train se corta antes de que ningún label llegue al test.
        assert span[split.train_idx].max() < test_start

    def test_expanding_crece_y_rolling_no(self, setup) -> None:
        usable, span = setup
        anclado = purged_walk_forward(usable, span, n_splits=4, expanding=True)
        rodante = purged_walk_forward(usable, span, n_splits=4, expanding=False)
        assert anclado[-1].n_train > anclado[0].n_train
        assert rodante[-1].n_train <= anclado[-1].n_train

    def test_los_test_cubren_el_tramo_final_sin_solaparse(self, setup) -> None:
        usable, span = setup
        splits = purged_walk_forward(usable, span, n_splits=5)
        todos = np.concatenate([s.test_idx for s in splits])
        assert todos.size == np.unique(todos).size
        assert np.all(np.diff(todos) > 0)

    def test_detecta_muestras_insuficientes(self) -> None:
        usable = np.arange(10)
        span = usable + 1
        with pytest.raises(ValueError, match="insuficientes"):
            purged_walk_forward(usable, span, n_splits=50)

    def test_n_splits_invalido(self, setup) -> None:
        usable, span = setup
        with pytest.raises(ValueError):
            purged_walk_forward(usable, span, n_splits=0)

    def test_serie_vacia(self) -> None:
        assert purged_walk_forward(np.array([], dtype=int), np.array([]), n_splits=3) == []

    def test_embargo_mas_grande_recorta_mas_train(self) -> None:
        n = 1000
        usable = np.arange(n)
        span = np.minimum(usable + 10, n - 1)
        chico = purged_walk_forward(usable, span, n_splits=3, embargo_frac=0.001)
        grande = purged_walk_forward(usable, span, n_splits=3, embargo_frac=0.05)
        # En walk-forward el embargo actúa hacia adelante; nunca agranda el train.
        assert grande[0].n_train <= chico[0].n_train


class TestPurgedKFold:
    def test_todos_los_folds_tienen_test(self, setup) -> None:
        usable, span = setup
        splits = purged_kfold(usable, span, n_splits=5)
        assert len(splits) == 5
        assert all(s.n_test > 0 for s in splits)

    def test_sin_fuga(self, setup) -> None:
        usable, span = setup
        splits = purged_kfold(usable, span, n_splits=5)
        assert_no_leakage(splits, span)

    def test_purga_a_ambos_lados(self, setup) -> None:
        """A diferencia del walk-forward, acá hay train antes y después del test."""
        usable, span = setup
        split = purged_kfold(usable, span, n_splits=5)[2]
        assert split.train_idx.min() < split.test_idx.min()
        assert split.train_idx.max() > split.test_idx.max()

    def test_n_splits_invalido(self, setup) -> None:
        usable, span = setup
        with pytest.raises(ValueError):
            purged_kfold(usable, span, n_splits=1)


class TestAssertNoLeakage:
    def test_pasa_cuando_esta_limpio(self) -> None:
        span = np.arange(100) + 1
        splits = [Split(train_idx=np.arange(50), test_idx=np.arange(60, 80), name="ok")]
        assert_no_leakage(splits, span)

    def test_detecta_fuga_construida_a_mano(self) -> None:
        """Un split sin purgar debe hacer saltar la verificación."""
        n = 100
        span = np.minimum(np.arange(n) + 30, n - 1)  # labels muy largos
        splits = [Split(train_idx=np.arange(50), test_idx=np.arange(60, 80), name="sucio")]
        with pytest.raises(AssertionError, match="fuga"):
            assert_no_leakage(splits, span)

    def test_ignora_folds_vacios(self) -> None:
        span = np.arange(10)
        splits = [Split(train_idx=np.array([], dtype=int), test_idx=np.arange(3), name="v")]
        assert_no_leakage(splits, span)


def test_iter_oos_predictions(setup) -> None:
    usable, span = setup
    splits = purged_walk_forward(usable, span, n_splits=3)
    pares = list(iter_oos_predictions(splits))
    assert len(pares) == 3
    assert all(isinstance(tr, np.ndarray) and isinstance(te, np.ndarray) for tr, te in pares)
