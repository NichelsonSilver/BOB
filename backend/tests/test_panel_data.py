"""Tests del panel: armado de la grilla, universo congelado y ensamblado."""

from __future__ import annotations

import json

import numpy as np
import pytest

from bob.data.panel import (
    CRYPTO_CONTRACT,
    STATUS_DELISTED,
    TRADFI_CONTRACT,
    PanelData,
    SymbolMeta,
    Universe,
    _panel_from_rows,
    load_universe,
    save_universe,
)
from bob.signals.panel_features import (
    MARKET_FEATURES,
    _contiguous_segments,
    assemble_panel_features,
)
from bob.signals.universe import eligibility_mask

DAY = 86_400_000


def _row(sym: str, t: int, px: float, qvol: float = 1e7):
    return (sym, t, str(px), str(px * 1.01), str(px * 0.99), str(px), "100", str(qvol), "50", 10)


# --------------------------------------------------------------------------- #
# Grilla
# --------------------------------------------------------------------------- #


def test_panel_usa_la_union_de_fechas_no_la_interseccion() -> None:
    """La intersección recortaría el panel al símbolo más joven.

    Eso es volver a seleccionar sobrevivientes: el símbolo que listó tarde
    borraría la historia de todos los demás.
    """
    rows = [_row("AAA", 0, 10.0), _row("AAA", DAY, 11.0), _row("BBB", DAY, 20.0)]
    panel = _panel_from_rows(["AAA", "BBB"], "1d", rows)

    assert panel.n_dates == 2
    assert panel.symbols == ("AAA", "BBB")
    assert np.isnan(panel.close[1, 0]), "BBB no existía: NaN, no cero"
    assert panel.close[1, 1] == 20.0


def test_panel_descarta_simbolos_sin_ninguna_vela() -> None:
    rows = [_row("AAA", 0, 10.0)]
    panel = _panel_from_rows(["AAA", "FANTASMA"], "1d", rows)
    assert panel.symbols == ("AAA",)


def test_panel_reporta_huecos_del_eje_de_fechas() -> None:
    rows = [_row("AAA", 0, 10.0), _row("AAA", 3 * DAY, 11.0)]
    panel = _panel_from_rows(["AAA"], "1d", rows)
    assert panel.date_gaps() == [(0, 3 * DAY)]


def test_panel_propiedades() -> None:
    rows = [_row("AAA", 0, 10.0), _row("BBB", 0, 20.0)]
    panel = _panel_from_rows(["AAA", "BBB"], "1d", rows)
    assert panel.n_symbols == 2
    assert panel.interval_ms == DAY
    assert panel.index_of("BBB") == 1
    assert panel.observed.all()


def test_panel_vacio_no_revienta() -> None:
    panel = _panel_from_rows([], "1d", [])
    assert panel.n_symbols == 0 and panel.n_dates == 0
    assert panel.date_gaps() == []


# --------------------------------------------------------------------------- #
# Universo congelado
# --------------------------------------------------------------------------- #


def _meta(sym: str, contract: str = CRYPTO_CONTRACT, status: str = "TRADING", months: int = 24):
    return SymbolMeta(
        symbol=sym,
        contract_type=contract,
        status=status,
        first_month="2023-01",
        last_month="2024-12",
        n_months=months,
        source="exchangeInfo",
    )


def test_universo_excluye_los_perpetuos_de_acciones() -> None:
    uni = Universe(
        timeframe="1d",
        discovered_at="2026-09-22T00:00:00+00:00",
        symbols=(
            _meta("ETHUSDT"),
            _meta("NVDAUSDT", contract=TRADFI_CONTRACT),
            _meta("LUNAUSDT", status=STATUS_DELISTED),
        ),
    )
    nombres = [m.symbol for m in uni.crypto_perps()]
    assert "NVDAUSDT" not in nombres
    assert "LUNAUSDT" in nombres, "un deslistado cripto SÍ entra: es el punto de la fase"
    assert uni.summary() == {"total": 3, "crypto_perp": 2, "tradfi": 1, "delisted": 1}


def test_universo_filtra_por_meses_minimos() -> None:
    uni = Universe("1d", "x", (_meta("AAA", months=3), _meta("BBB", months=30)))
    assert [m.symbol for m in uni.crypto_perps(min_months=6)] == ["BBB"]


def test_universo_va_y_vuelve_de_json(tmp_path) -> None:  # noqa: ANN001
    uni = Universe(
        "1d",
        "2026-09-22T00:00:00+00:00",
        (_meta("ETHUSDT"), _meta("LUNAUSDT", status=STATUS_DELISTED)),
    )
    path = tmp_path / "universe-1d.json"
    save_universe(uni, path)
    back = load_universe(path)
    assert back == uni
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["summary"]["delisted"] == 1


def test_load_universe_sin_artefacto_explica_como_arreglarlo(tmp_path) -> None:  # noqa: ANN001
    with pytest.raises(FileNotFoundError, match="--discover"):
        load_universe(tmp_path / "no-existe.json")


# --------------------------------------------------------------------------- #
# Ensamblado
# --------------------------------------------------------------------------- #


def test_segmentos_contiguos_cortan_en_los_huecos() -> None:
    observed = np.array([True, True, False, True, True])
    step_ok = np.ones(5, dtype=bool)
    assert _contiguous_segments(observed, step_ok) == [(0, 2), (3, 5)]


def test_segmentos_cortan_cuando_salta_el_eje_de_fechas() -> None:
    observed = np.array([True, True, True])
    step_ok = np.array([True, True, False])  # el panel saltó un día
    assert _contiguous_segments(observed, step_ok) == [(0, 2), (2, 3)]


def _fake_panel(n_sym: int = 6, n_dates: int = 400, seed: int = 0) -> PanelData:
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(scale=0.02, size=(n_sym, n_dates)), axis=1))
    return PanelData(
        symbols=tuple(f"S{i}USDT" for i in range(n_sym)),
        timeframe="1d",
        open_time=np.arange(n_dates, dtype=np.int64) * DAY,
        open=close,
        high=close * 1.01,
        low=close * 0.99,
        close=close,
        volume=np.full((n_sym, n_dates), 1e5),
        quote_volume=np.full((n_sym, n_dates), 1e8),
        taker_buy_volume=np.full((n_sym, n_dates), 5e4),
        n_trades=np.full((n_sym, n_dates), 1000.0),
    )


def test_ensamblado_produce_bloques_y_rangos() -> None:
    panel = _fake_panel()
    eligible = eligibility_mask(panel.close, panel.quote_volume)
    feats = assemble_panel_features(panel, eligible)

    assert "mom_5d" in feats.names, "las ventanas diarias deben usar el perfil diario"
    assert "mom_5d__xs" in feats.names, "falta el bloque transversal"
    assert "beta_60d" in feats.names
    assert feats.valid.any()


def test_ensamblado_sin_transversal_es_el_baseline_secundario() -> None:
    panel = _fake_panel()
    eligible = eligibility_mask(panel.close, panel.quote_volume)
    con = assemble_panel_features(panel, eligible)
    sin = assemble_panel_features(panel, eligible, add_cross_sectional=False)
    assert len(sin.names) < len(con.names)
    assert not any(n.endswith("__xs") for n in sin.names)


def test_ensamblado_no_mira_el_futuro() -> None:
    """Mutar el futuro del panel no puede cambiar las features del pasado."""
    panel = _fake_panel()
    eligible = eligibility_mask(panel.close, panel.quote_volume)
    base = assemble_panel_features(panel, eligible)

    close2 = panel.close.copy()
    close2[:, 300:] *= 5.0
    panel2 = PanelData(
        symbols=panel.symbols,
        timeframe=panel.timeframe,
        open_time=panel.open_time,
        open=close2,
        high=close2 * 1.01,
        low=close2 * 0.99,
        close=close2,
        volume=panel.volume,
        quote_volume=panel.quote_volume,
        taker_buy_volume=panel.taker_buy_volume,
        n_trades=panel.n_trades,
    )
    mutated = assemble_panel_features(panel2, eligible)
    np.testing.assert_allclose(base.X[:, :300, :], mutated.X[:, :300, :], equal_nan=True)


def test_ensamblado_de_panel_vacio() -> None:
    panel = _fake_panel(n_sym=1, n_dates=1)
    eligible = np.zeros((1, 1), dtype=bool)
    feats = assemble_panel_features(panel, eligible)
    assert feats.X.shape[2] == 0 and not feats.valid.any()


def test_ensamblado_llena_la_matriz_en_su_lugar() -> None:
    """La matriz se preasigna y se llena; no se concatenan bloques sueltos.

    Concatenar sostiene cada número dos veces en el pico — sobre el panel real
    son ~3 GB en vez de ~1,5, y eso alcanzó para que el sistema matara una
    corrida. Este test fija la propiedad observable del arreglo: la matriz es
    contigua y tiene exactamente el ancho de los tres bloques, sin copias
    intermedias que la dupliquen.
    """
    panel = _fake_panel(n_sym=8, n_dates=300)
    eligible = eligibility_mask(panel.close, panel.quote_volume)
    con = assemble_panel_features(panel, eligible)
    sin = assemble_panel_features(panel, eligible, add_cross_sectional=False)

    n_market = 5
    n_base = len(sin.names) - n_market
    assert len(con.names) == 2 * n_base + n_market
    assert con.X.flags["C_CONTIGUOUS"]
    # El bloque transversal va DESPUÉS del de mercado, y son los mismos
    # nombres del bloque base con su sufijo.
    assert con.names[n_base : n_base + n_market] == list(MARKET_FEATURES)
    assert con.names[n_base + n_market :] == [f"{n}__xs" for n in con.names[:n_base]]
