import asyncio
import contextlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.exceptions import HTTPException as StarletteHTTPException

from bob.api.ws import broadcast_hub
from bob.api.ws import router as ws_router
from bob.config import settings
from bob.db.session import init_db
from bob.live.analyst import LiveAnalyst
from bob.live.feed import LiveDataService
from bob.models.projection import LeverageProfile
from bob.paper.tracker import tracker_loop


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info(
        "BOB starting | watchlist={} | timeframe={} | threshold={}",
        settings.watchlist,
        settings.bob_default_timeframe,
        settings.bob_signal_threshold,
    )
    init_db()

    feed: LiveDataService | None = None
    if settings.bob_live_data:
        feed = LiveDataService(
            settings.watchlist,
            settings.bob_default_timeframe,
            publish=broadcast_hub.publish,
            feed_mode=settings.bob_feed_mode,
            snapshot_period=settings.bob_snapshot_period,
            snapshot_interval_s=settings.bob_snapshot_interval_min * 60,
        )
        app.state.feed = feed
        await feed.start()
    else:
        logger.warning("BOB_LIVE_DATA=false — backend sin feed de Binance (modo offline)")

    # Fase 5 — el analista y su tracker. Solo tienen sentido con feed: sin
    # velas nuevas no hay barra que analizar ni horizonte que madurar.
    analyst: LiveAnalyst | None = None
    stop_tracker = asyncio.Event()
    boot_task: asyncio.Task[None] | None = None
    symbol = settings.watchlist[0] if settings.watchlist else ""

    if feed is not None and settings.bob_live_analyst and symbol:
        analyst = LiveAnalyst(
            symbol,
            settings.bob_default_timeframe,
            publish=broadcast_hub.publish,
            feature_set=settings.bob_live_features,
            profile=LeverageProfile(leverage=settings.bob_default_leverage),
            refit_every_bars=settings.bob_refit_every_bars,
        )
        analyst.attach(feed.hub)
        app.state.analyst = analyst
        # El ajuste inicial son ~10 fits de boosting sobre dos años de velas.
        # Hacerlo dentro del lifespan dejaría el backend sin responder varios
        # minutos: arranca en background y el analista publica cuando termina.
        boot_task = asyncio.create_task(
            _boot_analyst(analyst, symbol, stop_tracker), name="analyst-boot"
        )
    elif settings.bob_live_analyst:
        logger.warning("analista deshabilitado: no hay feed o la watchlist está vacía")

    # Fase 7: aquí arranca APScheduler (snapshots de sentimiento)

    yield

    stop_tracker.set()
    if boot_task is not None:
        boot_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await boot_task
    if analyst is not None:
        await analyst.stop()
    if feed is not None:
        await feed.stop()
    await broadcast_hub.stop()
    logger.info("BOB shutting down")


async def _boot_analyst(
    analyst: LiveAnalyst, symbol: str, stop: asyncio.Event
) -> None:
    """Ajusta el modelo y recién entonces levanta el paper tracker.

    El orden importa: el tracker le devuelve al cono conformal cada cobertura
    observada (ACI), y para eso necesita el bundle ya construido. Lanzarlo
    antes lo dejaría midiendo sin realimentar, que es el modo en que el cono
    deja de adaptarse al régimen sin que nadie lo note.
    """
    try:
        await analyst.start()
    except Exception as exc:  # noqa: BLE001 — un modelo caído no tumba el feed
        logger.exception("analista: no pudo arrancar: {}", exc)
        await broadcast_hub.publish(
            "analysis.error",
            {"symbol": symbol, "detail": f"el analista no arrancó: {exc}"},
        )
        return

    await tracker_loop(
        symbol,
        analyst.timeframe,
        settings.bob_tracker_interval_min * 60,
        stop=stop,
        cones_provider=lambda: analyst.bundle.cones if analyst.bundle else None,
    )


app = FastAPI(
    title="BOB — Asistente de Decisión Intradía",
    version="0.2.0",
    lifespan=lifespan,
)


@app.exception_handler(StarletteHTTPException)
async def _http_exc(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": "http", "detail": exc.detail, "path": request.url.path},
    )


@app.exception_handler(RequestValidationError)
async def _validation_exc(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": "validation",
            "detail": exc.errors(),
            "path": request.url.path,
        },
    )


@app.exception_handler(Exception)
async def _unhandled_exc(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled exception on {}: {}", request.url.path, exc)
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal",
            "detail": str(exc),
            "path": request.url.path,
        },
    )


app.include_router(ws_router)


@app.get("/api/health")
async def health(request: Request) -> dict[str, Any]:
    feed: LiveDataService | None = getattr(request.app.state, "feed", None)
    analyst: LiveAnalyst | None = getattr(request.app.state, "analyst", None)
    return {
        "status": "ok",
        "service": "bob",
        "mode": "assistant",  # BOB nunca ejecuta órdenes
        "watchlist": settings.watchlist,
        "default_timeframe": settings.bob_default_timeframe,
        "signal_threshold": settings.bob_signal_threshold,
        # Regla 8: el estado del feed es parte de lo que el usuario debe ver.
        "live_data": settings.bob_live_data,
        "feed": (
            {"source": feed.hub.source_name, **feed.hub.status.as_dict()}
            if feed is not None
            else None
        ),
        # Un backend en verde que no emite nada se ve igual que uno sano si no
        # se mira esto: durante una corrida larga es LA pregunta.
        "analyst": analyst.status() if analyst is not None else None,
    }
