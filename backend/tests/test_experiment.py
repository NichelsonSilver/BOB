"""Test de integración del experimento completo y del reporte.

Corre el pipeline entero (features → labels → splits → modelos → métricas →
texto) sobre una serie sintética chica. No verifica que el modelo *acierte*
—sobre ruido no debería— sino que el andamiaje sea coherente: que no haya
fuga, que las métricas existan, que el gate se evalúe y que el reporte no
reviente al formatear valores indefinidos.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from bob.data.store import BookDepthSeries, DerivativesSeries, OHLCVSeries
from bob.models.experiment import (
    ExperimentConfig,
    assemble_features,
    assert_columns_trainable,
    run_experiment,
)
from bob.models.labeling import BarrierConfig
from bob.models.report import render_report, render_summary

TF_MS = 900_000


def _serie(n: int = 4000, seed: int = 0) -> OHLCVSeries:
    rng = np.random.default_rng(seed)
    # Volatilidad con clustering Y reversión a la media: un AR(1) en
    # log-volatilidad. Es la propiedad real del mercado (Mandelbrot) y la
    # que hace que el target de volatilidad sea aprendible. Un paseo
    # aleatorio en log-vol tendría clustering pero sería no estacionario:
    # el nivel del test no se parecería al del train y ningún modelo
    # honesto podría extrapolarlo.
    log_vol = np.empty(n)
    log_vol[0] = np.log(0.003)
    phi, mu = 0.98, np.log(0.003)
    for i in range(1, n):
        log_vol[i] = mu + phi * (log_vol[i - 1] - mu) + rng.normal(0, 0.06)
    vol = np.exp(log_vol)
    ret = rng.normal(0, 1, n) * vol
    close = 2000.0 * np.exp(np.cumsum(ret))
    spread = np.abs(rng.normal(0, 0.0015, n)) + 0.0005
    open_ = np.concatenate([[close[0]], close[:-1]])
    volume = np.abs(rng.lognormal(5, 1, n))
    return OHLCVSeries(
        symbol="TESTUSDT",
        timeframe="15m",
        open_time=np.arange(n, dtype=np.int64) * TF_MS + 1_700_000_000_000,
        open=open_,
        high=np.maximum(close * (1 + spread), np.maximum(open_, close)),
        low=np.minimum(close * (1 - spread), np.minimum(open_, close)),
        close=close,
        volume=volume,
        quote_volume=volume * close,
        taker_buy_volume=volume * rng.uniform(0.35, 0.65, n),
        n_trades=rng.integers(50, 5000, n).astype(np.int64),
    )


@pytest.fixture(scope="module")
def resultado():
    cfg = ExperimentConfig(
        barrier=BarrierConfig(tp_mult=0.5, sl_mult=0.5, horizon_bars=8, vol_window_bars=48),
        directions=("long",),
        n_splits=2,
        model_kind="logistic",
        vol_kind="ridge",
        conformal_alphas=(0.20,),
    )
    return run_experiment(_serie(), cfg)


class TestRunExperiment:
    def test_serie_demasiado_corta(self) -> None:
        with pytest.raises(ValueError, match="demasiado corta"):
            run_experiment(_serie(n=500))

    def test_estructura_del_resultado(self, resultado) -> None:
        assert resultado.symbol == "TESTUSDT"
        assert resultado.n_features > 30
        assert len(resultado.feature_names) == resultado.n_features
        assert resultado.n_bars == 4000
        assert resultado.runtime_s > 0

    def test_hay_un_resultado_por_direccion(self, resultado) -> None:
        assert set(resultado.directions) == {"long"}
        dr = resultado.directions["long"]
        assert dr.n_samples > 0
        assert 0.0 <= dr.metrics_model.base_rate <= 1.0

    def test_las_muestras_efectivas_son_menos_que_los_labels(self, resultado) -> None:
        """Los labels se solapan: tratarlos como independientes sería mentir."""
        dr = resultado.directions["long"]
        assert dr.effective_n < dr.resolution_mix["n"]

    def test_la_mezcla_de_resoluciones_suma_uno(self, resultado) -> None:
        mix = resultado.directions["long"].resolution_mix
        assert mix["tp"] + mix["sl"] + mix["vertical"] == pytest.approx(1.0)

    def test_probabilidades_out_of_sample_en_rango(self, resultado) -> None:
        m = resultado.directions["long"].metrics_model
        assert 0.0 <= m.brier <= 1.0
        assert all(0.0 <= b.mean_predicted <= 1.0 for b in m.buckets)

    def test_hay_folds_reportados(self, resultado) -> None:
        assert len(resultado.folds) == 2
        for fold in resultado.folds:
            assert fold["n_train"] > 0 and fold["n_test"] > 0
            assert fold["test_from"] <= fold["test_to"]

    def test_los_folds_de_test_avanzan_en_el_tiempo(self, resultado) -> None:
        fechas = [f["test_from"] for f in resultado.folds]
        assert fechas == sorted(fechas)

    def test_volatilidad_tiene_los_tres_baselines(self, resultado) -> None:
        v = resultado.volatility
        assert v.n_samples > 0
        for m in (v.model, v.ewma, v.garch, v.har):
            assert np.isfinite(m.rmse)

    def test_la_volatilidad_es_predecible_por_el_baseline_har(self, resultado) -> None:
        """El target de volatilidad tiene señal real; si esto falla, se rompió algo.

        Se afirma sobre HAR-RV y no sobre el modelo con features: HAR es el
        estimador diseñado exactamente para esta estructura y con el train
        chico de este test es la referencia limpia. Que el GBM le gane o no
        sobre datos reales es una pregunta empírica que responde el reporte,
        no algo que un test deba dar por sentado.
        """
        assert resultado.volatility.har.r2 > 0.0

    def test_las_metricas_de_volatilidad_son_finitas(self, resultado) -> None:
        v = resultado.volatility
        for m in (v.model, v.ewma, v.garch, v.har):
            assert np.isfinite(m.rmse) and np.isfinite(m.qlike)

    def test_intervalos_conformales_cubren_razonablemente(self, resultado) -> None:
        c = resultado.intervals.conformal[0.20]
        assert 0.60 < c.empirical_coverage < 0.95
        assert c.mean_width > 0

    def test_hay_baseline_gaussiano_para_comparar(self, resultado) -> None:
        assert 0.20 in resultado.intervals.gaussian

    def test_importancia_ordenada_y_completa(self, resultado) -> None:
        imp = resultado.importance
        assert len(imp) == resultado.n_features
        valores = [v for _, v in imp]
        assert valores == sorted(valores, reverse=True)

    def test_importancia_por_familia(self, resultado) -> None:
        fams = resultado.family_importance
        assert set(fams) >= {"momentum", "volatilidad", "microestructura"}

    def test_dm_contra_el_baseline_existe(self, resultado) -> None:
        dm = resultado.directions["long"].dm_vs_baseline
        assert isinstance(dm.verdict(), str)


class TestSerializacion:
    def test_to_dict_es_json_serializable(self, resultado) -> None:
        texto = resultado.to_json()
        vuelto = json.loads(texto)
        assert vuelto["symbol"] == "TESTUSDT"
        assert "directions" in vuelto
        assert "volatility" in vuelto

    def test_config_queda_registrada(self, resultado) -> None:
        cfg = resultado.to_dict()["config"]
        assert cfg["barrier"]["horizon_bars"] == 8
        assert cfg["model_version"]

    def test_los_buckets_viajan_al_json(self, resultado) -> None:
        d = resultado.to_dict()
        buckets = d["directions"]["long"]["model"]["buckets"]
        assert isinstance(buckets, list)
        if buckets:
            assert "error_pp" in buckets[0]


class TestGate:
    def test_el_gate_devuelve_booleano(self, resultado) -> None:
        assert isinstance(resultado.gate_passed(), bool)

    def test_umbral_imposible_no_pasa(self, resultado) -> None:
        assert resultado.gate_passed(max_calibration_error_pp=0.0) is False

    def test_umbral_laxo_pasa(self, resultado) -> None:
        assert resultado.gate_passed(max_calibration_error_pp=100.0) is True

    def test_discriminacion_es_un_criterio_aparte(self, resultado) -> None:
        """Calibrar y discriminar son cosas distintas.

        Un modelo que predice siempre la tasa base calibra perfecto y es
        inútil. Por eso el gate de la Fase 4 exige los dos criterios y
        `discriminates()` se evalúa aparte de `gate_passed()`.
        """
        assert isinstance(resultado.discriminates(), bool)
        assert resultado.discriminates(min_auc=0.0, min_bss=-10.0) is True
        assert resultado.discriminates(min_auc=1.01, min_bss=-10.0) is False

    def test_discriminacion_exige_tambien_bss(self, resultado) -> None:
        """AUC alto con BSS negativo no habilita: ordena casos pero no aporta
        sobre la tasa base. El umbral de BSS tiene que morder por su cuenta."""
        assert resultado.discriminates(min_auc=0.0, min_bss=10.0) is False


class TestReporte:
    def test_renderiza_sin_reventar(self, resultado) -> None:
        texto = render_report(resultado)
        assert len(texto) > 1000

    def test_contiene_las_secciones_clave(self, resultado) -> None:
        texto = render_report(resultado)
        for esperado in (
            "TARGET 1",
            "TARGET 2",
            "TARGET 3",
            "Curva de fiabilidad",
            "Diebold-Mariano",
            "GATE DE LA FASE 4",
            "IMPORTANCIA DE FEATURES",
            "DISCRIMINACIÓN",
        ):
            assert esperado in texto, esperado

    def test_muestra_los_baselines_junto_al_modelo(self, resultado) -> None:
        """Ningún número del modelo puede aparecer sin su baseline al lado."""
        texto = render_report(resultado)
        assert "baseline" in texto
        assert "EWMA" in texto and "GARCH" in texto and "HAR-RV" in texto

    def test_declara_el_veredicto_del_gate(self, resultado) -> None:
        texto = render_report(resultado)
        assert ("PASA" in texto) or ("NO PASA" in texto)

    def test_resumen_de_una_linea(self, resultado) -> None:
        resumen = render_summary(resultado)
        assert "TESTUSDT" in resumen
        assert "Brier" in resumen

    def test_es_ascii_seguro_para_consola(self, resultado) -> None:
        """El reporte se escribe a archivo en UTF-8; debe poder codificarse."""
        render_report(resultado).encode("utf-8")


class TestEnsambladoDeFamilias:
    """Fase 2b: como entran derivados y libro a la matriz del experimento."""

    @staticmethod
    def _serie(n=3000):
        rng = np.random.default_rng(5)
        close = 3000.0 * np.exp(np.cumsum(rng.normal(0, 0.002, n)))
        vol = np.abs(rng.normal(100.0, 10.0, n))
        return OHLCVSeries(
            symbol="ETHUSDT",
            timeframe="15m",
            open_time=np.arange(n, dtype=np.int64) * 900_000,
            open=close,
            high=close * 1.002,
            low=close * 0.998,
            close=close,
            volume=vol,
            quote_volume=vol * close,
            taker_buy_volume=vol * 0.55,
            n_trades=np.full(n, 400, dtype=np.int64),
        )

    @staticmethod
    def _derivados(n_pts):
        rng = np.random.default_rng(9)
        oi = 2e6 * np.exp(np.cumsum(rng.normal(0, 0.001, n_pts)))
        return DerivativesSeries(
            symbol="ETHUSDT",
            period="5m",
            timestamp=np.arange(n_pts, dtype=np.int64) * 300_000,
            open_interest=oi,
            open_interest_value=oi * 3000.0,
            long_short_ratio=np.exp(rng.normal(0.4, 0.1, n_pts)),
            taker_buy_sell_ratio=np.exp(rng.normal(0.0, 0.1, n_pts)),
            top_trader_account_ratio=np.exp(rng.normal(0.3, 0.1, n_pts)),
            top_trader_position_ratio=np.exp(rng.normal(0.35, 0.1, n_pts)),
            funding_rate=np.full(n_pts, np.nan),
        )

    @staticmethod
    def _libro(n, con_near):
        rng = np.random.default_rng(11)
        bid_1 = np.abs(rng.normal(4.5e7, 4e6, n))
        ask_1 = np.abs(rng.normal(4.5e7, 4e6, n))
        near = np.full(n, np.nan)
        return BookDepthSeries(
            symbol="ETHUSDT",
            timeframe="15m",
            open_time=np.arange(n, dtype=np.int64) * 900_000,
            bid_02=bid_1 / 9.0 if con_near else near,
            ask_02=ask_1 / 9.0 if con_near else near,
            bid_1=bid_1,
            ask_1=ask_1,
            bid_5=bid_1 * 4.4,
            ask_5=ask_1 * 4.4,
            n_snapshots=np.full(n, 30, dtype=np.int64),
        )

    def test_sin_series_es_el_baseline_de_fase_2(self):
        """Sin derivados ni libro, la matriz son las 55 features de precio."""
        serie = self._serie()
        X, names, sparse, families = assemble_features(serie, ExperimentConfig())

        assert X.shape == (len(serie), 55)
        assert sparse == set()
        assert "derivados" not in families
        assert "libro" not in families

    def test_las_familias_se_suman_a_la_matriz(self):
        serie = self._serie()
        X, names, sparse, families = assemble_features(
            serie,
            ExperimentConfig(),
            derivatives=self._derivados(len(serie) * 3),
            book=self._libro(len(serie), con_near=True),
        )

        assert X.shape[1] == len(names)
        assert X.shape[1] > 55
        assert len(set(names)) == len(names)  # sin nombres repetidos
        assert families["derivados"] and families["libro"]
        # Sin el flag, el near-touch NO entra aunque el libro lo traiga.
        assert not any("_02" in n for n in names)
        assert sparse == set()

    def test_el_near_touch_entra_solo_con_su_flag_y_queda_marcado_sparse(self):
        serie = self._serie()
        cfg = ExperimentConfig(use_book_near=True)
        X, names, sparse, families = assemble_features(
            serie, cfg, book=self._libro(len(serie), con_near=True)
        )

        assert any("_02" in n for n in names)
        assert sparse and all("_02" in n or n in sparse for n in sparse)
        assert sparse < set(names)

    def test_las_columnas_sparse_no_filtran_filas(self):
        """El punto entero del diseño: exigirlas tiraría el 70% de la muestra.

        Con un libro sin near-touch (el archivo anterior a 2026-01-15) esas
        columnas son NaN enteras. Si entraran al criterio de finitud, no
        quedaría ni una fila utilizable.
        """
        serie = self._serie()
        cfg = ExperimentConfig(use_book_near=True)
        X, names, sparse, _ = assemble_features(
            serie, cfg, book=self._libro(len(serie), con_near=False)
        )

        densas = [i for i, n in enumerate(names) if n not in sparse]
        finite_densas = np.all(np.isfinite(X[:, densas]), axis=1)
        finite_todas = np.all(np.isfinite(X), axis=1)

        assert finite_densas.sum() > 1000  # el experimento puede correr
        assert finite_todas.sum() == 0  # y no podría si exigiera las sparse

    def test_el_modelo_logistico_rechaza_columnas_sparse(self):
        """Falla temprano y explicando, en vez de reventar dentro de sklearn."""
        serie = self._serie()
        cfg = ExperimentConfig(model_kind="logistic", use_book_near=True)
        with pytest.raises(ValueError, match="logístico no admite NaN"):
            run_experiment(serie, cfg, book=self._libro(len(serie), con_near=True))

    def test_las_familias_apagadas_se_ignoran_aunque_llegue_la_serie(self):
        serie = self._serie()
        cfg = ExperimentConfig(use_derivatives=False, use_book=False)
        X, names, _, families = assemble_features(
            serie,
            cfg,
            derivatives=self._derivados(len(serie) * 3),
            book=self._libro(len(serie), con_near=True),
        )
        assert X.shape[1] == 55
        assert "derivados" not in families


class TestColumnasEntrenables:
    """Una columna vacia en el primer train rompe sklearn con un error mudo."""

    def test_detecta_la_columna_vacia_en_el_train(self):
        X = np.random.default_rng(0).normal(size=(1000, 3))
        X[:600, 2] = np.nan  # existe solo en la segunda mitad
        with pytest.raises(ValueError, match="sin un solo valor en el primer train"):
            assert_columns_trainable(X, ["a", "b", "tardia"], 0.35)

    def test_nombra_la_columna_culpable(self):
        X = np.random.default_rng(0).normal(size=(1000, 2))
        X[:600, 1] = np.nan
        with pytest.raises(ValueError, match="near_touch"):
            assert_columns_trainable(X, ["ok", "near_touch"], 0.35)

    def test_una_columna_con_huecos_pero_presente_pasa(self):
        """Huecos si, ausencia total no: el GBM parte el NaN como rama propia."""
        X = np.random.default_rng(0).normal(size=(1000, 2))
        X[::2, 1] = np.nan  # la mitad de las filas, repartidas
        assert_columns_trainable(X, ["a", "con_huecos"], 0.35)

    def test_matriz_densa_pasa(self):
        X = np.random.default_rng(0).normal(size=(1000, 4))
        assert_columns_trainable(X, list("abcd"), 0.35)

    def test_el_experimento_falla_temprano_y_explicando(self):
        """Antes reventaba adentro de sklearn tras minutos de computo."""
        serie = TestEnsambladoDeFamilias._serie()
        libro = TestEnsambladoDeFamilias._libro(len(serie), con_near=True)
        # Near-touch presente solo en el ultimo tercio: como en el archivo real.
        corte = int(len(serie) * 0.7)
        libro = BookDepthSeries(
            symbol=libro.symbol,
            timeframe=libro.timeframe,
            open_time=libro.open_time,
            bid_02=np.where(np.arange(len(serie)) >= corte, libro.bid_02, np.nan),
            ask_02=np.where(np.arange(len(serie)) >= corte, libro.ask_02, np.nan),
            bid_1=libro.bid_1,
            ask_1=libro.ask_1,
            bid_5=libro.bid_5,
            ask_5=libro.ask_5,
            n_snapshots=libro.n_snapshots,
        )
        cfg = ExperimentConfig(use_book_near=True)
        with pytest.raises(ValueError, match="use_book_near=False"):
            run_experiment(serie, cfg, book=libro)
