"""COT de la CFTC: cliente, fecha de publicación, huecos por semana ISO e ingesta."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

from marea.data import ingest_cot, store
from marea.data.catalog import COT_BY_CODE, CotMarket
from marea.data.cftc import CftcClient, CftcError, available_date
from marea.data.ingest_cot import missing_weeks, sync_market

D = date.fromisoformat


def tff(
    day: str, lev_l: float = 10, lev_s: float = 5, name: str = "E-MINI S&P 500"
) -> dict[str, Any]:
    return {
        "report_date_as_yyyy_mm_dd": f"{day}T00:00:00.000",
        "market_and_exchange_names": name,
        "cftc_contract_market_code": "13874A",
        "open_interest_all": "100",
        "lev_money_positions_long": str(lev_l),
        "lev_money_positions_short": str(lev_s),
        "asset_mgr_positions_long": "50",
        "asset_mgr_positions_short": "20",
    }


def disagg(day: str) -> dict[str, Any]:
    return {
        "report_date_as_yyyy_mm_dd": f"{day}T00:00:00.000",
        "market_and_exchange_names": "GOLD - COMMODITY EXCHANGE INC.",
        "cftc_contract_market_code": "088691",
        "open_interest_all": "500",
        "m_money_positions_long_all": "200",
        "m_money_positions_short_all": "40",
    }


class FakeCftc:
    def __init__(self, rows: dict[str, list[dict[str, Any]]]) -> None:
        self.rows = rows
        self.fail_next: list[int] = []
        self.wheres: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.fail_next:
            return httpx.Response(self.fail_next.pop(0), text="busy")
        ds = request.url.path.rsplit("/", 1)[-1].removesuffix(".json")
        where = request.url.params["$where"]
        self.wheres.append(where)
        code = where.split("'")[1]
        since = where.split("'")[3][:10]
        rows = [
            r
            for r in self.rows.get(ds, [])
            if r["cftc_contract_market_code"] == code
            and r["report_date_as_yyyy_mm_dd"][:10] >= since
        ]
        off, lim = int(request.url.params["$offset"]), int(request.url.params["$limit"])
        return httpx.Response(200, json=rows[off : off + lim])


def client(fake: FakeCftc) -> CftcClient:
    http = httpx.Client(
        base_url="https://publicreporting.cftc.gov/resource",
        transport=httpx.MockTransport(fake.handler),
    )
    return CftcClient(http=http, min_interval_s=0.0, sleep=lambda _s: None)


def test_se_conoce_el_lunes_siguiente() -> None:
    assert available_date(D("2026-09-22")) == D("2026-09-28")


class TestClient:
    def test_tff_trae_especulador_e_institucional_filtrando_por_codigo(self) -> None:
        fake = FakeCftc({"gpe5-46if": [tff("2026-09-15"), tff("2026-09-22", 12, 4)]})
        rows = client(fake).positions("tff", "13874A", D("2026-09-20"))
        assert len(rows) == 1
        r = rows[0]
        assert (r.spec_long, r.spec_short, r.inst_long, r.inst_short) == (12, 4, 50, 20)
        assert r.available_date == D("2026-09-28")
        assert "cftc_contract_market_code='13874A'" in fake.wheres[0]

    def test_disagg_sin_institucional(self) -> None:
        rows = client(FakeCftc({"72hh-3qpy": [disagg("2026-09-22")]})).positions(
            "disagg", "088691", D("2006-01-01")
        )
        assert (rows[0].spec_long, rows[0].inst_long) == (200, None)

    def test_pagina(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from marea.data import cftc

        monkeypatch.setattr(cftc, "PAGE_LIMIT", 2)
        fake = FakeCftc({"gpe5-46if": [tff(f"2026-09-{d:02d}") for d in (1, 8, 15, 22, 29)]})
        assert len(client(fake).positions("tff", "13874A", D("2026-01-01"))) == 5

    def test_reintenta_y_luego_falla_con_mensaje(self) -> None:
        fake = FakeCftc({"gpe5-46if": [tff("2026-09-22")]})
        fake.fail_next = [503, 429]
        assert len(client(fake).positions("tff", "13874A", D("2026-01-01"))) == 1
        fake.fail_next = [400]
        with pytest.raises(CftcError, match="400"):
            client(fake).positions("tff", "13874A", D("2026-01-01"))


class TestMissingWeeks:
    def test_reporte_en_lunes_por_feriado_no_es_hueco(self) -> None:
        # Caso real: Navidad 2018 -> reportes del lunes 24 y lunes 31.
        ds = [D("2018-12-18"), D("2018-12-24"), D("2018-12-31"), D("2019-01-08")]
        assert missing_weeks(ds) == 0

    def test_semana_faltante_si_cuenta(self) -> None:
        assert missing_weeks([D("2026-09-01"), D("2026-09-15")]) == 1

    def test_serie_corta(self) -> None:
        assert missing_weeks([D("2026-09-01")]) == 0


class TestSync:
    def test_incremental_cuenta_nuevas_y_revisadas(self, conn: sqlite3.Connection) -> None:
        m = COT_BY_CODE["13874A"]
        fake = FakeCftc({"gpe5-46if": [tff("2026-09-08"), tff("2026-09-15")]})
        assert sync_market(client(fake), conn, m) == ("nuevo", 2, 0)
        # La CFTC corrige el reporte del 15 y publica el del 22.
        fake.rows["gpe5-46if"] = [tff("2026-09-08"), tff("2026-09-15", 11), tff("2026-09-22")]
        assert sync_market(client(fake), conn, m) == ("actualizado", 1, 1)
        assert [r.spec_long for r in store.load_cot(conn, "13874A")] == [10, 11, 10]

    def test_run_reporta_error_sin_tirar_la_corrida(self, conn: sqlite3.Connection) -> None:
        fake = FakeCftc({"gpe5-46if": [tff("2026-09-15"), tff("2026-09-22")]})
        bad = CotMarket("XXXXXX", "tff", "roto", None)
        good = COT_BY_CODE["13874A"]

        original = fake.handler

        def handler(request: httpx.Request) -> httpx.Response:
            if "XXXXXX" in request.url.params["$where"]:
                return httpx.Response(400, text="bad")
            return original(request)

        fake.handler = handler  # type: ignore[method-assign]
        reports = ingest_cot.run(client(fake), conn, [good, bad])
        assert reports[0].weeks == 2 and reports[0].gaps == 0
        assert reports[1].status.startswith("error: CftcError")


def test_cli_cot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from marea.data import ingest

    fake = FakeCftc({"gpe5-46if": [tff("2026-09-22")]})
    monkeypatch.setattr(ingest, "DB_PATH", tmp_path / "m.db")
    monkeypatch.setattr(ingest, "CftcClient", lambda: client(fake))
    assert ingest.main(["cot", "--codes", "13874A"]) == 0
    assert "0 con error" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        ingest.main(["cot", "--codes", "NOEXISTE"])
