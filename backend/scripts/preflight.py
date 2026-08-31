"""Comprobación previa a la corrida de validación (Fase 5).

Responde, en ~10 segundos y **sin pagar el ajuste de ~80 s**, la única pregunta
que puede hacerte perder un día entero: *cuando levante el backend, ¿el
analista va a poder emitir sobre la barra de ahora?*

Es la pregunta cara porque el fallo es silencioso desde afuera. Un backend
puede quedar en verde, con `status: ok` y el feed conectado, y no emitir nunca
porque una familia de features llega con retraso y deja la cola en NaN. Se
descubre horas después, mirando que `resueltos` no sube.

Ejercita exactamente el mismo camino que el arranque real —`_load_inputs`,
`_assemble`, `assert_tail_observable`— y se detiene justo antes de `fit_bundle`,
que es la parte lenta y la única que este script no necesita.

Además reporta el margen que queda contra el acantilado de derivados de ~41 h,
que es el único daño irreversible de una pausa.

    uv run python scripts/preflight.py
    uv run python scripts/preflight.py --features price+deriv

Sale con código 0 si la corrida puede arrancar, 1 si no.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time

import numpy as np

from bob.live.analyst import LiveAnalyst
from bob.models.production import assert_tail_observable

DERIV_CLIFF_HOURS = 41.0


def _utc(ms: float | None) -> str:
    if ms is None:
        return "—"
    return dt.datetime.fromtimestamp(ms / 1000, dt.UTC).strftime(
        "%Y-%m-%d %H:%M UTC"
    )


async def _noop(_topic: str, _payload: object) -> None:
    return None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--symbol", default="ETHUSDT")
    p.add_argument("--timeframe", default="15m")
    p.add_argument(
        "--features",
        default="price+deriv",
        help="familias de features del vivo (default: la del backend)",
    )
    args = p.parse_args(argv)

    print(f"preflight — {args.symbol} {args.timeframe}, variante {args.features}\n")

    analyst = LiveAnalyst(
        args.symbol,
        args.timeframe,
        publish=_noop,
        feature_set=args.features,
        # No reparamos acá: el preflight OBSERVA, no arregla. Si falta algo,
        # queremos verlo, no que se tape solo y arranque distinto que el backend.
        repair_on_fit=False,
        persist=False,
    )

    t0 = time.perf_counter()
    inputs = analyst._load_inputs()
    X, names, sparse, _ = analyst._assemble(inputs)
    dt_load = time.perf_counter() - t0

    series = inputs.series
    now_ms = time.time() * 1000
    print(f"  velas        : {len(series):,}  última {_utc(series.open_time[-1])}")
    print(f"  matriz       : {X.shape[0]:,} × {X.shape[1]} features ({dt_load:.1f}s)")
    if sparse:
        print(f"  ralas        : {len(sparse)} columna(s) declaradas — {sorted(sparse)}")

    ok = True

    # 1. ¿La cola es observable? Es el chequeo que decide si emite o no.
    try:
        assert_tail_observable(X, names, sparse)
        print("  cola         : OK — todas las columnas densas por encima del 70%")
    except Exception as exc:  # el mensaje nombra las columnas culpables
        ok = False
        print(f"  cola         : FALLA — {exc}")

    # 2. Contigüidad: las ventanas de features cuentan BARRAS, no tiempo, así
    #    que un hueco corrompe toda la ventana de contexto que lo contenga.
    step = int(series.interval_ms)
    deltas = np.diff(series.open_time[-400:])
    huecos = int((deltas != step).sum())
    if huecos:
        ok = False
        print(f"  contigüidad  : FALLA — {huecos} hueco(s) en las últimas 400 barras")
        print("                 → uv run python -m bob.data.download "
              f"--symbol {args.symbol} --timeframe {args.timeframe} --repair")
    else:
        print("  contigüidad  : OK — últimas 400 barras sin huecos")

    # 3. Frescura de la vela: el feed va a escribir la siguiente, pero si la
    #    última es de hace horas el backfill todavía no corrió.
    edad_vela_min = (now_ms - series.open_time[-1]) / 60_000
    print(f"  última vela  : hace {edad_vela_min:.0f} min")

    # 4. El acantilado de derivados: el único daño irreversible.
    if inputs.derivatives is not None and len(inputs.derivatives.timestamp):
        ts = float(inputs.derivatives.timestamp[-1])
        edad_h = (now_ms - ts) / 3_600_000
        margen = DERIV_CLIFF_HOURS - edad_h
        estado = "OK" if margen > 17 else ("AJUSTADO" if margen > 0 else "PERDIDO")
        print(
            f"  derivados    : {estado} — último {_utc(ts)}, "
            f"{edad_h:.1f} h de atraso, margen {margen:.1f} h al acantilado"
        )
        if margen <= 0:
            ok = False
    else:
        ok = False
        print("  derivados    : FALLA — no hay serie de derivados cargada")

    print()
    if ok:
        print("LISTO PARA ARRANCAR.")
        print("  uv run python -m uvicorn bob.main:app --host 127.0.0.1 --port 8000")
    else:
        print("NO ARRANCAR TODAVÍA — resolver lo de arriba primero.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
