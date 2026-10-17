"""Crecimiento por industria (M7): sectores de EE.UU., categorías DeFi y su ruta."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient

from bob.main import app
from marea import api, i18n, industries
from marea.data import ingest_industries, store
from marea.data.catalog import ETF_BY_TICKER, SECTOR_ETFS
from marea.data.tiingo import DailyBar
from marea.defi_categories import CategoryTvl, aggregate
from marea.strength import align

D0 = date(2026, 1, 1)


def _proto(
    cat: str | None, tvl: float, day: float | None, week: float | None, month: float | None
) -> dict[str, Any]:
    return {
        "category": cat,
        "tvl": tvl,
        "tvlPrevDay": day,
        "tvlPrevWeek": week,
        "tvlPrevMonth": month,
    }


# --- Agregado por categoría ------------------------------------------------------


def test_agregado_deja_fuera_cex_y_sin_categoria() -> None:
    cats = aggregate(
        [
            _proto("Lending", 100, 100, 100, 100),
            _proto("CEX", 1e6, 1e6, 1e6, 1e6),
            _proto(None, 50, 50, 50, 50),
            _proto("", 50, 50, 50, 50),
        ]
    )
    assert [c.category for c in cats] == ["Lending"]


def test_crecimiento_compara_solo_los_mismos_protocolos() -> None:
    # Un alta del listado sin valor de hace 30 días no se cuenta como dinero nuevo.
    cats = aggregate(
        [
            _proto("Dexs", 120, 110, 100, 100),
            _proto("Dexs", 500, 490, None, None),
        ]
    )
    (c,) = cats
    assert c.tvl == 620 and c.protocols == 2
    assert c.growth("month") == pytest.approx(0.20)
    assert c.tvl_unmatched_month == 500
    # A un día los dos tienen valor: entran los dos.
    assert c.growth("day") == pytest.approx(620 / 600 - 1)
    # Sin el filtro el crecimiento a 30 días sería 520%.
    assert c.growth("week") == pytest.approx(0.20)


def test_valores_rotos_no_suman() -> None:
    cats = aggregate(
        [
            _proto("Yield", 10, 0, -5, float("nan")),
            _proto("Yield", float("inf"), 1, 1, 1),
            _proto("Yield", True, 1, 1, 1),  # type: ignore[arg-type]
        ]
    )
    (c,) = cats
    assert c.tvl == 10 and c.protocols == 1
    assert c.growth("day") is None and c.growth("month") is None
    assert c.tvl_unmatched_month == 10


def test_orden_por_tvl() -> None:
    cats = aggregate([_proto("A", 1, 1, 1, 1), _proto("B", 3, 3, 3, 3), _proto("C", 2, 2, 2, 2)])
    assert [c.category for c in cats] == ["B", "C", "A"]


# --- Store y controles de la ingesta -------------------------------------------------


def _cat(name: str, tvl: float, protocols: int = 10, prev: float | None = None) -> CategoryTvl:
    p = tvl if prev is None else prev
    return CategoryTvl(name, tvl, protocols, tvl, p, tvl, p, tvl, p, 0.0)


def test_una_foto_por_dia_utc_y_la_ultima_reemplaza(conn: sqlite3.Connection) -> None:
    t = datetime(2026, 10, 17, 2, 0, tzinfo=UTC)
    store.save_defi_categories(conn, t, [_cat("A", 1), _cat("B", 2)])
    store.save_defi_categories(conn, t + timedelta(hours=5), [_cat("A", 5)])
    store.save_defi_categories(conn, t + timedelta(days=1), [_cat("A", 7)])
    snaps = store.load_defi_categories(conn)
    assert sorted(snaps) == [date(2026, 10, 17), date(2026, 10, 18)]
    at, rows = snaps[date(2026, 10, 17)]
    assert at == "2026-10-17T07:00:00+00:00"
    assert [(r.category, r.tvl) for r in rows] == [("A", 5.0)]


def test_dia_utc_y_no_local(conn: sqlite3.Connection) -> None:
    # 22:00 en Chile (UTC−3) ya es el día siguiente en UTC.
    from datetime import timezone

    local = datetime(2026, 10, 17, 22, 0, tzinfo=timezone(timedelta(hours=-3)))
    store.save_defi_categories(conn, local, [_cat("A", 1)])
    assert list(store.load_defi_categories(conn)) == [date(2026, 10, 18)]


def test_control_cambio_de_un_dia() -> None:
    ok = [_cat("A", 100, prev=90)]
    assert ingest_industries.check(ok, None) is None
    roto = [_cat("A", 100, prev=50)]
    assert "cambio de un día" in (ingest_industries.check(roto, None) or "")
    vacio = [_cat("A", 100, prev=0)]
    assert "ayer" in (ingest_industries.check(vacio, None) or "")


def test_control_listado_truncado() -> None:
    cats = [_cat("A", 100, protocols=850)]
    assert ingest_industries.check(cats, 900) is None
    assert "truncado" in (ingest_industries.check(cats, 1000) or "")


class _FakeClient:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.requests_made = 0

    def protocols(self) -> list[dict[str, Any]]:
        self.requests_made += 1
        return self.rows


def test_ingesta_guarda_y_rechaza_sin_tocar_lo_guardado(conn: sqlite3.Connection) -> None:
    t = datetime(2026, 10, 17, 12, tzinfo=UTC)
    good = [_proto("Lending", 100, 99, 95, 90)] * 20
    rep = ingest_industries.run(_FakeClient(good), conn, t)  # type: ignore[arg-type]
    assert rep.status == "guardada" and rep.snapshots == 1
    # Al día siguiente vienen la mitad de los protocolos: no se guarda.
    rep = ingest_industries.run(
        _FakeClient(good[:10]),
        conn,
        t + timedelta(days=1),  # type: ignore[arg-type]
    )
    assert rep.status.startswith("error") and "truncado" in rep.status
    assert list(store.load_defi_categories(conn)) == [date(2026, 10, 17)]


def test_ingesta_no_cae_si_la_fuente_falla(conn: sqlite3.Connection) -> None:
    class Broken:
        requests_made = 0

        def protocols(self) -> list[dict[str, Any]]:
            raise RuntimeError("HTTP 500")

    rep = ingest_industries.run(Broken(), conn, datetime.now(UTC))  # type: ignore[arg-type]
    assert rep.status.startswith("error") and "HTTP 500" in rep.status


# --- Sectores ------------------------------------------------------------------------


def _panel(rets: dict[str, list[float]]) -> Any:
    n = max(len(r) for r in rets.values())
    days = [D0 + timedelta(days=i) for i in range(n)]
    return align({t: (days[n - len(r) :], np.array(r, dtype=float)) for t, r in rets.items()})


def _sector_rets(n: int, seed: int = 0) -> dict[str, list[float]]:
    rng = np.random.default_rng(seed)
    tickers = ["SPY", *(s.ticker for s in SECTOR_ETFS)]
    return {t: list(rng.normal(0, 0.01, n)) for t in tickers}


def test_relativo_es_sector_menos_spy_y_la_linea_termina_en_la_tabla() -> None:
    rep = industries.build_equity(_panel(_sector_rets(200)), 13, table_weeks=(4, 13, 52))
    last = rep["series"][-1]
    for s in rep["sectors"]:
        assert last[s["ticker"]] == pytest.approx(s["ret_13s"], abs=1e-6)
        assert s["rel_13s"] == pytest.approx(
            s["ret_13s"] - rep["benchmark_by_weeks"]["13"], abs=2e-6
        )
        assert s["rel_window"] == pytest.approx(s["rel_13s"], abs=1e-6)
        # 52 semanas (260 sesiones) no caben en 200: sin cifra, no error.
        assert s["ret_52s"] is None and s["rel_52s"] is None
    assert rep["series"][0]["SPY"] == 0.0
    rets = [s["ret_window"] for s in rep["sectors"]]
    assert rets == sorted(rets, reverse=True)
    assert len(rep["sectors"]) == 11 and rep["missing"] == []


def test_sector_nuevo_queda_fuera_de_ventanas_largas() -> None:
    rets = _sector_rets(300)
    rets["XLC"] = rets["XLC"][-30:]
    rep = industries.build_equity(_panel(rets), 13, table_weeks=(4, 13))
    assert "XLC" in rep["excluded"]
    assert "XLC" not in {s["ticker"] for s in rep["sectors"]}


def test_sin_spy_no_hay_referencia() -> None:
    rets = _sector_rets(100)
    del rets["SPY"]
    with pytest.raises(ValueError, match="SPY"):
        industries.build_equity(_panel(rets), 13, table_weeks=(4,))


def test_sectores_fuera_del_universo_de_la_m3() -> None:
    # Si entraran a ETFS cambiarían la fuerza relativa por clase y el ranking M3.
    assert not {s.ticker for s in SECTOR_ETFS} & set(ETF_BY_TICKER)
    assert len({s.sector for s in SECTOR_ETFS}) == 11
    assert set(industries.SECTOR_LABEL) == {s.sector for s in SECTOR_ETFS}


# --- DeFi ----------------------------------------------------------------------------


def test_defi_sin_fotos_es_none() -> None:
    assert industries.build_defi({}) is None


def test_defi_agrupa_la_cola_en_otras_y_los_pesos_suman_uno() -> None:
    cats = [_cat(f"C{i}", 100 - i, prev=90 - i) for i in range(15)]
    snaps = {date(2026, 10, 16): ("t0", cats[:5]), date(2026, 10, 17): ("t1", cats)}
    rep = industries.build_defi(snaps, top=12)
    assert rep is not None
    rows = rep["categories"]
    assert len(rows) == 13 and rows[-1]["category"] == "Otras" and rows[-1]["other"]
    assert rows[-1]["members"] == 3 and rows[-1]["protocols"] == 30
    assert rows[-1]["tvl"] == pytest.approx(sum(100 - i for i in range(12, 15)))
    assert sum(r["share"] for r in rows) == pytest.approx(1.0, abs=1e-5)
    assert rows[0]["growth_30d"] == pytest.approx(100 / 90 - 1, abs=1e-6)
    # Historia: una fila por foto; una categoría que no estaba ese día queda en None.
    assert [h["day"] for h in rep["history"]] == ["2026-10-16", "2026-10-17"]
    assert rep["history"][0]["C9"] is None and rep["history"][1]["C9"] == 91
    assert rep["snapshots"] == 2 and rep["as_of"] == "2026-10-17"


def test_defi_con_pocas_categorias_no_inventa_otras() -> None:
    rep = industries.build_defi({date(2026, 10, 17): ("t", [_cat("A", 1), _cat("B", 2)])})
    assert rep is not None and [r["category"] for r in rep["categories"]] == ["A", "B"]


# --- Ruta ----------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "m.db"
    monkeypatch.setattr(api, "_db_path", lambda: path)
    return path


def _bars(conn: sqlite3.Connection, tickers: list[str], n: int) -> list[date]:
    rng = np.random.default_rng(3)
    days = [date(2025, 1, 1) + timedelta(days=i) for i in range(n)]
    for t in tickers:
        px = 100 * np.cumprod(1 + rng.normal(0, 0.01, n))
        store.upsert_bars(
            conn,
            t,
            [
                DailyBar(
                    day=d,
                    open=p,
                    high=p,
                    low=p,
                    close=p,
                    volume=1.0,
                    div_cash=0.0,
                    split_factor=1.0,
                    adj_close=p,
                )
                for d, p in zip(days, px, strict=True)
            ],
        )
    return days


def test_ruta_sin_base(db: Path) -> None:
    assert TestClient(app).get("/api/macro/industries").status_code == 404
    assert not db.exists()


def test_ruta_rechaza_ventanas_no_ofrecidas(db: Path) -> None:
    assert TestClient(app).get("/api/macro/industries?weeks=4").status_code == 422


def test_ruta_cada_mitad_falla_sola(db: Path) -> None:
    store.connect(db).close()
    body = TestClient(app).get("/api/macro/industries").json()
    assert body["equity"] is None and "SPY" in body["equity_error"]
    assert body["defi"] is None
    assert "no es flujo" in body["evidence"]


def test_ruta_calcula_y_traduce(db: Path) -> None:
    conn = store.connect(db)
    days = _bars(conn, ["SPY", "XLK", "XLE"], 300)
    store.save_defi_categories(
        conn, datetime(2026, 10, 17, 12, tzinfo=UTC), [_cat(f"C{i}", 20 - i) for i in range(14)]
    )
    conn.close()
    es = TestClient(app).get("/api/macro/industries?weeks=13").json()
    assert es["equity"]["as_of"] == days[-1].isoformat() and es["equity"]["stale"] is True
    assert {s["ticker"] for s in es["equity"]["sectors"]} == {"XLK", "XLE"}
    assert sorted(es["equity"]["missing"]) == sorted(
        s.ticker for s in SECTOR_ETFS if s.ticker not in {"XLK", "XLE"}
    )
    assert es["defi"]["categories"][-1]["category"] == "Otras"

    en = TestClient(app).get("/api/macro/industries?weeks=13&lang=en").json()
    assert {s["label"] for s in en["equity"]["sectors"]} == {"Technology", "Energy"}
    assert en["defi"]["categories"][-1]["category"] == "Other"
    assert en["evidence"].startswith("measured")
    # Ninguna cifra cambia con el idioma.
    assert [s["ret_13s"] for s in en["equity"]["sectors"]] == [
        s["ret_13s"] for s in es["equity"]["sectors"]
    ]


def test_traduccion_cubre_todos_los_sectores_y_el_metodo() -> None:
    assert set(i18n.SECTORS_EN) == {s.sector for s in SECTOR_ETFS}
    assert {s.ticker for s in SECTOR_ETFS if s.caveat} == set(i18n.SECTOR_CAVEATS_EN)
    assert set(i18n.INDUSTRIES_METHOD_EN) == set(industries.METHOD)
