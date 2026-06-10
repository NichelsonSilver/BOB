"""Calcula config de grid para ETH usando rango 1d real desde GRVT.

Uso:
    .venv/Scripts/python.exe -m scripts.eth_1d_config \
        --investment 100 --leverage 50 --step 5

Imprime los flags que hay que pasarle a scripts.phase5_live_smoke
y valida min_size / min_notional antes de sugerirlos.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from decimal import Decimal

from bob.grvt.client import create_grvt_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("eth_1d_config")

SYMBOL = "ETH_USDT_Perp"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--investment", type=str, default="100")
    ap.add_argument("--leverage", type=int, default=50)
    ap.add_argument("--step", type=str, default="5", help="USD entre niveles del grid")
    ap.add_argument("--cap", type=int, default=500, help="cap duro de n_grids")
    args = ap.parse_args()

    client = create_grvt_client()
    await client.load_markets()
    instrument = client.markets.get(SYMBOL)
    if instrument is None:
        raise RuntimeError(f"{SYMBOL} no encontrado en markets")
    tick = Decimal(str(instrument.get("tick_size", "0.01")))
    lot = Decimal(str(instrument.get("min_size", "0.001")))
    min_notional = Decimal(str(instrument.get("min_notional", "0")))
    log.info("instrument %s | tick=%s lot=%s min_notional=%s",
             SYMBOL, tick, lot, min_notional)

    # 24h klines de 1h → 24 velas
    raw = await client.fetch_ohlcv(SYMBOL, timeframe="1h", limit=24)
    log.info("ohlcv top-level type=%s", type(raw).__name__)
    if isinstance(raw, dict):
        # GRVT envuelve en {"result": [...]} o {"klines": [...]}
        for key in ("result", "klines", "data", "k"):
            if key in raw and isinstance(raw[key], list):
                klines = raw[key]
                break
        else:
            log.info("dict keys = %s | sample = %r", list(raw.keys())[:8], raw)
            raise RuntimeError("no encuentro la lista de klines en el dict")
    else:
        klines = raw
    if not klines:
        raise RuntimeError("no klines devueltas")
    log.info("kline[0] sample = %r", klines[0])

    def _field(k, idx_or_key, fallback_keys=()):
        if isinstance(k, dict):
            for key in (idx_or_key, *fallback_keys):
                if key in k and k[key] not in (None, ""):
                    return k[key]
            raise KeyError(f"missing {idx_or_key} in {k!r}")
        return k[idx_or_key]

    if isinstance(klines[0], dict):
        highs = [Decimal(str(_field(k, "high", ("h",)))) for k in klines]
        lows = [Decimal(str(_field(k, "low", ("l",)))) for k in klines]
        last = Decimal(str(_field(klines[-1], "close", ("c",))))
    else:
        highs = [Decimal(str(k[2])) for k in klines]
        lows = [Decimal(str(k[3])) for k in klines]
        last = Decimal(str(klines[-1][4]))
    hi_raw = max(highs)
    lo_raw = min(lows)
    log.info("ETH 1d real | low=%s high=%s last=%s rango=%s",
             lo_raw, hi_raw, last, hi_raw - lo_raw)

    # Cuantizar a tick
    def q(v: Decimal) -> Decimal:
        return (v / tick).to_integral_value() * tick

    lo = q(lo_raw)
    hi = q(hi_raw)
    span = hi - lo
    step = Decimal(args.step)
    investment = Decimal(args.investment)
    leverage = args.leverage

    # Maximizar n_grids = floor(span/step), respetando lot/min_notional
    n_by_step = int(span / step)
    avg = (lo + hi) / 2
    notional_total = investment * Decimal(leverage)

    def feasible(n: int) -> tuple[bool, Decimal, Decimal, str]:
        qty = notional_total / (Decimal(n) * avg)
        qty_q = (qty // lot) * lot
        notional = qty_q * avg
        if qty_q < lot:
            return False, qty_q, notional, f"qty {qty_q} < lot {lot}"
        if notional < min_notional:
            return False, qty_q, notional, f"notional {notional} < min {min_notional}"
        return True, qty_q, notional, "ok"

    n = min(n_by_step, args.cap)
    while n >= 2:
        ok, qty_q, notional, why = feasible(n)
        if ok:
            break
        log.warning("n=%d no viable (%s) — bajo", n, why)
        n -= 1
    else:
        raise RuntimeError("ningún n>=2 viable con estos parámetros")

    log.info("=" * 60)
    log.info("CONFIG SUGERIDA")
    log.info("  symbol     = %s", SYMBOL)
    log.info("  price_low  = %s", lo)
    log.info("  price_high = %s", hi)
    log.info("  span       = %s  (step real = %s)", span, span / Decimal(n))
    log.info("  n_grids    = %d  (cap por step=$%s era %d)", n, args.step, n_by_step)
    log.info("  investment = %s USDT", investment)
    log.info("  leverage   = %dx", leverage)
    log.info("  qty/grid   = %s ETH  → notional ≈ %s",
             qty_q, notional)
    log.info("  notional total nominal = %s", notional_total)
    log.info("=" * 60)
    log.info("CMD para lanzar smoke (testnet):")
    print(
        f"\n.venv/Scripts/python.exe -m scripts.phase5_live_smoke "
        f"--symbol {SYMBOL} "
        f"--low {lo} --high {hi} --grids {n} "
        f"--investment {investment} --leverage {leverage} "
        f"--direction neutral --tick {tick} --lot {lot} --minutes 60 "
        f"--bot-id eth-50x-1d\n"
    )


if __name__ == "__main__":
    asyncio.run(main())
