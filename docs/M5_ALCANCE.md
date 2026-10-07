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
| B4 | Spread de funding BTC − ETH | Funding de Binance que ya está en la DB | — |
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
