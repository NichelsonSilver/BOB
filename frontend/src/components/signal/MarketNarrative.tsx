import { Badge, Card } from "../common/Card";
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

export function MarketNarrative({ data }: { data: NarrativeResponse }) {
  return (
    <Card
      title="Lectura del mercado"
      right={
        <span
          className={`font-mono text-[11px] ${data.is_stale ? "text-warn" : "text-neutral-600"}`}
        >
          {data.is_stale ? "vieja · " : ""}vela del {stamp(data.forecast_open_time)}
        </span>
      }
    >
      <p
        className={`text-base leading-snug ${
          data.headline.includes("ELEVADO") ? "text-warn" : "text-neutral-100"
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
            } ${s.title === "Lo que esto no dice" ? "lg:col-span-2" : ""}`}
          >
            <header className="mb-1 flex items-baseline justify-between gap-2">
              <h3 className="text-xs uppercase tracking-wider text-neutral-400">{s.title}</h3>
              <Badge tone={BASIS_TONE[s.basis]}>{s.basis}</Badge>
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
