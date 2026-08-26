"""Tests del ajuste de producción — el modelo que consulta el vivo.

Lo que se protege acá no es que el número sea bueno (eso lo decide el gate),
sino que sea el número que dice ser: sin lookahead, sobre una fila cuyos
features existen, y con la misma aritmética de ACI que midió el experimento.
"""

from __future__ import annotations

import numpy as np
import pytest

from bob.models.experiment import ExperimentConfig, assemble_features
from bob.models.labeling import BarrierConfig
from bob.models.production import (
    MIN_FIT_ROWS,
    BarForecast,
    OnlineConformalCone,
    assert_tail_observable,
    backward_sigma,
    build_analysis,
    fit_bundle,
)
from bob.models.projection import LeverageProfile

from .conftest import synthetic_series  # type: ignore[attr-defined]


@pytest.fixture(scope="module")
def fitted() -> tuple:
    """Un bundle real ajustado sobre serie sintética. Caro: se comparte."""
    series = synthetic_series(n=4000, seed=7)
    cfg = ExperimentConfig(
        barrier=BarrierConfig(horizon_bars=8, vol_window_bars=48),
        conformal_alphas=(0.20,),
        use_derivatives=False,
        use_book=False,
    )
    X, names, sparse, _ = assemble_features(series, cfg)
    bundle = fit_bundle(
        X,
        series.close,
        series.open,
        series.high,
        series.low,
        series.open_time,
        names,
        sparse,
        series.interval_ms,
        cfg,
    )
    return bundle, X, names, series, cfg


# --------------------------------------------------------------------- #
# Procedencia — de qué modelo salió cada pronóstico
# --------------------------------------------------------------------- #


def test_el_model_version_nombra_el_estimador_de_volatilidad(fitted: tuple) -> None:
    """`ForecastRecord` guarda este string y es lo único que identifica al modelo.

    Sin el estimador adentro, una muestra forward acumulada a ambos lados de
    un cambio de modelo queda mezclada, y la cobertura medida no describe a
    ninguno de los dos. El paper tracking de la Fase 5 se apoya en esto.
    """
    bundle, _, _, _, cfg = fitted
    assert bundle.model_version.endswith(f"+vol={cfg.vol_kind}")


def test_el_bundle_ajusta_y_predice_con_xgboost(fitted: tuple) -> None:
    """El camino de producción con XGBoost, no solo el del experimento.

    El vivo consulta `fit_bundle`, no `run_experiment`: que el estimador
    funcione en el gate no prueba que funcione en la ruta que emite.
    """
    _, X, names, series, cfg = fitted
    import dataclasses

    bundle = fit_bundle(
        X,
        series.close,
        series.open,
        series.high,
        series.low,
        series.open_time,
        names,
        set(),
        series.interval_ms,
        dataclasses.replace(cfg, vol_kind="xgb"),
    )
    assert bundle.model_version.endswith("+vol=xgb")

    fila = X[bundle.n_train - 1]
    assert bundle.row_is_usable(fila)
    sigma = bundle.volatility.predict(fila.reshape(1, -1))[0]
    assert np.isfinite(sigma) and sigma > 0


# --------------------------------------------------------------------- #
# Causalidad — la invariante que más caro cuesta romper
# --------------------------------------------------------------------- #


def test_el_ajuste_no_usa_etiquetas_incompletas(fitted: tuple) -> None:
    """Las últimas H barras no pueden entrar al train: su label no terminó.

    `fit_through_ms` debe quedar al menos H barras antes del final de la
    serie. Si quedara en la última barra, el modelo habría aprendido de una
    volatilidad futura que todavía no ocurrió.
    """
    bundle, _, _, series, cfg = fitted
    horizon = cfg.barrier.horizon_bars
    assert bundle.fit_through_ms <= int(series.open_time[-1 - horizon])


def test_mutar_el_futuro_no_mueve_el_forecast_del_pasado(fitted: tuple) -> None:
    """Invariante de no-lookahead, aplicada al camino en vivo completo.

    Se toma una barra, se pronostica, se destruyen todas las barras
    posteriores y se vuelve a pronosticar la misma. El número debe ser
    idéntico: el forecast de la barra i no puede depender de i+1.
    """
    bundle, X, names, series, cfg = fitted
    i = len(series) - 200

    sigma = backward_sigma(series.close, cfg)
    antes = bundle.predict_bar(
        X[i], open_time=int(series.open_time[i]), reference_price=float(series.close[i]),
        sigma_backward=float(sigma[i]),
    )

    truncada = series.slice(0, i + 1)
    X2, names2, _, _ = assemble_features(truncada, cfg)
    assert names2 == names
    sigma2 = backward_sigma(truncada.close, cfg)
    despues = bundle.predict_bar(
        X2[-1], open_time=int(truncada.open_time[-1]),
        reference_price=float(truncada.close[-1]),
        sigma_backward=float(sigma2[-1]),
    )

    assert despues.sigma_forecast == pytest.approx(antes.sigma_forecast, rel=1e-9)
    assert despues.sigma_backward == pytest.approx(antes.sigma_backward, rel=1e-9)
    for alpha in antes.cones:
        assert despues.cones[alpha] == pytest.approx(antes.cones[alpha], rel=1e-9)


# --------------------------------------------------------------------- #
# Composición de la proyección
# --------------------------------------------------------------------- #


def test_la_proyeccion_usa_la_sigma_que_declara(fitted: tuple) -> None:
    """`barrier_sigma_source` no es decorativo: cambia las barreras de verdad."""
    bundle, X, names, series, cfg = fitted
    sigma = backward_sigma(series.close, cfg)
    i = len(series) - 100
    fc = bundle.predict_bar(
        X[i], open_time=int(series.open_time[i]),
        reference_price=float(series.close[i]), sigma_backward=float(sigma[i]),
    )
    feats = dict(zip(names, X[i], strict=True))

    con_fc = build_analysis(
        bundle, fc, symbol="TEST", timeframe="15m", features=feats,
        barrier_sigma_source="forecast",
    )
    con_bw = build_analysis(
        bundle, fc, symbol="TEST", timeframe="15m", features=feats,
        barrier_sigma_source="backward",
    )

    assert con_fc.projections["long"].sigma_horizon == pytest.approx(fc.sigma_forecast)
    assert con_bw.projections["long"].sigma_horizon == pytest.approx(fc.sigma_backward)
    # Y el campo que avisa si la probabilidad describe el setup dibujado.
    assert con_fc.probability_matches_barriers is False
    assert con_bw.probability_matches_barriers is True


def test_el_kpi1_nunca_sale_marcado_como_calibrado(fitted: tuple) -> None:
    """Regla 2: el gate lo rechazó, así que el flag es False siempre."""
    bundle, X, names, series, cfg = fitted
    sigma = backward_sigma(series.close, cfg)
    fc = bundle.predict_bar(
        X[-1], open_time=int(series.open_time[-1]),
        reference_price=float(series.close[-1]), sigma_backward=float(sigma[-1]),
    )
    analysis = build_analysis(
        bundle, fc, symbol="TEST", timeframe="15m",
        features=dict(zip(names, X[-1], strict=True)),
    )
    assert analysis.probability_calibrated is False
    assert analysis.as_dict()["probability_calibrated"] is False


def test_sin_probabilidad_el_ev_sale_exactamente_cero(fitted: tuple) -> None:
    """Con el KPI 1 ausente se proyecta al equilibrio, no a un 0.5 inventado."""
    bundle, X, names, series, cfg = fitted
    fc = BarForecast(
        open_time=int(series.open_time[-1]),
        reference_price=float(series.close[-1]),
        sigma_forecast=0.01,
        sigma_backward=0.01,
        cones={0.20: (-0.01, 0.01)},
        probabilities={},  # ninguna dirección tiene probabilidad
    )
    analysis = build_analysis(
        bundle, fc, symbol="TEST", timeframe="15m",
        features=dict(zip(names, X[-1], strict=True)),
    )
    for proj in analysis.projections.values():
        assert proj.net_ev_pct == pytest.approx(0.0, abs=1e-12)


def test_el_leverage_no_mueve_los_niveles_pero_si_la_liquidacion(fitted: tuple) -> None:
    """Separación que le importa a un usuario que ya se liquidó una vez."""
    bundle, X, names, series, cfg = fitted
    sigma = backward_sigma(series.close, cfg)
    fc = bundle.predict_bar(
        X[-1], open_time=int(series.open_time[-1]),
        reference_price=float(series.close[-1]), sigma_backward=float(sigma[-1]),
    )
    feats = dict(zip(names, X[-1], strict=True))
    x1 = build_analysis(bundle, fc, symbol="T", timeframe="15m", features=feats,
                        profile=LeverageProfile(leverage=1.0))
    x10 = build_analysis(bundle, fc, symbol="T", timeframe="15m", features=feats,
                         profile=LeverageProfile(leverage=10.0))

    a, b = x1.projections["long"], x10.projections["long"]
    assert b.take_profit == pytest.approx(a.take_profit)
    assert b.stop_loss == pytest.approx(a.stop_loss)
    # La liquidación se acerca al precio y el ROE se amplifica.
    assert b.liq_distance_pct < a.liq_distance_pct
    assert b.roe_pct == pytest.approx(a.roe_pct * 10.0, rel=1e-9)


def test_sigma_no_utilizable_falla_en_vez_de_proyectar(fitted: tuple) -> None:
    bundle, X, names, series, _ = fitted
    fc = BarForecast(
        open_time=1, reference_price=100.0, sigma_forecast=float("nan"),
        sigma_backward=0.01, cones={}, probabilities={},
    )
    with pytest.raises(ValueError, match="sigma no utilizable"):
        build_analysis(bundle, fc, symbol="T", timeframe="15m",
                       features=dict(zip(names, X[-1], strict=True)))


def test_fuente_de_sigma_desconocida_es_error(fitted: tuple) -> None:
    bundle, X, names, series, cfg = fitted
    fc = BarForecast(
        open_time=1, reference_price=100.0, sigma_forecast=0.01,
        sigma_backward=0.01, cones={}, probabilities={},
    )
    with pytest.raises(ValueError, match="barrier_sigma_source"):
        build_analysis(bundle, fc, symbol="T", timeframe="15m",
                       features={}, barrier_sigma_source="inventada")


# --------------------------------------------------------------------- #
# Utilidad de la fila y observabilidad
# --------------------------------------------------------------------- #


def test_fila_con_hueco_denso_no_es_utilizable(fitted: tuple) -> None:
    bundle, X, _, _, _ = fitted
    fila = X[-1].copy()
    assert bundle.row_is_usable(fila)
    fila[int(bundle.dense_idx[0])] = np.nan
    assert not bundle.row_is_usable(fila)
    assert bundle.missing_dense(fila) == [bundle.feature_names[int(bundle.dense_idx[0])]]


def test_assert_tail_observable_nombra_la_columna_que_falta() -> None:
    """Una familia que dejó de llegar: 25% de cobertura en la cola."""
    X = np.ones((200, 3))
    X[-72:, 1] = np.nan
    with pytest.raises(ValueError, match="col_b"):
        assert_tail_observable(X, ["col_a", "col_b", "col_c"], set(), n_bars=96)


def test_assert_tail_observable_tolera_un_nan_aislado() -> None:
    """Distingue "esta familia no llega" de "este cociente se degeneró una vez".

    Medido sobre la cola real de ETHUSDT: `oi_per_px_24h` tiene 1 NaN de 96
    —el cociente se degenera cuando el precio no se movió en 24h— contra las 5
    columnas de top traders con 73 de 96. Exigir cobertura perfecta abortaba
    el arranque por lo primero. La barra concreta la sigue vigilando
    `row_is_usable` en el momento de emitir.
    """
    X = np.ones((200, 3))
    X[-5, 1] = np.nan  # 99% de cobertura
    assert_tail_observable(X, ["col_a", "col_b", "col_c"], set(), n_bars=96)


def test_assert_tail_observable_reporta_la_cobertura_medida() -> None:
    X = np.ones((200, 2))
    X[-96:, 1] = np.nan  # 0%
    with pytest.raises(ValueError, match=r"col_b \(0%\)"):
        assert_tail_observable(X, ["col_a", "col_b"], set(), n_bars=96)


def test_assert_tail_observable_ignora_las_columnas_ralas() -> None:
    """El near-touch puede faltar: es cobertura parcial declarada, no un hueco."""
    X = np.ones((200, 3))
    X[:, 1] = np.nan
    assert_tail_observable(X, ["col_a", "near_x", "col_c"], {"near_x"}, n_bars=96)


def test_assert_tail_observable_rechaza_matriz_vacia() -> None:
    with pytest.raises(ValueError, match="vacía"):
        assert_tail_observable(np.zeros((0, 3)), ["a", "b", "c"], set())


def test_historia_corta_no_ajusta() -> None:
    """Menos de MIN_FIT_ROWS filas: se falla, no se entrega un modelo débil."""
    series = synthetic_series(n=600, seed=3)
    cfg = ExperimentConfig(
        barrier=BarrierConfig(horizon_bars=8, vol_window_bars=48),
        conformal_alphas=(0.20,), use_derivatives=False, use_book=False,
    )
    X, names, sparse, _ = assemble_features(series, cfg)
    with pytest.raises(ValueError, match=f"{MIN_FIT_ROWS}"):
        fit_bundle(X, series.close, series.open, series.high, series.low,
                   series.open_time, names, sparse, series.interval_ms, cfg)


def test_matriz_desalineada_es_error() -> None:
    cfg = ExperimentConfig(use_derivatives=False, use_book=False)
    with pytest.raises(ValueError, match="mismo largo"):
        fit_bundle(np.zeros((10, 2)), np.zeros(5), np.zeros(5), np.zeros(5),
                   np.zeros(5), np.zeros(5, dtype=np.int64), ["a", "b"], set(),
                   900_000, cfg)


# --------------------------------------------------------------------- #
# ACI en línea
# --------------------------------------------------------------------- #


class _StubCQR:
    """Intervalo fijo [-1, 1] — aísla la aritmética de ACI de la del modelo."""

    gamma = 0.01

    def predict_interval(self, X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        n = X.shape[0]
        return np.full(n, -1.0), np.full(n, 1.0)


class _StubQuantile:
    """Regresor cuantílico constante, para inyectar dentro de un CQR real."""

    def __init__(self, value: float) -> None:
        self.value = value

    def predict(self, X: np.ndarray) -> np.ndarray:
        return np.full(X.shape[0], self.value)


def test_aci_ensancha_tras_un_fallo_y_estrecha_tras_aciertos() -> None:
    """El comportamiento que le da sentido: reacciona a la cobertura observada."""
    cone = OnlineConformalCone(model=_StubCQR(), alpha=0.20, gamma=0.05)  # type: ignore[arg-type]
    x = np.zeros(3)

    lo0, hi0 = cone.interval(x)
    assert (lo0, hi0) == (-1.0, 1.0)  # sin observaciones, el intervalo base

    # Un fallo (el retorno se salió) debe ENSANCHAR el siguiente intervalo.
    cone.observe(5.0, lo0, hi0)
    lo1, hi1 = cone.interval(x)
    assert hi1 - lo1 > hi0 - lo0

    # Aciertos sucesivos lo devuelven hacia el nominal.
    for _ in range(20):
        lo, hi = cone.interval(x)
        cone.observe(0.0, lo, hi)
    lo2, hi2 = cone.interval(x)
    assert hi2 - lo2 < hi1 - lo1


def test_aci_registra_la_cobertura_empirica() -> None:
    cone = OnlineConformalCone(model=_StubCQR(), alpha=0.20)  # type: ignore[arg-type]
    assert np.isnan(cone.empirical_coverage)
    assert cone.observe(0.0, -1.0, 1.0) is True
    assert cone.observe(9.0, -1.0, 1.0) is False
    assert cone.n_observed == 2
    assert cone.empirical_coverage == pytest.approx(0.5)


def test_aci_coincide_con_la_version_del_experimento() -> None:
    """Misma aritmética que `predict_interval_adaptive`, o el gate deja de describir el vivo.

    Se recorre la misma secuencia de verdades en línea y se exige que los
    intervalos coincidan uno a uno con los que produce el método que midió el
    experimento.
    """
    from bob.models.forecast import ConformalReturnInterval

    y = np.array([0.0, 5.0, 0.0, 0.0, 7.0, 0.0, 0.0, 0.0])
    X = np.zeros((y.size, 3))

    offline = ConformalReturnInterval(alpha=0.20, gamma=0.05, adaptive=True)
    offline._lo_model = _StubQuantile(-1.0)  # type: ignore[assignment]
    offline._hi_model = _StubQuantile(1.0)  # type: ignore[assignment]
    offline._correction = 0.0
    lo_ref, hi_ref = offline.predict_interval_adaptive(X, y)

    online = OnlineConformalCone(model=offline, alpha=0.20, gamma=0.05)
    for t in range(y.size):
        lo, hi = online.interval(X[t])
        assert lo == pytest.approx(lo_ref[t], rel=1e-12)
        assert hi == pytest.approx(hi_ref[t], rel=1e-12)
        online.observe(float(y[t]), lo, hi)
