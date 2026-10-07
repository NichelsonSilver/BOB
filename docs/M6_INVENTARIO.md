# M6.0 — Inventario del dashboard (2026-10-07)

> Primera etapa de la M6 (`docs/M6_ALCANCE.md`). Qué muestra cada página, a
> quién sirve, en qué estado están sus datos hoy y adónde iría con la
> arquitectura recomendada. Medido contra una instancia offline del backend
> (`BOB_LIVE_DATA=false`, puerto 8001), consultando cada endpoint que la
> página llama. **Sin capturas**: esta sesión no tiene navegador. Las capturas
> entran en la M6.1, sobre la maqueta.

**Decisión de Nichelson (07-10): todo lo referido al forward se mantiene**
—la validación de 15m cerrada en la Fase 5 y la del panel diario en curso
(10-bis)—. Ninguna página que lo muestre se elimina; a lo sumo cambia de lugar.
El IC del panel sigue sin mostrarse antes del interino (Enmienda 1).

## 1. Hallazgos que ordenan la M6

1. **La portada es un módulo congelado, y hoy muestra datos vencidos.** `/` es
   la Signal de ETH 15m. Su último pronóstico es del **2026-10-05 16:15 UTC**,
   215 barras atrás (~54 h), y la API lo marca `is_stale: true`. Lo primero
   que ve el usuario es lo menos vigente del sistema.
2. **El encabezado describe al BOB de 15m, no a MAREA.** Muestra el estado del
   WebSocket de Binance y "BOB nunca ejecuta órdenes". Con el vivo congelado,
   el feed sale apagado siempre. Lo que importa ahora es la **frescura de las
   fuentes de MAREA** (que `marea.brief` ya calcula).
3. **Hay tres cosas medidas que ninguna página muestra:**
   - el **panorama de la M5** (`/api/macro/panorama`, ya construido);
   - el **estado operativo del forward del panel diario** (10-bis): fechas
     válidas, backfill, ritmo, fecha estimada del corte. Hoy solo lo ve el
     brief. Necesita un endpoint, que reutilice `brief.panel_forward_status`
     (no lee el IC);
   - el **registro de narrativa** (`narrativa/registro.jsonl`).
4. **El menú mezcla idiomas y públicos.** "Signal, Analysis, Backtest,
   History, Settings" en inglés junto a "Macro, Flujos, Mapa, Riesgo"; y
   páginas para decidir (Riesgo) al mismo nivel que páginas de auditoría
   (Backtest).
5. **Dos cargas lentas sin estado de carga claro:** `history/coverage` 4,2 s
   y `macro/panorama` 4,4 s la primera vez (después, caché).

## 2. Página por página

Leyenda de público: **D** = para decidir (Nichelson operando o leyendo el
mercado); **A** = auditoría y método (qué tan confiable es un número).
Jerga = términos técnicos que aparecen en la vista principal de la página.

| ruta | página | qué muestra | público | estado de datos hoy | jerga en primer plano | destino propuesto (opción A) |
|---|---|---|---|---|---|---|
| `/macro` | Macro · liquidez | régimen y percentil del compuesto, liquidez neta de la Fed, tabla de indicadores (núcleo, contexto con lo de la M5, Chile), COT, método | D + A | ✅ al día (reporte del 07-10, 23 indicadores) | percentil ×9, pre-registro | **Liquidez**. Arriba: el régimen en una frase y el gráfico de la Fed. Tablas y método a "Ver detalle" |
| `/flows` | Flujos · clases de activo | retorno relativo por clase y por ETF, por horizonte, y el veredicto de la M3 ("¿y hacia adelante?") | D + A | ✅ ETFs al 10-02 | IC, IC95, AUC, percentil | **Flujos**. Arriba: qué clases ganan y pierden. Veredicto M3 en una frase; sus cifras a detalle |
| `/` | Signal (ETH 15m) | narrativa del mercado, niveles del setup, cono, seguimiento de sigma, gauge KPI 1 en gris, filtro de régimen | D (cuando opera) + A | ⚠ **vencido**: último pronóstico 10-05 16:15 UTC | sigma ×5, IC | **Cripto ▸ Señal 15m**, rotulada *congelada* con la edad del dato visible. Deja de ser la portada |
| `/map` | Mapa técnico | zonas de liquidez, order blocks, soportes, EMAs, RSI; evidencia del backtest de reglas técnicas (no pasaron) | D | ✅ velas 15m hasta 10-05 | AUC, pre-registro | **Cripto ▸ Mapa técnico**. La evidencia ya está en una tarjeta: se mantiene |
| `/risk` | Riesgo por trade | calculadora: tamaño, margen, liquidación, costos, P de tocar stop/objetivo, filtro de régimen | **D** — es la herramienta más usada para operar | ✅ funciona sin el analista | sigma ×5, σ, ruido | **Cripto ▸ Riesgo**. La utilidad más concreta para operar: va arriba en la sección. σ a detalle |
| `/analysis` | Analysis | aporte por familia de features, features más importantes, KPI 3 (no emitido) | A | ✅ artefactos del gate | AUC | **Laboratorio ▸ Análisis** |
| `/backtest` | Backtest | ablación del gate, métricas, curva de calibración, corte forward congelado (n=288) | A | ✅ artefactos | AUC ×10, BSS ×8, Brier, ECE, QLIKE, Diebold | **Laboratorio ▸ Backtest**. Sin cambios de contenido |
| `/history` | History | muestra forward de 15m (n=1.174), volatilidad contra baselines, cobertura del cono, tabla de pronósticos, CSV | A — **forward, se mantiene** | ✅ (4,2 s de carga) | QLIKE, IC95, R², DM, Mincer-Zarnowitz, Winkler | **Laboratorio ▸ Validación forward 15m**. Se mantiene completa |
| `/settings` | Settings | espejo de la config del modelo de 15m y del setup; leverage/nivel/símbolo en localStorage | A | ✅ | σ, AUC, BSS | **Ajustes**, más un **glosario** (M6.5) |
| — | *(no existe)* | panorama M5: recesión, ciclo BTC, amplitud, funding, opciones, stablecoins | D | ✅ endpoint listo | — | **Cripto ▸ Panorama** (nueva) y tarjetas de **Inicio** |
| — | *(no existe)* | estado del forward del panel diario (10-bis), sin IC | D + A — **forward, se agrega** | ⚠ necesita endpoint | — | **Cripto ▸ Panel diario** (nueva) y una tarjeta de **Inicio** |
| — | *(no existe)* | registro de narrativa | D | ⚠ necesita endpoint de lectura | — | Candidata para **Inicio** o **Liquidez**; a decidir en la M6.2 |

## 3. Qué sale del camino principal

Lo que pasa a "Ver detalle" o al Laboratorio, sin borrarse:

- todas las métricas de validación (AUC, BSS, Brier, ECE, QLIKE, Diebold-
  Mariano, Mincer-Zarnowitz, Winkler, IC por bloques) fuera de su página de
  Laboratorio;
- σ y sigma como unidad: en la vista principal se dicen en % de movimiento;
- tablas completas de indicadores y de ETFs;
- secciones de método.

Lo que **no** sale del camino principal, aunque sea técnico, porque cambia
una decisión: la distancia a liquidación y el estado del filtro de régimen en
Riesgo, y la advertencia de dato vencido donde corresponda.

## 4. Mapa propuesto (opción A de `M6_ALCANCE.md` §4.1)

    Inicio                      ← nueva: tarjetas simples
    Liquidez                    ← /macro
    Flujos                      ← /flows
    Cripto ▾
      Panorama                  ← nueva (M5)
      Panel diario              ← nueva (estado forward 10-bis, sin IC)
      Riesgo por trade          ← /risk
      Mapa técnico              ← /map
      Señal 15m · congelada     ← /  (deja de ser la portada)
    Laboratorio ▾
      Validación forward 15m    ← /history
      Backtest                  ← /backtest
      Análisis                  ← /analysis
    Ajustes                     ← /settings (+ glosario)

El encabezado deja de mostrar el feed de Binance como estado principal y pasa
a mostrar la **frescura de las fuentes de MAREA**; el estado del feed queda
dentro de Señal 15m, que es la única página que lo usa.

## 5. Trabajo de backend que la M6 necesita

| endpoint | para qué | origen |
|---|---|---|
| `/api/macro/panel-status` | estado operativo del forward 10-bis, **sin IC** | `marea.brief.panel_forward_status` (solo lectura de `bob.db`) |
| `/api/macro/freshness` | encabezado: antigüedad de cada fuente | `marea.brief.freshness` |
| `/api/macro/narrative` | últimas entradas del registro | `marea.narrative.read` |

Los tres son de solo lectura y no tocan nada de la regla 12.
