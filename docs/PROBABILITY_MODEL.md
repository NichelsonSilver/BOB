# PROBABILITY_MODEL — Deducción del stack de forecasting de BOB

> Documento de método. Los resultados numéricos de cada corrida viven en
> `backend/artifacts/<run_id>.txt` y `.json`, no acá: este archivo explica
> **por qué** el modelo está construido así y qué decisiones se tomaron
> contra qué alternativas.

---

## 0. La pregunta que NO se responde

El pedido original fue "un modelo que pronostique precios". No se construyó
eso, y la razón es la decisión de diseño más importante del proyecto.

Predecir el **nivel** del precio (`close[t+H]` como valor) produce métricas
espectaculares y completamente vacías. Un modelo que simplemente copia el
último precio obtiene R² ≈ 0.99 y un gráfico "predicción vs real" que se ve
perfecto — desplazado exactamente una barra. Lo que mide ese R² no es
capacidad predictiva: es la autocorrelación de una serie casi integrada.

El diagnóstico se puede verificar en este mismo repo. El barrido de
`BarrierConfig` sobre 2 años de ETHUSDT 15m da EV ≈ **−0.15% en las 24
configuraciones probadas**, y ese número es exactamente la fricción
(`fee_roundtrip + slippage` = 0.14%). Es decir: sin modelo, el retorno
esperado de entrar en cualquier barra es cero bruto y negativo neto. El
mercado es una martingala neta de costos. Cualquier proyecto que reporte
haber "predicho el precio" y no muestre ese piso, no lo midió.

Lo que sí se puede predecir, y lo que BOB necesita, son tres cosas
distintas.

---

## 1. Los tres targets

| # | Target | Tipo | Por qué es predecible | Alimenta |
|---|---|---|---|---|
| 1 | `P(TP antes que SL)` | Clasificación binaria | Débilmente: es el objetivo difícil. La calibración importa más que el acierto. | KPI 1 — Seguridad |
| 2 | Volatilidad realizada futura | Regresión | **Fuertemente**: la volatilidad tiene clustering (Mandelbrot 1963) y memoria larga. Es lo único con R² OOS sustancial. | Dimensionado de TP/SL, KPI 2 |
| 3 | Intervalo del retorno a H barras | Predicción conformal | La *dispersión* es predecible aunque la *dirección* no lo sea. | Cono de precio del dashboard |

La asimetría entre el 1 y el 2 no es un defecto del modelo: es la estructura
del problema. La dirección del precio es casi imposible de anticipar; su
magnitud, bastante. Un asistente honesto explota lo segundo para acotar el
riesgo de lo primero.

---

## 2. Etiquetado triple-barrier (`models/labeling.py`)

Basado en López de Prado, *Advances in Financial Machine Learning*, cap. 3.
Para cada barra `i` se define un setup y se recorre el futuro hasta que
toque una de tres barreras: TP, SL, o el vencimiento (`H` barras).

### 2.1 Entrada al open siguiente

La decisión se toma al **cierre** de la barra `i`; el fill ocurre en el
**open de `i+1`**. Etiquetar usando `close[i]` como precio de entrada es
lookahead de una barra: en producción esa información no está disponible en
el momento de decidir. Es el error que infla el win rate de un backtest y
desaparece en vivo.

Verificado por `test_entrada_es_el_open_siguiente_no_el_close_actual`.

### 2.2 Empate intrabarra: se resuelve contra el trader

Si en la misma vela el `high` alcanza el TP y el `low` alcanza el SL, el
OHLC **no dice cuál ocurrió primero**. Hay tres opciones: asumir TP (optimista),
asumir SL (conservadora), o descartar el caso (pierde muestras y sesga).

Se elige SL. El usuario opera apalancado y ya pasó por una liquidación:
cualquier supuesto optimista acá se traduce en una probabilidad que no
existe. El costo es visible — en las configuraciones simétricas el
resultado sale ~44.6% TP contra ~46.1% SL, y esa diferencia de 1.5pp *es*
la regla de desempate. Es preferible verla explícita que esconderla.

Verificado por `test_empate_intrabarra_se_resuelve_contra_el_trader`.

### 2.3 Las barreras escalan con la volatilidad del horizonte

`sigma_H = sigma_por_barra · sqrt(H)`, y las barreras se ponen a
`mult · sigma_H` del precio de entrada.

Escalar por sigma *por barra* parece equivalente y no lo es. En ETH 15m la
sigma por barra ronda 0.25%, así que un SL de "1.0 sigma" pediría arriesgar
0.25% neto cuando la fricción de ida y vuelta ya son 0.14%. El setup queda
imposible de ganar por aritmética, no por falta de señal. Con `sigma_H`
(≈1% a H=16) el costo pesa ~14% del riesgo y el problema vuelve a ser
predecir.

Usar volatilidad en vez de porcentajes fijos es también lo que permite
entrenar un solo modelo sobre dos años que contienen regímenes muy
distintos: "1 sigma" significa lo mismo en un día plano y en uno agitado.

### 2.4 Los costos van en el retorno, no en las barreras

Se evaluaron dos formulaciones:

- **(A) Costos dentro de las barreras** — desplazar el TP hacia afuera y el
  SL hacia adentro para netear exactamente el objetivo. Aritméticamente
  correcta, pero rompe el KPI: con fricción de 0.14% y `sigma_H` de 1%, un
  setup nominalmente simétrico de ±0.5 sigma termina con barreras brutas de
  **+0.64% / −0.36%** — 1.8:1 en contra — y la "probabilidad" deja de
  corresponder a los precios que el usuario ve en pantalla.
- **(B) Barreras a nivel de mercado, costos en el retorno** — elegida. El TP
  y el SL son los precios que el usuario efectivamente escribe en Binance, y
  `P(toca TP antes que SL)` es literalmente lo que el KPI promete.

Los costos no desaparecen en (B): entran completos en `net_return` (fees
0.10% + slippage 0.04% + drag de funding por barra) y son los que alimentan
el EV del KPI 2 y la regla de emisión. Además, un setup cuyo TP no supera el
costo se descarta al etiquetar — es imposible de ganar por construcción.

Supuesto conservador declarado: el funding se cobra como costo en **ambas**
direcciones, cuando en realidad un short suele *cobrarlo* si la tasa es
positiva. Cablear la serie real de funding es trabajo de la Fase 7.

### 2.5 Elección de barreras — barrido empírico

Los defaults no se eligieron por intuición. Barrido sobre 69.119 velas de
ETHUSDT 15m (2024-08-31 a 2026-08-21), dirección long:

| H | TP | SL | tasa base | TP% | SL% | vertical% | EV neto |
|---|---|---|---|---|---|---|---|
| 8 | 0.50 | 0.50 | 0.438 | 43.8% | 45.7% | 10.5% | −0.150% |
| **16** | **0.50** | **0.50** | **0.446** | **44.6%** | **46.1%** | **9.2%** | **−0.152%** |
| 16 | 0.75 | 0.75 | 0.349 | 34.9% | 36.9% | 28.1% | −0.158% |
| 16 | 1.00 | 1.00 | 0.256 | 25.6% | 27.8% | 46.6% | −0.160% |
| 16 | 1.50 | 1.00 | 0.130 | 13.0% | 28.8% | 58.2% | −0.161% |
| 32 | 0.50 | 0.50 | 0.448 | 44.8% | 47.1% | 8.1% | −0.163% |
| 48 | 0.50 | 0.50 | 0.451 | 45.1% | 47.6% | 7.2% | −0.171% |

Criterios de selección:

1. **La barrera vertical no debe dominar.** Con barreras de 1.0 sigma, el
   46.6% de los setups expira sin tocar nada: el target deja de ser "TP
   antes que SL" y pasa a ser "¿se movió algo?", que es otra pregunta con
   otra tasa base. Con 0.5 sigma la vertical baja a ~9%.
2. **Muestras efectivas suficientes.** Los labels se solapan (§4.2). A H=16
   los pesos por unicidad suman **16.146** sobre 68.813 labels nominales
   (peso medio 0.23); con H=48 el solapamiento triplica y el tamaño efectivo
   caería a un tercio, muy poco para 55 features.
3. **Sigue siendo intradía.** H=16 son 4 horas, coherente con el perfil del
   usuario.

Default: `tp_mult=0.5, sl_mult=0.5, horizon_bars=16`.

### 2.6 El listón: probabilidad de equilibrio

Una Seguridad de 45% no significa nada por sí sola. El número contra el que
hay que leerla es la probabilidad de equilibrio:

```
P_equilibrio = (SL + costo) / (TP + SL)
```

Con barreras simétricas y fricción de 0.14% sobre la `sigma_H` mediana del
periodo, el equilibrio medido queda en **61.6%** contra una tasa base de
44.0%: el modelo tiene que levantar la probabilidad **17.6 puntos** para que
un trade tenga EV positivo. El
reporte muestra siempre los tres números juntos (tasa base, equilibrio, y lo
que el modelo predice) para que no se puedan leer por separado.

---

## 3. Features (`signals/features.py`)

55 features en seis familias: momentum, volatilidad, osciladores,
estructura, microestructura y estacionalidad.

Dos invariantes gobiernan el diseño, y ambas están cubiertas por tests que
fallan si se rompen:

### 3.1 Causalidad estricta

El valor en el índice `i` depende solo de barras cerradas hasta `i`
inclusive. El test decisivo (`test_mutar_el_futuro_no_altera_el_pasado`) no
revisa función por función: muta **toda** la serie después de un corte —
crash del 60%, volumen ×50 — y exige que ni un solo valor anterior al corte
cambie, feature por feature.

Un bug encontrado por esta vía: `np.cumsum` propaga NaN hasta el final del
array, y como las series de retornos empiezan con NaN por construcción, eso
vaciaba en silencio todos los features derivados. `rolling_sum` ahora trata
los NaN como ventana inválida.

### 3.2 Adimensionalidad

Ningún feature puede escalar con el nivel de precio. Todo es ratio, retorno
log, z-score o percentil móvil. No es cosmética: un modelo entrenado sobre
niveles aprende el rango histórico del símbolo y se rompe en cuanto el
precio sale de él — además de hacer imposible la promesa de que el motor sea
agnóstico del símbolo.

El test `test_escalar_el_precio_no_cambia_los_features` multiplica la serie
completa por 10 y exige matrices idénticas. Encontró dos fugas reales:

- `log1p` **no es invariante a escala** — el `+1` rompe la propiedad
  `log(k·x) = log(k) + log(x)` que el z-score cancelaría. `avg_trade_size_z`
  dependía del nivel de precio. Reemplazado por `log` con piso.
- `amihud_illiq` aplicaba el piso numérico al cociente `|r|/qvol`, que sí
  escala, así que el recorte se activaba a distinto precio en cada símbolo.
  Reescrito como `log|r| − log(qvol)`, donde el piso cae sobre el retorno,
  que es invariante.

Ambos eran silenciosos: producían números plausibles.

### 3.3 Microestructura sin streams extra

`taker_buy_volume` viene gratis en cada kline, así que el volume delta, el
taker ratio, el tamaño medio de trade y la iliquidez de Amihud salen del
histórico REST sin depender del WebSocket. Relevante porque ~70-90% del
volumen es algorítmico y deja huellas sistemáticas.

---

## 4. Validación (`models/validation.py`)

### 4.1 Por qué el K-Fold estándar es inválido

Dos razones que se suman:

1. Mezcla el orden temporal: entrena con futuro para predecir pasado.
2. Aunque se respete el orden, el label de la barra `i` cubre `i+1..i+H`. Si
   `i` queda en train e `i+3` en test, el modelo ya vio buena parte del
   futuro que se le pide predecir.

La solución (López de Prado, cap. 7) es **purga + embargo**: se eliminan de
train las muestras cuyo label se solapa con el periodo de test, más una
banda posterior para cubrir la autocorrelación serial.

`purged_walk_forward` es el que reporta resultados — replica lo que BOB
puede hacer en vivo. `purged_kfold` existe solo para investigación de
features y sus métricas no se publican como desempeño esperado.

`assert_no_leakage` verifica la propiedad directamente y se corre en cada
experimento, no solo en tests. La fuga es peligrosa justamente porque hace
que el reporte se vea **mejor**: nadie la busca cuando los números salen
bien.

### 4.2 Pesos por unicidad

Los labels solapados no son observaciones independientes: la barra `i` y la
`i+1` comparten casi todo su futuro. Tratarlas como independientes le da al
modelo la ilusión de tener H veces más datos de los que tiene.

El peso de cada muestra es el promedio de `1/concurrencia` sobre las barras
que ocupa. Sobre ETHUSDT 15m con H=16 los pesos suman **16.146** sobre
68.813 labels nominales (peso medio 0.23). Ese es el tamaño real del
dataset, y el reporte lo muestra siempre al lado del nominal — reportar
69.000 muestras cuando hay 16.000 efectivas es la forma silenciosa de
exagerar la solidez de un resultado.

---

## 5. Modelos (`models/forecast.py`)

### 5.1 Gradient boosting, no deep learning

Sobre datos tabulares de este tamaño el boosting sigue siendo el estado del
arte, entrena en segundos (lo que permite correr walk-forward completo
muchas veces) y es auditable: se puede sacar importancia por permutación y
explicar qué familia de features mueve la probabilidad. Un modelo que no se
puede explicar no debería mover capital apalancado.

`early_stopping=False` a propósito: el early stopping de scikit-learn parte
un set de validación **aleatorio**, lo que en una serie con labels
solapados es fuga. Se prefiere capacidad fija y conservadora
(`max_leaf_nodes=15`, `min_samples_leaf=100`, `l2=1.0`), que es
reproducible.

### 5.2 Calibración isotónica sobre OOF purgado

La calibración se ajusta sobre predicciones out-of-fold generadas con
walk-forward purgado **dentro** del train. Calibrar sobre predicciones
in-sample produce un mapa que corrige un sobreajuste que no existirá en
test, y deja el modelo peor calibrado que sin calibrar.

El reporte muestra siempre las tres columnas: modelo calibrado, modelo sin
calibrar, y baseline. Así se ve cuánto aporta cada pieza por separado.

### 5.3 Cono conformal (CQR + ACI)

Regresión cuantílica conformalizada (Romano, Patterson & Candès, 2019): se
ajustan cuantiles al `alpha/2` y `1-alpha/2`, y se corrigen con el cuantil
de los scores de conformidad medidos sobre un tramo de calibración disjunto
y posterior. Da cobertura marginal ≥ `1-alpha` **sin asumir ninguna
distribución**.

La alternativa habitual en trading —bandas de ±2σ— asume normalidad, y los
retornos de cripto tienen colas gordas: esa banda subcubre justo en los días
que importan. El reporte muestra ambas para que la diferencia sea visible.

**Honestidad sobre el supuesto**: la garantía conformal exige
intercambiabilidad, que una serie financiera no cumple estrictamente. Por
eso se activa ACI (Gibbs & Candès, 2021), que ajusta `alpha` en línea según
los fallos observados y recupera la cobertura bajo cambio de régimen. La
cobertura real se **mide** y se reporta; nunca se asume.

---

## 6. Métricas (`models/metrics.py`)

Cada target se mide con lo que puede desmentirlo:

- **Probabilidad**: Brier, Brier skill score, log loss, AUC, ECE, MCE y la
  curva de fiabilidad por buckets fijos. Lo que importa no es el accuracy:
  un modelo que dice 70% y acierta 70% es útil aunque su accuracy sea 55%;
  uno que dice 90% y acierta 60% es peligroso aunque acierte más veces.
  Los buckets fijos (no cuantiles) son deliberados: la promesa al usuario es
  "de los casos donde dije 70-80%, acerté X%", y esa frase necesita cortes
  interpretables. Los buckets con menos de 20 casos se muestran pero no
  cuentan para el gate.
- **Volatilidad**: RMSE, R², **QLIKE** y regresión de Mincer-Zarnowitz.
  QLIKE (Patton 2011) penaliza más subestimar la volatilidad que
  sobreestimarla, que es la asimetría de riesgo correcta para un
  apalancado. Mincer-Zarnowitz descompone en sesgo (`alpha`=0) y eficiencia
  (`beta`=1).
- **Intervalos**: cobertura empírica vs nominal, ancho medio y Winkler
  score. Cobertura sola no basta: un intervalo del 95% que abarca todo el
  día es inútil.

### 6.1 Diebold-Mariano

La pregunta que casi nunca se hace en proyectos de trading: la mejora sobre
el baseline, ¿es distinguible de la suerte?

Test DM (1995) con corrección de Harvey et al. (1997) y varianza
Newey-West a `H-1` rezagos, porque los forecasts a H pasos tienen errores
autocorrelacionados hasta `H-1`. Ignorarlo produce p-values artificialmente
diminutos — la forma más común de "demostrar" un edge inexistente.

---

## 7. Baselines

Ninguno es un hombre de paja; cada uno es el estándar competitivo de su
target:

| Baseline | Referencia | Rol |
|---|---|---|
| Tasa base | — | Perfectamente calibrado por construcción, cero poder discriminante. Separa "mi modelo calibra" de "mi modelo sabe algo". |
| Random walk | Hipótesis de mercado eficiente débil | El mejor predictor del retorno futuro es cero. Difícil de batir. |
| EWMA / RiskMetrics (λ=0.94) | JP Morgan 1996 | IGARCH(1,1) con parámetros fijos; estándar de industria. |
| GARCH(1,1) | Bollerslev 1986 | QMLE gaussiana. A diferencia del EWMA, revierte a la varianza incondicional: tras un shock proyecta calma. |
| HAR-RV | Corsi 2009 | Componentes diario/semanal/mensual en logs. Simple y difícil de batir. |

Los forecasts de volatilidad en log llevan **corrección de Jensen**
(`exp(µ + σ²/2)`). Sin el término de varianza quedan sistemáticamente
sesgados hacia abajo, y subestimar la volatilidad es exactamente el error
que liquida cuentas apalancadas.

---

## 8. El gate de la Fase 4 — dos criterios, no uno

Criterio de CLAUDE.md, exigido en **todas** las direcciones evaluadas (con
una sola dirección aprobada no se habilita nada, porque el dashboard ofrece
ambas):

1. **Calibración**: error medio de calibración < 10 puntos porcentuales por
   bucket.
2. **Discriminación**: AUC > 0.55 y Brier skill score > 0 contra la tasa
   base, out-of-sample.

El segundo criterio no estaba en la versión original del gate; se incorporó
al documento del proyecto tras este trabajo, porque **la calibración sola no
alcanza y conviene decirlo fuerte**. Un modelo que predice siempre la tasa
base está **perfectamente calibrado por construcción** —cuando dice 45%,
acierta 45%— y es completamente inútil: no distingue un setup de otro.
Habría pasado el gate escrito sin aportar nada.

Son dos propiedades distintas:

- **Calibración**: "cuando digo 70%, acierto 70%". Se mide con ECE y la
  curva de fiabilidad.
- **Discriminación**: "sé cuáles son los casos de 70% y cuáles los de 40%".
  Se mide con AUC y Brier skill score.

Por eso el reporte evalúa y muestra **ambos criterios por separado**
(`ExperimentResult.gate_passed()` y `.discriminates()`), y advierte
explícitamente el caso "calibra pero no discrimina" en vez de dejar que se
lea como aprobación.

Mientras cualquiera de los dos falle, el KPI se muestra en gris con etiqueta
"experimental" y no se emiten señales en vivo. La persistencia en
`BacktestRun` guarda la **peor** dirección, no el promedio: promediar
escondería que una de las dos está descalibrada.

Nota importante de lectura: el TARGET 2 (volatilidad) se evalúa aparte y
puede ser útil aunque el de dirección no lo sea — de hecho es el orden
esperado bajo eficiencia de mercado. Dimensionar TP/SL con una volatilidad
bien pronosticada ya es valor operativo real, sin necesidad de acertar la
dirección.

---

## 9. Resultados de la primera corrida completa

Run `ETHUSDT-15m-20260824143247` — 69.119 velas (2024-08-31 → 2026-08-21),
55 features, 6 folds walk-forward purgados, TP/SL 0.5σ_H, H=16, ambas
direcciones. 44.293 predicciones out-of-sample por dirección.

### TARGET 1 — Dirección: **sin edge**

| | long | short |
|---|---|---|
| Tasa base | 44.0% | 45.5% |
| Probabilidad de equilibrio | 61.6% | 61.6% |
| AUC | 0.519 | 0.533 |
| Brier skill score | −0.0028 | +0.0005 |
| Error de calibración | 4.0pp | 5.1pp |

El modelo **calibra bien y no discrimina**: reproduce la tasa base sin
distinguir setups. Con el umbral de emisión en 70% aparecen 3 señales en
44.293 barras (long) y ninguna (short) — el sistema, correctamente, casi
no habla.

El DM da "significativo" contra el baseline, pero eso hay que leerlo con
cuidado: con n=44.293 una mejora de Brier de 0.0005 sale significativa y es
**irrelevante en la práctica**. Significancia estadística no es significancia
económica; el número que decide es que el AUC no llega a 0.55.

Es el resultado que predice la hipótesis de mercado eficiente en forma
débil, y es coherente con el piso medido en §0 (EV ≈ −costo en las 24
configuraciones del barrido). **No es un bug: es el hallazgo.** La
calibración isotónica sí hace su trabajo — baja el error de 9.9pp a 4.0pp
(long) y de 14.0pp a 5.1pp (short).

### TARGET 2 — Volatilidad: **edge real y robusto**

| Modelo | RMSE | R² vs media | R² vs EWMA | QLIKE |
|---|---|---|---|---|
| **GBM (55 features)** | **0.00559** | **+0.400** | **+0.374** | **0.3963** |
| HAR-RV | 0.00617 | +0.269 | +0.237 | 0.5069 |
| EWMA / RiskMetrics | 0.00706 | +0.042 | — | 0.6708 |
| GARCH(1,1) | 0.00760 | −0.111 | −0.159 | 0.5319 |

R² out-of-sample de **0.400** contra la media y **0.374** contra el estándar
de industria, ganándole también a HAR-RV. Diebold-Mariano contra ambos con
p < 0.0001. Mincer-Zarnowitz: `alpha = +0.0004` (prácticamente insesgado),
`beta = 0.962` (prácticamente eficiente).

Que el GARCH quede último no es un error de implementación: converge bien,
pero un GARCH(1,1) sobre barras de 15m tiene solo el retorno pasado como
insumo, mientras el GBM usa rango intrabarra (Parkinson, Garman-Klass),
estructura de término de volatilidad, volumen y estacionalidad intradía.

### TARGET 3 — Cono de precio: **cobertura casi exacta**

| Nominal | Método | Cobertura empírica | Desvío | Winkler |
|---|---|---|---|---|
| 95% | **CQR + ACI** | **94.8%** | **−0.2pp** | **0.05799** |
| 95% | Gaussiano ±zσ | 91.0% | −4.0pp | 0.08513 |
| 80% | **CQR + ACI** | **79.9%** | **−0.1pp** | **0.04537** |
| 80% | Gaussiano ±zσ | 80.9% | +0.9pp | 0.04918 |

La banda gaussiana **subcubre 4 puntos al 95%** — exactamente el fallo que
predice la teoría cuando los retornos tienen colas gordas, y ocurre en la
cola, que es donde una liquidación se decide. El conformal acierta la
cobertura con un ancho apenas mayor (0.04838 vs 0.04688) y mejor Winkler en
ambos niveles.

### Importancia de features

Por familia (Δ Brier por permutación sobre test): **volatilidad** domina
(+0.00190), seguida de momentum (+0.00069) y estacionalidad (+0.00049).
Los cinco features más importantes son `rv_24h_rank`, `rv_24h`, `rsi_48`,
`donchian_pos_72h` y `vol_of_vol`. Consistente con el resto: lo que el
modelo capta es el régimen de volatilidad, no la dirección.

### Lectura conjunta

El stack **no habilita señales direccionales** — y el gate lo bloquea
correctamente. Lo que sí entrega es un pronóstico de volatilidad con skill
real y un cono de precio con cobertura verificada. Para el asistente eso ya
es valor operativo: dimensionar TP/SL y mostrarle al usuario un rango de
precio en el que confiar es útil aunque nadie sepa hacia dónde va el precio.

---

## 9-bis. Detector de régimen: HMM gaussiano (`models/hmm.py`)

El `markov.py` heredado clasifica el régimen con umbrales fijos sobre retorno
y volatilidad. Sirve de baseline, pero los umbrales son una decisión del
programador, no de los datos. El HMM gaussiano los reemplaza por un modelo
generativo: K estados ocultos, cada uno con su media y varianza sobre
`(retorno log, log-volatilidad realizada por barra)`, y una matriz de
transición estimada por Baum-Welch.

### 9-bis.1 Filtrado, no suavizado — y por qué se escribió a mano

Un HMM ofrece dos inferencias distintas sobre el estado de la barra *t*:

| | qué condiciona | ¿sirve de feature? |
|---|---|---|
| **Filtrado** `P(s_t \| x_0..x_t)` | solo el pasado | **sí** |
| **Suavizado** `P(s_t \| x_0..x_T)` | toda la serie | **no: es lookahead** |
| Viterbi | toda la serie | **no: es lookahead** |

Las APIs habituales (`predict`, `predict_proba` de `hmmlearn`) devuelven las
dos últimas. Usarlas para alimentar el KPI 1 viola la regla 5, y el bug sería
invisible: el backtest mejora y la señal en vivo no reproduce. Por eso el
módulo implementa el forward-backward propio, expone `filtered_probs` como la
única entrada válida al modelo, y deja `smoothed_probs` documentado como
herramienta de análisis histórico.

Hay un test que lo fija: mutar el futuro de la serie no cambia ni un dígito de
las probabilidades filtradas del pasado, mientras que las suavizadas sí
cambian. Es el mismo invariante que protege al feature engine.

(Además, `hmmlearn` no publica wheel para Python 3.14 y exige compilar con
MSVC. Lo que aportaba era el Baum-Welch, ~80 líneas de numpy.)

### 9-bis.2 Elección de n estados: **BIC no encuentra óptimo interior**

CLAUDE.md especifica elegir n por BIC sobre la ventana de entrenamiento. Se
implementó, se corrió sobre las 69.023 observaciones usables de ETHUSDT 15m, y
el resultado hay que decirlo tal cual es:

| n | BIC | ICL | convergió |
|---|---|---|---|
| 2 | −576.375 | −571.765 | sí |
| 3 | −630.994 | −626.516 | sí |
| 4 | −671.952 | −667.233 | sí |
| 5 | −703.539 | −698.385 | sí |
| 6 | −724.761 | −718.574 | **no** |

El BIC **decrece monótonamente**: el "óptimo" es siempre el candidato más
grande que se pruebe. La causa es doble y ninguna es un bug:

1. Con n = 69.023, la penalización `p·log n` (~700) es ruido frente a
   ganancias de verosimilitud de decenas de miles.
2. La densidad real de `(retorno, log-vol)` no es una mezcla de K gaussianas.
   Agregar estados siempre ajusta mejor porque el modelo **tesela el eje de
   volatilidad**: con n=6 salen cinco estados "lateral" que solo difieren en
   sigma.

Tres respuestas, todas en el código y en el dashboard:

- **ICL** (Biernacki, Celeux & Govaert 2000) = BIC + 2·entropía de los
  posteriores. Penaliza estados que se solapan en vez de premiar solo el
  ajuste. Acá también resulta monótono, lo que refuerza el diagnóstico en vez
  de taparlo.
- **Regla de parsimonia declarada** (`knee_n`): primer n cuya mejora marginal
  cae bajo la mitad de la primera. No es un óptimo, es un corte explícito.
- **Advertencias en el resultado** (`StateSelection.warnings`): "el criterio
  mejora en todo el rango probado", "el ganador está en el borde", "sin
  converger en n=6". El selector nunca devuelve un número pelado.

Los ajustes que no convergieron quedan fuera de la competencia: la
verosimilitud de un EM interrumpido no es comparable con la de uno convergido.

### 9-bis.3 Qué encuentra el modelo sobre ETHUSDT

Ajuste parsimonioso con n=3 sobre los dos años completos:

| estado | etiqueta | retorno/barra | vol/barra | permanencia | duración esperada | tiempo |
|---|---|---|---|---|---|---|
| 0 | ranging | +0,0008% | 0,313% | 0,989 | 23,1 h | 38% |
| 1 | volatile | −0,0018% | 0,490% | 0,990 | 25,4 h | 27% |
| 2 | ranging | +0,0004% | 0,187% | 0,994 | 38,5 h | 35% |

Dos lecturas:

1. **Los estados se separan por volatilidad, no por dirección.** Los retornos
   medios son estadísticamente indistinguibles de cero en los tres. Es el
   mismo veredicto que dio el gate de la Fase 4 desde otro ángulo: en 15m la
   dirección no es predecible, la volatilidad sí tiene estructura.
2. **Los regímenes son muy persistentes** (0,989-0,994 en la diagonal), o sea
   duraciones esperadas de 23 a 38 horas. Eso es lo que alimenta el KPI 3 —
   y por qué se muestra como rango: la duración de una geométrica tiene
   varianza enorme, la media informa poco por sí sola.

### 9-bis.4 Costo

El ajuste sobre 69k observaciones toma ~40 s por modelo y ~150 s la búsqueda
completa de n ∈ {2..6}. Es un reentrenamiento periódico (APScheduler, ventana
rodante), nunca parte del hot path del tick.

---

## 10. Reproducibilidad

```bash
cd backend

# 1. Histórico (idempotente, reanuda desde lo ya persistido)
uv run python -m bob.data.download --symbol ETHUSDT --timeframe 15m --months 24
uv run python -m bob.data.download --status

# 2. Experimento walk-forward completo
uv run python -m bob.backtest.runner --symbol ETHUSDT --timeframe 15m --folds 6

# Variantes
uv run python -m bob.backtest.runner --tp 1.0 --sl 0.5 --horizon 32 --folds 8
uv run python -m bob.backtest.runner --model logistic --rolling --no-persist
```

Cada run escribe `backend/artifacts/<run_id>.txt` (reporte legible) y
`<run_id>.json` (resultado completo, incluidos todos los buckets), y
persiste una fila en `BacktestRun`. Toda la aleatoriedad pasa por `seed`
(default 42).

Verificado, no solo declarado: dos corridas independientes con la misma
configuración produjeron reportes **idénticos línea por línea**, salvo el
runtime y el identificador del run. Es la propiedad que permite atribuir
cualquier cambio de resultado a un cambio de código o de datos, y no al
azar del entrenamiento.

---

## 11. Límites conocidos

Declarados acá para que nadie los descubra después leyendo el código:

1. **Un solo símbolo, un solo timeframe validados.** El motor es agnóstico
   por construcción y hay tests que lo verifican, pero agnóstico ≠ validado.
2. **Funding aproximado.** Constante de 0.01%/8h como costo en ambas
   direcciones. La serie real existe (`funding_history`) y no está cableada.
3. **OI y ratios long/short ausentes.** Binance solo conserva ~30 días de
   histórico, insuficiente para entrenar sobre 2 años. Hay que empezar a
   snapshotearlos desde ya para que estén disponibles más adelante.
4. **Sin datos de orderbook.** El `depth` requiere captura en vivo; la
   microestructura actual sale solo de lo que trae la kline.
5. **Los costos son un supuesto, no una medición.** 0.10% de fees + 0.04%
   de slippage. El slippage real depende del tamaño y del momento; medirlo
   requiere ejecución real, que BOB no hace por diseño.
6. **Dos años cubren pocos regímenes macro.** Los folds tempranos entrenan
   con relativamente pocos datos.

---

## Referencias

- López de Prado, M. (2018). *Advances in Financial Machine Learning*. Caps. 3, 4, 7.
- Corsi, F. (2009). A Simple Approximate Long-Memory Model of Realized Volatility. *Journal of Financial Econometrics*.
- Bollerslev, T. (1986). Generalized Autoregressive Conditional Heteroskedasticity. *Journal of Econometrics*.
- Patton, A. (2011). Volatility forecast comparison using imperfect volatility proxies. *Journal of Econometrics*.
- Diebold, F. & Mariano, R. (1995). Comparing Predictive Accuracy. *JBES*.
- Harvey, D., Leybourne, S. & Newbold, P. (1997). Testing the equality of prediction mean squared errors. *IJF*.
- Romano, Y., Patterson, E. & Candès, E. (2019). Conformalized Quantile Regression. *NeurIPS*.
- Gibbs, I. & Candès, E. (2021). Adaptive Conformal Inference Under Distribution Shift. *NeurIPS*.
- Parkinson, M. (1980); Garman, M. & Klass, M. (1980). Estimadores de volatilidad por rango.
- Rabiner, L. (1989). A Tutorial on Hidden Markov Models. *Proceedings of the IEEE*. (Forward-backward con escalado.)
- Biernacki, C., Celeux, G. & Govaert, G. (2000). Assessing a mixture model for clustering with the integrated completed likelihood. *IEEE TPAMI*.
