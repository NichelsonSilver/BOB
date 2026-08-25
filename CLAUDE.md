# BOB — Asistente de Decisión para Trading Intradía

> Contexto estratégico del ecosistema (memoria de LUPE):
> @../lupe/contexto/MEMORIA.md

## IDENTIDAD DEL PROYECTO

BOB (**B**ot **O**perador **B**ursátil) pivoteó el 2026-08-19, por decisión
explícita de Nichelson, de bot ejecutor de grid en GRVT a **asistente de
decisión para trading intradía de futuros perpetuos en Binance**.

BOB **nunca ejecuta órdenes**. Su trabajo es observar el mercado en vivo,
computar un estado estadístico avanzado, y decirle al usuario con honestidad
probabilística: *"este es el escenario más seguro y probable para una
ganancia, este es el riesgo, tú decides"*. El usuario entra manualmente en
Binance, típicamente apalancado — por eso la calidad de la probabilidad es
la razón de existir del proyecto.

**Usuario:** Nichelson — trader intradía, **averso al riesgo**, ya pasó por
una liquidación. Busca pocas entradas de alta convicción, no muchas señales.
**Par inicial:** `ETHUSDT` perpetuo (Binance Futures). El modelo es agnóstico
del símbolo: cualquier par de la watchlist debe funcionar sin cambios de código.
**Norte:** herramienta operativa real. Un KPI de probabilidad que no está
calibrado contra datos reales es peor que no tener KPI — está prohibido
mostrarlo sin validación.

### Historia previa (no borrar)

El build anterior (grid trading GRVT, fases 1-5 completadas, smoke tests en
vivo en testnet) está **congelado íntegro en el branch `legacy/grvt-grid`**.
No se toca, no se mezcla. Si el airdrop GRVT revive interés, está a un
checkout de distancia. De ese build heredamos y mantenemos en main:

- `markov.py` → base del detector de régimen (se le sube el nivel, ver Modelos)
- `range_suggestion.py` → ATR, percentiles, volatilidad → migra a `signals/indicators.py`
- Infraestructura FastAPI + WebSocket broadcast + SQLModel/SQLite
- Frontend React/Vite/TS/Tailwind (shell, layout, hooks WS, dark theme)
- Patrón de cliente WS con reconexión + backoff (`ws_market.py`)

---

## LOS TRES KPIs

### KPI 1 — Seguridad (%) — EL PRINCIPAL

Probabilidad calibrada de que un setup concreto alcance su take-profit antes
que su stop-loss:

```
Seguridad = P( precio toca TP antes que SL | estado actual del mercado )
```

donde el setup es `(dirección, TP = +X%, SL = −Y%, horizonte H)`. No es un
"score de confianza" arbitrario: es una probabilidad que se valida — si el
modelo dice 75%, debe acertar ≈75% de las veces en datos que nunca vio
(walk-forward) y en las señales emitidas en vivo (paper tracking).

Componentes que alimentan el KPI (cada uno es un feature, el modelo aprende
los pesos — no se ponderan a mano):

1. **Régimen de mercado** (Markov/HMM): ranging / trending↑ / trending↓ / volatile
2. **Técnicos**: ATR, EMAs, RSI, VWAP, estructura de máximos/mínimos
3. **Microestructura**: imbalance del orderbook, delta de volumen comprador/vendedor,
   ratio taker buy/sell — relevante porque ~70-90% del volumen es algorítmico
   y deja huellas sistemáticas
4. **Derivados**: funding rate, open interest y sus deltas, ratio long/short
5. **Sentimiento/contexto**: Fear & Greed, dominancia BTC, marketcap global

**Regla de emisión**: BOB solo emite señal cuando Seguridad ≥ umbral
(configurable en dashboard, default 70%). Pocas señales buenas > muchas mediocres.

**Regla de honestidad**: junto al KPI se muestra SIEMPRE la precisión
histórica real del modelo en ese rango de probabilidad ("cuando BOB dijo
70-80%, acertó el 74% de las veces, n=212"). Sin ese respaldo, el número
se muestra en gris con etiqueta "sin calibrar".

### KPI 2 — Proyección de profit

Para cada señal: TP y SL sugeridos (derivados de ATR y niveles de
liquidez), y la **esperanza matemática** del trade:

```
EV = P_win × TP% − (1 − P_win) × SL%       (neto de fees + funding estimado)
```

Se muestra sin leverage y con el leverage que el usuario configure,
incluyendo **precio de liquidación estimado** — para un usuario averso al
riesgo, ver la distancia a liquidación es tan importante como ver el profit.

### KPI 3 — Duración estimada de la tendencia (secundario)

Tiempo esperado de permanencia en el régimen actual, derivado de la matriz
de transición de Markov (`E[duración] = 1 / (1 − p_permanencia)` en unidades
del timeframe). Es proyectable pero de baja precisión — se muestra como rango
con banda de incertidumbre, y el seguimiento fino se hace **in-live**: el KPI
de Seguridad se recalcula con cada tick y el dashboard muestra su evolución
durante el trade abierto (si cae bajo un umbral de salida, alerta).

---

## STACK TÉCNICO

### Backend (se mantiene del build anterior)
- **Python 3.11+** (asyncio)
- **FastAPI** (REST + WebSocket)
- **SQLModel** sobre SQLite
- **pydantic v2**, **loguru**, **APScheduler**
- **websockets** para streams de Binance
- **numpy** para el motor numérico (features, HMM, backtest). `hmmlearn`
  permitido para el HMM gaussiano; nada de librerías all-in-one de trading.

### Fuentes de datos (criterio: máxima calidad, mínimo costo, mínima latencia)

| Fuente | Qué da | Costo | Fase |
|---|---|---|---|
| **Binance Futures WS** (`fstream.binance.com`) | klines, aggTrades, depth, markPrice, funding — latencia <100ms, sin API key | Gratis | 1 |
| **Binance Futures REST** (`fapi.binance.com`) | histórico de klines y de funding (completo), OI y ratios de los últimos ~30 días | Gratis (respetar rate limits con token bucket) | 1 |
| **Binance Data Vision** (`data.binance.vision`) | archivo diario estático: OI, ratios long/short y taker ratio en grilla 5m desde 2021-12; profundidad del libro a ±0,2/1/5% desde 2023-01 | Gratis, sin API key ni peso de rate limit | 2b |
| **alternative.me** | Fear & Greed index | Gratis | 7 |
| **CoinGecko API** | dominancia BTC, marketcap global | Gratis (30 req/min) | 7 |
| **Outliers Club** (premium de Nichelson) | métricas de riesgo, analytics curados | Membresía ya pagada | 8 — vía credenciales en `.env` local, NUNCA versionadas; evaluar API interna vs export manual; si es frágil o roza ToS, queda como fuente manual |
| TradingView / CoinMarketCap | redundantes con lo anterior | — | Solo si aparece un dato que nadie más da |

**Market data de Binance no requiere API key.** BOB v1 no necesita ninguna
credencial de trading — coherente con "nunca ejecuta órdenes".

### Frontend (se mantiene)
- **React 18 + Vite + TypeScript estricto**
- **Tailwind CSS**, **Zustand**, **TanStack Query**
- **lightweight-charts** para el chart principal (velas + niveles TP/SL/liquidación)
- **Recharts** para curvas de calibración, equity de backtest, histórico de KPIs

### Alertas
- **Telegram bot** (canal principal — señales al celular con los 3 KPIs)
- **Dashboard** (WS broadcast, alerta visual + sonora)

---

## ESTRUCTURA DEL PROYECTO (objetivo)

```
bob/
├── CLAUDE.md
├── README.md
├── .env.example
│
├── backend/
│   ├── pyproject.toml              # uv
│   ├── src/bob/
│   │   ├── main.py                 # Entry FastAPI
│   │   ├── config.py               # Settings (pydantic-settings)
│   │   ├── db/
│   │   │   ├── models.py           # Signal, PaperTrade, BacktestRun, Snapshot, Candle
│   │   │   └── session.py
│   │   ├── venues.py               # Perfiles de venue de ejecución (fees, MMR, funding)
│   │   ├── data/                   # Conectores (ÚNICO lugar con I/O de mercado)
│   │   │   ├── binance_ws.py       # WS multiplexado por símbolo, reconexión+backoff
│   │   │   ├── binance_rest.py     # Histórico + OI + funding + ratios, token bucket
│   │   │   ├── vision.py           # Archivo histórico data.binance.vision (metrics, bookDepth)
│   │   │   ├── download_vision.py  # CLI de ingesta idempotente del archivo
│   │   │   ├── sentiment.py        # Fear&Greed, CoinGecko (cache agresivo, APScheduler)
│   │   │   ├── outliers.py         # Fase 8 — Outliers Club
│   │   │   └── store.py            # Persistencia de klines para backtest offline
│   │   ├── signals/                # Feature engine (PURO, sin I/O)
│   │   │   ├── indicators.py       # Decimal, pocas velas — camino de PRESENTACIÓN
│   │   │   ├── numeric.py          # float64 vectorizado — camino de MODELADO
│   │   │   ├── features.py         # Ensambla las 55 features adimensionales
│   │   │   ├── microstructure.py   # Libro: imbalance, pendiente, cobertura
│   │   │   └── derivatives.py      # OI × precio, posicionamiento, funding
│   │   ├── models/                 # Modelos probabilísticos (PUROS, sin I/O)
│   │   │   ├── markov.py           # Heredado — baseline de régimen
│   │   │   ├── labeling.py         # Triple-barrier, targets, pesos por unicidad
│   │   │   ├── validation.py       # Walk-forward purgado + embargo
│   │   │   ├── forecast.py         # Probabilidad calibrada, volatilidad, conformal
│   │   │   ├── baselines.py        # RandomWalk, EWMA, GARCH(1,1), HAR-RV
│   │   │   ├── metrics.py          # Brier, ECE, QLIKE, Winkler, Diebold-Mariano
│   │   │   ├── experiment.py       # Orquesta el walk-forward completo
│   │   │   ├── report.py           # Renderiza el reporte a texto
│   │   │   └── projection.py       # (pendiente) EV con leverage, precio de liq
│   │   ├── backtest/               # Capa de I/O del experimento
│   │   │   └── runner.py           # DB → experiment → reporte + BacktestRun
│   │   ├── paper/
│   │   │   └── tracker.py          # Simula forward cada señal emitida, registra outcome
│   │   ├── live/
│   │   │   └── analyst.py          # Loop en vivo: data → features → modelo → señal
│   │   ├── alerts/
│   │   │   ├── telegram.py
│   │   │   └── broadcast.py        # WS al frontend
│   │   ├── api/
│   │   │   ├── routes/             # signal, backtest, history, watchlist, settings
│   │   │   └── ws.py
│   │   └── utils/
│   └── tests/                      # signals/, models/, backtest/ con cobertura ≥ 90%
│
├── frontend/
│   └── src/
│       ├── pages/
│       │   ├── SignalPage.tsx      # Principal: gauge Seguridad, chart TP/SL, EV
│       │   ├── AnalysisPage.tsx    # Desglose de componentes del KPI
│       │   ├── BacktestPage.tsx    # Correr backtests, curvas de calibración
│       │   ├── HistoryPage.tsx     # Señales emitidas + precisión real acumulada
│       │   └── SettingsPage.tsx    # Watchlist, umbral, params Markov, Telegram
│       └── ...                     # (shell heredado)
│
└── docs/
    ├── PROBABILITY_MODEL.md        # Deducción del modelo del KPI Seguridad
    └── DATA_SOURCES.md             # Endpoints, rate limits, trampas de cada fuente
```

---

## MODELOS — DECISIONES DE DISEÑO

### Markov / HMM

El `markov.py` heredado es un clasificador heurístico con matriz de
transición empírica — sirve como fallback y como baseline de comparación,
pero el detector de régimen v2 es un **HMM gaussiano sobre retornos
log + volatilidad**:

- **Número de estados: óptimo por defecto, elegido automáticamente por BIC**
  sobre la ventana de entrenamiento (típicamente 3-5).
- **Modificable por el usuario desde el dashboard** (Settings → Markov):
  puede fijar n manualmente; el dashboard muestra el BIC de cada n para que
  la decisión sea informada.
- Reentrenamiento periódico (APScheduler) con ventana rodante; la matriz de
  transición alimenta el KPI 3 y los estados son feature del KPI 1.

### Probabilidad calibrada (KPI 1)

- Etiquetado de datos históricos: para cada barra, ¿tocó +X% antes que −Y%
  dentro de H? (triple-barrier). Eso da el target binario.
- Modelo: empezar simple (regresión logística sobre el feature vector) y
  solo escalar a algo más complejo (gradient boosting) si el backtest lo
  justifica. La simplicidad es auditable; un modelo negro no.
- **Calibración isotónica** sobre out-of-fold predictions. La curva de
  fiabilidad (predicho vs observado) se muestra en el dashboard.
- Fees, funding y slippage entran en el etiquetado, no se descuentan después.

### Backtesting engine (gate de todo lo demás)

Event-driven sobre klines persistidas: replay barra a barra, el modelo solo
ve el pasado (walk-forward estricto, sin lookahead), las señales se evalúan
con fills realistas (siguiente open + slippage). Métricas mínimas: win rate
por bucket de probabilidad, profit factor, max drawdown, expectancy, y
**error de calibración** (el KPI dice 75% → ¿acertó 75%?).

**Ninguna señal se muestra como operable en el dashboard hasta que el
backtest walk-forward pase los DOS criterios del gate**. Antes de eso, todo
se etiqueta "experimental".

1. **Calibración**: error medio de calibración < 10 puntos porcentuales
   por bucket.
2. **Discriminación**: AUC > 0.55 y Brier Skill Score > 0 contra la tasa
   base, out-of-sample.

Los dos, no uno. Un modelo que predice SIEMPRE la tasa base está
perfectamente calibrado por construcción y es inútil: no distingue nada.
La calibración dice "cuando digo 70%, acierto 70%"; la discriminación dice
"sé cuáles son los casos de 70%". Pasar solo la primera no habilita operar.

### Paper tracking (validación forward continua)

Cada señal emitida en vivo se registra en DB y se simula su resultado con
los precios reales subsecuentes (sin ejecutar nada). La precisión forward
se compara contra la del backtest — si divergen, es red flag de overfitting
y el dashboard lo dice explícitamente.

---

## DASHBOARD — ESPECIFICACIÓN

Tema dark heredado: fondo `#0e0f0f`, verde `#3cd9a8`, rojo `#f07045`,
amarillo alerts `#f5b731`.

### Page 1: Signal (principal)
- Selector de símbolo (watchlist) + timeframe (5m / 15m / 1h; default 15m)
- **Gauge grande de Seguridad** con dirección (long/short) y color según umbral,
  acompañado SIEMPRE de la precisión histórica del bucket
- Chart de velas con overlay: entrada sugerida, TP, SL, precio de liquidación
  según leverage elegido (slider 1x-20x)
- Card de EV: profit esperado %, con/sin leverage, fees+funding descontados
- KPI 3: duración estimada del régimen con banda
- Timeline in-live: evolución del KPI Seguridad tick a tick (para acompañar
  un trade abierto)

### Page 2: Analysis
- Desglose de los 5 componentes del KPI: régimen actual y matriz de transición,
  técnicos, microestructura, derivados, sentimiento — cada uno con su lectura
  y su contribución al modelo
- Estado del HMM: n estados, BIC, última fecha de reentrenamiento

### Page 3: Backtest
- Configurar y correr backtest (símbolo, rango de fechas, TP/SL/H, umbral)
- Resultados: equity curve, métricas, y la **curva de calibración** (central)
- Comparación entre runs

### Page 4: History
- Tabla de señales emitidas (en vivo) con outcome del paper tracking
- Precisión forward acumulada vs precisión de backtest
- Exportable a CSV

### Page 5: Settings
- Watchlist de símbolos
- Umbral de emisión de señal
- Params Markov (n estados: auto/manual con BIC visible)
- TP/SL/horizonte por defecto
- Telegram (bot token + chat id, test button)

---

## FASES DE CONSTRUCCIÓN

Ejecutar en orden. Al final de cada una, resumir y esperar confirmación.

### Fase 0 — Migración del esqueleto ✅ (2026-08-19)
Completada. Main limpio de grid/GRVT, módulos heredados migrados, esquema
de DB definido, backend booteable sin `.env`, frontend compilando con las
5 páginas placeholder, 35 tests en verde. Estado detallado y gotchas de
entorno: `docs/HANDOFF_FASE1.md`.

### Fase 1 — Pipeline de datos Binance ✅ (2026-08-24)
1. ✅ `data/binance_ws.py`: kline + markPrice + aggTrade multiplexados en una
   conexión, reconexión con backoff+jitter, TTL de 23h (el corte de 24h de
   Binance no espera), y `MarketDataHub` que persiste **solo velas cerradas**
   (`k.x == true`). `depth20` queda para Fase 2b junto a microstructure.py
2. ✅ `data/binance_rest.py`: histórico de klines, OI, funding, ratios; limiter
   autorregulado por el header `X-MBX-USED-WEIGHT-1M`
3. ✅ `data/store.py`: klines y derivados en SQLite + `OHLCVSeries` (frontera de
   pureza: de acá para arriba solo numpy). Huecos se reportan, nunca se rellenan
4. ✅ `data/download.py`: CLI idempotente. **69.119 velas de ETHUSDT 15m
   persistidas (720 días, 100% de completitud, 0 huecos)** — superado el
   objetivo de 90 días
5. ✅ `data/snapshots.py`: la ventana de OI / long-short / taker ratio es de
   ~30 días y **no se puede recuperar hacia atrás**, así que el snapshot corre
   con el backend (cada 30 min) y como CLI. Primera captura: 2026-08-24
6. ✅ `live/feed.py`: puente feed → dashboard (`market.tick`, `market.candle`,
   `conn.status`), cableado en el `lifespan` de `main.py`. Se apaga con
   `BOB_LIVE_DATA=false` para trabajar offline
7. ✅ **Hallazgo cerrado (2026-08-24)**: el WS de futuros mainnet no está
   bloqueado — Binance **calla streams concretos y entrega otros sobre la misma
   conexión TLS** (mudos: `@aggTrade`, `@kline_*`, `@markPrice`, `@ticker`,
   `@forceOrder`; vivos: `@trade`, `@bookTicker`, `@depth*`). El primer
   diagnóstico ("filtrado por IP/región del feed de derivados") era falso. El
   flujo taker se sostiene con `@trade`, verificado idéntico al REST oficial
   (0.000% de diferencia de volumen), y `kline`/`markPrice` se rellenan por
   REST en paralelo. La salud se lleva **por stream**, no por socket, y
   `conn.status` reporta el híbrido (`binance_ws+rest_fill(kline,markPrice)`).
   Regla 6 cumplida: precio y flujo por WS a <100ms. Evidencia, medición
   decisiva y diseño en `docs/DATA_SOURCES.md`

### Fase 2 — Feature engine (PURO) ✅
1. ✅ `signals/numeric.py` (primitivas causales) + `features.py` (55 features
   en 6 familias)
2. ✅ Cobertura 100% en features.py, 92% en numeric.py. Dos invariantes con
   test propio: **sin lookahead** (mutar el futuro no altera el pasado) y
   **adimensionalidad** (escalar el precio ×10 no cambia la matriz)

### Fase 2b — Derivados + microestructura ✅ (2026-08-24)

**El hallazgo que la desbloqueó**: la ventana de ~30 días es del *endpoint*
`/futures/data/*`, no del *dato*. Binance publica los mismos campos en el
archivo estático diario `data.binance.vision`, sin API key y sin peso de rate
limit, con grilla de 5m desde **2021-12-01**. La nota de Fase 2 que decía que
el OI histórico "NO está disponible gratis" era falsa y quedó corregida en
`docs/DATA_SOURCES.md`.

1. ✅ `data/vision.py`: cliente del archivo (listado S3 paginado, descarga
   concurrente, 404 = hueco reportado y no error) + parseo de `metrics` y
   `bookDepth`. `bookDepth` se **agrega a la grilla del timeframe al ingerir**:
   el crudo son ~2 GB por año y por símbolo, y lo que el modelo necesita son
   seis columnas por barra
2. ✅ `data/download_vision.py`: ingesta idempotente **por día UTC** — un día
   ya completo no se vuelve a bajar. Cubre `metrics`, `bookDepth` y `funding`
   (este último por REST, que sí tiene historia completa). Distingue **404 de
   fallo de red**: la primera corrida atravesó un corte de conectividad y
   reportó 726 días "ausentes del archivo" que existen todos — ahora el día
   caído se reintenta, queda pendiente, y el reporte dice que la corrida no
   está completa en vez de terminar en verde
3. ✅ `signals/derivatives.py`: `align_to_bars` lleva la grilla de 5m a la de
   velas **con retraso de publicación explícito** y NaN por staleness. Es la
   pieza donde un bug de causalidad se esconde mejor, y tiene test de
   no-lookahead propio. Features: cambios de OI, **interacción OI × precio**
   (dinero nuevo vs. short covering), posicionamiento top-traders vs. multitud,
   flujo taker y funding
4. ✅ `signals/microstructure.py`: 23 features de libro partidas en dos por una
   razón de datos, no de diseño. **Núcleo (15 columnas, niveles 1% y 5%)**:
   desbalance, pendiente, profundidad relativa al volumen y presión taker —
   cubre los 720 días. **Near-touch (8 columnas, nivel 0,2%)**: Binance lo
   publica solo desde 2026-01-15, así que sale NaN antes, con máscara
   `near_available` y `near_names` declarados explícitamente. Exigirlo descartó
   508 días en silencio en la primera ingesta; y como la pendiente estaba
   definida contra el 0,2%, cinco columnas más quedaban al 30% de cobertura sin
   necesidad — medida sobre el núcleo dicen lo mismo y cubren todo
5. ✅ **Grilla del snapshot en vivo alineada a 5m** (antes 15m). Con periods
   distintos, archivo y REST escribían dos series que nunca se tocaban y la
   familia se cortaba el día en que terminaba el archivo
6. ✅ `venues.py`: perfil declarativo del **venue de ejecución** (fees, tiers
   de MMR, intervalo de funding). Separa los dos roles de Binance — fuente de
   datos, que no es opcional, y lugar donde el usuario entra, que sí lo es
7. ✅ Cobertura 99% en derivatives.py y microstructure.py, 100% en venues.py,
   95% en vision.py. Las dos invariantes del proyecto extendidas a las familias
   nuevas, incluida la adimensionalidad frente a un mercado ×1000

**Datos persistidos (2026-08-25)**, todos sobre los mismos 720 días de ETHUSDT
15m que ya tenía el backtest:

| Fuente | Filas | Cobertura |
|---|---|---|
| klines 15m | 69.119 | 2024-08-31 .. 2026-08-21 |
| metrics 5m | 210.232 | 2024-08-25 .. 2026-08-24, **730/730 días, 0 huecos** |
| funding 8h | 2.192 | 2024-08-25 .. 2026-08-25 |
| bookDepth 15m | 70.074 | 2024-08-25 .. 2026-08-24, **730/730 días, 0 huecos** |

**104 features**: 55 de precio + 26 de derivados (26/26 con >90% de cobertura,
mediana 99,9%) + 23 de libro (núcleo 15/15 >90%, mediana 100%; near-touch 8
columnas al 30,4% por la fecha en que Binance empezó a publicar el nivel).

8. ✅ `numeric.zscore` unificado. Devolvía `0.0` tanto para una ventana plana
   (dato válido: el punto ES su media) como para una ventana sin datos
   (warm-up o hueco) — o sea imputaba "exactamente el promedio" donde no
   sabía nada, y esa fila pasaba el filtro de finitud. Medido sobre los 720
   días reales: afectaba **671 filas, todas de warm-up, y ninguna llegaba al
   modelo** porque otro feature tenía la ventana más larga. La protección era
   accidental. Ahora devuelve NaN sin datos y 0 solo si la ventana es plana;
   verificado que la matriz que llega al modelo queda **byte-idéntica**, así
   que el cambio no mueve el gate. `zscore_nan` se eliminó por redundante

**Pendiente que abre**: rehacer el gate de Fase 4 con las dos familias nuevas.
Antes entrenaban sobre la ventana de snapshots; ahora cubren el backtest
completo.

### Fase 3 — Modelos (PUROS) ✅
1. ✅ HMM gaussiano (`models/hmm.py`): Baum-Welch propio en numpy, filtrado
   causal separado del suavizado (el `predict` de las librerías es lookahead),
   n elegido por BIC **o ICL**, duraciones de régimen para el KPI 3. Hallazgo
   registrado: sobre 69k barras el BIC decrece monótonamente y elige el borde
   —el HMM tesela el eje de volatilidad—, así que el selector reporta ambos
   criterios, una regla de parsimonia explícita y sus advertencias. El markov
   heredado sigue de baseline. Detalle en `docs/PROBABILITY_MODEL.md` §9-bis
2. ✅ Triple-barrier + probabilidad calibrada (isotónica sobre OOF purgado)
3. ✅ Dos targets más: volatilidad realizada (HAR/GARCH/EWMA de baseline) y
   cono de precio conformal (CQR + ACI)
4. ✅ `projection.py`: TP/SL dimensionados por la sigma pronosticada, EV neto
   (fees + slippage + funding), ROE por leverage, **precio de liquidación**,
   distancia en sigmas y leverage máximo seguro. Se apoya en el target de
   volatilidad —que sí pasó el gate— y no en el de dirección
5. ✅ Cobertura ≥ 94% en todo `models/`

### Fase 4 — Backtesting engine — EL GATE ✅ (corrido)
1. ✅ Walk-forward purgado con embargo + métricas + curva de calibración +
   test de Diebold-Mariano contra baselines
2. ✅ Corrido sobre 24 meses de ETHUSDT 15m
3. **Criterio de salida (los dos, en TODAS las direcciones)**: error de
   calibración < 10pp por bucket **y** AUC > 0.55 con BSS > 0. Ver el
   veredicto en el último `backend/artifacts/*.txt`. Método completo y
   límites conocidos: `docs/PROBABILITY_MODEL.md`

### Fase 5 — Señales en vivo + paper tracking
1. `live/analyst.py`: loop data → features → modelo → señal
2. `paper/tracker.py`: outcome forward de cada señal
3. Correr 72h continuas, comparar precisión forward vs backtest

### Fase 6 — API + Dashboard (pages 1-4 + settings)
### Fase 7 — Alertas Telegram + sentimiento (F&G, CoinGecko)
### Fase 8 — Outliers Club + multi-símbolo watchlist
### Fase 9 — Validación end-to-end
1. 2 semanas de operación asistida: BOB señala, Nichelson decide (puede ser
   sin capital o con capital mínimo — decisión suya, fuera del software)
2. Revisión honesta: ¿la precisión forward sostiene el KPI? ¿las alertas
   llegan a tiempo? ¿la latencia in-live es suficiente?

---

## REGLAS DE OPERACIÓN

1. **BOB nunca ejecuta órdenes.** No hay código de ejecución en main. Punto.
2. **Ningún KPI probabilístico se muestra como operable sin calibración
   Y discriminación demostradas** (backtest walk-forward + paper tracking).
   "Experimental" en gris hasta entonces.
3. **Pureza por capas**: `signals/`, `models/`, `backtest/` no hacen I/O.
   Todo I/O de mercado vive en `data/`. Es lo que hace el motor testeable
   y agnóstico del símbolo.
4. **Cobertura ≥ 90%** en signals/, models/, backtest/. Un bug aquí = una
   probabilidad falsa = capital apalancado del usuario en riesgo.
5. **Sin lookahead**: cualquier feature o label que use información futura
   respecto de su timestamp es un bug crítico. Los tests deben cazarlo.
6. **Latencia in-live**: del tick de Binance al dashboard < 1s. Cache
   agresivo para fuentes lentas (sentimiento se actualiza por scheduler,
   nunca en el hot path).
7. **Rate limits**: token bucket para REST de Binance y CoinGecko. Nunca
   rafagear.
8. **Reconexiones**: WS con exponential backoff + jitter, resuscripción
   automática, y el dashboard muestra el estado de cada conexión.
9. **Typing obligatorio** backend (mypy), **TypeScript estricto** frontend.
10. **Loggear toda señal emitida** con su feature vector completo — sin eso
    no hay post-mortem posible.
11. **Commits pequeños por fase.** Un módulo funcional por commit.

## RESTRICCIONES

- **No agregar ejecución de órdenes** — ni "opcional", ni "para después".
  Si algún día se decide, será un pivote explícito como este.
- **No usar librerías all-in-one de trading** (freqtrade, backtrader, jesse).
  El backtest es propio porque el KPI de calibración es la razón del proyecto.
- **Credenciales de Outliers Club solo en `.env` local**, nunca versionadas,
  nunca enviadas a servicios ajenos a Outliers.
- **No inflar el KPI**: nada de redondear hacia arriba, suavizar drawdowns
  ni ocultar buckets malos. El usuario decide con la verdad o no decide.
- **No saltar el gate de Fase 4.** Señales en vivo sin backtest calibrado
  es exactamente el tipo de error que ya costó una liquidación.

---

## ARRANQUE DE SESIÓN

Al abrir sesión en este repo: leer este documento, revisar en qué fase está
el proyecto (git log + estado de tests), y continuar la fase en curso. Ante
decisiones no cubiertas aquí: preguntar. No asumir.
