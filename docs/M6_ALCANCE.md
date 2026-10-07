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
| | | | |

## 7. Dependencias

- Las tarjetas de stablecoins, ciclo BTC y recesión necesitan los endpoints
  de **M5**. M6.0 y M6.1 pueden adelantarse; M6.2 espera a M5.6.
- No se agrega librería de UI nueva sin evaluarla primero: el stack sigue
  siendo React, Tailwind, lightweight-charts y Recharts.
