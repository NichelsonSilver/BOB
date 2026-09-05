/**
 * Paleta de los gráficos, separada de la del producto y validada, no elegida
 * a ojo.
 *
 * Hay dos paletas en el dashboard y confundirlas sería un error de lectura:
 *
 * - **Estado** (`STATUS`): los colores de CLAUDE.md — verde `#3cd9a8`, rojo
 *   `#f07045`, amarillo `#f5b731`. Significan long/short, positivo/negativo,
 *   alerta. Están reservados para eso y **nunca** se usan como "serie 3": un
 *   verde que a veces significa "long" y a veces "la línea de arriba" no
 *   significa nada.
 * - **Series** (`SERIES`): identidad dentro de un gráfico — pronosticado vs
 *   realizado, modelo vs baseline. Se asignan en orden fijo, nunca ciclado.
 *
 * `SERIES` está verificado con el validador de la guía de dataviz contra la
 * superficie real del tema (`#0e0f0f`), en modo oscuro y con `--pairs all`:
 *
 *     banda de luminosidad   los 3 dentro de L 0.48–0.67
 *     piso de croma          los 3 >= 0.1
 *     separación CVD         peor par ΔE 10.6 (protan) · tritan 3.4
 *     piso de visión normal  peor par ΔE 18.0
 *     contraste vs superficie los 3 >= 3:1
 *
 * Los colores del producto NO pasan la banda de luminosidad para modo oscuro
 * (`#3cd9a8` da L 0.79, `#f5b731` L 0.82): son correctos como acento sobre
 * texto y demasiado claros como relleno de serie. Por eso las dos paletas son
 * distintas — no por gusto.
 *
 * Regla que vale para todos los gráficos: **una sola escala por eje**. Dos
 * medidas de escalas distintas van a dos gráficos, nunca a dos ejes Y.
 */

/** Series categóricas, en orden fijo. Una cuarta serie no se genera: se agrupa. */
export const SERIES = ["#4a8fe0", "#b8820d", "#18a37c"] as const;

/** Nombres para que el uso se lea en el sitio y no haya que contar índices. */
export const SERIES_BY_ROLE = {
  /** Lo que el modelo dijo que iba a pasar. */
  forecast: SERIES[0],
  /** Lo que pasó. */
  realized: SERIES[1],
  /** Un tercero de comparación: baseline, nominal, referencia. */
  reference: SERIES[2],
} as const;

/** Colores de estado del producto (CLAUDE.md). Reservados. */
export const STATUS = {
  good: "#3cd9a8",
  bad: "#f07045",
  warn: "#f5b731",
} as const;

/** Tinta y estructura: rejilla y ejes recesivos, texto en tokens de texto. */
export const INK = {
  surface: "#0e0f0f",
  grid: "#1e2020",
  axis: "#3a3d3d",
  muted: "#8a8a8a",
  text: "#d4d4d4",
} as const;

/** Estilo común de los tooltips de recharts, para no repetirlo en cada gráfico. */
export const TOOLTIP_STYLE = {
  contentStyle: {
    background: "#141616",
    border: "1px solid #2a2d2d",
    borderRadius: 6,
    fontSize: 12,
    fontFamily: "JetBrains Mono, Consolas, monospace",
  },
  labelStyle: { color: INK.muted, fontSize: 11 },
  itemStyle: { color: INK.text },
} as const;
