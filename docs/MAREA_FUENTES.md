# MAREA — fuentes de datos

> Fase M1. Cada fuente se verificó con una request real antes de escribir
> código (2026-09-27). Lo que sigue son mediciones, no la documentación de los
> proveedores.

Credenciales: todas en `backend/.env` (ignorado por git), con los nombres de
`.env.example`. Nunca en el repo ni en un artefacto.

## Estado por fuente

| fuente | qué da | acceso | estado |
|---|---|---|---|
| **FRED / ALFRED** | liquidez, tasas, crédito, actividad, FX, commodities, cripto (35 series; 9 sumadas en la M5.1 y `DTB3` en la M5.3) | API key gratis (`API_FRED`) | ✅ ingestado |
| **Tiingo** | precios diarios de 23 ETFs (acciones por región, Tesoro, crédito, oro, plata, commodities, cobre, REITs, dólar, ECH) | registro gratis (`API_TIINGO`); 50 req/hora, 1.000/día, 500 símbolos/mes; **licencia de uso interno**. Un 429 = cuota de la hora agotada: la corrida no espera, omite el resto y la siguiente lo completa | ✅ ingestado |
| **BCCh** (SieteRestWS) | dólar observado, TPM, cobre diario, BCP 10a, tasas de política BCE y BoJ | registro gratis + activación (`BCCH_USER`, `BCCH_PASS`) | ✅ ingestado |
| **CFTC** (Socrata) | posicionamiento COT de 13 mercados | sin key | ✅ ingestado |
| **DefiLlama** (`stablecoins.llama.fi`) | oferta de stablecoins: 2 totales y 7 tokens, diaria desde 2017-11 | sin key | ✅ ingestado (M5.2). **Sin vintages de origen**: se construyen por corrida |
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

### Sumadas en la M5.1 (2026-10-07)

Elegidas del catálogo de Outliers y traídas de FRED (`docs/M5_ALCANCE.md`).
Verificadas contra la API el mismo día; 9/9 sin error, 36 requests.

| serie | bloque | obs | 1ª vintage | rezago máx | nota |
|---|---|---|---|---|---|
| BAA10Y | crédito | 10.190 | 2014-01-27 | **166 d** | desde 1986: cubre lo que ICE recorta a 3 años. El rezago es un **apagón real** de FRED: los datos de 2016-10-07 en adelante se publicaron recién el 2017-03-22. Mediana: 1 d |
| T5YIE, T10YIE | tasas | 5.945 | 2014-01-27 | 6 d | breakevens desde 2003 |
| T10Y3M | tasas | 11.193 | 2014-01-27 | 6 d | plazo constante: **no** es la pendiente del probit de la NY Fed (difiere hasta 0,13 pts) |
| DTB3 | tasas | 18.181 | 2005-06-28 | 11 d | sumada en la M5.3: con `DGS10` reproduce el spread de la NY Fed (ver `marea.recession`) |
| SAHMREALTIME | actividad | 801 | 2019-09-06 | 80 d | point-in-time **de origen**: cada valor usa el desempleo publicado en su momento. Rezago por el cierre del gobierno de EE.UU. (oct–nov 2025) |
| DRTSCILM | crédito | 146 | 2010-04-20 | 46 d | SLOOS, trimestral |
| ICSA | actividad | 3.117 | 2009-05-28 | 54 d | mediana 5 d; el máximo es el mismo cierre de 2025 (datos de oct publicados el 2025-11-20) |
| TOTLL | crédito | 2.804 | 1996-12-06 | 16 d | H.8: 192.650 períodos, se revisa mucho |
| DPSACBW027SBOG | crédito | 2.804 | 2012-08-17 | 16 d | H.8, quiebres por reclasificación |

Los huecos de las diarias (254–484) son feriados del mercado de bonos,
igual que en `DGS10`. Los dos rezagos grandes **no se corrigen**: el rezago
máximo solo fecha los datos anteriores a la primera vintage, y ahí
equivocarse hacia largo es conservador, no lookahead.

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

## CFTC: posicionamiento COT

13 mercados: E-mini S&P, Tesoro 2a/10a/largo, euro, yen, índice dólar,
bitcoin (CME), oro, plata, WTI, gas y cobre. 1.059 semanas desde 2006-06 (bitcoin
442 desde 2018-04), **0 semanas faltantes**, 13 requests.

- **Se indexa por código de contrato, nunca por nombre.** La CFTC renombró los
  mercados en febrero de 2022; filtrando por nombre, el Tesoro, el petróleo y el
  cobre parecen empezar en 2022.
- **Dos reportes.** Financieros en *Traders in Financial Futures*
  (especulador = fondos apalancados, institucional = asset managers);
  commodities en *Disaggregated* (especulador = managed money).
- **Fecha del dato ≠ fecha de publicación.** El reporte es "al martes" y se
  publica el viernes (lunes con feriado). `available_date` = fecha del reporte
  + 6 días. Usar el martes sería lookahead.
- **No todos los reportes son de martes**: 14 están fechados en lunes o
  miércoles por feriados. La primera métrica de huecos contaba martes y los
  marcaba como faltantes; se cuentan semanas ISO.
- Cada corrida re-pide 4 semanas y **cuenta** las revisiones de la CFTC.

## BCCh: Banco Central de Chile

6 series diarias, 6 requests: dólar observado (desde 1982), TPM (1997), cobre
refinado BML (2012), BCP 10 años secundario (2004) y las tasas de política del
BCE (1998) y del Banco de Japón (2008). El IPSA **no** está en la base diaria:
la renta variable chilena sale de ECH (Tiingo). El cobre diario del BCCh
reemplaza con ventaja al `PCOPPUSDM` de FRED (mensual, con 717 días de rezago
máximo).

Lo que costó conectarla, para que no se repita:

- **Dos pasos de alta**: registro en la Base de Datos Estadísticos **y**
  activación iniciando sesión en el sitio de la API. Antes del segundo, la API
  responde `-5 Invalid username or password` con credenciales correctas.
- **`pass` es la contraseña de la cuenta**, no el token largo que muestra el
  sitio (probado: con el token también responde `-5`).
- **Los errores vienen con HTTP 200**; el estado real es el campo `Codigo`.
- **Responde en ISO-8859-1**; leerlo como UTF-8 revienta en "dólar".
- La página pública de indicadores diarios **no** sirve como fuente: muestra
  solo el día, y reconstruir historia por scraping de HTML es la clase de
  fuente frágil que se descartó con Stooq.

**Cuándo se conoce cada dato.** El BCCh no publica vintages, así que el rezago
es **declarado**, no medido, y va en el catálogo con su razón. El domingo
27-09 ya estaban publicados el dólar observado y el cobre **del lunes 28**: el
BCCh estampa el día d con datos del hábil anterior, así que usar su fecha no es
lookahead (rezago 0). La TPM se anuncia antes de regir (0). El BCP 10 años es
la tasa de transacciones del día (1). BCE y BoJ los carga el BCCh (1, margen de
carga). Cada corrida re-pide 14 días y **cuenta** revisiones, que es lo único
observable sin vintages.

## DefiLlama: stablecoins con vintages propias (M5.2, 2026-10-07)

Cuatro hechos medidos el 07-10 que ordenan el cliente (`marea/data/defillama.py`):

1. Grilla diaria a las 00:00 UTC, sin saltos, desde 2017-11-29.
2. **La fila del día en curso es parcial**: a las 17:17 UTC ya existía la del
   07-10 y cambiaba durante el día. Se descarta hasta que el día cierra.
3. **No publica vintages y reescribe su historia** (al sumar una stablecoin,
   rellena su pasado). Cada corrida es una vintage nuestra: se baja la serie
   completa y se compara con lo guardado (`marea.pit.apply_snapshot`). Lo que
   cambió cierra su período y abre otro, y `stable_run` registra cuántas filas
   se revisaron y cuánto. Se lee con `marea.pit.as_of`, igual que FRED.
4. **Los muertos siguen en el total.** UST (hoy `USTC`) no aparece en el listado
   vigente con su símbolo viejo, pero el total la incluye: cae de 186,8 a 158,0
   mil millones entre el 30-04 y el 15-05 de 2022. No hay sesgo de
   supervivencia, pero esa caída fue valor destruido y no dinero que salió:
   las emisiones netas la leen como −3,7 mil millones por día (media de 7 días).

| serie | desde | nota |
|---|---|---|
| STABLE_USD | 2017-11-29 | atadas al dólar, en USD. Incluye algorítmicas |
| STABLE_ALL | 2017-11-29 | todas las monedas, en USD |
| USDT, USDC, DAI | 2017-11 / 2018-09 / 2019-11 | |
| USDS, USDE | 2024-09 / 2023-12 | USDS sucede a DAI; USDe es sintética |
| BUSD, UST | 2020-04 / 2020-11 | muertas, incluidas a propósito |

**Límite declarado:** todo lo anterior a la primera corrida (2026-10-07) se lee
como **no exacto**, con 1 día de rezago **declarado, no medido**. La precisión
point-in-time empieza a acumularse desde esa fecha, y depende de que la
ingesta corra seguido. La segunda corrida del mismo día revisó 0 filas.

Derivadas en `marea/stablecoins.py` (puro): emisiones netas con media móvil
que no cruza huecos, supply de BTC por interpolación entre halvings (error
< 0,2 %, falla en voz alta desde 2028-01-01 hasta cargar el 5º halving) y SSR.
Control del 07-10: supply 20,09 M BTC, SSR 5,49 (2,10 en el fondo de 2022-11;
145 en 2018-12, cuando las stablecoins eran marginales: el SSR anterior a
2020 no es comparable).

## Operación

    cd backend
    uv run python -m marea.data.ingest fred                  # incremental
    uv run python -m marea.data.ingest fred --series M2SL
    uv run python -m marea.data.ingest fred --full           # desde cero, ~2 min
    uv run python -m marea.data.ingest etf                   # 23 ETFs, ~45 s
    uv run python -m marea.data.ingest cot                   # 13 mercados COT, ~17 s
    uv run python -m marea.data.ingest bcch                  # 6 series BCCh, ~12 s
    uv run python -m marea.data.ingest stable                # 9 series DefiLlama, 8 requests
    uv run python -m marea.data.ingest todo                  # las cinco en orden

Si FRED no actualizó una serie desde la última corrida, se consulta solo su
metadata: una corrida sin cambios cuesta 1 request por serie. La base es
`backend/marea.db`, separada de `bob.db`.

## Cierre de la M1 (2026-09-29)

Corrida `todo` sobre las cuatro fuentes reales: **4/4 sin error, 1 min 50 s,
95 requests** (FRED 53, Tiingo 23, CFTC 13, BCCh 6). Incremental: una barra
nueva por ETF, 0 cierres revisados, 0 huecos, control contra `adjClose` en
~1e-10 %.

| fuente | contenido | desde | cuándo se conoce cada dato |
|---|---|---|---|
| FRED/ALFRED | 25 series de liquidez, tasas, crédito, FX, commodities, cripto | 1954–2023 según serie | **medido**: historial de vintages (`marea.pit.as_of`) |
| Tiingo | 23 ETFs | 1993–2014 según ETF; universo común desde 2014-07 | cierre del día d, conocido al cierre |
| CFTC | 13 mercados COT | 2006-06 (bitcoin 2018-04) | reporte + 6 días |
| BCCh | 6 series diarias | 1982–2012 según serie | **declarado** por serie en el catálogo |

Lo que la M1 deja resuelto para la M2 es la pregunta que decide si un
monitor de liquidez es honesto: **qué se sabía en cada fecha**. Es exacta en
FRED, declarada y conservadora en las otras tres.

