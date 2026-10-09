import { useQuery } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";

import { Badge, type BadgeTone } from "../components/common/Card";
import { api } from "../lib/api";
import { count, isNum, num, pct, pValue, signedPct } from "../lib/format";
import type { LabExperiment, LabStatus } from "../types";

/**
 * Laboratorio ▸ Resumen (M6.4). Todo lo que el proyecto midió, con su
 * veredicto, en un solo lugar: lo que pasó, lo que no, y lo que está en curso.
 *
 * **Las cifras y los veredictos salen de `/api/lab/experiments`**, que los lee
 * de cada artefacto versionado; acá solo se redactan. Si un experimento se
 * remide, la página cambia sola.
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

const n = (f: F, k: string): number | null => (isNum(f[k] as number) ? (f[k] as number) : null);
const obj = (f: F, k: string): F =>
  f[k] && typeof f[k] === "object" && !Array.isArray(f[k]) ? (f[k] as F) : {};
const list = (f: F, k: string): F[] => (Array.isArray(f[k]) ? (f[k] as F[]) : []);
function ci(f: F, k: string, fmt: (v: number) => string = (v) => num(v, 3)): string {
  const v = f[k];
  return Array.isArray(v) && v.length === 2 && isNum(v[0]) && isNum(v[1]) ? `[${fmt(v[0])}, ${fmt(v[1])}]` : "—";
}

type Status = LabStatus | "en_curso";

const STATUS: Record<Status, { tone: BadgeTone; text: string }> = {
  pasa: { tone: "ok", text: "pasó" },
  no_pasa: { tone: "neutral", text: "no pasó" },
  control_ok: { tone: "neutral", text: "control limpio" },
  control_falla: { tone: "bad", text: "control falló" },
  informativo: { tone: "muted", text: "informativo" },
  en_curso: { tone: "warn", text: "en curso" },
};

type Spec = {
  title: string;
  question: string;
  scale: "15m" | "diario" | "macro";
  link?: { to: string; label: string };
  summary: (f: F) => ReactNode;
  note?: string;
  detail: (f: F) => ReactNode;
};

const Mono = ({ children }: { children: ReactNode }) => <span className="whitespace-nowrap font-mono">{children}</span>;

const SPECS: Record<string, Spec> = {
  gate_direction: {
    title: "Dirección a 15 minutos",
    question: "¿El modelo sabe si ETH va a tocar antes la ganancia o el stop?",
    scale: "15m",
    link: { to: "/lab/backtest", label: "Backtest" },
    summary: (f) => (
      <>
        Ninguna de las <Mono>{count(n(f, "n_runs"))}</Mono> variantes pasó. La mejor separa los casos con AUC{" "}
        <Mono>{num(n(f, "best_auc"), 3)}</Mono> y la prueba pide más de <Mono>{num(n(f, "min_auc"), 2)}</Mono>.
      </>
    ),
    note: "Por eso el % de Seguridad de la Señal 15m se dibuja en gris.",
    detail: (f) => (
      <>
        AUC entre {num(n(f, "worst_auc"), 3)} y {num(n(f, "best_auc"), 3)} sobre {count(n(f, "n_bars"))} velas;
        variantes habilitadas: {count(n(f, "n_enabled"))}. Además del AUC, la prueba pide BSS &gt; 0 y error de
        calibración &lt; 10 pp en las dos direcciones.
      </>
    ),
  },
  gate_volatility: {
    title: "Volatilidad a 15 minutos",
    question: "¿Cuánto se va a mover ETH en las próximas 4 horas?",
    scale: "15m",
    link: { to: "/lab/backtest", label: "Backtest" },
    summary: (f) => (
      <>
        Lo pronostica mejor que los métodos clásicos (EWMA y HAR), y la diferencia no es suerte (<Mono>p {pValue(n(f, "dm_p_vs_ewma"))}</Mono>).
      </>
    ),
    note: "Es la base de los niveles, del cono de precio y del filtro de régimen.",
    detail: (f) => (
      <>
        R² contra EWMA {signedPct(n(f, "r2_vs_ewma"), 1)}; Diebold-Mariano vs EWMA p={pValue(n(f, "dm_p_vs_ewma"))},
        vs HAR p={pValue(n(f, "dm_p_vs_har"))}; {count(n(f, "n_samples"))} muestras fuera de muestra.
      </>
    ),
  },
  forward_15m: {
    title: "Volatilidad en días nuevos",
    question: "¿Lo que pasó en el backtest se sostuvo emitiendo en vivo?",
    scale: "15m",
    link: { to: "/lab/forward-15m", label: "Validación forward 15m" },
    summary: (f) => {
      const c = obj(f, "cones");
      const c80 = obj(c, "0.20");
      const c95 = obj(c, "0.05");
      return (
        <>
          Con <Mono>{count(n(f, "n_resolved"))}</Mono> pronósticos emitidos en vivo, el rango del 80% contuvo al
          precio el <Mono>{pct(n(c80, "empirical"), 1)}</Mono> de las veces y el del 95% el{" "}
          <Mono>{pct(n(c95, "empirical"), 1)}</Mono>. Los dos quedan dentro de su margen de error.
        </>
      );
    },
    note: "La sigma tiene techo: en los picos fuertes se queda corta.",
    detail: (f) => {
      const c = obj(f, "cones");
      return (
        <>
          Ventana {String(f.date_from ?? "—")} → {String(f.date_to ?? "—")} UTC. Cobertura 80%: IC95% por bloques{" "}
          {ci(obj(c, "0.20"), "ci95", (v) => pct(v, 1))}; 95%: {ci(obj(c, "0.05"), "ci95", (v) => pct(v, 1))}. DM vs
          EWMA p={pValue(n(f, "dm_p_vs_ewma"))}.
        </>
      );
    },
  },
  riskfilter: {
    title: "Filtro de régimen",
    question: "¿Sirve saber cuándo NO entrar?",
    scale: "15m",
    link: { to: "/cripto/riesgo", label: "Riesgo por trade" },
    summary: (f) => (
      <>
        Descartando el <Mono>{pct(n(f, "reject_rate"), 1)}</Mono> de los momentos se evita el{" "}
        <Mono>{pct(n(f, "capture_rate"), 1)}</Mono> de las liquidaciones a <Mono>{num(n(f, "leverage"), 0)}x</Mono>
        . La misma regla con un método clásico evita el <Mono>{pct(n(f, "baseline_capture_rate"), 1)}</Mono>.
      </>
    ),
    note: "No dice hacia dónde entrar. Medido sin stop y a 20x; a leverage bajo no hubo eventos suficientes.",
    detail: (f) => <>Lift {num(n(f, "lift"), 3)}, IC95% {ci(f, "lift_ci95")} (el azar da 1.0).</>,
  },
  stopfilter: {
    title: "Filtro de régimen con stop",
    question: "Con un stop fijo, ¿el régimen cambia cuántas veces salta?",
    scale: "15m",
    link: { to: "/cripto/riesgo", label: "Riesgo por trade" },
    summary: (f) => {
      const r = obj(f, "mean_r");
      return (
        <>
          Con stop al <Mono>{pct(n(f, "stop_pct"), 0)}</Mono>, en régimen elevado salta el{" "}
          <Mono>{pct(n(f, "hit_rate_elevated"), 1)}</Mono> de las veces, contra{" "}
          <Mono>{pct(n(f, "hit_rate_normal"), 1)}</Mono> en régimen normal. La ganancia esperada por trade no
          cambia (<Mono>{num(n(r, "normal"), 2)} R</Mono> y <Mono>{num(n(r, "elevado"), 2)} R</Mono>).
        </>
      );
    },
    note: "Cambia la frecuencia de los stops, y con ella las rachas; no la ganancia.",
    detail: (f) => {
      const g = obj(f, "calibration_gap_pp");
      return (
        <>
          Razón de saltos ×{num(n(f, "hit_rate_ratio"), 2)}. La P(toque) de la calculadora está calibrada: brecha{" "}
          {num(n(g, "normal"), 1)} pp en régimen normal y {num(n(g, "elevado"), 1)} pp en elevado (negativo =
          sobreestima).
        </>
      );
    },
  },
  sigmatail: {
    title: "Cola de la volatilidad",
    question: "¿Un modelo de cuantil cubre los movimientos extremos que la sigma no ve?",
    scale: "15m",
    summary: (f) => {
      const lv = list(f, "levels");
      return (
        <>
          No.{" "}
          {lv.map((l, i) => (
            <span key={i}>
              Debía superarse el <Mono>{pct(n(l, "nominal"), 0)}</Mono> de las veces y se superó el{" "}
              <Mono>{pct(n(l, "exceedance"), 1)}</Mono>.{" "}
            </span>
          ))}
        </>
      );
    },
    note: "Una versión que se corrige sola sí calibra, pero se encontró después de mirar los datos y no se adopta.",
    detail: (f) => (
      <>
        {list(f, "levels").map((l, i) => (
          <div key={i}>
            q={num(n(l, "q"), 2)}: excedencia {pct(n(l, "exceedance"), 1)}, IC95%{" "}
            {ci(l, "exceedance_ci95", (v) => pct(v, 1))}; adaptativa calibra: {l.adaptive_calibrated ? "sí" : "no"}{" "}
            (post-hoc).
          </div>
        ))}
      </>
    ),
  },
  technical: {
    title: "Reglas de análisis técnico",
    question: "¿Entrar con barridos de liquidez u order blocks deja ganancia?",
    scale: "15m",
    link: { to: "/cripto/mapa", label: "Mapa técnico" },
    summary: (f) => {
      const rules = list(f, "rules");
      const rs = rules.map((r) => n(r, "mean_r")).filter(isNum);
      return (
        <>
          No. Las <Mono>{rules.length}</Mono> reglas principales pierden entre{" "}
          <Mono>{num(rs.length ? Math.min(...rs) : null, 2)} R</Mono> y{" "}
          <Mono>{num(rs.length ? Math.max(...rs) : null, 2)} R</Mono> por trade, lo mismo que entrar al azar con
          igual stop y objetivo. De <Mono>{count(n(f, "n_exploratory"))}</Mono> variantes extra,{" "}
          <Mono>{count(n(f, "n_exploratory_positive"))}</Mono> salieron positivas.
        </>
      );
    },
    note: "Los niveles del mapa quedan para ubicar el stop, no para elegir la entrada.",
    detail: (f) => (
      <>
        {list(f, "rules").map((r, i) => (
          <div key={i}>
            {String(r.rule)} {String(r.timeframe)}: {num(n(r, "mean_r"), 3)} R (azar {num(n(r, "placebo_mean_r"), 3)}{" "}
            R) — {String(r.verdict)}
          </div>
        ))}
        <div>Costo ida y vuelta {pct(n(f, "cost_pct"), 2)}.</div>
      </>
    ),
  },
  panel_offline: {
    title: "Orden entre criptomonedas",
    question: "¿Se puede ordenar qué monedas van a rendir más que la mediana en los próximos días?",
    scale: "diario",
    link: { to: "/cripto/panel", label: "Panel diario" },
    summary: (f) => (
      <>
        En el histórico sí: sobre <Mono>{count(n(f, "n_symbols"))}</Mono> monedas y{" "}
        <Mono>{count(n(f, "n_dates"))}</Mono> días, el orden acierta más que el azar y que las reglas simples.
      </>
    ),
    note: "Es una prueba sobre el pasado. Se opera largo y corto a la vez, con una cola de pérdidas grande; se está probando en días nuevos.",
    detail: (f) => (
      <>
        Horizonte {num(n(f, "horizon_days"), 0)} días. AUC {num(n(f, "auc"), 4)}; IC {num(n(f, "ic"), 4)}, IC95%{" "}
        {ci(f, "ic_ci95", (v) => num(v, 4))}; spread neto por período {signedPct(n(f, "spread_net"), 2)}, IC95%{" "}
        {ci(f, "spread_net_ci95", (v) => signedPct(v, 2))}.
      </>
    ),
  },
  panel_placebo: {
    title: "Control: el mismo panel al azar",
    question: "¿La prueba fabrica ventaja donde no la hay?",
    scale: "diario",
    summary: (f) => (
      <>
        No. Mezclando al azar los resultados de cada día, la correlación del orden cae a <Mono>{num(n(f, "ic"), 4)}</Mono>{" "}
        (cero, dentro del margen) y el par a <Mono>{signedPct(n(f, "spread_net"), 2)}</Mono>.
      </>
    ),
    detail: (f) => (
      <>
        AUC {num(n(f, "auc"), 4)}; IC95% del IC {ci(f, "ic_ci95", (v) => num(v, 4))}.
      </>
    ),
  },
  macro_power: {
    title: "Potencia del estudio macro",
    question: "Con los datos que existen, ¿qué tan grande tiene que ser un efecto para verlo?",
    scale: "macro",
    summary: (f) => (
      <>
        Grande: con unos <Mono>{num(n(f, "independent_blocks"), 0)}</Mono> bloques independientes, la menor correlación
        de orden detectable es <Mono>{num(n(f, "mde"), 3)}</Mono>.
      </>
    ),
    note: "No aprueba nada: dice qué se puede llegar a ver con estos datos.",
    detail: (f) => (
      <>
        Horizonte {num(n(f, "horizon_weeks"), 0)} semanas. Lectura del artefacto: {String(f.verdict ?? "—")}.
      </>
    ),
  },
  macro_ranking: {
    title: "Ranking entre clases de activo",
    question: "¿Se puede ordenar qué clase (acciones, bonos, oro, cobre…) va a rendir más en 4 semanas?",
    scale: "macro",
    link: { to: "/flujos", label: "Flujos" },
    summary: (f) => (
      <>
        No con estos datos: la correlación del orden da <Mono>{num(n(f, "ic"), 3)}</Mono>, con un margen que incluye el cero, y
        empata con la regla simple de impulso. Agregar liquidez no ayudó.
      </>
    ),
    note: "Por eso MAREA macro es monitoreo: describe, no pronostica.",
    detail: (f) => (
      <>
        IC95% {ci(f, "ic_ci95")}; contra {String(f.best_baseline ?? "—")}, diferencia pareada IC95%{" "}
        {ci(f, "paired_ci95")}; aporte del bloque de liquidez {num(n(f, "liquidity_contribution"), 3)}.
      </>
    ),
  },
};

const SCALES: { key: Spec["scale"]; title: string; blurb: string }[] = [
  { key: "15m", title: "Cripto a 15 minutos", blurb: "ETH perpetuo, dos años de velas de 15 minutos." },
  { key: "diario", title: "Cripto diario", blurb: "Todas las monedas de Binance, incluidas las que dejaron de cotizar." },
  { key: "macro", title: "Macro semanal", blurb: "Clases de activo vía ETFs, desde 2011." },
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
  const [open, setOpen] = useState(false);
  const s = STATUS[status];
  const summary = spec.summary(figures);
  return (
    <section className="flex flex-col gap-2 rounded-lg border border-neutral-800 bg-neutral-900/70 p-4">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-base font-semibold text-neutral-100">{spec.title}</h3>
        <Badge tone={s.tone}>{s.text}</Badge>
      </header>
      <p className="text-xs text-neutral-500">{spec.question}</p>
      {summary && <p className="text-sm text-neutral-200">{summary}</p>}
      {spec.note && <p className="text-xs text-neutral-500">{spec.note}</p>}
      {extra}
      <footer className="mt-auto flex flex-wrap items-center justify-between gap-2 pt-2 text-[11px] text-neutral-600">
        <span>{exp?.date ? `medido el ${exp.date}` : ""}</span>
        <span className="flex gap-1">
          {spec.link && (
            <Link to={spec.link.to} className="rounded px-2 py-0.5 text-neutral-400 hover:bg-neutral-800 hover:text-accent">
              {spec.link.label} →
            </Link>
          )}
          {exp && (
            <button
              type="button"
              onClick={() => setOpen((o) => !o)}
              aria-expanded={open}
              className="rounded px-2 py-0.5 text-neutral-400 hover:bg-neutral-800 hover:text-accent"
            >
              {open ? "Ocultar detalle" : "Ver detalle"}
            </button>
          )}
        </span>
      </footer>
      {open && exp && (
        <div className="space-y-1 border-t border-neutral-800 pt-2 text-xs text-neutral-400">
          <div>{spec.detail(figures)}</div>
          <div className="text-neutral-500">
            Criterio: {exp.criterion_doc ?? "sin pre-registro propio"}
            {exp.criterion_commit && <> (commit <span className="font-mono">{exp.criterion_commit}</span>)</>}
          </div>
          <div className="break-all font-mono text-[11px] text-neutral-600">artifacts/{exp.artifact_id}</div>
        </div>
      )}
    </section>
  );
}

const PANEL_FORWARD: Spec = {
  title: "Orden entre criptomonedas, en días nuevos",
  question: "¿El orden que pasó la prueba histórica se sostiene con pronósticos emitidos antes del resultado?",
  scale: "diario",
  link: { to: "/cripto/panel", label: "Panel diario" },
  summary: () => null,
  note: "El resultado no se mira antes de la revisión intermedia.",
  detail: () => null,
};

export function LabPage() {
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

  return (
    <div className="mx-auto max-w-6xl space-y-8">
      <header>
        <h1 className="text-2xl font-semibold text-neutral-100">Laboratorio</h1>
        <p className="mt-1 text-sm text-neutral-400">
          Todo lo que se midió, con su resultado. Cada prueba fijó su criterio antes de correr, y un “no pasó” bien
          medido vale tanto como un “pasó”. Pasar una prueba habilita seguir midiendo, no operar.
        </p>
        {q.data && (
          <p className="mt-3 text-sm text-neutral-300">
            <Mono>{counts.pasa ?? 0}</Mono> pasaron · <Mono>{counts.no_pasa ?? 0}</Mono> no pasaron ·{" "}
            <Mono>{st ? 1 : 0}</Mono> en curso · <Mono>{(counts.control_ok ?? 0) + (counts.control_falla ?? 0)}</Mono>{" "}
            {counts.control_falla ? "control falló" : "control limpio"} · <Mono>{counts.informativo ?? 0}</Mono>{" "}
            informativa
          </p>
        )}
      </header>

      {q.error && <p className="text-sm text-danger">No se pudo leer el registro de experimentos: {String(q.error)}</p>}
      {q.isLoading && <p className="text-sm text-neutral-500">leyendo artefactos…</p>}

      {q.data &&
        SCALES.map((sc) => {
          const items = exps.filter((e) => SPECS[e.id]?.scale === sc.key);
          const showForward = sc.key === "diario" && st;
          if (!items.length && !showForward) return null;
          return (
            <section key={sc.key} className="space-y-3">
              <div>
                <h2 className="text-lg font-semibold text-neutral-200">{sc.title}</h2>
                <p className="text-xs text-neutral-500">{sc.blurb}</p>
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
                        días válidos hasta la revisión intermedia; el corte final es a los{" "}
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
