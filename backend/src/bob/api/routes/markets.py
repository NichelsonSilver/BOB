from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, Request

from bob.grid.range_suggestion import (
    Mode,
    RangeSuggestion,
    parse_candle,
    suggest_n_grids,
    suggest_range,
    volatility_pct,
)

router = APIRouter(prefix="/api/markets", tags=["markets"])


@router.get("/symbols")
async def get_symbols(request: Request) -> dict:
    """Return a slim list of perpetual symbols and their instrument metadata."""
    client = request.app.state.grvt_client
    markets = client.markets or {}
    items: list[dict] = []
    for symbol, m in markets.items():
        if not isinstance(m, dict):
            continue
        items.append(
            {
                "symbol": symbol,
                "tick_size": m.get("tick_size"),
                "lot_size": m.get("min_size") or m.get("lot_size"),
                "min_notional": m.get("min_notional"),
                "base": m.get("base"),
                "quote": m.get("quote"),
            }
        )
    items.sort(key=lambda x: x["symbol"])
    return {"symbols": [i["symbol"] for i in items], "instruments": items}


@router.get("/instrument/{symbol}")
async def get_instrument(symbol: str, request: Request) -> dict:
    """Return full instrument metadata for a symbol (tick/lot/min_notional)."""
    client = request.app.state.grvt_client
    m = (client.markets or {}).get(symbol)
    if not m:
        raise HTTPException(status_code=404, detail=f"Instrument {symbol!r} not loaded")
    return {
        "symbol": symbol,
        "tick_size": m.get("tick_size"),
        "lot_size": m.get("min_size") or m.get("lot_size"),
        "min_notional": m.get("min_notional"),
        "base": m.get("base"),
        "quote": m.get("quote"),
        "raw": m,
    }


@router.get("/ticker/{symbol}")
async def get_ticker(symbol: str, request: Request) -> dict:
    """Get ticker data for a symbol (e.g. BTC_USDT_Perp)."""
    client = request.app.state.grvt_client
    ticker = await client.fetch_ticker(symbol)
    if not ticker:
        raise HTTPException(status_code=404, detail=f"Ticker not found for {symbol}")
    return ticker


@router.get("/klines/{symbol}")
async def get_klines(
    symbol: str,
    request: Request,
    timeframe: str = "1m",
    limit: int = 100,
) -> dict:
    """Get historical klines/candlesticks for a symbol.

    REST fallback for when WS history is not enough.
    """
    client = request.app.state.grvt_client
    result = await client.fetch_ohlcv(
        symbol=symbol,
        timeframe=timeframe,
        limit=limit,
    )
    return result


@router.get("/orderbook/{symbol}")
async def get_orderbook(
    symbol: str,
    request: Request,
    depth: int = 10,
) -> dict:
    """Get order book for a symbol."""
    client = request.app.state.grvt_client
    book = await client.fetch_order_book(symbol, limit=depth)
    if not book:
        raise HTTPException(status_code=404, detail=f"Order book not found for {symbol}")
    return book


@router.get("/range-suggestion/{symbol}")
async def get_range_suggestion(
    symbol: str,
    request: Request,
    days: int = Query(30, ge=3, le=365),
    mode: Mode = Query("percentile"),
    investment_usdt: float = Query(100.0, gt=0),
    leverage: int = Query(5, ge=1, le=20),
) -> dict:
    """Suggest price_low/high and optimal n_grids from 1D klines.

    Modes:
      - percentile: p10/p90 of closes — conservative, rejects outliers.
      - minmax: min(low)/max(high) — captures full range.
      - atr: last_close ± 3·ATR(14) — adaptive to volatility.
    """
    client = request.app.state.grvt_client
    raw = await client.fetch_ohlcv(symbol=symbol, timeframe="1d", limit=days)
    rows = raw.get("result", []) if isinstance(raw, dict) else (raw or [])
    candles = [c for c in (parse_candle(r) for r in rows) if c is not None]
    if len(candles) < 2:
        raise HTTPException(
            status_code=404,
            detail=f"No hay suficientes klines 1D para {symbol} (obtuve {len(candles)})",
        )

    m = (request.app.state.grvt_client.markets or {}).get(symbol) or {}
    tick_size = Decimal(str(m.get("tick_size") or "0.1"))
    min_notional = Decimal(str(m.get("min_notional") or "100"))

    price_low, price_high, atr_value = suggest_range(
        candles, mode=mode, tick_size=tick_size
    )
    n_grids = suggest_n_grids(
        price_low=price_low,
        price_high=price_high,
        investment_usdt=Decimal(str(investment_usdt)),
        leverage=leverage,
        min_notional=min_notional,
        tick_size=tick_size,
    )
    vol = volatility_pct(candles)

    suggestion = RangeSuggestion(
        price_low=price_low,
        price_high=price_high,
        atr=atr_value,
        volatility_pct=vol,
        suggested_n_grids=n_grids,
        mode=mode,
        sample_size=len(candles),
    )
    return {
        "symbol": symbol,
        "mode": suggestion.mode,
        "days": days,
        "sample_size": suggestion.sample_size,
        "price_low": str(suggestion.price_low),
        "price_high": str(suggestion.price_high),
        "atr": str(suggestion.atr),
        "volatility_pct": str(suggestion.volatility_pct),
        "suggested_n_grids": suggestion.suggested_n_grids,
        "tick_size": str(tick_size),
        "min_notional": str(min_notional),
    }


@router.get("/funding/{symbol}")
async def get_funding_rate(
    symbol: str,
    request: Request,
    limit: int = 100,
) -> dict:
    """Get funding rate history for a symbol."""
    client = request.app.state.grvt_client
    return await client.fetch_funding_rate_history(symbol=symbol, limit=limit)
