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

## Tests

```bash
cd backend
uv run python -m pytest      # NO usar `uv run pytest` en Windows (bug del trampoline)
```

## Documentación

- `CLAUDE.md` — identidad, arquitectura, KPIs, fases y reglas del proyecto
- `docs/DATA_SOURCES.md` — endpoints de Binance/CoinGecko/etc. y sus trampas
- `docs/HANDOFF_FASE1.md` — estado al cierre de Fase 0 y qué sigue
