"""Tests del archivo histórico de Binance — parseo, agregación y cliente.

La red se simula con un transport de httpx: los tests no tocan
data.binance.vision, así que son deterministas y no dependen del CDN.

Los CSV de ejemplo copian el formato real verificado el 2026-08-24 contra
`ETHUSDT-metrics-2026-08-20.zip` y `ETHUSDT-bookDepth-2026-08-22.zip`.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date

import numpy as np
import pytest
from httpx import AsyncClient, MockTransport, Request, Response

from bob.data.binance_rest import INTERVAL_MS
from bob.data.store import (
    book_depth_coverage,
    book_depth_day_counts,
    load_book_depth,
    upsert_book_depth,
)
from bob.data.vision import (
    DEPTH_LEVELS,
    BookDepthAggregate,
    DepthSnapshot,
    VisionClient,
    VisionError,
    _day_from_key,
    _ms,
    aggregate_book_depth,
    daterange,
    object_key,
    parse_book_depth_csv,
    parse_metrics_csv,
)

METRICS_HEADER = (
    "create_time,symbol,sum_open_interest,sum_open_interest_value,"
    "count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,"
    "count_long_short_ratio,sum_taker_long_short_vol_ratio"
)

BOOK_HEADER = "timestamp,percentage,depth,notional"


def _metrics_csv(rows: list[str]) -> bytes:
    return ("\n".join([METRICS_HEADER, *rows])).encode("utf-8")


def _metrics_row(ts: str, oi: str = "2321075.748", symbol: str = "ETHUSDT") -> str:
    return f"{ts},{symbol},{oi},5263224944.65,1.32284313,1.389674,2.42720496,1.049553"


#: Formato moderno (con near-touch) y el anterior a ~2026-01-15 (sin él).
NIVELES_CON_NEAR = (-5.0, -1.0, -0.2, 0.2, 1.0, 5.0)
NIVELES_SIN_NEAR = (-5.0, -1.0, 1.0, 5.0)


def _book_csv(
    timestamps: list[str], notional: float = 1000.0, niveles=NIVELES_CON_NEAR
) -> bytes:
    lines = [BOOK_HEADER]
    for ts in timestamps:
        for pct in niveles:
            # Notional creciente con la distancia: el archivo es acumulado.
            scale = abs(pct)
            lines.append(f"{ts},{pct:.2f},{scale:.8f},{notional * scale:.8f}")
    return "\n".join(lines).encode("utf-8")


def _zip_bytes(name: str, payload: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, payload)
    return buffer.getvalue()


# --------------------------------------------------------------------------- #
# Parseo de metrics
# --------------------------------------------------------------------------- #


def test_parse_metrics_mapea_las_ocho_columnas():
    points = parse_metrics_csv(_metrics_csv([_metrics_row("2026-08-20 02:10:00")]), "ETHUSDT")

    assert len(points) == 1
    p = points[0]
    # Verificado por una ruta independiente de fromisoformat: calendar.timegm.
    assert p.timestamp == 1787191800000  # 2026-08-20T02:10:00Z
    assert p.open_interest == "2321075.748"
    assert p.open_interest_value == "5263224944.65"
    assert p.long_short_ratio == "2.42720496"
    assert p.taker_buy_sell_ratio == "1.049553"
    assert p.top_trader_account_ratio == "1.32284313"
    assert p.top_trader_position_ratio == "1.389674"


def test_parse_metrics_reconstruye_los_porcentajes_de_cuentas():
    """long = r/(1+r) es exacto, no una aproximación: debe sumar 1."""
    points = parse_metrics_csv(_metrics_csv([_metrics_row("2026-08-20 00:00:00")]), "ETHUSDT")

    long_pct = float(points[0].long_account_pct)
    short_pct = float(points[0].short_account_pct)
    assert long_pct + short_pct == pytest.approx(1.0)
    assert long_pct / short_pct == pytest.approx(2.42720496)


def test_parse_metrics_ordena_por_timestamp():
    raw = _metrics_csv(
        [
            _metrics_row("2026-08-20 12:00:00"),
            _metrics_row("2026-08-20 02:10:00"),
            _metrics_row("2026-08-20 07:30:00"),
        ]
    )
    points = parse_metrics_csv(raw, "ETHUSDT")
    assert [p.timestamp for p in points] == sorted(p.timestamp for p in points)


def test_parse_metrics_celda_vacia_es_none_no_cero():
    """"No sé" y "no había posicionamiento" no son lo mismo para el modelo."""
    row = "2026-08-20 00:00:00,ETHUSDT,2321075.748,,1.32,1.38,2.42,"
    points = parse_metrics_csv(_metrics_csv([row]), "ETHUSDT")

    assert points[0].open_interest_value is None
    assert points[0].taker_buy_sell_ratio is None
    assert points[0].open_interest == "2321075.748"


def test_parse_metrics_descarta_otro_simbolo():
    raw = _metrics_csv(
        [_metrics_row("2026-08-20 00:00:00", symbol="BTCUSDT"), _metrics_row("2026-08-20 00:05:00")]
    )
    assert len(parse_metrics_csv(raw, "ETHUSDT")) == 1


def test_parse_metrics_header_distinto_es_error():
    """Si Binance cambia el formato hay que enterarse, no parsear basura."""
    with pytest.raises(VisionError, match="header inesperado"):
        parse_metrics_csv(b"a,b,c\n1,2,3", "ETHUSDT")


def test_parse_metrics_vacio_devuelve_lista_vacia():
    assert parse_metrics_csv(b"", "ETHUSDT") == []


# --------------------------------------------------------------------------- #
# Parseo y agregación de bookDepth
# --------------------------------------------------------------------------- #


def test_parse_book_depth_separa_lados_por_signo():
    snaps = parse_book_depth_csv(_book_csv(["2026-08-22 00:00:06"]))

    assert len(snaps) == 1
    snap = snaps[0]
    assert set(snap.bid) == set(DEPTH_LEVELS)
    assert set(snap.ask) == set(DEPTH_LEVELS)
    assert snap.complete
    # Acumulado: 5% contiene a 1%, que contiene a 0,2%.
    assert snap.bid[5.0] > snap.bid[1.0] > snap.bid[0.2]


def test_parse_book_depth_ignora_los_niveles_intermedios():
    raw = b"\n".join(
        [
            BOOK_HEADER.encode(),
            b"2026-08-22 00:00:06,-3.00,1.0,300.0",
            b"2026-08-22 00:00:06,-1.00,1.0,100.0",
            b"2026-08-22 00:00:06,1.00,1.0,110.0",
        ]
    )
    snap = parse_book_depth_csv(raw)[0]
    assert set(snap.bid) == {1.0}
    assert 3.0 not in snap.bid
    assert not snap.complete  # le falta el 5%, que sí es del núcleo


def test_parse_book_depth_header_distinto_es_error():
    with pytest.raises(VisionError, match="header inesperado"):
        parse_book_depth_csv(b"ts,pct\n1,2")


def test_aggregate_promedia_dentro_de_la_barra():
    # Dos snapshots en la misma barra de 15m, uno en la siguiente.
    raw = _book_csv(["2026-08-22 00:00:06", "2026-08-22 00:10:00"], notional=1000.0)
    raw2 = _book_csv(["2026-08-22 00:20:00"], notional=2000.0)
    snaps = parse_book_depth_csv(raw) + parse_book_depth_csv(raw2)

    bars = aggregate_book_depth(snaps, "15m")

    assert len(bars) == 2
    assert bars[0].n_snapshots == 2
    assert bars[1].n_snapshots == 1
    assert bars[0].bid_1 == pytest.approx(1000.0)
    assert bars[1].bid_1 == pytest.approx(2000.0)
    assert bars[1].open_time - bars[0].open_time == INTERVAL_MS["15m"]


def test_aggregate_usa_floor_y_no_round():
    """Un snapshot a los 14:59 pertenece a SU barra, no a la siguiente.

    Redondear lo empujaria a una barra que todavia no ocurrio: lookahead
    disfrazado de detalle de implementacion.
    """
    snaps = parse_book_depth_csv(_book_csv(["2026-08-22 00:14:59", "2026-08-22 00:15:00"]))
    bars = aggregate_book_depth(snaps, "15m")

    apertura_00 = _ms("2026-08-22 00:00:00")
    assert [b.open_time for b in bars] == [apertura_00, apertura_00 + INTERVAL_MS["15m"]]
    assert all(b.n_snapshots == 1 for b in bars)
    assert bars[0].open_time <= snaps[0].timestamp


def test_aggregate_descarta_snapshots_sin_el_nucleo():
    """Sin 1% y 5% no hay barra: eso sí es un snapshot roto."""
    incompleto = DepthSnapshot(timestamp=0, bid={0.2: 1.0}, ask={0.2: 1.0})
    assert not incompleto.complete
    assert aggregate_book_depth([incompleto], "15m") == []


def test_el_archivo_viejo_sin_near_touch_si_produce_barras():
    """La regresión que costó 508 días.

    Binance agregó el nivel ±0,2% recién ~2026-01-15. Exigirlo hacía que todo
    el archivo anterior se descartara en silencio y —peor— se reportara como
    huecos del archivo. El núcleo (1% y 5%) existe en toda la historia.
    """
    viejo = parse_book_depth_csv(
        _book_csv(["2024-09-15 00:00:00"], niveles=NIVELES_SIN_NEAR)
    )

    assert len(viejo) == 1
    assert viejo[0].complete  # utilizable
    assert not viejo[0].has_near

    bars = aggregate_book_depth(viejo, "15m")
    assert len(bars) == 1
    assert bars[0].bid_1 > 0 and bars[0].bid_5 > 0
    # Y el near-touch queda en None, no en 0: no hay dato, no es libro vacío.
    assert bars[0].bid_02 is None
    assert bars[0].ask_02 is None
    assert bars[0].n_snapshots_near == 0


def test_el_near_touch_se_promedia_solo_sobre_los_que_lo_traen():
    """Mezclarlo con ceros inventaría un libro vacío cerca del mid."""
    con = parse_book_depth_csv(_book_csv(["2026-02-01 00:00:00"], notional=1000.0))
    sin = parse_book_depth_csv(
        _book_csv(["2026-02-01 00:05:00"], notional=1000.0, niveles=NIVELES_SIN_NEAR)
    )

    bars = aggregate_book_depth(con + sin, "15m")

    assert len(bars) == 1
    assert bars[0].n_snapshots == 2
    assert bars[0].n_snapshots_near == 1
    # 0,2% promediado solo sobre el snapshot que lo tenía: 1000 * 0.2 = 200.
    assert bars[0].bid_02 == pytest.approx(200.0)
    # El núcleo sí promedia los dos.
    assert bars[0].bid_1 == pytest.approx(1000.0)


def test_aggregate_sin_snapshots():
    assert aggregate_book_depth([], "15m") == []


# --------------------------------------------------------------------------- #
# Rutas y utilidades
# --------------------------------------------------------------------------- #


def test_object_key_arma_la_ruta_real():
    key = object_key("metrics", "ETHUSDT", date(2026, 8, 20))
    assert key == "data/futures/um/daily/metrics/ETHUSDT/ETHUSDT-metrics-2026-08-20.zip"


@pytest.mark.parametrize(
    ("key", "esperado"),
    [
        ("a/b/ETHUSDT-metrics-2026-08-20.zip", date(2026, 8, 20)),
        ("a/b/ETHUSDT-bookDepth-2023-01-01.zip", date(2023, 1, 1)),
        ("a/b/basura.zip", None),
        ("a/b/ETHUSDT-metrics-no-es-fecha.zip", None),
    ],
)
def test_day_from_key(key, esperado):
    assert _day_from_key(key) == esperado


def test_daterange_es_inclusivo_en_ambos_extremos():
    dias = list(daterange(date(2026, 8, 20), date(2026, 8, 22)))
    assert dias == [date(2026, 8, 20), date(2026, 8, 21), date(2026, 8, 22)]


def test_daterange_vacio_si_start_mayor_que_end():
    assert list(daterange(date(2026, 8, 22), date(2026, 8, 20))) == []


# --------------------------------------------------------------------------- #
# Cliente
# --------------------------------------------------------------------------- #


def _listing_xml(keys: list[str], next_token: str | None = None) -> str:
    contents = "".join(f"<Contents><Key>{k}</Key></Contents>" for k in keys)
    truncated = "true" if next_token else "false"
    token = f"<NextContinuationToken>{next_token}</NextContinuationToken>" if next_token else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        f"<IsTruncated>{truncated}</IsTruncated>{contents}{token}"
        "</ListBucketResult>"
    )


async def test_available_days_pagina_el_listado():
    """El listado S3 corta en 1000 claves: sin paginar se pierden años."""
    pagina1 = _listing_xml(
        [
            "data/futures/um/daily/metrics/ETHUSDT/ETHUSDT-metrics-2026-08-20.zip",
            "data/futures/um/daily/metrics/ETHUSDT/ETHUSDT-metrics-2026-08-20.zip.CHECKSUM",
        ],
        next_token="TOKEN",
    )
    pagina2 = _listing_xml(
        ["data/futures/um/daily/metrics/ETHUSDT/ETHUSDT-metrics-2026-08-21.zip"]
    )
    vistas: list[str | None] = []

    def handler(request: Request) -> Response:
        token = request.url.params.get("continuation-token")
        vistas.append(token)
        return Response(200, text=pagina2 if token else pagina1)

    async with AsyncClient(transport=MockTransport(handler)) as http:
        async with VisionClient(client=http) as client:
            dias = await client.available_days("metrics", "ETHUSDT")

    assert dias == [date(2026, 8, 20), date(2026, 8, 21)]
    assert vistas == [None, "TOKEN"]  # el CHECKSUM no generó un día extra


async def test_fetch_day_descomprime_el_csv():
    csv = _metrics_csv([_metrics_row("2026-08-20 00:00:00")])
    payload = _zip_bytes("ETHUSDT-metrics-2026-08-20.csv", csv)

    async def handler(request: Request) -> Response:
        assert "ETHUSDT-metrics-2026-08-20.zip" in str(request.url)
        return Response(200, content=payload)

    async with AsyncClient(transport=MockTransport(handler)) as http:
        async with VisionClient(client=http) as client:
            raw = await client.fetch_day("metrics", "ETHUSDT", date(2026, 8, 20))

    assert raw is not None
    assert parse_metrics_csv(raw, "ETHUSDT")[0].open_interest == "2321075.748"


async def test_fetch_day_404_es_ausencia_no_error():
    """Hay huecos reales en el archivo: se reportan, no se rellenan."""

    async def handler(request: Request) -> Response:
        return Response(404)

    async with AsyncClient(transport=MockTransport(handler)) as http:
        async with VisionClient(client=http) as client:
            assert await client.fetch_day("metrics", "ETHUSDT", date(2021, 1, 1)) is None


async def test_fetch_day_zip_con_dos_csv_es_error():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("a.csv", b"x")
        archive.writestr("b.csv", b"y")

    async def handler(request: Request) -> Response:
        return Response(200, content=buffer.getvalue())

    async with AsyncClient(transport=MockTransport(handler)) as http:
        async with VisionClient(client=http) as client:
            with pytest.raises(VisionError, match="se esperaba 1"):
                await client.fetch_day("metrics", "ETHUSDT", date(2026, 8, 20))


async def test_fetch_days_degrada_el_fallo_aislado():
    """Con 1.700 dias en vuelo, un timeout no puede tumbar la ingesta entera."""
    payload = _zip_bytes("x.csv", _metrics_csv([_metrics_row("2026-08-20 00:00:00")]))

    def handler(request: Request) -> Response:
        if "2026-08-21" in str(request.url):
            raise RuntimeError("timeout simulado")
        return Response(200, content=payload)

    dias = [date(2026, 8, 20), date(2026, 8, 21), date(2026, 8, 22)]
    async with AsyncClient(transport=MockTransport(handler)) as http:
        async with VisionClient(client=http, retries=1) as client:
            resultados = await client.fetch_days("metrics", "ETHUSDT", dias)

    assert [r.day for r in resultados] == dias  # orden preservado
    assert resultados[0].ok
    assert resultados[1].failed
    assert resultados[2].ok


async def test_fallo_de_red_no_es_hueco_del_archivo():
    """La distincion que decide si un dia se reintenta o se da por inexistente.

    Colapsar las dos cosas hizo que una corrida real con la red caida reportara
    726 dias "ausentes del archivo" que en realidad existen todos.
    """

    def handler(request: Request) -> Response:
        if "2026-08-21" in str(request.url):
            raise RuntimeError("[Errno 11001] getaddrinfo failed")
        return Response(404)

    dias = [date(2026, 8, 20), date(2026, 8, 21)]
    async with AsyncClient(transport=MockTransport(handler)) as http:
        async with VisionClient(client=http, retries=1) as client:
            ausente, caido = await client.fetch_days("metrics", "ETHUSDT", dias)

    assert ausente.absent and not ausente.failed
    assert caido.failed and not caido.absent
    assert "getaddrinfo" in caido.error


async def test_el_fallo_de_transporte_se_reintenta():
    intentos = {"n": 0}

    def handler(request: Request) -> Response:
        intentos["n"] += 1
        if intentos["n"] < 3:
            raise RuntimeError("bache de red")
        return Response(200, content=_zip_bytes("x.csv", _metrics_csv([])))

    async with AsyncClient(transport=MockTransport(handler)) as http:
        async with VisionClient(client=http, retries=3) as client:
            resultado = await client._fetch_day_with_retries(
                "metrics", "ETHUSDT", date(2026, 8, 20)
            )

    assert intentos["n"] == 3
    assert resultado.ok


async def test_el_404_no_se_reintenta():
    """Un 404 es una respuesta, no un fallo: insistir gasta 1.700 requests."""
    intentos = {"n": 0}

    def handler(request: Request) -> Response:
        intentos["n"] += 1
        return Response(404)

    async with AsyncClient(transport=MockTransport(handler)) as http:
        async with VisionClient(client=http, retries=3) as client:
            resultado = await client._fetch_day_with_retries("metrics", "ETHUSDT", date(2021, 1, 1))

    assert intentos["n"] == 1
    assert resultado.absent


async def test_el_zip_corrupto_no_se_reintenta():
    """VisionError es un problema del archivo: no mejora insistiendo."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("a.csv", b"x")
        archive.writestr("b.csv", b"y")
    intentos = {"n": 0}

    def handler(request: Request) -> Response:
        intentos["n"] += 1
        return Response(200, content=buffer.getvalue())

    async with AsyncClient(transport=MockTransport(handler)) as http:
        async with VisionClient(client=http, retries=3) as client:
            with pytest.raises(VisionError):
                await client._fetch_day_with_retries("metrics", "ETHUSDT", date(2026, 8, 20))

    assert intentos["n"] == 1


# --------------------------------------------------------------------------- #
# Persistencia de la profundidad
# --------------------------------------------------------------------------- #


def _agg(open_time: int, escala: float = 1.0, near: bool = True) -> BookDepthAggregate:
    return BookDepthAggregate(
        open_time=open_time,
        bid_1=1000.0 * escala,
        ask_1=1100.0 * escala,
        bid_5=5000.0 * escala,
        ask_5=5100.0 * escala,
        n_snapshots=30,
        bid_02=100.0 * escala if near else None,
        ask_02=110.0 * escala if near else None,
        n_snapshots_near=30 if near else 0,
    )


def test_el_near_touch_ausente_se_persiste_como_nan_no_como_cero(in_memory_engine):
    """En DB es NULL y al cargar es NaN. Un 0 diría "no hay libro cerca del mid"."""
    upsert_book_depth("ETHUSDT", "15m", [_agg(0, near=False)])

    serie = load_book_depth("ETHUSDT", "15m")
    assert np.isnan(serie.bid_02[0])
    assert np.isnan(serie.ask_02[0])
    assert serie.bid_1[0] == pytest.approx(1000.0)  # el núcleo sí está


def test_upsert_book_depth_es_idempotente(in_memory_engine):
    step = INTERVAL_MS["15m"]
    bars = [_agg(i * step) for i in range(4)]

    assert upsert_book_depth("ETHUSDT", "15m", bars) == 4
    upsert_book_depth("ETHUSDT", "15m", bars)  # reingesta del mismo día

    assert book_depth_coverage("ETHUSDT", "15m")["n_bars"] == 4


def test_upsert_book_depth_pisa_el_valor_anterior(in_memory_engine):
    upsert_book_depth("ETHUSDT", "15m", [_agg(0, escala=1.0)])
    upsert_book_depth("ETHUSDT", "15m", [_agg(0, escala=2.0)])

    serie = load_book_depth("ETHUSDT", "15m")
    assert len(serie) == 1
    assert serie.bid_1[0] == pytest.approx(2000.0)


def test_upsert_book_depth_vacio_no_escribe(in_memory_engine):
    assert upsert_book_depth("ETHUSDT", "15m", []) == 0


def test_load_book_depth_conserva_la_precision_del_promedio(in_memory_engine):
    """El promedio de la barra se guarda como str: no puede perder dígitos."""
    bar = BookDepthAggregate(
        open_time=0,
        bid_1=1.0,
        ask_1=1.0,
        bid_5=1.0,
        ask_5=1.0,
        n_snapshots=7,
        bid_02=1234.5678901234567,
        ask_02=1.0,
        n_snapshots_near=7,
    )
    upsert_book_depth("ETHUSDT", "15m", [bar])

    serie = load_book_depth("ETHUSDT", "15m")
    assert serie.bid_02[0] == bar.bid_02
    assert serie.n_snapshots[0] == 7


def test_book_depth_day_counts_agrupa_por_dia_utc(in_memory_engine):
    step = INTERVAL_MS["15m"]
    dia = 86_400_000
    bars = [_agg(0), _agg(step), _agg(dia), _agg(dia + step), _agg(dia + 2 * step)]
    upsert_book_depth("ETHUSDT", "15m", bars)

    counts = book_depth_day_counts("ETHUSDT", "15m")
    assert counts == {0: 2, 1: 3}


def test_load_book_depth_filtra_por_rango(in_memory_engine):
    step = INTERVAL_MS["15m"]
    upsert_book_depth("ETHUSDT", "15m", [_agg(i * step) for i in range(5)])

    serie = load_book_depth("ETHUSDT", "15m", start_time=step, end_time=3 * step)
    assert len(serie) == 3
