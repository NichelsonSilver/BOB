# Pre-registro — Reglas técnicas: barrido de liquidez y order block

> **Este documento se escribió y commiteó ANTES de correr la medición**, en
> el mismo commit que el código que la corre (`signals/technical.py`,
> `models/technical_rules.py`, `backtest/technical_runner.py`). El resultado
> va en un artefacto aparte. Este archivo no se edita después de correr, salvo
> para enlazar el artefacto.

Fecha de registro: **2026-10-05**, sobre `main` en `bce8d02`.

---

## 1. Qué se pregunta y por qué

Nichelson pidió (05-10) un asistente para operar ETH **ahora**, con su propio
panel, con análisis técnico: soportes y resistencias, tomas de liquidez,
order blocks, RSI y medias móviles. Tiene ~500 USD.

Parte de esa lista **ya está medida y no anticipa la dirección**. RSI, EMAs,
ATR, VWAP y estructura de máximos y mínimos son features del gate de la
Fase 4. Con 55 features de precio, el AUC fue 0,519 / 0,533 sobre 720 días de
ETHUSDT 15m. Ese resultado no se vuelve a discutir acá: RSI y medias entran
al panel como **contexto descriptivo**, no como señal.

Lo que **no** está medido son las dos reglas de estructura que la lista trae.
Son **eventos discretos**, no features por barra, y por eso el gate no las
evaluó:

- **Barrido de liquidez** (*liquidity sweep*): el precio perfora un máximo o
  un mínimo previo no tomado, donde descansan los stops, y cierra de vuelta
  adentro. Se opera la reversión.
- **Order block**: la última vela contraria antes de un desplazamiento que
  rompe estructura. Se opera el primer retorno del precio a esa zona.

La pregunta es la que importa con 500 USD: **¿operadas tal como se describen,
con costos reales, ganan más de lo que pierden?** No se pregunta si "aciertan
la dirección" ni si "funcionan a veces". Se pregunta si tienen **expectancy
neta positiva**.

## 2. Hipótesis

> **H1 (por regla y por timeframe).** La expectancy neta por trade, medida en
> R (unidades del riesgo asumido), es mayor que cero.

La nula no es "la regla no hace nada". Una regla sin información, operada con
stop y objetivo sobre un camino sin deriva, tiene expectancy bruta 0 **por
álgebra** (Fase 5, hallazgo (a)). Su neta es −costo/riesgo. Para pasar, la
regla tiene que superar eso **y además** cero.

## 3. Definiciones fijadas

Todo es causal. Hay tests que mutan el futuro y exigen que el pasado no
cambie (`tests/test_technical.py`).

**Pivote:** extremo estrictamente mayor que las `k = 3` barras anteriores y
mayor o igual que las 3 siguientes. **Se conoce recién al cierre de `j + 3`**
y antes de eso no existe para ninguna regla.

**Zona de liquidez:** pivote confirmado, no tomado, con menos de 192 barras.
Deja de serlo cuando una barra lo perfora, sea con barrido o con ruptura.

**Barrido:** la barra `i` perfora una o más zonas confirmadas hasta `i − 1` y
cierra de vuelta del lado de adentro de **todas** las que tomó. Barrer máximos
da SHORT y barrer mínimos da LONG. Si una vela barre los dos lados, no hay
señal.

- Entrada: `open` de `i + 1`.
- Stop: mecha del barrido ± 0,1·ATR(14).

**Order block alcista:** en la barra `i` se cumplen las dos condiciones:

- el cuerpo es al menos 1,0·ATR(14) de `i − 1`;
- es el **primer cierre** por encima del último pivote alto confirmado
  (ruptura de estructura).

La zona es el rango de la última vela bajista entre `i − 5` y `i − 1`. Vive
96 barras. Muere si una barra abre por debajo de ella o al primer toque. El
bajista es simétrico.

- Entrada: orden límite en el borde cercano, llenada al tocar. Si la barra
  abre adentro de la zona, se llena al `open`.
- Stop: borde lejano ± 0,1·ATR.

**Operación (configuración primaria):**

| parámetro | valor |
|---|---|
| objetivo | **2 R** |
| salida por tiempo | **48 barras** (12 h en 15m, 48 h en 1h) al cierre |
| costo | **0,14%** round-trip (taker ida y vuelta + slippage, el del gate) + funding 0,01%/8h por barra abierta |
| piso de riesgo | stop a **≥ 0,28%** (2× costo). Más cerca, el costo se come más de la mitad del riesgo y el setup no es operable |

**Convenciones conservadoras, todas en contra de la regla:**

- Si una vela toca stop y objetivo, cuenta el stop.
- En la vela de llenado de una límite solo se mira el stop.
- Un gap a través del stop sale al `open`.
- Un gap a través del objetivo no paga el gap.

## 4. Pruebas confirmatorias

**Cuatro:** {barrido, order block} × {15m, 1h}. ETHUSDT 15m de la DB hasta
**2026-09-14T00:45 UTC** (71.344 velas, 0 huecos). El 1h se arma agregando
horas UTC completas y descarta cualquier hora a la que le falte una vela. Los
mismos parámetros en barras para los dos timeframes.

**Criterio:** la regla pasa en un timeframe si el **límite inferior del IC
de la expectancy en R excluye 0 por arriba**. El IC se calcula así:

- Nivel **98,75%**: Bonferroni, 0,05/4.
- Bootstrap de **bloques móviles de H = 48 barras** sobre la serie de barras.
  Los trades se solapan; remuestrearlos sueltos encoge el intervalo. Es la
  misma convención que el cono y el filtro de régimen.
- 5.000 remuestreos, semilla 0.

**Potencia, medida antes de mirar un solo resultado.** Solo se contaron
eventos sobre los datos reales:

| | 15m | 1h |
|---|---|---|
| barridos | 4.560 | 1.122 |
| toques de order block | 2.010 | 526 |

El piso de riesgo va a descartar una parte. Con desvío ≈ 1,4 R por trade
(resultados cerca de −1 y +2), el efecto mínimo detectable queda en
**~0,07 R** para los barridos de 15m y en **~0,2 R** para los order blocks
de 1h. Con **menos de 150 trades** la prueba sale **SIN POTENCIA**, no
NO PASA.

## 5. Control obligatorio: placebo

Cada trade de cada prueba se vuelve a sortear en una barra al azar, con su
misma dirección y su mismo riesgo en ATR. Eso rompe el vínculo con la
estructura y deja todo lo demás igual, incluido el simulador. **Si algún
placebo pasa el criterio, la medición entera queda inválida**: lo que
"ganaría" sería la plomería. Lo esperable es ≈ −costo/riesgo, y hay un test
que lo verifica sobre un paseo aleatorio.

## 6. Exploratorias declaradas (no deciden)

Se reportan con IC 95% y no reemplazan a ninguna primaria. Con ~26 pruebas,
que alguna salga positiva por azar es lo esperable:

- R = 1 y R = 3;
- solo a favor de la EMA200;
- RSI ≤ 40 para LONG y ≥ 60 para SHORT;
- sin piso de riesgo;
- barrido de máximos o mínimos iguales (≥ 2 niveles a la vez);
- order block con entrada maker;
- 4h.

Cada una para las dos reglas y para 15m y 1h, donde aplique.

## 7. Qué se hace con cada desenlace

- **Pasa en algún timeframe** → el panel muestra esa regla con su
  expectancy medida, su IC y su win rate al lado, etiquetada **"validada
  offline — sin forward"**. La Fase 5 enseñó que offline y forward no son lo
  mismo, así que lo siguiente es su validación forward, no "operar tranquilo".
- **No pasa** → el panel dibuja igual las zonas, porque son el mapa que
  Nichelson ya mira. Al lado va la expectancy medida, que va a ser negativa, en
  vez de una flecha de "entrá acá". Un dibujo sin su número es la forma más
  barata de inflar un KPI.
- **Sin potencia** → se dice así y no se redondea hacia ningún lado.

**Lo que esto NO cambia, pase lo que pase:**

- La regla 1: nada ejecuta órdenes.
- El KPI 1 sigue en gris.
- El filtro de régimen sigue mandando sobre el leverage.
- Con 500 USD, el riesgo por trade se fija en un porcentaje chico de la cuenta
  (1–2%). El tamaño sale del stop, no del leverage.
- Nada de esto toca el árbol de la regla 12 ni la corrida forward del panel.

---

**Resultado (enlace agregado después de correr, sin tocar lo de arriba):**
`backend/artifacts/technical-ETHUSDT-20261005T131229Z.txt|.json` — las
cuatro confirmatorias **NO PASAN**; ningún placebo pasa, así que la medición
es válida.
