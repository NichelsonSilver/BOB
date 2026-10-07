"""Fuerza relativa por clase (`marea.strength`) y sus dos rutas de la API."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from bob.main import app
from marea import api, strength
from marea.data import store
from marea.data.tiingo import DailyBar

D0 = date(2026, 1, 1)


def _days(n: int) -> list[date]:
    return [D0 + timedelta(days=i) for i in range(n)]


def _panel(rets: dict[str, list[float]]) -> strength.Panel:
    n = max(len(r) for r in rets.values())
    return strength.align(
        {t: (_days(n)[n - len(r) :], np.array(r, dtype=float)) for t, r in rets.items()}
    )


BLOCKS = {"A1": "a", "A2": "a", "B1": "b", "C1": "c"}


def _rets(n: int, seed: int = 0) -> dict[str, list[float]]:
    rng = np.random.default_rng(seed)
    return {t: list(rng.normal(0, 0.01, n)) for t in BLOCKS}


def test_el_ultimo_punto_de_la_linea_es_la_cifra_de_la_tabla() -> None:
    rep = strength.build(_panel(_rets(400)), BLOCKS, chart_weeks=13, table_weeks=(4, 13))
    last = rep["series"][-1]
    for c in rep["classes"]:
        assert last[c["block"]] == pytest.approx(c["rel_13s"], abs=1e-6)
        assert c["rel_window"] == pytest.approx(c["rel_13s"], abs=1e-6)
    assert rep["series"][0]["median"] == 0.0  # parte de 0%
    assert len(rep["series"]) == 5 * 13 + 1


def test_clase_es_promedio_de_miembros_contra_la_mediana() -> None:
    # 10 sesiones: A1 +1%/día, A2 0, B1 −1%/día, C1 +0,5%/día.
    n = 10
    rets = {"A1": [0.01] * n, "A2": [0.0] * n, "B1": [-0.01] * n, "C1": [0.005] * n}
    # Una sesión más al principio: su cierre es el punto de partida de la ventana.
    panel = _panel({t: [0.0, *r] for t, r in rets.items()})
    _, paths = strength.window_paths(panel, n)
    med, rel = strength.relative_paths(paths)
    tot = {t: (1 + r[0]) ** n - 1 for t, r in rets.items()}
    expected_med = float(np.median(list(tot.values())))
    assert med[-1] == pytest.approx(expected_med)
    cls = strength.class_paths(rel, BLOCKS)
    assert cls["a"][1][-1] == pytest.approx((tot["A1"] + tot["A2"]) / 2 - expected_med)
    assert sorted(cls["a"][0]) == ["A1", "A2"]


def test_un_etf_con_hueco_en_la_ventana_queda_fuera_sin_rellenar() -> None:
    rets = _rets(200)
    rets["C1"][-3] = float("nan")
    rep = strength.build(_panel(rets), BLOCKS, chart_weeks=13, table_weeks=(4,))
    assert rep["excluded"] == ["C1"]
    assert all(c["block"] != "c" for c in rep["classes"])


def test_un_etf_nuevo_no_entra_a_ventanas_mas_largas_que_su_historia() -> None:
    rets = _rets(400)
    rets["B1"] = rets["B1"][-30:]  # lanzado hace 30 sesiones
    rep = strength.build(_panel(rets), BLOCKS, chart_weeks=13, table_weeks=(4, 13))
    assert "B1" in rep["excluded"]
    assert all(c["block"] != "b" for c in rep["classes"])  # fuera del gráfico de 13 sem


def test_ventana_mas_larga_que_la_historia_falla_con_mensaje() -> None:
    with pytest.raises(ValueError, match="hacen falta"):
        strength.window_paths(_panel(_rets(20)), 65)


# --- API -------------------------------------------------------------------


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "m.db"
    monkeypatch.setattr(api, "_db_path", lambda: path)
    return path


def test_ruta_sin_base_dice_que_correr_y_no_la_crea(db: Path) -> None:
    res = TestClient(app).get("/api/macro/strength")
    assert res.status_code == 404 and "ingest etf" in res.json()["detail"]
    assert not db.exists()


def test_ruta_rechaza_ventanas_no_ofrecidas(db: Path) -> None:
    assert TestClient(app).get("/api/macro/strength?weeks=7").status_code == 422


def test_ruta_calcula_desde_la_base(db: Path) -> None:
    conn = store.connect(db)
    rng = np.random.default_rng(1)
    days = [date(2025, 1, 1) + timedelta(days=i) for i in range(300)]
    for t in ("SPY", "TLT", "GLD"):
        px = 100 * np.cumprod(1 + rng.normal(0, 0.01, len(days)))
        store.upsert_bars(
            conn,
            t,
            [
                DailyBar(
                    day=d, open=p, high=p, low=p, close=p, volume=1.0,
                    div_cash=0.0, split_factor=1.0, adj_close=p,
                )
                for d, p in zip(days, px, strict=True)
            ],
        )
    conn.close()
    body = TestClient(app).get("/api/macro/strength?weeks=26").json()
    assert body["as_of"] == days[-1].isoformat()
    assert {c["block"] for c in body["classes"]} == {"acciones", "renta_fija", "reserva_valor"}
    assert "no es flujo" in body["evidence"]
    assert body["stale"] is True


def test_veredicto_sale_del_artefacto(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = sorted(Path(api.ARTIFACTS).glob("marea-ranking-*.json"))
    if not real:
        pytest.skip("sin artefacto M3 versionado")
    art = json.loads(real[-1].read_text(encoding="utf-8"))
    body = TestClient(app).get("/api/macro/ranking-verdict").json()
    assert body["viable"] is art["primary"]["verdict"]["viable"]
    assert body["ic_mean"] == art["primary"]["verdict"]["c1_ic_mean"]
    assert body["placebo_ic_mean"] is not None
    monkeypatch.setattr(api, "_artifacts_dir", lambda: tmp_path)
    assert TestClient(app).get("/api/macro/ranking-verdict").status_code == 404
