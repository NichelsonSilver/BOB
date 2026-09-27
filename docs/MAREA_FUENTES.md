# MAREA — fuentes de datos

> Fase M1. Cada fuente se verificó con una request real antes de escribir
> código (2026-09-27). Lo que sigue son mediciones, no la documentación de los
> proveedores.

## Estado por fuente

| fuente | qué da | acceso | estado |
|---|---|---|---|
| **FRED / ALFRED** | liquidez, tasas, crédito, FX, commodities, cripto (25 series) | API key gratis (`API_FRED`) | ✅ ingestado |
| **Tiingo** | precios diarios de 23 ETFs (acciones por región, Tesoro, crédito, oro, plata, commodities, cobre, REITs, dólar, ECH) | registro gratis (`API_TIINGO`); 50 req/hora, 1.000/día, 500 símbolos/mes; **licencia de uso interno** | ✅ ingestado |
| **BCCh** (SieteRestWS) | USD/CLP diario, TPM, cobre | registro gratis (`BCCH_USER`, `BCCH_PASS`) | ⏳ falta registro |
| **CFTC** (Socrata) | posicionamiento COT | sin key | ✅ verificado, pendiente de conectar |
| **Treasury** (fiscaldata) | TGA diario | sin key | ✅ verificado, **no se usa por ahora**: `WTREGEN` semanal alcanza para liquidez neta semanal |
| ~~Stooq~~ | — | — | ❌ **descartado**: responde con un desafío anti-bot de prueba de trabajo en JavaScript. Resolverlo por código sería saltarse su protección |
| ~~yfinance~~ | — | — | ❌ no es una API oficial y su ruptura es silenciosa |

La licencia de Tiingo importa por el objetivo de portafolio: el repo puede
mostrar **métricas** calculadas sobre sus precios, no las series crudas.

## FRED: por qué vintages y no la serie actual

La fecha de un dato macro no es la fecha en que se conoce, y muchas series se
revisan. Medido sobre la DB real:

- El USD/EUR del 15-09 se publicó el 21-09 (el H.10 sale semanal).
- El M2 de agosto se publicó el 22-09; el de octubre de 2019 cambió nueve
  veces entre enero y junio de 2020.
- **NFCI de la semana del 3-oct-2008 tiene 450 versiones**: se registró en
  1,95 y hoy vale 2,079. El índice se re-estima entero cada semana.

Alinear cualquiera de esas series por fecha del dato es lookahead (regla 5).
MAREA guarda el **historial completo de períodos de tiempo real** de ALFRED y
consulta con `marea.pit.as_of(serie, known_at)`: lo que se sabía ese día.

### Tres restricciones de la API que ordenan el cliente

1. **Tope de 2.000 vintages por request** (`DGS10` tiene 5.117): se parte la
   ventana en bloques de 1.500.
2. **Recorte en los bordes**: un período que cruza el borde de la ventana
   vuelve con inicio/fin recortados. `pit.stitch` los une; la misma regla cose
   la ingesta incremental contra la DB.
3. **120 requests/minuto**: intervalo mínimo de 0,55 s y backoff ante 429/5xx.

### El límite declarado: antes de la primera vintage

ALFRED no registra vintages anteriores a cierta fecha (2011 en la mayoría).
Antes de eso no se sabe cuándo se conoció cada dato, así que `as_of` usa el
**rezago máximo de publicación medido sobre la propia serie** y marca el punto
`exact=False`. Quien necesite point-in-time estricto filtra por `exact`.

El rezago se mide **solo sobre observaciones que extendieron el final de la
serie**. La primera versión medía sobre todo lo aparecido después de la
primera vintage y daba 16.058 días para `DGS10` y 17.044 para `NFCI`: FRED
agrega historia vieja en vintages posteriores, y eso no es una publicación.

## Catálogo FRED (corrida del 2026-09-27)

| serie | bloque | obs | 1ª vintage | rezago máx | nota |
|---|---|---|---|---|---|
| WALCL | liquidez | 1.241 | 2011-07-07 | 8 d | |
| WTREGEN | liquidez | 1.241 | 2008-12-18 | 8 d | |
| RRPONTSYD | liquidez | 3.334 | 2016-03-28 | 4 d | 2.832 huecos: antes de 2013 no había operación diaria |
| WRESBAL | liquidez | 1.241 | 2004-09-02 | 8 d | |
| M2SL | liquidez | 812 | 1980-02-08 | 58 d | se revisa casi todas las semanas |
| ECBASSETSW | liquidez | 1.447 | 2020-02-25 | 11 d | millones de EUR |
| JPNASSETS | liquidez | 341 | 2013-02-05 | 65 d | mensual, 100 millones de JPY |
| DFF, DGS2, DGS10, DFII10, T10Y2Y | tasas | — | 2005–2014 | 6–11 d | |
| BAMLH0A0HYM2, BAMLC0A0CM | crédito | ~790 | 2023-09-26 | 3 d | **licencia ICE: solo 3 años** |
| NFCI | crédito | 2.907 | 2011-05-25 | 20 d | 922.914 períodos: re-estimado entero cada semana |
| DTWEXBGS, DEXUSEU, DEXJPUS, DEXCHUS | fx | — | 2014–2019 | 11–14 d | H.10 semanal |
| VIXCLS | riesgo | 9.279 | 2010-11-22 | 5 d | |
| DCOILWTICO, DHHNGSP | commodities | — | 2011–2014 | 33 / 16 d | |
| PCOPPUSDM | commodities | 415 | 2015-11-06 | **717 d** | una publicación salió con casi 2 años de atraso: el cobre pre-2015 queda casi inutilizable; el diario sale de Tiingo/BCCh |
| CBBTCUSD, CBETHUSD | cripto | ~4.000 | 2018-06-17 | 2 d | |

### Descartadas en la verificación

- **M2 de eurozona, China y Japón**: discontinuadas en FRED (2017/2019). La
  liquidez global se aproxima con los **balances de bancos centrales** (Fed,
  BCE, BoJ) convertidos a USD.
- **SP500**: licencia S&P, solo 10 años. El índice sale de Tiingo (SPY).

## Tiingo: por qué cierre crudo y no `adjClose`

Tiingo recalcula **todo** el historial ajustado cada vez que hay un dividendo.
Una ingesta incremental que guardara `adjClose` mezclaría dos bases de ajuste
y fabricaría un retorno falso en el borde. Se guarda el cierre crudo, el
dividendo y el split, y el retorno total se calcula en `marea.returns`:

    r_t = (close_t + div_t) · split_t / close_{t-1} − 1

**Control cruzado, y una predicción equivocada que quedó corregida.** Se
compone ese retorno y se compara con la razón de `adjClose`. La primera
versión suponía que Tiingo ajusta con el factor multiplicativo
`1 − div/close_prev` y predecía una brecha chica y no nula. Medido: **~1e-10 %
en los 23 ETFs**, incluidos 289 dividendos de TLT y 4 splits inversos de UNG.
Tiingo usa la misma convención aditiva, así que el control es una
**identidad**: por encima de 1e-6 % el ETF sale con error (un split o un
dividendo mal leído). Hay un test que lo fuerza omitiendo un split.

Cada corrida vuelve a pedir los últimos 14 días y **cuenta** los cierres que
cambiaron. Los huecos se miden contra el calendario del propio universo (un día
hábil es uno en que cotizó al menos la mitad de los ETFs).

| ETF | desde | | ETF | desde |
|---|---|---|---|---|
| SPY | 1993-01 | | TIP | 2003-12 |
| QQQ | 1999-03 | | LQD | 2002-07 |
| IWM | 2000-05 | | HYG | 2007-04 |
| EFA | 2001-08 | | GLD | 2004-11 |
| EEM | 2003-04 | | SLV | 2006-04 |
| FXI | 2004-10 | | USO, UNG, DBA | 2006–2007 |
| EWJ | 1996-04 | | **CPER** (cobre) | **2011-11** |
| **ECH** (Chile) | 2007-11 | | VNQ | 2004-09 |
| SHY, IEF, TLT | 2002-07 | | **REET** | **2014-07** |
| | | | UUP | 2007-03 |

Corrida del 2026-09-27: 23/23, 0 huecos, 0 cierres revisados, 23 requests.
**La historia común a todo el universo arranca en 2014-07** (por REET); sin
REET, en 2011-11 (CPER); sin los dos, en 2007-11. Esto entra directo en la
medición de potencia de la M3.

USO, UNG, DBA, CPER y UUP son **fondos de futuros**: su retorno incluye el
costo de roll y se separa del spot en contango. Es el retorno que obtiene
quien los compra, que es lo que importa para el ranking; no es el precio del
commodity.

## Operación

    cd backend
    uv run python -m marea.data.ingest fred                  # incremental
    uv run python -m marea.data.ingest fred --series M2SL
    uv run python -m marea.data.ingest fred --full           # desde cero, ~2 min
    uv run python -m marea.data.ingest etf                   # 23 ETFs, ~45 s

Si FRED no actualizó una serie desde la última corrida, se consulta solo su
metadata: una corrida sin cambios cuesta 1 request por serie. La base es
`backend/marea.db`, separada de `bob.db`.
