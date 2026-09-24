"""Tests de la validación forward del panel (Fase 10-bis).

El que decide la fase es `test_backfill_*`: si un pronóstico emitido tarde
entrara a la muestra confirmatoria, la corrida entera sería un backtest con
otro nombre — el panel usa solo klines y **cualquier fecha se puede
reconstruir después**.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from bob.data.panel import PanelData
from bob.db.models import PanelForecastRecord
from bob.models.panel_experiment import PanelConfig
from bob.models.panel_production import fit_panel_bundle, predict_date
from bob.paper.panel_forward import (
    DAY_MS,
    FORWARD_GRACE_MS,
    build_report,
    emit_date,
    forecast_id,
    is_backfill,
    resolve_mature,
)
from bob.signals.panel_features import assemble_panel_features
from bob.signals.universe import eligibility_mask


@pytest.fixture()
def session():  # noqa: ANN201
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _panel(n_sym: int = 30, n_dates: int = 500, seed: int = 0) -> PanelData:
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(scale=0.03, size=(n_sym, n_dates)), axis=1))
    open_ = close.copy()
    open_[:, 1:] = close[:, :-1]
    return PanelData(
        symbols=tuple(f"S{i}USDT" for i in range(n_sym)),
        timeframe="1d",
        open_time=np.arange(n_dates, dtype=np.int64) * DAY_MS,
        open=open_,
        high=close * 1.01,
        low=close * 0.99,
        close=close,
        volume=np.full((n_sym, n_dates), 1e5),
        quote_volume=np.full((n_sym, n_dates), 1e8),
        taker_buy_volume=np.full((n_sym, n_dates), 5e4),
        n_trades=np.full((n_sym, n_dates), 1000.0),
    )


def _fitted(panel: PanelData, cfg: PanelConfig):  # noqa: ANN202
    eligible = eligibility_mask(panel.close, panel.quote_volume)
    features = assemble_panel_features(panel, eligible)
    bundle = fit_panel_bundle(features, panel.open, panel.close, eligible, cfg)
    return eligible, features, bundle


# --------------------------------------------------------------------------- #
# La regla que sostiene la fase
# --------------------------------------------------------------------------- #


def test_backfill_separa_lo_emitido_a_tiempo_de_lo_reconstruido() -> None:
    """La barra de decisión cierra en `open_time + 1 día`; de ahí corre la gracia."""
    t = 100 * DAY_MS
    cierre = t + DAY_MS

    def en(ms: int) -> datetime:
        return datetime.fromtimestamp(ms / 1000, tz=UTC)

    assert not is_backfill(t, en(cierre + 60_000)), "un minuto después del cierre es a tiempo"
    assert not is_backfill(t, en(cierre + FORWARD_GRACE_MS - 1))
    assert is_backfill(t, en(cierre + FORWARD_GRACE_MS + 1)), "pasada la gracia es backfill"
    # 20 h después ya se conoce casi toda la barra de entrada.
    assert is_backfill(t, en(cierre + 20 * 3_600_000))


def test_backfill_queda_fuera_del_ic_confirmatorio(session: Session) -> None:
    """EL test de la fase: reconstruir el pasado no puede contar como forward.

    Sin esta separación, correr el comando sobre historia vieja produciría un
    "IC forward" idéntico al del backtest — y la validación no mediría nada.
    """
    base = 200 * DAY_MS
    tarde = datetime.fromtimestamp((base + 30 * DAY_MS) / 1000, tz=UTC)
    a_tiempo = datetime.fromtimestamp((base + DAY_MS + 60_000) / 1000, tz=UTC)

    for k, (open_time, emitido) in enumerate(
        [(base, tarde), (base + DAY_MS, a_tiempo)]
    ):
        for i in range(6):
            session.add(
                PanelForecastRecord(
                    forecast_id=forecast_id(open_time, f"S{i}"),
                    symbol=f"S{i}",
                    open_time=open_time,
                    probability=i / 6.0,
                    cross_section_rank=(i + 1) / 7.0,
                    section_width=6,
                    reference_close="100",
                    status="resolved",
                    relative_return=float(i) * 0.01,
                    emitted_at=emitido,
                    is_backfill=(k == 0),
                )
            )
    session.commit()

    solo_forward = build_report(n_boot=400, session=session)
    assert solo_forward.n_dates == 1, "la fecha reconstruida no entra"
    assert solo_forward.backfill_dates == 1, "pero se reporta que existe"

    con_todo = build_report(n_boot=400, include_backfill=True, session=session)
    assert con_todo.n_dates == 2, "--include-backfill las mete, y NO es la muestra confirmatoria"


# --------------------------------------------------------------------------- #
# Ajuste y emisión
# --------------------------------------------------------------------------- #


def test_el_bundle_no_entrena_con_etiquetas_sin_ocurrir() -> None:
    """Las últimas H fechas no tienen resultado todavía: entrenar con ellas es lookahead."""
    panel = _panel()
    cfg = PanelConfig(horizon=5)
    _, features, bundle = _fitted(panel, cfg)

    ultima_utilizable = int(features.open_time[panel.n_dates - cfg.horizon - 1])
    assert bundle.fit_through_ms <= ultima_utilizable
    assert bundle.n_train > 0


def test_el_bundle_rechaza_otro_set_de_features() -> None:
    panel = _panel()
    cfg = PanelConfig(horizon=5)
    eligible, features, bundle = _fitted(panel, cfg)
    sin_xs = assemble_panel_features(panel, eligible, add_cross_sectional=False)
    with pytest.raises(ValueError, match="otro set de features"):
        predict_date(bundle, sin_xs, panel.close, eligible, panel.n_dates - 1)


def test_predecir_una_fecha_rankea_solo_dentro_de_esa_fecha() -> None:
    panel = _panel()
    cfg = PanelConfig(horizon=5)
    eligible, features, bundle = _fitted(panel, cfg)
    pred = predict_date(bundle, features, panel.close, eligible, panel.n_dates - 1)

    assert pred.width == len(pred.symbols) == pred.probability.size
    assert pred.rank.min() > 0.0 and pred.rank.max() < 1.0
    # El orden del rango sigue al de la probabilidad, dentro de la fecha.
    assert np.array_equal(np.argsort(pred.rank), np.argsort(pred.probability))


def test_emitir_es_idempotente(session: Session) -> None:
    panel = _panel()
    cfg = PanelConfig(horizon=5)
    eligible, features, bundle = _fitted(panel, cfg)
    j = panel.n_dates - 1

    primera = emit_date(panel, bundle, eligible, features, j, session=session)
    segunda = emit_date(panel, bundle, eligible, features, j, session=session)

    assert primera.written > 0
    assert segunda.written == 0, "recorrer la misma fecha no duplica ni infla la muestra"
    total = len(list(session.exec(select(PanelForecastRecord))))
    assert total == primera.written


def test_emitir_guarda_el_vector_completo(session: Session) -> None:
    """Regla 10: sin el vector no hay post-mortem posible."""
    import json

    panel = _panel()
    cfg = PanelConfig(horizon=5)
    eligible, features, bundle = _fitted(panel, cfg)
    emit_date(panel, bundle, eligible, features, panel.n_dates - 1, session=session)

    row = next(iter(session.exec(select(PanelForecastRecord))))
    vector = json.loads(row.features_json)
    assert len(vector) == len(features.names)
    assert row.model_version.startswith("bob-panel-")


# --------------------------------------------------------------------------- #
# Resolución
# --------------------------------------------------------------------------- #


def test_resolver_mide_contra_la_mediana_de_su_propia_fecha(session: Session) -> None:
    """El target es relativo a la sección de ESA fecha, no a una mediana global."""
    panel = _panel(n_sym=10, n_dates=400)
    cfg = PanelConfig(horizon=5)
    eligible, features, bundle = _fitted(panel, cfg)

    j = 300  # deja margen para el warm-up y para que el horizonte cierre
    emit_date(panel, bundle, eligible, features, j, session=session)
    counts = resolve_mature(panel, cfg.horizon, session=session)

    assert counts["resolved"] > 0
    filas = list(session.exec(select(PanelForecastRecord)))
    rel = np.array([r.relative_return for r in filas if r.relative_return is not None])
    assert abs(float(np.median(rel))) < 1e-12, "la mediana del exceso de la fecha es 0"


def test_resolver_deja_abierto_lo_que_no_cumplio_el_horizonte(session: Session) -> None:
    panel = _panel(n_sym=10, n_dates=400)
    cfg = PanelConfig(horizon=5)
    eligible, features, bundle = _fitted(panel, cfg)

    emit_date(panel, bundle, eligible, features, panel.n_dates - 1, session=session)
    counts = resolve_mature(panel, cfg.horizon, session=session)

    assert counts["resolved"] == 0
    assert counts["open"] > 0


def test_resolver_marca_gap_cuando_falta_el_precio(session: Session) -> None:
    panel = _panel(n_sym=10, n_dates=400)
    cfg = PanelConfig(horizon=5)
    eligible, features, bundle = _fitted(panel, cfg)
    j = 300
    emit_date(panel, bundle, eligible, features, j, session=session)

    roto = PanelData(
        symbols=panel.symbols,
        timeframe=panel.timeframe,
        open_time=panel.open_time,
        open=np.full_like(panel.open, np.nan),
        high=panel.high,
        low=panel.low,
        close=np.full_like(panel.close, np.nan),
        volume=panel.volume,
        quote_volume=panel.quote_volume,
        taker_buy_volume=panel.taker_buy_volume,
        n_trades=panel.n_trades,
    )
    counts = resolve_mature(roto, cfg.horizon, session=session)
    assert counts["gap"] > 0 and counts["resolved"] == 0


# --------------------------------------------------------------------------- #
# Reporte
# --------------------------------------------------------------------------- #


def _sembrar(session: Session, n_dates: int, signal: float, seed: int = 0) -> None:
    rng = np.random.default_rng(seed)
    a_tiempo = timedelta(minutes=1)
    for d in range(n_dates):
        open_time = (1000 + d) * DAY_MS
        emitido = datetime.fromtimestamp((open_time + DAY_MS) / 1000, tz=UTC) + a_tiempo
        prob = rng.uniform(size=20)
        rel = signal * (prob - 0.5) + rng.normal(scale=0.05, size=20)
        rel = rel - np.median(rel)
        for i in range(20):
            session.add(
                PanelForecastRecord(
                    forecast_id=forecast_id(open_time, f"S{i}"),
                    symbol=f"S{i}",
                    open_time=open_time,
                    probability=float(prob[i]),
                    cross_section_rank=(i + 1) / 21.0,
                    section_width=20,
                    reference_close="100",
                    status="resolved",
                    relative_return=float(rel[i]),
                    emitted_at=emitido,
                    is_backfill=False,
                )
            )
    session.commit()


def test_el_reporte_no_confirma_con_ruido(session: Session) -> None:
    """Control negativo: sin señal, el IC95% tiene que contener 0."""
    _sembrar(session, n_dates=120, signal=0.0, seed=1)
    rep = build_report(n_boot=2000, session=session)
    assert rep.n_dates == 120
    assert not rep.confirmed
    assert rep.ic_ci[0] <= 0.0 <= rep.ic_ci[1]


def test_el_reporte_confirma_una_senal_inyectada(session: Session) -> None:
    """Control positivo: sin esto, el negativo pasaría con un reporte roto."""
    _sembrar(session, n_dates=200, signal=0.30, seed=2)
    rep = build_report(n_boot=2000, session=session)
    assert rep.ic_mean > 0.2
    assert rep.confirmed


def test_el_corte_primario_gobierna_el_veredicto(session: Session) -> None:
    """Antes de 180 fechas el reporte es descriptivo, aunque el IC excluya 0."""
    from bob.paper.panel_forward import PRIMARY_CUTOFF_DAYS, render_report

    _sembrar(session, n_dates=60, signal=0.30, seed=3)
    rep = build_report(n_boot=2000, session=session)
    assert rep.n_dates < PRIMARY_CUTOFF_DAYS
    assert not rep.reached_cutoff
    texto = render_report(rep)
    assert "LECTURA DESCRIPTIVA" in texto
    assert "VEREDICTO" not in texto, "no se emite veredicto antes del corte"


def test_el_reporte_vacio_no_revienta(session: Session) -> None:
    from bob.paper.panel_forward import render_report

    rep = build_report(n_boot=100, session=session)
    assert rep.n_dates == 0
    assert "todavía no hay ninguna fecha resuelta" in render_report(rep)
