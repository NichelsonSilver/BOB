# DATA_SOURCES — Endpoints, rate limits y trampas conocidas

> Referencia para Fase 1 (Binance) y Fase 7-8 (sentimiento/Outliers).
> Verificar contra la doc oficial antes de cablear: los detalles finos
> (pesos de rate limit, ventanas de historia) cambian sin aviso.
> Doc oficial futures: https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info

## Binance USDⓈ-M Futures — REST (`https://fapi.binance.com`)

Sin API key para todo lo que BOB necesita (market data público).

| Endpoint | Qué da | Notas / trampas |
|---|---|---|
| `/fapi/v1/exchangeInfo` | símbolos, tickSize, stepSize, filtros | Cachear al boot; cuantizar SIEMPRE precios mostrados al tickSize |
| `/fapi/v1/klines` | velas históricas | `limit` máx 1500 por request. Para 12+ meses de 15m (~35k velas) paginar por `startTime`/`endTime`. El peso del request sube con `limit` |
| `/fapi/v1/premiumIndex` | mark price + funding rate actual + next funding time | |
| `/fapi/v1/fundingRate` | historial de funding (cada 8h) | máx 1000 rows/request, paginar. **NO tiene la ventana de 30 días**: verificado devolviendo datos de 2021-01-01. Es la fuente del funding histórico completo |
| `/futures/data/openInterestHist` | OI histórico (granularidad 5m mínima) | ⚠️ **Solo conserva ~30 días** — pero eso es límite del *endpoint*, no del *dato*: el mismo OI está en el archivo estático (ver abajo) desde 2021-12-01. Este endpoint queda para el tramo caliente que el archivo aún no publicó |
| `/futures/data/topLongShortPositionRatio` | ratio long/short de top traders | misma ventana ~30 días del endpoint, máx 500 rows. Histórico largo: archivo estático |
| `/futures/data/globalLongShortAccountRatio` | ratio long/short global | idem |
| `/futures/data/takerlongshortRatio` | taker buy/sell volume ratio | idem |

**Rate limit**: 2400 de peso por minuto por IP en `/fapi/*`. Con token bucket
conservador (usar el header `X-MBX-USED-WEIGHT-1M` de las responses para
autorregular) no se llega ni cerca. Los `/futures/data/*` tienen límites
propios más estrictos — espaciar esas llamadas (son para snapshots
periódicos, no para el hot path).

## Binance Data Vision — archivo histórico (`https://data.binance.vision`)

**La fuente que cierra el hueco de la Fase 2b.** Binance publica dumps diarios
estáticos, sin API key y sin peso de rate limit: es un CDN de objetos, no la
API. Todo lo verificado abajo es contra archivos reales el 2026-08-24.

Cableado en `data/vision.py` (parseo + cliente) y `data/download_vision.py`
(ingesta idempotente por día UTC).

| Dataset | Qué da | Desde | Tamaño | Grilla |
|---|---|---|---|---|
| `futures/um/daily/metrics/` | `sum_open_interest`, `sum_open_interest_value`, `count_toptrader_long_short_ratio`, `sum_toptrader_long_short_ratio`, `count_long_short_ratio`, `sum_taker_long_short_vol_ratio` | **2021-12-01** | ~12 KB/día (≈20 MB por 5 años) | 5m exacta: 288 filas/día, delta 300s, sin duplicados |
| `futures/um/daily/bookDepth/` | profundidad **acumulada** a ±0,2/1/2/3/4/5% del mid (`timestamp,percentage,depth,notional`) | **2023-01-01** | ~600 KB/día comprimido, ~2 MB descomprimido | 2880 snapshots/día (uno cada ~30s), 12 filas cada uno |
| `futures/um/daily/aggTrades/`, `trades/` | ticks | 2019-11 | cientos de MB/día | tick |
| `futures/um/monthly/klines/<sym>/<tf>/` | velas | 2020-01 | — | mensual |

### Trampas verificadas

1. **No hay agregados mensuales para `metrics` ni `bookDepth`.** Solo diarios,
   aunque `klines` sí los tiene. Bajar 720 días son 720 requests.
2. **El listado pagina de a 1000 claves.** Va contra el bucket S3 crudo
   (`https://s3-ap-northeast-1.amazonaws.com/data.binance.vision`), no contra
   `data.binance.vision`, que sirve objetos pero no índices. Sin paginar se
   pierden años en silencio. Cada `.zip` tiene su `.CHECKSUM` en el mismo
   prefijo: filtrar por extensión o el conteo de días sale al doble.
3. **El archivo del día D aparece con ~1 día de retraso** (a veces más). El
   tramo caliente lo sigue cubriendo `data/snapshots.py` por REST; las dos
   fuentes escriben la misma tabla y el upsert las reconcilia.
4. **Hay huecos reales de días.** Se reportan, nunca se rellenan — misma regla
   que los huecos de klines. **Pero un 404 y un corte de red no son lo mismo**:
   en la primera corrida de 730 días la red se cayó a mitad y la ingesta
   reportó 726 días "ausentes del archivo" que existen todos. `fetch_days`
   devuelve tres estados (ok / ausente / fallido), reintenta solo el fallido
   —un 404 es una respuesta, no un error— y el reporte los cuenta por separado
   y avisa que la corrida quedó incompleta.
5. **`bookDepth` es acumulado, no marginal**: el notional hasta 1% incluye el
   que está hasta 0,2%. Restarlo para obtener el tramo es correcto; sumarlo
   cuenta doble.
6. **El esquema de `bookDepth` cambió con el tiempo.** El nivel ±0,2% —el
   near-touch, el más informativo a horizonte corto— aparece recién alrededor
   de **2026-01-15**; los archivos anteriores traen solo ±1/2/3/4/5%
   (verificado bajando días de 2024, 2025 y enero de 2026). Exigirlo descartó
   508 días en silencio en la primera ingesta. El núcleo utilizable en toda la
   historia es **1% y 5%**; el 0,2% es opcional y sus features salen NaN en el
   tramo viejo. El listado S3 dice que el archivo existe desde 2023-01-01 y es
   cierto — lo que cambió es qué trae adentro, y el listado no lo dice.
6. **`metrics` no trae `longAccount`/`shortAccount` sueltos**, solo el ratio.
   La reconstrucción `long = r/(1+r)` es exacta (long+short=1), no una
   aproximación: por eso las filas del archivo y las del REST son
   intercambiables en la misma tabla.

### Qué NO se baja, y por qué

`aggTrades` histórico son decenas de GB para dos años, y el 80% de su valor
para features de 15m —el desbalance de flujo taker— ya viene resumido en
`sum_taker_long_short_vol_ratio` cada 5 minutos. La relación costo/beneficio
solo cambiaría si el modelo bajara a horizontes de segundos.

## Binance USDⓈ-M Futures — WebSocket (`wss://fstream.binance.com`)

Formato: `wss://fstream.binance.com/stream?streams=ethusdt@kline_15m/ethusdt@aggTrade/...`
(streams combinados; los nombres de símbolo van en minúscula).

La columna "desde acá" es el resultado medido el 2026-08-24 desde la red del
usuario; ver el hallazgo más abajo antes de asumir que un stream llega.

| Stream | Qué da | Uso en BOB | Desde acá |
|---|---|---|---|
| `<sym>@kline_<tf>` | vela en curso (update ~250ms) + flag de cierre `k.x` | chart + features al cierre de barra | ❌ mudo |
| `<sym>@aggTrade` | trades agregados con flag `m` (maker side) | volume delta / taker ratio en tiempo real | ❌ mudo |
| `<sym>@trade` | fills individuales, mismos campos + `X` (tipo de orden) | **sustituto de `@aggTrade`** — mismo flujo sin agregar | ✅ |
| `<sym>@depth20@100ms` | top 20 niveles del book cada 100ms | orderbook imbalance | ✅ |
| `<sym>@bookTicker` | mejor bid/ask con tamaños, por update | spread, presión en el tope del book | ✅ |
| `<sym>@markPrice@1s` | mark price + funding rate corriente | KPI in-live, distancia a liquidación | ❌ mudo |
| `<sym>@forceOrder` | liquidaciones | feature de microestructura (⚠️ Binance lo limita a 1 evento/seg por símbolo — es muestra, no feed completo) | ❌ mudo |

**Trampas**:
- Binance **corta cada conexión WS a las 24h** por diseño. La reconexión con
  backoff+jitter no es un edge case, es el caso normal — resuscribir todo.
- El server manda `ping` periódico; responder `pong` o desconecta (la lib
  `websockets` de Python lo hace sola con `ping_interval=None` NO — dejar el
  auto-pong por defecto activado).
- Una kline con `k.x == false` es la vela EN CURSO: para features de cierre
  de barra esperar `k.x == true`, si no, lookahead sutil en producción que el
  backtest no tiene.
- Límite de streams por conexión (~200) y de mensajes de control — para la
  watchlist v1 (pocos símbolos) irrelevante, pero multiplexar en una sola
  conexión como hacía el build GRVT.

**Cómo quedó implementado** (`data/binance_ws.py`, Fase 1):

- Endpoint combinado por URL (`/stream?streams=...`): la suscripción viaja en
  el handshake, así que "resuscribir" tras un corte es simplemente reconectar.
- **TTL propio de 23h**: la conexión se recicla antes de que Binance la corte a
  las 24h, para que el corte no caiga en medio de un cierre de vela.
- **Timeout de recepción de 60s**: con markPrice@1s activo, el silencio es
  anomalía. Un socket vivo que no manda nada es peor que uno caído — el
  dashboard mostraría un precio viejo como si fuera de ahora.
- **Piso de 100ms entre reconexiones**, además del backoff con jitter: sin él,
  un handshake que falla al instante gira en vacío y se come el event loop.
- Símbolos en minúscula: con mayúscula Binance acepta el handshake y no manda
  nada, así que el bug parece de red y no lo es.

### ⚠️ Hallazgo de campo (2026-08-24): Binance calla ciertos streams, no el host

> **Este hallazgo se diagnosticó mal el primer día y se corrigió el mismo día
> midiéndolo bien.** La versión anterior decía "el WS de futuros está mudo
> desde acá, es filtrado por IP/región del feed de derivados". Es falso: el
> host entrega con normalidad, lo que calla son streams concretos. Se deja
> escrito el error porque la conclusión equivocada casi nos cuesta la
> microestructura de Fase 2b entera.

Desde la red del usuario (Antofagasta, ISP residencial, PoP `ap-northeast-1`),
`fstream.binance.com` acepta la conexión, confirma la suscripción y entrega
**unos streams sí y otros no**:

| Entrega ✅ | Mudo ❌ |
|---|---|
| `@trade`, `@bookTicker`, `!bookTicker` | `@aggTrade`, `@kline_*` |
| `@depth`, `@depth5`, `@depth20` (todas las cadencias) | `@markPrice`, `@markPrice@1s`, `!markPrice@arr` |
| WS-API (`ws-fapi.binance.com`, request/response) | `@ticker`, `@miniTicker`, `@forceOrder` |
| COIN-M (`dstream.binance.com`), spot, testnet | |

**La medición decisiva**: sobre **una sola conexión TLS** con los 6 streams
suscritos a la vez, 90 segundos seguidos:

```
ethusdt@bookTicker      38.322 frames     ethusdt@aggTrade       0 frames
ethusdt@trade            4.169 frames     ethusdt@kline_1m       0 frames
ethusdt@depth20@100ms      852 frames     ethusdt@markPrice@1s   0 frames
```

Un middlebox de ISP no puede descartar mensajes selectivos dentro de un TLS ya
establecido. Por lo tanto **no es la red del usuario ni un bug del cliente: es
Binance a nivel de aplicación**. El corte tampoco es "mainnet vs derivados":
separa **evento crudo del motor de matching** (trades, book, mejor bid/ask →
llega) de **evento derivado o agregado** (klines, tickers, mark price, aggTrade,
liquidaciones → no llega). Eso apunta a un servicio de agregación caído para
ese PoP, no a una política — o sea que **puede volver solo**, y por eso el
cliente sigue pidiendo siempre los streams estándar.

**Lo que NO se pierde.** `@trade` es el mismo flujo taker que `@aggTrade`,
fill por fill en vez de agregado por orden agresora. Verificado contra
`/fapi/v1/aggTrades` sobre la misma ventana de 60s:

```
WS @trade   : 2090 trades    buy=456.332  sell=388.171
REST aggTrades: 496 agregados buy=456.332  sell=388.171   → 0.000% de diferencia
```

Trampa de `@trade`: ~0,6% de los frames son relleno con `p=q=0` y `X="NA"`. No
son trades. Se filtran por `p>0 && q>0` y no por `X`, porque el tamaño positivo
es una invariante de lo que es un trade mientras que `X` es un enum sin
documentar. Medido: los dos filtros seleccionan exactamente el mismo conjunto.

Lo único realmente perdido es `@forceOrder`, que igual era muestra incompleta
(1 evento/seg) y no está entre las 55 features.

**Cómo quedó el diseño** (`data/binance_ws.py` + `data/binance_poll.py`):

- La suscripción pide `@kline_<tf>` + `@markPrice@1s` + `@aggTrade` + `@trade`.
  Los dos streams de trades son redundantes a propósito: donde Binance entrega
  los dos, el hub se queda con el agregado y **descarta el crudo** (contarlos
  juntos duplicaría el volumen taker); donde `@aggTrade` calla, `@trade`
  sostiene el flujo solo.
- La salud se lleva **por stream**, no por socket (`ConnectionStatus.subscribed`
  + `stream_messages` + `mute_streams`). Un socket vivo con un stream mudo es
  invisible para un watchdog de conexión y es exactamente lo que pasó acá.
- Pasado `ws_probe_s` (25s), el hub decide: si no llegó **nada**, cambia entero
  a polling REST; si faltan `kline`/`markPrice`, los rellena por REST **en
  paralelo** y deja el WS sirviendo el flujo taker a <100ms. `conn.status`
  reporta el híbrido tal cual: `binance_ws+rest_fill(kline,markPrice)`.
- `@bookTicker` y `@depth20` entregan bien y son el enganche de Fase 2b, pero
  están **apagados por defecto** (`stream_names(book_ticker=…, depth=…)`): hoy
  no tienen consumidor y `@bookTicker` solo son ~425 frames/s por símbolo.

Verificado en vivo el 2026-08-24, hub en modo `auto`, 50s:

```
source_name -> binance_ws+rest_fill(kline,markPrice)
por stream  -> {'ethusdt@trade': 1591}
mudos       -> ['ethusdt@kline_15m', 'ethusdt@markPrice@1s', 'ethusdt@aggTrade']
trade_crudo 1580 (1ro a los 1.7s) · kline_en_curso 10 · markPrice 10 · kline_cerrada 1
```

**Consecuencia para la regla 6 (<1s del tick al dashboard)**: se cumple. El
precio y el flujo taker llegan por WS en el orden de 100ms. Lo que va por REST
es mark price y funding, magnitudes lentas por naturaleza que no pierden nada a
cadencia de 3s. Si el usuario opera desde otra red o con VPN, los streams
estándar vuelven a usarse solos al reiniciar; `BOB_FEED_MODE=ws` fuerza el
camino puro de WS y `=rest` el de polling.

**Pendiente anotado**: el orderbook, igual que el OI, **no se puede recuperar
hacia atrás**. Cada día sin persistir agregados de `@depth`/`@trade` por barra
es historia que Fase 2b nunca va a tener para entrenar. Decisión de scope del
2026-08-24: no se persiste todavía.

## Klines REST — formato posicional

`/fapi/v1/klines` devuelve arrays, no dicts:

```
[ openTime, open, high, low, close, volume, closeTime,
  quoteVolume, nTrades, takerBuyBaseVol, takerBuyQuoteVol, ignore ]
```

`takerBuyBaseVol` viene gratis en cada vela → feature de presión compradora
sin stream adicional. Mapear a `CandleRecord` (db/models.py) que ya tiene
`taker_buy_volume`.

## Fees para el etiquetado (triple-barrier)

Binance Futures VIP0 sin descuento BNB: **maker 0.02% / taker 0.05%**.
Para el labeling ser conservador: asumir taker en entrada y salida
(2 × 0.05% = 0.10% round-trip) + slippage estimado. El funding se cobra
cada 8h (00:00/08:00/16:00 UTC) — para trades intradía de horas puede
tocar 1-2 cobros; incluirlo en el label con el funding rate vigente.

## Sentimiento / contexto (Fase 7)

| Fuente | Endpoint | Cadencia | Notas |
|---|---|---|---|
| Fear & Greed | `https://api.alternative.me/fng/?limit=0` | diaria | gratis, histórico completo en un request |
| CoinGecko | `https://api.coingecko.com/api/v3/global` | ~cada 10 min | dominancia BTC + marketcap total; free tier ~30 req/min con demo key; cachear SIEMPRE |

Estas fuentes son lentas y diarias → APScheduler las snapshotea a
`SentimentSnapshot` en DB; el hot path lee de DB/cache, nunca llama fuera.

## Outliers Club (Fase 8)

`https://app.outlinersclub.com` — membresía premium de Nichelson.
Credenciales en `.env` local (`OUTLINERS_EMAIL/PASSWORD`), nunca versionadas,
nunca enviadas a terceros. Antes de escribir código: inspeccionar (con la
sesión del usuario en el browser, DevTools → Network) si el dashboard consume
una API JSON interna estable. Si sí → cliente httpx con login + cookie de
sesión. Si es HTML server-rendered o cambia seguido, o los ToS lo prohíben →
queda como fuente de consulta manual y BOB no lo automatiza.
