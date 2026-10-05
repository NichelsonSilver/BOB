# Pre-registro — El filtro de régimen leído por un operador con stop

> **Este documento se escribió y commiteó ANTES de correr la medición**, en
> el mismo commit que el código que la corre (`models/stopfilter.py`,
> `backtest/stopfilter_runner.py`). Este archivo no se edita después de
> correr, salvo para enlazar el artefacto.

Fecha de registro: **2026-10-05**, sobre `main` en `caacd0b`.

---

## 1. Por qué

El filtro de régimen (Fase 5-bis, `PREREGISTRO_FILTRO_RIESGO.md`) se validó
contra **liquidaciones a 20x sin stop**. Desde el 05-10 la página Riesgo se
lo muestra a Nichelson, que opera **con stop y a ≤5x**. A ese leverage la
liquidación no es su riesgo: a 5x no hubo un solo evento en dos años.

Para ese uso, la interfaz afirma hoy dos cosas que **nadie midió**:

1. `RegimeFilter.tsx` dice que, en régimen elevado, "el mismo stop salta
   más seguido".
2. La calculadora muestra "P(el ruido toca el stop) ≈ X%". Ese número sale
   de `projection.prob_touch` sobre la sigma pronosticada. Su propio
   docstring lo declara una aproximación que **subestima**, pero nunca se
   midió cuánto, ni si subestima distinto según el régimen.

Una frase de la interfaz que no sale de una medición es lo mismo que una
cifra inventada en un memo. Este pre-registro mide las dos.

## 2. Observación de diseño: un stop es una liquidación a otra distancia

"La excursión adversa llegó a `d` dentro del horizonte" es exactamente el
evento que `riskfilter.liquidates` cuenta, con `d = distancia de
liquidación`. Por eso se reutiliza todo:

- la misma excursión (entrada al `open` de `i+1`, barras `i+1..i+16`);
- la misma sigma **out-of-fold** del walk-forward purgado;
- el mismo umbral causal en ventana expansiva (q = 0,80, warm-up 2.000);
- el mismo lift y el mismo bootstrap por bloques de H barras.

Lo único que cambia es el umbral del evento. Hay un test que fija la
equivalencia: con `d` igual a la distancia de liquidación a 20x, los eventos
son idénticos.

## 3. Hipótesis

> **H1.** Con un stop a distancia fija, el filtro (sigma del modelo ≥ su
> cuantil causal 0,80) concentra las barras donde el stop salta mejor que el
> azar, **y** mejor que el mismo filtro hecho con EWMA.

> **H2 (descriptiva, con regla de decisión).** La `P(toque)` de la
> calculadora tiene una brecha observado − predicho que depende del régimen.
> La sospecha declarada es que subestima, y más en el régimen elevado
> (techo de la sigma, Fase 5 punto 13).

## 4. Configuración fijada

| | |
|---|---|
| datos | ETHUSDT 15m hasta **2026-09-14T00:45 UTC**: el mismo corte del artefacto de la 5-bis, aunque la DB ya llega a octubre |
| sigma | `price+deriv`, `gbm`, folds por defecto, H = 16 (4 h) |
| stop primario | **1,0%** (cerca del stop típico en la calculadora) |
| filtro | q = 0,80, baseline EWMA |
| bootstrap | 5.000 remuestreos, bloques de 16 barras, semilla 0 |

## 5. Criterios

**C1 (confirmatoria).** Pasa si se cumplen los dos:

- el IC95% del lift excluye 1,0;
- el IC95% de la diferencia **pareada** de lift contra EWMA excluye 0.

Es el mismo criterio doble de la 5-bis. Se reporta además la razón de tasas
de stop saltado, elevado contra normal, que es la cifra que se le muestra al
usuario.

**C2 (regla de decisión, no aprueba ni reprueba).** La brecha observado −
predicho de `P(toque)` se calcula en tres conjuntos: todas, normal y elevado.
IC95% por bloques, remuestreando la serie entera para no romper las rachas.

- Si en un régimen **el IC excluye 0 y |brecha| > 5 pp** → la calculadora
  **se corrige** en ese régimen y muestra la frecuencia medida al lado.
- Si no → queda como está, con la brecha medida citada.

Se corrige según lo que salga, en cualquiera de las dos direcciones.

**Si C1 no pasa**, la frase "el mismo stop salta más seguido" se **saca** de
`RegimeFilter.tsx`.

## 6. Control obligatorio

Con la misma sigma y los mismos filtros, el runner recalcula el lift de
**liquidación a 20x**. Tiene que dar **3,077**, el valor del artefacto
`riskfilter-*-20260914202117`. Si no reproduce, la sigma out-of-fold no es la
misma y **la medición es inválida**, pase lo que pase con C1 y C2.

## 7. Exploratorias declaradas (no deciden)

- **Stops a 0,5% y 2,0%**, con q = 0,80: lift, diferencia contra EWMA y
  brecha de calibración.
- **q = 0,70 y q = 0,90**, con stop a 1%.
- **Expectancy en R** de un trade con stop a 1% y objetivo 2R, neto de
  0,14%, en las dos direcciones, separada por régimen. Se reporta también
  la tasa de pérdidas peores que la planeada (gap a través del stop) y la
  peor pérdida en R.

  Sin edge, la expectancy es −costo/riesgo por álgebra en cualquier régimen,
  así que se espera que **no** cambie con el régimen. Si cambia, es por la
  cola: gaps a través del stop. Las convenciones conservadoras del simulador
  restan ~0,01 R adicional, medido sobre paseos aleatorios
  (`tests/test_stopfilter.py`).

## 8. Lo que esto NO cambia

- El filtro no da dirección y el KPI 1 sigue en gris.
- El EV no se vuelve positivo.
- El filtro original y su artefacto no se tocan. Esta es una lectura nueva
  del mismo objeto, no un reemplazo.
- No se toca la corrida forward del panel (regla 12).

---

**Resultado (enlace agregado después de correr, sin tocar lo de arriba):**
`backend/artifacts/stopfilter-ETHUSDT-15m-price+deriv-gbm-20261005170642.txt|.json`.

- El control **reproduce** (lift 20x = 3,077).
- **C1 PASA**: lift 1,701, IC95% [1,654; 1,754]; diferencia contra EWMA
  +0,189, IC95% [+0,149; +0,234]. El stop salta ×2,03: 55,9% de los setups
  en elevado contra 27,5% en normal.
- **C2: la calculadora queda como está.** La brecha es −0,8 pp en normal y
  −1,3 pp en elevado: sobreestima levemente y no subestima. La sospecha
  declarada en §3 resultó falsa a esta distancia.
- La expectancy no cambia con el régimen (−0,140 R contra −0,142 R), y no
  hubo pérdidas peores que la planeada.
