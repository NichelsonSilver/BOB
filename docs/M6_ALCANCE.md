# Fase M6 — El panel de MAREA para cualquier lector

> Decisión de Nichelson, 2026-10-07. M6 es una fase de **frontend**: ordena las
> páginas y utilidades para que lo útil se vea primero y bien organizado, y lo
> técnico quede en una capa secundaria o explicado en lenguaje simple. Cierra
> la fusión visible de BOB dentro de MAREA.
>
> Se trabaja **iterando sobre opciones**: cada etapa presenta alternativas con
> capturas, Nichelson elige, y la elección queda registrada en §6.

## 1. Objetivos

1. **Página principal simple y contundente:** pocos gráficos, porcentajes y
   una frase por tarjeta. Una persona con conocimientos básicos tiene que
   entenderla sin glosario.
2. **MAREA como nombre en todo lo visible.** Cripto pasa a ser una sección
   propia, desplegable o con sub-ventanas.
3. **Capa técnica separada:** backtests, artefactos del gate, AUC, σ,
   intervalos y la Signal de 15m quedan accesibles pero fuera del camino
   principal.

## 2. Restricciones que no se negocian

| restricción | por qué |
|---|---|
| **La fusión con BOB es solo de cara al usuario.** No se renombran el paquete `bob`, el directorio `proyectos/bob`, la DB ni la tarea `BOB_panel_forward_diario` hasta el corte de la Fase 10-bis | La tarea corre `bob.paper.panel_forward` por ruta absoluta; renombrar rompe la corrida (`REESTRUCTURACION_MAREA.md` §6, regla 12). El renombre interno queda como una etapa aparte, después del corte |
| **Regla 2 también en la página simple.** Simplificar no puede volver operable lo que no lo es | Ninguna tarjeta de la portada sugiere comprar ni vender. Las que son monitoreo dicen "monitoreo" |
| **El IC del forward cripto no se muestra** antes del análisis interino a las 90 fechas | Enmienda 1: nadie mira el IC antes. La portada muestra solo el estado operativo (fechas válidas acumuladas) |
| **Las cifras vienen de la API, no del TSX** | La misma regla de la Fase 6: un número escrito a mano queda viejo sin que nada falle |
| Simplificar el texto no cambia el número | "Más alto que el 80% de las últimas semanas" es el percentil 80, no un redondeo favorable |

## 3. Inventario actual (punto de partida)

Rutas: `/` (Signal), `/macro`, `/flows`, `/map`, `/risk`, `/analysis`,
`/backtest`, `/history`, `/settings`. Hoy la portada es la Signal de 15m, que
es un módulo **congelado**: lo primero que ve el usuario es lo menos vigente.

## 4. Opciones a evaluar

### 4.1 Arquitectura de navegación

| opción | estructura | a favor | en contra |
|---|---|---|---|
| **A. Secciones con desplegable** (recomendada) | Inicio · Liquidez · Flujos · **Cripto ▾** (Panel diario, Mapa, Riesgo, Señal 15m) · Laboratorio ▾ (Backtest, Historial, Análisis) · Ajustes | Pocas entradas arriba; cripto agrupado como pediste; lo técnico tiene un lugar fijo | Un clic más para llegar a cripto |
| B. Dos espacios: "MAREA" y "MAREA Cripto", con sub-pestañas | Un selector arriba cambia todo el menú | Separación nítida | Duplica la navegación; la portada no puede mezclar macro y cripto |
| C. Una sola página larga por secciones | Todo en scroll | Simple de construir | No escala con M5 y esconde las utilidades (calculadora, mapa) |

### 4.2 Cómo se esconde lo técnico

| opción | mecanismo | evaluación |
|---|---|---|
| **A. Detalle progresivo por tarjeta** (recomendada) | Cada tarjeta muestra número + frase; "Ver detalle" abre la versión técnica (IC, n, método, artefacto) | El rigor queda a un clic del dato al que pertenece |
| B. Interruptor global "Simple / Experto" | Toda la app cambia de modo | Fácil de entender, pero duplica cada página y uno de los modos envejece |
| C. Solo en el Laboratorio | La portada no tiene detalle técnico | Separa demasiado: el número simple pierde su respaldo visible |

### 4.3 Portada: candidatas a tarjeta

Máximo 6. Cada una con: número grande, flecha de cambio, una frase de "qué
significa" y una de "qué NO significa".

| tarjeta | número | ejemplo de frase |
|---|---|---|
| Régimen de liquidez | etiqueta + percentil | "La liquidez de la Fed crece más que en el 70% de las últimas semanas" |
| Dinero entrando a cripto | emisión neta de stablecoins a 30 días (M5) | "Entraron US$ X mil millones en stablecoins en el último mes" |
| Ciclo de BTC | Risk point-in-time de 0 a 100 (M5) | "Zona media del ciclo. No indica hacia dónde va el precio" |
| Riesgo de recesión EE.UU. | probabilidad (M5) | "6 de cada 100: bajo" |
| Clases de activo más fuertes y más débiles | fuerza relativa (M4) | "Esta semana: oro y cobre arriba, bonos largos abajo" — descriptivo |
| Estado del estudio cripto | fechas válidas / 90 | "Validación en curso: 34 de 90 días. Sin resultado todavía" |

A decidir en M6.2: cuáles quedan, en qué orden, y si va un gráfico de fondo
(sparkline) por tarjeta o un gráfico grande único.

### 4.4 Lenguaje

| técnico | en la portada |
|---|---|
| percentil 80 | "más alto que el 80% de las últimas semanas" |
| σ, AUC, IC, BSS | no aparecen; viven en "Ver detalle" |
| régimen expansivo / contractivo | "la liquidez crece / se achica" |
| sin potencia / no significativo | "todavía no hay datos suficientes para saberlo" |

Un glosario corto en Ajustes cubre lo que quede.

## 5. Etapas

| etapa | contenido | entrega para elegir |
|---|---|---|
| **M6.0** | Inventario: cada página, a quién sirve, qué muestra que no debería estar en el camino principal | Tabla |
| **M6.1** | Arquitectura de navegación (§4.1) y del detalle técnico (§4.2) | Maqueta navegable de las opciones elegidas |
| **M6.2** | Portada (§4.3) con datos reales | 2 variantes con captura |
| **M6.3** | Sección Cripto: agrupar Panel, Mapa, Riesgo y la Señal 15m (marcada congelada) | Captura |
| **M6.4** | Laboratorio: backtest, historial, análisis y artefactos | Captura |
| **M6.5** | Lenguaje: textos de cada tarjeta y glosario | Lista de textos para revisar |
| **M6.6** | Renombre visible: título, menú, README, favicon, textos | Diff |
| **M6.7** (después del corte de la 10-bis) | Renombre interno: paquete, directorio, DB y tarea programada | Requiere su propia decisión |

Cada etapa se cierra con capturas mirando la página real, no solo con `tsc` en
verde. La Fase 6 encontró cuatro defectos que solo se veían así.

## 6. Registro de decisiones de iteración

| fecha | etapa | opción elegida | motivo |
|---|---|---|---|
| 2026-10-07 | M6.1 navegación (§4.1) | **A. Secciones con desplegable**: Inicio · Liquidez · Flujos · Cripto ▾ · Laboratorio ▾ · Ajustes | pocas entradas arriba, cripto agrupado, lo técnico con lugar fijo |
| 2026-10-07 | M6.1 lo técnico (§4.2) | **A. "Ver detalle" por tarjeta** | el respaldo queda junto al dato al que pertenece |
| 2026-10-07 | M6.0 | todo lo referido al forward se mantiene (15m y 10-bis) | se espera el resultado del panel |
| 2026-10-08 | M6.2 portada (§4.3) | **A. Seis tarjetas con gráfico chico**: liquidez, stablecoins, ciclo BTC, recesión, clases de activo, estudio del panel | se ve de un vistazo qué sube y qué baja; la B ponía en grande la serie más inestable del panel |
| 2026-10-09 | M6.5 preguntas abiertas | la etiqueta de liquidez **conserva sus colores**; se deja "disponible para comprar"; **sin** enlace al glosario desde cada tarjeta | decisión de Nichelson tras ver la app levantada |
| 2026-10-09 | idioma | **interfaz bilingüe, inglés por defecto**, con botón EN / ES; se hace en **dos pasos** (páginas simples primero, técnicas y textos del backend después); README del repo en inglés | pedido de Nichelson |

## 7. Dependencias

- Las tarjetas de stablecoins, ciclo BTC y recesión necesitan los endpoints
  de **M5**. M6.0 y M6.1 pueden adelantarse; M6.2 espera a M5.6.
- No se agrega librería de UI nueva sin evaluarla primero: el stack sigue
  siendo React, Tailwind, lightweight-charts y Recharts.

## 8. Registro de etapas

### M6.0 (2026-10-07)

Inventario en `docs/M6_INVENTARIO.md`.

### M6.1 (2026-10-07) — navegación y "Ver detalle"

**Navegación A** (`components/layout/Layout.tsx`): Inicio · Liquidez · Flujos ·
Cripto ▾ (Panorama, Panel diario, Riesgo por trade, Mapa técnico, Señal 15m
congelada) · Laboratorio ▾ (Validación forward 15m, Backtest, Análisis) ·
Ajustes. Las rutas viejas (`/macro`, `/risk`, `/history`…) redirigen
conservando la query, así `/risk?entry=&stop=` sigue prellenando la
calculadora. **Nada del forward se borró.**

**"Ver detalle"** (`components/common/DetailCard.tsx`): número, frase de qué
significa, frase de qué no significa, fecha del dato siempre visible, y el
respaldo técnico a un clic. Las tarjetas viven en
`components/marea/Cards.tsx` y las comparten Inicio y Panorama: una cifra no
puede salir distinta en dos páginas.

**Páginas nuevas**: Inicio (primera versión: liquidez, stablecoins, ciclo
BTC, recesión, estudio del panel), Cripto ▸ Panorama (las 7 lecturas de la
M5) y Cripto ▸ Panel diario (avance del forward 10-bis hacia el interino de 90
y el corte de 180, días que no contaron y por qué; **sin IC**).

**Encabezado**: la frescura de las fuentes de MAREA reemplaza al feed de
Binance, que pasó a la Señal 15m junto con un aviso de "módulo congelado" con
la edad del último pronóstico.

**Backend**: `/api/macro/freshness` (con umbral por fuente),
`/api/macro/panel-status` (agrega el interino de 90 de la Enmienda 1; no lee
ni envía el IC) y `/api/macro/narrative`. 3 tests nuevos; 201 en verde.

**Encontrado mirando capturas, no compilando** (`tsc` estaba en verde en los
cuatro casos), igual que en la Fase 6:

1. cifras escritas a mano con coma decimal ("0,867") al lado de cifras con
   punto ("312.7") — corregido a punto en todo lo nuevo;
2. abrir un detalle estiraba la tarjeta vecina con un hueco vacío;
3. un umbral único de 8 días marcaba el COT como atrasado cuando 9–10 días es
   su ritmo normal (reporte del martes, publicado el viernes). Ahora cada
   fuente tiene su umbral en `brief.STALE_AFTER_DAYS`, que usan el memo y el
   encabezado;
4. jerga interna filtrada a la página del panel ("ver docstring").

Capturas tomadas con Playwright manejando el Edge instalado, desde un entorno
temporal (sin dependencias nuevas en el proyecto).

**Pendientes registrados para etapas siguientes:** el título de la pestaña
sigue siendo "BOB — Bursatil Operator Buddy" y los encabezados de Laboratorio
están en inglés (M6.6); el texto de la Señal 15m usa coma decimal
("2.686,29") contra punto en su encabezado (M6.5).

### M6.2 (2026-10-08) — portada, dos variantes con datos reales

Las dos variantes muestran las **mismas seis lecturas y las mismas cifras**
(salen de los mismos componentes en `components/marea/Cards.tsx`). Lo único que
cambia es el dibujo. Se comparó con un selector A/B en el Inicio.

| | A. Seis tarjetas con gráfico chico | B. Liquidez como lectura principal |
|---|---|---|
| estructura | grilla de 3 × 2, todas iguales | liquidez a lo ancho con su historia desde 2020, debajo 3 + 2 tarjetas sin gráfico |
| gráficos | uno por tarjeta: línea del último año (liquidez, stablecoins, ciclo), 12 meses (recesión), barras por clase, barra de avance (estudio) | uno grande, más la barra de avance del estudio |
| a favor | se lee de un vistazo si cada cosa sube o baja; todas pesan lo mismo | jerarquía clara: MAREA es un analista de liquidez y eso va primero |
| en contra | seis gráficos chicos compiten; el de recesión en escala 0–100% casi no se mueve, y el de stablecoins no parte de cero (está dicho en el detalle) | la línea de liquidez salta mucho (racha mediana de 3 semanas) y, puesta grande, invita a leerle tendencias que no tiene |

**Las seis tarjetas, en orden:** liquidez global, dinero listo para cripto
(stablecoins), ciclo de BTC, riesgo de recesión EE.UU., clases de activo del
último mes (nueva, fuerza relativa a 4 semanas) y estudio del panel cripto.
Cada una lleva una **flecha de cambio en gris**: subir o bajar no se pinta
como bueno o malo (regla 2). La flecha queda horizontal si el cambio es menor
a 5 puntos. Esa elección es de lectura, no de método.

**Lenguaje (adelanto de la M6.5, para revisar):** la etiqueta de liquidez se
dice "Fuerte / Normal / Débil" (expansivo / neutro / contractivo, por
tercios). La etiqueta técnica queda en el detalle. El percentil se dice "avanza
más lento que en el 82% de las semanas anteriores", **truncado** para no
redondear a favor.

**Encontrado en capturas:** en el ancho de un teléfono, el número "6 / 90" se
partía en dos líneas y las barras por clase quedaban de ancho cero. Los dos
casos están corregidos. Queda un defecto **previo a esta etapa**: el menú
lateral de `Layout.tsx` no se pliega en el móvil y se come la mitad del ancho
(en la B, el gráfico grande queda ilegible). Se registra para la M6.3.

De paso se actualizaron los precios de ETFs (`marea.data.ingest etf`): Tiingo
estaba en 7 días y la tarjeta de clases salía como "dato viejo".

**Elegida la A (2026-10-08, §6).** Se borraron el selector, `LiquidityHero` y
el gráfico grande. La historia completa del compuesto sigue en Liquidez.

### M6.3 (2026-10-09) — sección Cripto

La agrupación ya existía desde la M6.1 (Cripto ▾ con Panorama, Panel diario,
Riesgo, Mapa y Señal 15m). Lo que agrega esta etapa:

**Cripto ▸ Resumen** (`/cripto`, `pages/CryptoPage.tsx`): la puerta de la
sección. Una tarjeta por página con la pregunta que contesta, una línea de qué
muestra y su **respaldo en una palabra**:

| página | respaldo | dato vivo (de la API) |
|---|---|---|
| Panorama | descriptivo | — |
| Panel diario | en prueba | días válidos / 90 (nunca el IC) |
| Riesgo por trade | aritmética | — |
| Mapa técnico | descriptivo (las reglas de entrada no le ganaron al azar) | — |
| Señal 15m | congelada (gris) | edad del último pronóstico |

**Menú en el móvil** (`Layout.tsx`): bajo `md` el menú es un panel que se abre
con ☰ en el encabezado y se cierra al navegar o al tocar fuera. Medido en
390 px: ninguna de las 13 páginas desborda a lo ancho.

**Encontrado en capturas:** en la Señal 15m el filtro de régimen decía
"Régimen normal" grande y en verde con un pronóstico de 3 días. Leído así
parece el estado de hoy. La página Riesgo ya lo ocultaba cuando el pronóstico
no está vigente; en la Señal se deja (la página *es* el último pronóstico),
pero en gris, con el medidor en gris y la frase "Estado del <fecha>, no de
ahora". La lectura en texto ya se marcaba "vieja".

### M6.4 (2026-10-09) — Laboratorio

Hasta ahora el Laboratorio solo mostraba el trabajo de 15m. Los resultados del
panel diario, de las reglas técnicas, del filtro con stop y del ranking macro
no tenían ningún lugar donde verse juntos.

**Laboratorio ▸ Resumen** (`/lab`, `pages/LabPage.tsx`): una tarjeta por
experimento, agrupadas por escala (cripto 15m, cripto diario, macro semanal).
Cada una lleva la pregunta en lenguaje simple, el veredicto en una palabra, una
o dos frases con las cifras y "Ver detalle" con los intervalos, el criterio, el
commit del pre-registro y el nombre del artefacto. Arriba va el conteo: 5
pasaron, 4 no pasaron, 1 en curso, 1 control limpio y 1 informativa.

**Backend**: `GET /api/lab/experiments` (`api/routes/lab.py`) lee cada familia
de `backend/artifacts/`. El veredicto sale del campo que escribió su propio
runner (`criteria.viable`, `verdict.c1_pass`, `RunSummary.habilitado`,
`adopted`…) y no se recalcula. Hay dos excepciones, declaradas en el código:
la volatilidad del gate usa `DieboldMariano.favors_model`, y el cono forward usa
"nominal dentro del IC por bloques", que es el criterio del cierre de la Fase 5.
La única tabla escrita a mano es la de pre-registros (documento y commit, sin
cifras). El placebo se marca como "control limpio" cuando falla. Si un
artefacto tiene otro esquema, la entrada se omite y la página sigue cargando.
Hay 2 tests nuevos y la suite completa pasa (1.172).

**El forward del panel diario aparece como "en curso"** con su avance (de
`/api/macro/panel-status`) y nunca con su resultado: el endpoint del
Laboratorio no lee esos registros (Enmienda 1).

**Tono**: "no pasó" va en gris neutro, no en rojo. Un negativo bien medido es
información. La página lo dice arriba, junto con que "pasar habilita seguir
midiendo, no operar".

**Títulos**: las tres páginas de 15m ahora se llaman "Backtest del gate 15m",
"Análisis del modelo 15m" y "Validación forward 15m".

**Encontrado en capturas:**

1. Abrir un detalle estiraba las tarjetas vecinas (es el mismo defecto de la
   M6.1). Se corrigió con `items-start` en la grilla.
2. "p <0.0001" se partía en dos líneas. Las cifras ahora van sin corte de línea.
3. En las frases, "IC" se leía como intervalo de confianza cuando era el
   coeficiente de orden. En la frase simple ahora dice "correlación del orden",
   y la sigla queda en el detalle.

### M6.5 (2026-10-09) — lenguaje y glosario

La lista de textos para revisar está en `docs/M6_TEXTOS.md`. Los textos se
leyeron en la página real, no en el código.

**Un error de contenido, no de estilo:** el detalle del funding decía "suma
diaria de **liquidaciones**", pero `marea/funding.py` suma las **tasas de
funding**. Para un usuario que ya fue liquidado, la confusión no es menor.

**Cifras escritas a mano que pasaron a la API:**

- techo de 2021 del ciclo (0.867 / 0.756);
- referencias de amplitud (7.6% / 97.2%);
- coeficientes del probit;
- racha mediana de la liquidez;
- rango de pérdida de las reglas técnicas en Riesgo;
- "AUC 0,52" en el Mapa y en la lectura de la Señal.

Las fechas de referencia son hechos históricos (`CYCLE_REFERENCES`,
`BREADTH_REFERENCES` en `marea/panorama.py`) y el valor se calcula en esa
fecha. Los valores calculados reproducen exactamente lo que estaba escrito.

**Percentiles:** se dicen hacia el lado que corresponde y truncados. El funding
decía "más que el 16% de los días" y ahora dice "más bajo que el 83%". El panel
redondeaba 43,75% a 44% y ahora dice 43%.

**Frases que afirmaban más de lo medido:** "este orden no se sostiene" pasó a
"no se encontró que lo anticipe", y "medias y RSI no anticiparon dirección
(AUC 0,52)" pasó a "el modelo de dirección no pasó la prueba".

**Punto decimal en la lectura de la Señal 15m** (`bob/models/narrative.py`, que
no está en la lista de la regla 12). Era el pendiente registrado en la M6.1.

**Glosario** en Ajustes (`components/marea/Glossary.tsx`): 29 términos sin
cifras y con buscador. Separa los dos "IC". Ajustes ahora abre con el
glosario; la configuración de 15m queda debajo, sin cambios.

Hay 3 preguntas abiertas en `docs/M6_TEXTOS.md` §5: el color de la etiqueta
de liquidez, la frase "disponible para comprar" y un enlace al glosario desde
cada tarjeta.

### M6.6 (2026-10-09) — renombre visible

**Lo que cambió:**

- pestaña "MAREA — liquidez global y cripto", con un título por página
  ("Cripto · Riesgo por trade — MAREA") que sale del mismo menú;
- favicon con una "M";
- las dos frases "BOB nunca ejecuta órdenes" (Riesgo y Señal);
- los títulos de Ajustes, que seguían en inglés (Runtime, Setup, Gate, Venue);
- el título de la API en `/docs`;
- el README, que ahora abre con MAREA y sus cuatro capas, deja a BOB como
  "módulo cripto" y pone al día el estado por fase (10, 10-bis, M0–M6), los
  tests (824 → 1.172) y la documentación.

**Verificado en la app real:** ninguna de las 15 páginas muestra "BOB".

**Lo que NO cambió, a propósito:**

- el paquete `bob`, el directorio, la DB y la tarea `BOB_panel_forward_diario`
  (M6.7, después del corte de la 10-bis);
- el `model_version` `bob-forecast-0.1.0`, que es un dato de la muestra
  forward y se ve en el pie de la Señal;
- el campo `service: "bob"` de `/api/health`, que es un identificador para
  programas. Se agregó al lado `product: "MAREA"`;
- los logs (`BOB starting`) y el encabezado de los reportes del gate. Los dos
  son internos, y los reportes viejos tienen que seguir comparables.

### M6.8 (2026-10-09) — idioma, paso 1

La interfaz es **bilingüe y parte en inglés** (decisión del 09-10, §6). Hay un
botón EN / ES en el encabezado, la elección se guarda en el navegador
(`marea.lang`) y `<html lang>` acompaña.

**Diseño** (`src/i18n.ts`): sin librería. Los dos textos van juntos donde se
usan, `tr("Home", "Inicio")`, porque un diccionario aparte se desincroniza del
código sin que nada falle. **Las cifras no cambian con el idioma**: siguen
pasando por `lib/format.ts`, con punto decimal en los dos.

**Paso 1, hecho:**

- el menú y el título de pestaña de las 15 rutas;
- el encabezado;
- Inicio, Cripto ▸ Resumen, Panorama y Panel diario;
- Laboratorio ▸ Resumen;
- el glosario (29 términos) y el encabezado de Ajustes;
- los componentes compartidos ("Show detail", "as of", "today", "Loading").

Los textos que hoy arma el backend en estas páginas (nombres de clases de
activo, zona del ciclo, etiqueta de altseason, advertencia de recesión, líneas
de respaldo) se traducen en el frontend desde claves estables. Si llega uno
desconocido, se muestra tal cual. La advertencia de recesión se rearma con
`peak_since_2022`, que la API ya envía.

**Verificado en la app:**

- en inglés, ninguna de las seis páginas del paso 1 tiene una línea en
  español (se buscaron tildes y palabras frecuentes);
- ES persiste al recargar;
- con 390 px de ancho no hay desborde en ninguno de los dos idiomas.

De paso, el porcentaje de días que cuentan del Panel diario se trunca (decía
44% con 43,75%).

**Paso 2, pendiente:**

- las páginas técnicas: Liquidez, Flujos, Riesgo, Mapa, Señal 15m, Backtest,
  Forward 15m, Análisis y la configuración de Ajustes;
- los textos que arma el backend: la lectura de la Señal, los avisos de la
  calculadora y los mensajes de error. Recibirían un parámetro `lang`.

**README del repositorio:** ya estaba entero en inglés. Se corrigió la frase
que decía "Spanish UI".

### M6.8 (2026-10-09) — idioma, paso 2

Con esto las 15 páginas son bilingües, con inglés por defecto.

**Backend con `lang`** (default `"es"`, así que quien llamaba antes no nota
nada):

- `models/narrative.py`: la lectura de la Señal;
- `models/sizing.py`: los avisos de la calculadora (`code` y `level` no
  cambian con el idioma);
- `models/projection.py`: los avisos de la proyección;
- las rutas `/api/analysis/narrative`, `/api/risk/sizing` y
  `/api/analysis/projection`.

Hay tests que exigen que el inglés lleve **las mismas cifras** que el español,
que no tenga palabras en español y que no pronostique dirección.

**`marea/i18n.py`** traduce al servirlos el reporte de liquidez, la fuerza
relativa y el veredicto M3. El reporte ya calculado en la DB y el artefacto de
la M3 no se tocan. La traducción va por clave estable (indicador, código COT,
ticker, clase), y un test exige que toda clave que existe hoy tenga su inglés.

**Frontend**: Liquidez, Flujos, Riesgo, Mapa, Señal 15m (página y siete
componentes), Backtest, Forward 15m, Análisis y la configuración de Ajustes.
Los códigos que llegan del backend (etiqueta de régimen, respaldo de cada
sección, familia de features, veredicto de las reglas, veredicto de
Diebold-Mariano, notas del exchange) se traducen al dibujarlos. La sección
"Lo que esto no dice" se reconoce por su código `basis` y no por su título,
que ahora cambia con el idioma.

**Verificado en la app real:**

- en inglés, ninguna de las 14 rutas tiene una línea en español, salvo
  nombres de archivo y apellidos de autores;
- la única que apareció (`sin-etiqueta`, el identificador de variante del
  primer run) se traduce al dibujarla;
- en un teléfono (390 px) ninguna ruta se desborda en ninguno de los dos
  idiomas;
- 1.179 tests en verde y mypy limpio.

**Tres textos corregidos de paso, porque afirmaban algo falso:**

1. El hint de QLIKE en el corte forward decía "no depende de la dispersión de
   la muestra". La Fase 5 (punto 13) midió lo contrario: su media la domina la
   cola.
2. Flujos decía que la M3 "midió que su orden no se sostiene". En realidad la
   M3 no encontró evidencia, que no es lo mismo (la misma corrección de la
   M6.5). También remitía a "la página Macro", que ahora se llama Liquidez.
3. El aviso de régimen elevado de la calculadora traía "52% a 20x" escrito a
   mano. La página ya muestra ese efecto al lado, leído del artefacto.

**Lo que no se traduce:** los mensajes de error HTTP (diagnóstico técnico,
salen tal cual), los nombres de archivo y los identificadores internos
(`model_version`, estados `resolved/open/gap`).

### Cierre de la fase (2026-10-09)

M6 cerrada por decisión de Nichelson, con M6.0 a M6.6 y M6.8 completas. Queda
fuera la **M6.7**: el renombre interno del paquete `bob`, el directorio, la
DB, la tarea `BOB_panel_forward_diario`, `model_version` y `service`. Espera al
corte de la 10-bis y requiere su propia decisión.

### M6.7 parcial (2026-10-14)

Decisión de Nichelson: el proyecto se llama MAREA en todos lados y BOB queda
como su módulo cripto. Se renombraron tres cosas:

- el repo de GitHub, de `NichelsonSilver/BOB` a `NichelsonSilver/MAREA`
  (GitHub redirige la URL vieja);
- el directorio, de `proyectos/BOB` a `proyectos/MAREA`, con
  `proyectos/mover_a_marea.ps1`;
- el proyecto Python, con `name = "marea"` en `pyproject.toml`.

Los `.ps1` de las tareas ya no llevan rutas absolutas. No se tocaron el
paquete `bob`, `bob.db`, el nombre de la tarea, `model_version` ni `service`.
Todo eso es código o identidad de la corrida forward, que sigue sin cambios
hasta el interino de las 90 fechas (regla 12).
