# Fase M5 — Fuentes nuevas de liquidez y de ciclo cripto

> Decisión de Nichelson, 2026-10-07. Origen: el catálogo de 182 gráficos de
> Outliners Club (su membresía premium), revisado título por título. Este
> documento fija qué entra, de dónde sale cada dato y qué NO se hace.

## 1. Por qué existe esta fase

Outliners no se puede automatizar: sus Términos prohíben bots, scraping e
ingeniería inversa (§23 y §27, revisados el 2026-10-07; ver
`docs/DATA_SOURCES.md`). Lo que sí se puede hacer es usar su catálogo como
**lista de ideas** y traer lo que aporta desde **la fuente primaria y
gratuita**, con su historia y en point-in-time. Así MAREA no depende de un
tercero y cada número se puede reconstruir.

## 2. El criterio de corte

Una métrica entra solo si cumple las cuatro condiciones:

1. sirve al horizonte de **semanas a meses** o a la **capa de liquidez**;
2. es información **nueva** para MAREA;
3. tiene **fuente gratuita con historia**;
4. se puede reconstruir **sin mirar el futuro**.

De 182 entraron 19. Lo descartado y su motivo está en §7.

## 3. Lo que entra

### 3.1 Prioridad alta: liquidez que entra a cripto y crédito

| id | métrica | fuente | frecuencia | trampa conocida |
|---|---|---|---|---|
| A1 | Oferta total de stablecoins, por token, y emisiones netas (media de 7 días) | DefiLlama `stablecoins.llama.fi` | diaria | **DefiLlama no tiene vintages.** Si agrega una stablecoin, rellena su historia hacia atrás. Mitigación: persistir la fecha de ingesta de cada fila y medir las revisiones entre corridas |
| A2 | Stablecoin Supply Ratio: mcap de BTC / oferta de stablecoins | A1 + `CBBTCUSD` × supply de BTC | diaria | El supply de BTC sale de su calendario de emisión, que es determinista |
| A3 | Spread Baa − Tesoro 10 años | FRED `BAA10Y` | diaria | Resuelve el límite de ICE: `BAMLH0A0HYM2` trae solo 3 años |
| A4 | Breakevens a 5 y 10 años | FRED `T5YIE`, `T10YIE` | diaria | — |
| A5 | Pendiente 10 años − 3 meses y probabilidad de recesión | FRED `T10Y3M` + probit de la NY Fed | diaria / mensual | Los coeficientes del probit se **leen del archivo que publica la NY Fed**, no se copian de memoria. El modelo usa promedios mensuales |
| A6 | Regla de Sahm en tiempo real | FRED `SAHMREALTIME` | mensual | Ya es point-in-time de origen |

### 3.2 Prioridad media

| id | métrica | fuente | trampa conocida |
|---|---|---|---|
| B1 | **Risk de ciclo BTC point-in-time**: 60% Mayer Multiple (cierre / SMA200) + 40% desviación de una regresión log, más el drawdown desde el máximo | `CBBTCUSD` (desde 2014-12) | La versión de Outliers fija los anchors con **toda** la historia: tiene lookahead. La nuestra ajusta la regresión y normaliza solo con el pasado estricto. Se publican las dos curvas lado a lado para que se vea cuánto pierde sin mirar el futuro |
| B2 | Amplitud cripto: % de la sección sobre la SMA200 y sobre la SMA de 20 semanas | Panel de 659 perpetuos (`bob.db`) | Hay que usar el universo **con deslistados**, como la Fase 10. Solo lectura de la DB: no se importa nada de la lista de la regla 12 |
| B3 | Altcoin Season Index: % de alts que le ganan a BTC a 90 días | Mismo panel | Misma advertencia de supervivencia |
| B4 | Spread de funding BTC − ETH | Historial de funding de Binance (`/fapi/v1/fundingRate`) | ⚠ Corregido en la M5.4: este documento decía "ya está en la DB", y en `bob.db` solo estaba el de ETH del vivo de 15m. Se baja completo para los dos |
| B5 | Put/call y open interest de opciones BTC y ETH | API pública de Deribit | **No hay historia.** Hay que snapshotearla desde ya con una tarea programada propia, a una hora que no choque con `BOB_panel_forward_diario` (21:05). Igual que con el OI de la Fase 1, la historia empieza el día en que arranca |
| B6 | Encuesta SLOOS: % neto de bancos que endurecen el crédito C&I | FRED `DRTSCILM` | Es trimestral: se usa como contexto, no como señal |
| B7 | Solicitudes iniciales de desempleo | FRED `ICSA` | Se revisa: se ingiere con vintages |
| B8 | Crédito bancario H.8: préstamos y depósitos | FRED `TOTLL`, `DPSACBW027SBOG` | Hay quiebres por reclasificación: se declaran |

### 3.3 Prioridad baja: contraste de narrativa (capa 4), nunca feature

| id | métrica | fuente |
|---|---|---|
| C1 | TVL DeFi y volumen de DEX | DefiLlama |
| C2 | Páginas vistas de Wikipedia sobre cripto | API oficial de Wikimedia, desde 2015-07 |

## 4. Etapas

| etapa | contenido | criterio de salida |
|---|---|---|
| **M5.1** | Series FRED nuevas (A3–A6, B6–B8) en `marea/data/catalog.py`, con vintages | `ingest todo` las trae; cobertura y huecos reportados en `MAREA_FUENTES.md` |
| **M5.2** | Cliente DefiLlama (A1, A2) en `marea/data/` | Historia completa, más un registro de revisiones entre corridas |
| **M5.3** | Probabilidad de recesión (A5) y Risk de ciclo point-in-time (B1) | Test de no-lookahead (cambiar el futuro no altera el pasado). La probabilidad reproduce la serie publicada por la NY Fed dentro de una tolerancia declarada |
| **M5.4** | Amplitud, Altcoin Season y funding BTC−ETH (B2–B4) | Test con un universo que incluye deslistados |
| **M5.5** | Snapshot de Deribit (B5) con su tarea programada | La tarea corre con batería y reporta en UTC (las mismas lecciones del panel) |
| **M5.6** | Integración: las series nuevas entran al **monitor** (M2) y a `marea/brief.py` | El brief las muestra con su fecha de dato y su frescura |
| **M5.7** (opcional) | C1 y C2 como contraste en `/noticia` | — |

## 5. Lo que M5 NO hace

- **No pronostica.** Todo entra como **monitoreo**. El Risk de ciclo y la
  amplitud son descriptivos: decir que "anticipan techos" exigiría un
  pre-registro propio, y con 3 o 4 ciclos no hay potencia para eso.
- **No modifica el compuesto de liquidez de la M2.** Sumarle componentes
  cambiaría una serie ya publicada. Si se quiere un compuesto ampliado, va
  como serie **nueva** con nombre propio, y la vieja sigue igual.
- **No toca nada de la regla 12.** Lo nuevo vive en `marea/`; los datos del
  panel cripto se leen de la DB, no se importan sus módulos.
- **No usa Outliers como fuente.** Sus cifras pueden servir de contraste
  manual en una sesión, nunca como serie ingerida.

## 6. Riesgos

| riesgo | mitigación |
|---|---|
| DefiLlama reescribe su historia sin aviso | Registro de revisiones (M5.2), y el caveat declarado en la página |
| El probit de la NY Fed se actualiza | Se lee del archivo publicado y se registra la fecha |
| Deribit cambia o cierra su API pública | El snapshot falla de forma ruidosa; la serie queda con hueco declarado |
| La máquina duerme a las 21:05 (ritmo actual de 33% de fechas válidas) | La M5 hereda el problema; no lo resuelve. Las series diarias se recuperan, las de Deribit no |

## 7. Lo descartado del catálogo de Outliers, por motivo

| motivo | ejemplos |
|---|---|
| Variantes del mismo índice de precio | Rainbow, Cowen Corridor, Bubble Risk, Pi Cycle, Bull Market Support Band, Golden/Death Cross, Métricas de Riesgo de S&P y las Mag7 |
| Estacionalidad y anécdota (n chico, muchas pruebas) | Las 13 de Returns: halving, mejor día para DCA, fines de semana, retornos mensuales. Ley de Benford |
| Indicadores técnicos ya medidos sin ventaja | RSI, MACD, Bollinger, Supertrend (frente intradía, 05-10) |
| Fuera de horizonte o de alcance | Vivienda, deuda federal, salarios, PIB per cápita, velocidad de M2, NFT, gas, emisión de ETH, dividendos de las Mag7, S&P según partido político, Stock-to-Flow |
| Sin fuente gratuita | Supply de holders de largo plazo (UTXO), historia de dominancia y de mcap total (CoinGecko de pago), Google Trends (sin API oficial) |
| Redundante con lo que ya está | M2, activos de la Fed, fondos federales, NFCI, WTI y gas, VIX, OI y funding de Binance, volatilidad realizada |
| Decisión explícita de Nichelson | **Minería** (hash rate, hash ribbons, Puell, ingreso minero, dificultad): pocas capitulaciones, sin potencia. **Gem Hunters** y **Value**: análisis fundamental discrecional, fuera del alcance |
| Poca historia gratuita | OI y funding de Bybit y OKX: ya está el de Binance, que es el de mayor volumen |

## 8. Registro de etapas

### M5.1 y M5.2 (2026-10-07)

Ver `docs/MAREA_FUENTES.md`: 9 series FRED nuevas y stablecoins de DefiLlama
con vintages construidas por corrida.

### M5.3 (2026-10-07) — recesión y Risk de ciclo

**Probabilidad de recesión** (`marea/recession.py`,
`uv run python -m marea.recession`). Es el probit de la NY Fed:
P(recesión en m+12) = Φ(α + β·spread_m).

- α = −0,5333 y β = −0,6330 se **recuperan exactos** de las 795 filas del
  archivo publicado (`allmonth.xls`, guardado el 2026-09-06): residuo 2e-15.
- El spread se reproduce con FRED (`DGS10` − `DTB3` llevada a base de bono,
  promedios mensuales redondeados a 2 decimales): **760 meses comparados,
  diferencia máxima 0,23 pp** en la probabilidad. `T10Y3M` no sirve (difiere
  hasta 0,13 puntos de spread). Por eso se sumó `DTB3` al catálogo.
- Como es un modelo de parámetros fijos estimado hasta 2006, la probabilidad
  es point-in-time desde ahí y dentro de muestra antes; se marca `exact`.
- Lectura del 07-10: **12,8 %** para 2027-09 (spread de septiembre, un mes más
  que el archivo). Máximo desde 2022: **70,9 %** con el spread de 2023-05, y la
  recesión no llegó. Se muestra con esa historia al lado.
- **El gauge de Outliers ("NY Fed RECPRO", 0,6 %) no es este modelo.** Casi
  seguro es `RECPROUSM156N` (Chauvet-Piger), que estima la probabilidad de
  estar en recesión **hoy**. Son preguntas distintas.

**Risk de ciclo BTC** (`marea/cycle.py`, `uv run python -m marea.cycle`).
Misma receta que Outliers (60 % Mayer Multiple + 40 % desviación log-log
contra los días desde el génesis), en dos versiones:

| | point-in-time | con lookahead |
|---|---|---|
| hoy (2026-10-06) | **0,476** | 0,431 |
| techo 2021-11-10 | 0,756 | **0,867** |
| piso 2022-11-21 | 0,060 | 0,044 |
| ETF 2024-03-13 | 0,781 | 0,838 |

Sobre 2.867 días con las dos: correlación 0,94, diferencia media 0,10, y en
el **14,3 %** de los días se separan más de 0,20. **La versión con lookahead
marca el techo de 2021 0,11 más arriba**: eso es lo que hace verse tan nítido
el gráfico de Outliers. Su 0,404 no se reproduce exacto porque su
normalización no está publicada.

Decisiones: normalización por percentil contra el pasado estricto (un
min-max point-in-time salta con cada máximo nuevo); la regresión expansiva se
calcula con sumas acumuladas; warm-up de 4 años, así que la versión PIT existe
desde 2018-11. El precio de cada día es el **corregido al día siguiente**:
`CBBTCUSD` publica primero un intradía y lo revisa al cierre (451
revisiones, mediana 1 %, máximo 10 %). La versión completa usa datos desde
2014-12 y no desde 2010 como la de Outliers.

Tests: cambiar el futuro deja idéntico el pasado PIT y **sí** mueve el de la
versión completa; el probit reproduce la probabilidad publicada de 2027-08;
la conversión reproduce la primera fila del archivo (1959-01). 12 tests,
cobertura 100 % en los dos módulos.

### M5.4 (2026-10-07) — amplitud, Altcoin Season y funding

**Amplitud y Altcoin Season** (`marea/breadth.py`,
`uv run python -m marea.breadth`, 3,6 s). Lee el panel diario de `bob.db` en
solo lectura, con el universo congelado y sus deslistados, y aplica la regla de
elegibilidad pre-registrada de la Fase 10 (`bob.signals.universe`, importada
sin cambios). Se excluyen 7 símbolos que no son monedas: índices de Binance
(BTCDOM, DEFI, BLUEBIRD, FOOTBALL), USDC y oro tokenizado (PAXG, XAUT). Solo
se leen velas cerradas.

| fecha | % sobre SMA200 | % sobre SMA 20 sem | Altcoin Season |
|---|---|---|---|
| hoy (2026-10-06) | 83,1 % (n=166) | 86,7 % | 0,70, neutral |
| 2021-05-01 | 100 % (n=63) | 100 % | 0,92, alts |
| techo 2021-11-10 | 77,4 % | 92,5 % | 0,54 |
| piso 2022-11-21 | **7,6 %** | 6,1 % | 0,38 |
| 2024-12-05 | 97,2 % | 98,4 % | 0,84, alts |

Altcoin Season: el top 50 es por **volumen mediano de 30 días** y no por
capitalización (no hay historia gratis): proxy declarado.

**Funding** (`marea/data/binance_funding.py`, `marea/funding.py`,
`uv run python -m marea.data.ingest funding` y `uv run python -m
marea.funding`). Historial completo: 7.754 liquidaciones de BTC desde
2019-09-10 y 7.520 de ETH desde 2019-11-27, 16 requests. Dos trampas:
`startTime=0` devuelve lo más reciente y no lo más viejo, y el intervalo de
liquidación no es fijo, así que lo diario es la **suma** de las tasas del día.
Solo días cerrados. Lectura del 07-10 (media de 7 días, anualizado): BTC
3,71 %, ETH 4,64 %, spread ETH−BTC 0,93 %, por encima del 56 % de los días
desde 2019-12.

Tests: cambiar el futuro no mueve la amplitud ni la Altcoin Season pasadas;
paginación del funding desde una fecha real; ingesta incremental. 11 tests;
cobertura 100 % en los módulos puros y en la ingesta.

### M5.5 (2026-10-07) — opciones de Deribit y la tarea diaria

**Snapshot de opciones** (`marea/data/deribit.py`, `marea/options.py`,
`uv run python -m marea.data.ingest options`). La API pública no tiene
historia: cada foto es irrecuperable. Por cada moneda (BTC, ETH) se guardan
las métricas y **la respuesta cruda comprimida** (~40 KB por moneda y día,
~27 MB por año), para poder calcular más adelante métricas que hoy nadie
pensó sobre toda la historia acumulada.

Métricas: put/call por open interest, por volumen de 24 h y de vencimientos
≤ 30 días; open interest nocional en USD. Los instrumentos ya vencidos (08:00
UTC) se ignoran. Primera foto, 2026-10-07 17:47 UTC:

| | instrumentos | P/C OI | P/C volumen | P/C ≤ 30 d | OI nocional |
|---|---|---|---|---|---|
| BTC | 950 | 0,564 | 0,957 | 0,538 | 30,9 mil M USD |
| ETH | 814 | 0,588 | 0,852 | 0,992 | 3,6 mil M USD |

Sin historia no hay percentil: estos números no se pueden calificar de altos
o bajos hasta que se acumulen meses de fotos.

**Tarea programada `MAREA_snapshot_diario`**, todos los días a las **12:00
locales**, corre `backend/scripts/marea_diario.ps1` →
`uv run python -m marea.data.ingest diario` (Deribit + DefiLlama + funding).
A mediodía y no de noche porque el equipo suele estar apagado a las 21:05 (la
del panel corre casi siempre de recuperación a la mañana siguiente). Mismos
flags que la del panel: `AllowStartIfOnBatteries`,
`DontStopIfGoingOnBatteries`, `StartWhenAvailable`, `WakeToRun`; límite de 30
minutos. Log en UTC en `backend/logs/marea_diario.log`. Script en ASCII con
BOM, verificado con el parser de PowerShell. **Verificada disparándola desde
el programador**: 3/3 fuentes sin error en 12 s, foto número 2 escrita.

Tests: parser de instrumentos (incluido el strike con `d` de ETH), agregación
que ignora vencidos, reintentos del cliente, cada corrida agrega una foto con
su crudo, y la corrida `diario` sigue si una fuente falla. 6 tests.

### M5.6 (2026-10-07) — integración al monitor, al brief y a la API

**Monitor de liquidez** (`marea/monitor.py`). Las 9 series nuevas entran
como **contexto**, ninguna al compuesto: Baa−10a, SLOOS, préstamos y
depósitos bancarios (con signo), stablecoins (signo +, marcada `declarado`
porque su historia anterior al 07-10 no es point-in-time), y breakevens a 5 y
10 años, Sahm y solicitudes de desempleo con signo 0 (son ciclo e inflación,
no tienen lectura de liquidez sin ambigüedad). Son **opcionales**: si una
falta en la base, su indicador sale sin dato y el régimen se calcula igual.

**Control bit a bit**, el mismo criterio que la regla 12 pide para el panel:
`marea.monitor.build` a 2022-06-17 y a 2026-10-05, antes y después del
cambio. Régimen, historia completa y los 14 indicadores previos
**idénticos**; solo se agregan los 9 nuevos.

**Panorama** (`marea/panorama.py`): una función por bloque —recesión, Risk de
ciclo BTC, amplitud y Altcoin Season, funding, opciones, stablecoins— cada
uno con su evidencia y su fecha de dato. Un bloque que falla devuelve
`error` y no apaga el resto. Lo usan:

- **el brief** (`marea.brief`), en la sección nueva **4b. Ciclo y cripto**,
  más cuatro filas de frescura (DefiLlama, funding, Deribit). ~6 s;
- **la API**, `GET /api/macro/panorama`, con caché de 15 minutos por proceso
  y cálculo fuera del event loop: 3,7 s la primera vez, 3 ms después.

La cifra que va a ver el usuario en la M6 y la que cita el memo salen de la
misma función.

Agentes: `coordinador` y `cripto` conocen la sección 4b, con dos reglas de
lectura: la recesión se cita con su falla de 2023, y las opciones no se
califican hasta tener historia.

Tests: los seis bloques sobre una base sintética (incluido un `bob.db` falso
con una stablecoin que la amplitud debe excluir), base vacía sin excepción,
render del brief con un bloque caído, y la ruta con su caché y su 404.
5 tests nuevos, cobertura 100 % en `panorama.py`; 189 en verde.
