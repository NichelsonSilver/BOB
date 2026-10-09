# M6.5 — Textos de MAREA para revisar

> Entrega de la M6.5 (`docs/M6_ALCANCE.md` §5): la lista de los textos que lee
> un usuario en las páginas simples. Los textos se leyeron **en la página real**
> (Playwright sobre la app con datos del 2026-10-09), no en el código. Las
> cifras de los ejemplos son las de ese día y salen de la API: si cambian los
> datos, cambia la frase y no el texto fijo.
>
> Columna "cambio": ✏️ = se corrigió en esta etapa, — = se deja como estaba.

## 1. Portada y Panorama (`components/marea/Cards.tsx`)

Cada tarjeta tiene un número, una frase de qué significa y una de qué NO
significa.

| tarjeta | qué significa | qué NO significa | cambio |
|---|---|---|---|
| Liquidez global — **Débil** | "La liquidez global avanza más lento que en el 82% de las semanas anteriores." | "Es un estado del momento, no un pronóstico de precios. La lectura suele cambiar en unas 3 semanas." | ✏️ decía "dura unas 3 semanas **en promedio**": el 3 es la mediana y estaba escrito en el TSX. Ahora sale de `persistence.median_run_weeks` |
| Dinero listo para cripto — **312.2** mil millones | "Dólares digitales en circulación: dinero que ya está dentro de cripto, disponible para comprar." | "Que entre dinero no garantiza que suba el precio." | — |
| Ciclo de BTC — **48** / 100 | "Dónde está el precio de BTC dentro de su propia historia: cerca de 0 fue zona baja del ciclo; cerca de 100, euforia." | "No dice hacia dónde va el precio." | ✏️ en el detalle, los valores del techo de 2021 (0.867 / 0.756) estaban escritos a mano. Ahora la API los calcula en esa fecha |
| Riesgo de recesión EE.UU. — **13%** | "Probabilidad de recesión según la curva de tasas, con el modelo que publica la Fed de Nueva York." | "Ojo: llegó a 71% en 2023-05 y la recesión no llegó." | ✏️ los coeficientes del probit vienen de `marea/recession.py`. Se quitó del detalle "760 meses, 0.23 pp", que estaba escrito a mano y sin fuente en la API |
| Clases de activo — **Dólar** | "Arriba: Dólar +5.1% y Commodities +1.0%. Abajo: Oro y plata -4.1% y Inmobiliario (REITs) -2.6%. Comparado con el ETF típico." | "Dice a quién le fue mejor, no a quién le va a ir mejor: se midió si este orden anticipa las semanas siguientes y no se encontró que lo haga." | ✏️ decía "medido, este orden **no se sostiene**", y eso afirma más de lo que se midió: la M3 no encontró orden a esa potencia, pero no probó que no lo haya. También se sacó "(REITs) (-2.6%)", con paréntesis dobles, y "la M3" del detalle |
| Estudio del panel cripto — **7 / 90** | "Validación en curso: … Al ritmo actual (cuenta el 43% de los días), la primera revisión llega el 2027-04-17." | "El avance no dice si el modelo funciona: el resultado no se mira antes de esa revisión." | ✏️ antes la fecha ocupaba el lugar de "qué NO significa", y 7/16 = 43,75% salía redondeado a "44%" |
| Amplitud cripto — **76%** | "De las 167 criptos con liquidez suficiente, esa fracción está por encima de su tendencia larga." | "Mide cuán extendida está la subida, no cuánto va a durar." | ✏️ las referencias 7.6% (2022-11-21) y 97.2% (2024-12-05) estaban escritas a mano. Ahora las calcula la API |
| Temporada de altcoins — **64** / 100 | "Cuántas de las 50 altcoins más negociadas le ganaron a BTC en los últimos 90 días. Desde 75 se habla de temporada de alts; bajo 25, de BTC." | "Dice a quién le fue mejor en 90 días, no cuál va a subir." | ✏️ no tenía frase de qué NO significa |
| Apalancamiento (funding) — **1.9** % al año | "Lo que pagan, en un año, los que apuestan al alza en BTC con dinero prestado. Hoy es más bajo que en el 83% de los días desde 2019." | "Mide cuánto apalancamiento hay a favor de la subida, no hacia dónde va el precio." | ✏️ **error de contenido**: el detalle decía "suma diaria de **liquidaciones**", pero `marea/funding.py` suma las **tasas de funding**. Además el percentil se decía al revés ("más que el 16%") y ocupaba el lugar de "qué NO significa" |
| Opciones de BTC — **0.59** puts por call | "Contratos de protección a la baja abiertos por cada apuesta al alza, en Deribit." | "Sin historia todavía (4 fotos desde 2026-10-07): no se puede decir si es alto o bajo." | — |

**Regla nueva para los percentiles de las tarjetas:** se dicen hacia el lado que
corresponde ("más alto que el 84%" o "más bajo que el 83%", nunca "más alto
que el 16%") y se **truncan**, igual que el de liquidez. La función de
redondeo `share` queda solo para fracciones que no son rangos.

## 2. Señal 15m, lectura en texto (`bob/models/narrative.py`)

| texto | cambio |
|---|---|
| Todos los números | ✏️ ahora con punto decimal ("2,686.29", "±0.76%"), igual que el resto del dashboard. Antes decía "2.686,29" al lado de un encabezado con "2,686.29" (pendiente desde la M6.1) |
| "medias y RSI no anticiparon dirección en el gate (AUC 0,52)" | ✏️ → "el modelo de dirección, que usa estas mismas medidas, no pasó la prueba (ver Laboratorio)". La cifra estaba escrita a mano y el gate no midió medias y RSI por separado |
| "BOB no tiene una ventaja direccional demostrada: el KPI de Seguridad sigue sin pasar el gate." | ✏️ → "MAREA no tiene una ventaja demostrada para la dirección: el modelo que la pronostica no pasó la prueba." |

## 3. Otras cifras escritas a mano que se sacaron

| dónde | antes | ahora |
|---|---|---|
| Riesgo por trade, nota de rachas | "pierden −0,15 a −0,35 R por trade" | el rango sale del artefacto técnico por `/api/lab/experiments` |
| Mapa técnico, nota de RSI y medias | "(AUC 0,52)" | sin cifra; remite al Laboratorio |
| Riesgo por trade, Mapa y Filtro de régimen | "1,5×", "0,1·ATR", "1,00" con coma | con punto (son constantes de diseño, no medidas) |

## 4. Glosario (Ajustes)

`components/marea/Glossary.tsx` tiene 29 términos en tres grupos: lecturas de
la portada, cripto, y cómo se mide. Tiene buscador y **no lleva cifras**. Lo
que más conviene revisar:

- **"IC"** aparece con dos sentidos en el proyecto: coeficiente de información
  e intervalo de confianza (IC95%). El glosario los separa, y en las frases
  simples del Laboratorio la sigla ya no aparece (desde la M6.4).
- **"Sin datos suficientes / sin potencia"** se define como "todavía no se
  puede saber", no como "no hay efecto". Es la misma distinción que motivó el
  cambio en la tarjeta de clases de activo.

## 5. Preguntas para Nichelson

1. **Color de la liquidez.** El número "Fuerte / Normal / Débil" va en verde,
   blanco y ámbar, mientras que la flecha va en gris para no sugerir qué es
   bueno. ¿Se deja el color (es lo primero que se ve) o se pasa a gris como la
   flecha?
2. **"Dinero listo para cripto … disponible para comprar."** Describe el stock
   de stablecoins. Leído rápido, puede sonar a que va a comprar. ¿Lo dejamos o
   decimos "dinero que ya está dentro de cripto"?
3. **El glosario está en Ajustes**, como pedía el §4.4. ¿Va también un enlace
   "¿qué significa?" desde cada tarjeta?

Queda para la M6.6 (renombre visible): los títulos de la configuración en
Ajustes siguen en inglés (Runtime, Setup, Gate, Venue), y el título de la
pestaña sigue siendo BOB.
