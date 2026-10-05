# Pre-registro — Ranking macro entre clases de activo (MAREA, fase M3)

> **Este documento se escribió y commiteó ANTES de correr cualquier ranking**,
> incluidos los triviales. Fija la pregunta, la configuración primaria, el
> número de pruebas y los criterios. El resultado va en un artefacto aparte;
> este archivo no se edita después de correr, salvo para enlazarlo.

Fecha de registro: **2026-10-05**, sobre `main` en `625f89d`.

Precedentes de forma: `PREREGISTRO_PANEL_DIARIO.md` (Fase 10) y
`PREREGISTRO_PANEL_FORWARD.md` (10-bis).

---

## 1. De dónde sale este pre-registro

### 1.1 La potencia se midió antes, con criterio fijado antes

Paso 1 de la M3 (`marea/power.py`, criterio en `d48db5c`; artefacto
`artifacts/marea-power-20261005T214336Z`). Lo que fija el diseño:

| | 23 ETFs (2014-07) | **22 sin REET (2011-11)** | 21 sin REET ni CPER (2007-11) |
|---|---|---|---|
| semanas | 638 | **776** | 984 |
| N efectivo (demediado) | 6,7 | **6,5** | 5,4 |
| MDE del IC, H=4 | 0,065 | **0,059** | 0,055 |

- Los ETFs equivalen a **unas 6–7 apuestas independientes**, no a 22.
- La dispersión del IC por fecha de una señal real (0,36) es **1,7×** la del
  nulo de permutación (0,21): las señales cargan sobre pocos factores.
- A H=13 semanas no hay potencia en ninguna variante.

**Veredicto del paso 1: potencia solo para un efecto grande.** Por el
criterio, la decisión fue de Nichelson (2026-10-05): **seguir, con 22 ETFs y
H = 4 semanas**. REET es redundante con VNQ y su exclusión compra 3 años de
historia; CPER se conserva porque es el cobre de la lente Chile.

La medición fue **ciega**: no se miró la media del IC de ninguna señal. La
elección de universo y horizonte se hizo por potencia, no por resultado —
mismo argumento que la elección del leverage en la Fase 5-bis.

### 1.2 El gate de AUC exige casi el doble de lo que esta muestra resuelve

Simulación con 22 activos y señal gaussiana (aritmética, no datos):

| IC de rango | AUC (target sobre la mediana) |
|---|---|
| 0,046 | 0,523 |
| **0,064** | **0,530** ← MDE de esta muestra |
| 0,081 | 0,540 |
| **0,110** | **0,552** ← umbral del gate |
| 0,137 | 0,566 |

El mapeo está validado por el propio repo: el panel cripto midió AUC 0,5541
con IC 0,107. **Exigir AUC > 0,55 sería exigir un IC ≈ 0,105**, casi el doble
de lo que esta muestra puede detectar, y del mismo tamaño que el mejor
resultado del proyecto sobre un universo 30 veces más ancho.

Este pre-registro **no mueve el gate**. Separa dos preguntas (§5): si el
ranking **ordena** (lo que la potencia permite responder) y si alguna
**probabilidad** que salga de él puede mostrarse como operable (regla 2, que
sigue exigiendo AUC > 0,55 y BSS > 0).

---

## 2. Hipótesis

> **H1.** Un modelo que usa solo información disponible al cierre de la
> semana `t` —precios de los ETFs y el estado de liquidez point-in-time de la
> M2— ordena los 22 ETFs por su retorno total de las **4 semanas siguientes,
> relativo a la mediana de la sección**, mejor que el mejor ranking trivial
> (momentum 12-1 y reversión a 4 semanas), fuera de muestra.

La nula no es "el modelo no ordena": es que **no ordena mejor que el ranking
trivial**. Momentum entre clases de activo es un efecto documentado que se
obtiene con una línea de código.

**Pregunta secundaria, la tesis de MAREA**: ¿agrega algo el bloque de
liquidez? Se responde comparando el modelo con su versión sin ese bloque
(§5, punto 5). Se reporta siempre, pero **no decide**: no hay potencia
declarada para una diferencia entre dos modelos casi iguales.

---

## 3. Diseño

### 3.1 Universo

Los 22 ETFs de `marea.data.catalog.ETFS` menos REET: SPY, QQQ, IWM, EFA, EEM,
FXI, EWJ, ECH, SHY, IEF, TLT, TIP, LQD, HYG, GLD, SLV, USO, UNG, DBA, CPER,
VNQ, UUP. Historia común desde **2011-11-25**, calendario común (0 días
descartados medidos en el paso 1). Universo fijo: todos existen hoy y todos
existían al inicio, así que **no hay deslistados que se pierdan** — la
supervivencia que sí existe es la de haber elegido hoy ETFs que siguen vivos,
y se declara como límite (§6.3).

### 3.2 Tiempo: decisión, entrada y salida

- **Decisión** al cierre del último día hábil de cada semana ISO (`t`).
  Toda feature usa precios hasta ese cierre y datos macro **conocidos** en
  esa fecha (`marea.pit.as_of`, vintages de ALFRED).
- **Entrada** al cierre del **día hábil siguiente** a `t`. Un día de rezago
  a propósito: computar la señal con el cierre de `t` y entrar a ese mismo
  cierre no es ejecutable.
- **Salida** al cierre del día hábil siguiente a la decisión de la semana
  `t + 4`. Tenencia de 4 semanas exactas en la misma grilla rezagada.

Diferencia declarada con el paso 1: la potencia se midió sin el día de
rezago. Es una de 20 sesiones por horizonte; no cambia el orden de magnitud.

### 3.3 Target

```
r(s,t) = retorno total (marea.returns) de s entre entrada y salida
y(s,t) = 1 si r(s,t) > mediana_transversal(r(·,t)), 0 si no
```

Tasa base 50% por construcción, neutral al mercado por construcción.

### 3.4 Features — 28, en cuatro bloques

| bloque | features | n |
|---|---|---|
| **A. precio, por activo** | retorno 4, 13 y 26 semanas; momentum 12-1; volatilidad 13 y 52 semanas; distancia al máximo de 52 semanas; momentum 12-1 / volatilidad 52 | 8 |
| **B. transversal** | rango percentil de cada feature del bloque A dentro de su fecha | 8 |
| **C. liquidez (M2)** | cambio orientado a 13 semanas de Fed neta, M2, dólar amplio, tasa real y NFCI, tal como se conocían en `t` | 5 |
| **D. descriptor del activo** | beta a SPY de 52 semanas; bloque de activo one-hot (acciones, renta fija, reserva de valor, commodities, real estate, divisas) | 7 |

- **Bloque C usa la señal cruda, no el percentil de la M2**: el percentil
  exige 3 años de historia y el compuesto existe recién desde 2020-02, lo que
  vaciaría los primeros folds. La Fed neta es NaN antes de 2014 (sin RRP
  diario); el GBM de sklearn maneja NaN, y la cobertura por fold se reporta.
- **Bloque C es igual para todos los activos de una fecha**: solo puede
  aportar a través de su interacción con el bloque D. Esa interacción
  —"con liquidez expansiva, las acciones quedan arriba de la mediana"— es
  literalmente la tesis de MAREA, y por eso el bloque D lleva la clase de
  activo.
- **No hay identificador de ticker.** Permitiría aprender "QQQ siempre gana",
  que es una deriva de la muestra y no un ordenamiento. El one-hot de bloque
  es más grueso, con un costo declarado: real estate (VNQ) y divisas (UUP)
  tienen un solo ETF, así que para esos dos el bloque *es* el ticker.
- Ninguna feature usa información posterior a `t`. Test de no-lookahead en
  los dos ejes, como en la Fase 10: mutar el futuro no altera el pasado, y
  mutar un activo no altera las features de otro en fechas anteriores.

### 3.5 Estimador

`HistGradientBoostingClassifier` con **los hiperparámetros nominales del
gate** (`bob.models.forecast._gbm_classifier`: 250 iteraciones, learning rate
0,05, 15 hojas, 100 filas mínimas por hoja, L2 1,0, sin early stopping),
semilla 42. Sin tuning. Caveat declarado: con ~17.000 filas, 100 filas por
hoja es más restrictivo en proporción que en el panel cripto (285.000).

### 3.6 Validación

Walk-forward **partido por fecha**, como la Fase 10: 6 folds, 35% mínimo de
entrenamiento, purga de H = 4 semanas a cada lado de la frontera, embargo del
1%. Todos los activos comparten frontera.

### 3.7 Incertidumbre

Bootstrap de bloques móviles **sobre fechas**, largo H = 4, 20.000
remuestreos, semilla 0 — el mismo objeto que el paso 1 y que
`bob.paper.forward_eval.coverage_interval`.

### 3.8 Costo

0,10% por lado y por activo (comisión ≈ 0 + medio spread), o sea 0,20% de
ida y vuelta por pata. Es generoso para SPY y corto para UNG, DBA o CPER;
se declara como aproximación única.

---

## 4. Configuración primaria y número de pruebas

| parámetro | valor |
|---|---|
| universo | 22 ETFs sin REET, desde 2011-11-25 |
| horizonte | 4 semanas, rebalanceo semanal |
| entrada | cierre del día hábil siguiente a la decisión |
| target | sobre la mediana transversal |
| features | 28 (§3.4) |
| estimador | GBM sklearn, hiperparámetros del gate |
| folds | 6, purgados por fecha |
| baseline primario | el **mejor** de {momentum 12-1, reversión 4 semanas}, elegido sobre el propio período de test (lo que lo hace más difícil de superar, no más fácil) |
| baseline secundario | el mismo modelo **sin el bloque C** (liquidez) |
| cartera para el criterio económico | tercio superior largo, tercio inferior corto (7 y 7) |

**1 confirmatoria**: H1 en la configuración primaria.

**8 exploratorias**, reportadas como tales pase lo que pase y sin poder
reemplazar a la primaria aunque la superen:

1. H = 1 semana
2. H = 13 semanas
3. 23 ETFs desde 2014-07
4. 21 ETFs desde 2007-11
5. sin el bloque C (liquidez) — es además el baseline secundario
6. sin el bloque B (transversal)
7. XGBoost con los mismos hiperparámetros nominales
8. entrada al cierre de `t`, sin día de rezago

**Control obligatorio**: placebo que permuta el resultado entre activos
dentro de cada fecha. Debe dar IC compatible con 0. Si no, hay una fuga y
nada de lo demás se lee.

Sub-períodos anuales: descriptivos, sin veredicto.

---

## 5. Criterios

El ranking se declara **viable** si y solo si, en la configuración primaria,
se cumplen **los dos**:

1. **Ordena.** Media del IC de Spearman por fecha > 0, con IC95% por bloques
   de fecha excluyendo 0. Es la métrica para la que se midió potencia.
2. **Supera al trivial.** Diferencia **pareada** por fecha entre el IC del
   modelo y el del mejor baseline trivial, con IC95% por bloques excluyendo 0.

Se reporta siempre, aprobado o no, y **no decide**:

3. **AUC, BSS y calibración.** La regla 2 sigue intacta: **ninguna
   probabilidad del ranking se muestra como operable sin AUC > 0,55 y
   BSS > 0**. Por §1.2, un ranking viable con IC entre ~0,06 y ~0,10 pasa 1
   y 2 y no pasa esto. Ese desenlace se lee **"ordena, pero no habilita
   mostrar probabilidades"**: el dashboard podría mostrar el **orden**
   (inclinación relativa) en gris y experimental, nunca un número de
   probabilidad.
4. **Spread neto** del tercio superior contra el inferior, con su IC. **Sin
   potencia declarada**: en la 10-bis el spread tuvo un tercio del t por
   fecha del IC, y acá la muestra es más chica. Se reporta, no aprueba ni
   reprueba.
5. **Lo que aporta la liquidez**: diferencia pareada del IC entre el modelo
   y el modelo sin bloque C. Es la tesis de MAREA y el número más
   interesante del run, pero **no tiene potencia declarada** y no decide.
6. **Rotación** por rebalanceo, **fracción del oráculo capturada** y desempeño
   condicionado al régimen de liquidez de la M2 (descriptivo).

### Qué significa cada desenlace

| resultado | lectura | consecuencia |
|---|---|---|
| falla 1 | no hay orden detectable a esta potencia | MAREA macro queda como monitoreo; se cierra la M3 |
| pasa 1, falla 2 | el ranking trivial captura lo que hay | no se construye el modelo; el momentum puede mostrarse como descriptivo |
| pasa 1 y 2 | viable | habilita **validación forward** (fase aparte, pre-registrada), no operar |
| placebo con IC ≠ 0 | fuga | nada se lee hasta encontrarla |

**Aprobar habilita medir forward, no operar** — la misma lección de §8.1 de
`PROBABILITY_MODEL.md` y de la Fase 10.

---

## 6. Trampas conocidas

### 6.1 La macro mira el futuro si se alinea por fecha del dato

M2 de agosto se publica a fines de septiembre; NFCI se re-estima entero cada
semana. El bloque C se arma con `marea.liquidity.realtime`, que es la misma
pieza cuya invariante point-in-time se verificó fila por fila sobre la DB
real en la M2. Usar la serie revisada sería lookahead invisible.

### 6.2 Pocos activos, muchas features

28 features sobre 22 activos y ~770 fechas. Un árbol puede memorizar la
historia de una clase de activo. Las defensas son las del diseño —sin ticker,
hiperparámetros conservadores sin tuning, folds purgados— y el control es el
placebo.

### 6.3 Supervivencia por elección del universo

Los 22 ETFs se eligieron en 2026 entre los que existen. No hay deslistados en
la muestra porque se eligieron grandes y vivos; un ranking que sobrepondera
"lo que sobrevivió" no se ve desde adentro. Es un límite declarado, más
chico que en cripto porque son clases de activo y no emisores.

### 6.4 El factor común se cuela por la clase de activo

Con target demediado, en una semana de risk-off todas las acciones caen bajo
la mediana. Un modelo que solo aprenda "acciones vs bonos según el momentum
del SPY" predice el mercado disfrazado de ranking. Por eso se reporta el IC
condicionado al signo del retorno de SPY en el horizonte, y el beta a SPY
entra como feature explícito para que sea atribuible.

### 6.5 Fondos de futuros

USO, UNG, DBA, CPER y UUP llevan costo de roll. Es el retorno de quien los
compra, que es lo que se ordena; no es el precio del commodity.

---

## 7. Lo que este pre-registro NO pretende

- **No habilita operar.** MAREA sugiere inclinaciones entre clases de activo,
  no operaciones, y un aprobado habilita medir forward.
- **No mueve el gate** ni la regla 2 (§1.2 y §5.3).
- **No toca el módulo cripto.** El código vive en `marea/`; importa
  `bob.models.validation` y `bob.models.forecast` sin modificarlos (regla 12).
- **No pronostica la liquidez.** El bloque C usa el estado medido de la M2
  como feature; la M2 sigue siendo monitoreo.

---

## Resultado (enlace, agregado después de correr)

Artefacto: `backend/artifacts/marea-ranking-20261005T221954Z.txt|json`.
**No viable**: IC +0,0359, IC95% [−0,0075, +0,0779], no pasa el criterio 1.
Una desviación declarada en una exploratoria (21 ETFs sin Fed neta); la
primaria corrió tal como está registrada arriba.
