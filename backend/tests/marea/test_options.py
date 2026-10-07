"""Opciones de Deribit: parser, agregación, cliente, ingesta y la corrida diaria."""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from marea.data import ingest, ingest_options, store
from marea.data.deribit import DeribitClient, DeribitError
from marea.options import parse_instrument, summarize

NOW = datetime(2026, 10, 7, 16, 0, tzinfo=UTC)


def test_parser_de_instrumentos() -> None:
    i = parse_instrument("BTC-27NOV26-73000-P")
    assert i.expiry == datetime(2026, 11, 27, 8, tzinfo=UTC)
    assert i.strike == 73000.0 and not i.is_call
    assert parse_instrument("ETH-8OCT26-1d5-C").strike == 1.5
    assert parse_instrument("ETH-8OCT26-2400-C").expiry.day == 8
    for bad in ("BTC-PERPETUAL", "BTC-27XXX26-1-C", "BTC-27NOV26-1-X"):
        with pytest.raises(ValueError):
            parse_instrument(bad)


def row(name: str, oi: float, vol: float = 0.0, px: float = 80_000.0) -> dict[str, Any]:
    return {"instrument_name": name, "open_interest": oi, "volume": vol, "underlying_price": px}


ROWS = [
    row("BTC-30OCT26-80000-C", 100.0, vol=10.0),  # cercano
    row("BTC-30OCT26-70000-P", 50.0, vol=20.0),  # cercano
    row("BTC-25DEC26-90000-C", 300.0),  # lejano
    row("BTC-25DEC26-60000-P", 150.0),  # lejano
    row("BTC-7OCT26-80000-C", 999.0),  # ya vencido a las 08:00 de hoy
    {"instrument_name": "BTC-25DEC26-1-P", "open_interest": None, "volume": float("nan")},
]


def test_agregacion_ignora_vencidos_y_separa_cercanos() -> None:
    s = summarize("BTC", ROWS, NOW)
    assert s.instruments == 5
    assert (s.oi_calls, s.oi_puts) == (400.0, 200.0)
    assert s.pc_oi == pytest.approx(0.5)
    assert s.pc_volume == pytest.approx(2.0)
    assert s.pc_oi_near == pytest.approx(0.5)
    assert s.oi_usd == pytest.approx(600.0 * 80_000.0)
    assert s.underlying == 80_000.0


def test_sin_calls_el_ratio_es_nan() -> None:
    s = summarize("BTC", [row("BTC-25DEC26-1-P", 5.0, px=0.0)], NOW)
    assert math.isnan(s.pc_oi) and math.isnan(s.underlying)


class FakeDeribit:
    def __init__(self) -> None:
        self.fail_next: list[int] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.fail_next:
            return httpx.Response(self.fail_next.pop(0))
        cur = request.url.params["currency"]
        if cur == "XXX":
            return httpx.Response(400, json={"error": {"message": "bad currency"}})
        if cur == "EMPTY":
            return httpx.Response(200, json={"result": []})
        return httpx.Response(200, json={"jsonrpc": "2.0", "result": ROWS[:4]})


def dclient(fake: FakeDeribit) -> DeribitClient:
    http = httpx.Client(transport=httpx.MockTransport(fake.handler), base_url="https://x")
    return DeribitClient(http=http, min_interval_s=0.0, sleep=lambda _: None)


def test_cliente_reintenta_y_falla_en_voz_alta() -> None:
    fake = FakeDeribit()
    fake.fail_next = [503, 429]
    c = dclient(fake)
    rows, raw = c.options_summary("BTC")
    assert len(rows) == 4 and json.loads(raw)["result"] == ROWS[:4]
    assert c.requests_made == 3
    with pytest.raises(DeribitError, match="400"):
        c.options_summary("XXX")
    with pytest.raises(DeribitError, match="sin instrumentos"):
        c.options_summary("EMPTY")


def test_cada_corrida_agrega_una_foto_y_guarda_el_crudo(conn: sqlite3.Connection) -> None:
    c = dclient(FakeDeribit())
    times = iter([NOW, NOW.replace(hour=17)])
    first = ingest_options.run(c, conn, ("BTC",), now=lambda: next(times))
    second = ingest_options.run(c, conn, ("BTC", "XXX"), now=lambda: next(times))
    assert first[0].total_snapshots == 1 and second[0].total_snapshots == 2
    assert second[1].status.startswith("error")
    snaps = store.load_options(conn, "BTC")
    assert [s.captured_at for s in snaps] == [NOW, NOW.replace(hour=17)]
    assert snaps[0].pc_oi == pytest.approx(0.5)
    raw = store.load_options_raw(conn, "BTC", NOW.isoformat(timespec="seconds"))
    assert raw is not None and json.loads(raw)["result"] == ROWS[:4]
    assert store.load_options_raw(conn, "BTC", "1999-01-01T00:00:00+00:00") is None
    ingest_options.print_reports([*first, *second], 3)


def test_corrida_diaria_sigue_aunque_una_fuente_falle(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(ingest, "_main_options", lambda: 0)
    monkeypatch.setattr(ingest, "_main_stable", lambda *_a: 1)
    monkeypatch.setattr(
        ingest, "_main_funding", lambda: (_ for _ in ()).throw(RuntimeError("sin red"))
    )
    assert ingest.main(["diario"]) == 1
    assert "1/3 fuentes sin error; fallaron: DefiLlama, Binance funding" in capsys.readouterr().out
    monkeypatch.setattr(ingest, "_main_stable", lambda *_a: 0)
    monkeypatch.setattr(ingest, "_main_funding", lambda: 0)
    assert ingest.main(["diario"]) == 0
