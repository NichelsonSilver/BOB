# BOB — Asistente de Decisión para Trading Intradía

> Contexto estratégico del ecosistema (memoria de LUPE):
> @../lupe/contexto/MEMORIA.md

## IDENTIDAD DEL PROYECTO

BOB (**B**ursatil **O**perator **B**uddy) pivoteó el 2026-08-19, por decisión
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

---

## DECISIÓN VIGENTE (2026-08-25) — el producto se apoya en VOLATILIDAD

Decisión explícita de Nichelson al cierre de la Fase 2b, tomada sobre la
evidencia de dos corridas del gate. **Leer esto antes de retomar cualquier
fase.**

### Qué se decidió

El producto se construye sobre el **target de volatilidad**, que pasó el gate,
y **no** sobre el target de dirección, que no lo pasó. En concreto:

- El **KPI 2 (proyección)** es el que sostiene el valor entregado: TP y SL
  dimensionados por la sigma pronosticada, EV neto de costos, ROE por leverage,
  **precio de liquidación** y leverage máximo seguro. Todo eso ya existe en
  `models/projection.py` y se apoya en el target que sí está validado.
- El **KPI 1 (Seguridad)** NO se muestra como operable. Sigue etiquetado
  "experimental" en gris, con su precisión histórica real al lado, tal como
  manda la regla 2. No se emite señal direccional contra él.
- El **KPI 3 (duración de régimen)** se mantiene como secundario, con banda de
  incertidumbre, sin cambios.

### Por qué — la evidencia, no la intuición

Dos corridas del walk-forward purgado sobre 69.119 velas de ETHUSDT 15m:

1. **2026-08-24, 55 features de precio**: calibra (4,0pp / 5,1pp) pero **no
   discrimina** (AUC 0,519 / 0,533; BSS −0,0028 / +0,0005).
2. **2026-08-25, hasta 96 features** con derivados y libro de la Fase 2b sobre
   730/730 días: **peor** (AUC 0,509 / 0,515; BSS −0,0049 / −0,0018),
   monótonamente con el número de features y en las dos direcciones.

La hipótesis que motivó la Fase 2b —"no discrimina porque le faltan datos de
derivados y microestructura"— quedó **refutada**: los datos ya no faltan y el
resultado empeoró. Mientras tanto el target de volatilidad pasa el gate con
p=0.0000 en Diebold-Mariano contra los baselines (`docs/PROBABILITY_MODEL.md`).

Entre las tres salidas que había sobre la mesa —barrer el target, seleccionar
features, o apoyarse en volatilidad— se eligió la tercera porque es **la única
con respaldo empírico hoy**. Barrer combinaciones de TP/SL/H corre el riesgo de
encontrar una que pase por azar; si algún día se retoma, hay que fijar el
criterio y el número de pruebas ANTES de correrlas.

### Qué implica para las fases siguientes

- **Fase 5** deja de ser "emitir señales direccionales". Pasa a ser: paper
  tracking del **pronóstico de volatilidad y de los niveles derivados de él**
  (¿la sigma pronosticada contuvo al precio?, ¿el cono conformal cubrió al
  nivel nominal?), más el registro de las condiciones de mercado.
- **Fase 6** (dashboard) construye la página Signal alrededor del KPI 2:
  niveles, EV, distancia a liquidación y el cono de precio. El gauge de
  Seguridad se dibuja en gris con su etiqueta de "sin calibrar".
- El motor direccional **no se borra**. Queda entrenado, versionado y medido;
  si alguna vez pasa los dos criterios, se enciende. Lo que no se hace es
  mostrarlo como operable mientras no los pase.

### Lo que NO cambia

La regla 2 sigue siendo la regla 2, y esta decisión es su aplicación literal:
un KPI probabilístico sin calibración **y** discriminación demostradas no se
muestra como operable. La Fase 2b no fue en vano —730 días de derivados y de
libro son infraestructura real y reutilizable— pero su premisa era falsa y
conviene que quede escrito que lo era.

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
- **numpy** para el motor numérico (features, HMM, backtest), **scipy** solo
  para optimización y distribuciones, **scikit-learn** para los estimadores
  de caja (GBM, logística, Ridge, isotónica, KMeans de inicialización) y
  **xgboost** como estimador alternativo del target de volatilidad, medido
  contra sklearn con los mismos hiperparámetros (`--vol-model {gbm,xgb}`).
  Nada de librerías all-in-one de trading.
- **Los baselines econométricos y el HMM están escritos desde cero**
  (`models/baselines.py`, `models/hmm.py`). **No se usan `statsmodels` ni
  `arch`**, y `hmmlearn` quedó descartado aunque este documento lo permitía:
  su `predict` es Viterbi sobre la secuencia completa y su `predict_proba` es
  el posterior suavizado, o sea **los dos miran el futuro de cada barra** —
  usarlos como feature es el lookahead de la regla 5, y sería invisible
  (métricas hermosas en backtest, irreproducibles en vivo). El filtro causal
  había que escribirlo igual; lo que agregaba la librería era el Baum-Welch.
  Con GARCH el motivo es otro y también decide: un GARCH que no converge en
  silencio hace ver *skill* donde no lo hay, así que la implementación propia
  reescala, restringe `alpha+beta` y **cae a EWMA si no converge**. El
  baseline es el número que decide; si es caja negra, la respuesta no es
  auditable. Tabla completa en `README.md` § "Cómo están implementados los
  modelos" y en `docs/PROBABILITY_MODEL.md` §7.1.

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
│   │   │   ├── models.py           # ForecastRecord, Signal, PaperTrade, BacktestRun, Snapshot, Candle
│   │   │   └── session.py
│   │   ├── venues.py               # Perfiles de venue de ejecución (fees, MMR, funding)
│   │   ├── data/                   # Conectores (ÚNICO lugar con I/O de mercado)
│   │   │   ├── binance_ws.py       # WS multiplexado por símbolo, reconexión+backoff
│   │   │   ├── binance_rest.py     # Histórico + OI + funding + ratios, token bucket
│   │   │   ├── binance_poll.py     # Relleno REST de los streams que el WS calla
│   │   │   ├── vision.py           # Archivo histórico data.binance.vision (metrics, bookDepth)
│   │   │   ├── download.py         # CLI de klines: idempotente, --resume, --repair
│   │   │   ├── download_vision.py  # CLI de ingesta idempotente del archivo
│   │   │   ├── snapshots.py        # OI / long-short / taker ratio en vivo (ventana de ~30d)
│   │   │   ├── sentiment.py        # (pendiente, Fase 7) Fear&Greed, CoinGecko
│   │   │   ├── outliers.py         # (pendiente, Fase 8) Outliers Club
│   │   │   └── store.py            # Persistencia de klines para backtest offline
│   │   ├── signals/                # Feature engine (PURO, sin I/O)
│   │   │   ├── indicators.py       # Decimal, pocas velas — camino de PRESENTACIÓN
│   │   │   ├── numeric.py          # float64 vectorizado — camino de MODELADO
│   │   │   ├── features.py         # Ensambla las 55 features adimensionales
│   │   │   ├── microstructure.py   # Libro: imbalance, pendiente, cobertura
│   │   │   └── derivatives.py      # OI × precio, posicionamiento, funding
│   │   ├── models/                 # Modelos probabilísticos (PUROS, sin I/O)
│   │   │   ├── markov.py           # Heredado — baseline de régimen
│   │   │   ├── hmm.py              # HMM gaussiano propio: Baum-Welch, filtrado causal, BIC/ICL
│   │   │   ├── labeling.py         # Triple-barrier, targets, pesos por unicidad
│   │   │   ├── validation.py       # Walk-forward purgado + embargo
│   │   │   ├── forecast.py         # Probabilidad calibrada, volatilidad, conformal
│   │   │   ├── baselines.py        # RandomWalk, EWMA, GARCH(1,1), HAR-RV — propios, en numpy
│   │   │   ├── metrics.py          # Brier, ECE, QLIKE, Winkler, Diebold-Mariano
│   │   │   ├── experiment.py       # Orquesta el walk-forward completo + umbrales del gate
│   │   │   ├── report.py           # Renderiza el reporte a texto
│   │   │   ├── production.py       # UN ajuste consultable barra a barra + ACI en vivo
│   │   │   └── projection.py       # EV con leverage, precio de liq, leverage máx seguro
│   │   ├── backtest/               # Capa de I/O del experimento
│   │   │   ├── runner.py           # DB → experiment → reporte + BacktestRun
│   │   │   └── compare.py          # Ablación entre variantes de features
│   │   ├── paper/
│   │   │   └── tracker.py          # Resuelve cada pronóstico con las métricas del gate
│   │   ├── live/
│   │   │   ├── feed.py             # Puente feed → dashboard (market.tick, market.candle)
│   │   │   └── analyst.py          # Loop en vivo: data → features → bundle → proyección
│   │   ├── alerts/                 # (pendiente, Fase 7) telegram.py, broadcast.py
│   │   ├── api/
│   │   │   ├── routes/             # (pendiente, Fase 6) signal, backtest, history, settings
│   │   │   └── ws.py               # Broadcast al frontend
│   │   └── utils/
│   ├── artifacts/                  # Reportes de gate VERSIONADOS (.txt legible + .json)
│   ├── scripts/                    # Operación de la corrida (no son parte del motor)
│   │   ├── preflight.py            # ¿puede emitir hoy? Sin pagar el ajuste de 80s
│   │   ├── watch_run.py            # Vigilante desatendido: 1 línea por chequeo a logs/
│   │   └── purge_stale_forecasts.py # Homogeneidad de model_version antes de acumular
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
    ├── PROBABILITY_MODEL.md        # Deducción del stack, el gate y sus resultados
    ├── DATA_SOURCES.md             # Endpoints, rate limits, trampas de cada fuente
    └── HANDOFF_FASE1.md            # Estado al cierre de Fase 0 y gotchas de entorno
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

**Y los dos juntos tampoco alcanzan — pasar el gate habilita MEDIR, no
OPERAR** (medido 2026-09-14, `docs/PROBABILITY_MODEL.md` §8.1). El gate mide
si el modelo ordena mejor que el azar; el mercado exige que la probabilidad
**calibrada** supere la **probabilidad de equilibrio**,
`P_equilibrio = (SL + costo) / (TP + SL)`, que en este setup es **61,6%**
contra una tasa base de 43,9%. Son listones distintos, y el AUC no mide el
segundo: mide **orden**, no **dispersión de las probabilidades**. Un modelo
puede ordenar apenas mejor que el azar y, ya calibrado, aplastar todas sus
predicciones contra la tasa base — que es literalmente lo que pasa hoy:
de 44.539 predicciones long, **40.521 caen en el bucket 40-50%** y solo 3
superan el 70%.

Cuantificado sobre un binormal de varianza igual, leyendo tasa base y
equilibrio del artefacto real del gate:

| AUC | % de barras sobre el equilibrio | barras/año a 15m |
|---|---|---|
| 0,52 (lo medido) | 0,000% | 0 |
| **0,55 — umbral del gate** | **0,003%** | **1** |
| 0,58 | 0,627% | 220 |
| 0,60 | 2,351% | 824 |
| 0,63 | 6,541% | 2.292 |
| 0,65 | 9,723% | 3.407 |

**Un modelo que aprueba el gate con AUC 0,551 emitiría del orden de UNA señal
operable por año.** El piso para que aparezca algo está cerca de 0,58; el de
un instrumento usable, cerca de 0,63. Entre el umbral del gate y el umbral
económico hay ocho centésimas de AUC, que en este problema es más de lo que
separó al modelo del azar en las dos corridas completas.

**El gate NO se mueve por esto.** AUC > 0.55 con BSS > 0 sigue siendo la
condición de entrada: es el listón de "esto no es ruido". Lo que cambia es
cómo se lee un aprobado — el número que decide si hay producto es el que el
reporte ya imprime al pie de cada dirección, **cuántas señales superan el
umbral y con qué expectancy neta**. Si son tres en dos años, no hay producto
aunque las tres ganen. Corolario para cualquier barrido de barreras futuro:
maximizar AUC es mirar la variable equivocada; lo que hay que maximizar es la
**masa de probabilidad calibrada por encima del equilibrio**, y bajar el
costo o alargar el horizonte baja el listón tanto como subir el AUC.

Se reimprime con `uv run python scripts/economic_floor.py` (solo lectura, lee
el artefacto del gate en vez de copiar sus cifras).

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

### Fase 4 — Backtesting engine — EL GATE ✅ (corrido 2 veces)
1. ✅ Walk-forward purgado con embargo + métricas + curva de calibración +
   test de Diebold-Mariano contra baselines
2. ✅ Corrido sobre 24 meses de ETHUSDT 15m
3. **Criterio de salida (los dos, en TODAS las direcciones)**: error de
   calibración < 10pp por bucket **y** AUC > 0.55 con BSS > 0. Método completo
   y límites conocidos: `docs/PROBABILITY_MODEL.md`. Recordar que aprobar
   habilita **medir**, no operar: el piso económico está ~0,08 de AUC por
   encima del umbral del gate (ver "Backtesting engine" arriba y §8.1)

**VEREDICTO: el target de dirección NO pasó el gate.** Calibra (4,0pp / 5,1pp)
y no discrimina (AUC 0,519 / 0,533). El target de volatilidad y el cono sí
pasan. Está escrito de forma verificable por cualquiera que entre al repo:
`README.md` abre con la sección "Veredicto del gate", los reportes completos
están **versionados** en `backend/artifacts/*.txt` con su bloque `GATE DE LA
FASE 4`, y `uv run python -m bob.backtest.compare` reimprime el veredicto
desde los `.json`. Nada de esto es un resumen escrito a mano: sale del
`ExperimentResult`.
4. ✅ **Reejecución con las familias de Fase 2b (2026-08-25)**. `runner.py
   --features {price|price+deriv|full|full+near}` corre la misma configuración
   con distintas familias, y `backtest/compare.py` las pone lado a lado. El
   comparador **importa las constantes del gate** en vez de copiarlas: un
   comparador con su propio criterio podría decir "habilitado" donde
   `gate_passed` dice que no

**Resultado de la ablación** (69.119 velas, misma semilla, mismos folds):

| variante | feat | AUC long | AUC short | BSS long | BSS short | veredicto |
|---|---|---|---|---|---|---|
| `price` | 55 | 0.519 | 0.533 | −0.0028 | +0.0005 | ✗ no habilitado |
| `price+deriv` | 81 | 0.512 | 0.517 | −0.0035 | −0.0025 | ✗ no habilitado |
| `full` | 96 | 0.509 | 0.515 | −0.0049 | −0.0018 | ✗ no habilitado |

**Las familias nuevas EMPEORAN la discriminación**, de forma monótona con el
número de features y en las dos direcciones a la vez. Lectura: dilución de la
capacidad fija del GBM entre columnas sin señal direccional, no ruido de
muestreo. Detalle revelador: en `price+deriv` la familia `derivados` marca
0.00151 de importancia por permutación (segundo lugar, tras `volatilidad`), o
sea el modelo **sí** las usa — pero en `full` cae a 0.00006 y `libro` sale
negativa. Importancia por permutación positiva ≠ ganancia fuera de muestra.

**Lo que esto refuta**: la hipótesis que motivó la Fase 2b era que el gate no
pasaba discriminación *por falta de datos de derivados y microestructura*. Los
datos ya no faltan —730/730 días— y el gate sigue sin pasar, un poco peor. La
causa del fallo no es la disponibilidad de datos. Queda como sospechoso la
**formulación del target**: barreras a ±0,5σ con H=16 sobre 15m puede ser
sencillamente casi impredecible.

**Control de regresión**: el run `price` de 2026-08-25 reproduce el del
2026-08-24 **bit a bit** (AUC 0.518701 / 0.532680, BSS −0.002801 / +0.000498).
Ni el cambio de `zscore` ni el refactor de Fase 2b tocaron el baseline.

**`full+near` no es evaluable con este periodo.** El near-touch arranca el
2026-01-15, así que en los primeros folds esas 8 columnas son NaN puro y el
binning de sklearn revienta con `window shape cannot be larger than input array
shape` — un error que no dice nada de la causa. `assert_columns_trainable` lo
convierte en un diagnóstico que nombra las columnas y falla en segundos.
Requiere que el nivel acumule historia para cubrir varios folds.

### Fase 5 — Live + paper tracking ✅ CERRADA (2026-09-14)

Construida el 25-08, cerrada el 14-09 con la corrida de validación forward
completa. Reformulada por la decisión del 25-08: se emite **proyección**, no
dirección. El veredicto de cierre está en el punto 13.

1. ✅ `models/production.py`: el puente que faltaba entre el experimento y el
   vivo. `experiment.py` entrena un modelo por fold y lo tira —su producto son
   métricas—; `fit_bundle` hace **un** ajuste consultable barra a barra sobre
   toda la historia. Las últimas H filas quedan fuera solas: su etiqueta no
   terminó de ocurrir. `OnlineConformalCone` lleva el ACI en vivo con la misma
   aritmética del experimento (test que exige coincidencia uno a uno)
2. ✅ `live/analyst.py`: vela cerrada → features → bundle → proyección,
   publicada como `analysis.forecast` y persistida en `ForecastRecord`. Los
   features se recalculan sobre la serie **completa** (0,68s), no sobre una
   cola: medido, la cola de 3.000 barras deja 3e-7 de diferencia en `oi_z_ctx`
   que **no converge** al alargarla. El reajuste corre en background y el
   bundle viejo sigue emitiendo mientras
3. ✅ `paper/tracker.py`: resuelve cada pronóstico cuando su horizonte cierra
   —sigma realizada, cobertura del cono, EV realizado— con **las mismas
   funciones de métrica del gate**, para que "forward vs backtest" compare y
   no traduzca. Un horizonte con huecos se marca `gap` y no entra
4. ✅ `ForecastRecord`: una fila por barra con el vector completo (regla 10),
   las dos sigmas y el resultado forward. `Signal`/`PaperTrade` quedan intactas
   para el día en que el motor direccional pase el gate
5. ✅ Cableado en el `lifespan`: el ajuste inicial (78s medidos sobre 69k
   barras) arranca en background para no dejar el backend sin responder
6. ✅ **Las pausas del proceso son seguras** (2026-08-25). El analista repara
   las tres series al arrancar y en cada reajuste, así que apagar el equipo no
   cuesta muestras más allá de las barras que no ocurrieron. Ver abajo
7. ✅ **`/api/health` reporta el estado del analista**, no solo el del feed.
   Durante una corrida larga la pregunta que importa no es "¿el backend
   responde?" sino "¿está emitiendo?", y son cosas distintas: el ajuste
   inicial toma ~80s, un reajuste fallido lo deja sirviendo con el bundle
   previo, y una familia de features que dejó de llegar lo deja mudo. Sin
   `analyst.status()` las tres se ven igual desde afuera — un backend en verde
   que no produce nada. Expone `fitted`, `refitting`, `bars_since_fit`,
   `n_train`, `last_forecast_open_time` y la cobertura del cono con su
   `alpha_t`
8. ✅ **Dos bugs de frescura de datos, encontrados al arrancar la corrida real
   (2026-08-31)**. Los dos dejaban al analista sin emitir, ninguno lo hacía en
   silencio —el error `no se pronostica sobre un hueco` salía correcto— y
   justamente por eso eran difíciles: el mensaje describía el síntoma real (la
   barra no tenía los features densos) y no la causa, así que eran
   indistinguibles de un corte de datos de Binance. La única forma de
   separarlos fue mirar la DB al lado del log y ver que el dato **sí estaba
   escrito**. Se encontraron uno detrás del otro: el primero tapaba al segundo,
   porque mientras todo fallaba siempre no se veía el patrón intermitente
   de abajo.

   **(a) `live/analyst.py` — la vela avanzaba en memoria y el contexto no.**
   `on_closed_candle` extendía la serie de velas pero construía el
   `AnalystInputs` nuevo **arrastrando** derivados, funding y libro de la barra
   anterior. Esas tres solo se releían en `_load_inputs`, o sea al ajustar y al
   reajustar: con `refit_every_bars=96`, cada 24h. El feed escribía snapshots
   correctos al disco cada 30 min y el analista no los veía nunca. Las series
   quedaban congeladas en la foto del arranque mientras el reloj corría, y cada
   familia iba cruzando su tolerancia de staleness por orden de exigencia
   —taker a los 15 min, funding a los 30, OI a la hora—. Medido: **1 pronóstico
   al arrancar y silencio hasta el reajuste**, o sea ~1 por día en vez de 96;
   las 280 muestras habrían tomado nueve meses. Arreglado con `_refresh_context`
   en cada vela cerrada, más `_maybe_ingest_funding`: el funding tenía un
   agravante propio —`snapshot_once` no lo escribe y `ingest_funding` solo corría
   en `_repair`—, así que con publicación cada 8h y tolerancia de 8h exactas se
   vencía solo entre reajustes.

   **(b) `data/snapshots.py` — la fila parcial en la punta de la grilla.** Los
   cinco endpoints de `/futures/data/*` no publican al mismo tiempo:
   `takerlongshortRatio` va un bucket de 5m detrás de `openInterestHist`. Cada
   ciclo escribía una fila con OI y con taker en NULL, y esa fila rompía la
   alineación aguas arriba — `align_to_bars` toma el último punto **por
   timestamp**, así que devolvía el NaN de la fila nueva en vez del valor bueno
   de cinco minutos antes. La tolerancia de staleness ni llegaba a ejecutarse:
   mide la EDAD del punto, no si el punto tiene dato. Firma inconfundible en
   producción: **exactamente 50% de emisión, una barra sí y una no cada 30
   minutos**, que es el intervalo del snapshot. La barra posterior a un ciclo
   veía la grilla completa; la siguiente ya tenía la fila parcial dentro de la
   ventana observable.

   Se arregló en la capa de I/O y **no** en `align_to_bars`, que era la otra
   opción sobre la mesa. La propagación de NaN de esa función es deliberada y
   tiene test propio (`test_align_propaga_los_nan_del_origen`): un punto sin
   dato debe seguir sin dato. Lo que estaba mal era escribir como "punto sin
   dato" un punto que el endpoint todavía no había publicado. `fetch_derivatives`
   corta ahora la grilla en el último instante donde los cinco ya publicaron
   (`_common_cutoff`); cuesta ~5 min de frescura en OI contra una tolerancia de
   1h. La alternativa —que `align_to_bars` arrastrara el último valor bueno— era
   defendible pero movía la matriz de entrenamiento en ~95 filas de 212.182, y
   el arreglo en I/O deja el módulo puro y los artefactos del gate intactos.

   Medida que decidió entre las dos: en 212.182 filas históricas hay **una sola**
   fila parcial de taker, y era la de ese mismo día. El archivo diario puebla
   todas las columnas; las filas parciales solo las genera el camino REST en
   vivo, así que el problema era del vivo y ahí se arregló.

9. ✅ **La reparación de velas se reintenta hasta que vuelva la red
   (2026-09-01)**. Tercer bug de la misma familia y el más barato de todos:
   `_repair` corría **una sola vez** al arrancar y su fallo se descartaba con
   un WARNING. Encontrado al reanudar la corrida después de una mudanza —el
   backend levantó a los segundos de encender el equipo en una red nueva, los
   cuatro intentos de `repair_series` murieron con `getaddrinfo failed`, y un
   minuto después la red volvió y el poll REST escribió la vela en curso. Las
   dos barras del apagón quedaron como hueco interior **permanente**:
   `--resume` avanza desde la última vela en DB y nunca vuelve a mirar hacia
   atrás. El analista no emitió una sola barra hasta correr `--repair` a mano.

   La ironía es la que ordena el arreglo: el arranque tras mover el equipo es
   a la vez el momento en que la red es menos probable que esté y el único en
   que la reparación es imprescindible. Una sola pasada, justo ahí, es la peor
   política posible. Ahora el fallo se **recuerda** (`_candle_repair_pending`)
   y se reintenta en cada vela cerrada; si la reparación escribe, se
   resincroniza de la DB, porque escribió en el **medio** de la serie y
   agregarle la barra nueva encima arrastraría el hueco.

   Las otras dos series de `_repair` ya tenían reintento y por eso nunca
   mostraron el problema: los derivados los reescribe el scheduler del feed y
   `_refresh_context` los relee en cada barra; el funding lo reingesta
   `_maybe_ingest_funding` al acercarse a su tolerancia. Las velas eran la
   única sin camino de vuelta.

   **Lo que el preflight no puede ver**: cuando se corrió, la serie terminaba
   en la última vela previa al apagón y **no había ningún hueco interior** —
   lo creó el poll REST después, al escribir la primera vela post-arranque. El
   preflight mira el pasado; ese hueco se abre en el futuro. Es un punto ciego
   estructural del chequeo, no un defecto de él, y la única defensa posible
   está del lado del analista. `analyst.status()` expone
   `candle_repair_pending` para que `/api/health` distinga "hueco que se
   arregla solo en la próxima barra" de "corte de datos de Binance".

10. ✅ **Corte de validación forward a n=288 (2026-09-04)**. El objetivo de
   ~280 se alcanzó con la corrida arrancada el 31-08: **288 resueltos, 16
   abiertos, 0 descartados por huecos**, un solo `model_version`
   (`bob-forecast-0.1.0+vol=gbm`). Artefacto congelado y versionado en
   `backend/artifacts/forward-ETHUSDT-15m-n288-*.txt|.json`. La corrida
   **sigue** para cerrar el intervalo del cono (ver abajo).

   **Veredicto — el target de volatilidad sobrevive el forward, y más fuerte
   que en el gate.** El primer reporte parecía decir lo contrario: R² forward
   **+0.240** contra el **+0.400** del walk-forward. No era degradación, era
   un error de lectura que el propio reporte inducía, y por dos motivos que
   quedaron cerrados:

   - **`R2_vs_base` salía `nan`** porque el tracker nunca calculaba los
     baselines forward, así que la única comparación posible era el R² contra
     la media de la propia muestra. Pero el R² se mide contra la dispersión
     del target **en esa ventana**, y tres días tienen una fracción de la
     dispersión de dos años: cae por aritmética aunque el modelo esté
     idéntico. La prueba es que sobre esas mismas 288 barras los **tres**
     baselines dan R² negativo —peores que la media constante— mientras el
     modelo queda en positivo:

     | | RMSE | R² | R² vs EWMA | QLIKE |
     |---|---|---|---|---|
     | **modelo** | 0.00332 | **+0.240** | **+0.548** | **0.2381** |
     | ewma | 0.00494 | −0.681 | — | 0.4464 |
     | garch | 0.00570 | −1.240 | −0.332 | 0.3773 |
     | har | 0.00445 | −0.363 | +0.189 | 0.3183 |

     Contra EWMA, que es el número comparable, el modelo **mejora** de +0.367
     (gate) a **+0.548** (forward). Diebold-Mariano: gana a EWMA con p=0.0015
     y a HAR con p=0.0076, mismo signo y significancia que el gate. QLIKE, que
     no depende de la dispersión de la muestra, también mejora (0.2381 vs
     0.4098). Mincer-Zarnowitz β=1.012 con α≈0: insesgado y eficiente.

     ⚠ **Ese argumento sobre QLIKE es falso y quedó corregido en el punto 13.**
     QLIKE es escala-libre *por observación*, pero su **media está dominada por
     la cola** —el decil superior aporta el 71% del total—, así que depende de
     la ventana tanto como el R², por otra vía. Con n=1158 la misma métrica da
     0.5123, o sea *peor* que el gate, y tampoco eso es un hallazgo: su IC por
     bloques contiene los dos valores. La frase se deja escrita porque el error
     de razonamiento importa más que el número: se usó como evidencia
     independiente algo que no lo era.

   - **El cono se leía sin su incertidumbre.** Cobertura empírica 90,6% al 95%
     nominal y 77,8% al 80% — parece bajo-cubrir. Pero los pronósticos **se
     solapan**: con H=16, dos consecutivos comparten 15 de 16 barras de
     horizonte, y la autocorrelación lag-1 de los aciertos es **+0,67 / +0,76**
     medida sobre los datos reales. Son ~18 observaciones independientes, no
     288. Con bootstrap de bloques móviles el IC95% da **[79,5%, 97,6%]** y
     **[61,1%, 89,9%]**: el nominal cae **dentro** en los dos niveles.

     Esto no es un detalle de presentación. Tratando los aciertos como
     independientes el IC al 95% habría dado **[87,2%, 93,8%]**, que **excluye**
     el nominal — o sea un falso hallazgo de bajo-cobertura, publicado con
     apariencia de rigor. La corrección de bloques da vuelta la conclusión.

   **Lo que esto implica: el criterio de 280 estaba bien dimensionado para la
   volatilidad y sub-potenciado para el cono.** Con ±9pp de incertidumbre esta
   muestra no puede resolver una desviación de 4pp. Llevar el IC a ±4,5pp pide
   ~4× bloques ≈ **1.150 resueltos ≈ 12 días de mercado**. Decisión de
   Nichelson (04-09): la corrida sigue con esa meta; el corte de n=288 queda
   congelado igual, así el veredicto de volatilidad está cerrado y fechado
   pase lo que pase con el resto.

   Señal coherente y menor, que apunta en la misma dirección: la razón
   realizada/pronosticada da media 1,069 (mediana 1,004) —sigma algo corta en
   las colas— y el ACI ya reaccionó solo, bajando `alpha_t` de 0,05 a 0,023 y
   de 0,20 a 0,132. El mecanismo hace su trabajo; lo que no se puede todavía
   es medirlo con precisión.

11. ✅ **El reporte forward se congela en artefacto** (`--artifact` en el
   tracker), igual que los del gate y por la misma razón: el veredicto de una
   fase tiene que salir de un archivo verificable y no de un resumen escrito a
   mano. Antes el tracker imprimía a stdout y no persistía nada. El `.json`
   trae las cifras crudas —baselines, DM, IC del cono, `model_versions`— y el
   encabezado marca **⚠ MUESTRA MIXTA** si conviven dos `model_version`, que
   es la contaminación que el tracker promediaba en silencio.

12. ✅ **La cuarta variante del hueco de velas: el que nace en caliente
   (2026-09-08)**. Encontrada auditando la corrida, no por un error nuevo — el
   analista reportaba correctamente `no se pronostica sobre un hueco` cada vez.
   Lo que no hacía nadie era pedirle esas velas a Binance.

   **La medición que la destapó**: 69 barras sin pronóstico en 13 tramos, pero
   **0 huecos de datos** en klines y derivados. O sea el dato estaba y el
   pronóstico no, que es exactamente el patrón que el punto 8 enseñó a mirar.
   Dos tramos concentran 49 de las 69: 09-02 20:15→02:00 UTC (22 barras) y
   09-06 05:15→12:15 UTC (27 barras), y los dos cierran en un reinicio o un
   reajuste, no en una barra cualquiera.

   **La causa**: Windows durmió el equipo con el backend arriba. Al despertar,
   el poll REST escribió la vela en curso y las del sueño quedaron como hueco
   interior. `_repair_candles` solo corre al ajustar y al reajustar, y
   `_candle_repair_pending` —el arreglo del punto 9— marca únicamente el fallo
   de **red** de la reparación. Acá la red estaba perfecta: lo que faltaba era
   enterarse del hueco. `_compute_and_heal` hacía su `_resync()`, releía la DB,
   encontraba el mismo hueco y publicaba `analysis.error` — sin volver a llamar
   a la reparación nunca. Se destrababa recién en el reajuste de las 24 h.

   El punto 9 dejó escrito que "las velas eran la única serie sin camino de
   vuelta" y le construyó uno; lo que no vio es que ese camino tenía **una sola
   puerta de entrada**, la del fallo de red. La lección se repite: el disparador
   correcto de una reparación no es que la reparación anterior haya fallado,
   sino que **falte el dato**, sin importar cómo se llegó a que faltara.

   Ahora el descubrimiento del hueco al ir a emitir enciende la misma bandera
   (`_flag_tail_gap_for_repair`) y la próxima barra lo repara. El error sigue
   saliendo a la superficie —no emitir sobre una ventana que no existió es lo
   correcto y lo exige la regla 5—, pero se arregla solo en 15 minutos en vez
   de 7 horas.

   **La firma es el conjunto de huecos de la cola, no el primero.** Si la
   reparación tuvo red y aun así no llenó el hueco, Binance no publica ese
   rango y re-pedirlo cada 15 min durante las ~100 h que tarda en salir de la
   ventana de warm-up sería rafaguear REST contra la regla 7: se pide una vez
   por conjunto distinto. Guardar solo el primer hueco era la opción simple y
   estaba mal — un hueco irreparable viejo taparía a uno nuevo que sí se puede
   cerrar, y el analista se quedaría mudo de más. `_tail_gaps` es una sola
   definición de "la cola" para sus dos usos (negarse a emitir y decidir si
   vale pedir el rango); con dos definiciones el analista podría negarse por un
   hueco que la reparación no mira.

   Los dos tests nuevos **fallan contra el código previo**, que es lo único que
   los hace valer: uno reproduce el sueño del equipo, el otro fija que el rango
   que el exchange no tiene se pide una vez y que un hueco nuevo sí merece el
   suyo.

13. ✅ **CIERRE DE LA FASE — la meta de 1.150 se alcanzó y el cono pasa
   (2026-09-14)**. Backend apagado, DB congelada, corrida terminada: **1.157
   resueltos, 16 abiertos, 0 descartados por huecos**, un solo `model_version`
   (`bob-forecast-0.1.0+vol=gbm`) sobre la ventana 2026-08-31 15:30 ..
   2026-09-14 00:30 UTC. Artefacto congelado y versionado en
   `backend/artifacts/forward-ETHUSDT-15m-n1157-*.txt|.json`.

   Por qué abajo aparecen 1.157 y también 1.158: el corte del artefacto se tomó
   con el backend todavía arriba y alcanzó a resolver **un** pronóstico más
   antes de apagarse. El veredicto se cita del artefacto (n=1.157); el
   diagnóstico se recalcula de la DB tal como quedó (n=1.158), así que quien lo
   corra hoy ve ese número. Ninguna conclusión se mueve por una barra, y es
   preferible la diferencia visible a un artefacto que no reproduzca la DB.

   **El cono conformal: veredicto cerrado, y pasa.** Era lo único que la meta
   de 1.150 compraba, porque n=288 no tenía potencia para resolverlo:

   | nivel | gate (walk-forward) | forward n=288 | **forward n=1157** | IC95% por bloques |
   |---|---|---|---|---|
   | 80% | 79,90% | 77,8% | **79,9%** (−0,1pp) | [73,0%, 85,8%] ✓ dentro |
   | 95% | 94,79% | 90,6% | **92,7%** (−2,3pp) | [88,4%, 96,1%] ✓ dentro |

   El cálculo de potencia del 04-09 era correcto y conviene que quede
   registrado que lo fue: 18 → **72 bloques independientes** dio exactamente el
   estrechamiento √4 = 2× que predecía (±9pp → ±3,9pp al 95%). La muestra
   **ahora sí** puede resolver una desviación de 4pp, y la que encuentra es de
   2,3pp. Al 80% el forward reproduce la cobertura del gate con 0,005pp de
   diferencia. La banda del 80% sigue siendo más ancha (±6,4pp) porque la
   varianza de un Bernoulli es máxima en el centro, no porque falte muestra:
   con el punto estimado a −0,1pp del nominal, ese ancho no cambia ninguna
   conclusión.

   **Lo que parecía degradación no lo es.** QLIKE subió de 0,2381 (n=288) a
   0,5123 y la pendiente de Mincer-Zarnowitz de 1,012 a 1,1342. Las dos
   lecturas se caen al ponerles su IC por bloques, que es la misma corrección
   que en n=288 dio vuelta la lectura del cono:

   | | forward | IC95% por bloques | gate | β=1 |
   |---|---|---|---|---|
   | QLIKE | 0,5123 | [0,2676, 0,8460] | 0,4098 → **dentro** | — |
   | MZ β | 1,1342 | [0,8279, 1,4748] | 1,0201 → **dentro** | **dentro** |

   Con ~72 bloques independientes esta muestra no distingue 0,24 de 0,41 ni de
   0,51, ni una pendiente de 1,13 de una de 1,00. **Y de ahí sale la corrección
   del punto 10**: el argumento de que QLIKE "no depende de la dispersión de la
   muestra" y por eso servía como evidencia independiente es falso. Es
   escala-libre por observación, pero su media la fijan unas pocas barras de
   pico —el decil superior aporta el **71%** del total, mediana por observación
   0,1240 contra media 0,5123—, así que depende de la ventana tanto como el R².
   Medido por sub-ventanas: 0,2378 / 0,5203 / 0,1483 / 1,1408. El error de
   razonamiento es el que importa, no el número.

   Lo que **sí** se sostiene es la comparación contra baselines, que es la que
   el gate usa para decidir: R² vs EWMA mejora de +0,367 (gate) a **+0,559**, y
   Diebold-Mariano gana a EWMA con p=0,0009 y a HAR con p=0,0000 — mismo signo
   y significancia que el gate. Los tres baselines dan R² negativo en esta
   ventana. **El target de volatilidad sobrevive el forward.**

   **El hallazgo nuevo, y el que tiene consecuencia operativa: el techo de la
   sigma.** El modelo no puede emitir una sigma alta.

   | | mediana | p95 | máximo |
   |---|---|---|---|
   | pronosticada | 0,00765 | 0,01305 | **0,01787** |
   | realizada | 0,00738 | 0,01612 | **0,04919** |

   **42 de 1.158 barras (3,6%) tuvieron sigma realizada por encima del máximo
   que el modelo llegó a emitir en toda la corrida**, y el máximo realizado es
   **2,75×** ese techo. El evento es uno: 09-11 09:45→12:15 UTC, realizada
   0,045–0,049 contra pronósticos de 0,008–0,010, o sea **4,8× a 5,6× corta**.
   Es lo que hace un GBM de media condicional —encoge hacia el centro de su
   distribución de entrenamiento— y se lee en la razón realizada/pronosticada:
   **mediana 0,940 con media 1,059**, o sea sobra en la calma y falta en el
   pico. Para un usuario apalancado y averso al riesgo es la dirección que
   duele: TP y SL dimensionados por la sigma pronosticada quedan demasiado
   ajustados exactamente donde importa.

   El ACI es la compensación de diseño y se lo ve trabajando con precisión:
   bajó `alpha_t` de 0,05 a **0,025** —ensancha el cono— donde había
   sub-cobertura, y de 0,20 a **0,194**, casi sin moverse, donde la cobertura
   estaba clavada en el nominal. Movió mucho donde había que mover y nada donde
   no. **Consecuencia para la Fase 6**: la página Signal ya muestra el cono con
   su cobertura medida, y debe mostrar también que la sigma tiene techo — un
   nivel derivado de sigma no es una cota superior del movimiento posible.

   **Emisión: se perdió el 8,7% de las barras, y casi nada es del software.**
   112 barras sin pronóstico de 1.286, con **0 huecos de klines** en la ventana
   y **0 descartados por hueco**. Atribuidas en 24 tramos:

   - **90 barras con el proceso abajo o suspendido** → la pérdida que el diseño
     acepta y que la tabla de abajo ya declaraba. 22 reinicios en 13 días.
   - **22 barras con el analista mudo y el proceso vivo** → la clase de bug del
     punto 12. De esas, **18 pre-arreglo y 4 post-arreglo**, y las 4 son
     singles aisladas: exactamente el costo de diseño del arreglo (descubre el
     hueco, lo marca, repara, emite en la barra siguiente).

   Los arreglos de los puntos 9 y 12 quedan **validados en producción y no solo
   en test**. El del punto 9 se lee textual dos veces en el log: 09-11 19:35 y
   09-12 23:49, `getaddrinfo failed` al arrancar en una red que todavía no
   estaba, hueco marcado como pendiente, y la barra siguiente lo cierra
   —2 barras de costo en vez de un hueco permanente—. Los tramos largos
   post-arreglo (21, 8 y 3 barras) son todos equipo abajo o dormido.

   **La trampa de medición que apareció al atribuirlas**, porque va a volver:
   `backend.log` escribe en hora local y **Chile cambia a horario de verano el
   06-09, en el medio de la ventana**. Un offset fijo de +3h —el primer intento—
   desplaza una hora toda la mitad anterior al cambio y reasigna tramos enteros
   entre "proceso abajo" y "analista mudo": daba 84/28 en vez de 90/22. Hay que
   convertir con el offset vigente en cada timestamp, que es lo que hace
   `log_activity`. El discriminador tampoco puede ser el reinicio limpio: un
   equipo que Windows durmió con el backend arriba vuelve sin haber escrito una
   sola vela y, desde la lista de arranques, aparenta haber corrido todo el
   tiempo. La prueba de vida es la actividad real en el log.

   **El EV confirma el álgebra del hallazgo (a) con 1.157 muestras.** Proyectado
   −0,1784% contra realizado −0,1430% en long, y −0,1631% contra −0,1421% en
   short: las dos direcciones negativas, el realizado apenas mejor que la
   proyección. El reparto es el espejo que predice un camino sin deriva —long
   tp 48,6% / sl 45,4%, short tp 45,2% / sl 48,7%, vertical 6,1% en las dos—.
   No hay edge direccional, y ahora está medido en vivo además de deducido.

   **Procedencia, que es lo que hace auditable este cierre.** El veredicto sale
   del artefacto del tracker. Las cuatro mediciones que ese artefacto no trae
   —atribución de las barras perdidas, techo de la sigma, QLIKE como estadística
   de cola, e ICs por bloques contra el gate— salen de
   `scripts/diagnose_forward.py`, que es de solo lectura sobre la DB y **lee las
   referencias del gate del artefacto** de su variante y su estimador en vez de
   copiarlas. Ninguna cifra de este punto es un cálculo hecho a mano: se
   reimprime con

       uv run python scripts/diagnose_forward.py

   **Lo que la Fase 5 NO cierra.** El KPI 1 sigue sin pasar el gate y sigue en
   gris (regla 2): esta corrida no lo evaluó, midió el target de volatilidad y
   el cono. El KPI 3 sigue sin emitirse en vivo. Y el techo de la sigma queda
   como el sospechoso abierto más concreto que deja la fase: si algún día se
   busca mejorar la sigma, el lugar donde el modelo pierde está identificado y
   fechado.

**Correr la validación: qué sobrevive a una pausa y qué no.**

No hacen falta 72 horas ininterrumpidas. Lo que se necesita es un número de
pronósticos resueltos —~280 dimensionó el veredicto de volatilidad, ~1.150 el
intervalo del cono— y el reloj puede pararse entre medio: todo lo que importa
vive en SQLite, no en memoria. Medido en la corrida que cerró la fase: 22
reinicios en 13 días costaron el 8,7% de las barras y **ni un solo resuelto**.

| | sobrevive a apagar el equipo |
|---|---|
| Pronósticos emitidos, con su vector completo | ✅ están en `ForecastRecord` |
| Resultados ya resueltos y el reporte de cobertura | ✅ se recalculan de la DB |
| Estado del ACI (`alpha_t`, cobertura acumulada) | ✅ se **deriva** de los registros resueltos con `replay_cone_state` — por eso no se persiste aparte |
| Velas y derivados del rango caído | ✅ el analista los repara al arrancar |
| Barras que ocurrieron con el proceso abajo | ❌ esas no se pronostican nunca |

Tres cosas rompían esto y quedaron cerradas, **cada una encontrada midiendo,
no razonando**:

1. **El hueco de velas era permanente.** El feed reconecta y escribe la vela
   actual, así que `download --resume` reanuda desde ahí y salta el rango
   caído para siempre. `data/download.repair_series` pide los huecos
   interiores uno por uno; el analista lo llama al arrancar —y lo **reintenta
   barra a barra si se quedó sin red**, ver el punto 9— y hay `--repair` en
   la CLI. No es cosmético: las ventanas de `features.py`
   cuentan **barras, no tiempo**, así que a partir de un hueco todas las
   features de contexto describen una ventana que no existió —
   `_assert_tail_contiguous` se niega a emitir sobre eso.
2. **El snapshot en vivo no pedía los dos endpoints de top traders.** Se
   asumía que "no vale la pena un request extra"; medido sobre la cola real,
   esas 5 columnas tenían **73 NaN de 96** porque solo las llenaba el archivo
   diario, que llega un día tarde. Ahora `fetch_derivatives` pide cinco
   endpoints. El funding tampoco lo refrescaba nadie en vivo (9 NaN de 96, su
   tolerancia es de 8h exactas): el analista lo reingesta por REST.
3. **La guarda de observabilidad exigía cobertura perfecta** y abortaba el
   arranque por un solo NaN. Confundía dos cosas: `oi_per_px_24h` con 1 NaN
   de 96 —un cociente que se degenera cuando el precio no se movió en 24h—
   contra una familia que no llega nunca. Ahora pide 70% de cobertura; la
   barra concreta la sigue vigilando `row_is_usable` al emitir.

**Procedimiento: `docs/RUNBOOK_FASE5.md`** — verificado end-to-end levantando
el backend real. Arrancar con `uv run python -m uvicorn bob.main:app` (con
`python -m`: `uv run uvicorn` falla con `trampoline failed to canonicalize`) y
dejarlo. El ajuste inicial toma ~83s medidos y corre en background. Para
pausar: cerrar el backend, apagar. Para reanudar: volver a levantarlo — repara
solo. El estado se consulta con
`uv run python -m bob.paper.tracker --symbol ETHUSDT`, que resuelve lo maduro
e imprime la cobertura acumulada.

Dos advertencias que no dependen del software: los snapshots de derivados
recuperan **~41h** por request y la ventana de Binance es de ~30 días, así que
una pausa mayor a ~41h deja un hueco de derivados **irrecuperable**. Y la
suspensión del equipo cuenta como pausa: si Windows lo duerme, el feed se
corta igual.


**Dos hallazgos de la fase, ambos con consecuencia de diseño:**

**(a) El EV no puede ser positivo sin edge direccional — es álgebra, no
mercado.** Para un camino sin deriva con barreras a +a y −b, P(tocar arriba
primero) = b/(a+b), y entonces `EV_bruto = [b/(a+b)]·a − [a/(a+b)]·b = 0`
para **todo** a y b; el neto es exactamente −costo. Mover el TP o cambiar el
ratio R:B no lo levanta: reordena probabilidad y pago en la proporción exacta
que deja el bruto en cero. Verificado en vivo sobre la última barra real de
ETHUSDT: barreras a ±0,788%, costo 0,145%, probabilidad de equilibrio 59,2%,
KPI 1 en 49,6% (long) y 44,4% (short) → las dos direcciones con EV negativo y
`is_actionable=False`.

Lo que la proyección **sí** entrega, y sigue siendo valor operativo: barreras
escaladas a la volatilidad que viene, **distancia a liquidación en sigmas**
(12,4σ a 5x en esa barra), leverage máximo seguro (59,3x) y el cono con
cobertura medida (2.412–2.486 al 80%). Consecuencia para la Fase 6: la página
Signal se construye sobre niveles, cono y riesgo; el EV se muestra **con su
probabilidad de equilibrio al lado**, como el listón a superar y no como una
promesa de ganancia.

**(b) El vivo no puede correr con `full`.** `bookDepth` sale del archivo
diario de data.binance.vision (~1 día de retraso) y `reindex_to_bars` hace un
join exacto por `open_time` —no forward-fill, porque rellenar sería inventar
liquidez—. Toda barra posterior a la última del archivo queda con NaN en las
15 columnas del núcleo, y el analista se callaría **para siempre, en
silencio**. `assert_tail_observable` es el gemelo en vivo de
`assert_columns_trainable`: aquel protege el pasado, este el presente, y falla
nombrando las columnas. Default en vivo: `price+deriv` (los derivados sí
llegan — snapshots cada 30 min, tolerancia de 1h). Usar libro en vivo exige
antes cablear el stream `@depth`.

### Fase 4-bis — XGBoost en el target de volatilidad ✅ (2026-08-26)

Agregado **antes** de arrancar la corrida de validación de la Fase 5, no
después, y esa es la parte que importa: `ForecastRecord` guarda la sigma que
produjo el bundle, así que cambiar de estimador a mitad de la acumulación
mezcla dos modelos en la misma muestra forward y la invalida. Con 2 registros
en la DB el cambio era gratis; con 280 habría costado la corrida.

**Resultado: empate, y el empate es el hallazgo.** Sobre las mismas 69.498
velas, mismos folds, misma semilla:

| variante | estimador | RMSE | QLIKE | R² vs media |
|---|---|---|---|---|
| `price` | GBM sklearn | 0.00559 | 0.3974 | +0.399 |
| `price` | **XGBoost** | 0.00559 | **0.3973** | **+0.400** |
| `price+deriv` | **GBM sklearn** | **0.00568** | **0.4098** | **+0.391** |
| `price+deriv` | XGBoost | 0.00570 | 0.4160 | +0.388 |

Los dos le ganan a EWMA, GARCH(1,1) y HAR-RV con Diebold-Mariano p<0.0001.
Con los mismos hiperparámetros, dos boostings por histograma competentes caen
dentro del 0,3% y cuál gana **se da vuelta con el feature set**. La ventaja
sobre los baselines viene de las features y del diseño del target, no de la
librería. **El default sigue en `gbm`**: no hay razón medida para moverlo y es
el estimador con el que corrió el gate.

Invariante que salió de regalo: el target de dirección es **bit a bit
idéntico** entre los dos, en las dos variantes. Debía serlo —`vol_kind` solo
toca el target 2— y ahora está medido en vez de asumido.

**Dos hallazgos con consecuencia:**

**(a) `assert_columns_trainable` deja de traducir un error y pasa a ser la
única protección.** Medido sobre la misma matriz: sklearn levanta
`ValueError` cuando una columna no tiene un solo valor finito en el train,
**XGBoost ajusta y predice sin decir nada**. El fallo ruidoso se vuelve
silencioso y el run terminaría en verde reportando métricas de un modelo que
aprendió de una columna vacía. Cubierto con un test que ejercita las dos
librerías lado a lado.

**(b) El control de regresión bit a bit se estaba degradando solo.**
`load_series` lee todo lo que hay en la DB, y la DB crece con el feed en vivo:
el mismo comando corrido dos días seguidos entrena sobre muestras distintas
sin que nada falle. Reproducir un run exige **nombrar su última barra**, y por
eso `--until` acepta `YYYY-MM-DD` o `YYYY-MM-DDTHH:MM`. La precisión de
minutos no es capricho: el run del 25-08 cerró en la barra de las 20:30 de un
día que la DB después completó hasta las 23:45, y cortar por fecha sola deja
13 barras de más — suficiente para mover el error de calibración de la
dirección short de 5,1pp a 10,3pp. Con el corte exacto, el run del 26-08
reproduce el artefacto del 25-08 **bit a bit** (AUC 0.518701 / 0.532680, BSS
−0.002801 / +0.000498, RMSE 0.00558709, QLIKE 0.39626506) **atravesando el
refactor de XGBoost**, que es la prueba de que el refactor es neutro.

Procedencia, que es lo que hace medible la Fase 5: el reporte etiqueta la fila
del target 2 con el modelo que la produjo, el `model_version` del bundle lleva
el estimador (`bob-forecast-0.1.0+vol=xgb`) y viaja a `ForecastRecord`,
`analyst.status()` lo expone, y el `run_id` lleva variante **y** estimador.
Como eso rompía el parseo por posición del nombre, `compare.py` lee la
variante de la config del JSON, que es la fuente real.

### Fase 6 — API + Dashboard ✅ construida (2026-09-04)

Construida **durante** la corrida de validación, y eso ordenó casi todas las
decisiones: el backend de producción no se reinició ni una vez, así que la
capa entera se desarrolló y verificó contra una segunda instancia offline
(`BOB_LIVE_DATA=false ... --port 8001`) que lee la misma SQLite y no escribe.
El dev server apunta ahí con `BOB_API=http://127.0.0.1:8001`. Los endpoints
nuevos **no existen todavía en :8000**: decisión de Nichelson (04-09), el
reinicio espera a que la corrida llegue a ~1.150 resueltos.

> **Actualización (14-09).** La condición se cumplió y la corrida está cerrada
> (punto 13 de la Fase 5) con el backend apagado, así que **el reinicio ya no
> está bloqueado**: el próximo arranque sirve los cinco grupos en :8000 y la
> instancia offline de :8001 deja de ser necesaria para probar.
>
> Y una corrección de registro: "el backend de producción no se reinició ni una
> vez" describía el proceso *que cargó el código de la Fase 6*, no la corrida.
> Medidos sobre `backend.log`, hubo **22 reinicios en 13 días** —varios antes
> del 04-09—, y por eso el cierre pudo separar las barras perdidas por proceso
> abajo de las perdidas por analista mudo. La frase no sostiene su lectura
> literal.

1. ✅ `api/routes/`: cinco grupos bajo `/api`, todos de **solo lectura**.
   `market` (velas + huecos), `analysis` (último pronóstico, reproyección por
   leverage, features por familia), `history` (emitidos, cobertura forward,
   CSV), `backtest` (artefactos del gate y corte forward congelado),
   `settings` (espejo de la config efectiva)
2. ✅ **Ninguna ruta escribe, y hay un test que lo verifica recorriendo todo
   lo que llama el dashboard.** Un endpoint que resolviera pronósticos haría
   avanzar la validación forward cuando alguien abre el navegador, y una
   muestra que se mide a sí misma no mide nada
3. ✅ **Nada toca SQLite en el event loop**: el mismo proceso corre el feed, y
   un request de 5s ajustando GARCH rompe la regla 6 sin que se vea por qué.
   Todo por `asyncio.to_thread`; `coverage_report` además cachea 2 min con
   lock por clave — medido 5,0s la primera llamada, 0,18s la siguiente
4. ✅ **El slider de leverage recalcula en el servidor.** Dibujar la
   liquidación en TypeScript pondría la fórmula —con sus tiers de MMR y su
   buffer— en dos lugares, y el número que el usuario mira antes de entrar
   apalancado saldría de la copia vieja. `project_setup` es puro: microsegundos
5. ✅ `feature_family_index`: corre `assemble_features` sobre una serie
   sintética para sacar el mapa nombre→familia sin rearmar la matriz sobre 69k
   barras. Una tabla de prefijos ya estaría mal hoy — `taker_buy_ratio` es de
   precio y `taker_ratio_log` es de derivados
6. ✅ **Bug encontrado al probar**: `/api/backtest/gate` devolvía el artefacto
   **xgb** para `price+deriv` cuando el vivo emite con **gbm**. Desde la Fase
   4-bis el `run_id` lleva el estimador, así que ordenar por nombre completo
   ordena por librería antes que por fecha (`gbm` < `xgb`) y "el más reciente
   de esta variante" devolvía el otro modelo. El dashboard habría citado, al
   lado del KPI 1, la medición de un modelo que no produjo ese número. Filtra
   por variante **y** estimador, y ordena por el timestamp del nombre
7. ✅ Las cinco páginas. **La Signal page se construye alrededor del KPI 2**:
   niveles y riesgo arriba, cono con su cobertura medida, seguimiento de la
   sigma, y el gauge de Seguridad **abajo y en gris** con AUC/BSS/calibración
   reales al lado. El diseño original lo pedía arriba y grande; darle al
   número menos respaldado el lugar de más autoridad contradiría la decisión
   del 25-08 en la única capa que el usuario mira
8. ✅ **El veredicto del gate viene de la API, no está escrito en el TSX.** El
   día que el motor direccional pase los dos criterios, el gauge se enciende
   solo. Hardcodearlo obligaría a que alguien se acuerde de borrarlo
9. ✅ **El EV sale siempre con su probabilidad de equilibrio al lado**
   (hallazgo (a) de la Fase 5). En la barra real del 04-09: EV −0,232% contra
   un equilibrio de 88,9%
10. ✅ WS ruteado de verdad (antes `void frame`): los eventos lentos —vela,
    pronóstico— invalidan la query y el dashboard relee de la DB, que es la
    única que sabe si ya se resolvió; el precio en curso va al store y no toca
    la red. Sin esa división habría dos versiones del mismo pronóstico en
    pantalla y ninguna forma de saber cuál se está mirando
11. ✅ Paleta de series (`lib/chartTheme.ts`) separada de la de estado y
    **validada**: los colores del producto dan L 0,79–0,82 en OKLCH y quedan
    fuera de la banda de modo oscuro. La terna `#4a8fe0/#b8820d/#18a37c` pasa
    las cinco comprobaciones contra `#0e0f0f` con `--pairs all`

**Cuatro cosas se encontraron mirando las capturas, no compilando** — y esa es
la parte que conviene que quede escrita, porque `tsc` estaba en verde en las
cuatro: el arco del gauge era invisible sobre su propio riel (el gauge parecía
vacío, que es mentir por omisión); "precio de liquidación 0.00" a 1x es
correcto y se lee como dato faltante; `toLocaleString("es")` escribía "42.098"
muestras al lado de "0.2474" de Brier, dos convenciones a diez píxeles; y el
reporte de cobertura viene en UTC mientras la tabla se dibuja en hora local,
lo que aparentaba cuatro horas de hueco.

**Decisiones de alcance, tomadas por Nichelson (04-09):**

- **Backtest es de solo lectura.** Lanzar un walk-forward desde la UI son ~10
  ajustes de boosting sobre 69k barras compitiendo por CPU con el analista en
  vivo, justo mientras corre la validación. Los runs siguen saliendo de la CLI
- **Settings es un espejo, no un formulario.** Cambiar `feature_set` o
  `vol_kind` en caliente mete dos `model_version` en la misma muestra. Lo
  editable es lo que no toca ningún modelo —leverage, nivel del cono, símbolo—
  y vive en localStorage

**Lo que la Fase 6 NO hace, y lo dice en la propia página**: el KPI 3 no se
emite en vivo (el HMM existe y está testeado, pero el analista no publica
régimen ni duración esperada, y estimarla de otra cosa sería un número sin
respaldo). Cablearlo es trabajo pendiente, no un olvido.

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
