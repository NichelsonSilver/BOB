"""Tiingo, retorno total e ingesta de ETFs."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest

from marea.data import ingest_etf, store
from marea.data.ingest_etf import adj_close_gap_pct, calendar_gaps, sync_ticker
from marea.data.tiingo import DailyBar, TiingoClient, TiingoError
from marea.returns import total_returns

D = date.fromisoformat


def row(
    day: str, close: float, div: float = 0.0, split: float = 1.0, adj: float | None = None
) -> dict[str, Any]:
    return {
        "date": f"{day}T00:00:00.000Z",
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1000,
        "adjClose": adj if adj is not None else close,
        "adjHigh": close,
        "adjLow": close,
        "adjOpen": close,
        "adjVolume": 1000,
        "divCash": div,
        "splitFactor": split,
    }


class FakeTiingo:
    def __init__(self, data: dict[str, list[dict[str, Any]]]) -> None:
        self.data = data
        self.fail_next: list[int] = []
        self.starts: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.fail_next:
            return httpx.Response(self.fail_next.pop(0), json={"detail": "slow down"})
        if request.headers.get("Authorization") != "Token k":
            return httpx.Response(401, json={"detail": "Invalid token."})
        ticker = request.url.path.split("/")[-2]
        if ticker not in self.data:
            return httpx.Response(404, json={"detail": f"Ticker '{ticker}' not found"})
        start = request.url.params["startDate"]
        self.starts.append(start)
        return httpx.Response(200, json=[r for r in self.data[ticker] if r["date"][:10] >= start])


def client(fake: FakeTiingo, token: str = "k") -> TiingoClient:
    http = httpx.Client(
        base_url="https://api.tiingo.com/tiingo",
        transport=httpx.MockTransport(fake.handler),
        headers={"Authorization": f"Token {token}"},
    )
    return TiingoClient(token, http=http, min_interval_s=0.0, sleep=lambda _s: None)


def adj_series(rows: list[tuple[str, float, float, float]]) -> list[dict[str, Any]]:
    """Filas con adjClose coherente con la convención aditiva (la de Tiingo)."""
    close = np.array([r[1] for r in rows])
    div = np.array([r[2] for r in rows])
    split = np.array([r[3] for r in rows])
    growth = np.cumprod(np.r_[1.0, 1.0 + total_returns(close, div, split)[1:]])
    return [row(d, c, dv, sp, adj=float(g)) for (d, c, dv, sp), g in zip(rows, growth, strict=True)]


class TestTotalReturns:
    def test_dividendo_se_suma_al_cierre(self) -> None:
        r = total_returns(np.array([100.0, 99.5]), np.array([0.0, 0.5]), np.array([1.0, 1.0]))
        assert np.isnan(r[0]) and r[1] == pytest.approx(0.0)

    def test_split_escala_a_la_base_de_ayer(self) -> None:
        # Split inverso 1:8 (USO 2020): el precio se multiplica por 8 y split=1/8.
        r = total_returns(np.array([2.0, 16.0]), np.array([0.0, 0.0]), np.array([1.0, 0.125]))
        assert r[1] == pytest.approx(0.0)

    def test_serie_vacia_o_de_un_punto(self) -> None:
        one = total_returns(np.array([1.0]), np.array([0.0]), np.array([1.0]))
        assert one.shape == (1,) and np.isnan(one[0])


class TestClient:
    def test_parsea_las_barras(self) -> None:
        bars = client(FakeTiingo({"TLT": [row("2026-09-01", 81.87, div=0.314686)]})).daily(
            "TLT", D("2026-01-01")
        )
        assert bars == [
            DailyBar(D("2026-09-01"), 81.87, 81.87, 81.87, 81.87, 1000.0, 0.314686, 1.0, 81.87)
        ]

    def test_429_espera_y_reintenta(self) -> None:
        fake = FakeTiingo({"SPY": [row("2026-09-25", 700.0)]})
        fake.fail_next = [429, 503]
        assert len(client(fake).daily("SPY", D("2026-01-01"), D("2026-12-31"))) == 1

    def test_error_con_detalle(self) -> None:
        with pytest.raises(TiingoError, match="not found"):
            client(FakeTiingo({})).daily("NADA", D("2026-01-01"))

    def test_token_invalido(self) -> None:
        with pytest.raises(TiingoError, match="Invalid token"):
            client(FakeTiingo({"SPY": []}), token="malo").daily("SPY", D("2026-01-01"))

    def test_sin_key_falla_al_construir(self) -> None:
        with pytest.raises(TiingoError, match="API_TIINGO"):
            TiingoClient("")

    def test_espacia_los_requests(self) -> None:
        waits: list[float] = []
        http = httpx.Client(
            base_url="https://api.tiingo.com/tiingo",
            transport=httpx.MockTransport(FakeTiingo({"SPY": []}).handler),
            headers={"Authorization": "Token k"},
        )
        c = TiingoClient("k", http=http, min_interval_s=10.0, sleep=waits.append)
        c.daily("SPY", D("2026-01-01"))
        c.daily("SPY", D("2026-01-01"))
        assert len(waits) == 1 and waits[0] > 9.0


class TestSync:
    def test_incremental_con_solape_cuenta_revisiones(self, conn: sqlite3.Connection) -> None:
        fake = FakeTiingo({"SPY": [row("2026-09-01", 100.0), row("2026-09-02", 101.0)]})
        assert sync_ticker(client(fake), conn, "SPY") == ("nuevo", 2, 0)
        # Tiingo corrige el cierre del 02 y agrega el 03.
        fake.data["SPY"] = [
            row("2026-09-01", 100.0),
            row("2026-09-02", 101.5),
            row("2026-09-03", 102.0),
        ]
        assert sync_ticker(client(fake), conn, "SPY") == ("actualizado", 1, 1)
        assert (
            fake.starts[-1]
            == (D("2026-09-02") - timedelta(days=ingest_etf.OVERLAP_DAYS)).isoformat()
        )
        assert [b.close for b in store.load_bars(conn, "SPY")] == [100.0, 101.5, 102.0]


class TestControls:
    def test_calendario_del_universo(self) -> None:
        days = [D("2026-09-01") + timedelta(days=i) for i in range(4)]
        gaps = calendar_gaps(
            {"A": days, "B": days, "C": [days[0], days[2], days[3]], "D": days[2:]}
        )
        # C falta un día hábil; D arranca tarde y eso no es hueco.
        assert gaps == {"A": 0, "B": 0, "C": 1, "D": 0}

    def test_adjclose_identidad(self) -> None:
        rows = adj_series(
            [
                ("2026-09-01", 100.0, 0.0, 1.0),
                ("2026-09-02", 99.0, 0.5, 1.0),
                ("2026-09-03", 50.0, 0.0, 2.0),
            ]
        )
        bars = client(FakeTiingo({"X": rows})).daily("X", D("2026-01-01"))
        gap = adj_close_gap_pct(bars)
        assert gap is not None and abs(gap) < ingest_etf.ADJ_TOLERANCE_PCT
        assert adj_close_gap_pct(bars[:1]) is None


class TestRun:
    def test_split_mal_leido_sale_con_error(self, conn: sqlite3.Connection) -> None:
        good = adj_series([("2026-09-01", 100.0, 0.0, 1.0), ("2026-09-02", 50.0, 0.0, 2.0)])
        bad = [dict(r) for r in good]
        bad[1]["splitFactor"] = 1.0  # Tiingo omitió el split: parece una caída de 50%
        fake = FakeTiingo({"OK": good, "MAL": bad})
        reports = {r.ticker: r for r in ingest_etf.run(client(fake), conn, ["OK", "MAL", "NADA"])}
        assert reports["OK"].status == "nuevo"
        assert "no cuadra con adjClose" in reports["MAL"].status
        assert reports["NADA"].status.startswith("error: TiingoError")

    def test_cli_etf(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from marea.data import ingest

        fake = FakeTiingo(
            {"SPY": adj_series([("2026-09-01", 100.0, 0.0, 1.0), ("2026-09-02", 101.0, 0.0, 1.0)])}
        )
        monkeypatch.setattr(ingest, "DB_PATH", tmp_path / "m.db")
        monkeypatch.setattr(ingest, "TiingoClient", lambda _key: client(fake))
        assert ingest.main(["etf", "--tickers", "SPY"]) == 0
        assert "0 con error" in capsys.readouterr().out
        with pytest.raises(SystemExit):
            ingest.main(["etf", "--tickers", "INVENTADO"])
