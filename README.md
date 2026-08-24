# BOB — Bot Operador Bursátil

Asistente de decisión para **trading intradía de futuros perpetuos** en
Binance. BOB observa el mercado en vivo, computa un estado estadístico
avanzado y muestra tres KPIs — **Seguridad (probabilidad calibrada)**,
proyección de profit (EV) y duración estimada de la tendencia — para que el
usuario decida si entrar o no. **BOB nunca ejecuta órdenes.**

Par inicial: `ETHUSDT` perp. El modelo es agnóstico del símbolo (watchlist
configurable).

> El build anterior (grid trading bot para GRVT) vive congelado en el branch
> `legacy/grvt-grid`.

## Requisitos

- Python 3.11+ y [uv](https://docs.astral.sh/uv/)
- Node.js 18+ con npm

## Setup

```bash
# 1. (Opcional) configurar environment — el backend bootea sin .env
cp .env.example backend/.env

# 2. Backend
cd backend
uv sync --extra dev
uv run uvicorn bob.main:app --reload
# → http://localhost:8000/api/health

# 3. Frontend (otra terminal)
cd frontend
npm install
npm run dev
# → http://localhost:5173
```

## Pipeline de forecasting

BOB **no pronostica el nivel del precio**. Predecir `close[t+H]` da R² ≈ 0.99
y no significa nada: el modelo aprende a copiar el último precio con un
retardo. El stack predice tres cosas que sí son predecibles y que el
asistente necesita:

| Target | Tipo | Alimenta |
|---|---|---|
| `P(TP antes que SL)` | Clasificación binaria calibrada | KPI 1 — Seguridad |
| Volatilidad realizada futura | Regresión | Dimensionado de TP/SL, KPI 2 |
| Intervalo del retorno a H barras | Predicción conformal (CQR + ACI) | Cono de precio |

Todo se valida con **walk-forward purgado con embargo** (los labels de
triple-barrier se solapan; un K-Fold estándar filtra futuro y sale inflado),
contra baselines reales — tasa base, random walk, EWMA/RiskMetrics,
GARCH(1,1) y HAR-RV — y con test de **Diebold-Mariano** para saber si la
diferencia es distinguible de la suerte.

```bash
cd backend

# 1. Histórico a SQLite (idempotente, reanuda solo)
uv run python -m bob.data.download --symbol ETHUSDT --timeframe 15m --months 24
uv run python -m bob.data.download --status

# 2. Experimento walk-forward completo
uv run python -m bob.backtest.runner --symbol ETHUSDT --timeframe 15m --folds 6
```

Cada run escribe `backend/artifacts/<run_id>.txt` (reporte legible) y
`.json` (resultado completo), y persiste una fila en `BacktestRun`.

La deducción del método, las decisiones de diseño con sus alternativas
descartadas y los límites conocidos están en **`docs/PROBABILITY_MODEL.md`**.

## Tests

```bash
cd backend
uv run python -m pytest      # NO usar `uv run pytest` en Windows (bug del trampoline)

# Con cobertura de las capas puras
uv run python -m pytest --cov=bob.signals --cov=bob.models --cov=bob.backtest --cov=bob.data
```

Dos tests sostienen las invariantes que, si se rompen, corrompen todo en
silencio (porque los resultados salen **mejores**, no peores):

- `test_mutar_el_futuro_no_altera_el_pasado` — sin lookahead.
- `test_escalar_el_precio_no_cambia_los_features` — features adimensionales,
  que es lo que hace al motor agnóstico del símbolo.

## Documentación

- `CLAUDE.md` — identidad, arquitectura, KPIs, fases y reglas del proyecto
- `docs/PROBABILITY_MODEL.md` — deducción del stack de forecasting
- `docs/DATA_SOURCES.md` — endpoints de Binance/CoinGecko/etc. y sus trampas
- `docs/HANDOFF_FASE1.md` — estado al cierre de Fase 0 y qué sigue
