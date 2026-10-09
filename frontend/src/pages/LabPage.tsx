import { useQuery } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";

import { Badge, type BadgeTone } from "../components/common/Card";
import { pick, useLang } from "../i18n";
import { api } from "../lib/api";
import { count, isNum, num, pct, pValue, signedPct } from "../lib/format";
import type { LabExperiment, LabStatus } from "../types";

/**
 * Laboratorio ▸ Resumen (M6.4). Todo lo que el proyecto midió, con su
 * veredicto, en un solo lugar: lo que pasó, lo que no, y lo que está en curso.
 *
 * **Las cifras y los veredictos salen de `/api/lab/experiments`**, que los lee
 * de cada artefacto versionado; acá solo se redactan, en inglés y en español.
 * Si un experimento se remide, la página cambia sola.
 *
 * Los resultados negativos van con el mismo peso que los positivos y en gris,
 * no en rojo: un "no pasó" bien medido es información, no una falla. Y un
 * "pasó" tampoco es una invitación a operar: pasar una prueba habilita medir
 * (CLAUDE.md § Backtesting engine).
 *
 * El forward del panel diario aparece como "en curso" con su avance, nunca con
 * su resultado: la Enmienda 1 prohíbe mirar el IC antes de las 90 fechas.
 */
type F = Record<string, unknown>;
type Tr = <T>(en: T, es: T) => T;
type Text = [en: string, es: string];

const n = (f: F, k: string): number | null => (isNum(f[k] as number) ? (f[k] as number) : null);
const obj = (f: F, k: string): F =>
  f[k] && typeof f[k] === "object" && !Array.isArray(f[k]) ? (f[k] as F) : {};
const list = (f: F, k: string): F[] => (Array.isArray(f[k]) ? (f[k] as F[]) : []);
function ci(f: F, k: string, fmt: (v: number) => string = (v) => num(v, 3)): string {
  const v = f[k];
  return Array.isArray(v) && v.length === 2 && isNum(v[0]) && isNum(v[1]) ? `[${fmt(v[0])}, ${fmt(v[1])}]` : "—";
}

type Status = LabStatus | "en_curso";

const STATUS: Record<Status, { tone: BadgeTone; text: Text }> = {
  pasa: { tone: "ok", text: ["passed", "pasó"] },
  no_pasa: { tone: "neutral", text: ["did not pass", "no pasó"] },
  control_ok: { tone: "neutral", text: ["clean control", "control limpio"] },
  control_falla: { tone: "bad", text: ["control failed", "control falló"] },
  informativo: { tone: "muted", text: ["informative", "informativo"] },
  en_curso: { tone: "warn", text: ["in progress", "en curso"] },
};

type Spec = {
  title: Text;
  question: Text;
  scale: "15m" | "diario" | "macro";
  link?: { to: string; label: Text };
  summary: (f: F, tr: Tr) => ReactNode;
  note?: Text;
  detail: (f: F, tr: Tr) => ReactNode;
};

const Mono = ({ children }: { children: ReactNode }) => <span className="whitespace-nowrap font-mono">{children}</span>;

const BACKTEST_LINK = { to: "/lab/backtest", label: ["Backtest", "Backtest"] as Text };
const RISK_LINK = { to: "/cripto/riesgo", label: ["Trade risk", "Riesgo por trade"] as Text };
const PANEL_LINK = { to: "/cripto/panel", label: ["Daily panel", "Panel diario"] as Text };

const SPECS: Record<string, Spec> = {
  gate_direction: {
    title: ["Direction at 15 minutes", "Dirección a 15 minutos"],
    question: [
      "Does the model know whether ETH will hit the profit target or the stop first?",
      "¿El modelo sabe si ETH va a tocar antes la ganancia o el stop?",
    ],
    scale: "15m",
    link: BACKTEST_LINK,
    summary: (f, tr) =>
      tr(
        <>
          None of the <Mono>{count(n(f, "n_runs"))}</Mono> variants passed. The best one separates cases with AUC{" "}
          <Mono>{num(n(f, "best_auc"), 3)}</Mono> and the test requires more than <Mono>{num(n(f, "min_auc"), 2)}</Mono>.
        </>,
        <>
          Ninguna de las <Mono>{count(n(f, "n_runs"))}</Mono> variantes pasó. La mejor separa los casos con AUC{" "}
          <Mono>{num(n(f, "best_auc"), 3)}</Mono> y la prueba pide más de <Mono>{num(n(f, "min_auc"), 2)}</Mono>.
        </>,
      ),
    note: [
      "That is why the Safety % on the 15m signal is drawn in grey.",
      "Por eso el % de Seguridad de la Señal 15m se dibuja en gris.",
    ],
    detail: (f, tr) =>
      tr(
        `AUC between ${num(n(f, "worst_auc"), 3)} and ${num(n(f, "best_auc"), 3)} over ${count(n(f, "n_bars"))} bars; enabled variants: ${count(n(f, "n_enabled"))}. Besides AUC, the test requires BSS > 0 and calibration error < 10 pp in both directions.`,
        `AUC entre ${num(n(f, "worst_auc"), 3)} y ${num(n(f, "best_auc"), 3)} sobre ${count(n(f, "n_bars"))} velas; variantes habilitadas: ${count(n(f, "n_enabled"))}. Además del AUC, la prueba pide BSS > 0 y error de calibración < 10 pp en las dos direcciones.`,
      ),
  },
  gate_volatility: {
    title: ["Volatility at 15 minutes", "Volatilidad a 15 minutos"],
    question: ["How much will ETH move over the next 4 hours?", "¿Cuánto se va a mover ETH en las próximas 4 horas?"],
    scale: "15m",
    link: BACKTEST_LINK,
    summary: (f, tr) =>
      tr(
        <>
          It forecasts it better than the classic methods (EWMA and HAR), and the difference is not luck (
          <Mono>p {pValue(n(f, "dm_p_vs_ewma"))}</Mono>).
        </>,
        <>
          Lo pronostica mejor que los métodos clásicos (EWMA y HAR), y la diferencia no es suerte (
          <Mono>p {pValue(n(f, "dm_p_vs_ewma"))}</Mono>).
        </>,
      ),
    note: [
      "It is the basis of the levels, the price cone and the regime filter.",
      "Es la base de los niveles, del cono de precio y del filtro de régimen.",
    ],
    detail: (f, tr) =>
      tr(
        `R² vs EWMA ${signedPct(n(f, "r2_vs_ewma"), 1)}; Diebold-Mariano vs EWMA p=${pValue(n(f, "dm_p_vs_ewma"))}, vs HAR p=${pValue(n(f, "dm_p_vs_har"))}; ${count(n(f, "n_samples"))} out-of-sample observations.`,
        `R² contra EWMA ${signedPct(n(f, "r2_vs_ewma"), 1)}; Diebold-Mariano vs EWMA p=${pValue(n(f, "dm_p_vs_ewma"))}, vs HAR p=${pValue(n(f, "dm_p_vs_har"))}; ${count(n(f, "n_samples"))} muestras fuera de muestra.`,
      ),
  },
  forward_15m: {
    title: ["Volatility on new days", "Volatilidad en días nuevos"],
    question: [
      "Did what passed in the backtest hold up when issuing forecasts live?",
      "¿Lo que pasó en el backtest se sostuvo emitiendo en vivo?",
    ],
    scale: "15m",
    link: { to: "/lab/forward-15m", label: ["15m forward validation", "Validación forward 15m"] },
    summary: (f, tr) => {
      const c = obj(f, "cones");
      const e80 = pct(n(obj(c, "0.20"), "empirical"), 1);
      const e95 = pct(n(obj(c, "0.05"), "empirical"), 1);
      const N = count(n(f, "n_resolved"));
      return tr(
        <>
          Over <Mono>{N}</Mono> forecasts issued live, the 80% range contained the price <Mono>{e80}</Mono> of the time
          and the 95% range <Mono>{e95}</Mono>. Both are within their margin of error.
        </>,
        <>
          Con <Mono>{N}</Mono> pronósticos emitidos en vivo, el rango del 80% contuvo al precio el <Mono>{e80}</Mono>{" "}
          de las veces y el del 95% el <Mono>{e95}</Mono>. Los dos quedan dentro de su margen de error.
        </>,
      );
    },
    note: ["Sigma has a ceiling: in sharp spikes it falls short.", "La sigma tiene techo: en los picos fuertes se queda corta."],
    detail: (f, tr) => {
      const c = obj(f, "cones");
      const c80 = ci(obj(c, "0.20"), "ci95", (v) => pct(v, 1));
      const c95 = ci(obj(c, "0.05"), "ci95", (v) => pct(v, 1));
      const w = `${String(f.date_from ?? "—")} → ${String(f.date_to ?? "—")} UTC`;
      return tr(
        `Window ${w}. 80% coverage: block-bootstrap 95% CI ${c80}; 95%: ${c95}. DM vs EWMA p=${pValue(n(f, "dm_p_vs_ewma"))}.`,
        `Ventana ${w}. Cobertura 80%: IC95% por bloques ${c80}; 95%: ${c95}. DM vs EWMA p=${pValue(n(f, "dm_p_vs_ewma"))}.`,
      );
    },
  },
  riskfilter: {
    title: ["Regime filter", "Filtro de régimen"],
    question: ["Is it useful to know when NOT to enter?", "¿Sirve saber cuándo NO entrar?"],
    scale: "15m",
    link: RISK_LINK,
    summary: (f, tr) => {
      const r = pct(n(f, "reject_rate"), 1);
      const c = pct(n(f, "capture_rate"), 1);
      const lev = `${num(n(f, "leverage"), 0)}x`;
      const b = pct(n(f, "baseline_capture_rate"), 1);
      return tr(
        <>
          Skipping <Mono>{r}</Mono> of the moments avoids <Mono>{c}</Mono> of liquidations at <Mono>{lev}</Mono>. The
          same rule with a classic method avoids <Mono>{b}</Mono>.
        </>,
        <>
          Descartando el <Mono>{r}</Mono> de los momentos se evita el <Mono>{c}</Mono> de las liquidaciones a{" "}
          <Mono>{lev}</Mono>. La misma regla con un método clásico evita el <Mono>{b}</Mono>.
        </>,
      );
    },
    note: [
      "It does not say which way to enter. Measured without a stop and at 20x; at low leverage there were not enough events.",
      "No dice hacia dónde entrar. Medido sin stop y a 20x; a leverage bajo no hubo eventos suficientes.",
    ],
    detail: (f, tr) =>
      tr(
        `Lift ${num(n(f, "lift"), 3)}, 95% CI ${ci(f, "lift_ci95")} (chance gives 1.0).`,
        `Lift ${num(n(f, "lift"), 3)}, IC95% ${ci(f, "lift_ci95")} (el azar da 1.0).`,
      ),
  },
  stopfilter: {
    title: ["Regime filter with a stop", "Filtro de régimen con stop"],
    question: [
      "With a fixed stop, does the regime change how often it gets hit?",
      "Con un stop fijo, ¿el régimen cambia cuántas veces salta?",
    ],
    scale: "15m",
    link: RISK_LINK,
    summary: (f, tr) => {
      const r = obj(f, "mean_r");
      const s = pct(n(f, "stop_pct"), 0);
      const he = pct(n(f, "hit_rate_elevated"), 1);
      const hn = pct(n(f, "hit_rate_normal"), 1);
      const rn = `${num(n(r, "normal"), 2)} R`;
      const re = `${num(n(r, "elevado"), 2)} R`;
      return tr(
        <>
          With a <Mono>{s}</Mono> stop, in an elevated regime it gets hit <Mono>{he}</Mono> of the time, against{" "}
          <Mono>{hn}</Mono> in a normal regime. The expected gain per trade does not change (<Mono>{rn}</Mono> and{" "}
          <Mono>{re}</Mono>).
        </>,
        <>
          Con stop al <Mono>{s}</Mono>, en régimen elevado salta el <Mono>{he}</Mono> de las veces, contra{" "}
          <Mono>{hn}</Mono> en régimen normal. La ganancia esperada por trade no cambia (<Mono>{rn}</Mono> y{" "}
          <Mono>{re}</Mono>).
        </>,
      );
    },
    note: [
      "It changes how often stops are hit, and with that the losing streaks; not the gain.",
      "Cambia la frecuencia de los stops, y con ella las rachas; no la ganancia.",
    ],
    detail: (f, tr) => {
      const g = obj(f, "calibration_gap_pp");
      return tr(
        `Hit ratio ×${num(n(f, "hit_rate_ratio"), 2)}. The calculator's P(touch) is calibrated: gap of ${num(n(g, "normal"), 1)} pp in a normal regime and ${num(n(g, "elevado"), 1)} pp in an elevated one (negative = overestimates).`,
        `Razón de saltos ×${num(n(f, "hit_rate_ratio"), 2)}. La P(toque) de la calculadora está calibrada: brecha ${num(n(g, "normal"), 1)} pp en régimen normal y ${num(n(g, "elevado"), 1)} pp en elevado (negativo = sobreestima).`,
      );
    },
  },
  sigmatail: {
    title: ["Volatility tail", "Cola de la volatilidad"],
    question: [
      "Does a quantile model cover the extreme moves that sigma misses?",
      "¿Un modelo de cuantil cubre los movimientos extremos que la sigma no ve?",
    ],
    scale: "15m",
    summary: (f, tr) => (
      <>
        {tr("No.", "No.")}{" "}
        {list(f, "levels").map((l, i) => {
          const nom = pct(n(l, "nominal"), 0);
          const ex = pct(n(l, "exceedance"), 1);
          return (
            <span key={i}>
              {tr(
                <>
                  It should have been exceeded <Mono>{nom}</Mono> of the time and was exceeded <Mono>{ex}</Mono>.{" "}
                </>,
                <>
                  Debía superarse el <Mono>{nom}</Mono> de las veces y se superó el <Mono>{ex}</Mono>.{" "}
                </>,
              )}
            </span>
          );
        })}
      </>
    ),
    note: [
      "A self-correcting version does calibrate, but it was found after looking at the data and is not adopted.",
      "Una versión que se corrige sola sí calibra, pero se encontró después de mirar los datos y no se adopta.",
    ],
    detail: (f, tr) => (
      <>
        {list(f, "levels").map((l, i) => (
          <div key={i}>
            {tr(
              `q=${num(n(l, "q"), 2)}: exceedance ${pct(n(l, "exceedance"), 1)}, 95% CI ${ci(l, "exceedance_ci95", (v) => pct(v, 1))}; adaptive version calibrates: ${l.adaptive_calibrated ? "yes" : "no"} (post hoc).`,
              `q=${num(n(l, "q"), 2)}: excedencia ${pct(n(l, "exceedance"), 1)}, IC95% ${ci(l, "exceedance_ci95", (v) => pct(v, 1))}; adaptativa calibra: ${l.adaptive_calibrated ? "sí" : "no"} (post-hoc).`,
            )}
          </div>
        ))}
      </>
    ),
  },
  technical: {
    title: ["Technical analysis rules", "Reglas de análisis técnico"],
    question: [
      "Does entering on liquidity sweeps or order blocks leave a profit?",
      "¿Entrar con barridos de liquidez u order blocks deja ganancia?",
    ],
    scale: "15m",
    link: { to: "/cripto/mapa", label: ["Technical map", "Mapa técnico"] },
    summary: (f, tr) => {
      const rules = list(f, "rules");
      const rs = rules.map((r) => n(r, "mean_r")).filter(isNum);
      const lo = `${num(rs.length ? Math.min(...rs) : null, 2)} R`;
      const hi = `${num(rs.length ? Math.max(...rs) : null, 2)} R`;
      const ne = count(n(f, "n_exploratory"));
      const np = count(n(f, "n_exploratory_positive"));
      return tr(
        <>
          No. The <Mono>{rules.length}</Mono> main rules lose between <Mono>{lo}</Mono> and <Mono>{hi}</Mono> per trade,
          the same as entering at random with the same stop and target. Of <Mono>{ne}</Mono> extra variants,{" "}
          <Mono>{np}</Mono> came out positive.
        </>,
        <>
          No. Las <Mono>{rules.length}</Mono> reglas principales pierden entre <Mono>{lo}</Mono> y <Mono>{hi}</Mono> por
          trade, lo mismo que entrar al azar con igual stop y objetivo. De <Mono>{ne}</Mono> variantes extra,{" "}
          <Mono>{np}</Mono> salieron positivas.
        </>,
      );
    },
    note: [
      "The map's levels are kept for placing the stop, not for choosing the entry.",
      "Los niveles del mapa quedan para ubicar el stop, no para elegir la entrada.",
    ],
    detail: (f, tr) => (
      <>
        {list(f, "rules").map((r, i) => (
          <div key={i}>
            {String(r.rule)} {String(r.timeframe)}: {num(n(r, "mean_r"), 3)} R ({tr("random", "azar")}{" "}
            {num(n(r, "placebo_mean_r"), 3)} R) — {String(r.verdict) === "NO PASA" ? tr("DOES NOT PASS", "NO PASA") : String(r.verdict)}
          </div>
        ))}
        <div>{tr(`Round-trip cost ${pct(n(f, "cost_pct"), 2)}.`, `Costo ida y vuelta ${pct(n(f, "cost_pct"), 2)}.`)}</div>
      </>
    ),
  },
  panel_offline: {
    title: ["Ranking across cryptocurrencies", "Orden entre criptomonedas"],
    question: [
      "Can we rank which coins will beat the median over the next few days?",
      "¿Se puede ordenar qué monedas van a rendir más que la mediana en los próximos días?",
    ],
    scale: "diario",
    link: PANEL_LINK,
    summary: (f, tr) => {
      const ns = count(n(f, "n_symbols"));
      const nd = count(n(f, "n_dates"));
      return tr(
        <>
          On history, yes: across <Mono>{ns}</Mono> coins and <Mono>{nd}</Mono> days, the ranking beats chance and the
          simple rules.
        </>,
        <>
          En el histórico sí: sobre <Mono>{ns}</Mono> monedas y <Mono>{nd}</Mono> días, el orden acierta más que el azar
          y que las reglas simples.
        </>,
      );
    },
    note: [
      "It is a test on the past. It trades long and short at once, with a large loss tail; it is now being tested on new days.",
      "Es una prueba sobre el pasado. Se opera largo y corto a la vez, con una cola de pérdidas grande; se está probando en días nuevos.",
    ],
    detail: (f, tr) =>
      tr(
        `Horizon ${num(n(f, "horizon_days"), 0)} days. AUC ${num(n(f, "auc"), 4)}; IC ${num(n(f, "ic"), 4)}, 95% CI ${ci(f, "ic_ci95", (v) => num(v, 4))}; net spread per period ${signedPct(n(f, "spread_net"), 2)}, 95% CI ${ci(f, "spread_net_ci95", (v) => signedPct(v, 2))}.`,
        `Horizonte ${num(n(f, "horizon_days"), 0)} días. AUC ${num(n(f, "auc"), 4)}; IC ${num(n(f, "ic"), 4)}, IC95% ${ci(f, "ic_ci95", (v) => num(v, 4))}; spread neto por período ${signedPct(n(f, "spread_net"), 2)}, IC95% ${ci(f, "spread_net_ci95", (v) => signedPct(v, 2))}.`,
      ),
  },
  panel_placebo: {
    title: ["Control: the same panel at random", "Control: el mismo panel al azar"],
    question: ["Does the test manufacture an edge where there is none?", "¿La prueba fabrica ventaja donde no la hay?"],
    scale: "diario",
    summary: (f, tr) => {
      const ic = num(n(f, "ic"), 4);
      const sp = signedPct(n(f, "spread_net"), 2);
      return tr(
        <>
          No. Shuffling each day&apos;s outcomes at random, the ranking correlation drops to <Mono>{ic}</Mono> (zero,
          within the margin) and the pair to <Mono>{sp}</Mono>.
        </>,
        <>
          No. Mezclando al azar los resultados de cada día, la correlación del orden cae a <Mono>{ic}</Mono> (cero,
          dentro del margen) y el par a <Mono>{sp}</Mono>.
        </>,
      );
    },
    detail: (f, tr) =>
      tr(
        `AUC ${num(n(f, "auc"), 4)}; 95% CI of the IC ${ci(f, "ic_ci95", (v) => num(v, 4))}.`,
        `AUC ${num(n(f, "auc"), 4)}; IC95% del IC ${ci(f, "ic_ci95", (v) => num(v, 4))}.`,
      ),
  },
  macro_power: {
    title: ["Power of the macro study", "Potencia del estudio macro"],
    question: [
      "With the data that exist, how large must an effect be to be seen?",
      "Con los datos que existen, ¿qué tan grande tiene que ser un efecto para verlo?",
    ],
    scale: "macro",
    summary: (f, tr) => {
      const b = num(n(f, "independent_blocks"), 0);
      const m = num(n(f, "mde"), 3);
      return tr(
        <>
          Large: with about <Mono>{b}</Mono> independent blocks, the smallest detectable ranking correlation is{" "}
          <Mono>{m}</Mono>.
        </>,
        <>
          Grande: con unos <Mono>{b}</Mono> bloques independientes, la menor correlación de orden detectable es{" "}
          <Mono>{m}</Mono>.
        </>,
      );
    },
    note: [
      "It approves nothing: it says what can be seen with these data.",
      "No aprueba nada: dice qué se puede llegar a ver con estos datos.",
    ],
    detail: (f, tr) => {
      const v = String(f.verdict ?? "—");
      const ven = v === "potencia solo para un efecto grande" ? "power only for a large effect" : v;
      return tr(
        `Horizon ${num(n(f, "horizon_weeks"), 0)} weeks. Artifact reading: ${ven}.`,
        `Horizonte ${num(n(f, "horizon_weeks"), 0)} semanas. Lectura del artefacto: ${v}.`,
      );
    },
  },
  macro_ranking: {
    title: ["Ranking across asset classes", "Ranking entre clases de activo"],
    question: [
      "Can we rank which class (equities, bonds, gold, copper…) will do best over 4 weeks?",
      "¿Se puede ordenar qué clase (acciones, bonos, oro, cobre…) va a rendir más en 4 semanas?",
    ],
    scale: "macro",
    link: { to: "/flujos", label: ["Flows", "Flujos"] },
    summary: (f, tr) => {
      const ic = num(n(f, "ic"), 3);
      return tr(
        <>
          Not with these data: the ranking correlation is <Mono>{ic}</Mono>, with a margin that includes zero, and it
          ties with the simple momentum rule. Adding liquidity did not help.
        </>,
        <>
          No con estos datos: la correlación del orden da <Mono>{ic}</Mono>, con un margen que incluye el cero, y empata
          con la regla simple de impulso. Agregar liquidez no ayudó.
        </>,
      );
    },
    note: [
      "That is why MAREA macro is monitoring: it describes, it does not forecast.",
      "Por eso MAREA macro es monitoreo: describe, no pronostica.",
    ],
    detail: (f, tr) =>
      tr(
        `95% CI ${ci(f, "ic_ci95")}; against ${String(f.best_baseline ?? "—")}, paired difference 95% CI ${ci(f, "paired_ci95")}; contribution of the liquidity block ${num(n(f, "liquidity_contribution"), 3)}.`,
        `IC95% ${ci(f, "ic_ci95")}; contra ${String(f.best_baseline ?? "—")}, diferencia pareada IC95% ${ci(f, "paired_ci95")}; aporte del bloque de liquidez ${num(n(f, "liquidity_contribution"), 3)}.`,
      ),
  },
};

const SCALES: { key: Spec["scale"]; title: Text; blurb: Text }[] = [
  {
    key: "15m",
    title: ["Crypto at 15 minutes", "Cripto a 15 minutos"],
    blurb: ["ETH perpetual, two years of 15-minute bars.", "ETH perpetuo, dos años de velas de 15 minutos."],
  },
  {
    key: "diario",
    title: ["Daily crypto", "Cripto diario"],
    blurb: [
      "Every coin on Binance, including the ones that were delisted.",
      "Todas las monedas de Binance, incluidas las que dejaron de cotizar.",
    ],
  },
  {
    key: "macro",
    title: ["Weekly macro", "Macro semanal"],
    blurb: ["Asset classes through ETFs, since 2011.", "Clases de activo vía ETFs, desde 2011."],
  },
];

function ExperimentCard({
  spec,
  status,
  figures,
  exp,
  extra,
}: {
  spec: Spec;
  status: Status;
  figures: F;
  exp?: LabExperiment;
  extra?: ReactNode;
}) {
  const { lang, tr } = useLang();
  const [open, setOpen] = useState(false);
  const s = STATUS[status];
  const summary = spec.summary(figures, tr);
  return (
    <section className="flex flex-col gap-2 rounded-lg border border-neutral-800 bg-neutral-900/70 p-4">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-base font-semibold text-neutral-100">{pick(lang, ...spec.title)}</h3>
        <Badge tone={s.tone}>{pick(lang, ...s.text)}</Badge>
      </header>
      <p className="text-xs text-neutral-500">{pick(lang, ...spec.question)}</p>
      {summary && <p className="text-sm text-neutral-200">{summary}</p>}
      {spec.note && <p className="text-xs text-neutral-500">{pick(lang, ...spec.note)}</p>}
      {extra}
      <footer className="mt-auto flex flex-wrap items-center justify-between gap-2 pt-2 text-[11px] text-neutral-600">
        <span>{exp?.date ? `${tr("measured on", "medido el")} ${exp.date}` : ""}</span>
        <span className="flex gap-1">
          {spec.link && (
            <Link to={spec.link.to} className="rounded px-2 py-0.5 text-neutral-400 hover:bg-neutral-800 hover:text-accent">
              {pick(lang, ...spec.link.label)} →
            </Link>
          )}
          {exp && (
            <button
              type="button"
              onClick={() => setOpen((o) => !o)}
              aria-expanded={open}
              className="rounded px-2 py-0.5 text-neutral-400 hover:bg-neutral-800 hover:text-accent"
            >
              {open ? tr("Hide detail", "Ocultar detalle") : tr("Show detail", "Ver detalle")}
            </button>
          )}
        </span>
      </footer>
      {open && exp && (
        <div className="space-y-1 border-t border-neutral-800 pt-2 text-xs text-neutral-400">
          <div>{spec.detail(figures, tr)}</div>
          <div className="text-neutral-500">
            {tr("Criterion", "Criterio")}: {exp.criterion_doc ?? tr("no pre-registration of its own", "sin pre-registro propio")}
            {exp.criterion_commit && (
              <>
                {" "}
                (commit <span className="font-mono">{exp.criterion_commit}</span>)
              </>
            )}
          </div>
          <div className="break-all font-mono text-[11px] text-neutral-600">artifacts/{exp.artifact_id}</div>
        </div>
      )}
    </section>
  );
}

const PANEL_FORWARD: Spec = {
  title: ["Ranking across cryptocurrencies, on new days", "Orden entre criptomonedas, en días nuevos"],
  question: [
    "Does the ranking that passed the historical test hold with forecasts issued before the outcome?",
    "¿El orden que pasó la prueba histórica se sostiene con pronósticos emitidos antes del resultado?",
  ],
  scale: "diario",
  link: PANEL_LINK,
  summary: () => null,
  note: [
    "The result is not looked at before the interim review.",
    "El resultado no se mira antes de la revisión intermedia.",
  ],
  detail: () => null,
};

export function LabPage() {
  const { lang, tr } = useLang();
  const q = useQuery({ queryKey: ["lab", "experiments"], queryFn: () => api.labExperiments(), retry: false });
  const panel = useQuery({
    queryKey: ["macro", "panel-status"],
    queryFn: () => api.panelStatus(),
    retry: false,
    staleTime: 600_000,
  });

  const exps = q.data?.experiments ?? [];
  const counts = q.data?.counts ?? {};
  const st = panel.data;
  const controls = (counts.control_ok ?? 0) + (counts.control_falla ?? 0);

  return (
    <div className="mx-auto max-w-6xl space-y-8">
      <header>
        <h1 className="text-2xl font-semibold text-neutral-100">{tr("Lab", "Laboratorio")}</h1>
        <p className="mt-1 text-sm text-neutral-400">
          {tr(
            "Everything that was measured, with its result. Each test fixed its criterion before running, and a well-measured “did not pass” is worth as much as a “passed”. Passing a test allows further measurement, not trading.",
            "Todo lo que se midió, con su resultado. Cada prueba fijó su criterio antes de correr, y un “no pasó” bien medido vale tanto como un “pasó”. Pasar una prueba habilita seguir midiendo, no operar.",
          )}
        </p>
        {q.data && (
          <p className="mt-3 text-sm text-neutral-300">
            <Mono>{counts.pasa ?? 0}</Mono> {tr("passed", "pasaron")} · <Mono>{counts.no_pasa ?? 0}</Mono>{" "}
            {tr("did not pass", "no pasaron")} · <Mono>{st ? 1 : 0}</Mono> {tr("in progress", "en curso")} ·{" "}
            <Mono>{controls}</Mono>{" "}
            {counts.control_falla ? tr("control failed", "control falló") : tr("clean control", "control limpio")} ·{" "}
            <Mono>{counts.informativo ?? 0}</Mono> {tr("informative", "informativa")}
          </p>
        )}
      </header>

      {q.error && (
        <p className="text-sm text-danger">
          {tr("Could not read the experiment log", "No se pudo leer el registro de experimentos")}: {String(q.error)}
        </p>
      )}
      {q.isLoading && <p className="text-sm text-neutral-500">{tr("reading artifacts…", "leyendo artefactos…")}</p>}

      {q.data &&
        SCALES.map((sc) => {
          const items = exps.filter((e) => SPECS[e.id]?.scale === sc.key);
          const showForward = sc.key === "diario" && st;
          if (!items.length && !showForward) return null;
          return (
            <section key={sc.key} className="space-y-3">
              <div>
                <h2 className="text-lg font-semibold text-neutral-200">{pick(lang, ...sc.title)}</h2>
                <p className="text-xs text-neutral-500">{pick(lang, ...sc.blurb)}</p>
              </div>
              <div className="grid items-start gap-4 md:grid-cols-2 xl:grid-cols-3">
                {items.map((e) => (
                  <ExperimentCard key={e.id} spec={SPECS[e.id]} status={e.status} figures={e.figures} exp={e} />
                ))}
                {showForward && (
                  <ExperimentCard
                    spec={PANEL_FORWARD}
                    status="en_curso"
                    figures={{}}
                    extra={
                      <p className="text-sm text-neutral-200">
                        <Mono>
                          {st.valid} / {st.interim_valid_days}
                        </Mono>{" "}
                        {tr(
                          "valid days until the interim review; the final cutoff is at",
                          "días válidos hasta la revisión intermedia; el corte final es a los",
                        )}{" "}
                        <Mono>{st.cutoff_valid_days}</Mono>.
                      </p>
                    }
                  />
                )}
              </div>
            </section>
          );
        })}
    </div>
  );
}
