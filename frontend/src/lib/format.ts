/**
 * Formateo de números para el dashboard.
 *
 * Una regla que atraviesa todo el archivo: **nada se redondea hacia arriba y
 * nada se suaviza** (restricción de CLAUDE.md, "No inflar el KPI"). Un valor
 * que no existe se dibuja como "—" y no como 0: son cosas distintas y
 * confundirlas es la forma más barata de mentir en una tabla.
 */

/** ¿Es un número real y finito? `null`, `undefined` y `NaN` no lo son. */
export function isNum(v: number | null | undefined): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

/** El guion largo que ocupa el lugar de un dato ausente. */
export const DASH = "—";

export function pct(v: number | null | undefined, digits = 2): string {
  return isNum(v) ? `${(v * 100).toFixed(digits)}%` : DASH;
}

/** Porcentaje con signo explícito — para EV, retornos y ROE. */
export function signedPct(v: number | null | undefined, digits = 2): string {
  if (!isNum(v)) return DASH;
  const s = (v * 100).toFixed(digits);
  return v > 0 ? `+${s}%` : `${s}%`;
}

/**
 * Conteo con separador de miles. Usa `en-US` (coma) a propósito, aunque el
 * dashboard esté en español: el resto de los números sale de `toFixed`, que
 * escribe el decimal con punto. Con `es` el mismo panel mostraba "42.098"
 * muestras al lado de "0.2474" de Brier — dos convenciones a diez píxeles de
 * distancia, y el lector no puede saber si el primero son 42 mil o 42 coma
 * cero nueve ocho.
 */
export function count(v: number | null | undefined): string {
  return isNum(v) ? v.toLocaleString("en-US") : DASH;
}

export function num(v: number | null | undefined, digits = 2): string {
  return isNum(v) ? v.toFixed(digits) : DASH;
}

/** Precio. 2 decimales alcanzan para ETH; los pares baratos piden más. */
export function price(v: number | null | undefined, digits = 2): string {
  return isNum(v)
    ? v.toLocaleString("en-US", {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      })
    : DASH;
}

/** p-value: por debajo de 1e-4 se dice "<0.0001", no "0.0000". */
export function pValue(v: number | null | undefined): string {
  if (!isNum(v)) return DASH;
  return v < 1e-4 ? "<0.0001" : v.toFixed(4);
}

export function multiplier(v: number | null | undefined, digits = 1): string {
  return isNum(v) ? `${v.toFixed(digits)}x` : DASH;
}

export function sigmas(v: number | null | undefined, digits = 1): string {
  return isNum(v) ? `${v.toFixed(digits)}σ` : DASH;
}

/** epoch ms → "dd-MM HH:mm" en hora local. */
export function stamp(ms: number | null | undefined): string {
  if (!isNum(ms)) return DASH;
  const d = new Date(ms);
  const p2 = (n: number) => String(n).padStart(2, "0");
  return `${p2(d.getDate())}-${p2(d.getMonth() + 1)} ${p2(d.getHours())}:${p2(
    d.getMinutes(),
  )}`;
}

/** Barras del timeframe → texto legible ("16 barras · 4,0 h"). */
export function horizon(bars: number, intervalMs: number): string {
  const hours = (bars * intervalMs) / 3_600_000;
  return `${bars} barras · ${hours.toFixed(1)} h`;
}

/** Clase de color por signo. Neutro cuando el valor no existe. */
export function signClass(v: number | null | undefined): string {
  if (!isNum(v) || v === 0) return "text-neutral-300";
  return v > 0 ? "text-accent" : "text-danger";
}
