"""Amplitud cripto, Altcoin Season y funding de Binance."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, timedelta

import httpx
import numpy as np
import pytest

from marea import breadth as br
from marea.data import binance_funding, ingest_funding, store
from marea.data.binance_funding import FundingClient, FundingError, FundingPoint
from marea.funding import annualized_pct, daily_funding, spread, trailing_mean

D = date.fromisoformat

# --- Amplitud ----------------------------------------------------------------------


def test_media_movil_exige_la_ventana_completa() -> None:
    close = np.array([[1.0, 2.0, 3.0, np.nan, 5.0, 6.0, 7.0]])
    out = br._rolling_full_mean(close, 3)
    assert np.isnan(out[0, :2]).all()
    assert out[0, 2] == pytest.approx(2.0)
    assert np.isnan(out[0, 3:6]).all()  # el hueco corta tres ventanas
    assert out[0, 6] == pytest.approx(6.0)
    assert np.isnan(br._rolling_full_mean(close, 10)).all()


def make_panel(n_days: int, n_alts: int, winners: int, seed: int = 0) -> br.Panel:
    """BTC plano; `winners` alts suben y el resto baja."""
    rng = np.random.default_rng(seed)
    t = np.arange(n_days, dtype=float)
    rows = [100.0 * np.ones(n_days)]
    for i in range(n_alts):
        drift = 0.002 if i < winners else -0.002
        rows.append(np.exp(np.log(10.0) + drift * t + rng.normal(0, 1e-4, n_days)))
    close = np.vstack(rows)
    qv = np.vstack([np.full(n_days, 1e9 - i) for i in range(n_alts + 1)])
    symbols = ("BTCUSDT", *(f"A{i:03d}USDT" for i in range(n_alts)))
    days = tuple(D("2024-01-01") + timedelta(days=i) for i in range(n_days))
    return br.Panel(symbols, days, close, qv)


def test_amplitud_y_altseason_cuentan_lo_que_deben() -> None:
    panel = make_panel(260, 60, winners=45)
    rows = br.breadth(panel, np.ones(panel.close.shape, dtype=bool))
    last = rows[-1]
    frac200, n200 = last.above[200]
    assert n200 == 61
    assert frac200 == pytest.approx(45 / 61)  # BTC plano no está por encima
    assert last.altseason_n == br.ALTSEASON_TOP
    # Top 50 por volumen = los primeros 50 alts: 45 ganadores y 5 perdedores.
    assert last.altseason == pytest.approx(45 / 50)
    assert rows[0].altseason is None and np.isnan(rows[0].above[200][0])


def test_altseason_necesita_cincuenta_alts_y_btc() -> None:
    small = make_panel(120, 20, winners=10)
    assert all(r.altseason is None for r in br.breadth(small, np.ones(small.close.shape, bool)))
    no_btc = br.Panel(("XUSDT",), small.days, small.close[1:2], small.quote_volume[1:2])
    assert all(r.altseason is None for r in br.breadth(no_btc, np.ones((1, 120), bool)))


def test_cambiar_el_futuro_no_mueve_el_pasado() -> None:
    panel = make_panel(300, 55, winners=30, seed=3)
    cut = 250
    shocked_close = panel.close.copy()
    shocked_close[:, cut:] *= np.linspace(0.2, 5.0, panel.close.shape[0])[:, None]
    shocked = br.Panel(panel.symbols, panel.days, shocked_close, panel.quote_volume)
    mask = np.ones(panel.close.shape, dtype=bool)
    a, b = br.breadth(panel, mask), br.breadth(shocked, mask)
    for x, y in zip(a[:cut], b[:cut], strict=True):
        assert x.altseason == y.altseason
        for w in br.SMA_WINDOWS:
            assert (np.isnan(x.above[w][0]) and np.isnan(y.above[w][0])) or x.above[w] == y.above[w]


def test_etiquetas_de_temporada() -> None:
    assert br.season_label(None) == "sin dato"
    assert br.season_label(0.80) == "temporada de alts"
    assert br.season_label(0.10) == "temporada de BTC"
    assert br.season_label(0.50) == "neutral"


def test_exclusiones_no_son_monedas() -> None:
    assert {"BTCDOMUSDT", "USDCUSDT", "PAXGUSDT"} <= br.EXCLUDED
    assert "BTCUSDT" not in br.EXCLUDED


# --- Funding: puro -----------------------------------------------------------------


def ms(s: str) -> int:
    return int(datetime.fromisoformat(s).replace(tzinfo=UTC).timestamp() * 1000)


def fp(t: str, rate: float, sym: str = "BTCUSDT") -> FundingPoint:
    return FundingPoint(sym, ms(t), rate)


def test_funding_diario_suma_y_solo_dias_cerrados() -> None:
    pts = [
        fp("2026-01-01T00:00", 0.0001),
        fp("2026-01-01T08:00", 0.0002),
        fp("2026-01-01T16:00", 0.0003),
        fp("2026-01-02T00:00", 0.0001),  # cierra el 01; el 02 sigue abierto
        fp("2026-01-02T08:00", 0.0005),
    ]
    out = daily_funding(pts)
    assert out == {D("2026-01-01"): pytest.approx(0.0006)}
    assert daily_funding([]) == {}
    assert annualized_pct(0.0003) == pytest.approx(10.95)


def test_spread_y_media_movil_cortan_en_huecos() -> None:
    a = {D("2026-01-01"): 3.0, D("2026-01-02"): 4.0, D("2026-01-04"): 1.0}
    b = {D("2026-01-01"): 1.0, D("2026-01-02"): 1.0, D("2026-01-03"): 9.0}
    sp = spread(a, b)
    assert sp == [(D("2026-01-01"), 2.0), (D("2026-01-02"), 3.0)]
    assert trailing_mean(sp, 2) == [(D("2026-01-02"), 2.5)]
    with pytest.raises(ValueError):
        trailing_mean(sp, 0)


# --- Funding: cliente e ingesta ----------------------------------------------------


class FakeBinance:
    def __init__(self, points: dict[str, list[int]]) -> None:
        self.points = points
        self.fail_next: list[int] = []
        self.starts: list[int] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.fail_next:
            return httpx.Response(self.fail_next.pop(0), headers={"Retry-After": "0"})
        sym = request.url.params["symbol"]
        if sym not in self.points:
            return httpx.Response(400, text='{"code":-1121,"msg":"Invalid symbol."}')
        start = int(request.url.params["startTime"])
        limit = int(request.url.params["limit"])
        self.starts.append(start)
        rows = [t for t in self.points[sym] if t >= start][:limit]
        return httpx.Response(
            200,
            json=[{"symbol": sym, "fundingTime": t, "fundingRate": "0.0001"} for t in rows],
        )


def fclient(fake: FakeBinance) -> FundingClient:
    http = httpx.Client(transport=httpx.MockTransport(fake.handler), base_url="https://x")
    return FundingClient(http=http, min_interval_s=0.0, sleep=lambda _: None)


def test_pagina_hasta_el_final_y_arranca_en_una_fecha_real(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(binance_funding, "PAGE_LIMIT", 2)
    times = [binance_funding.EPOCH_MS + k * 28_800_000 for k in range(5)]
    fake = FakeBinance({"BTCUSDT": times})
    pts = fclient(fake).history("BTCUSDT")
    assert [p.funding_time for p in pts] == times
    assert fake.starts[0] == binance_funding.EPOCH_MS != 0
    assert len(fake.starts) == 3


def test_reintenta_429_y_falla_con_simbolo_invalido() -> None:
    fake = FakeBinance({"BTCUSDT": [binance_funding.EPOCH_MS]})
    fake.fail_next = [429, 503]
    c = fclient(fake)
    assert len(c.history("BTCUSDT")) == 1
    with pytest.raises(FundingError, match="400"):
        c.history("NOPEUSDT")


def test_ingesta_incremental(conn: sqlite3.Connection) -> None:
    t0 = binance_funding.EPOCH_MS
    fake = FakeBinance({"BTCUSDT": [t0, t0 + 1000], "ETHUSDT": [t0]})
    c = fclient(fake)
    first = ingest_funding.run(c, conn)
    assert [(r.status, r.new) for r in first] == [("nuevo", 2), ("nuevo", 1)]
    fake.points["BTCUSDT"].append(t0 + 2000)
    second = ingest_funding.run(c, conn)
    assert [(r.status, r.new, r.points) for r in second] == [
        ("actualizado", 1, 3),
        ("actualizado", 0, 1),
    ]
    assert fake.starts[-2] == t0 + 1001  # pide solo lo posterior
    broken = ingest_funding.run(c, conn, ("NOPEUSDT",))
    assert broken[0].status.startswith("error")
    ingest_funding.print_reports([*second, *broken], 4)
    assert store.last_funding_time(conn, "NOPEUSDT") is None
