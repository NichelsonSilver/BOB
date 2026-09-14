"""Tests de la capa REST (Fase 6).

El riesgo que cubren no es que un endpoint devuelva 500. Es más específico y
más caro: que el dashboard muestre un número correcto **de otra cosa** — el
veredicto del gate de un estimador que no es el que emite, una proyección
apalancada de una configuración distinta, un pronóstico de anteayer con cara
de recién. Todos esos responden 200.

`TestClient` se usa SIN `with`: el context manager corre el lifespan, que
levanta el feed de Binance y ajusta el modelo. Los tests hablan con las rutas,
no con el arranque.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from bob.api.routes import backtest as backtest_routes
from bob.db.models import CandleRecord, ForecastRecord
from bob.db.session import get_session
from bob.main import app
from bob.models.experiment import (
    FEATURE_SETS,
    ExperimentConfig,
    assemble_features,
    feature_family_index,
)

from .conftest import TF_MS, synthetic_series  # type: ignore[attr-defined]

SYMBOL = "TESTUSDT"
TIMEFRAME = "15m"
BASE_MS = 1_700_000_000_000


@pytest.fixture
def client(in_memory_engine: Any) -> TestClient:
    return TestClient(app)


def _candle(open_time: int, close: float) -> CandleRecord:
    return CandleRecord(
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        open_time=open_time,
        close_time=open_time + TF_MS - 1,
        open=str(close),
        high=str(close * 1.001),
        low=str(close * 0.999),
        close=str(close),
        volume="10",
    )


def _forecast(
    open_time: int,
    *,
    status: str = "open",
    model_version: str = "bob-forecast-0.1.0+vol=gbm",
    horizon_bars: int = 16,
    sigma: float = 0.01,
) -> ForecastRecord:
    return ForecastRecord(
        forecast_id=f"{SYMBOL}-{TIMEFRAME}-{open_time}",
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        open_time=open_time,
        reference_price="2000.0",
        sigma_forecast=sigma,
        sigma_backward=sigma * 1.1,
        barrier_sigma_source="forecast",
        horizon_bars=horizon_bars,
        cones_json=json.dumps(
            [
                {
                    "alpha": 0.05,
                    "nominal": 0.95,
                    "ret_lo": -0.02,
                    "ret_hi": 0.02,
                    "price_lo": 1960.0,
                    "price_hi": 2040.0,
                }
            ]
        ),
        projections_json="{}",
        probabilities_json=json.dumps({"long": 0.5, "short": 0.5}),
        features_json=json.dumps({"mom_1h": 0.1, "taker_ratio_log": -0.2}),
        model_version=model_version,
        status=status,
        cone_hits_json=json.dumps({"0.05": True}) if status == "resolved" else "{}",
        realized_vol=sigma * 1.05 if status == "resolved" else None,
        vol_ratio=1.05 if status == "resolved" else None,
        realized_return=0.001 if status == "resolved" else None,
        emitted_at=datetime.now(UTC),
    )


def _seed(records: list[Any]) -> None:
    with get_session() as session:
        for record in records:
            session.add(record)
        session.commit()


def _now_aligned() -> int:
    """`open_time` de la barra que acaba de cerrar, para probar frescura."""
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    return (now_ms // TF_MS) * TF_MS - TF_MS


# --------------------------------------------------------------------- #
# Velas: el hueco tiene que verse
# --------------------------------------------------------------------- #


def test_candles_reporta_el_hueco_en_vez_de_dibujar_continuidad(
    client: TestClient,
) -> None:
    """Un hueco sin marcar se lee como un movimiento de precio que no ocurrió.

    Es además el tramo donde el analista se calla —`_assert_tail_contiguous`
    se niega a emitir sobre una ventana que cuenta barras que no existieron—,
    así que el chart tiene que poder explicar el silencio.
    """
    _seed(
        [
            _candle(BASE_MS, 2000.0),
            _candle(BASE_MS + TF_MS, 2010.0),
            # faltan dos barras
            _candle(BASE_MS + 4 * TF_MS, 2100.0),
        ]
    )
    body = client.get(f"/api/market/candles?symbol={SYMBOL}&limit=10").json()

    assert body["n"] == 3
    assert body["gaps"] == [
        {
            "from_open_time": BASE_MS + TF_MS,
            "to_open_time": BASE_MS + 4 * TF_MS,
            "missing_bars": 2,
        }
    ]


def test_candles_valida_el_timeframe_contra_la_tabla_del_proyecto(
    client: TestClient,
) -> None:
    assert client.get("/api/market/candles?timeframe=7m").status_code == 422


# --------------------------------------------------------------------- #
# Frescura: un pronóstico viejo no puede parecer nuevo
# --------------------------------------------------------------------- #


def test_latest_marca_viejo_el_pronostico_atrasado(client: TestClient) -> None:
    """Los dos bugs de la Fase 5 se veían así: el número estaba bien y
    describía otro momento. El endpoint lo dice con `age_bars`/`is_stale` en
    vez de dejar que el frontend lo deduzca de un timestamp."""
    _seed([_forecast(BASE_MS)])  # de 2023: viejísimo
    body = client.get(f"/api/analysis/latest?symbol={SYMBOL}").json()

    assert body["forecast"]["is_stale"] is True
    assert body["forecast"]["age_bars"] > 1000
    # Sin lifespan no hay analista: el registro sigue siendo la fuente.
    assert body["analyst"] is None


def test_latest_no_marca_viejo_el_pronostico_de_la_barra_anterior(
    client: TestClient,
) -> None:
    _seed([_forecast(_now_aligned())])
    body = client.get(f"/api/analysis/latest?symbol={SYMBOL}").json()

    assert body["forecast"]["age_bars"] <= 2
    assert body["forecast"]["is_stale"] is False


def test_latest_sin_pronosticos_responde_200_con_null(client: TestClient) -> None:
    """Un símbolo sin historia no es un error: es un dashboard recién abierto."""
    body = client.get("/api/analysis/latest?symbol=NADAUSDT").json()
    assert body["forecast"] is None


# --------------------------------------------------------------------- #
# El slider de leverage
# --------------------------------------------------------------------- #


def test_el_leverage_mueve_la_liquidacion_y_no_mueve_el_mercado(
    client: TestClient,
) -> None:
    """Invariante del KPI 2: subir el leverage acerca la liquidación y agranda
    el ROE, pero TP, SL, EV neto y probabilidad son los mismos. Si alguno de
    esos cuatro se moviera, la página estaría diciendo que apalancarse cambia
    lo que va a hacer el precio."""
    _seed([_forecast(_now_aligned())])

    x1 = client.get(f"/api/analysis/projection?symbol={SYMBOL}&leverage=1").json()
    x10 = client.get(f"/api/analysis/projection?symbol={SYMBOL}&leverage=10").json()

    a, b = x1["projections"]["long"], x10["projections"]["long"]
    for campo in ("take_profit", "stop_loss", "tp_pct", "sl_pct", "net_ev_pct",
                  "probability", "breakeven_probability", "max_safe_leverage"):
        assert a[campo] == pytest.approx(b[campo]), campo

    assert b["liq_distance_pct"] < a["liq_distance_pct"]
    assert abs(b["roe_pct"]) > abs(a["roe_pct"])
    assert b["margin_pct"] == pytest.approx(0.1)


def test_la_proyeccion_se_niega_si_el_registro_es_de_otro_horizonte(
    client: TestClient,
) -> None:
    """Reproyectar un registro de otro setup daría números coherentes entre sí
    y ajenos al pronóstico que se está mostrando. Peor que no reproyectar."""
    _seed([_forecast(_now_aligned(), horizon_bars=99)])
    res = client.get(f"/api/analysis/projection?symbol={SYMBOL}&leverage=5")

    assert res.status_code == 409
    assert "horizonte" in res.json()["detail"]


def test_la_proyeccion_nunca_declara_calibrada_la_probabilidad(
    client: TestClient,
) -> None:
    """Regla 2: recalcular la proyección no recalibra nada. El KPI 1 sigue sin
    haber pasado discriminación, y el flag que lo dice no puede perderse por
    pasar por otro endpoint."""
    _seed([_forecast(_now_aligned())])
    body = client.get(f"/api/analysis/projection?symbol={SYMBOL}").json()
    assert body["probability_calibrated"] is False


# --------------------------------------------------------------------- #
# Familias de features
# --------------------------------------------------------------------- #


def test_el_indice_de_familias_no_confunde_los_dos_taker(client: TestClient) -> None:
    """`taker_buy_ratio` es de precio y `taker_ratio_log` es de derivados.
    Cualquier clasificación por prefijo los mete en la misma familia, y el
    desglose de la Analysis page atribuiría la lectura al bloque equivocado."""
    index = feature_family_index("15m", "price+deriv")

    assert index["taker_buy_ratio"] == "microestructura"
    assert index["taker_ratio_log"] == "derivados"


@pytest.mark.parametrize("feature_set", sorted(FEATURE_SETS))
def test_el_indice_de_familias_coincide_con_la_matriz_real(feature_set: str) -> None:
    """La guarda contra la deriva: el índice sale de una serie sintética, así
    que hay que probar que describe exactamente las columnas que produce
    `assemble_features` sobre datos de verdad. Si alguien agrega un bloque de
    features y olvida registrarlo en `families`, esto falla."""
    flags = FEATURE_SETS[feature_set]
    cfg = ExperimentConfig(
        use_derivatives=flags[0], use_book=flags[1], use_book_near=flags[2]
    )
    series = synthetic_series(n=1200, seed=3)
    index = feature_family_index("15m", feature_set)

    # Sin series de derivados ni de libro, `assemble_features` devuelve solo
    # las de precio: se compara ese subconjunto, que es el que puede existir
    # sin I/O. Las otras dos familias las cubre el test de arriba.
    _, names, _, families = assemble_features(series, cfg)
    esperado = {n: f for f, ms in families.items() for n in ms}

    assert set(names) <= set(index)
    for name in names:
        assert index[name] == esperado[name], name


def test_features_agrupa_y_cuenta_los_faltantes(client: TestClient) -> None:
    _seed([_forecast(_now_aligned())])
    body = client.get(f"/api/analysis/features?symbol={SYMBOL}").json()

    assert body["n_features"] == 2
    assert body["n_missing"] == 0
    assert {"name": "mom_1h", "value": 0.1} in body["families"]["momentum"]
    assert {"name": "taker_ratio_log", "value": -0.2} in body["families"]["derivados"]


# --------------------------------------------------------------------- #
# Historial y cobertura
# --------------------------------------------------------------------- #


def test_history_cuenta_por_estado_y_pagina(client: TestClient) -> None:
    _seed(
        [_forecast(BASE_MS + i * TF_MS, status="resolved") for i in range(5)]
        + [_forecast(BASE_MS + 100 * TF_MS, status="open")]
        + [_forecast(BASE_MS + 200 * TF_MS, status="gap")]
    )
    body = client.get(f"/api/history/forecasts?symbol={SYMBOL}&limit=2").json()

    assert body["counts"] == {"resolved": 5, "open": 1, "gap": 1}
    assert body["total"] == 7
    assert len(body["forecasts"]) == 2
    # Del más nuevo al más viejo: la tabla arranca por lo último emitido.
    assert body["forecasts"][0]["open_time"] > body["forecasts"][1]["open_time"]


def test_history_filtra_por_estado(client: TestClient) -> None:
    _seed(
        [
            _forecast(BASE_MS, status="resolved"),
            _forecast(BASE_MS + TF_MS, status="open"),
        ]
    )
    body = client.get(
        f"/api/history/forecasts?symbol={SYMBOL}&status=resolved"
    ).json()
    assert [f["status"] for f in body["forecasts"]] == ["resolved"]


def test_coverage_marca_la_muestra_mixta(client: TestClient) -> None:
    """Dos `model_version` en la misma muestra invalidan la comparación, y el
    tracker ya lo marca en su reporte de texto. La API tiene que decir lo
    mismo: si solo estuviera en el .txt, el dashboard promediaría dos modelos
    sin avisar — que es exactamente lo que el flag existe para evitar."""
    series = synthetic_series(n=200, seed=5, symbol=SYMBOL)
    _seed(
        [
            _candle(int(t), float(c))
            for t, c in zip(series.open_time, series.close, strict=True)
        ]
        + [
            _forecast(int(series.open_time[10]), status="resolved"),
            _forecast(
                int(series.open_time[20]),
                status="resolved",
                model_version="bob-forecast-0.1.0+vol=xgb",
            ),
        ]
    )
    body = client.get(
        f"/api/history/coverage?symbol={SYMBOL}&baselines=false"
    ).json()

    assert body["mixed_sample"] is True
    assert sorted(body["model_versions"]) == [
        "bob-forecast-0.1.0+vol=gbm",
        "bob-forecast-0.1.0+vol=xgb",
    ]


def test_csv_saca_los_niveles_del_cono_de_los_datos(client: TestClient) -> None:
    """Las columnas del cono no se hardcodean: salen de `conformal_alphas`, y
    cambiarlas allá no debe dejar el export mudo."""
    _seed([_forecast(BASE_MS, status="resolved")])
    res = client.get(f"/api/history/forecasts.csv?symbol={SYMBOL}")

    assert res.status_code == 200
    assert "text/csv" in res.headers["content-type"]
    header = res.text.splitlines()[0]
    assert "cone_hit_0.05" in header
    assert f"{SYMBOL}" in res.headers["content-disposition"]


# --------------------------------------------------------------------- #
# El gate: el veredicto tiene que ser el del modelo que emite
# --------------------------------------------------------------------- #


def _artifact(
    variant_flags: tuple[bool, bool, bool], vol_kind: str, auc: float
) -> dict[str, Any]:
    direction = {
        "n_samples": 1000,
        "breakeven_prob": 0.6,
        "diebold_mariano": {"p_value": 0.5, "statistic": 0.1},
        "model": {
            "auc": auc,
            "brier": 0.24,
            "brier_skill_score": 0.01,
            "mean_calibration_error_pp": 3.0,
        },
    }
    return {
        "n_features": 81,
        "n_bars": 69000,
        "date_from": "2024-08-31",
        "date_to": "2026-08-25",
        "config": {
            "use_derivatives": variant_flags[0],
            "use_book": variant_flags[1],
            "use_book_near": variant_flags[2],
            "vol_kind": vol_kind,
            "model_kind": "gbm",
        },
        "directions": {"long": direction, "short": direction},
        "volatility": {},
        "intervals": {},
        "family_importance": {},
        "importance_top": [],
    }


@pytest.fixture
def fake_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # gbm es MÁS NUEVO que xgb: por fecha gana gbm, por orden alfabético del
    # nombre completo ganaría xgb. Es justo el caso que rompía la selección.
    (tmp_path / "ETHUSDT-15m-price+deriv-xgb-20260826141930.json").write_text(
        json.dumps(_artifact((True, False, False), "xgb", 0.61)), encoding="utf-8"
    )
    (tmp_path / "ETHUSDT-15m-price+deriv-gbm-20260901120000.json").write_text(
        json.dumps(_artifact((True, False, False), "gbm", 0.52)), encoding="utf-8"
    )
    (tmp_path / "forward-ETHUSDT-15m-n288-20260904051028.json").write_text(
        json.dumps({"n_resolved": 288}), encoding="utf-8"
    )
    (tmp_path / "riskfilter-ETHUSDT-15m-price+deriv-gbm-20260914202117.json").write_text(
        json.dumps(
            {
                "config": {"preregistration": "docs/PREREGISTRO_FILTRO_RIESGO.md"},
                "primary": {
                    "leverage": 20.0,
                    "quantile": 0.80,
                    "n_bars": 43422,
                    "n_events": 1430,
                    "effective_events": 89.375,
                    "lift_ci95": [2.639, 3.515],
                    "diff_ci95": [0.489, 1.284],
                    "lifts": {
                        "model": {
                            "lift": 3.077,
                            "capture_rate": 0.520,
                            "reject_rate": 0.169,
                            "false_reject_rate": 0.163,
                        },
                        "ewma": {"lift": 2.195, "capture_rate": 0.359},
                    },
                },
                "verdict": {"viable": True, "reasons": ["criterio 1: PASA"]},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(backtest_routes, "ARTIFACTS_DIR", tmp_path)
    return tmp_path


def test_el_gate_elige_por_fecha_y_por_estimador_no_por_orden_alfabetico(
    client: TestClient, fake_artifacts: Path
) -> None:
    """El veredicto que va al lado del KPI 1 tiene que salir del run del modelo
    que está emitiendo. Ordenar por nombre completo elegía `xgb` sobre `gbm`
    por la 'x', y el dashboard habría citado la medición de otro modelo — con
    otro AUC, o sea con otro veredicto."""
    body = client.get("/api/backtest/gate").json()

    assert body["vol_kind"] == "gbm"
    assert body["run_id"].endswith("gbm-20260901120000")
    assert body["directions"]["long"]["auc"] == pytest.approx(0.52)
    # AUC 0.52 < 0.55: no discrimina, no se habilita.
    assert body["gate"]["habilitado"] is False


def test_el_gate_puede_pedirse_para_otro_estimador(
    client: TestClient, fake_artifacts: Path
) -> None:
    body = client.get("/api/backtest/gate?vol_kind=xgb").json()
    assert body["run_id"].endswith("xgb-20260826141930")
    # Ese run sí supera 0.55 de AUC y tiene BSS > 0: el gate lo habilita, y
    # que la API lo diga prueba que el veredicto no está escrito a mano.
    assert body["gate"]["habilitado"] is True


def test_el_gate_404_si_no_hay_artefacto_de_esa_combinacion(
    client: TestClient, fake_artifacts: Path
) -> None:
    res = client.get("/api/backtest/gate?variant=full&vol_kind=gbm")
    assert res.status_code == 404


def test_los_artefactos_forward_no_se_mezclan_con_los_del_gate(
    client: TestClient, fake_artifacts: Path
) -> None:
    """Viven en el mismo directorio y tienen otro esquema."""
    body = client.get("/api/backtest/runs").json()
    assert body["n"] == 2
    assert all("forward-" not in r["run_id"] for r in body["runs"])
    assert all("riskfilter-" not in r["run_id"] for r in body["runs"])

    forward = client.get("/api/backtest/forward").json()
    assert forward["frozen"] is True
    assert forward["report"]["n_resolved"] == 288


def test_el_filtro_de_regimen_sale_del_artefacto_con_su_evidencia(
    client: TestClient, fake_artifacts: Path
) -> None:
    """Regla 2 aplicada al filtro: el estado no puede ir sin su lift medido.

    Un cartel de "no entres ahora" sin decir que evita el 52% de las
    liquidaciones —y no el 100%— es una alarma sin magnitud. Y el número sale
    del artefacto, no del TSX: el día que se remida, cambia solo.
    """
    body = client.get("/api/backtest/riskfilter").json()

    assert body["viable"] is True
    assert body["lift"] == pytest.approx(3.077)
    assert body["lift_ci95"] == [2.639, 3.515]
    assert body["capture_rate"] == pytest.approx(0.520)
    assert body["baseline_lift"] == pytest.approx(2.195)
    assert body["quantile"] == pytest.approx(0.80)
    assert body["preregistration"] == "docs/PREREGISTRO_FILTRO_RIESGO.md"


def test_el_filtro_404_sin_medicion(client: TestClient, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(backtest_routes, "ARTIFACTS_DIR", tmp_path)
    assert client.get("/api/backtest/riskfilter").status_code == 404


def test_run_detail_no_deja_salir_del_directorio_de_artefactos(
    client: TestClient, fake_artifacts: Path
) -> None:
    """`run_id` se resuelve contra el listado, nunca se concatena a una ruta."""
    assert client.get("/api/backtest/runs/..%2f..%2fbob").status_code == 404
    assert client.get("/api/backtest/runs/no-existe").status_code == 404


# --------------------------------------------------------------------- #
# Settings y la promesa de solo lectura
# --------------------------------------------------------------------- #


def test_settings_no_devuelve_credenciales(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Restricción de CLAUDE.md: las credenciales viven en `.env` local y no
    salen del disco. El endpoint dice si están configuradas, nunca cuáles son."""
    from pydantic import SecretStr

    from bob.config import settings as cfg

    monkeypatch.setattr(cfg, "telegram_bot_token", SecretStr("secreto-123"))
    monkeypatch.setattr(cfg, "telegram_chat_id", "chat-456")

    res = client.get("/api/settings")
    body = res.json()

    assert body["integrations"]["telegram_configured"] is True
    assert "secreto-123" not in res.text
    assert "chat-456" not in res.text
    assert body["editable"] is False


def test_ninguna_ruta_de_lectura_toca_la_muestra(client: TestClient) -> None:
    """La promesa central de esta capa: la API es un visor.

    Un endpoint que resolviera pronósticos haría avanzar la validación forward
    cuando alguien abre el navegador, y una muestra que se mide a sí misma no
    mide nada. El test recorre todo lo que el dashboard llama y verifica que
    los registros quedan intactos.
    """
    _seed(
        [_candle(BASE_MS + i * TF_MS, 2000.0 + i) for i in range(50)]
        + [_forecast(BASE_MS + 10 * TF_MS, status="open")]
    )

    def snapshot() -> list[tuple[str, str, float | None]]:
        with get_session() as session:
            return [
                (r.forecast_id, r.status, r.realized_vol)
                for r in session.exec(select(ForecastRecord)).all()
            ]

    antes = snapshot()
    for url in (
        f"/api/market/candles?symbol={SYMBOL}",
        f"/api/analysis/latest?symbol={SYMBOL}",
        f"/api/analysis/projection?symbol={SYMBOL}&leverage=3",
        f"/api/analysis/features?symbol={SYMBOL}",
        f"/api/history/forecasts?symbol={SYMBOL}",
        f"/api/history/coverage?symbol={SYMBOL}&baselines=false",
        f"/api/history/forecasts.csv?symbol={SYMBOL}",
        "/api/settings",
    ):
        assert client.get(url).status_code == 200, url

    assert snapshot() == antes
