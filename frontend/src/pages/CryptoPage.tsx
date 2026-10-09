import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { Badge, type BadgeTone } from "../components/common/Card";
import { useTarget } from "../hooks/useTarget";
import { api } from "../lib/api";

/**
 * Cripto ▸ Resumen (M6.3). La puerta de la sección: qué contesta cada página
 * y con qué respaldo, antes de entrar. El respaldo va en palabras y con el
 * mismo tono en todas las tarjetas, para que "congelado" o "en prueba" no se
 * lean como "listo para usar".
 *
 * Las cifras vivas salen de la API (avance del panel, edad del último
 * pronóstico de 15m). Nada de lo que se muestra acá sugiere entrar ni salir
 * (regla 2), y el resultado del panel no aparece: la Enmienda 1 lo prohíbe
 * antes de las 90 fechas.
 */
type Backing = { tone: BadgeTone; text: string };

const DESCRIPTIVE: Backing = { tone: "neutral", text: "descriptivo" };

function Section({
  to,
  title,
  question,
  backing,
  live,
  children,
}: {
  to: string;
  title: string;
  question: string;
  backing: Backing;
  live?: ReactNode;
  children: ReactNode;
}) {
  return (
    <Link
      to={to}
      className="group flex flex-col gap-3 rounded-lg border border-neutral-800 bg-neutral-900/70 p-4 transition hover:border-neutral-600"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-base font-semibold text-neutral-100 group-hover:text-accent">{title}</h2>
        <Badge tone={backing.tone}>{backing.text}</Badge>
      </div>
      <p className="text-sm text-neutral-300">{question}</p>
      {live && <div className="text-sm text-neutral-200">{live}</div>}
      <p className="text-xs text-neutral-500">{children}</p>
      <span className="mt-auto text-xs text-neutral-500 group-hover:text-neutral-300">Abrir →</span>
    </Link>
  );
}

function ago(ms: number): string {
  const h = (Date.now() - ms) / 3_600_000;
  return h < 48 ? `hace ${Math.max(1, Math.round(h))} h` : `hace ${Math.floor(h / 24)} días`;
}

export function CryptoPage() {
  const { symbol, timeframe } = useTarget();
  const panel = useQuery({
    queryKey: ["macro", "panel-status"],
    queryFn: () => api.panelStatus(),
    retry: false,
    staleTime: 600_000,
  });
  // Misma clave que la Señal 15m: si ya se abrió, no hay segunda petición.
  const latest = useQuery({
    queryKey: ["latest", symbol, timeframe],
    queryFn: () => api.latest({ symbol, timeframe }),
    enabled: Boolean(symbol && timeframe),
    retry: false,
  });

  const st = panel.data;
  const forecast = latest.data?.forecast ?? null;

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <header>
        <h1 className="text-2xl font-semibold text-neutral-100">Cripto</h1>
        <p className="mt-1 text-sm text-neutral-400">
          El módulo cripto de MAREA. Cada página dice qué mide y cuánto respaldo tiene; ninguna dice qué comprar.
        </p>
      </header>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <Section
          to="/cripto/panorama"
          title="Panorama"
          question="¿En qué parte del ciclo está cripto y cuánto apalancamiento hay?"
          backing={DESCRIPTIVE}
        >
          Siete lecturas: ciclo de BTC, amplitud, altseason, stablecoins, funding, opciones y recesión. Describen el
          momento; no pronostican.
        </Section>

        <Section
          to="/cripto/panel"
          title="Panel diario"
          question="¿El orden entre criptomonedas que pasó la prueba histórica se sostiene en días nuevos?"
          backing={{ tone: "warn", text: "en prueba" }}
          live={
            st ? (
              <span>
                <span className="font-mono">
                  {st.valid} / {st.interim_valid_days}
                </span>{" "}
                días válidos hasta la revisión intermedia
              </span>
            ) : panel.error ? (
              <span className="text-neutral-500">sin acceso al estado del panel</span>
            ) : null
          }
        >
          Muestra el avance, no el resultado: el resultado no se mira antes de la revisión intermedia.
        </Section>

        <Section
          to="/cripto/riesgo"
          title="Riesgo por trade"
          question="Si entro con este stop, ¿cuánto pierdo, dónde me liquidan y con qué tamaño?"
          backing={{ tone: "ok", text: "aritmética" }}
        >
          Calculadora: del riesgo que aceptás al tamaño de la posición, con costos incluidos. No elige la entrada ni
          la dirección. Funciona aunque el análisis de 15m esté apagado.
        </Section>

        <Section
          to="/cripto/mapa"
          title="Mapa técnico"
          question="¿Dónde están los niveles que el mercado mira, para ubicar el stop?"
          backing={DESCRIPTIVE}
        >
          Zonas de liquidez, order blocks, soportes y medias en 15m, 1h y 4h. Las reglas de entrada con estos niveles
          se midieron y no le ganaron al azar; el único uso que se ofrece es poner el stop.
        </Section>

        <Section
          to="/cripto/senal-15m"
          title="Señal 15m"
          question="¿Cuánto se va a mover el precio en las próximas horas y en qué rango?"
          backing={{ tone: "muted", text: "congelada" }}
          live={
            forecast ? (
              <span>
                último pronóstico <span className="font-mono">{ago(forecast.open_time)}</span>
              </span>
            ) : null
          }
        >
          El análisis en vivo no corre desde el 26-09. Lo que se ve es el último pronóstico emitido, con su edad
          arriba; un nivel viejo no describe el mercado de hoy.
        </Section>
      </div>
    </div>
  );
}
