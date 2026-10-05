"""Registro de narrativa y brief del memo (fase M4)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from marea import brief, narrative

NOW = datetime(2026, 10, 5, 12, tzinfo=UTC)


def raw(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "published_at": "2026-10-04",
        "source": "Reserva Federal",
        "url": "https://example.org/fed",
        "primary_source": True,
        "claim": "La Fed anuncia el fin de la reducción de su balance en diciembre.",
        "kind": "hecho",
        "assets": ["TLT", "SPY"],
        "claimed_direction": "expansivo para liquidez",
        "manipulation_risk": "bajo",
        "risk_reasons": ["fuente primaria"],
        "data_check": {
            "series": "WALCL",
            "reading": "13s -0,85% al 2026-09-30",
            "source_command": "marea.monitor",
        },
        "divergence": True,
    }
    base.update(over)
    return base


def test_agrega_con_id_y_hora_y_solo_agrega(tmp_path: Path) -> None:
    reg = tmp_path / "narrativa" / "registro.jsonl"
    a = narrative.add(raw(), reg, now=NOW)
    b = narrative.add(raw(supersedes=a.id), reg, now=NOW)
    assert a.id == "N-20261005-0001" and b.id == "N-20261005-0002"
    lines = reg.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and json.loads(lines[0])["id"] == a.id
    assert [e.id for e in narrative.read(reg)] == [a.id, b.id]


@pytest.mark.parametrize(
    "bad",
    [
        {"id": "N-x"},
        {"manipulation_risk": "nulo"},
        {"assets": []},
        {"claim": "corto"},
        {"extra": 1},
        {"divergence": None},  # hay dato: no puede quedar sin contrastar
        {"data_check": {"series": "M2SL", "reading": "pendiente_datos"}, "divergence": False},
        {"supersedes": "N-19990101-0001"},
    ],
)
def test_rechaza_entradas_que_romperian_el_registro(tmp_path: Path, bad: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        narrative.add(raw(**bad), tmp_path / "r.jsonl", now=NOW)
    assert narrative.read(tmp_path / "r.jsonl") == []


def test_pendiente_datos_con_divergencia_nula_es_valida(tmp_path: Path) -> None:
    e = narrative.add(
        raw(data_check={"series": "M2 China", "reading": "pendiente_datos"}, divergence=None),
        tmp_path / "r.jsonl",
        now=NOW,
    )
    assert "sin contrastar" in narrative.render([e])


def test_recent_y_linea_corrupta(tmp_path: Path) -> None:
    reg = tmp_path / "r.jsonl"
    narrative.add(raw(), reg, now=datetime(2026, 9, 1, tzinfo=UTC))
    narrative.add(raw(), reg, now=NOW)
    assert len(narrative.recent(narrative.read(reg), 7, now=NOW)) == 1
    assert narrative.render([]).startswith("sin noticias")
    reg.write_text(reg.read_text(encoding="utf-8") + '{"roto": true}\n', encoding="utf-8")
    with pytest.raises(ValueError, match=r"r\.jsonl:3"):
        narrative.read(reg)


def test_cli_add_y_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    reg = tmp_path / "r.jsonl"
    monkeypatch.setattr(narrative, "REGISTRY", reg)
    monkeypatch.setattr(narrative.add, "__defaults__", (reg, None))
    monkeypatch.setattr(narrative.read, "__defaults__", (reg,))
    f = tmp_path / "n.json"
    f.write_text(json.dumps(raw()), encoding="utf-8")
    assert narrative.main(["add", "--file", str(f)]) == 0
    f.write_text(json.dumps(raw(kind="rumor")), encoding="utf-8")
    assert narrative.main(["add", "--file", str(f)]) == 2
    assert narrative.main(["list", "--days", "3650"]) == 0
    out = capsys.readouterr()
    assert "registrada N-" in out.out and "rechazada" in out.err and "DIVERGE" in out.out


def _bob_db(path: Path, rows: list[tuple[int, int]]) -> None:
    c = sqlite3.connect(path)
    c.execute(
        "CREATE TABLE panelforecastrecord "
        "(open_time INTEGER, is_backfill INTEGER, probability REAL)"
    )
    for ot, bf in rows:
        c.execute("INSERT INTO panelforecastrecord VALUES (?, ?, 0.5)", (ot, bf))
    c.commit()
    c.close()


def test_estado_del_forward_cuenta_validas_backfill_y_huecos(tmp_path: Path) -> None:
    day = 86_400_000
    t0 = int(datetime(2026, 9, 23, tzinfo=UTC).timestamp() * 1000)
    db = tmp_path / "bob.db"
    _bob_db(db, [(t0, 1), (t0 + 3 * day, 0), (t0 + 4 * day, 0), (t0 + 5 * day, 1)])
    st = brief.panel_forward_status(db, date(2026, 10, 1))
    assert st is not None
    assert st["valid"] == 2 and st["decision_dates"] == 6
    assert st["never_emitted"] == ["2026-09-24", "2026-09-25"]
    assert st["eta_if_every_day_valid"] == "2027-03-28"  # 01-10 + 178 días
    assert "no se reporta" in st["ic"]  # ciego: nunca toca probability
    assert brief.panel_forward_status(tmp_path / "no.db", date(2026, 10, 1)) is None


def test_el_brief_no_lee_resultados_del_forward() -> None:
    src = Path(brief.__file__).read_text(encoding="utf-8")
    query = src[src.index("def panel_forward_status") :]
    query = query[: query.index("def build")]
    assert "probability" not in query and "cross_section_rank" not in query


def test_brief_de_punta_a_punta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from datetime import timedelta

    from marea.data import store
    from marea.data.cftc import CotRow
    from marea.data.tiingo import DailyBar

    mdb = tmp_path / "marea.db"
    conn = store.connect(mdb)
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(200)]
    for i, t in enumerate(("SPY", "TLT")):
        store.upsert_bars(
            conn,
            t,
            [DailyBar(d, 1, 1, 1, 100 + k * (i + 1) * 0.1, 1, 0, 1, 1) for k, d in enumerate(days)],
        )
    store.upsert_cot(conn, [CotRow("088691", days[-7], days[-1], "Oro", 10, 6, 3, None, None)])
    store.upsert_bcch(conn, "F019.PPB.PRE.100.D", [(days[-1], days[-1], 4.5)])
    conn.execute(
        "INSERT INTO fred_series VALUES ('WALCL','t','W','u','2002-12-18',?,'x',NULL,NULL,8,'x')",
        (days[-1].isoformat(),),
    )
    ind = {
        "key": "fed_net",
        "name": "Fed neta",
        "obs_date": "2026-07-01",
        "chg_13w": 0.01,
        "pct": 0.7,
        "label": "expansivo",
        "in_composite": True,
        "group": "nucleo",
    }
    chile = {**ind, "key": "cobre", "name": "Cobre (BML)", "in_composite": False, "group": "chile"}
    rep = {
        "as_of": "2026-07-19",
        "regime": {
            "label": "expansivo",
            "composite_pct": 0.8,
            "since": "2026-07-01",
            "label_4w_ago": "neutro",
            "label_13w_ago": "neutro",
            "persistence": {"median_run_weeks": 3.0},
        },
        "indicators": [ind, chile],
    }
    store.save_liquidity_report(conn, "2026-07-19", json.dumps(rep))
    conn.close()

    arts = tmp_path / "artifacts"
    arts.mkdir()
    (arts / "marea-power-1.json").write_text(
        json.dumps(
            {"variants": {"primaria": {"effective_n_demeaned": {"participation_ratio": 6.7}}}}
        )
    )
    (arts / "marea-ranking-1.json").write_text(
        json.dumps(
            {
                "run_id": "marea-ranking-1",
                "primary": {
                    "verdict": {
                        "viable": False,
                        "reading": "no hay orden",
                        "c1_ic_mean": 0.03,
                        "c1_ic_ci95": [-0.01, 0.07],
                    }
                },
            }
        )
    )
    reg = tmp_path / "r.jsonl"
    narrative.add(raw(), reg, now=datetime.now(UTC))

    monkeypatch.setattr(brief, "DB_PATH", mdb)
    monkeypatch.setattr(brief, "ARTIFACTS", arts)
    monkeypatch.setattr(brief, "BOB_DB", tmp_path / "no.db")
    monkeypatch.setattr(narrative.read, "__defaults__", (reg,))
    monkeypatch.setattr("marea.brief.ETFS", [e for e in brief.ETFS if e.ticker in ("SPY", "TLT")])

    b = brief.build(today=date(2026, 8, 1), bob_db=tmp_path / "no.db")
    assert b["liquidity"]["label"] == "expansivo" and b["ranking_m3"]["viable"] is False
    assert b["panel_forward"] is None and len(b["narrative_7d"]) == 1
    rows = {r["ticker"]: r for r in b["relative_strength"]["rows"]}
    assert rows["TLT"]["rel_13s"] > 0 > rows["SPY"]["rel_13s"]
    text = brief.render(b)
    assert "NO VIABLE" in text and "más de 8 días" in text and "DIVERGE" in text
    monkeypatch.setattr(brief, "build", lambda: b)
    assert brief.main(["--json"]) == 0 and '"ranking_m3"' in capsys.readouterr().out
