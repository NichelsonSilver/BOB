"""Monitor de liquidez, capa de I/O (`marea.monitor`) sobre una DB sintética."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

from marea import monitor
from marea.data import store
from marea.data.cftc import CotRow
from marea.pit import OPEN_END, Period

D = date.fromisoformat
START = D("2003-01-01")
WEEKS = 520  # ~10 años: alcanza para 3 años de señal + 3 de percentil + 3 del compuesto

FRED = {
    "WALCL": 2e6,
    "WTREGEN": 1e5,
    "RRPONTSYD": 50.0,
    "ECBASSETSW": 1e6,
    "DEXUSEU": 1.2,
    "JPNASSETS": 1e6,
    "DEXJPUS": 110.0,
    "M2SL": 6000.0,
    "DTWEXBGS": 100.0,
    "DFII10": 1.5,
    "NFCI": -0.3,
    "WRESBAL": 1e6,
    "BAMLH0A0HYM2": 4.0,
    "T10Y2Y": 1.0,
    "VIXCLS": 18.0,
}
BCCH = [
    "F022.TPM.TIN.D001.NO.Z.D",
    "F073.TCO.PRE.Z.D",
    "F019.PPB.PRE.100.D",
    "F022.BCLP.TIS.AN10.NO.Z.D",
]


def _walk(rng: np.random.Generator, base: float) -> np.ndarray:
    return base * np.exp(np.cumsum(rng.normal(0, 0.01, WEEKS)))


@pytest.fixture
def db(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    monkeypatch.setattr(monitor, "FED_NET_START", D("2003-06-01"))
    rng = np.random.default_rng(7)
    days = [START + timedelta(weeks=i) for i in range(WEEKS)]
    for sid, base in FRED.items():
        vals = _walk(rng, base)
        periods = [Period(d, d + timedelta(days=3), OPEN_END, float(v)) for d, v in zip(days, vals)]
        store.replace_periods(conn, sid, periods)
        store.upsert_series_meta(
            conn,
            {
                "id": sid,
                "title": sid,
                "frequency_short": "W",
                "units_short": "u",
                "observation_start": days[0].isoformat(),
                "observation_end": days[-1].isoformat(),
                "last_updated": "x",
            },
            first_vintage=START,
            last_vintage=days[-1],
            fallback_lag_days=3,
        )
    for sid in BCCH:
        vals = _walk(rng, 10.0)
        store.upsert_bcch(conn, sid, [(d, d, float(v)) for d, v in zip(days, vals)])
    rows = [
        CotRow("088691", d, d + timedelta(days=6), "Oro", 1000.0, 600.0 + i % 50, 300.0, None, None)
        for i, d in enumerate(days)
    ]
    store.upsert_cot(conn, rows)
    return conn


AS_OF = D("2012-12-21")


def test_reporte_completo(db: sqlite3.Connection) -> None:
    rep = monitor.build(db, AS_OF)
    assert rep["as_of"] == AS_OF.isoformat()
    assert rep["method"]["forecast"] is False
    assert rep["regime"]["label"] in ("expansivo", "neutro", "contractivo")
    keys = [i["key"] for i in rep["indicators"]]
    assert keys == [i.key for i in monitor.INDICATORS]
    nucleo = [i for i in rep["indicators"] if i["in_composite"]]
    assert {i["key"] for i in nucleo} == {"fed_net", "m2", "dolar", "tasa_real", "nfci"}
    assert all(i["pct"] is not None for i in nucleo)
    pend = next(i for i in rep["indicators"] if i["key"] == "pendiente")
    assert pend["label"] is None  # signo ambiguo: no se clasifica
    assert rep["cot"][0]["name"] == "Oro" and rep["cot"][0]["pct_3y"] is not None
    text = monitor.render(rep)
    assert "sin pronóstico" in text and "Liquidez neta" in text


def test_reconstruir_una_fecha_reproduce_la_historia(db: sqlite3.Connection) -> None:
    """La invariante de point-in-time: lo que el reporte de hoy dice de una
    fecha pasada es exactamente lo que se habría dicho ese día."""
    full = monitor.build(db, AS_OF)
    cut = D("2011-06-17")
    past = monitor.build(db, cut)
    by_date = {h["date"]: h for h in full["history"]}
    assert past["history"] and all(h == by_date[h["date"]] for h in past["history"])


def test_liquidez_neta_no_existe_antes_de_su_inicio(
    db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(monitor, "FED_NET_START", D("2010-01-01"))
    rep = monitor.build(db, AS_OF)
    first = next(h["date"] for h in rep["history"] if h["fed_net"] is not None)
    assert first >= "2010-01-01"


def test_serie_faltante_avisa_que_ingestar(conn: sqlite3.Connection) -> None:
    with pytest.raises(LookupError, match="marea.data.ingest fred"):
        monitor.build(conn, AS_OF)


def test_cli_guarda_solo_el_vigente(
    db: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "cli.db"
    backup = sqlite3.connect(path)
    db.backup(backup)
    backup.close()
    monkeypatch.setattr(monitor, "DB_PATH", path)

    # Una reconstrucción histórica no pisa el reporte que sirve la API.
    assert monitor.main(["--as-of", "2012-06-15"]) == 0
    c = store.connect(path)
    assert store.latest_liquidity_report(c) is None
    c.close()

    class _Fixed(monitor.datetime):  # type: ignore[misc,name-defined]
        @classmethod
        def now(cls, tz=None):  # type: ignore[no-untyped-def]
            return monitor.datetime(2012, 12, 21, 12, tzinfo=tz)

    monkeypatch.setattr(monitor, "datetime", _Fixed)
    assert monitor.main([]) == 0
    c = store.connect(path)
    saved = store.latest_liquidity_report(c)
    c.close()
    assert saved is not None and json.loads(saved)["as_of"] == "2012-12-21"
    assert "guardado" in capsys.readouterr().out
