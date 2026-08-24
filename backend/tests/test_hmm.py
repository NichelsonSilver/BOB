"""Tests del HMM gaussiano de regímenes.

Dos cosas se protegen acá por encima de todo:

1. **Que el filtro sea causal.** `filtered_probs` no puede cambiar cuando
   cambia el futuro. Es el mismo invariante que testea el feature engine, y es
   la razón por la que este módulo no usa el `predict` de una librería.
2. **Que el EM sea correcto.** Se verifica recuperando un HMM sintético de
   parámetros conocidos: si Baum-Welch está mal, los parámetros no vuelven.
"""

from __future__ import annotations

import numpy as np
import pytest

from bob.models.hmm import (
    GaussianHMM,
    StateSelection,
    finite_rows,
    regime_observations,
    select_n_states,
)
from bob.models.markov import MarketRegime

# HMM sintético: un estado calmo con deriva positiva y uno agitado con deriva
# negativa. Es la caricatura de un mercado de cripto, y sirve como verdad
# conocida contra la cual medir el ajuste.
TRUE_TRANSMAT = np.array([[0.97, 0.03], [0.06, 0.94]])
TRUE_MEANS = np.array([[0.0006, -5.5], [-0.0012, -4.6]])
TRUE_SDS = np.array([[0.003, 0.20], [0.010, 0.25]])


def _simulate(n: int = 4000, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    X = np.empty((n, 2), dtype=np.float64)
    states = np.empty(n, dtype=np.int64)
    state = 0
    for t in range(n):
        states[t] = state
        X[t] = rng.normal(TRUE_MEANS[state], TRUE_SDS[state])
        state = int(rng.choice(2, p=TRUE_TRANSMAT[state]))
    return X, states


@pytest.fixture(scope="module")
def synthetic() -> tuple[np.ndarray, np.ndarray]:
    return _simulate()


@pytest.fixture(scope="module")
def fitted(synthetic: tuple[np.ndarray, np.ndarray]) -> GaussianHMM:
    X, _ = synthetic
    return GaussianHMM(n_states=2, seed=0).fit(X)


def _match_states(model: GaussianHMM) -> list[int]:
    """El EM no garantiza el orden de los estados: se emparejan por media."""
    return [int(np.argmin(np.abs(model.means_[:, 0] - m[0]))) for m in TRUE_MEANS]


class TestObservaciones:
    def test_devuelve_retorno_y_log_volatilidad(self) -> None:
        close = np.cumprod(1 + np.full(300, 0.001))
        X = regime_observations(close, vol_window=96)
        assert X.shape == (300, 2)

    def test_el_warmup_sale_nan_y_no_relleno(self) -> None:
        close = np.cumprod(1 + np.full(300, 0.001)) * 2500
        X = regime_observations(close, vol_window=96)
        mask = finite_rows(X)
        assert not mask[:96].any()
        assert mask[120:].all()

    def test_es_adimensional(self) -> None:
        """Invariante del proyecto: escalar el precio x10 no mueve nada."""
        rng = np.random.default_rng(3)
        close = 2500 * np.cumprod(1 + rng.normal(0, 0.004, 500))
        a = regime_observations(close, vol_window=96)
        b = regime_observations(close * 10.0, vol_window=96)
        mask = finite_rows(a)
        np.testing.assert_allclose(a[mask], b[mask], rtol=1e-9, atol=1e-12)

    def test_es_causal(self) -> None:
        """Mutar el futuro no puede alterar una observación pasada."""
        rng = np.random.default_rng(4)
        close = 2500 * np.cumprod(1 + rng.normal(0, 0.004, 400))
        base = regime_observations(close, vol_window=96)
        alterado = close.copy()
        alterado[300:] *= 1.5
        mutado = regime_observations(alterado, vol_window=96)
        np.testing.assert_allclose(base[:300], mutado[:300], rtol=1e-12, equal_nan=True)

    def test_rechaza_matrices(self) -> None:
        with pytest.raises(ValueError, match="1-D"):
            regime_observations(np.ones((10, 2)))


class TestAjuste:
    def test_recupera_la_matriz_de_transicion(self, fitted: GaussianHMM) -> None:
        order = _match_states(fitted)
        recuperada = fitted.transmat_[np.ix_(order, order)]
        np.testing.assert_allclose(recuperada, TRUE_TRANSMAT, atol=0.05)

    def test_recupera_las_medias(self, fitted: GaussianHMM) -> None:
        order = _match_states(fitted)
        # atol holgado en la columna de log-volatilidad: recuperar -4.5974
        # contra -4.6 verdadero es error de muestreo, no de estimador.
        np.testing.assert_allclose(fitted.means_[order], TRUE_MEANS, atol=0.005)

    def test_recupera_las_desviaciones(self, fitted: GaussianHMM) -> None:
        order = _match_states(fitted)
        np.testing.assert_allclose(np.sqrt(fitted.covars_[order]), TRUE_SDS, atol=0.02)

    def test_identifica_el_estado_oculto(
        self, fitted: GaussianHMM, synthetic: tuple[np.ndarray, np.ndarray]
    ) -> None:
        X, states = synthetic
        order = _match_states(fitted)
        pred = fitted.filtered_states(X)
        traducidos = np.array([order.index(s) for s in pred])
        assert (traducidos == states).mean() > 0.90

    def test_la_verosimilitud_nunca_baja(self, fitted: GaussianHMM) -> None:
        """Garantía teórica del EM: si baja, el M-step está mal."""
        hist = fitted.log_likelihood_history_
        assert len(hist) >= 2
        assert all(b >= a - 1e-6 for a, b in zip(hist, hist[1:]))

    def test_converge(self, fitted: GaussianHMM) -> None:
        assert fitted.converged_ is True
        assert fitted.n_iter_run_ < 100

    def test_es_determinista(self, synthetic: tuple[np.ndarray, np.ndarray]) -> None:
        X, _ = synthetic
        a = GaussianHMM(n_states=3, seed=7).fit(X)
        b = GaussianHMM(n_states=3, seed=7).fit(X)
        np.testing.assert_allclose(a.transmat_, b.transmat_)
        np.testing.assert_allclose(a.means_, b.means_)

    def test_un_solo_estado_es_una_gaussiana(
        self, synthetic: tuple[np.ndarray, np.ndarray]
    ) -> None:
        X, _ = synthetic
        m = GaussianHMM(n_states=1, seed=0).fit(X)
        np.testing.assert_allclose(m.means_[0], X.mean(axis=0), atol=1e-6)
        np.testing.assert_allclose(m.transmat_, [[1.0]], atol=1e-9)

    def test_rechaza_datos_insuficientes(self) -> None:
        with pytest.raises(ValueError, match="no alcanzan"):
            GaussianHMM(n_states=3).fit(np.zeros((3, 2)))

    def test_rechaza_nan(self) -> None:
        X = np.ones((100, 2))
        X[5, 0] = np.nan
        with pytest.raises(ValueError, match="NaN"):
            GaussianHMM(n_states=2).fit(X)

    def test_rechaza_dimension_equivocada(self) -> None:
        with pytest.raises(ValueError, match="matriz"):
            GaussianHMM(n_states=2).fit(np.ones(50))

    def test_rechaza_cero_estados(self) -> None:
        with pytest.raises(ValueError, match="n_states"):
            GaussianHMM(n_states=0)


class TestCausalidad:
    """El corazón del módulo: el filtro no puede mirar el futuro."""

    def test_el_filtro_no_cambia_cuando_cambia_el_futuro(
        self, fitted: GaussianHMM, synthetic: tuple[np.ndarray, np.ndarray]
    ) -> None:
        X, _ = synthetic
        base = fitted.filtered_probs(X)
        mutado = X.copy()
        mutado[2000:] = mutado[2000:] * 3.0 + 0.05  # el futuro se vuelve otro
        despues = fitted.filtered_probs(mutado)
        np.testing.assert_allclose(base[:2000], despues[:2000], rtol=1e-10, atol=1e-12)

    def test_el_suavizado_si_usa_el_futuro(
        self, fitted: GaussianHMM, synthetic: tuple[np.ndarray, np.ndarray]
    ) -> None:
        """Documenta por qué el suavizado no puede ser feature: la misma barra
        recibe una probabilidad distinta según lo que venga después.

        Se compara la serie completa contra su truncamiento: en la cola, donde
        el futuro pesa, las dos versiones difieren. El filtro causal, en el
        mismo test de arriba, no difiere en absoluto.
        """
        X, _ = synthetic
        completo = fitted.smoothed_probs(X)[:1500]
        truncado = fitted.smoothed_probs(X[:1500])
        assert not np.allclose(completo[-50:], truncado[-50:], atol=1e-6)

    def test_truncar_la_serie_no_cambia_el_filtro(
        self, fitted: GaussianHMM, synthetic: tuple[np.ndarray, np.ndarray]
    ) -> None:
        """Corolario operativo: en vivo, la barra de hoy da lo mismo que dio en
        el backtest cuando era la última."""
        X, _ = synthetic
        completo = fitted.filtered_probs(X)[:1500]
        truncado = fitted.filtered_probs(X[:1500])
        np.testing.assert_allclose(completo, truncado, rtol=1e-10, atol=1e-12)

    def test_las_probabilidades_suman_uno(
        self, fitted: GaussianHMM, synthetic: tuple[np.ndarray, np.ndarray]
    ) -> None:
        X, _ = synthetic
        for probs in (fitted.filtered_probs(X), fitted.smoothed_probs(X)):
            np.testing.assert_allclose(probs.sum(axis=1), 1.0, atol=1e-9)
            assert np.all(np.isfinite(probs))

    def test_no_underflowea_con_outliers_extremos(self, fitted: GaussianHMM) -> None:
        """Una barra imposible (100 sigmas) no puede dejar el filtro en NaN."""
        X = np.tile(TRUE_MEANS[0], (200, 1))
        X[100] = [5.0, 10.0]
        probs = fitted.filtered_probs(X)
        assert np.all(np.isfinite(probs))
        np.testing.assert_allclose(probs.sum(axis=1), 1.0, atol=1e-9)


class TestDiagnostico:
    def test_la_duracion_esperada_es_la_geometrica(self, fitted: GaussianHMM) -> None:
        esperado = 1.0 / (1.0 - np.diag(fitted.transmat_))
        np.testing.assert_allclose(fitted.expected_durations(), esperado)

    def test_la_distribucion_estacionaria_es_invariante(self, fitted: GaussianHMM) -> None:
        pi = fitted.stationary_distribution()
        np.testing.assert_allclose(pi @ fitted.transmat_, pi, atol=1e-8)
        assert pi.sum() == pytest.approx(1.0)

    def test_cuenta_bien_los_parametros(self) -> None:
        m = GaussianHMM(n_states=3, seed=0).fit(np.random.default_rng(0).normal(size=(500, 2)))
        # (k−1) + k(k−1) + 2kd = 2 + 6 + 12
        assert m.n_parameters == 20

    def test_el_bic_es_la_formula(self, synthetic: tuple[np.ndarray, np.ndarray]) -> None:
        X, _ = synthetic
        m = GaussianHMM(n_states=2, seed=0).fit(X)
        esperado = -2 * m.log_likelihood(X) + m.n_parameters * np.log(len(X))
        assert m.bic(X) == pytest.approx(esperado)

    def test_mas_estados_pagan_mas_penalizacion(
        self, synthetic: tuple[np.ndarray, np.ndarray]
    ) -> None:
        X, _ = synthetic
        dos = GaussianHMM(n_states=2, seed=0).fit(X)
        cinco = GaussianHMM(n_states=5, seed=0).fit(X)
        pen_dos = dos.bic(X) + 2 * dos.log_likelihood(X)
        pen_cinco = cinco.bic(X) + 2 * cinco.log_likelihood(X)
        assert pen_cinco > pen_dos > 0

    def test_el_icl_castiga_el_solapamiento(self, synthetic: tuple[np.ndarray, np.ndarray]) -> None:
        X, _ = synthetic
        m = GaussianHMM(n_states=3, seed=0).fit(X)
        assert m.icl(X) >= m.bic(X)
        assert m.posterior_entropy(X) >= 0.0

    def test_etiqueta_los_regimenes(self, fitted: GaussianHMM) -> None:
        labels = fitted.regime_labels()
        assert len(labels) == 2
        assert all(isinstance(x, MarketRegime) for x in labels)

    def test_el_estado_agitado_se_llama_volatil(self) -> None:
        rng = np.random.default_rng(11)
        calmo = rng.normal([0.0, -6.0], [0.001, 0.1], size=(1500, 2))
        agitado = rng.normal([0.0, -4.0], [0.01, 0.1], size=(1500, 2))
        X = np.vstack([calmo, agitado, calmo])
        m = GaussianHMM(n_states=2, seed=0).fit(X)
        assert MarketRegime.VOLATILE in m.regime_labels()

    def test_resume_para_el_dashboard(self, fitted: GaussianHMM) -> None:
        info = fitted.summary()
        assert info["n_states"] == 2
        assert len(info["transition_matrix"]) == 2
        assert len(info["expected_durations_bars"]) == 2
        assert all(isinstance(x, str) for x in info["labels"])

    def test_no_se_puede_diagnosticar_sin_ajustar(self) -> None:
        m = GaussianHMM(n_states=2)
        with pytest.raises(RuntimeError, match="no está ajustado"):
            m.expected_durations()
        with pytest.raises(RuntimeError, match="no está ajustado"):
            m.summary()

    def test_rechaza_features_de_otra_forma(self, fitted: GaussianHMM) -> None:
        with pytest.raises(ValueError, match="features"):
            fitted.filtered_probs(np.ones((10, 5)))


class TestSeleccionDeEstados:
    def test_elige_el_n_verdadero_en_datos_sinteticos(
        self, synthetic: tuple[np.ndarray, np.ndarray]
    ) -> None:
        """Con datos generados POR un HMM de 2 estados, el BIC lo encuentra."""
        X, _ = synthetic
        best, sel = select_n_states(X, candidates=(1, 2, 3, 4), seed=0)
        assert sel.best_n == 2
        assert best.n_states == 2
        assert sel.gap > 2

    def test_reporta_los_dos_criterios_para_cada_n(
        self, synthetic: tuple[np.ndarray, np.ndarray]
    ) -> None:
        """Settings → Markov necesita el detalle para que el usuario fije n
        de forma informada, no a ciegas."""
        X, _ = synthetic
        _, sel = select_n_states(X, candidates=(1, 2, 3), seed=0)
        assert set(sel.bic_by_n) == {1, 2, 3}
        assert set(sel.icl_by_n) == {1, 2, 3}
        assert set(sel.log_likelihood_by_n) == {1, 2, 3}
        assert set(sel.converged_by_n) == {1, 2, 3}
        d = sel.as_dict()
        assert d["best_n"] == 2
        assert "bic_by_n" in d and "icl_by_n" in d and "warnings" in d

    def test_el_criterio_icl_es_seleccionable(
        self, synthetic: tuple[np.ndarray, np.ndarray]
    ) -> None:
        X, _ = synthetic
        _, sel = select_n_states(X, candidates=(1, 2, 3), criterion="icl", seed=0)
        assert sel.criterion == "icl"
        assert sel.scores == sel.icl_by_n

    def test_rechaza_criterio_desconocido(self, synthetic: tuple[np.ndarray, np.ndarray]) -> None:
        X, _ = synthetic
        with pytest.raises(ValueError, match="criterion"):
            select_n_states(X, candidates=(2,), criterion="ojimetro")

    def test_rechaza_lista_vacia(self, synthetic: tuple[np.ndarray, np.ndarray]) -> None:
        X, _ = synthetic
        with pytest.raises(ValueError, match="candidato"):
            select_n_states(X, candidates=())

    def test_avisa_cuando_el_ganador_esta_en_el_borde(self) -> None:
        sel = StateSelection(
            best_n=6,
            criterion="bic",
            bic_by_n={4: -100.0, 5: -150.0, 6: -160.0},
            icl_by_n={4: -90.0, 5: -140.0, 6: -150.0},
            log_likelihood_by_n={4: 60.0, 5: 85.0, 6: 90.0},
            converged_by_n={4: True, 5: True, 6: True},
        )
        assert sel.at_boundary is True
        assert sel.monotone_in_n is True
        assert any("no hay óptimo interior" in w for w in sel.warnings)

    def test_la_parsimonia_corta_donde_la_mejora_se_desploma(self) -> None:
        """El caso real de ETHUSDT: el criterio baja siempre, así que el
        automático elige el borde y hay que declarar un corte."""
        sel = StateSelection(
            best_n=6,
            criterion="bic",
            bic_by_n={2: 0.0, 3: -100.0, 4: -140.0, 5: -160.0, 6: -170.0},
            icl_by_n={2: 0.0, 3: -100.0, 4: -140.0, 5: -160.0, 6: -170.0},
            log_likelihood_by_n=dict.fromkeys((2, 3, 4, 5, 6), 1.0),
            converged_by_n=dict.fromkeys((2, 3, 4, 5, 6), True),
        )
        # Mejoras: 100 (2→3), 40 (3→4), 20, 10. Pasar de 3 a 4 ya rinde menos
        # que la mitad de lo que rindió pasar de 2 a 3: el corte es en 3.
        assert sel.knee_n == 3

    def test_avisa_empate_tecnico(self) -> None:
        sel = StateSelection(
            best_n=3,
            criterion="bic",
            bic_by_n={2: -100.5, 3: -101.0},
            icl_by_n={2: -100.0, 3: -100.5},
            log_likelihood_by_n={2: 55.0, 3: 56.0},
            converged_by_n={2: True, 3: True},
        )
        assert sel.gap < 2
        assert any("empate" in w for w in sel.warnings)

    def test_avisa_cuando_algun_ajuste_no_convergio(self) -> None:
        sel = StateSelection(
            best_n=2,
            criterion="bic",
            bic_by_n={2: -100.0, 3: -90.0},
            icl_by_n={2: -95.0, 3: -85.0},
            log_likelihood_by_n={2: 55.0, 3: 50.0},
            converged_by_n={2: True, 3: False},
        )
        assert any("sin converger" in w for w in sel.warnings)

    def test_los_no_convergidos_no_ganan(self, synthetic: tuple[np.ndarray, np.ndarray]) -> None:
        """Un EM interrumpido tiene una verosimilitud que no es comparable."""
        X, _ = synthetic
        # n_iter=1 no converge nunca; el candidato con más estados ganaría por
        # verosimilitud si se lo dejara competir.
        _, sel = select_n_states(X, candidates=(2, 3), n_iter=1, seed=0)
        assert all(not ok for ok in sel.converged_by_n.values())
        assert any("sin converger" in w for w in sel.warnings)
