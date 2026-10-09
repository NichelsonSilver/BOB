import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { Badge, type BadgeTone } from "../components/common/Card";
import { useTarget } from "../hooks/useTarget";
import { useLang } from "../i18n";
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
  const { tr } = useLang();
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
      <span className="mt-auto text-xs text-neutral-500 group-hover:text-neutral-300">{tr("Open →", "Abrir →")}</span>
    </Link>
  );
}

function ago(ms: number, en: boolean): string {
  const h = (Date.now() - ms) / 3_600_000;
  if (h < 48) {
    const n = Math.max(1, Math.round(h));
    return en ? `${n} h ago` : `hace ${n} h`;
  }
  const d = Math.floor(h / 24);
  return en ? `${d} days ago` : `hace ${d} días`;
}

export function CryptoPage() {
  const { lang, tr } = useLang();
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
  const descriptive: Backing = { tone: "neutral", text: tr("descriptive", "descriptivo") };

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <header>
        <h1 className="text-2xl font-semibold text-neutral-100">{tr("Crypto", "Cripto")}</h1>
        <p className="mt-1 text-sm text-neutral-400">
          {tr(
            "MAREA's crypto module. Each page says what it measures and how much backing it has; none says what to buy.",
            "El módulo cripto de MAREA. Cada página dice qué mide y cuánto respaldo tiene; ninguna dice qué comprar.",
          )}
        </p>
      </header>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <Section
          to="/cripto/panorama"
          title={tr("Panorama", "Panorama")}
          question={tr(
            "Where is crypto in its cycle, and how much leverage is there?",
            "¿En qué parte del ciclo está cripto y cuánto apalancamiento hay?",
          )}
          backing={descriptive}
        >
          {tr(
            "Seven readings: BTC cycle, breadth, altseason, stablecoins, funding, options and recession. They describe the moment; they do not forecast.",
            "Siete lecturas: ciclo de BTC, amplitud, altseason, stablecoins, funding, opciones y recesión. Describen el momento; no pronostican.",
          )}
        </Section>

        <Section
          to="/cripto/panel"
          title={tr("Daily panel", "Panel diario")}
          question={tr(
            "Does the ranking across cryptocurrencies that passed the historical test hold on new days?",
            "¿El orden entre criptomonedas que pasó la prueba histórica se sostiene en días nuevos?",
          )}
          backing={{ tone: "warn", text: tr("under test", "en prueba") }}
          live={
            st ? (
              <span>
                <span className="font-mono">
                  {st.valid} / {st.interim_valid_days}
                </span>{" "}
                {tr("valid days until the interim review", "días válidos hasta la revisión intermedia")}
              </span>
            ) : panel.error ? (
              <span className="text-neutral-500">{tr("panel status unavailable", "sin acceso al estado del panel")}</span>
            ) : null
          }
        >
          {tr(
            "It shows progress, not the result: the result is not looked at before the interim review.",
            "Muestra el avance, no el resultado: el resultado no se mira antes de la revisión intermedia.",
          )}
        </Section>

        <Section
          to="/cripto/riesgo"
          title={tr("Trade risk", "Riesgo por trade")}
          question={tr(
            "If I enter with this stop, how much do I lose, where do I get liquidated and with what size?",
            "Si entro con este stop, ¿cuánto pierdo, dónde me liquidan y con qué tamaño?",
          )}
          backing={{ tone: "ok", text: tr("arithmetic", "aritmética") }}
        >
          {tr(
            "Calculator: from the risk you accept to the position size, costs included. It does not pick the entry or the direction. It works even when the 15m analysis is off.",
            "Calculadora: del riesgo que aceptás al tamaño de la posición, con costos incluidos. No elige la entrada ni la dirección. Funciona aunque el análisis de 15m esté apagado.",
          )}
        </Section>

        <Section
          to="/cripto/mapa"
          title={tr("Technical map", "Mapa técnico")}
          question={tr(
            "Where are the levels the market watches, to place the stop?",
            "¿Dónde están los niveles que el mercado mira, para ubicar el stop?",
          )}
          backing={descriptive}
        >
          {tr(
            "Liquidity zones, order blocks, supports and moving averages on 15m, 1h and 4h. Entry rules built on these levels were measured and did not beat chance; the only use offered is placing the stop.",
            "Zonas de liquidez, order blocks, soportes y medias en 15m, 1h y 4h. Las reglas de entrada con estos niveles se midieron y no le ganaron al azar; el único uso que se ofrece es poner el stop.",
          )}
        </Section>

        <Section
          to="/cripto/senal-15m"
          title={tr("15m signal", "Señal 15m")}
          question={tr(
            "How much will the price move over the next few hours, and within what range?",
            "¿Cuánto se va a mover el precio en las próximas horas y en qué rango?",
          )}
          backing={{ tone: "muted", text: tr("frozen", "congelada") }}
          live={
            forecast ? (
              <span>
                {tr("last forecast", "último pronóstico")}{" "}
                <span className="font-mono">{ago(forecast.open_time, lang === "en")}</span>
              </span>
            ) : null
          }
        >
          {tr(
            "The live analysis has not run since 26-09. What you see is the last forecast issued, with its age above; an old level does not describe today's market.",
            "El análisis en vivo no corre desde el 26-09. Lo que se ve es el último pronóstico emitido, con su edad arriba; un nivel viejo no describe el mercado de hoy.",
          )}
        </Section>
      </div>
    </div>
  );
}
