"""Stablecoins: vintages propias, cliente DefiLlama, ingesta y derivadas."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest

from marea.data import ingest_stable, store
from marea.data.catalog import STABLE_BY_ID, STABLE_SERIES
from marea.data.defillama import DefiLlamaClient, DefiLlamaError
from marea.pit import OPEN_END, Period, apply_snapshot
from marea.stablecoins import btc_supply, known_series, net_mints, ssr

D = date.fromisoformat


def ts(day: str) -> str:
    return str(int(datetime.combine(D(day), datetime.min.time(), UTC).timestamp()))


# --- apply_snapshot: vintages construidas --------------------------------------


def test_snapshot_inicial_abre_un_periodo_por_observacion() -> None:
    periods, diff = apply_snapshot(
        [], [(D("2026-01-01"), 1.0), (D("2026-01-02"), 2.0)], D("2026-01-10")
    )
    assert diff.new == 2 and diff.revised == 0 and diff.removed == 0
    assert all(p.realtime_start == D("2026-01-10") and p.realtime_end == OPEN_END for p in periods)


def test_valor_igual_no_crea_periodo_y_el_ruido_de_redondeo_tampoco() -> None:
    first, _ = apply_snapshot([], [(D("2026-01-01"), 100.0)], D("2026-01-10"))
    again, diff = apply_snapshot(first, [(D("2026-01-01"), 100.0 * (1 + 1e-12))], D("2026-01-11"))
    assert diff.revised == 0
    assert again == first


def test_revision_cierra_el_vigente_y_abre_otro() -> None:
    first, _ = apply_snapshot([], [(D("2026-01-01"), 100.0)], D("2026-01-10"))
    second, diff = apply_snapshot(first, [(D("2026-01-01"), 110.0)], D("2026-01-12"))
    assert diff.revised == 1
    assert diff.max_revision == pytest.approx(0.10)
    assert second == [
        Period(D("2026-01-01"), D("2026-01-10"), D("2026-01-11"), 100.0),
        Period(D("2026-01-01"), D("2026-01-12"), OPEN_END, 110.0),
    ]


def test_segunda_corrida_del_mismo_dia_reemplaza_sin_periodo_vacio() -> None:
    first, _ = apply_snapshot([], [(D("2026-01-01"), 100.0)], D("2026-01-10"))
    second, diff = apply_snapshot(first, [(D("2026-01-01"), 105.0)], D("2026-01-10"))
    assert diff.revised == 1
    assert second == [Period(D("2026-01-01"), D("2026-01-10"), OPEN_END, 105.0)]


def test_lo_que_la_fuente_saca_se_cierra_y_no_se_borra() -> None:
    first, _ = apply_snapshot([], [(D("2026-01-01"), 1.0), (D("2026-01-02"), 2.0)], D("2026-01-10"))
    second, diff = apply_snapshot(first, [(D("2026-01-01"), 1.0)], D("2026-01-12"))
    assert diff.removed == 1
    gone = [p for p in second if p.obs_date == D("2026-01-02")]
    assert gone == [Period(D("2026-01-02"), D("2026-01-10"), D("2026-01-11"), 2.0)]
    # Si vuelve, cuenta como revisión y no como dato nuevo.
    third, diff3 = apply_snapshot(
        second, [(D("2026-01-01"), 1.0), (D("2026-01-02"), 2.5)], D("2026-01-15")
    )
    assert diff3.revised == 1 and diff3.new == 0
    assert [p for p in third if p.obs_date == D("2026-01-02")][-1].value == 2.5


def test_una_fecha_pasada_se_lee_con_lo_que_se_sabia_entonces() -> None:
    """El test que decide: sin esto, DefiLlama reescribiendo su historia sería lookahead."""
    first, _ = apply_snapshot([], [(D("2026-01-01"), 100.0)], D("2026-01-10"))
    second, _ = apply_snapshot(
        first, [(D("2026-01-01"), 150.0), (D("2026-01-11"), 7.0)], D("2026-01-12")
    )
    before = known_series(second, D("2026-01-11"))
    after = known_series(second, D("2026-01-12"))
    assert [(p.obs_date, p.value, p.exact) for p in before] == [(D("2026-01-01"), 100.0, True)]
    assert [(p.value, p.exact) for p in after] == [(150.0, True), (7.0, True)]


def test_antes_de_la_primera_corrida_es_no_exacto_con_un_dia_de_rezago() -> None:
    periods, _ = apply_snapshot(
        [], [(D("2025-06-01"), 1.0), (D("2025-06-02"), 2.0)], D("2026-01-10")
    )
    pts = known_series(periods, D("2025-06-02"))
    assert [(p.obs_date, p.exact) for p in pts] == [(D("2025-06-01"), False)]
    assert known_series([], D("2025-06-02")) == []


# --- Cliente ----------------------------------------------------------------------


class FakeLlama:
    def __init__(self) -> None:
        self.total: list[dict[str, Any]] = [
            {
                "date": ts("2026-01-01"),
                "totalCirculatingUSD": {"peggedUSD": 100.0, "peggedEUR": 5.0},
            },
            {"date": ts("2026-01-02"), "totalCirculatingUSD": {"peggedUSD": 110.0}},
            # Fila del día en curso: parcial, tiene que quedar afuera.
            {"date": ts("2026-01-03"), "totalCirculatingUSD": {"peggedUSD": 999.0}},
        ]
        self.tokens: dict[str, list[dict[str, Any]]] = {}
        self.fail_next: list[int] = []
        self.calls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.fail_next:
            return httpx.Response(self.fail_next.pop(0))
        sid = request.url.params.get("stablecoin")
        if sid is None:
            return httpx.Response(200, json=self.total)
        if sid not in self.tokens:
            return httpx.Response(404)
        return httpx.Response(200, json=self.tokens[sid])


def client_for(fake: FakeLlama) -> DefiLlamaClient:
    http = httpx.Client(transport=httpx.MockTransport(fake.handler), base_url="https://x")
    return DefiLlamaClient(http=http, min_interval_s=0.0, sleep=lambda _: None)


def test_total_excluye_el_dia_en_curso_y_separa_usd_de_todas() -> None:
    fake = FakeLlama()
    out = client_for(fake).total(today=D("2026-01-03"))
    assert out["usd"] == [(D("2026-01-01"), 100.0), (D("2026-01-02"), 110.0)]
    assert out["all"] == [(D("2026-01-01"), 105.0), (D("2026-01-02"), 110.0)]


def test_reintenta_5xx_y_falla_en_voz_alta_ante_un_404() -> None:
    fake = FakeLlama()
    fake.fail_next = [503, 429]
    c = client_for(fake)
    assert len(c.total(D("2026-01-03"))["usd"]) == 2
    assert fake.calls == 3
    with pytest.raises(DefiLlamaError, match="404"):
        c.token(99, D("2026-01-03"))


def test_respuesta_sin_datos_en_usd_es_error() -> None:
    fake = FakeLlama()
    fake.total = [{"date": ts("2026-01-01"), "totalCirculatingUSD": {"peggedEUR": 1.0}}]
    fake.tokens["7"] = [{"date": ts("2026-01-01"), "totalCirculatingUSD": {}}]
    c = client_for(fake)
    with pytest.raises(DefiLlamaError):
        c.total(D("2026-01-03"))
    with pytest.raises(DefiLlamaError):
        c.token(7, D("2026-01-03"))


# --- Ingesta ----------------------------------------------------------------------


def test_ingesta_registra_cada_corrida_y_las_revisiones(conn: sqlite3.Connection) -> None:
    fake = FakeLlama()
    fake.tokens["1"] = [
        {"date": ts("2026-01-01"), "totalCirculatingUSD": {"peggedUSD": 60.0}},
        {"date": ts("2026-01-02"), "totalCirculatingUSD": {"peggedUSD": 61.0}},
    ]
    series = [STABLE_BY_ID["STABLE_USD"], STABLE_BY_ID["STABLE_ALL"], STABLE_BY_ID["USDT"]]
    c = client_for(fake)
    first = ingest_stable.run(c, conn, series, D("2026-01-03"))
    assert [r.status for r in first] == ["nuevo"] * 3
    assert first[0].observations == 2 and first[0].last_obs == D("2026-01-02")
    assert fake.calls == 2  # los dos totales salen de una sola request

    fake.total[1] = {"date": ts("2026-01-02"), "totalCirculatingUSD": {"peggedUSD": 120.0}}
    second = ingest_stable.run(c, conn, series[:1], D("2026-01-04"))
    assert second[0].status == "actualizado"
    assert second[0].revised == 1 and second[0].runs == 2
    runs = store.stable_runs(conn, "STABLE_USD")
    assert [r["revised"] for r in runs] == [0, 1]
    known = known_series(store.load_stable_periods(conn, "STABLE_USD"), D("2026-01-03"))
    assert known[-1].value == 110.0  # lo que se sabía antes de la revisión


def test_una_serie_rota_no_tira_la_corrida(conn: sqlite3.Connection) -> None:
    fake = FakeLlama()
    reports = ingest_stable.run(
        client_for(fake), conn, [STABLE_BY_ID["USDT"], STABLE_BY_ID["STABLE_USD"]], D("2026-01-03")
    )
    assert reports[0].status.startswith("error")
    assert reports[1].status == "nuevo"
    ingest_stable.print_reports(reports, 2)


def test_catalogo_tiene_totales_y_los_muertos() -> None:
    ids = {s.id for s in STABLE_SERIES}
    assert {"STABLE_USD", "STABLE_ALL", "UST", "BUSD"} <= ids
    assert len({s.llama_id for s in STABLE_SERIES if s.llama_id is not None}) == len(ids) - 2


# --- Derivadas --------------------------------------------------------------------


def test_net_mints_promedia_la_ventana_completa_y_no_cruza_huecos() -> None:
    days = [D("2026-01-01") + timedelta(days=i) for i in range(6)]
    series = [(d, 100.0 + 10 * i) for i, d in enumerate(days) if i != 3]
    out = net_mints(series, window=2)
    # Día 3 falta: no hay variación en el 3 ni en el 4, y la ventana del 5 tampoco cierra.
    assert out == [(days[2], 10.0)]
    assert net_mints([(days[0], 1.0), (days[1], 3.0)], window=1) == [(days[1], 2.0)]
    with pytest.raises(ValueError):
        net_mints(series, window=0)


def test_btc_supply_clava_los_halvings_y_falla_despues_del_quinto() -> None:
    assert btc_supply(D("2008-12-31")) == 0.0
    assert btc_supply(D("2016-07-09")) == 15_750_000.0
    assert btc_supply(D("2024-04-20")) == 19_687_500.0
    assert btc_supply(D("2024-04-21")) == pytest.approx(19_687_500.0 + 450.0)
    mid = btc_supply(D("2018-06-01"))
    assert 15_750_000.0 < mid < 18_375_000.0
    with pytest.raises(ValueError, match="halving"):
        btc_supply(D("2028-01-01"))


def test_ssr_solo_en_dias_con_las_dos_series() -> None:
    d1, d2, d3 = D("2025-01-01"), D("2025-01-02"), D("2025-01-03")
    out = ssr([(d1, 100_000.0), (d2, float("nan")), (d3, 90_000.0)], [(d1, 200e9), (d2, 200e9)])
    assert len(out) == 1
    assert out[0][1] == pytest.approx(100_000.0 * btc_supply(d1) / 200e9)
