"""Cliente FRED e ingesta contra un servidor falso que reproduce el recorte real.

El servidor guarda la VERDAD (períodos de tiempo real sin recortar) y responde
cada ventana como FRED: filtra los períodos que la tocan y recorta sus bordes.
La prueba que decide es que bajar en bloques, o incrementalmente, reconstruye
la verdad exacta.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

from marea.data import fred as fred_mod
from marea.data import store
from marea.data.fred import FredClient, FredError
from marea.data.ingest import sync_series
from marea.pit import OPEN_END, Period

D = date.fromisoformat


def per(obs: str, start: str, end: str, value: float | None) -> Period:
    return Period(D(obs), D(start), D(end), value)


TRUTH = [
    # obs anterior a ALFRED: su inicio es la 1ª vintage
    per("2010-01-01", "2011-01-05", "2011-03-01", 10.0),
    per("2010-01-01", "2011-03-02", OPEN_END.isoformat(), 10.5),
    per("2011-02-01", "2011-02-10", "2011-02-20", 20.0),
    per("2011-02-01", "2011-02-21", "2011-04-04", None),
    per("2011-02-01", "2011-04-05", OPEN_END.isoformat(), 21.0),
    per("2011-03-01", "2011-03-15", OPEN_END.isoformat(), 30.0),
]


class FakeFred:
    def __init__(self, truth: list[Period], last_updated: str = "2026-09-24 07:00:00-05") -> None:
        self.truth = truth
        self.last_updated = last_updated
        self.calls: list[str] = []
        self.fail_next: list[int] = []

    def vintages(self) -> list[date]:
        return sorted({p.realtime_start for p in self.truth})

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/fred")
        self.calls.append(path)
        if self.fail_next:
            return httpx.Response(self.fail_next.pop(0), json={})
        q = dict(request.url.params)
        if q.get("api_key") != "k":
            return httpx.Response(400, json={"error_message": "Bad Request. api_key not valid"})
        if path == "/series":
            return httpx.Response(
                200,
                json={
                    "seriess": [
                        {
                            "id": q["series_id"],
                            "title": "t",
                            "frequency_short": "M",
                            "units_short": "u",
                            "observation_start": "2010-01-01",
                            "observation_end": "2011-03-01",
                            "last_updated": self.last_updated,
                        }
                    ]
                },
            )
        rs, re_ = D(q["realtime_start"]), D(q["realtime_end"])
        limit, offset = int(q.get("limit", 100000)), int(q.get("offset", 0))
        if path == "/series/vintagedates":
            vs = [v.isoformat() for v in self.vintages() if rs <= v <= re_]
            return httpx.Response(
                200, json={"count": len(vs), "vintage_dates": vs[offset : offset + limit]}
            )
        if path == "/series/observations":
            rows = [
                {
                    "date": p.obs_date.isoformat(),
                    "realtime_start": max(p.realtime_start, rs).isoformat(),
                    "realtime_end": min(p.realtime_end, re_).isoformat(),
                    "value": "." if p.value is None else str(p.value),
                }
                for p in sorted(self.truth, key=lambda p: (p.obs_date, p.realtime_start))
                if p.realtime_start <= re_ and p.realtime_end >= rs
            ]
            return httpx.Response(
                200, json={"count": len(rows), "observations": rows[offset : offset + limit]}
            )
        return httpx.Response(404, json={"error_message": "not found"})


def client(fake: FakeFred, **kw: Any) -> FredClient:
    http = httpx.Client(
        base_url="https://api.stlouisfed.org/fred", transport=httpx.MockTransport(fake.handler)
    )
    return FredClient("k", http=http, min_interval_s=0.0, sleep=lambda _s: None, **kw)


def canon(ps: list[Period]) -> list[Period]:
    return sorted(ps, key=lambda p: (p.obs_date, p.realtime_start))


class TestClient:
    def test_una_sola_ventana_reconstruye_la_verdad(self) -> None:
        assert canon(client(FakeFred(TRUTH)).periods("X")) == canon(TRUTH)

    @pytest.mark.parametrize("chunk", [1, 2, 3])
    def test_en_bloques_reconstruye_la_verdad(
        self, chunk: int, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(fred_mod, "VINTAGE_CHUNK", chunk)
        assert canon(client(FakeFred(TRUTH)).periods("X")) == canon(TRUTH)

    def test_paginacion(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(fred_mod, "PAGE_LIMIT", 2)
        assert canon(client(FakeFred(TRUTH)).periods("X")) == canon(TRUTH)

    def test_reintenta_429_y_5xx(self) -> None:
        fake = FakeFred(TRUTH)
        fake.fail_next = [429, 503]
        assert client(fake).series_info("X")["id"] == "X"

    def test_se_rinde_tras_los_reintentos(self) -> None:
        fake = FakeFred(TRUTH)
        fake.fail_next = [503] * 3
        with pytest.raises(httpx.HTTPStatusError):
            client(fake, max_retries=2).series_info("X")

    def test_error_de_la_api_sale_con_su_mensaje(self) -> None:
        http = httpx.Client(
            base_url="https://api.stlouisfed.org/fred",
            transport=httpx.MockTransport(FakeFred(TRUTH).handler),
        )
        c = FredClient("mala", http=http, min_interval_s=0.0, sleep=lambda _s: None)
        with pytest.raises(FredError, match="api_key not valid"):
            c.series_info("X")

    def test_sin_key_falla_al_construir(self) -> None:
        with pytest.raises(FredError, match="API_FRED"):
            FredClient("")

    def test_espacia_los_requests(self) -> None:
        waits: list[float] = []
        http = httpx.Client(
            base_url="https://api.stlouisfed.org/fred",
            transport=httpx.MockTransport(FakeFred(TRUTH).handler),
        )
        c = FredClient("k", http=http, min_interval_s=10.0, sleep=waits.append)
        c.series_info("X")
        c.series_info("X")
        assert len(waits) == 1 and waits[0] > 9.0


class TestSync:
    def test_primera_corrida_persiste_la_verdad_y_mide_el_rezago(
        self, conn: sqlite3.Connection
    ) -> None:
        r = sync_series(client(FakeFred(TRUTH)), conn, "X")
        assert r.status == "nuevo"
        assert canon(store.load_periods(conn, "X")) == canon(TRUTH)
        assert r.first_vintage == D("2011-01-05")
        # 2011-02-01 publicado 2011-02-10 (9 d); 2011-03-01 el 2011-03-15 (14 d)
        assert r.fallback_lag_days == 14
        assert r.observations == 3 and r.missing == 0

    def test_sin_cambios_no_pide_observaciones(self, conn: sqlite3.Connection) -> None:
        fake = FakeFred(TRUTH)
        sync_series(client(fake), conn, "X")
        fake.calls.clear()
        r = sync_series(client(fake), conn, "X")
        assert r.status == "sin cambios"
        assert fake.calls == ["/series"]

    def test_incremental_equivale_a_bajar_todo(self, conn: sqlite3.Connection) -> None:
        old = [p for p in TRUTH if p.realtime_start <= D("2011-03-02")]
        # Lo que se veía el 2011-03-02: los períodos vigentes estaban abiertos.
        seen = [
            p
            if p.realtime_end < D("2011-03-02")
            else Period(p.obs_date, p.realtime_start, OPEN_END, p.value)
            for p in old
        ]
        sync_series(client(FakeFred(seen, last_updated="v1")), conn, "X")
        r = sync_series(client(FakeFred(TRUTH, last_updated="v2")), conn, "X")
        assert r.status == "actualizado"
        assert canon(store.load_periods(conn, "X")) == canon(TRUTH)

    def test_full_rehace(self, conn: sqlite3.Connection) -> None:
        sync_series(client(FakeFred(TRUTH)), conn, "X")
        r = sync_series(client(FakeFred(TRUTH)), conn, "X", full=True)
        assert r.status == "rehecho"
        assert canon(store.load_periods(conn, "X")) == canon(TRUTH)

    def test_faltante_vigente_se_reporta(self, conn: sqlite3.Connection) -> None:
        truth = [*TRUTH, per("2011-02-15", "2011-03-20", OPEN_END.isoformat(), None)]
        assert sync_series(client(FakeFred(truth)), conn, "X").missing == 1


def test_el_json_de_series_meta_es_legible(conn: sqlite3.Connection) -> None:
    sync_series(client(FakeFred(TRUTH)), conn, "X")
    meta = store.series_meta(conn, "X")
    assert meta is not None
    assert json.loads(json.dumps(meta))["fallback_lag_days"] == 14
    assert store.series_meta(conn, "NADA") is None


class TestCli:
    def _patch(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake: FakeFred) -> None:
        from marea.data import ingest

        monkeypatch.setattr(ingest, "DB_PATH", tmp_path / "m.db")
        monkeypatch.setattr(ingest, "FredClient", lambda _key: client(fake))

    def test_corre_el_catalogo_pedido_y_reporta(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from marea.data.ingest import main

        self._patch(monkeypatch, tmp_path, FakeFred(TRUTH))
        assert main(["fred", "--series", "M2SL", "NFCI"]) == 0
        out = capsys.readouterr().out
        assert "M2SL" in out and "NFCI" in out and "0 con error" in out

    def test_una_serie_rota_no_tira_la_corrida_pero_sale_con_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from marea.data.ingest import main

        fake = FakeFred(TRUTH)
        original = fake.handler

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.params.get("series_id") == "NFCI":
                return httpx.Response(400, json={"error_message": "series does not exist"})
            return original(request)

        fake.handler = handler  # type: ignore[method-assign]
        self._patch(monkeypatch, tmp_path, fake)
        assert main(["fred", "--series", "M2SL", "NFCI"]) == 1
        out = capsys.readouterr().out
        assert "1 con error" in out and "series does not exist" in out

    def test_excepcion_inesperada_queda_reportada(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from marea.data.ingest import main

        fake = FakeFred(TRUTH)
        fake.handler = lambda _r: httpx.Response(200, json={"seriess": []})  # type: ignore[method-assign,assignment]
        self._patch(monkeypatch, tmp_path, fake)
        assert main(["fred", "--series", "M2SL"]) == 1
        assert "IndexError" in capsys.readouterr().out

    def test_rechaza_series_fuera_del_catalogo(self) -> None:
        from marea.data.ingest import main

        with pytest.raises(SystemExit):
            main(["fred", "--series", "INVENTADA"])


def test_todo_sigue_aunque_una_fuente_falle(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from marea.data import ingest

    monkeypatch.setattr(ingest, "_main_fred", lambda *_a, **_k: 0)
    monkeypatch.setattr(
        ingest, "_main_etf", lambda *_a: (_ for _ in ()).throw(RuntimeError("sin red"))
    )
    monkeypatch.setattr(ingest, "_main_cot", lambda *_a: 1)
    monkeypatch.setattr(ingest, "_main_bcch", lambda *_a: 0)
    monkeypatch.setattr(ingest, "_main_stable", lambda *_a: 0)
    assert ingest.main(["todo"]) == 1
    out = capsys.readouterr().out
    assert "3/5 fuentes sin error; fallaron: Tiingo, CFTC" in out
    monkeypatch.setattr(ingest, "_main_etf", lambda *_a: 0)
    monkeypatch.setattr(ingest, "_main_cot", lambda *_a: 0)
    assert ingest.main(["todo"]) == 0
