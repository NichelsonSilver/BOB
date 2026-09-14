# Pre-registro — Filtro de régimen (Fase 5-bis)

> **Este documento se escribió y commiteó ANTES de correr la medición.**
> Es el requisito que el `CLAUDE.md` impone a cualquier barrido: *"si algún
> día se retoma, hay que fijar el criterio y el número de pruebas ANTES de
> correrlas"*. Lo que sigue fija los dos. El resultado va en un artefacto
> aparte; este archivo no se edita después de correr, salvo para enlazarlo.

Fecha de registro: **2026-09-14**, sobre `main` en `35122c9`.

---

## 1. Qué se pregunta, y por qué esta pregunta y no otra

La Fase 5 cerró con el target de volatilidad validado y el direccional
refutado. Al auditar el "techo de la sigma" que la Fase 5 dejó abierto,
aparecieron tres hechos que parten el producto en dos mitades con respaldo
muy distinto:

1. **No hay un evento de sub-pronóstico, hay tres** en 13 días (09-03, 09-04,
   09-11). El punto 13 de la Fase 5 registra solo el del 09-11. Uno cada
   cuatro días no es una rareza.
2. **Donde falla la sigma falla también el cono.** En las 42 barras con
   volatilidad realizada por encima del techo, el cono al 80% cubrió **0%** y
   el de 95% cubrió 50%. El 92,7% global lo sostienen las 1.116 barras calmas.
3. **Pero el modelo ordena bien.** AUC **0,875** para separar el decil más
   volátil; evitando el 20% de barras de mayor sigma pronosticada se captura
   el **82%** de las barras peligrosas. Lo que no anticipa es el *arranque*:
   dos de los tres eventos empezaron con la sigma en el percentil 45 y 51.

La lectura es que el modelo sabe **en qué régimen estás** y falla en la
**magnitud de la cola**. Eso importa porque los dos productos posibles se
apoyan en propiedades distintas:

| producto | se apoya en | estado de esa propiedad |
|---|---|---|
| niveles TP/SL dimensionados por sigma | **magnitud** | rota en la cola |
| filtro "ahora no entres / bajá el leverage" | **orden** | validada |

Este pre-registro mide **solo el filtro**, porque es el único que se apoya en
lo que sobrevivió dos validaciones.

**Lo que este documento NO pretende.** No busca edge direccional, no lo
necesita y no lo va a encontrar: la liquidación es un evento de movimiento
adverso, no de dirección acertada. Un filtro que funcione no convierte el EV
en positivo —eso sigue siendo álgebra imposible sin edge (Fase 5, hallazgo
(a))—. Reduce la probabilidad de ruina, que es otra cosa, y es la que le
importa a un usuario que ya fue liquidado una vez.

---

## 2. Hipótesis

> **H1.** La sigma pronosticada por el modelo, disponible causalmente en la
> barra `i`, identifica las barras cuya excursión adversa posterior llega a
> liquidar una posición apalancada, **mejor de lo que lo hace un baseline de
> volatilidad** (EWMA), a la misma tasa de rechazo.

La hipótesis nula no es "el filtro no reduce liquidaciones". Un filtro que
rechaza el 20% de las barras al azar evita el 20% de las liquidaciones por
construcción. **La nula es que el filtro no hace mejor que eso.**

---

## 3. Diseño de la medición

### 3.1 El setup simulado

Misma convención que el gate, para que los números sean comparables: se entra
al **`open` de la barra `i+1`** y se recorren las barras `i+1 .. i+H` con
`H = 16`. Sin esa convención compartida, cualquier diferencia con el gate
sería atribuible al simulador.

### 3.2 Excursión adversa máxima (MAE)

Por barra y por dirección, una sola cantidad que no depende del leverage:

```
long :  MAE = 1 − min(low [i+1 .. i+H]) / entry
short:  MAE = max(high[i+1 .. i+H]) / entry − 1
```

### 3.3 Liquidación

Con margen aislado, la distancia relativa a la liquidación sale de la misma
fórmula de `models/projection.py` que el dashboard ya le muestra al usuario,
así que no se reimplementa acá:

```
long :  d(L) = (1/L − MMR) / (1 − MMR)
short:  d(L) = (1/L − MMR) / (1 + MMR)
```

**Hay liquidación si `MAE ≥ d(L)`.** Tratar la liquidación como un umbral
sobre el MAE —en vez de resimular por leverage— es deliberado: da la curva
completa de leverage con una sola pasada y sin la tentación de elegir el
leverage después de ver los resultados.

Se hereda el conservadurismo declarado de `projection.py`: margen aislado,
una sola posición, sin margen agregado a mano, sin fees de cierre.

### 3.4 El filtro, y su trampa de lookahead

El filtro rechaza la barra `i` si `sigma_oof[i] ≥ umbral_q(i)`.

**`umbral_q(i)` es el cuantil `q` de las sigmas pronosticadas estrictamente
anteriores a `i`**, en ventana expansiva con warm-up mínimo. Esto no es un
detalle: tomar el cuantil sobre la muestra completa usaría el futuro para
fijar el umbral de hoy, que es exactamente el lookahead de la regla 5, y
sería invisible —el filtro se vería mejor de lo que es y nada fallaría—.

La `sigma_oof` es la predicción **out-of-fold** del walk-forward purgado con
embargo, la misma maquinaria de `models/validation.py` que usó el gate. No se
admite ninguna sigma ajustada sobre la muestra en que se evalúa.

### 3.5 La métrica: lift

```
lift = (fracción de liquidaciones evitadas) / (fracción de barras rechazadas)
     = P(rechazada | liquida) / P(rechazada)
```

Un filtro aleatorio da **exactamente 1,0** a cualquier tasa de rechazo. Por
eso el listón es 1 y no 0, y por eso la métrica es comparable entre valores
de `q`: normaliza por lo que el filtro cuesta.

### 3.6 Incertidumbre

Los setups **se solapan**: con `H = 16`, dos consecutivos comparten 15 de 16
barras. Los ICs se calculan por **bootstrap de bloques móviles de largo H**,
reutilizando la maquinaria de `paper/forward_eval.py`, que es la misma
corrección que en la Fase 5 dio vuelta la lectura del cono. Tratar los
eventos como independientes encogería el error estándar ~4× y convertiría
ruido en hallazgo.

---

## 4. Configuración primaria — fijada antes de correr

| parámetro | valor | por qué |
|---|---|---|
| leverage `L` | **20x** | por **potencia**, ver abajo. Además es el tope del slider del dashboard |
| cuantil `q` | **0,80** | rechaza 1 de cada 5 barras: costo tolerable para un usuario de pocas entradas |
| dirección | **long y short agrupadas** | la liquidación no distingue; separarlas duplicaría las pruebas sin cambiar la pregunta |
| horizonte `H` | **16 barras** | el del gate |
| baseline primario | **EWMA** | es el que el proyecto trata como "el número comparable" (`R2_vs_ewma`) |
| MMR | **0,005** (tier 1) | `venues.py`, notional < 50k USDT |

### Cómo se eligió el leverage, y por qué eso no es hacer trampa

Se contaron los eventos de liquidación por leverage **antes** de fijar el
pre-registro, sobre las 71.328 barras de ETHUSDT 15m:

| leverage | distancia (long) | eventos long | eventos short | efectivos (÷H) |
|---|---|---|---|---|
| 5x | 19,60% | 15 | 0 | ~1 |
| 10x | 9,55% | 76 | 92 | ~10 |
| **20x** | **4,52%** | **1.540** | **1.113** | **~166** |
| 25x | 3,52% | 2.881 | 2.261 | ~321 |
| 50x | 1,51% | 15.521 | 14.762 | ~1.893 |

Mirar la **potencia** antes de registrar es práctica correcta, y el proyecto
ya la aplicó (el cálculo de 288 → 1.150 bloques de la Fase 5). Lo que sería
trampa es mirar el **resultado** —el lift— y después elegir el leverage que
mejor se ve. Por eso el conteo de eventos se hizo y quedó escrito acá, y la
medición del lift todavía no se había corrido cuando este archivo se
commiteó.

A 5x y 10x la muestra no tiene potencia para resolver nada; se reportan
igual, **etiquetados como sin potencia**, porque ocultarlos sería elegir qué
mostrar después de ver.

---

## 5. Criterios de aprobación — pre-registrados

El filtro se declara **viable** si y solo si, en la configuración primaria,
se cumplen **los dos**:

1. **Mejor que el azar.** `lift > 1` con IC95% por bloques que **excluye 1,0**.
2. **Mejor que el baseline.** `lift_modelo > lift_ewma`, con el IC95% por
   bloques de la **diferencia pareada** excluyendo 0.

Se reporta siempre, aprobado o no:

3. **El costo del filtro**: qué fracción de barras *que no liquidaban* se
   rechazaron. Un filtro con lift alto que rechaza toda la actividad no es
   utilizable, y ese número es el que lo delata.

**Los dos criterios, no uno**, por la misma razón que el gate pide
calibración *y* discriminación: superar solo el azar no demuestra que el
modelo aporte algo sobre la regla trivial de "no entres cuando viene
volátil", que un EWMA da gratis.

### Qué refutaría H1

Que `lift_modelo` no se distinga de `lift_ewma`. Ese resultado diría que la
skill de volatilidad del modelo —real contra EWMA en RMSE y QLIKE— **no se
traduce** en mejor identificación del riesgo de ruina, y que el filtro se
puede construir con tres líneas de EWMA sin todo el stack. Sería un resultado
negativo publicable y cerraría la opción B.

---

## 6. Número de pruebas — declarado

- **1 prueba confirmatoria**: la configuración primaria de §4.
- **Exploratorias, declaradas de antemano y reportadas como tales**:
  `L ∈ {5, 10, 20, 25, 50}` × `q ∈ {0,70, 0,80, 0,90}` × {long, short,
  agrupadas}, más los baselines GARCH y HAR. Ninguna de ellas puede declarar
  viable el filtro por sí sola, y ninguna se reporta sin su etiqueta.

El veredicto se lee **solo** de la confirmatoria. Las exploratorias sirven
para describir la forma de la superficie y para detectar incoherencias (por
ejemplo, un lift que no crece con el leverage sería señal de bug, no de
hallazgo).

---

## 7. Qué NO decide esta medición

Aunque el filtro apruebe:

- **No habilita el KPI 1.** El direccional sigue sin pasar el gate y sigue en
  gris (regla 2). Este filtro no dice hacia dónde entrar.
- **No vuelve positivo el EV.** Sigue siendo −costo sin edge direccional.
- **No arregla el techo de la sigma.** Esa es la opción A, y se apoya en la
  propiedad que sigue rota; si el filtro aprueba, A pasa a tener sentido,
  no antes.
