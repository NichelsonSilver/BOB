"""BCCh: codificación ISO-8859-1, errores con HTTP 200, ND e ingesta."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from marea.data import ingest_bcch, store
from marea.data.bcch import BcchClient, BcchError
from marea.data.catalog import BCCH_BY_ID, BcchSeries

D = date.fromisoformat
DOLAR = BCCH_BY_ID["F073.TCO.PRE.Z.D"]
BCP10 = BCCH_BY_ID["F022.BCLP.TIS.AN10.NO.Z.D"]


def obs(day: str, value: float | None) -> dict[str, str]:
    d = D(day).strftime("%d-%m-%Y")
    if value is None:
        return {"indexDateString": d, "value": "NaN", "statusCode": "ND"}
    return {"indexDateString": d, "value": str(value), "statusCode": "OK"}


class FakeBcch:
    def __init__(self, series: dict[str, list[dict[str, str]]]) -> None:
        self.series = series
        self.fail_next: list[int] = []
        self.firsts: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.fail_next:
            return httpx.Response(self.fail_next.pop(0))
        q = request.url.params
        if q["pass"] != "clave":
            body: dict[str, Any] = {"Codigo": -5, "Descripcion": "Invalid username or password"}
        else:
            sid = q["timeseries"]
            first = datetime.strptime(q["firstdate"], "%Y-%m-%d").date()
            self.firsts.append(q["firstdate"])
            rows = [
                o
                for o in self.series.get(sid, [])
                if datetime.strptime(o["indexDateString"], "%d-%m-%Y").date() >= first
            ]
            body = {
                "Codigo": 0,
                "Descripcion": "Success",
                "Series": {
                    "descripEsp": "Tipo de cambio nominal (dólar observado)",
                    "seriesId": sid,
                    "Obs": rows,
                },
            }
        # El BCCh responde en ISO-8859-1: el test lo reproduce byte a byte.
        return httpx.Response(
            200,
            content=json.dumps(body, ensure_ascii=False).encode("latin-1"),
            headers={"content-type": "application/json; charset=ISO-8859-1"},
        )


def client(fake: FakeBcch, password: str = "clave") -> BcchClient:
    http = httpx.Client(transport=httpx.MockTransport(fake.handler))
    return BcchClient(
        "yo@correo.cl", password, http=http, min_interval_s=0.0, sleep=lambda _s: None
    )


class TestClient:
    def test_lee_latin1_y_separa_nd(self) -> None:
        fake = FakeBcch(
            {
                DOLAR.id: [
                    obs("2026-09-17", 954.85),
                    obs("2026-09-18", None),
                    obs("2026-09-21", 958.42),
                ]
            }
        )
        data = client(fake).series(DOLAR.id, D("2026-09-01"), D("2026-09-30"))
        assert data.title.startswith("Tipo de cambio nominal (dólar")
        assert [(o.day, o.value) for o in data.obs] == [
            (D("2026-09-17"), 954.85),
            (D("2026-09-21"), 958.42),
        ]
        assert data.no_data_days == [D("2026-09-18")]

    def test_error_con_http_200_se_detecta_por_codigo(self) -> None:
        with pytest.raises(BcchError, match="-5: Invalid username"):
            client(FakeBcch({}), password="mala").series(DOLAR.id, D("2026-09-01"), D("2026-09-30"))

    def test_reintenta_5xx_y_falla_con_4xx(self) -> None:
        fake = FakeBcch({DOLAR.id: [obs("2026-09-17", 1.0)]})
        fake.fail_next = [503]
        assert len(client(fake).series(DOLAR.id, D("2026-09-01"), D("2026-09-30")).obs) == 1
        fake.fail_next = [403]
        with pytest.raises(BcchError, match="HTTP 403"):
            client(fake).series(DOLAR.id, D("2026-09-01"), D("2026-09-30"))

    def test_sin_credenciales_falla_al_construir(self) -> None:
        with pytest.raises(BcchError, match="BCCH_USER"):
            BcchClient("", "")


class TestSync:
    def test_rezago_declarado_incremental_y_revisiones(self, conn: sqlite3.Connection) -> None:
        today = D("2026-09-22")
        fake = FakeBcch({BCP10.id: [obs("2026-09-17", 6.03), obs("2026-09-21", 6.0)]})
        assert ingest_bcch.sync_series(client(fake), conn, BCP10, today)[:3] == ("nuevo", 2, 0)
        # BCP 10a: la tasa del día d se usa desde d+1.
        assert store.load_bcch(conn, BCP10.id)[0] == (D("2026-09-17"), D("2026-09-18"), 6.03)

        fake.series[BCP10.id] = [
            obs("2026-09-17", 6.03),
            obs("2026-09-21", 6.01),
            obs("2026-09-22", 5.97),
        ]
        assert ingest_bcch.sync_series(client(fake), conn, BCP10, today)[:3] == (
            "actualizado",
            1,
            1,
        )
        assert (
            fake.firsts[-1]
            == (D("2026-09-21") - timedelta(days=ingest_bcch.OVERLAP_DAYS)).isoformat()
        )

    def test_nd_habiles_cuenta_solo_lunes_a_viernes_hasta_hoy(
        self, conn: sqlite3.Connection
    ) -> None:
        rows = [
            obs("2026-09-18", None),
            obs("2026-09-19", None),
            obs("2026-09-21", 958.42),
            obs("2026-09-29", None),
        ]
        _s, _n, _r, nd = ingest_bcch.sync_series(
            client(FakeBcch({DOLAR.id: rows})), conn, DOLAR, D("2026-09-27")
        )
        assert nd == 1  # viernes 18; el sábado 19 y el 29 (futuro) no cuentan

    def test_run_no_se_cae_por_una_serie(self, conn: sqlite3.Connection) -> None:
        fake = FakeBcch({DOLAR.id: [obs("2026-09-21", 958.42)]})
        rota = BcchSeries("NO.EXISTE", "rota", "2020-01-01", 0, "test")
        original = fake.handler

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.params["timeseries"] == "NO.EXISTE":
                return httpx.Response(500)
            return original(request)

        fake.handler = handler  # type: ignore[method-assign]
        c = BcchClient(
            "yo@correo.cl",
            "clave",
            http=httpx.Client(transport=httpx.MockTransport(fake.handler)),
            min_interval_s=0.0,
            max_retries=0,
            sleep=lambda _s: None,
        )
        reports = ingest_bcch.run(c, conn, [DOLAR, rota], D("2026-09-27"))
        assert reports[0].obs == 1 and reports[1].status.startswith("error: BcchError")


def test_cli_bcch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from marea.data import ingest

    fake = FakeBcch({DOLAR.id: [obs("2026-09-21", 958.42)]})
    monkeypatch.setattr(ingest, "DB_PATH", tmp_path / "m.db")
    monkeypatch.setattr(ingest, "BcchClient", lambda _u, _p: client(fake))
    assert ingest.main(["bcch", "--series", DOLAR.id]) == 0
    assert "0 con error" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        ingest.main(["bcch", "--series", "INVENTADA"])
