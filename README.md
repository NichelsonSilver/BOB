# BOB — Bot Operador Bursátil

Asistente de decisión para **trading intradía de futuros perpetuos** en
Binance. BOB observa el mercado en vivo, computa un estado estadístico
avanzado y le dice al usuario, con honestidad probabilística, qué escenario
es el más seguro y cuál es el riesgo. **BOB nunca ejecuta órdenes.**

Par inicial: `ETHUSDT` perp. El modelo es agnóstico del símbolo (watchlist
configurable).

> El build anterior (grid trading bot para GRVT) vive congelado en el branch
> `legacy/grvt-grid`.

---

## ⚠️ Veredicto del gate: qué está validado y qué no

Este proyecto tiene un **gate de dos criterios** que decide si un KPI
probabilístico puede mostrarse como operable. El resultado, medido y
reproducible, es:

| Target | Gate | Estado |
|---|---|---|
| **Dirección** — `P(TP antes que SL)` | Calibra (4,0pp / 5,1pp) pero **NO discrimina** (AUC 0,519 / 0,533; BSS −0,0028 / +0,0005) | ❌ **NO habilitado.** Se muestra en gris, etiquetado "experimental". No se emiten señales direccionales. |
| **Volatilidad realizada futura** | R² OOS **+0,400** vs media y **+0,374** vs EWMA; Diebold-Mariano contra EWMA y HAR-RV con **p < 0,0001** | ✅ **Validado.** Es lo que sostiene el producto. |
| **Cono de precio** (CQR + ACI) | Cobertura empírica 94,8% al 95% nominal (desvío −0,2pp) y 79,9% al 80% (−0,1pp) | ✅ **Validado.** |

**Los dos criterios del gate son obligatorios, no alternativos:**

1. **Calibración** — error medio < 10pp por bucket. Dice *"cuando digo 70%,
   acierto 70%"*.
2. **Discriminación** — AUC > 0,55 **y** Brier Skill Score > 0. Dice *"sé
   cuáles son los casos de 70%"*.

Un modelo que predice siempre la tasa base está **perfectamente calibrado por
construcción** y es inútil. Por eso pasar solo el primero no habilita nada.
El de dirección pasa el primero y falla el segundo: **calibra y no
discrimina.** No es un bug — es el hallazgo, y es el que predice la hipótesis
de mercado eficiente en forma débil para este horizonte.

### La consecuencia de producto

Por decisión explícita del 2026-08-25, **BOB se construye sobre el target de
volatilidad**, no sobre el de dirección. Lo que entrega:

- TP y SL **dimensionados por la sigma pronosticada**, no por un número fijo
- **Precio de liquidación** y distancia a liquidación en sigmas, por leverage
- **Leverage máximo seguro**
- Cono de precio con cobertura verificada
- EV neto de costos, mostrado **siempre junto a su probabilidad de
  equilibrio** — como el listón a superar, no como una promesa

Hay un resultado adicional que conviene conocer antes de esperar EV positivo:
para un camino sin deriva con barreras a `+a` y `−b`, la probabilidad de tocar
arriba primero es `b/(a+b)`, y entonces el EV bruto es **exactamente 0 para
todo a y b** — el neto es `−costo`. Mover el TP o cambiar el ratio R:B no lo
levanta: reordena probabilidad y pago en la proporción exacta que deja el
bruto en cero. **Sin edge direccional no hay EV positivo, y eso es álgebra,
no mercado.** Deducción completa en `docs/PROBABILITY_MODEL.md` §9-ter.3.

### Cómo verificarlo tú mismo

Los reportes de todas las corridas están **versionados en el repo**, no
resumidos a mano. El bloque `GATE DE LA FASE 4` está al final de cada uno:

```bash
cat backend/artifacts/ETHUSDT-15m-price-20260825150516.txt
cat backend/artifacts/ETHUSDT-15m-price+deriv-20260825151728.txt
cat backend/artifacts/ETHUSDT-15m-full-20260825153235.txt

# El comparador de variantes, que importa los umbrales del gate del
# propio ExperimentResult en vez de copiarlos
cd backend && uv run python -m bob.backtest.compare
```

Y para reproducir desde cero (descarga de datos + experimento). Toda la
aleatoriedad del run pasa por un único `seed` en `ExperimentConfig`, fijo en
42, así que dos corridas con la misma configuración dan reportes **idénticos
línea por línea** salvo el runtime y el identificador:

```bash
cd backend
uv run python -m bob.data.download --symbol ETHUSDT --timeframe 15m --months 24
uv run python -m bob.data.download_vision --symbol ETHUSDT --timeframe 15m --days 730
uv run python -m bob.backtest.runner --symbol ETHUSDT --timeframe 15m --folds 6 --features price
```

Verificado, no solo declarado: el run `price` del 2026-08-25 reproduce el del
2026-08-24 **bit a bit** (AUC 0.518701 / 0.532680, BSS −0.002801 / +0.000498)
pese al refactor intermedio.

### La ablación que refutó la hipótesis de trabajo

La Fase 2b se hizo bajo la premisa de que el gate no pasaba discriminación
*por falta de datos de derivados y microestructura*. Se consiguieron 730/730
días de ambos. El resultado, con la misma semilla y los mismos folds:

| variante | features | AUC long | AUC short | BSS long | BSS short | veredicto |
|---|---|---|---|---|---|---|
| `price` | 55 | 0,519 | 0,533 | −0,0028 | +0,0005 | ✗ no habilitado |
| `price+deriv` | 81 | 0,512 | 0,517 | −0,0035 | −0,0025 | ✗ no habilitado |
| `full` | 96 | 0,509 | 0,515 | −0,0049 | −0,0018 | ✗ no habilitado |

**Las familias nuevas empeoran la discriminación**, de forma monótona con el
número de features y en las dos direcciones a la vez. La premisa era falsa y
queda escrito que lo era. Detalle revelador: en `price+deriv` la familia
`derivados` marca 0.00151 de importancia por permutación (segundo lugar) —
o sea el modelo **sí** las usa — pero en `full` cae a 0.00006 y `libro` sale
negativa. **Importancia por permutación positiva ≠ ganancia fuera de muestra.**

El target de volatilidad, en cambio, se sostiene en las tres variantes
(R² vs media: 0,400 / 0,392 / 0,405).

---

## Cómo están implementados los modelos

Decisión de diseño explícita: **los baselines econométricos y el motor de
inferencia están escritos desde cero en numpy**, no importados de una
librería. El backtest y la calibración son la razón de existir del proyecto;
un baseline que no se puede auditar línea por línea no sirve para decidir si
el modelo aporta algo.

| Componente | Implementación | Dependencia |
|---|---|---|
| **GARCH(1,1)**, QMLE gaussiana | Propia — verosimilitud a mano, reescalado numérico, fallback a EWMA si no converge | numpy + `scipy.optimize.minimize` (L-BFGS-B) |
| **HAR-RV** (Corsi 2009) | Propia — OLS sobre log-volatilidad con corrección de Jensen `exp(µ+σ²/2)` | numpy (`np.linalg.lstsq`) |
| **EWMA / RiskMetrics** (λ=0,94) | Propia | numpy |
| Random walk, tasa base | Propias | numpy |
| **HMM gaussiano** — Baum-Welch, forward-backward con escalado de Rabiner, selección de n por BIC/ICL | Propia (~80 líneas de EM) | numpy; `sklearn.cluster.KMeans` **solo** para inicializar |
| **Métricas** — Brier, BSS, ECE, QLIKE, Winkler, Mincer-Zarnowitz | Propias | numpy |
| **Diebold-Mariano** con corrección Harvey-Leybourne-Newbold | Propia | `scipy.stats` solo para la t de Student |
| Triple-barrier, walk-forward purgado + embargo, pesos por unicidad | Propios | numpy |
| Conformal CQR + ACI | Propia | numpy |
| GBM, regresión logística, Ridge, isotónica, StandardScaler | **scikit-learn** | scikit-learn |

**No se usan `statsmodels` ni `arch`.** No están en `backend/pyproject.toml`
ni en el entorno. `hmmlearn` tampoco: además de no publicar wheel para el
Python de este entorno, su inferencia **no sirve como feature** — `predict` es
Viterbi sobre la secuencia completa y `predict_proba` es el posterior
suavizado, y los dos miran el futuro de cada barra. Eso es exactamente el
lookahead que el proyecto prohíbe, y el bug sería invisible: el backtest daría
métricas hermosas e irreproducibles en vivo. El filtro causal había que
escribirlo igual; lo que agregaba la librería era solo el Baum-Welch.

Tampoco se usan librerías all-in-one de trading (freqtrade, backtrader,
jesse): el backtest es propio porque el error de calibración es el número que
decide.

---

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

`GET /api/health` reporta el estado del feed **y del analista** (si está
ajustado, si está reajustando, último pronóstico emitido, cobertura del cono).
Durante una corrida larga la pregunta que importa no es "¿el backend
responde?" sino "¿el analista está emitiendo?", y son cosas distintas.

Para trabajar offline (sin tocar Binance): `BOB_LIVE_DATA=false`.

---

## Pipeline de forecasting

BOB **no pronostica el nivel del precio**. Predecir `close[t+H]` da R² ≈ 0,99
y no significa nada: el modelo aprende a copiar el último precio con un
retardo. El stack predice tres cosas que sí son predecibles:

| Target | Tipo | Alimenta | Gate |
|---|---|---|---|
| `P(TP antes que SL)` | Clasificación binaria calibrada | KPI 1 — Seguridad | ❌ no pasa |
| Volatilidad realizada futura | Regresión | Dimensionado de TP/SL, KPI 2 | ✅ pasa |
| Intervalo del retorno a H barras | Predicción conformal (CQR + ACI) | Cono de precio | ✅ pasa |

Todo se valida con **walk-forward purgado con embargo** (los labels de
triple-barrier se solapan; un K-Fold estándar filtra futuro y sale inflado),
contra baselines reales y con test de **Diebold-Mariano** para saber si la
diferencia es distinguible de la suerte.

### Datos

Ningún dato de mercado de Binance requiere API key. BOB no tiene ni necesita
credenciales de trading — es coherente con "nunca ejecuta órdenes".

| Fuente | Qué da | Persistido |
|---|---|---|
| Binance Futures WS | klines, trades, depth, markPrice — latencia <100ms | en vivo |
| Binance Futures REST | histórico de klines y funding, OI y ratios de ~30 días | 69.119 velas 15m (720 días, 0 huecos) |
| **data.binance.vision** | archivo diario estático: OI, ratios long/short, taker ratio (5m desde 2021-12) y profundidad del libro (desde 2023-01) | metrics 210.232 filas · bookDepth 70.074 filas · **730/730 días, 0 huecos** |

**104 features** en total: 55 de precio + 26 de derivados + 23 de libro. De
las de libro, 8 son *near-touch* (nivel ±0,2%) y salen NaN antes de
2026-01-15, que es cuando Binance empezó a publicar ese nivel — van con
máscara explícita, no imputadas.

Hallazgo de la Fase 1 que vale documentar: el WS de futuros mainnet **no está
bloqueado**; Binance calla streams concretos y entrega otros sobre la misma
conexión TLS (mudos: `@aggTrade`, `@kline_*`, `@markPrice`, `@ticker`; vivos:
`@trade`, `@bookTicker`, `@depth*`). El flujo taker se sostiene con `@trade`,
verificado idéntico al REST oficial (0,000% de diferencia de volumen), y
`kline`/`markPrice` se rellenan por REST en paralelo. Detalle en
`docs/DATA_SOURCES.md`.

---

## En vivo y paper tracking

```bash
cd backend
uv run uvicorn bob.main:app            # el ajuste inicial toma ~83s, en background
uv run python -m bob.paper.tracker --symbol ETHUSDT   # resuelve lo maduro e imprime cobertura
```

Cada vela cerrada produce un pronóstico (`analysis.forecast` por WS,
`ForecastRecord` en SQLite con el **vector de features completo**). El tracker
lo resuelve cuando su horizonte cierra —sigma realizada, cobertura del cono,
EV realizado— usando **las mismas funciones de métrica del gate**, para que
"forward vs backtest" compare y no traduzca.

**Las pausas del proceso son seguras.** No hacen falta 72 horas seguidas: lo
que se necesita son ~280 pronósticos resueltos, y todo lo que importa vive en
SQLite. El analista repara las series de velas y derivados al arrancar.

| | ¿sobrevive a apagar el equipo? |
|---|---|
| Pronósticos emitidos con su vector completo | ✅ están en `ForecastRecord` |
| Resultados resueltos y reporte de cobertura | ✅ se recalculan de la DB |
| Estado del ACI (`alpha_t`, cobertura acumulada) | ✅ se **deriva** de los resueltos con `replay_cone_state` |
| Velas y derivados del rango caído | ✅ el analista los repara al arrancar |
| Barras que ocurrieron con el proceso abajo | ❌ esas no se pronostican nunca |

Dos advertencias que no dependen del software: los snapshots de derivados
recuperan **~41h por request** y la ventana de Binance es de ~30 días, así que
una pausa mayor a ~41h deja un hueco de derivados **irrecuperable**. Y la
suspensión del equipo cuenta como pausa.

En vivo el default es `price+deriv`, no `full`: `bookDepth` sale del archivo
diario (~1 día de retraso) y el join es exacto por `open_time`, sin
forward-fill —rellenar sería inventar liquidez—. `assert_tail_observable`
falla nombrando las columnas en vez de dejar al analista mudo en silencio.

---

## Tests

```bash
cd backend
uv run python -m pytest      # NO usar `uv run pytest` en Windows (bug del trampoline)

# Con cobertura de las capas puras
uv run python -m pytest --cov=bob.signals --cov=bob.models --cov=bob.backtest --cov=bob.data
```

**699 tests en verde.** Cobertura ≥ 90% en `signals/`, `models/` y
`backtest/` — un bug ahí es una probabilidad falsa sobre capital apalancado.

Dos tests sostienen las invariantes que, si se rompen, corrompen todo en
silencio (porque los resultados salen **mejores**, no peores):

- `test_mutar_el_futuro_no_altera_el_pasado` — sin lookahead.
- `test_escalar_el_precio_no_cambia_los_features` — features adimensionales,
  que es lo que hace al motor agnóstico del símbolo.

---

## Estado por fase

| Fase | Qué | Estado |
|---|---|---|
| 0 | Migración del esqueleto | ✅ |
| 1 | Pipeline de datos Binance (WS + REST + store + CLI) | ✅ |
| 2 | Feature engine puro — 55 features de precio | ✅ |
| 2b | Derivados + microestructura sobre 730 días, perfiles de venue | ✅ |
| 3 | Modelos puros — HMM, triple-barrier, conformal, proyección | ✅ |
| 4 | Backtesting engine — **el gate** (corrido 2 veces) | ✅ corrido · ❌ dirección no habilitada |
| 5 | Live + paper tracking de la proyección | ✅ construida · ⏳ acumulando muestras |
| 6 | API + Dashboard | ⬜ |
| 7 | Alertas Telegram + sentimiento | ⬜ |
| 8 | Outliers Club + multi-símbolo | ⬜ |
| 9 | Validación end-to-end | ⬜ |

---

## Documentación

- `CLAUDE.md` — identidad, arquitectura, KPIs, fases y reglas del proyecto
- `docs/PROBABILITY_MODEL.md` — deducción del stack de forecasting, el gate y
  sus resultados, con las alternativas descartadas y los límites conocidos
- `docs/DATA_SOURCES.md` — endpoints de Binance/CoinGecko/etc. y sus trampas
- `docs/HANDOFF_FASE1.md` — estado al cierre de Fase 0 y gotchas de entorno
- `backend/artifacts/*.txt` — los reportes de gate, versionados

## Reglas que no se negocian

1. **BOB nunca ejecuta órdenes.** No hay código de ejecución en `main`.
2. **Ningún KPI probabilístico se muestra como operable sin calibración
   Y discriminación demostradas.** "Experimental" en gris hasta entonces.
3. **Pureza por capas**: `signals/`, `models/` y `backtest/` no hacen I/O.
4. **Sin lookahead.** Cualquier feature o label que use información futura
   respecto de su timestamp es un bug crítico, y los tests deben cazarlo.
5. **No inflar el KPI**: nada de redondear hacia arriba, suavizar drawdowns
   ni ocultar buckets malos.
