# BOB — Bot Operador Bursatil

Grid trading bot para perpetuos en [GRVT](https://grvt.io) (Gravity Markets).

Objetivo dual: generar profit del grid + farmear puntos del airdrop GRVT.

## Requisitos

- Python 3.11+
- Node.js 18+ con pnpm
- [uv](https://docs.astral.sh/uv/) (gestor de paquetes Python)

## Setup

```bash
# 1. Clonar y configurar environment
cp .env.example .env
# Editar .env con tus credenciales de GRVT

# 2. Backend
cd backend
uv sync
uv run uvicorn bob.main:app --reload

# 3. Frontend (otra terminal)
cd frontend
pnpm install
pnpm dev
```

## Uso

Abrir `http://localhost:5173` para el dashboard.

1. **Settings** — configurar API keys y ambiente
2. **Trading** — crear y configurar bots de grid
3. **Bots** — monitorear bots activos
4. **History** — ver PnL, trades, fees
5. **Points** — tracker del airdrop GRVT

## Ambientes

- **testnet**: desarrollo y testing (default)
- **prod**: operacion real (requiere validacion completa)

No usar prod hasta completar la validacion end-to-end de 72h en testnet.
