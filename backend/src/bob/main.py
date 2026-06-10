from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.exceptions import HTTPException as StarletteHTTPException

from bob.api.routes.bots import router as bots_router
from bob.api.routes.history import router as history_router
from bob.api.routes.markets import router as markets_router
from bob.api.routes.points import router as points_router
from bob.api.routes.presets import router as presets_router
from bob.api.routes.settings import router as settings_router
from bob.api.ws import broadcast_hub, router as ws_router
from bob.config import settings
from bob.db.session import init_db
from bob.grid.manager import BotManager
from bob.grvt.client import check_grvt_connection, create_grvt_client
from bob.grvt.rest import GrvtRestClient
from bob.grvt.ws_market import market_data_hub
from bob.grvt.ws_trading import trading_hub


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info(
        "BOB starting | env={} | trading_account={}",
        settings.grvt_env,
        settings.grvt_trading_account_id,
    )
    init_db()

    grvt_client = create_grvt_client()
    await grvt_client.load_markets()
    app.state.grvt_client = grvt_client
    app.state.rest_client = GrvtRestClient(grvt_client)

    await market_data_hub.start()
    app.state.market_data_hub = market_data_hub

    await trading_hub.start()
    app.state.trading_hub = trading_hub

    app.state.bot_manager = BotManager(
        market_data_hub=market_data_hub,
        trading_hub=trading_hub,
        rest_client=app.state.rest_client,
    )

    broadcast_hub.attach_manager(app.state.bot_manager)
    await broadcast_hub.start()
    app.state.broadcast_hub = broadcast_hub

    try:
        rehydrated = await app.state.bot_manager.rehydrate_from_db()
        if rehydrated:
            logger.info("rehydrated bots from DB: {}", rehydrated)
    except Exception as e:  # pragma: no cover — best-effort
        logger.exception("rehydrate_from_db failed: {}", e)

    await app.state.bot_manager.start_reconcile_loop()

    yield

    await app.state.bot_manager.stop_reconcile_loop()
    await broadcast_hub.stop()
    await app.state.bot_manager.stop_all(reason="shutdown")
    await trading_hub.stop()
    await market_data_hub.stop()
    logger.info("BOB shutting down")


app = FastAPI(
    title="BOB — Grid Trading Bot",
    version="0.1.0",
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


app.include_router(markets_router)
app.include_router(bots_router)
app.include_router(history_router)
app.include_router(points_router)
app.include_router(presets_router)
app.include_router(settings_router)
app.include_router(ws_router)


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "service": "bob"}


@app.get("/api/health/grvt")
async def health_grvt() -> dict:
    client = app.state.grvt_client
    return await check_grvt_connection(client)
