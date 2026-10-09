import { Badge, Card } from "../common/Card";
import { type Lang, useLang } from "../../i18n";
import { stamp } from "../../lib/format";
import type { NarrativeResponse, NarrativeSection } from "../../types";

/**
 * Lectura del mercado en lenguaje natural, como un parte del tiempo.
 *
 * El texto lo arma el backend con plantillas (`models/narrative.py`), sin LLM:
 * cada cifra sale de un número que el resto de la página también muestra.
 * Cada sección lleva su respaldo, porque una frase sobre la volatilidad
 * (validada forward) no pesa lo mismo que una sobre una zona de liquidez
 * (operarla no mostró ventaja). Sin la etiqueta, el texto corrido las hace
 * sonar igual de seguras.
 */

const BASIS_TONE: Record<NarrativeSection["basis"], "ok" | "neutral" | "muted"> = {
  validado: "ok",
  descriptivo: "neutral",
  "sin ventaja medida": "muted",
  alcance: "muted",
};

/** `basis` es un código del backend; se traduce al dibujarlo. */
const BASIS_EN: Record<NarrativeSection["basis"], string> = {
  validado: "validated",
  descriptivo: "descriptive",
  "sin ventaja medida": "no measured edge",
  alcance: "scope",
};

function basisText(lang: Lang, b: NarrativeSection["basis"]): string {
  return lang === "en" ? BASIS_EN[b] : b;
}

export function MarketNarrative({ data }: { data: NarrativeResponse }) {
  const { lang, tr } = useLang();
  // La última sección siempre es la de alcance y va a todo el ancho; se
  // reconoce por su código y no por su título, que cambia con el idioma.
  return (
    <Card
      title={tr("Market reading", "Lectura del mercado")}
      right={
        <span
          className={`font-mono text-[11px] ${data.is_stale ? "text-warn" : "text-neutral-600"}`}
        >
          {data.is_stale ? tr("stale · ", "vieja · ") : ""}
          {tr("bar of", "vela del")} {stamp(data.forecast_open_time)}
        </span>
      }
    >
      <p
        className={`text-base leading-snug ${
          /ELEVADO|ELEVATED/.test(data.headline) ? "text-warn" : "text-neutral-100"
        }`}
      >
        {data.headline}
      </p>
      <div className="mt-3 grid grid-cols-1 gap-3 lg:grid-cols-2">
        {data.sections.map((s) => (
          <section
            key={s.title}
            className={`rounded border p-3 ${
              s.tone === "warn"
                ? "border-warn/40 bg-warn/5"
                : "border-neutral-800 bg-neutral-950/60"
            } ${s.basis === "alcance" ? "lg:col-span-2" : ""}`}
          >
            <header className="mb-1 flex items-baseline justify-between gap-2">
              <h3 className="text-xs uppercase tracking-wider text-neutral-400">{s.title}</h3>
              <Badge tone={BASIS_TONE[s.basis]}>{basisText(lang, s.basis)}</Badge>
            </header>
            <p
              className={`text-sm leading-relaxed ${
                s.tone === "muted" ? "text-neutral-500" : "text-neutral-300"
              }`}
            >
              {s.text}
            </p>
          </section>
        ))}
      </div>
    </Card>
  );
}
