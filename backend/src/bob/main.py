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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info(
        "BOB starting | watchlist={} | timeframe={} | threshold={}",
        settings.watchlist,
        settings.bob_default_timeframe,
        settings.bob_signal_threshold,
    )
    init_db()

    # Fase 1: aquí arranca el MarketDataHub de Binance (data/binance_ws.py)
    # Fase 5: aquí arranca el LiveAnalyst y el PaperTracker
    # Fase 7: aquí arranca APScheduler (snapshots de sentimiento)

    yield

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
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "bob",
        "mode": "assistant",  # BOB nunca ejecuta órdenes
        "watchlist": settings.watchlist,
        "default_timeframe": settings.bob_default_timeframe,
        "signal_threshold": settings.bob_signal_threshold,
    }
