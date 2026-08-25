"""Tests de la ingesta del archivo histórico: idempotencia y reporte honesto.

La ingesta baja ~1.700 días. Dos propiedades no son negociables: que reejecutarla
no vuelva a bajar lo que ya está, y que los días que el archivo no tiene se
reporten como huecos en vez de desaparecer del conteo.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date

from httpx import AsyncClient, MockTransport, Request, Response

from bob.data.binance_rest import FAPI_BASE, INTERVAL_MS, BinanceRestClient
from bob.data.download_vision import (
    FUNDING_PERIOD,
    METRICS_PERIOD,
    METRICS_ROWS_PER_DAY,
    IngestReport,
    _batched,
    _clamp_start,
    _epoch_day,
    _pending_days,
    ingest_book_depth,
    ingest_funding,
    ingest_metrics,
)
from bob.data.store import (
    book_depth_coverage,
    derivative_day_counts,
    derivatives_coverage,
)
from bob.data.vision import DATASET_START, VisionClient

METRICS_HEADER = (
    "create_time,symbol,sum_open_interest,sum_open_interest_value,"
    "count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,"
    "count_long_short_ratio,sum_taker_long_short_vol_ratio"
)


def _zip(name: str, payload: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, payload)
    return buffer.getvalue()


def _metrics_day(day: date) -> bytes:
    """Un día completo: los 288 puntos de la grilla de 5m."""
    filas = [METRICS_HEADER]
    for i in range(METRICS_ROWS_PER_DAY):
        hora, minuto = divmod(i * 5, 60)
        ts = f"{day.isoformat()} {hora:02d}:{minuto:02d}:00"
        filas.append(f"{ts},ETHUSDT,2000000.0,6e9,1.3,1.4,2.4,1.05")
    return _zip("m.csv", "\n".join(filas).encode())


def _book_day(day: date) -> bytes:
    """Un día completo de libro: un snapshot por minuto, 12 filas cada uno."""
    filas = ["timestamp,percentage,depth,notional"]
    for minuto in range(1440):
        hora, mm = divmod(minuto, 60)
        ts = f"{day.isoformat()} {hora:02d}:{mm:02d}:00"
        for pct in (-5.0, -1.0, -0.2, 0.2, 1.0, 5.0):
            filas.append(f"{ts},{pct:.2f},1.0,{1000.0 * abs(pct):.4f}")
    return _zip("b.csv", "\n".join(filas).encode())


def _client(builder) -> tuple[VisionClient, list[str]]:
    """VisionClient sobre un transport falso, más el registro de URLs pedidas."""
    pedidas: list[str] = []

    def handler(request: Request) -> Response:
        url = str(request.url)
        pedidas.append(url)
        payload = builder(url)
        return Response(200, content=payload) if payload else Response(404)

    http = AsyncClient(transport=MockTransport(handler))
    return VisionClient(client=http), pedidas


# --------------------------------------------------------------------------- #
# Selección de días
# --------------------------------------------------------------------------- #


def test_epoch_day_es_el_dia_utc():
    assert _epoch_day(date(1970, 1, 1)) == 0
    assert _epoch_day(date(1970, 1, 2)) == 1


def test_pending_days_salta_los_dias_completos():
    have = {_epoch_day(date(2026, 8, 20)): METRICS_ROWS_PER_DAY}

    pendientes = _pending_days(date(2026, 8, 20), date(2026, 8, 22), have, METRICS_ROWS_PER_DAY)

    assert pendientes == [date(2026, 8, 21), date(2026, 8, 22)]


def test_pending_days_reintenta_el_dia_a_medias():
    have = {_epoch_day(date(2026, 8, 20)): 100}
    pendientes = _pending_days(date(2026, 8, 20), date(2026, 8, 20), have, METRICS_ROWS_PER_DAY)
    assert pendientes == [date(2026, 8, 20)]


def test_pending_days_tolera_el_dia_casi_completo():
    """Binance publica días levemente incompletos: reintentarlos es un bucle."""
    have = {_epoch_day(date(2026, 8, 20)): METRICS_ROWS_PER_DAY - 1}
    assert _pending_days(date(2026, 8, 20), date(2026, 8, 20), have, METRICS_ROWS_PER_DAY) == []


def test_clamp_start_no_pide_antes_del_primer_dia_publicado():
    assert _clamp_start("metrics", date(2019, 1, 1)) == DATASET_START["metrics"]
    assert _clamp_start("bookDepth", date(2019, 1, 1)) == DATASET_START["bookDepth"]
    assert _clamp_start("metrics", date(2026, 1, 1)) == date(2026, 1, 1)


def test_batched_parte_sin_perder_dias():
    dias = [date(2026, 8, d) for d in range(1, 8)]
    lotes = _batched(dias, 3)

    assert [len(lote) for lote in lotes] == [3, 3, 1]
    assert [d for lote in lotes for d in lote] == dias


def test_batched_lista_vacia():
    assert _batched([], 10) == []


# --------------------------------------------------------------------------- #
# Reporte
# --------------------------------------------------------------------------- #


def test_reporte_lista_los_huecos_sin_esconderlos():
    report = IngestReport(
        dataset="metrics",
        days_requested=10,
        days_written=8,
        days_absent=2,
        absent_days=[date(2026, 8, 20), date(2026, 8, 21)],
    )
    texto = report.render()

    assert "ausentes (404)   : 2" in texto
    assert "2026-08-20" in texto


def test_reporte_resume_cuando_hay_muchos_huecos():
    dias = [date(2026, 8, 1) for _ in range(9)]
    report = IngestReport(dataset="metrics", days_absent=9, absent_days=dias)

    assert "y 4 más" in report.render()


# --------------------------------------------------------------------------- #
# Ingesta end-to-end
# --------------------------------------------------------------------------- #


async def test_ingest_metrics_persiste_el_dia_completo(in_memory_engine):
    client, _ = _client(lambda url: _metrics_day(date(2026, 8, 20)))

    async with client:
        report = await ingest_metrics(
            "ETHUSDT", date(2026, 8, 20), date(2026, 8, 20), client=client
        )

    assert report.days_written == 1
    assert report.rows_written == METRICS_ROWS_PER_DAY
    assert derivatives_coverage("ETHUSDT", METRICS_PERIOD)["n_points"] == METRICS_ROWS_PER_DAY


async def test_ingest_metrics_es_idempotente(in_memory_engine):
    """Reejecutar no vuelve a pedir el día: con 1.700 días eso importa."""
    client, pedidas = _client(lambda url: _metrics_day(date(2026, 8, 20)))

    async with client:
        await ingest_metrics("ETHUSDT", date(2026, 8, 20), date(2026, 8, 20), client=client)
        pedidas_primera = len(pedidas)
        segunda = await ingest_metrics(
            "ETHUSDT", date(2026, 8, 20), date(2026, 8, 20), client=client
        )

    assert pedidas_primera == 1
    assert len(pedidas) == 1  # no hubo segunda descarga
    assert segunda.days_skipped == 1
    assert segunda.days_written == 0
    assert derivatives_coverage("ETHUSDT", METRICS_PERIOD)["n_points"] == METRICS_ROWS_PER_DAY


async def test_ingest_metrics_reporta_el_dia_ausente(in_memory_engine):
    def builder(url: str) -> bytes | None:
        return None if "2026-08-21" in url else _metrics_day(date(2026, 8, 20))

    client, _ = _client(builder)
    async with client:
        report = await ingest_metrics(
            "ETHUSDT", date(2026, 8, 20), date(2026, 8, 21), client=client
        )

    assert report.days_written == 1
    assert report.days_absent == 1
    assert report.absent_days == [date(2026, 8, 21)]


async def test_ingest_metrics_no_pide_antes_del_inicio_del_dataset(in_memory_engine):
    client, pedidas = _client(lambda url: None)

    async with client:
        report = await ingest_metrics(
            "ETHUSDT", date(2019, 1, 1), date(2019, 1, 5), client=client
        )

    assert pedidas == []
    assert report.days_requested == 0


async def test_ingest_book_depth_agrega_a_la_grilla_del_timeframe(in_memory_engine):
    client, _ = _client(lambda url: _book_day(date(2026, 8, 20)))

    async with client:
        report = await ingest_book_depth(
            "ETHUSDT", "15m", date(2026, 8, 20), date(2026, 8, 20), client=client
        )

    esperado = 86_400_000 // INTERVAL_MS["15m"]  # 96 barras por día
    assert report.days_written == 1
    assert report.rows_written == esperado
    assert book_depth_coverage("ETHUSDT", "15m")["n_bars"] == esperado


async def test_ingest_book_depth_es_idempotente(in_memory_engine):
    client, pedidas = _client(lambda url: _book_day(date(2026, 8, 20)))

    async with client:
        await ingest_book_depth(
            "ETHUSDT", "15m", date(2026, 8, 20), date(2026, 8, 20), client=client
        )
        segunda = await ingest_book_depth(
            "ETHUSDT", "15m", date(2026, 8, 20), date(2026, 8, 20), client=client
        )

    assert len(pedidas) == 1
    assert segunda.days_skipped == 1


async def test_archivo_publicado_pero_sin_filas_no_es_un_404(in_memory_engine):
    """La cuarta categoria, la que oculto el bug del near-touch.

    Un dia que llega con HTTP 200 y no produce filas no es un hueco del
    archivo: es este codigo que no supo leerlo. Contarlo como 404 lo vuelve
    invisible — asi se perdieron 508 dias de libro sin que el reporte lo dijera.
    """
    client, _ = _client(lambda url: _zip("m.csv", METRICS_HEADER.encode()))

    async with client:
        report = await ingest_metrics(
            "ETHUSDT", date(2026, 8, 20), date(2026, 8, 20), client=client
        )

    assert report.days_written == 0
    assert report.days_absent == 0
    assert report.days_empty == 1
    assert report.empty_days == [date(2026, 8, 20)]
    assert report.incomplete  # el archivo cumplio; el que fallo fue este codigo

    texto = report.render()
    assert "sin filas" in texto
    assert "formato" in texto


def test_day_counts_arranca_vacio(in_memory_engine):
    assert derivative_day_counts("ETHUSDT", METRICS_PERIOD) == {}


async def test_ingest_funding_pagina_hasta_el_final(in_memory_engine):
    """Cortar en la primera pagina truncaria anos de historia en silencio."""
    paginas: list[list[dict]] = [
        [
            {"symbol": "ETHUSDT", "fundingTime": i * 28_800_000, "fundingRate": "0.0001"}
            for i in range(1000)
        ],
        [
            {"symbol": "ETHUSDT", "fundingTime": (1000 + i) * 28_800_000, "fundingRate": "0.0002"}
            for i in range(7)
        ],
    ]
    cursores: list[str | None] = []

    def handler(request: Request) -> Response:
        cursores.append(request.url.params.get("startTime"))
        return Response(200, json=paginas[len(cursores) - 1] if len(cursores) <= 2 else [])

    async with AsyncClient(transport=MockTransport(handler), base_url=FAPI_BASE) as http:
        rest = BinanceRestClient(client=http)
        report = await ingest_funding("ETHUSDT", 0, client=rest)

    assert len(cursores) == 2  # se detuvo al ver la pagina corta
    assert report.rows_written == 1007
    assert derivatives_coverage("ETHUSDT", FUNDING_PERIOD)["n_points"] == 1007


async def test_ingest_funding_corta_si_la_pagina_se_repite(in_memory_engine):
    """Una pagina llena que no avanza el cursor no puede volverse bucle infinito."""
    llena = [
        {"symbol": "ETHUSDT", "fundingTime": i * 28_800_000, "fundingRate": "0.0001"}
        for i in range(1000)
    ]
    llamadas = 0

    def handler(request: Request) -> Response:
        nonlocal llamadas
        llamadas += 1
        assert llamadas < 10, "la paginacion no cortó"
        return Response(200, json=llena)  # siempre la misma pagina

    async with AsyncClient(transport=MockTransport(handler), base_url=FAPI_BASE) as http:
        rest = BinanceRestClient(client=http)
        report = await ingest_funding("ETHUSDT", 0, client=rest)

    assert llamadas == 2  # la segunda ya no aporta puntos nuevos
    assert report.rows_written == 1000


async def test_ingest_funding_sin_datos(in_memory_engine):
    async def handler(request: Request) -> Response:
        return Response(200, json=[])

    async with AsyncClient(transport=MockTransport(handler), base_url=FAPI_BASE) as http:
        rest = BinanceRestClient(client=http)
        report = await ingest_funding("ETHUSDT", 0, client=rest)

    assert report.rows_written == 0


def test_reporte_de_funding_no_imprime_conteo_de_dias():
    report = IngestReport(dataset="funding", by_day=False, rows_written=2190)
    texto = report.render()

    assert "dias pedidos" not in texto.replace("í", "i")
    assert "2,190" in texto


async def test_el_fallo_de_red_deja_el_dia_pendiente(in_memory_engine):
    """El bug real: la red se cae y el reporte dice "el archivo no lo tiene".

    Un dia caido no puede contarse como hueco ni marcarse como resuelto: tiene
    que quedar pendiente para la proxima corrida.
    """
    caido = {"activo": True}

    def handler(request: Request) -> Response:
        if caido["activo"]:
            raise RuntimeError("[Errno 11001] getaddrinfo failed")
        return Response(200, content=_metrics_day(date(2026, 8, 20)))

    pedidas: list[str] = []

    def wrapper(request: Request) -> Response:
        pedidas.append(str(request.url))
        return handler(request)

    http = AsyncClient(transport=MockTransport(wrapper))
    async with VisionClient(client=http, retries=1) as client:
        primera = await ingest_metrics(
            "ETHUSDT", date(2026, 8, 20), date(2026, 8, 20), client=client
        )
        assert primera.days_failed == 1
        assert primera.days_absent == 0
        assert primera.absent_days == []
        assert primera.incomplete

        # La red vuelve: la reejecucion retoma el dia, no lo da por perdido.
        caido["activo"] = False
        segunda = await ingest_metrics(
            "ETHUSDT", date(2026, 8, 20), date(2026, 8, 20), client=client
        )

    assert segunda.days_written == 1
    assert not segunda.incomplete
    assert derivatives_coverage("ETHUSDT", METRICS_PERIOD)["n_points"] == METRICS_ROWS_PER_DAY


async def test_el_fallo_de_red_del_libro_tampoco_es_hueco(in_memory_engine):
    def handler(request: Request) -> Response:
        raise RuntimeError("timeout")

    http = AsyncClient(transport=MockTransport(handler))
    async with VisionClient(client=http, retries=1) as client:
        report = await ingest_book_depth(
            "ETHUSDT", "15m", date(2026, 8, 20), date(2026, 8, 20), client=client
        )

    assert report.days_failed == 1
    assert report.days_absent == 0
    assert report.incomplete


def test_el_reporte_grita_cuando_quedo_incompleto():
    report = IngestReport(dataset="bookDepth", days_requested=730, days_failed=726)
    texto = report.render()

    assert "fallidos (red)   : 726" in texto
    assert "reejecutar" in texto
    assert report.incomplete


def test_un_reporte_limpio_no_grita():
    report = IngestReport(dataset="metrics", days_requested=10, days_written=10)
    assert "reejecutar" not in report.render()
    assert not report.incomplete
