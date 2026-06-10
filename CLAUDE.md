# BOB — Grid Trading Bot para GRVT

> Contexto estratégico del ecosistema (memoria de LUPE):
> @../lupe/contexto/MEMORIA.md

## IDENTIDAD DEL PROYECTO

BOB (**B**ot **O**perador **B**ursátil) es un sistema de **grid trading sobre perpetuos** que opera en **GRVT** (Gravity Markets), clon funcional del Futures Grid de Pionex pero adaptado a GRVT y optimizado para dos objetivos simultáneos: **generar profit del grid + farmear puntos del airdrop GRVT** (TGE confirmado para fines de junio 2026).

Se gestiona 100% desde un dashboard web — el usuario nunca toca la línea de comandos después del setup inicial.

**Usuario:** Nichelson — operador con criterio técnico. Ya pasó por una liquidación; no vale repetir historia por falta de testing.
**Norte:** herramienta operativa real, no prototipo académico. Código sobrio, modular, con kill switch global y testing riguroso del motor de grid.

---

## OBJETIVO DUAL

BOB existe para dos cosas, en este orden:

1. **Farmear puntos GRVT durante la ventana pre-TGE** (≈ abril → junio 2026). Los puntos vienen de: volumen de trading, open interest mantenido, TVL, liquidez provista.
2. **Generar profit neto** del grid o, mínimo, **operar break-even** después de fees y funding.

El diseño **no sacrifica el #2 por el #1**. No se trata de quemar capital para farmear. Se trata de usar una estrategia (grid) que genera volumen y OI por naturaleza, mientras mantiene drawdown controlado.

---

## STACK TÉCNICO

### Backend
- **Python 3.11+** (asyncio + uvloop para performance)
- **FastAPI** (REST + WebSocket nativo)
- **SQLModel** sobre SQLite (persistencia, simple y suficiente para v1)
- **pygrvt** (SDK oficial Python de GRVT) + **CCXT** como fallback
- **websockets** para streams de GRVT
- **pydantic v2** para validación
- **loguru** para logging (mejor DX que logging stdlib)
- **APScheduler** para tareas periódicas (snapshot de puntos, etc.)

### Frontend
- **React 18 + Vite + TypeScript**
- **Tailwind CSS** + **shadcn/ui** para componentes
- **Zustand** para state client; **TanStack Query** para state de server
- **lightweight-charts** (TradingView, open source) para el chart principal con overlay del grid
- **Recharts** para métricas secundarias (PnL curves, puntos)
- **react-hook-form** + **zod** para forms de configuración de bot

### Infra mínima
- **Docker Compose** opcional (un container backend, uno frontend); correr local con `uv` + `pnpm` debe funcionar igual
- Todas las credenciales via `.env`; `.env.example` como referencia
- Config de bots en DB, no en YAML (se edita desde el dashboard)

---

## ESTRUCTURA DEL PROYECTO

```
bob/
├── CLAUDE.md                       # Este documento
├── README.md                       # Setup + uso
├── docker-compose.yml              # Opcional
├── .env.example
│
├── backend/
│   ├── pyproject.toml              # uv
│   ├── src/bob/
│   │   ├── main.py                 # Entry FastAPI
│   │   ├── config.py               # Settings (pydantic-settings)
│   │   ├── db/
│   │   │   ├── models.py           # SQLModel: Bot, Order, Fill, Snapshot
│   │   │   └── session.py
│   │   ├── grvt/
│   │   │   ├── auth.py             # EIP-712 + session cookie
│   │   │   ├── client.py           # Wrapper sobre pygrvt / ccxt
│   │   │   ├── ws_market.py        # WS market data (compartido)
│   │   │   ├── ws_trading.py       # WS trading (auth, por subcuenta)
│   │   │   ├── rest.py             # REST endpoints (orders, positions, etc.)
│   │   │   └── points.py           # Consulta de puntos / epochs
│   │   ├── grid/
│   │   │   ├── engine.py           # Motor de grid (PURO, sin I/O)
│   │   │   ├── bot.py              # GridBot: instancia ejecutable
│   │   │   ├── manager.py          # BotManager: registry + orquestación
│   │   │   ├── spacing.py          # Arithmetic / geometric levels
│   │   │   └── state_machine.py    # Estados del bot: idle/running/paused/stopped/error
│   │   ├── api/
│   │   │   ├── routes/
│   │   │   │   ├── bots.py         # CRUD + start/stop/pause
│   │   │   │   ├── markets.py      # Símbolos disponibles, precios
│   │   │   │   ├── history.py      # PnL, fills, trades
│   │   │   │   ├── points.py       # Tracking GRVT airdrop
│   │   │   │   └── settings.py     # API keys, ambiente, kill switch
│   │   │   └── ws.py               # WebSocket al frontend (broadcast)
│   │   ├── services/
│   │   │   ├── pnl.py              # Cálculo realized/unrealized
│   │   │   ├── fees.py             # Tracking de fees pagados
│   │   │   ├── funding.py          # Funding rate tracker
│   │   │   └── risk.py             # Kill switch, límites globales
│   │   └── utils/
│   │       ├── math.py             # Spacing, quantization
│   │       └── time.py
│   └── tests/
│       ├── test_grid_engine.py     # CRÍTICO — cobertura alta
│       ├── test_spacing.py
│       ├── test_state_machine.py
│       ├── test_pnl.py
│       └── test_bot_manager.py
│
├── frontend/
│   ├── package.json                # pnpm
│   ├── vite.config.ts
│   ├── src/
│   │   ├── main.tsx
│   │   ├── App.tsx
│   │   ├── pages/
│   │   │   ├── TradingPage.tsx     # Home — crear bot (réplica Pionex)
│   │   │   ├── BotsPage.tsx        # Bots activos
│   │   │   ├── HistoryPage.tsx     # PnL, trades, equity curve
│   │   │   ├── PointsPage.tsx      # Airdrop tracker
│   │   │   └── SettingsPage.tsx    # API keys, ambiente
│   │   ├── components/
│   │   │   ├── layout/             # Sidebar, Topbar
│   │   │   ├── trading/            # Chart, GridOverlay, OrderBook, ConfigPanel
│   │   │   ├── bots/               # BotCard, BotControls
│   │   │   ├── common/             # shadcn wrappers
│   │   │   └── kill-switch/        # Botón global de emergencia
│   │   ├── hooks/
│   │   │   ├── useWebSocket.ts
│   │   │   ├── useBots.ts
│   │   │   └── useMarketData.ts
│   │   ├── stores/                 # Zustand
│   │   └── lib/
│   │       ├── api.ts              # Cliente REST
│   │       └── types.ts            # Types compartidos con backend
│   └── index.html
│
└── docs/
    ├── GRID_MATH.md                # Deducción de las fórmulas
    └── GRVT_INTEGRATION.md         # Notas del proceso de auth
```

---

## GRVT — CONCEPTOS CLAVE

### Autenticación (leer antes de cablear cualquier cosa)

GRVT usa **EIP-712** (Ethereum typed data signing) + **session cookie**.

Flujo:
1. Usuario provee `GRVT_API_KEY` + `GRVT_SUB_ACCOUNT_ID` (desde la UI de GRVT)
2. POST a `{AUTH_ENDPOINT}/auth/api_key/login` con el api_key
3. Response setea cookie `gravity=...` y header `X-Grvt-Account-Id`
4. Ambos se usan en todas las requests subsecuentes (REST y WS)

**Endpoints por ambiente:**

| Ambiente | Auth endpoint | Market data WS | Trading WS |
|---|---|---|---|
| testnet | `https://edge.testnet.grvt.io/auth/api_key/login` | `wss://market-data.testnet.grvt.io/ws/full` | `wss://trades.testnet.grvt.io/ws/full` |
| prod | `https://edge.grvt.io/auth/api_key/login` | `wss://market-data.grvt.io/ws/full` | `wss://trades.grvt.io/ws/full` |

Default en `.env`: **testnet**. Cambiar a prod requiere edit explícito del archivo.

### Órdenes

- GRVT usa payload de orden unificado con firma EIP-712 por cada orden enviada. Esto es **trustless settlement** — el motor de matching no puede ejecutar órdenes que el usuario no haya firmado.
- El SDK `pygrvt` maneja esto internamente. **No reimplementar**.
- Soporta: `LIMIT`, `MARKET`, `STOP`, `POST_ONLY`. El grid usa exclusivamente **LIMIT POST_ONLY** (maker, obtiene rebate y más puntos).

### Símbolos

- Formato: `BTC_USDT_Perp`, `ETH_USDT_Perp`, `SOL_USDT_Perp`, etc.
- Lista completa via endpoint `v1/instruments`
- **Altcoins pagan más puntos** (multiplier vigente según epoch — verificar en dashboard GRVT al inicio)

### Funding rate

- Se cobra cada 8h. Para perpetuos en mercado lateral puede ser **el principal sink de capital** del grid.
- BOB debe loggearlo y mostrarlo en el dashboard como métrica separada (no enterrada en "fees").

### WebSocket subscriptions relevantes

- `v1/kline` (velas, para chart)
- `v1/orderbook` (profundidad)
- `v1/trade` (último trade)
- `v1/mark.price` (para PnL unrealized)
- `v1.fill` (auth, stream de fills del usuario)
- `v1.order` (auth, stream de updates de órdenes)
- `v1.state` (auth, posiciones)

---

## GRID TRADING — MECÁNICA

### Config de un bot (réplica Pionex)

```python
class BotConfig:
    symbol: str                  # "BTC_USDT_Perp"
    direction: Literal["long", "short", "neutral"]
    price_low: Decimal           # límite inferior del rango
    price_high: Decimal          # límite superior del rango
    n_grids: int                 # 2..500
    investment_usdt: Decimal     # capital asignado
    leverage: int                # 1x..20x (default 3x, cap configurable)
    spacing: Literal["arithmetic", "geometric"]  # default arithmetic
    stop_loss_pct: Optional[Decimal]  # desde precio de entrada
    take_profit_pct: Optional[Decimal]
    out_of_range_action: Literal["pause", "close", "trail"]  # default pause
```

### Cálculo de niveles

**Arithmetic** (default — más simple, intuitivo):
```
step = (price_high - price_low) / n_grids
levels = [price_low + i * step for i in range(n_grids + 1)]
```

**Geometric** (mejor para altcoins volátiles — niveles más densos cerca del bajo):
```
ratio = (price_high / price_low) ** (1 / n_grids)
levels = [price_low * (ratio ** i) for i in range(n_grids + 1)]
```

Todos los niveles deben **cuantizarse al tick size** del símbolo antes de enviar órdenes.

### Semántica de dirección

- **Long**: bot espera mercado alcista/lateral. Capital empieza en USDT. Coloca N órdenes `BUY LIMIT` debajo del precio actual y N órdenes `SELL LIMIT` arriba. Cada buy fill genera un sell al nivel siguiente. Max exposición: long.
- **Short**: inverso. Empieza opening una posición short equivalente al capital dividido en grids. Cada sell fill genera un buy al nivel siguiente. Max exposición: short.
- **Neutral**: el bot arranca comprando/vendiendo hasta quedar con posición cero en el centro del rango. Coloca grids en ambos lados simétricamente. Ideal para mercados sin sesgo.

### Fill cascade

Cuando un `BUY @ level_i` se llena:
1. Cancelar el sell correspondiente si existe
2. Colocar `SELL @ level_{i+1}` con `quantity = quantity_del_buy + ajuste_de_fee`
3. Marcar el trade como "grid trade" en DB, registrar profit teórico

Y viceversa para sells.

### Tamaño por grid

```
qty_per_grid = (investment_usdt * leverage) / (n_grids * avg_price)
```

Con `avg_price` calculado según spacing. Cuantizar al lot size del símbolo.

### Profit por grid (display en dashboard)

```
gross_profit_per_grid = (step / avg_price) - (2 * taker_fee)   # arithmetic
# o equivalente para geometric
net_profit_per_grid = gross_profit_per_grid - funding_estimate
```

Mostrar rango `[min, max]` como hace Pionex.

### Out-of-range: qué hacer cuando el precio sale del rango

3 modos, configurables:

- **`pause`** (default): cancela órdenes colgadas del lado correspondiente, mantiene posición, espera que el precio vuelva al rango.
- **`close`**: cierra toda la posición a mercado, detiene el bot, notifica al usuario.
- **`trail`** (v2): desplaza el rango en la misma dirección del break (implementar solo si las fases 1-7 están estables).

### Kill switch global

Botón rojo, prominente en el dashboard. Al presionarlo:
1. Pausa todos los bots (no cierra posiciones)
2. Cancela todas las órdenes abiertas en GRVT
3. Notifica al usuario qué posiciones quedaron abiertas
4. El usuario decide manualmente cerrar a mercado o esperar

**Nunca cerrar posiciones automáticamente sin confirmación** — puede empeorar la situación en momentos de liquidez baja.

---

## DASHBOARD — ESPECIFICACIÓN

Réplica funcional del Futures Grid de Pionex (ver screenshot de referencia). Tema **dark minimalista**. Paleta: fondo `#0e0f0f`, verde long `#3cd9a8`, rojo short `#f07045`, amarillo alerts `#f5b731`.

### Layout general
- Sidebar izquierda fija (nav entre pages)
- Topbar con: selector de ambiente (testnet/prod), status de conexión a GRVT, balance funding+trading, **kill switch**
- Main content según page

### Page 1: Trading (crear bot nuevo)
Réplica del panel de Pionex:
- **Izquierda (main)**: chart TradingView con velas + overlay de líneas horizontales del grid que se previsualizan mientras el usuario configura rango/grids
- **Derecha (panel config)**:
  - Tabs `Copy strategy` (v2) | `Customize` (v1)
  - Toggle `Long | Short | Neutral`
  - Inputs `Price Range` (low, high) con validación vs precio actual
  - Slider + input `Quantity of Grids` (2-500) con botón "Recommended" que sugiere basado en volatilidad histórica 24h
  - Preview `Profit/grid (fee deducted)`: rango min-max
  - Input `Investment (USDT)` con leverage selector (1x, 3x, 5x, 10x)
  - Preview `Actual Investment + Extra Margin`
  - Botón `Create Bot` (grande, verde si long, rojo si short)

### Page 2: Active Bots
- Grid de cards, una por bot activo
- Cada card muestra:
  - Símbolo + dirección + leverage
  - Mini chart con precio actual y líneas del grid
  - Métricas: grids ejecutados, volumen acumulado, PnL realized, PnL unrealized, runtime
  - Botones: `Pause`, `Stop`, `Details`
- Soporta **múltiples bots en paralelo** (ej: BTC long + ETH neutral + SOL short corriendo simultáneamente)

### Page 3: History
- Equity curve global (todos los bots)
- Tabla de trades ejecutados (sortable, filterable por bot/símbolo/fecha)
- Breakdown de fees pagados, funding pagado/cobrado, profit neto
- Exportable a CSV

### Page 4: Points (GRVT Airdrop tracker) — **CRÍTICO**
- **Total points acumulados** (consulta a API de GRVT)
- **Points este epoch** + días restantes del epoch (epochs son 7 días)
- Desglose por fuente:
  - Trading volume (points estimados)
  - Open interest mantenido
  - TVL
  - Liquidity provision
- **Volumen semanal por símbolo** (altcoins destacados con multiplier del epoch)
- Proyección lineal de puntos al cierre del epoch
- Histórico de epochs pasados

### Page 5: Settings
- API key management (masked, nunca mostrar en claro después de guardar)
- Toggle ambiente: `testnet / prod` con **confirmación doble para cambio a prod**
- Límites globales: max bots simultáneos, max capital total, max leverage global
- Configuración de alertas (v2)

---

## ARQUITECTURA MULTI-BOT

### `BotManager` (singleton)
Mantiene registry de `GridBot` instances. Responsable de:
- Crear / iniciar / pausar / detener bots
- Compartir una única conexión WS de market data entre todos los bots
- Delegar WS de trading por sub-cuenta (GRVT permite múltiples sub-cuentas por usuario)
- Persistir estado en DB en cada transición
- Re-hidratar bots desde DB al reiniciar el proceso

### `GridBot` (instancia ejecutable)
Cada bot corre en su propio `asyncio.Task`. Loop principal:
1. Subscribe a fills/orders de su símbolo
2. En cada fill, ejecuta la cascade logic
3. En cada price update relevante, verifica out-of-range
4. Reporta estado al `BotManager` via queue

### State machine

```
    idle → starting → running ⇄ paused
                        ↓
                     stopping → stopped
                        ↓
                      error → (requiere intervención manual)
```

Transiciones se persisten en DB. Si el proceso muere y reinicia, cada bot lee su último estado y reanuda apropiadamente.

### Compartir recursos
- **WS de market data**: una sola conexión, multiplexada por símbolo. El `MarketDataHub` distribuye a los bots suscritos.
- **WS de trading**: una por sub-cuenta. Si varios bots usan la misma sub-cuenta, se comparte.
- **Rate limits**: respetar los límites de GRVT. El `BotManager` implementa un token bucket global para órdenes.

---

## FASES DE CONSTRUCCIÓN

Ejecutar en orden. **No saltar fases**. Al final de cada una, resume qué hiciste y espera confirmación.

### Fase 1 — Setup + GRVT auth (testnet)
1. Scaffold backend y frontend (estructura vacía arriba)
2. `.env.example` con todas las vars documentadas
3. `backend/src/bob/grvt/auth.py`: login con API key → obtiene cookie y account_id
4. Endpoint `/api/health/grvt` que retorna status de la conexión
5. Test manual: correr backend, hacer `curl /api/health/grvt`, ver que retorna OK con ambiente testnet

### Fase 2 — Market data pipeline
1. `ws_market.py`: cliente WS de market data, maneja reconexiones, expone `AsyncIterator` por símbolo
2. REST fallback para klines históricas
3. Endpoint `/api/markets/symbols` y `/api/markets/ticker/{symbol}`
4. Test: suscribirse a BTC_USDT_Perp en testnet, recibir al menos 10 klines de 1m

### Fase 3 — Grid engine (PURO, sin I/O)
1. `grid/spacing.py`: funciones puras para generar levels (arithmetic + geometric)
2. `grid/engine.py`: lógica del grid. Recibe `BotConfig` + `market_state` + `current_orders`, retorna `Actions` (lista de órdenes a colocar/cancelar). **Ningún I/O acá**.
3. `grid/state_machine.py`
4. **Tests obligatorios con cobertura ≥ 90%** en este módulo:
   - Spacing correcto (arithmetic, geometric)
   - Fill cascade en los 3 modos (long, short, neutral)
   - Out-of-range handling (3 modos)
   - Cuantización correcta a tick/lot size
   - Edge cases: 2 grids, 500 grids, rango muy estrecho, rango muy amplio
   - PnL cálculo en todos los escenarios

### Fase 4 — Paper trading mode
1. Wrapper sobre el engine que simula fills con datos de market data real (sin enviar a GRVT)
2. Correr un bot en paper por 4h, verificar que la lógica se comporta como se espera
3. Persistir todo en DB como si fuera real, con flag `mode=paper`

### Fase 5 — Order execution (testnet)
1. `grvt/rest.py`: place_order, cancel_order, get_open_orders
2. `ws_trading.py`: stream de fills del usuario
3. `GridBot` real: conecta engine + GRVT
4. Correr **1 solo bot** en testnet con capital simulado, 1h mínimo. Verificar todos los grids se ejecutan correctamente y la DB refleja fielmente lo que pasa en GRVT.

### Fase 6 — Backend API completa
1. Rutas REST para CRUD de bots + start/stop/pause
2. WebSocket server para broadcast al frontend (updates de bots, fills, precio)
3. Endpoints de history, pnl, points
4. Tests de integración de las rutas principales

### Fase 7 — Frontend dashboard
1. Usa el skill `/mnt/skills/public/frontend-design/SKILL.md` si está disponible antes de empezar UI
2. Implementar las 5 páginas en orden: Settings → Trading → Bots → History → Points
3. Prioridad: funcional > bonito. Luego iterar UI.
4. Validar que el kill switch global funciona end-to-end

### Fase 8 — Multi-bot orchestration
1. `BotManager` real con múltiples bots concurrentes
2. Test: correr 3 bots simultáneos en testnet (BTC long + ETH neutral + SOL short)
3. Validar que el fill de un bot no contamina estado de otros
4. Validar que kill switch corta todos

### Fase 9 — Points tracker
1. `grvt/points.py`: consultar API de puntos / epochs
2. Page 4 del dashboard
3. Snapshots periódicos a DB para tracking temporal
4. Proyecciones

### Fase 10 — Validación end-to-end
1. Correr en testnet durante **al menos 72 horas continuas** con 3 bots
2. Reiniciar el proceso a propósito 2-3 veces para verificar re-hidratación
3. Forzar scenarios de error (internet cae, GRVT responde 500, etc.)
4. Checklist de observabilidad: ¿todos los logs relevantes están? ¿todas las métricas se ven en el dashboard?

### Fase 11 — Switch a mainnet
1. Cambio de ambiente via UI (con confirmación doble)
2. Deposito mínimo en cuenta GRVT real
3. **Un solo bot**, capital mínimo (≤$50), 24h de observación
4. Escalar gradualmente según comportamiento

---

## REGLAS DE OPERACIÓN

1. **Testing del motor de grid es innegociable.** Cobertura ≥ 90% en `grid/*` antes de pasar a Fase 4. Bugs en esta capa = pérdida de dinero.
2. **Nunca enviar órdenes sin cuantizar** al tick/lot size del símbolo. GRVT las rechaza y ensuciamos el estado.
3. **Siempre POST_ONLY** para órdenes del grid. Si el tick hace que la orden se convertiría en taker, se ajusta en 1 tick para que sea maker, o se reintenta.
4. **Loggear todo**: cada orden enviada, cada fill, cada cancel, cada error de GRVT. `loguru` con rotación diaria.
5. **Idempotencia**: cada orden lleva un `client_order_id` determinístico `{bot_id}-{level_idx}-{side}-{timestamp_bucket}`. Si se reintenta, no duplica.
6. **Rate limiting**: respetar límites de GRVT con token bucket. Nunca rafagear.
7. **Reconexiones**: todos los WS con exponential backoff + jitter. Re-suscribir a streams después de reconectar.
8. **Typing obligatorio** en backend (mypy en CI si hay CI; manual si no).
9. **TypeScript estricto** en frontend. `strict: true` en tsconfig.
10. **Commits pequeños por fase**. Un módulo funcional por commit.

---

## RESTRICCIONES

- **No tocar mainnet hasta terminar Fase 10.** Esta regla no se negocia.
- **No persistir nunca API keys en claro**. Usar `pydantic.SecretStr` + encryption-at-rest si se puede; mínimo, solo `.env` fuera del repo.
- **No autoclose de posiciones** desde kill switch u out-of-range "close" sin confirmación del usuario en casos ambiguos.
- **No agregar features fuera de scope** (ej: strategies distintas a grid, indicadores técnicos, backtesting completo) hasta Fase 11.
- **No usar librerías de trading all-in-one** (backtrader, freqtrade, jesse). BOB es específico para grid + GRVT — capa de indirección adicional = más bugs.
- **No subir el `.env` ni las keys al repo**. `.gitignore` desde el commit 1.

---

## PRIMER OUTPUT ESPERADO

Al final de la Fase 1, corriendo:

```bash
cd backend && uv run uvicorn bob.main:app --reload
```

Debo poder hacer:

```bash
curl http://localhost:8000/api/health/grvt
```

Y recibir:

```json
{
  "status": "ok",
  "environment": "testnet",
  "authenticated": true,
  "funding_account": "0x...",
  "sub_account_id": "123456789"
}
```

Al final de la Fase 7 debo poder abrir `http://localhost:5173` y:
- Ver el dashboard
- Ir a Settings, pegar mis API keys
- Ir a Trading, configurar un bot BTC_USDT_Perp neutral con rango y grids
- Ver la preview del grid en el chart
- Crear el bot (en testnet)
- Ir a Bots, verlo corriendo

---

## ARRANQUE

Lee este documento completo. Luego:

1. Resume en 5 líneas qué entendiste del objetivo del proyecto.
2. Lista las decisiones técnicas con las que estás de acuerdo y las que cuestionarías. **Si cuestionas algo, justifica técnicamente** — no por cambiar por cambiar.
3. Crea la estructura de carpetas vacía.
4. Escribe `.env.example`, `README.md`, y los `pyproject.toml` / `package.json` iniciales.
5. **Espera confirmación antes de empezar Fase 1.**

Ante decisiones no cubiertas aquí: pregunta. No asumas.
