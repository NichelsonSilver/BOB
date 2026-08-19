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
| `/fapi/v1/fundingRate` | historial de funding (cada 8h) | máx 1000 rows/request, paginar |
| `/futures/data/openInterestHist` | OI histórico (granularidad 5m mínima) | ⚠️ **Solo conserva ~30 días de historia** — para el backtest de 12 meses el OI histórico NO está disponible gratis. Decisión Fase 2: empezar a persistir OI desde ya (data/store.py) y entrenar el feature de OI solo sobre la ventana que tengamos; el modelo debe funcionar con features faltantes |
| `/futures/data/topLongShortPositionRatio` | ratio long/short de top traders | misma ventana ~30 días, máx 500 rows |
| `/futures/data/globalLongShortAccountRatio` | ratio long/short global | idem |
| `/futures/data/takerlongshortRatio` | taker buy/sell volume ratio | idem |

**Rate limit**: 2400 de peso por minuto por IP en `/fapi/*`. Con token bucket
conservador (usar el header `X-MBX-USED-WEIGHT-1M` de las responses para
autorregular) no se llega ni cerca. Los `/futures/data/*` tienen límites
propios más estrictos — espaciar esas llamadas (son para snapshots
periódicos, no para el hot path).

## Binance USDⓈ-M Futures — WebSocket (`wss://fstream.binance.com`)

Formato: `wss://fstream.binance.com/stream?streams=ethusdt@kline_15m/ethusdt@aggTrade/...`
(streams combinados; los nombres de símbolo van en minúscula).

| Stream | Qué da | Uso en BOB |
|---|---|---|
| `<sym>@kline_<tf>` | vela en curso (update ~250ms) + flag de cierre `k.x` | chart + features al cierre de barra |
| `<sym>@aggTrade` | trades agregados con flag `m` (maker side) | volume delta / taker ratio en tiempo real |
| `<sym>@depth20@100ms` | top 20 niveles del book cada 100ms | orderbook imbalance |
| `<sym>@markPrice@1s` | mark price + funding rate corriente | KPI in-live, distancia a liquidación |
| `<sym>@forceOrder` | liquidaciones | feature de microestructura (⚠️ Binance lo limita a 1 evento/seg por símbolo — es muestra, no feed completo) |

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
