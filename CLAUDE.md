# MAREA — Analista de Liquidez Global (módulo cripto: BOB)

> Contexto estratégico del ecosistema (memoria de LUPE):
> @../lupe/contexto/MEMORIA.md

## IDENTIDAD VIGENTE (2026-09-26) — MAREA

Por decisión explícita de Nichelson, el proyecto se reestructura y agranda su
alcance. **Registro completo de la decisión: `docs/REESTRUCTURACION_MAREA.md`.
Leerlo antes de trabajar en cualquier fase M.**

**MAREA** es un analista de **liquidez global y de flujos entre clases de
activo**, con horizonte de **semanas a meses**. Mapea, sigue y monitorea
hacia dónde se mueve la liquidez mundial y qué clases de activo la reciben, y
entrega un panel que ayuda a Nichelson a decidir inversiones alineadas con la
macro global. **Nunca ejecuta órdenes** y **sugiere inclinaciones entre clases
de activo, no operaciones**.

Cuatro capas:

1. **Liquidez**: balance Fed − TGA − RRP, M2, dólar, tasas reales, crédito,
   condiciones financieras, COT. **Monitoreo, sin pronóstico.**
2. **Activos macro**: ranking relativo sobre ~35 proxies (acciones, Tesoro,
   crédito, oro/plata, commodities, cobre, REITs, divisas, cripto). **Pronóstico
   con pre-registro y gate propio — condicionado a que la medición de potencia
   de la M3 lo permita.**
3. **Cripto = módulo BOB**: todo lo que sigue más abajo en este documento. Es
   el activo principal dentro del estudio macro y tiene su propio panel.
4. **Narrativa**: noticias que Nichelson pega en una sesión. **Nunca es feature
   del modelo**; su función es marcar divergencias entre lo que se dice y lo
   que muestran los datos, con peso bajo y declarado.

**Decisiones de diseño fijadas (26-09):** agentes y skills **solo en Claude
Code** (`.claude/agents/`, `.claude/skills/`), sin LLM en el camino de cálculo
ni costo de API en runtime; **solo datos gratuitos** (proxy declarado donde no
existan); **lente Chile** (cobre, USD/CLP, ECH, BCCh); **vivo de 15m
congelado** como módulo, sin borrar nada.

**Qué NO se renombra todavía:** el paquete Python `bob`, el directorio
`proyectos/bob` y la DB. La tarea programada `BOB_panel_forward_diario` corre
`bob.paper.panel_forward` desde una ruta absoluta, y la Fase 10-bis tiene 180
días por delante. El código macro nuevo vive en el paquete hermano
**`marea`** del mismo backend.

### Fases de MAREA

| fase | contenido | estado |
|---|---|---|
| **M0** | Decisión, este documento, agentes y skills | ✅ 2026-09-26 |
| **M1** | Ingesta macro gratuita en `marea/data/`: FRED con vintages (25 series, point-in-time), Tiingo (23 ETFs), CFTC (13 mercados COT) y BCCh (6 series). `uv run python -m marea.data.ingest todo` actualiza las cuatro en ~2 min. Stooq descartado. Detalle: `docs/MAREA_FUENTES.md` | ✅ 2026-09-29 |
| **M2** | Monitor de liquidez point-in-time (`marea/liquidity.py`, `marea/monitor.py`) + página **Macro**. Régimen por percentil del cambio a 13 semanas contra el pasado estricto; compuesto de pesos iguales declarados (Fed neta, M2, dólar, tasa real, NFCI). Reconstruir una fecha pasada reproduce la historia fila por fila. Límites: compuesto desde 2020-02 y etiqueta inestable (racha mediana 3 semanas). Detalle: `docs/MAREA_MONITOR.md` | ✅ 2026-10-05 |
| **M3** | Medición de potencia → pre-registro → gate del ranking. **Cerrada (05-10): el ranking NO es viable.** Potencia (`marea/power.py`, `d48db5c`): N efectivo ≈ 6–7, MDE 0,059 con 22 ETFs y H=4. Pre-registro `docs/PREREGISTRO_RANKING_MACRO.md` (`836e603`). Gate: IC +0,036, IC95% [−0,008, +0,078] → no ordena a esta potencia; empata con momentum 12-1; AUC 0,516; placebo limpio (+0,003). El bloque de liquidez **resta** (−0,022, sin potencia) y el IC depende del signo del mercado (+0,09 si SPY sube, −0,09 si cae). Por el pre-registro: **MAREA macro queda como monitoreo** y el pronóstico vive en el módulo cripto. Artefacto `artifacts/marea-ranking-20261005T221954Z` | ✅ 2026-10-05 |
| **M4** | Registro de narrativa validado y solo-agregar (`marea/narrative.py`, `narrativa/registro.jsonl`) + insumos del memo en un comando (`marea/brief.py`, 3 s, 118 MB): frescura, liquidez, fuerza relativa descriptiva, veredicto M3, estado operativo del forward cripto **sin su IC** (ciego) y narrativa de 7 días. Agentes y skills actualizados: ningún memo cita un ranking esperado | ✅ 2026-10-05 |
| **M5** | Fuentes nuevas de liquidez y ciclo cripto, elegidas del catálogo de Outliers (19 de 182) y traídas **de la fuente primaria gratuita**: stablecoins (DefiLlama), Baa−10a, breakevens, 10a−3m y recesión, Sahm en tiempo real, SLOOS, claims, H.8, Risk de ciclo BTC **point-in-time**, amplitud y altseason del panel, funding BTC−ETH, snapshot de opciones Deribit. Solo monitoreo. Minería, Value y Gem Hunters fuera. Detalle: `docs/M5_ALCANCE.md`. **M5.1 ✅ (07-10)**: 9 series FRED nuevas con vintages (34 en total). **M5.2 ✅ (07-10)**: stablecoins de DefiLlama (2 totales + 7 tokens, muertos incluidos) con **vintages construidas por corrida** porque la fuente reescribe su historia; emisiones netas, supply de BTC y SSR en `marea/stablecoins.py`. **M5.3 ✅ (07-10)**: probit de recesión de la NY Fed reproducido desde FRED (760 meses, ≤0,23 pp; se sumó `DTB3`) y Risk de ciclo BTC en versión point-in-time junto a la con lookahead (techo 2021: 0,756 contra 0,867). **M5.4 ✅ (07-10)**: amplitud (% sobre SMA200 y 20 semanas) y Altcoin Season desde el panel con deslistados, y funding BTC/ETH completo de Binance. **M5.5 ✅ (07-10)**: foto diaria del libro de opciones de Deribit (put/call, OI) con el crudo comprimido, y tarea **`MAREA_snapshot_diario`** a las 12:00 locales (Deribit + DefiLlama + funding), verificada desde el programador. **M5.6 ✅ (07-10)**: las 9 series nuevas como contexto del monitor (régimen e historia reproducidos bit a bit), sección **4b** del brief y `GET /api/macro/panorama`. **M5.7 ✅ (07-10)**: TVL DeFi y volumen DEX con vintages, vistas de Wikipedia (esperan `WIKI_CONTACT` en `.env`) y `marea.attention` como contraste de la capa 4 | ✅ 2026-10-07 |
| **M6** | Frontend: portada simple (porcentajes y una frase por tarjeta), Cripto como sección desplegable, lo técnico como capa de detalle, MAREA en todo lo visible. Fusión con BOB **solo de cara al usuario** hasta el corte de la 10-bis. Se itera sobre opciones registradas. Detalle: `docs/M6_ALCANCE.md`. **M6.0 ✅ (07-10)**: inventario en `docs/M6_INVENTARIO.md` — la portada actual es la Señal 15m congelada con datos de 54 h; el panorama M5 y el estado del forward 10-bis no tienen página. Todo lo del forward se mantiene (decisión de Nichelson). **M6.1 ✅ (07-10)**: navegación por secciones con Cripto y Laboratorio desplegables, tarjetas con "Ver detalle", páginas Inicio, Panorama y Panel diario (forward sin IC), encabezado con frescura por fuente. **M6.2 ✅ (08-10)**: portada de seis tarjetas con gráfico chico (variante A, elegida sobre B), flecha de cambio en gris y tarjeta nueva de clases de activo. **M6.3 ✅ (09-10)**: Cripto ▸ Resumen con el respaldo de cada página en una palabra (la Señal 15m en gris, "congelada"), menú plegable en el móvil, y el filtro de régimen de la Señal en gris y fechado cuando el pronóstico es viejo. **M6.4 ✅ (09-10)**: Laboratorio ▸ Resumen con los 11 experimentos medidos y su veredicto, leído de cada artefacto (`GET /api/lab/experiments`); los negativos en gris neutro; el forward del panel como "en curso" sin resultado. **M6.5 ✅ (09-10)**: textos revisados en la página real (`docs/M6_TEXTOS.md`), cifras escritas a mano pasadas a la API, percentiles truncados y dichos hacia su lado, punto decimal en la Señal, glosario en Ajustes; corregido un error de contenido (el funding se describía como "liquidaciones") | 🟡 en curso |

Al final de cada una, resumir y esperar confirmación (igual que las fases de
BOB).

### Agentes y skills

| agente | rol | skill |
|---|---|---|
| `ingesta` | Corre las CLIs de datos, reporta cobertura y huecos; nunca rellena | `/ingesta` |
| `liquidez` | Lee el régimen de liquidez global y su cambio | `/liquidez` |
| `cross-asset` | Ranking, fuerza relativa, correlaciones, rotación | `/ranking` |
| `cripto` | Módulo BOB: panel diario y su corrida forward | `/cripto` |
| `noticias` | Clasifica, contrasta con datos, evalúa manipulación, registra | `/noticia` |
| `auditor` | Lookahead, pre-registro, potencia, regla 2 | `/auditar` |
| `coordinador` | Integra todo en un memo que separa lo medido de lo narrado | `/informe` |

Los agentes **leen y calculan con el código del repo**; no inventan cifras.
Una cifra en un memo que no salga de un comando o de un artefacto es un bug de
la misma gravedad que un lookahead.

### Regla 12 (nueva, vigente hasta el corte de la Fase 10-bis)

**No se cambia el comportamiento de la corrida forward del panel.** Su
árbol de dependencias es ancho: `paper/panel_forward.py`,
`models/panel_production.py`, `models/panel_experiment.py`,
`models/forecast.py`, `backtest/panel_runner.py`, `data/panel.py`,
`data/download_panel.py`, `signals/{cross_section,panel_features,universe,features,numeric}.py`,
`paper/forward_eval.py` y `backend/scripts/panel_daily.ps1`. Cambiarlo a mitad
de muestra mezcla dos modelos en la misma validación, que es exactamente lo
que la Fase 4-bis enseñó a no hacer.

Como `marea` va a importar parte de ese árbol (`numeric`, `metrics`,
`validation`), la regla operativa es: **cualquier commit que toque un módulo
de esa lista debe reproducir bit a bit el artefacto primario del panel**
(`panel_runner --until 2026-09-21`) antes de entrar. Si no reproduce, no entra.
Lo nuevo se escribe en `marea/`, no se "generaliza" lo de `bob/`.

---

## MÓDULO BOB — identidad original (cripto)

> Todo lo que sigue es la historia y el estado del módulo cripto. Sigue
> vigente como tal; donde dice "BOB" se lee "el módulo cripto de MAREA".

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

> **Extensión (2026-09-14, Fase 5-bis).** "Apoyarse en volatilidad" resultó ser
> **dos** cosas y no una, con respaldo distinto. Los **niveles** usan la
> *magnitud* de la sigma, que está rota en la cola. El **filtro de régimen**
> —cuándo no entrar, o con cuánto leverage— usa solo su *orden*, y eso pasó su
> propio criterio pre-registrado: rechazando el 16,9% de las barras se evita el
> 52,0% de las liquidaciones a 20x, contra 35,9% del mismo filtro hecho con
> EWMA. La decisión del 25-08 no cambia; se afina: **el producto se apoya
> primero en el orden de la volatilidad y solo después en su magnitud.**

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
   **2,75×** ese techo. El más violento es el del 09-11 09:30→15:30 UTC,
   realizada 0,045–0,049 contra pronósticos de 0,008–0,010, o sea **4,8× a
   5,6× corta**.

   > ⚠ **Corrección de registro (2026-09-14, Fase 5-bis).** Esta línea decía
   > *"El evento es uno: 09-11 09:45→12:15"*. Son **tres**: 09-03 11:45→14:30
   > (12 barras), 09-04 08:30→12:15 (16 barras) y 09-11 09:30→15:30 (14
   > barras). Uno cada cuatro días, no una rareza — y la diferencia importa,
   > porque un evento único se lee como mala suerte y tres se leen como
   > propiedad del modelo. El del 09-11 es el único que se había mirado.

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

### Fase 5-bis — El filtro de régimen ✅ (2026-09-14)

**Qué la motivó.** Auditando el techo de la sigma que la Fase 5 dejó abierto
aparecieron tres hechos que parten el producto en dos mitades con respaldo muy
distinto:

1. Los eventos de sub-pronóstico son **tres y no uno** (corrección arriba).
2. **Donde falla la sigma falla también el cono**: en esas 42 barras el cono al
   80% cubrió **0%** y el de 95% cubrió 50%. El 92,7% global lo sostienen las
   1.116 barras calmas. Los dos targets validados fallan **juntos**, y
   exactamente en el régimen que liquida a un usuario apalancado.
3. **Pero el modelo ordena bien**: AUC **0,875** para separar el decil más
   volátil. Lo que no anticipa es el *arranque* — dos de los tres eventos
   empezaron con la sigma en el percentil 45 y 51.

La lectura es que el modelo sabe **en qué régimen estás** y falla en la
**magnitud de la cola**. Y eso importa porque los dos productos posibles no se
apoyan en lo mismo:

| producto | se apoya en | estado |
|---|---|---|
| niveles TP/SL dimensionados por sigma | **magnitud** | rota en la cola |
| filtro "ahora no entres / bajá el leverage" | **orden** | validada |

**El criterio se pre-registró antes de correr nada**
(`docs/PREREGISTRO_FILTRO_RIESGO.md`, commit `5c94510`), que es lo que el
CLAUDE.md exige de cualquier barrido. El leverage primario se eligió por
**potencia** —conteo de eventos, ~89 independientes a 20x— y no por resultado.
1 prueba confirmatoria y 24 exploratorias, declaradas.

**Resultado: el filtro es VIABLE.** 43.422 barras con sigma OOF del mismo
walk-forward purgado del gate, 20x, q=0,80, direcciones agrupadas:

| filtro | lift | rechaza | captura de liquidaciones |
|---|---|---|---|
| **modelo** | **3,077** | 16,9% | **52,0%** |
| ewma | 2,195 | 16,4% | 35,9% |
| garch | 2,181 | 17,1% | 37,3% |
| har | 2,394 | 16,3% | 39,0% |

- **Criterio 1** (mejor que el azar): IC95% por bloques **[2,639, 3,515]**,
  excluye 1,0. Un filtro que rechaza al azar da exactamente 1,000.
- **Criterio 2** (mejor que EWMA): diferencia **pareada** +0,882, IC95%
  **[+0,489, +1,284]**, excluye 0.

Rechazando 1 de cada 6 barras se evita **la mitad** de las liquidaciones a 20x,
y la versión del modelo captura 16pp más que la misma regla hecha con un EWMA.
El lift baja con el leverage (3,08 a 20x → 2,05 a 50x) y sube con `q`, que es
lo que tiene que pasar; 5x y 10x salen etiquetados **sin potencia**.

**El bug que casi lo infla, y por qué se vio.** La primera corrida dio lift
**5,06**. El denominador incluía las ~28.000 barras sin sigma OOF, que no
pueden rechazarse ni liquidar: bajaban la tasa de rechazo sin tocar la captura
e inflaban el lift un **64%** sin que ninguna cuenta fallara. Se detectó porque
`n_bars` (43.422) y el `n` interno (71.344) no coincidían — o sea por una
inconsistencia de contabilidad, no por un error. El test de regresión **falla
contra el código previo** (4000 vs 1984), que es lo único que lo hace valer.

**Lo que sostiene el resultado**, y conviene que quede escrito porque cada una
era una forma de mentirse:

- El umbral es el cuantil del **pasado estricto** en ventana expansiva. Uno
  sobre la muestra completa usaría el futuro para decidir qué es "alto" hoy:
  lookahead de la regla 5, invisible, y el filtro se vería mejor de lo que es.
- La sigma es **out-of-fold**, del mismo walk-forward purgado con embargo.
- Los ICs son por **bloques móviles de largo H** —los setups solapan 15 de 16
  barras— con un test que reproduce `coverage_interval` de la Fase 5, para que
  los dos intervalos del proyecto sean el mismo objeto estadístico.
- La comparación contra el baseline es **pareada**, no dos ICs marginales: dos
  intervalos que se solapan no permiten concluir que no hay diferencia. Mismo
  motivo por el que el proyecto usa Diebold-Mariano y no dos RMSE sueltos.
- El **oráculo satura el lift en 4,1** sobre un máximo teórico de 5, y el ruido
  da 1,01. Es la prueba de que la plomería mide lo que dice medir.

Se reimprime con:

    uv run python -m bob.backtest.riskfilter_runner \
      --features price+deriv --vol-model gbm --until 2026-09-14T00:45

Artefacto versionado en `backend/artifacts/riskfilter-*.txt|.json`, reproduce
bit a bit y cita su pre-registro.

**Lo que la Fase 5-bis NO hace.** No habilita el KPI 1, que sigue sin pasar el
gate y sigue en gris (regla 2) — este filtro no dice hacia dónde entrar. No
vuelve positivo el EV: sigue siendo −costo sin edge direccional. No arregla el
techo de la sigma. Y es medición **offline**: la Fase 5 enseñó que el forward
es otra cosa, así que la validación forward del filtro queda pendiente.

### Fase 5-ter — El techo de la sigma: la hipótesis cuantílica se cae ✅ (2026-09-14)

**La hipótesis.** `VolatilityModel` entrena con pérdida cuadrática sobre
log-volatilidad, o sea estima la **media condicional**, y una media encoge la
cola por construcción. Entrenar el **cuantil** condicional debería cubrirla.

Criterios fijados antes de correr (docstring de `backtest/sigma_tail_runner.py`):
calibrar como cuantil, ganarle al escalado trivial `k·media` con `k` del train,
y menor pérdida pinball. Los tres.

**Resultado: la hipótesis se cae, en los dos cuantiles y por los tres
criterios.**

| q=0,90 | excedencia | IC95% | pinball |
|---|---|---|---|
| nominal | 10,0% | — | — |
| GBM cuantílico | 14,3% | [13,3, 15,2] | 0,001281 |
| `k·media` (k del train) | 14,9% | [13,9, 15,9] | **0,001213** |

El GBM cuantílico **pierde contra multiplicar la media por una constante**, en
pinball y casi en excedencia. Caveat que corresponde declarar: sus
hiperparámetros se heredaron del modelo de media a propósito —para aislar la
variable— y un cuantílico con los suyos podría andar mejor; buscarlos exigiría
su propio pre-registro.

**El hallazgo está en que los DOS fallan igual.** Los dos sub-cubren, en la
misma dirección y en los dos niveles. Eso no es un problema de estimador: es
**deriva**. La cola del test es más pesada que la del train, y ninguna
constante ajustada en el pasado puede seguir eso.

Y el proyecto ya tiene la forma correcta de respuesta a la deriva: el **ACI**
que lleva el cono conformal, que en la Fase 5 movió `alpha_t` de 0,05 a 0,025
justo donde había sub-cobertura. Lo que la sigma no tiene es un mecanismo
equivalente. Aplicándole la misma aritmética —`k(t)·media`, corregido en línea
con retroalimentación retrasada H barras, como en vivo—:

| | q=0,80 | q=0,90 |
|---|---|---|
| nominal | 20,0% | 10,0% |
| **k(t)·media con ACI** | **20,0%** [18,9, 21,1] ✓ | **10,1%** [9,2, 10,9] ✓ |

**Calibra en los dos niveles, y es aritmética sobre el modelo que ya existe.**

⚠ **Pero es un candidato POST-HOC y no se adopta todavía.** Se agregó después
de ver que los dos estimadores estáticos sub-cubrían, así que declararlo
ganador acá sería exactamente el p-hacking que este proyecto evita. Lo que lo
hace prometedor y no arbitrario es que **no tiene un solo parámetro ajustado a
estos datos** —γ se fijó una vez, y el mecanismo es el mismo ya validado para
el cono—. Lo que corresponde es confirmarlo con su propio pre-registro sobre
datos que no tocó.

Y su costo hay que decirlo: **pierde en pinball** (0,001278 contra 0,001213 a
q=0,90). Pinball premia la agudeza, y cubrir la cola se paga con ancho. Para
un usuario apalancado y averso al riesgo la cobertura es lo que importa, pero
el intercambio es real y no se esconde.

**El techo sí sube, aunque no alcance.** El cuantílico a q=0,90 emite un máximo
de 0,07591 contra 0,05327 de la media (**1,43×**) y las barras por encima del
techo caen de 79 a 23. El déficit en las barras donde la media se queda corta
baja de 24,3% a 21,3% del movimiento. O sea: mejora y no resuelve.

Se reimprime con:

    uv run python -m bob.backtest.sigma_tail_runner \
      --features price+deriv --vol-model gbm --until 2026-09-14T00:45

Artefacto en `backend/artifacts/sigmatail-*.txt|.json`.

**Qué NO cambia.** Nada se adoptó: el vivo sigue dimensionando con la media y
el techo sigue siendo un límite declarado. Adoptar cualquiera de estos
cambiaría `model_version`, y eso no se hace a mitad de una corrida forward por
la razón de la Fase 4-bis. El filtro de régimen no se toca — usa el *orden*, y
esta medición es sobre la *magnitud*.

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

### Fase 10 — El panel transversal diario ✅ construida y corrida (2026-09-22)

**Qué la motivó, y por qué no es "otra vuelta al mismo problema".** Explorando
velas **diarias** aparecieron cuatro mediciones que juntas describen un
problema distinto del de 15m:

1. **En diario el costo deja de mandar.** Las barreras son múltiplos de sigma,
   así que crecen con √H mientras el costo sigue fijo: el margen a superar cae
   de **17,7 pp** (15m, H=16) a **1,4 pp** (1d, H=10).
2. **Pero un solo símbolo no tiene potencia.** Con 2.491 velas diarias de
   ETHUSDT —todo lo que existe— y H=5, el IC95% del AUC es **±0,051**. El gate
   saldría inconcluyente por construcción, y el fold 1 entrenaría con ~415 filas.
3. **Lo que parecía margen positivo es deriva.** A H=16 el long da +0,2 pp y el
   short −8,1 pp: espejo. La deriva misma no es significativa (t=1,35).
4. **La sección transversal solo paga si la pregunta es relativa.** Medido sobre
   238 perpetuos × 2.456 fechas: la correlación media entre pares de retornos
   crudos es **+0,422** —PC1 explica el 48,4%— y 238 símbolos equivalen a
   **2,4 independientes**. Demediando por fecha cae a −0,007 y quedan 238.

De ahí el piso económico, que es el contraste que ordena la fase: **AUC 0,5047
contra el 0,63 de 15m**, porque el oráculo del spread decil-contra-decil es
**97,5× el costo del par**.

**El criterio se pre-registró antes de correr nada**
(`docs/PREREGISTRO_PANEL_DIARIO.md`, commit `605e1f7`): 3 criterios
confirmatorios, 18 exploratorias declaradas, configuración primaria fijada.

**Resultado: los TRES criterios pasan.** 659 perpetuos cripto × 2.571 fechas,
284.763 filas evaluadas, 111 features, H=5, 6 folds purgados por fecha:

| criterio | resultado |
|---|---|
| 1. discrimina | AUC **0,5541** > 0,55, BSS **+0,0088** > 0, calibración 0,72 pp |
| 2. supera al trivial | IC **+0,1072** vs +0,0331 (reversal) y −0,0334 (momentum); diferencia pareada **+0,0741**, IC95% [+0,0589, +0,0903] |
| 3. paga | spread neto **+1,390%** por período de 5 días, IC95% [+0,984%, +1,817%] |

**El placebo está limpio, y era la primera pregunta.** Permutando el resultado
entre los símbolos de cada fecha —rompe el vínculo símbolo→resultado y deja
todo lo demás intacto— el AUC cae a **0,5007**, el IC a **+0,0021** (IC95%
contiene 0) y el spread neto a **−0,046%**. La plomería no fabrica edge. Con el
piso económico en 0,5047 y la resolución en ±0,004 este control no es opcional:
un sesgo chico produce exactamente el tamaño de efecto que se busca.

**Estable en los cinco años**, ninguno sostiene al resto:

| año | IC | spread neto | IC95% |
|---|---|---|---|
| 2022 | +0,0986 | +0,879% | [+0,017%, +1,800%] |
| 2023 | +0,0957 | +0,827% | [+0,206%, +1,493%] |
| 2024 | +0,1037 | +0,888% | [+0,042%, +1,687%] |
| 2025 | +0,1146 | +1,505% | [+0,660%, +2,318%] |
| 2026 | +0,1282 | +3,347% | [+1,914%, +4,870%] |

**Tres hallazgos con consecuencia, y el tercero es el que manda:**

**(a) El bloque transversal casi no aporta.** Sin él —el baseline secundario
declarado— el IC es **+0,0959** contra +0,1072, y el spread neto sale
*levemente mejor* (+1,424% vs +1,390%). O sea: lo que funciona es la reversión
de **serie de tiempo** por símbolo, *medida* contra la sección. Los rangos
transversales como feature agregan poco. La hipótesis H1 pasa, pero el
mecanismo no es el que la motivó, y sin el bloque el AUC cae a 0,5455 — abajo
del gate.

**(b) Solo se captura el 5,47% del techo.** El oráculo da +30,56% y el modelo
+1,67%. Es coherente con un IC de 0,107 y conviene tenerlo a la vista: no hay
nada parecido a "adivinar" cuál sube.

**(c) La cola izquierda es catastrófica, y ese es el veredicto real para ESTE
usuario.** Asimetría **−1,04**, curtosis **20,3**:

| | |
|---|---|
| peor período | **−68,8%** (el siguiente, −32,2%) |
| mejor período | +28,6% |
| p1 / p99 | −11,8% / +16,2% |
| períodos con neto > 0 | 60,5% |
| media sin los 5 peores | +1,485% (vs +1,390%) |

El edge **no vive en unos pocos períodos** —quitar los cinco peores casi no
mueve la media— pero un par largo/corto que pierde 68,8% en cinco días liquida
cualquier cuenta apalancada. Para un usuario averso al riesgo que **ya fue
liquidado una vez**, ese número pesa más que el Sharpe de 2,16. La firma es la
de un short squeeze en la pata corta, que es el riesgo clásico de estas
estrategias.

**Las exploratorias declaradas: las 18 cerradas (2026-09-24).** Se corren con
`panel_runner` y se tabulan con `uv run python -m bob.backtest.panel_compare`,
que las pone al lado de la primaria **sin elegir ninguna**: con 18 pruebas, que
alguna gane por azar es lo esperable, así que una exploratoria que supere a la
primaria NO la reemplaza. Sirven para lo contrario — para falsificar.

| variante | filas | AUC | IC | neto | veredicto |
|---|---|---|---|---|---|
| **PRIMARIA** H=5, mediana | 284.763 | 0,5541 | +0,1072 | +1,390% | ✅ |
| H=3 | 285.058 | 0,5536 | +0,1061 | +0,881% | ✅ |
| H=10 | 284.018 | 0,5596 | +0,1192 | +2,907% | ✅ |
| quintiles q=0,20 | 284.763 | 0,5541 | +0,1072 | +1,016% | ✅ |
| ventiles q=0,05 | 284.763 | 0,5541 | +0,1072 | +1,833% | ✅ |
| liquidez >$1M | 398.756 | 0,5525 | +0,1048 | +1,463% | ✅ |
| liquidez >$20M | 149.713 | 0,5555 | +0,1107 | +1,622% | ✅ |
| **sin filtro de liquidez** | 465.237 | **0,5498** | +0,1037 | +1,550% | ❌ |
| sin deslistados | 276.721 | 0,5549 | +0,1088 | +1,561% | ✅ |
| pesos por unicidad | 284.763 | 0,5533 | +0,1063 | +1,584% | ✅ |
| XGBoost como estimador | 284.763 | 0,5540 | +0,1087 | +1,473% | ✅ |
| **target por tercil** ⚠ | 190.044 | *0,5737* | *+0,1246* | *+2,136%* | ⚠ no comparable |
| **sin bloque transversal** | 284.763 | **0,5455** | +0,0959 | +1,424% | ❌ |
| *placebo (control)* | 284.763 | *0,5007* | *+0,0021* | *−0,046%* | ✅ falla como debe |

**11 de 13 sostienen los tres criterios**, más los seis sub-períodos anuales.
Las dos que rompen lo hacen **las dos por el criterio 1**, y las dos dicen algo:

**(a) Sin filtro de liquidez el AUC cae a 0,5498 — y la causa está declarada
de antemano.** El §6.3 del pre-registro anotó el precio rancio como trampa
conocida; medido, sin filtro el **10,5% de las filas tiene retorno exactamente
cero** contra 0,54% en la primaria. El filtro de $5M no era decorativo: es lo
que saca de la sección los símbolos cuyo precio no se mueve y que fabrican
reversión de la nada. Nótese que el criterio económico **sí** pasa (+1,550%),
que es justamente lo que haría un spread inflado por precios que nadie pudo
operar.

**(b) Y al revés: subir el corte a $20M MEJORA todo.** Con la mitad de las
filas y solo símbolos líquidos, el IC sube a +0,1107 y el neto a +1,622%. Eso
contesta el caveat más serio que la fase se había puesto a sí misma —"el costo
está calibrado sobre ETH, no sobre el decil ilíquido"—: **el edge no vive en
los microcaps**. No lo cierra (sigue faltando modelar el spread real de cada
símbolo) pero mueve la sospecha en la dirección buena.

**El sesgo de supervivencia, medido en vez de asumido.** Excluir los
deslistados —**8.042 filas, el 2,8% de la muestra**— infla el spread neto de
+1,390% a **+1,561%**, o sea un **12% relativo**, con AUC +0,0008 e IC +0,0015.
Ese es el apalancamiento del sesgo: lo que se muere está concentrado en la cola
mala, así que sacarlo rinde mucho más de lo que pesa. Un backtest armado con el
`exchangeInfo` de hoy reportaría +1,56% donde la verdad es +1,39%.

**Dos invariantes que salieron de control gratis:**

- **`q=0,20` y `q=0,05` comparten AUC, BSS e IC exactos con la primaria**
  (0,5541 / +0,00883 / +0,10722). Tienen que compartirlos: el corte de cartera
  toca la construcción del par, no el modelo. Si hubieran diferido, el corte
  estaría filtrándose al entrenamiento.
- **Los pesos por unicidad no mueven nada** (AUC −0,0008, IC −0,0010), y eso
  estaba predicho *por construcción*: con horizonte fijo la concurrencia es H
  en casi todas las fechas, el peso sale casi uniforme, y un peso uniforme no
  cambia un boosting. Hay un test que fija esa propiedad para que un resultado
  nulo se lea como "no podía aportar" y no como "no aporta".

**Bug encontrado al implementarlo**, y de los que no fallan: la primera versión
de `panel_uniqueness_weights` le daba peso **0** a la última fila de cada
símbolo. La ventana de ocupación de ese label cae más allá del final de la
serie y `uniqueness_weights` la recorta a longitud cero. Una fila legítima por
símbolo —659 filas— habría desaparecido del entrenamiento sin que nada se
quejara. Lo cazaron dos tests, no la lectura del código.

**XGBoost: empate, igual que en la Fase 4-bis.** AUC 0,5540 contra 0,5541 del
GBM, IC +0,1087 contra +0,1072, neto +1,473% contra +1,390%. Con los mismos
hiperparámetros nominales, dos boostings competentes caen dentro del ruido. La
ventaja sobre el ranking trivial viene de las features y del diseño del target,
no de la librería — el mismo hallazgo que el target de volatilidad ya había
dado, ahora replicado en otro problema. El default sigue en `gbm`.

Caveat que corresponde declarar y que el regresor no tenía:
`min_child_weight` es la suma de hessianos de la hoja, y con pérdida logística
el hessiano es `p(1−p) ≤ 0,25`, no 1. O sea 100 **no** equivale a
`min_samples_leaf=100` de sklearn. La comparación es "mismos hiperparámetros
nominales", no "misma capacidad".

**El target por tercil sale mejor en TODAS las métricas, y es un artefacto.**
AUC 0,5737, BSS +0,0163, IC +0,1246, neto +2,136% — la mejor fila de la tabla.
No se adopta, y ni siquiera se compara: **evalúa sobre el 67,1% de la sección,
y cuál 67% lo decide el retorno futuro**. El tercio del medio se descarta *por
dónde terminó*, así que el spread del par decil se calcula eligiendo el top 10%
entre símbolos que ya se sabe que se movieron a un extremo. Al decidir no se
sabe cuáles van a quedar en el medio: **ese spread no es alcanzable**.

Sus AUC/BSS/IC contestan una pregunta condicional legítima —"entre los que van
a moverse a un extremo, ¿cuál es cuál?"— pero suben porque el problema quedó
más fácil, no porque el modelo sea mejor. `panel_compare` marca la fila con ⚠ y
lo dice en el pie; sin esa marca es exactamente la fila que alguien leería como
"acá está el producto". Hay un test que fija la marca.

**Corrección de registro (24-09).** El cierre del 23-09 decía "16 de 18, la
tanda se cortó por memoria y faltan XGBoost y el tercil". Era falso: **las 11
corridas habían terminado**. La cronología, por marca de tiempo de los
archivos:

| | |
|---|---|
| 14:45:44 | se escribe el artefacto de **XGBoost** |
| 14:47:05 | commit `f5023d4` — lo arrastra sin que nadie lo notara |
| 14:47:33 | se escribe el del **tercil**, 28 s después del commit |

El proceso de la tanda sí lo mató el sistema por presión de memoria, pero
**después** de la última corrida. Lo que falló fue la lectura: se corrió el
comparador antes de que esas dos terminaran y se reportó su ausencia como
definitiva, en vez de volver a mirar el directorio antes de escribir el
veredicto. Un artefacto en disco es el estado; la salida de un comparador
corrido antes, no.

**El problema de memoria era real igual, y se arregló.**
`assemble_panel_features` armaba los tres bloques como arrays sueltos y los
concatenaba, lo que en el pico sostiene cada número **dos veces**: ~3 GB en vez
de ~1,5 sobre el panel real, en una máquina de 7 GB con 0,76 GB libres. Ahora
la matriz se preasigna y se llena en su lugar. **El control es que las dos
corridas rehechas reproducen bit a bit sus artefactos del día anterior** —las
1.667 series por fecha incluidas— atravesando el cambio, que es la prueba de
que la refactorización es neutra sobre el panel completo y no solo en un
fixture.

**Lo que la Fase 10 NO hace.**

- **No habilita el KPI 1.** Sigue sin pasar el gate y sigue en gris (regla 2).
  Esto es otra pregunta, sobre otro objeto, a otra escala.
- **No es una estrategia lista para operar.** Se opera **neutral al mercado**
  —largo y corto a la vez, doble fricción, rotación medida del **44,4%** por
  rebalanceo— que es una forma de operar distinta de todo lo que BOB asumió y
  de cómo opera Nichelson. Y el costo modelado (0,14% por pata) está calibrado
  sobre ETH, no sobre el decil ilíquido de una sección de 170 altcoins.
- **No mide forward.** Es offline. La Fase 5 enseñó que offline y forward no
  son lo mismo; la validación forward es una fase aparte.
- **No toca el vivo.** No cambia `model_version` ni entra al analista.

**El sesgo de supervivencia, corregido y medido.** El universo se reconstruye
del archivo de `data.binance.vision` y no de `exchangeInfo`, que es solo-vivo:
864 símbolos USDT, **179 perpetuos de acciones excluidos por bandera del
exchange** (`TRADIFI_PERPETUAL`) y **31 deslistados recuperados**, entre ellos
**`LUNAUSDT`** —el colapso de Terra— con sus 471 velas.

**Y el sesgo se colaba una capa más abajo de donde se lo vigilaba.** Con el
universo ya bien construido, la ingesta por REST falló en 4 símbolos con
`-1121 Invalid symbol`: **Binance no sirve klines de contratos deslistados**.
Uno de ellos, `SXPUSDT`, tiene seis años de historia. Se agregó el fallback al
archivo mensual (`VisionClient.fetch_month_klines`) y los cuatro entraron.
Tener bien la regla no alcanza si la capa que la materializa no puede.

**El feature engine degeneraba en diario, en silencio.** `window_bars` tiene
piso de 2 barras, así que 1h, 4h, 12h y 24h caían **todas** a 2: medido sobre
las 2.491 velas diarias reales, 55 features colapsaban a **34 distintas** —25
pares duplicados **exactos** y 4 constantes— y el modelo entrenaba igual
reportando métricas. `WindowSet` separa el perfil de intradía del diario; el de
intradía **reproduce la matriz de 15m byte a byte sobre 71.344 velas × 55
features**, verificado contra el código anterior sacado de git, así que los
artefactos del gate siguen siendo reproducibles.

Se reimprime con:

    uv run python -m bob.data.download_panel --discover   # congela el universo
    uv run python -m bob.data.download_panel --ingest
    uv run python -m bob.backtest.panel_runner --until 2026-09-21
    uv run python -m bob.backtest.panel_runner --until 2026-09-21 --placebo
    uv run python -m bob.backtest.panel_compare   # primaria vs. exploratorias
    uv run python scripts/diagnose_panel.py       # sub-períodos, cola, oráculo

Artefactos versionados en `backend/artifacts/panel-*.txt|.json` y
`universe-1d.json`. Runtime: ingesta ~9 min (piso por rate limit), corrida
~2 min.

### Fase 10-bis — Validación forward del panel 🟡 EN CURSO (arrancada 2026-09-24)

La Fase 10 pasó sus tres criterios **offline**. La Fase 5 dejó escrito que
offline y forward no son lo mismo, así que esto mide si el ordenamiento
sobrevive cuando los pronósticos se emiten **antes de que el resultado
exista**.

**Criterio pre-registrado antes de emitir nada**
(`docs/PREREGISTRO_PANEL_FORWARD.md`, commit `99532e8`).

> **Enmienda 1 (2026-10-05): diseño secuencial**
> (`docs/ENMIENDA_PANEL_FORWARD_SECUENCIAL.md`), commiteada sin que nadie
> hubiera mirado el IC forward. Análisis interino a las **90 fechas válidas**:
> confirma si z ≥ 2,967, cierra por futilidad si IC ≤ 0. Final a las 180 con
> z ≥ 1,968. Mismo α (0,025 a una cola), −0,4 pp de potencia; fechas
> esperadas 135 bajo el nulo y 98 con el efecto entero. **Nadie mira el IC
> antes de las 90 fechas**: el interino se implementa en `marea/`, solo
> lectura, sin tocar `panel_forward.py` (regla 12). El ritmo de fechas
> válidas era de 33% al enmendar: el portátil duerme a las 21:05 y los
> temporizadores de reactivación están desactivados.

**La medición de potencia se hizo ANTES de fijar el criterio, y decide la
forma entera de la fase.** Sobre las 1.667 fechas del artefacto primario,
por fecha:

| serie | media | desvío | t por fecha | días para excluir 0 |
|---|---|---|---|---|
| IC de rango | +0,1072 | 0,1495 | **0,7173** | **37** |
| spread neto | +1,390% | 5,512% | **0,2522** | **302** |

**El IC se puede confirmar; la economía no.** El spread pediría 302 días a
efecto completo y **1.208 —3,3 años— si cae a la mitad**, que es el desenlace
más probable. Así que **el criterio económico de la Fase 10 no se traslada**:
el spread se reporta siempre con su IC y no puede aprobar ni reprobar nada.
Una métrica sin potencia no se vuelve veredicto por reportarla con más
decimales.

**Corte primario: 180 días** (decisión de Nichelson entre 45/90/180). Detecta
un IC de 0,049 — el 46% del offline — y es el único corte que separa "se
sostuvo a la mitad" de "se evaporó":

| días | bloques | IC mínimo detectable | % del offline |
|---|---|---|---|
| 45 | 9 | 0,098 | 91% |
| 90 | 18 | 0,069 | 64% |
| **180** | **36** | **0,049** | **46%** |
| 365 | 73 | 0,034 | 32% |

Las miradas intermedias quedan **declaradas descriptivas y sin veredicto**, y
el reporte se niega a emitir uno antes del corte — a 45 días el número va a
ser ruidoso y va a tentar.

**El pareado NO sirve acá, y la intuición era razonable.** Restarle al IC del
modelo el del ranking trivial —como hace la Fase 5-bis con EWMA— **empeora**
la potencia: t cae de 0,717 a 0,378. La causa es la correlación por fecha,
**+0,217** y **−0,171**: con `var(A−B) = var(A) + var(B) − 2·cov`, covarianza
casi nula y un baseline tan disperso como el modelo, restar **agrega**
varianza. El pareado ayuda cuando las series comparten el ruido; estas no.
Queda escrito porque el proyecto usa pareado en todos lados: **no es una regla
universal, es una propiedad de las series concretas.**

**La trampa específica: todo es recuperable.** El panel usa solo klines y
Binance las sirve para siempre, así que no hace falta proceso vivo —alcanza un
comando diario idempotente— y **no hay ventana de 30 días que se pierda**,
que era la restricción más cara de la Fase 5. Pero si cualquier fecha se
reconstruye después, **una corrida que se ponga al día es indistinguible de un
backtest**. Lo único que separa esta validación de un backtest es *cuándo* se
computó cada pronóstico.

> **Regla que sostiene la fase:** una fila entra a la muestra confirmatoria
> solo si se computó dentro de la gracia posterior al cierre de su barra de
> decisión. Lo demás se persiste igual, marcado `is_backfill`, y queda fuera
> del IC que decide.

**Corrección del propio pre-registro, hecha antes de emitir un solo dato.**
§5 decía "antes del cierre de la barra `t+1`", o sea 24 h. Está mal: la
entrada es al **open** de `t+1`, así que computar 20 h después significa
conocer ya el 80% del primer día del horizonte — lookahead parcial sobre uno
de los cinco días que se miden. `FORWARD_GRACE_MS` son **2 horas**, que deja
la exposición bajo el 2% del horizonte y sigue siendo operable con una corrida
diaria. Es un endurecimiento, no un aflojamiento, y `emitted_at` se guarda
exacto: quien quiera leer el resultado con la regla original de 24 h puede
hacerlo sin volver a correr nada.

**Lo construido:**

1. ✅ `db/models.py` → `PanelForecastRecord`: una fila por **(fecha, símbolo)**.
   Guardar solo los deciles bastaría para el spread y **no** para el IC, que
   es justamente la métrica confirmatoria. Con el vector completo (regla 10)
2. ✅ `models/panel_production.py`: **un** ajuste consultable por fecha, el
   papel que `production.fit_bundle` cumple para el vivo de 15m. Las últimas
   `H` fechas quedan fuera del train solas — su etiqueta no terminó de ocurrir
3. ✅ `paper/panel_forward.py`: emisión diaria idempotente, resolución a los
   H días contra **la mediana de su propia fecha**, y reporte con IC por
   bloques + artefacto
4. ✅ 14 tests. El que decide —que el backfill no entre al IC confirmatorio—
   **falla contra una implementación que lo deja entrar**, verificado en las
   dos direcciones. Más control negativo (ruido → no confirma) y positivo
   (señal inyectada → confirma)

**Operación: está PROGRAMADA, no se corre a mano** (2026-09-26).

La restricción operativa sale de la regla de gracia: para que una emisión
cuente como forward, el comando tiene que correr dentro de las **2 h
posteriores a las 00:00 UTC**. Corrido a mano cuando uno se acuerda, la
mayoría de los días salen `backfill` y la validación no avanza — lo que pasó
exactamente con la primera emisión, que se hizo 13,7 h tarde.

Tarea de Windows **`BOB_panel_forward_diario`**, todos los días a las **21:05
locales = 00:05 UTC**, corriendo `backend/scripts/panel_daily.ps1`:

    uv run python -m bob.data.download_panel --ingest --concurrency 4   # ~12 min
    uv run python -m bob.paper.panel_forward --emit                     # ~1 min

**Los tres flags que en NODO dejaron una ventana de scraping vacía reportando
éxito** están desactivados de entrada: `DisallowStartIfOnBatteries` y
`StopIfGoingOnBatteries` vienen en `True` por defecto, y en un portátil eso es
**cero corridas con batería mientras la tarea informa `Last Result: 0`**. Van
`-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
-WakeToRun`.

**El log va en UTC a propósito** (`backend/logs/panel_daily.log`). La Fase 5
perdió un análisis entero por atribuir barras con un log en hora local
atravesando el cambio de horario del 06-09: un offset fijo de +3 h reasignaba
tramos completos entre "proceso abajo" y "analista mudo", y daba 84/28 en vez
de 90/22. Con UTC no hay nada que convertir. Cada corrida escribe si la
emisión **cuenta como forward o salió backfill**, que es el dato que decide si
el día entra a la muestra.

**Por qué 21:05 y no otra hora.** La vela diaria cierra a las 00:00 UTC, así
que antes de eso no hay nada nuevo que predecir. Con Chile en UTC-3 las 21:05
locales son las 00:05 UTC — cinco minutos después del cierre, con ~1 h 45 min
de margen sobre la gracia. **Cuando termine el horario de verano** el offset
pasa a UTC-4 y esas mismas 21:05 serán 01:05 UTC: sigue después del cierre y
sigue dentro de la gracia, con 55 min de margen. No hay que reprogramar nada,
y queda escrito para que nadie lo "arregle" mirando el reloj equivocado.

Para mirar el estado, cuando se quiera (sin veredicto antes del corte):

    uv run python -m bob.paper.panel_forward --report

**Lo que esta fase NO pretende.** No habilita el KPI 1. No vuelve operable el
panel ni aunque apruebe —se opera neutral al mercado, con 44,4% de rotación y
una cola izquierda que perdió **−68,8%** en su peor período—. No mide la
economía, por falta de potencia. Y no toca el vivo de 15m: no cambia
`model_version` del analista.

### Frente intradía — reglas técnicas y calculadora de riesgo (2026-10-05)

Nichelson pidió un asistente para operar ETH **ya**, con ~500 USD, usando
análisis técnico. Es un frente del módulo cripto, a horizonte intradía, y
no se mezcla con la fase M en curso.

1. ✅ **Reglas técnicas medidas, y no pasan.** Pre-registro
   `docs/PREREGISTRO_TECNICO.md` (commit `07a71ff`, antes de correr).
   Barrido de liquidez y order block, en {15m, 1h}, objetivo 2R, costo
   0,14%: las **cuatro** confirmatorias tienen expectancy neta negativa
   (−0,15 a −0,35 R por trade) e **indistinguible del placebo** que entra
   al azar con la misma geometría. Ninguna de las 26 exploratorias sale
   positiva. Lo que mata a 15m es el costo: con el stop típico a 0,48%,
   la fricción solo ya son ~0,3 R por trade. Artefacto:
   `backend/artifacts/technical-ETHUSDT-20261005T131229Z.*`. El detector
   causal vive en `signals/technical.py` y es el mismo que usaría el
   mapa del panel.
2. ✅ **Calculadora de riesgo** (`models/sizing.py`, `/api/risk/sizing`,
   página **Riesgo**). Va de la cuenta y el % arriesgado, al stop, y de
   ahí al tamaño. La pérdida al stop incluye los costos, y la cantidad se
   redondea hacia abajo. Calcula margen, liquidación aislada con la misma
   fórmula de `projection.py`, la razón liquidación/stop, el leverage
   mínimo y el prudente, el R neto, el % de aciertos para empatar y las
   rachas de pérdidas. Avisa cuando la liquidación llega antes que el
   stop, cuando el margen no cabe, cuando el costo pesa demasiado o
   cuando el riesgo pasa de 2%. Funciona aunque el analista no esté
   emitiendo. Si el último precio de la DB tiene más de una hora, lo
   marca como viejo y no lo usa de entrada.
3. ✅ **Mapa técnico** (`signals/technical.technical_map`,
   `/api/analysis/technical`, página **Mapa**) en 15m, 1h y 4h. El 1h y
   el 4h se arman agregando 15m con la misma función del runner. Dibuja:
   - zonas de liquidez, fundiendo máximos o mínimos iguales;
   - order blocks vivos;
   - soportes y resistencias por pivotes agrupados;
   - barridos de la ventana;
   - EMA 20/50/200, y el RSI en un gráfico aparte.

   Usa el **mismo `scan`** que el backtest (hay un test que lo fija), así
   que lo dibujado es lo que se midió. Encima del gráfico va la evidencia
   del artefacto, leída del `.json` y no escrita en el TSX: veredicto,
   expectancy con IC, aciertos contra empate y placebo. Las zonas van en
   gris y los barridos como puntos, no como flechas (§7 del
   pre-registro). El único uso que se ofrece es ubicar el stop: cada
   nivel lleva "stop aquí", que abre la calculadora con el stop a 0,1·ATR
   detrás del nivel.

4. ✅ **El filtro de régimen entra a la calculadora** (05-10). Dos hechos
   que el registro no decía:
   - **El filtro nunca había corrido en vivo.** Se construyó el 14-09 con
     la corrida forward ya cerrada: 0 de 1.174 `ForecastRecord` tenían
     `regime_json`. La **primera emisión en vivo** es la de la barra 05-10 14:45 UTC
     (ETH 2.703, sigma 4 h 0,89%, percentil 33, normal), tras
     reingestar tres semanas de velas con `download --repair` y de
     `metrics` y funding con `download_vision --days 30`. Los derivados del
     apagón no se perdieron: los cubre el archivo diario.
   - **Su alcance validado es estrecho**: liquidación a 20x sin stop. A 5x
     no hubo un solo evento en dos años, y a 10x salió sin potencia.
     Operando con stop y leverage bajo, lo que transmite es el **orden** de
     la volatilidad: viene movimiento grande, el mismo stop salta más
     seguido y la sigma subestima la cola. `RegimeFilter.tsx` lo dice ahora
     en las dos páginas.

   La página Riesgo muestra el estado del filtro solo si el pronóstico está
   fresco, y le pasa al cálculo la sigma del horizonte. Con eso agrega el
   stop en σ y la P de que el ruido toque el stop y el objetivo
   (`prob_touch`, aproximación declarada). Avisa si el stop queda dentro
   del ruido (P > 60%). Con régimen elevado avisa en ámbar, y en rojo
   desde 20x, que es donde se validó. **Pendiente:** validación forward
   del filtro. Con ~89 eventos independientes en dos años, medir la
   liquidación a 20x en forward no tiene potencia en plazos razonables; si
   se hace, la pregunta tiene que ser otra y pre-registrada.

5. ✅ **Lectura del mercado en lenguaje natural** (`models/narrative.py`,
   `/api/analysis/narrative`, arriba de la página Signal). Se arma con
   plantillas, sin LLM. Cada sección declara su respaldo: validado,
   descriptivo, sin ventaja medida o alcance. Un test prohíbe frases que
   pronostiquen dirección. Al leer el primer texto apareció un bug de
   `cluster_levels`: encadenaba pivotes en escalera y producía
   "resistencias" de 37 toques. Corregido, con un test que falla contra el
   código previo.
6. ✅ **El filtro leído con stop** (`docs/PREREGISTRO_FILTRO_STOP.md`,
   commit `015c9eb`, antes de correr). Un stop a `d` es una liquidación a
   otra distancia, así que se reutilizó la maquinaria de la 5-bis, con un
   control que reproduce su lift a 20x (3,077). Resultados:
   - **C1 pasa.** Con stop a 1%, el stop salta **×2,03** en régimen
     elevado (55,9% contra 27,5% de los setups en 4 h). Lift 1,70 [1,65;
     1,75], mejor que EWMA.
   - **C2: la `P(toque)` de la calculadora está calibrada.** La brecha es
     −0,8 / −1,3 pp, o sea que sobreestima levemente. La advertencia
     "subestima, más en régimen elevado" era falsa y se sacó de la UI, de
     `sizing.py`, de `narrative.py` y del docstring de `prob_touch`. La cola
     extrema de la Fase 5 sigue siendo cierta, pero a otra escala.
   - **La expectancy no cambia con el régimen** (−0,14 R en los dos): el
     filtro cambia la frecuencia de los stops, y con ella las rachas, no la
     ganancia esperada.

   `RegimeFilter` y la calculadora citan esto desde
   `/api/backtest/stopfilter`, no desde el TSX.

### Fase 7 — Alertas Telegram + sentimiento (F&G, CoinGecko)
### Fase 8 — Outliers Club + multi-símbolo watchlist

> **Outliers no se automatiza (2026-10-07).** Sus Términos prohíben bots,
> scraping e ingeniería inversa (§23, §27). Queda como fuente de consulta
> manual; las métricas útiles de su catálogo se traen de la fuente primaria
> en la fase M5. Ver `docs/DATA_SOURCES.md`.
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

Desde el 26-09 hay dos frentes y no se mezclan: la **fase M en curso** de
MAREA (tabla al inicio) y la **corrida forward del panel**, que avanza sola por
tarea programada. Desde el 07-10 hay una segunda tarea,
`MAREA_snapshot_diario` (12:00 locales, log en `backend/logs/marea_diario.log`):
guarda lo que no se recupera después (opciones de Deribit, vintages de
stablecoins). Para mirar esta última sin tocarla: `/cripto`.
