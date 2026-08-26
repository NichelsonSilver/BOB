"""Tests de la capa de I/O: descarga y persistencia de runs.

Ni la red ni la base real se tocan: el cliente REST y la sesión de DB se
sustituyen por dobles, así que estos tests no dependen de que Binance esté
arriba ni escriben en `backend/bob.db`.
"""

from __future__ import annotations

import json
import sys

import numpy as np
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from bob.backtest import runner
from bob.data import download
from bob.data.binance_rest import INTERVAL_MS, Kline
from bob.data.store import upsert_klines
from bob.db.models import BacktestRun
from bob.models.experiment import ExperimentConfig, run_experiment
from bob.models.labeling import BarrierConfig
from tests.test_experiment import _serie

TF = "15m"
STEP = INTERVAL_MS[TF]


def _row(open_time: int, close: float = 100.0) -> list:
    return [
        open_time,
        f"{close:.2f}",
        f"{close + 1:.2f}",
        f"{close - 1:.2f}",
        f"{close:.2f}",
        "10.5",
        open_time + STEP - 1,
        "1050.0",
        42,
        "6.0",
        "600.0",
        "0",
    ]


@pytest.fixture
def db(monkeypatch):
    """Base en memoria enchufada al runner, para no escribir en backend/bob.db.

    `persist_run` usa la sesión como context manager, así que el doble debe
    sobrevivir al `__exit__`: por eso se neutraliza el cierre.
    """
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    session = Session(engine)
    monkeypatch.setattr(session, "close", lambda: None)
    monkeypatch.setattr(runner, "get_session", lambda: session)
    monkeypatch.setattr(runner, "init_db", lambda: None)
    yield session, engine


@pytest.fixture(scope="module")
def resultado():
    cfg = ExperimentConfig(
        barrier=BarrierConfig(tp_mult=0.5, sl_mult=0.5, horizon_bars=8, vol_window_bars=48),
        directions=("long", "short"),
        n_splits=2,
        model_kind="logistic",
        vol_kind="ridge",
        conformal_alphas=(0.20,),
    )
    return run_experiment(_serie(), cfg)


class TestPersistRun:
    def test_guarda_el_run(self, db, resultado) -> None:
        session, _ = db
        run_id = runner.persist_run(resultado)
        guardado = session.exec(select(BacktestRun).where(BacktestRun.run_id == run_id)).one()
        assert guardado.symbol == "TESTUSDT"
        assert guardado.status == "done"
        assert guardado.finished_at is not None

    def test_registra_la_config_completa(self, db, resultado) -> None:
        session, _ = db
        run_id = runner.persist_run(resultado)
        guardado = session.exec(select(BacktestRun).where(BacktestRun.run_id == run_id)).one()
        cfg = json.loads(guardado.config_json)
        assert cfg["barrier"]["horizon_bars"] == 8
        assert cfg["model_version"]

    def test_reporta_la_peor_direccion_no_el_promedio(self, db, resultado) -> None:
        """Promediar escondería que una de las dos está descalibrada."""
        session, _ = db
        run_id = runner.persist_run(resultado)
        guardado = session.exec(select(BacktestRun).where(BacktestRun.run_id == run_id)).one()
        peor = max(
            d.metrics_model.mean_calibration_error_pp for d in resultado.directions.values()
        )
        assert float(guardado.calibration_error_pp) == pytest.approx(peor)

    def test_guarda_los_buckets_de_ambas_direcciones(self, db, resultado) -> None:
        session, _ = db
        run_id = runner.persist_run(resultado)
        guardado = session.exec(select(BacktestRun).where(BacktestRun.run_id == run_id)).one()
        buckets = json.loads(guardado.buckets_json)
        assert set(buckets) == {"long", "short"}


class TestRunId:
    """El nombre del artefacto es cómo se encuentra una cifra citada."""

    def test_lleva_simbolo_timeframe_variante_y_modelo(self, resultado) -> None:
        from bob.models.experiment import feature_set_name

        variante = feature_set_name(resultado.config)
        run_id = runner.build_run_id(resultado)
        assert run_id.startswith(
            f"TESTUSDT-15m-{variante}-{resultado.config.vol_kind}-"
        )

    def test_distingue_dos_runs_que_solo_cambian_el_estimador(self, resultado) -> None:
        """Sin el estimador en el nombre, los dos archivos son indistinguibles."""
        import dataclasses

        con_xgb = dataclasses.replace(
            resultado, config=dataclasses.replace(resultado.config, vol_kind="xgb")
        )
        assert runner.build_run_id(resultado) != runner.build_run_id(con_xgb)
        assert f"-{resultado.config.vol_kind}-" in runner.build_run_id(resultado)
        assert "-xgb-" in runner.build_run_id(con_xgb)


class TestArtifacts:
    def test_escribe_reporte_y_json(self, tmp_path, monkeypatch, resultado) -> None:
        monkeypatch.setattr(runner, "ARTIFACTS_DIR", tmp_path / "artifacts")
        report_path, json_path = runner.write_artifacts(resultado, "run-de-prueba")
        assert report_path.exists() and json_path.exists()
        assert "TARGET 1" in report_path.read_text(encoding="utf-8")
        assert json.loads(json_path.read_text(encoding="utf-8"))["symbol"] == "TESTUSDT"

    def test_crea_el_directorio_si_no_existe(self, tmp_path, monkeypatch, resultado) -> None:
        destino = tmp_path / "no" / "existe"
        monkeypatch.setattr(runner, "ARTIFACTS_DIR", destino)
        runner.write_artifacts(resultado, "run-2")
        assert destino.is_dir()



def _sin_familias_2b(monkeypatch):
    """Stub de los loaders de Fase 2b para los tests del CLI.

    Sin esto el runner leeria la DB real del usuario —210k puntos de derivados—
    y el test dejaria de ser determinista y de correr en aislamiento.
    """
    monkeypatch.setattr(runner, "load_derivatives", lambda *a, **k: None)
    monkeypatch.setattr(runner, "load_book_depth", lambda *a, **k: None)


class TestCLI:
    def test_los_defaults_del_cli_son_los_del_modelo(
        self, tmp_path, monkeypatch, resultado
    ) -> None:
        """Sin argumentos, el CLI debe correr exactamente la config documentada.

        Regresión: los defaults del argparse estaban escritos a mano y quedaron
        desfasados de `BarrierConfig` al reelegir las barreras por barrido. El
        experimento corría con otra configuración que la documentada y nada
        fallaba — solo cambiaban los números.
        """
        from bob.data.store import series_from_klines

        serie = series_from_klines("TESTUSDT", TF, [Kline.from_row(_row(0))])
        capturado: dict[str, object] = {}

        def run_falso(s, cfg, *extras):
            capturado["cfg"] = cfg
            return resultado

        monkeypatch.setattr(sys, "argv", ["runner"])
        _sin_familias_2b(monkeypatch)
        monkeypatch.setattr(runner, "load_series", lambda s, t: serie)
        monkeypatch.setattr(runner, "run_experiment", run_falso)
        monkeypatch.setattr(runner, "ARTIFACTS_DIR", tmp_path / "artifacts")
        monkeypatch.setattr(runner, "persist_run", lambda r: "dry")
        runner.main()

        cfg = capturado["cfg"]
        esperado = BarrierConfig()
        assert cfg.barrier.tp_mult == esperado.tp_mult
        assert cfg.barrier.sl_mult == esperado.sl_mult
        assert cfg.barrier.horizon_bars == esperado.horizon_bars
        assert cfg.n_splits == ExperimentConfig().n_splits
        assert cfg.signal_threshold == ExperimentConfig().signal_threshold

    def test_falla_con_mensaje_util_si_no_hay_velas(self, monkeypatch) -> None:
        """El error debe decir qué comando correr, no solo 'no hay datos'."""
        from bob.data.store import series_from_klines

        monkeypatch.setattr(sys, "argv", ["runner", "--symbol", "NADAUSDT"])
        _sin_familias_2b(monkeypatch)
        monkeypatch.setattr(
            runner, "load_series", lambda s, t: series_from_klines(s, t, [])
        )
        with pytest.raises(SystemExit, match="bob.data.download"):
            runner.main()

    def test_flujo_completo_sin_persistir(self, tmp_path, monkeypatch, capsys, resultado) -> None:
        from bob.data.store import series_from_klines

        serie = series_from_klines(
            "TESTUSDT", TF, [Kline.from_row(_row(i * STEP)) for i in range(5)]
        )
        capturado: dict[str, object] = {}

        def run_falso(s, cfg, *extras):
            capturado["cfg"] = cfg
            return resultado

        monkeypatch.setattr(sys, "argv", ["runner", "--tp", "1.5", "--horizon", "24", "--rolling"])
        _sin_familias_2b(monkeypatch)
        monkeypatch.setattr(runner, "load_series", lambda s, t: serie)
        monkeypatch.setattr(runner, "run_experiment", run_falso)
        monkeypatch.setattr(runner, "ARTIFACTS_DIR", tmp_path / "artifacts")
        runner.main()

        cfg = capturado["cfg"]
        assert cfg.barrier.tp_mult == 1.5
        assert cfg.barrier.horizon_bars == 24
        assert cfg.expanding is False  # --rolling
        salida = capsys.readouterr().out
        assert "TARGET 1" in salida
        assert "reporte" in salida

    def test_persiste_cuando_se_le_pide(self, tmp_path, monkeypatch, db, resultado) -> None:
        from bob.data.store import series_from_klines

        session, _ = db
        serie = series_from_klines("TESTUSDT", TF, [Kline.from_row(_row(0))])
        monkeypatch.setattr(sys, "argv", ["runner"])
        _sin_familias_2b(monkeypatch)
        monkeypatch.setattr(runner, "load_series", lambda s, t: serie)
        monkeypatch.setattr(runner, "run_experiment", lambda s, cfg, *extras: resultado)
        monkeypatch.setattr(runner, "ARTIFACTS_DIR", tmp_path / "artifacts")
        runner.main()
        assert session.exec(select(BacktestRun)).all()


class TestDownload:
    def test_formatea_timestamps(self) -> None:
        assert download._fmt_ms(0) == "—"
        assert "2023" in download._fmt_ms(1_700_000_000_000)

    async def test_descarga_y_persiste(self, monkeypatch) -> None:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
        SQLModel.metadata.create_all(engine)
        session = Session(engine)

        klines = [Kline.from_row(_row(i * STEP)) for i in range(50)]

        class ClienteFalso:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def fetch_klines(self, *args, **kwargs):
                return klines

        escrito: dict[str, int] = {}

        def upsert_espia(symbol, timeframe, ks, sess=None):
            escrito["n"] = upsert_klines(symbol, timeframe, ks, session)
            return escrito["n"]

        monkeypatch.setattr(download, "init_db", lambda: None)
        monkeypatch.setattr(download, "BinanceRestClient", ClienteFalso)
        monkeypatch.setattr(
            download,
            "coverage",
            lambda s, t: {"n_candles": 0, "first_open_time": 0, "last_open_time": 0},
        )
        monkeypatch.setattr(download, "upsert_klines", upsert_espia)

        n = await download.download_history("ETHUSDT", TF, months=1)
        assert n == 50
        session.close()

    async def test_reanuda_desde_lo_ya_descargado(self, monkeypatch) -> None:
        """Reejecutar no vuelve a bajar lo que ya está en DB."""
        import time

        ahora = int(time.time() * 1000)
        llamadas: dict[str, int] = {}

        class ClienteFalso:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def fetch_klines(self, symbol, tf, start, **kwargs):
                llamadas["start"] = start
                return []

        monkeypatch.setattr(download, "init_db", lambda: None)
        monkeypatch.setattr(download, "BinanceRestClient", ClienteFalso)
        monkeypatch.setattr(
            download,
            "coverage",
            lambda s, t: {
                "n_candles": 1000,
                "first_open_time": ahora - 1000 * STEP,
                "last_open_time": ahora - 10 * STEP,
            },
        )
        await download.download_history("ETHUSDT", TF, months=1)
        assert llamadas["start"] == ahora - 10 * STEP + STEP

    async def test_nada_nuevo_que_descargar(self, monkeypatch) -> None:
        import time

        ahora = int(time.time() * 1000)
        monkeypatch.setattr(download, "init_db", lambda: None)
        monkeypatch.setattr(
            download,
            "coverage",
            lambda s, t: {
                "n_candles": 100,
                "first_open_time": ahora - 100 * STEP,
                "last_open_time": ahora + STEP,
            },
        )
        assert await download.download_history("ETHUSDT", TF, months=1) == 0

    async def test_respuesta_vacia_de_binance(self, monkeypatch) -> None:
        class ClienteFalso:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def fetch_klines(self, *args, **kwargs):
                return []

        monkeypatch.setattr(download, "init_db", lambda: None)
        monkeypatch.setattr(download, "BinanceRestClient", ClienteFalso)
        monkeypatch.setattr(
            download,
            "coverage",
            lambda s, t: {"n_candles": 0, "first_open_time": 0, "last_open_time": 0},
        )
        assert await download.download_history("ETHUSDT", TF, months=1) == 0

    def test_print_status_reporta_huecos(self, monkeypatch, capsys) -> None:
        from bob.data.store import series_from_klines

        indices = [0, 1, 2, 9, 10]
        serie = series_from_klines("ETHUSDT", TF, [Kline.from_row(_row(i * STEP)) for i in indices])
        monkeypatch.setattr(
            download,
            "coverage",
            lambda s, t: {
                "n_candles": 5,
                "first_open_time": 0,
                "last_open_time": 10 * STEP,
            },
        )
        monkeypatch.setattr(download, "load_series", lambda s, t: serie)
        download.print_status("ETHUSDT", TF)
        salida = capsys.readouterr().out
        assert "huecos" in salida
        assert "completitud" in salida

    def test_print_status_sin_datos(self, monkeypatch, capsys) -> None:
        monkeypatch.setattr(
            download,
            "coverage",
            lambda s, t: {"n_candles": 0, "first_open_time": 0, "last_open_time": 0},
        )
        download.print_status("NADA", TF)
        assert "0" in capsys.readouterr().out


def test_serie_de_prueba_es_coherente() -> None:
    """Sanity del helper compartido: OHLC bien ordenado."""
    s = _serie(n=500)
    assert np.all(s.high >= s.low)
    assert np.all(s.high >= s.close) and np.all(s.low <= s.close)
