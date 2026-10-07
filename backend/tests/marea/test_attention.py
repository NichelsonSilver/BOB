"""Contraste de narrativa (M5.7): TVL, volumen DEX, Wikipedia y su lectura."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from marea.attention import attention_row, reading
from marea.data import ingest, ingest_attention, store
from marea.data.defillama import DefiLlamaClient, DefiLlamaError
from marea.data.wikimedia import Article, WikimediaClient, WikimediaError, user_agent

D = date.fromisoformat
TODAY = D("2026-10-07")


def ts(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp())


# --- Lectura -----------------------------------------------------------------------


def test_media_cambio_y_percentil_del_ultimo_ano() -> None:
    days = [D("2025-01-01") + timedelta(days=i) for i in range(500)]
    series = [(d, 100.0 + i) for i, d in enumerate(days)]  # crece siempre
    r = attention_row("x", series)
    assert r is not None and r.as_of == days[-1]
    assert r.mean_7d == pytest.approx(100.0 + 499 - 3)
    assert r.change_30d == pytest.approx(r.mean_7d / (r.mean_7d - 30) - 1)
    assert r.pct_1y == pytest.approx(1.0)  # el máximo del último año
    assert reading(r) == "alta para el último año"


def test_sin_historia_suficiente() -> None:
    short = [(D("2026-01-01") + timedelta(days=i), 1.0) for i in range(20)]
    r = attention_row("x", short)
    assert r is not None and r.pct_1y is None and r.change_30d is None
    assert reading(r) == "sin historia suficiente"
    assert attention_row("x", short[:3]) is None


def test_lecturas_baja_y_en_rango() -> None:
    days = [D("2025-01-01") + timedelta(days=i) for i in range(400)]
    falling = [(d, 1000.0 - i) for i, d in enumerate(days)]
    low = attention_row("x", falling)
    assert low is not None and reading(low) == "baja para el último año"
    saw = [(D("2025-01-01") + timedelta(days=i), float(i % 100)) for i in range(350)]
    flat = attention_row("x", saw)  # termina a mitad de un diente: percentil medio
    assert flat is not None and reading(flat) == "en rango del último año"


# --- DefiLlama: TVL y DEX ----------------------------------------------------------


def llama_handler(request: httpx.Request) -> httpx.Response:
    days = [TODAY - timedelta(days=k) for k in (2, 1, 0)]  # el último es el día en curso
    if request.url.path.endswith("historicalChainTvl"):
        return httpx.Response(
            200, json=[{"date": ts(d), "tvl": 1e9 + k} for k, d in enumerate(days)]
        )
    if request.url.path.endswith("overview/dexs"):
        assert request.url.params["dataType"] == "dailyVolume"
        return httpx.Response(200, json={"totalDataChart": [[ts(d), 5e8] for d in days]})
    return httpx.Response(404)


def llama(handler: Any = llama_handler) -> DefiLlamaClient:
    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://x")
    return DefiLlamaClient(http=http, min_interval_s=0.0, sleep=lambda _: None)


def test_tvl_y_dex_excluyen_el_dia_en_curso() -> None:
    c = llama()
    assert [d for d, _ in c.chain_tvl(TODAY)] == [
        TODAY - timedelta(days=2),
        TODAY - timedelta(days=1),
    ]
    assert len(c.dex_volume(TODAY)) == 2


def test_tvl_y_dex_vacios_son_error() -> None:
    c = llama(lambda r: httpx.Response(200, json=[] if "Tvl" in r.url.path else {}))
    with pytest.raises(DefiLlamaError):
        c.chain_tvl(TODAY)
    with pytest.raises(DefiLlamaError):
        c.dex_volume(TODAY)


# --- Wikimedia ---------------------------------------------------------------------


class FakeWiki:
    def __init__(self, views: dict[str, list[tuple[date, int]]]) -> None:
        self.views = views
        self.fail_next: list[int] = []
        self.paths: list[str] = []
        self.agents: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        self.agents.append(request.headers.get("user-agent", ""))
        if self.fail_next:
            return httpx.Response(self.fail_next.pop(0), text="robot policy")
        parts = request.url.path.split("/")
        title, start, end = parts[-4], parts[-2], parts[-1]
        if title not in self.views:
            return httpx.Response(404)
        lo, hi = datetime.strptime(start, "%Y%m%d").date(), datetime.strptime(end, "%Y%m%d").date()
        items = [
            {"timestamp": f"{d:%Y%m%d}00", "views": v}
            for d, v in self.views[title]
            if lo <= d <= hi
        ]
        return httpx.Response(200, json={"items": items})


def wiki(fake: FakeWiki, contact: str = "contacto@ejemplo.org") -> WikimediaClient:
    http = httpx.Client(
        transport=httpx.MockTransport(fake.handler),
        base_url="https://x",
        headers={"User-Agent": user_agent(contact)},
    )
    return WikimediaClient(contact, http=http, min_interval_s=0.0, sleep=lambda _: None)


def test_wikimedia_exige_contacto_y_lo_manda() -> None:
    with pytest.raises(WikimediaError, match="WIKI_CONTACT"):
        WikimediaClient("")
    fake = FakeWiki({"Bitcoin": [(D("2026-10-01"), 10)]})
    out = wiki(fake).daily_views(
        Article("en.wikipedia", "Bitcoin"), D("2026-09-30"), D("2026-10-02")
    )
    assert out == [(D("2026-10-01"), 10)]
    assert "contacto@ejemplo.org" in fake.agents[0]


def test_wikimedia_404_vacio_403_error_y_reintento() -> None:
    fake = FakeWiki({})
    c = wiki(fake)
    art = Article("en.wikipedia", "Nada")
    assert c.daily_views(art, D("2026-01-01"), D("2026-01-02")) == []
    fake.fail_next = [403]
    with pytest.raises(WikimediaError, match="403"):
        c.daily_views(art, D("2026-01-01"), D("2026-01-02"))
    fake.fail_next = [429]
    assert c.daily_views(art, D("2026-01-01"), D("2026-01-02")) == []


# --- Ingesta -----------------------------------------------------------------------


def test_ingesta_defi_y_wiki_incremental(conn: sqlite3.Connection) -> None:
    defi = ingest_attention.run_defi(llama(), conn, TODAY)
    assert [(r.series_id, r.status, r.observations) for r in defi] == [
        ("DEFI_TVL", "nuevo", 2),
        ("DEX_VOLUME", "nuevo", 2),
    ]
    days = [D("2026-09-20") + timedelta(days=i) for i in range(17)]  # hasta el 10-06
    fake = FakeWiki({"Bitcoin": [(d, 100 + i) for i, d in enumerate(days)]})
    arts = (Article("en.wikipedia", "Bitcoin"), Article("en.wikipedia", "Nada"))
    c = wiki(fake)
    first = ingest_attention.run_wiki(c, conn, TODAY, arts)
    assert (first[0].status, first[0].days, first[0].last) == ("nuevo", 17, D("2026-10-06"))
    assert first[1].days == 0  # sin datos no es error
    assert fake.paths[0].endswith("/20150701/20261006")  # pide hasta ayer, desde el inicio
    fake.views["Bitcoin"].append((D("2026-10-07"), 999))
    second = ingest_attention.run_wiki(c, conn, TODAY + timedelta(days=1), arts[:1])
    assert (second[0].status, second[0].new) == ("actualizado", 1)
    assert fake.paths[-1].endswith("/20261003/20261007")  # solape de 3 días
    fake.fail_next = [403]
    broken = ingest_attention.run_wiki(c, conn, TODAY, arts[:1])
    assert broken[0].status.startswith("error")
    bad = ingest_attention.run_defi(llama(lambda r: httpx.Response(500)), conn, TODAY)
    assert all(r.status.startswith("error") for r in bad)
    ingest_attention.print_reports([*defi, *bad], [*first, *broken], 9)
    assert store.load_wiki(conn, "en.wikipedia:Bitcoin")[-1] == (D("2026-10-07"), 999)


def test_corrida_sin_contacto_salta_wikipedia_sin_fallar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(ingest, "DB_PATH", tmp_path / "m.db")
    monkeypatch.setattr(ingest, "DefiLlamaClient", lambda: llama())
    monkeypatch.setattr(ingest, "MareaSettings", lambda: SimpleNamespace(wiki_contact=None))
    assert ingest.main(["atencion"]) == 0
    assert "Wikipedia omitida" in capsys.readouterr().out
