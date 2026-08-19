# HANDOFF — Fase 0 completada, arrancar Fase 1

> Escrito el 2026-08-19 al cierre de la Fase 0 (sesión Fable). Destinatario:
> la próxima sesión (Opus 5 o quien sea). Leer CLAUDE.md completo primero —
> este archivo solo dice dónde quedó todo y qué sigue.

## Estado al cierre de Fase 0

- **Pivote hecho**: BOB es asistente de decisión (nunca ejecuta órdenes).
  Todo el build grid/GRVT vive congelado en el branch `legacy/grvt-grid`.
- **Backend booteable sin `.env`**: `cd backend && uv run uvicorn bob.main:app`
  → `GET /api/health` responde con watchlist/timeframe/threshold.
- **Tests: 35 en verde** — markov (con `expected_regime_duration` nuevo,
  base del KPI 3) e indicators (ATR, percentile, suggest_range, volatility).
- **Frontend compila** (`npm run build`): shell con 5 páginas placeholder
  (Signal, Analysis, Backtest, History, Settings), WS hook y paleta de
  CLAUDE.md. El contenido real es Fase 6.
- **Esquema de DB definido** en `backend/src/bob/db/models.py`: CandleRecord,
  Signal, PaperTrade, BacktestRun, SentimentSnapshot — leerlo antes de tocar
  cualquier fase, es el contrato entre capas. Convención: precios como `str`
  (Decimal), timestamps de velas en epoch ms UTC.
- Cada paquete nuevo (`data/`, `signals/`, `models/`, `backtest/`, `paper/`,
  `live/`, `alerts/`) tiene en su `__init__.py` el docstring de qué módulos
  le tocan y en qué fase.

## Gotchas de entorno (Windows, esta máquina)

- `uv run pytest` falla con "uv trampoline failed to canonicalize script
  path" — **usar `uv run python -m pytest`**. Mismo patrón para cualquier
  entry point con .exe trampoline.
- El venv resolvió a **Python 3.14.3**. Funciona para lo actual, pero en
  Fase 3 `hmmlearn`/`scikit-learn` pueden no tener wheels para 3.14 en
  Windows (mismo problema que tuvo THECREW con CrewAI). Si `uv sync` falla
  al agregarlas: `uv python pin 3.12` y resync — nada del código depende
  de 3.14.
- Frontend: `npm` (hay package-lock.json), no pnpm.

## Fase 1 — Pipeline de datos Binance (el siguiente paso)

Leer `docs/DATA_SOURCES.md` — tiene endpoints, formatos, rate limits y las
trampas conocidas (klines posicionales, corte WS a las 24h, ventana de 30
días del OI histórico, flag `k.x` de vela cerrada).

Tareas en orden:

1. `data/binance_rest.py` — cliente httpx: exchangeInfo (tick/step size),
   klines paginadas por startTime, funding history. Token bucket
   autorregulado con el header `X-MBX-USED-WEIGHT-1M`.
2. `data/store.py` — upsert de klines a `CandleRecord` (única por
   symbol+timeframe+open_time). Comando/función para descargar histórico:
   objetivo **12+ meses de ETHUSDT 15m** (~35k velas), que es el insumo del
   backtest de Fase 4.
3. `data/binance_ws.py` — una conexión multiplexada para la watchlist:
   kline + aggTrade + markPrice (depth20 puede esperar a Fase 2).
   Reconexión con backoff+jitter y resuscripción (patrón de referencia en
   `legacy/grvt-grid:backend/src/bob/grvt/ws_market.py`). Emitir velas
   cerradas (`k.x == true`) hacia el store y hacia `broadcast_hub`
   ("market.tick").
4. Arrancar el hub en el `lifespan` de `main.py` (el hueco está comentado).
5. **Empezar a snapshotear OI y ratios long/short a DB desde ya** (aunque
   el feature se use en Fase 2): la historia gratis es de ~30 días, cada
   día que pasa sin persistir es un día menos de entrenamiento futuro.

**Criterio de cierre de Fase 1** (equivalente al test de la fase en
CLAUDE.md): 90 días de ETHUSDT 15m persistidos en DB + stream en vivo
recibiendo velas y reconectando solo tras un corte forzado.

## Reglas que más riesgo tienen de romperse sin querer

- Regla 5 (sin lookahead): la vela en curso no es una vela. Todo feature se
  computa sobre velas cerradas.
- Regla 3 (pureza): `data/` es el único paquete que toca red. Si un módulo
  de `signals/` o `models/` importa httpx/websockets, está mal diseñado.
- Restricción 1: no existe código de ejecución de órdenes. Ni "por si acaso".
