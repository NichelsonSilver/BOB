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
from bob.live.feed import LiveDataService


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

    # Fase 5: aquí arranca el LiveAnalyst y el PaperTracker
    # Fase 7: aquí arranca APScheduler (snapshots de sentimiento)

    yield

    if feed is not None:
        await feed.stop()
    await broadcast_hub.stop()
    logger.info("BOB shutting down")


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
    }
