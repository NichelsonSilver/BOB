import { useEffect } from "react";
import { create } from "zustand";

/**
 * Idioma de la interfaz (M6, botón EN / ES). Inglés por defecto, decisión de
 * Nichelson del 2026-10-09.
 *
 * Sin librería: los dos textos van juntos donde se usan, `tr("Home", "Inicio")`.
 * Un diccionario aparte se desincroniza del código sin que nada falle, y con
 * dos idiomas no hace falta pluralización ni interpolación propia.
 *
 * **Las cifras no cambian con el idioma**: solo el texto que las rodea. Los
 * números siguen pasando por `lib/format.ts`, que usa punto decimal en los dos.
 */
export type Lang = "en" | "es";

const KEY = "marea.lang";

function load(): Lang {
  try {
    return localStorage.getItem(KEY) === "es" ? "es" : "en";
  } catch {
    return "en";
  }
}

type LangState = { lang: Lang; setLang: (l: Lang) => void };

export const useLangStore = create<LangState>((set) => ({
  lang: load(),
  setLang: (lang) => {
    try {
      localStorage.setItem(KEY, lang);
    } catch {
      /* modo privado: el idioma vuelve al default al recargar, nada se rompe */
    }
    set({ lang });
  },
}));

/** Elige el texto del idioma activo. Para funciones fuera de componentes. */
export function pick<T>(lang: Lang, en: T, es: T): T {
  return lang === "en" ? en : es;
}

export function useLang() {
  const lang = useLangStore((s) => s.lang);
  const setLang = useLangStore((s) => s.setLang);
  return { lang, setLang, tr: <T,>(en: T, es: T): T => pick(lang, en, es) };
}

/** Mantiene `<html lang>` al día: lo leen los lectores de pantalla y el traductor del navegador. */
export function useHtmlLang(): void {
  const lang = useLangStore((s) => s.lang);
  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);
}

// ---------------------------------------------------------------------------
// Textos que hoy arma el backend en español y aparecen en las páginas simples.
// Se traducen desde una clave estable; si llega uno desconocido, se muestra
// tal cual (mejor en español que vacío). El paso 2 los moverá al backend.
// ---------------------------------------------------------------------------

/** Clases de activo, por la clave `block` de `marea/strength.py`. */
export const CLASS_EN: Record<string, string> = {
  acciones: "Equities",
  renta_fija: "Bonds",
  reserva_valor: "Gold & silver",
  commodities: "Commodities",
  real_estate: "Real estate (REITs)",
  divisas: "US dollar",
};

/** Zona del ciclo de BTC (`marea/panorama._zone`). */
export const ZONE_EN: Record<string, string> = {
  "baja del ciclo": "low in the cycle",
  "media del ciclo": "mid-cycle",
  "alta del ciclo": "high in the cycle",
  "extrema del ciclo": "extreme",
};

/** Etiqueta de Altcoin Season (`marea/breadth.season_label`). */
export const SEASON_EN: Record<string, string> = {
  "temporada de alts": "altcoin season",
  "temporada de BTC": "bitcoin season",
  neutral: "neutral",
  "sin dato": "no data",
};

/** Líneas de respaldo (`evidence`) del panorama. */
export const EVIDENCE_EN: Record<string, string> = {
  "medido (monitoreo, sin pronóstico)": "measured (monitoring, no forecast)",
  "modelo publicado (NY Fed), reproducido desde FRED; no es pronóstico propio":
    "published model (NY Fed), reproduced from FRED; not MAREA's own forecast",
  "foto diaria sin historia previa: sin percentil hasta acumular meses":
    "daily snapshot with no prior history: no percentile until months accumulate",
};

export function translated(lang: Lang, map: Record<string, string>, es: string): string {
  if (lang === "es") return es;
  if (map[es]) return map[es];
  // Las evidencias compuestas ("medido …; detalle") se traducen por partes.
  const parts = es.split("; ");
  if (parts.length > 1 && map[parts[0]]) {
    return [map[parts[0]], ...parts.slice(1).map((p) => EVIDENCE_TAIL_EN[p] ?? p)].join("; ");
  }
  return es;
}

const EVIDENCE_TAIL_EN: Record<string, string> = {
  "Altcoin Season con top 50 por volumen (proxy declarado)": "Altcoin Season with the top 50 by volume (declared proxy)",
  "point-in-time solo desde la primera corrida (2026-10-07)": "point-in-time only since the first run (2026-10-07)",
};
