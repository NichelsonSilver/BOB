import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Badge, Card, ErrorBox, Loading, Warning } from "../components/common/Card";
import { useLang } from "../i18n";
import { api } from "../lib/api";
import { INK, SERIES, TOOLTIP_STYLE } from "../lib/chartTheme";
import { DASH, isNum, num, signedPct } from "../lib/format";
import type { RankingVerdict, StrengthClass, StrengthReport } from "../types";

/**
 * MAREA — hacia dónde se movió el mercado entre clases de activo.
 *
 * **No es flujo de dinero.** El flujo neto hacia una clase (creaciones y
 * redenciones de ETFs) no está en ninguna fuente gratuita del proyecto. Lo que
 * se dibuja es fuerza relativa: retorno total de cada clase menos la mediana
 * del universo. Dice a quién premió el mercado, no hacia dónde entró el dinero.
 *
 * **Y no hay pronóstico.** La M3 midió si este orden se sostiene hacia adelante
 * y no lo hace. Donde iría la línea del "cambio esperado" va el veredicto, leído
 * del artefacto versionado: dibujar una proyección sin gate aprobado es
 * exactamente lo que prohíbe la regla 2.
 *
 * Un solo color de serie: las seis clases van en paneles separados (small
 * multiples) con la misma escala, en vez de seis colores en un gráfico. La
 * paleta validada tiene tres, y una cuarta serie no se genera.
 */

const WINDOWS = [13, 26, 52] as const;

/** Las etiquetas de régimen son códigos del backend; se traducen al dibujarlas. */
const REGIME_EN: Record<string, string> = {
  expansivo: "expansionary",
  neutro: "neutral",
  contractivo: "contractionary",
};
const AXIS = { stroke: INK.axis, tick: { fill: INK.muted, fontSize: 10 } };
const BAR = SERIES[0];

/** Paso "redondo" (1, 2, 2,5 o 5 × 10^k) para unos 4 intervalos en el rango. */
function niceTicks(lo: number, hi: number): number[] {
  const raw = (hi - lo) / 4 || 0.01;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const out: number[] = [];
  for (let t = Math.floor(lo / step) * step; t <= hi + step / 2; t += step) {
    out.push(Math.round(t / step) * step);
  }
  return out;
}

export function FlowsPage() {
  const { lang, tr } = useLang();
  const [weeks, setWeeks] = useState<number>(26);
  const q = useQuery({
    queryKey: ["macro", "strength", weeks, lang],
    queryFn: () => api.strength(weeks, lang),
    retry: false,
    staleTime: 600_000,
  });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-neutral-100">{tr("Flows · asset classes", "Flujos · clases de activo")}</h1>
          <p className="text-xs text-neutral-500">
            {q.data
              ? tr(
                  `prices as of ${q.data.as_of} · window from ${q.data.window_from}`,
                  `precios al ${q.data.as_of} · ventana desde ${q.data.window_from}`,
                )
              : tr("relative strength by class", "fuerza relativa por clase")}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <WindowPicker value={weeks} onChange={setWeeks} />
          <Badge
            tone="muted"
            title={tr("Relative return, not net money flow; not a forecast", "Retorno relativo, no flujo neto de dinero; no es pronóstico")}
          >
            {tr("descriptive · not money flow", "descriptivo · no es flujo de dinero")}
          </Badge>
        </div>
      </div>

      {q.isLoading && <Loading what={tr("relative strength", "la fuerza relativa")} />}
      {q.error && <ErrorBox>{String(q.error)}</ErrorBox>}
      {q.data && <StrengthView rep={q.data} />}

      <OutlookCard />
    </div>
  );
}

function WindowPicker({ value, onChange }: { value: number; onChange: (w: number) => void }) {
  const { tr } = useLang();
  return (
    <div className="flex overflow-hidden rounded border border-neutral-800 text-xs">
      {WINDOWS.map((w) => (
        <button
          key={w}
          type="button"
          onClick={() => onChange(w)}
          className={`px-2.5 py-1 ${
            w === value ? "bg-neutral-800 text-neutral-100" : "text-neutral-400 hover:text-neutral-200"
          }`}
        >
          {w} {tr("wk", "sem")}
        </button>
      ))}
    </div>
  );
}

function StrengthView({ rep }: { rep: StrengthReport }) {
  const { tr } = useLang();
  return (
    <>
      {rep.stale && (
        <Warning>
          {tr(`Prices are ${rep.age_days} days old. Run`, `Los precios tienen ${rep.age_days} días. Correr`)}{" "}
          <code>uv run python -m marea.data.ingest etf</code>.
        </Warning>
      )}
      {rep.excluded.length > 0 && (
        <Warning>
          {tr(
            `Left out of the ${rep.window_weeks}-week window for incomplete data (not filled in)`,
            `Fuera de la ventana de ${rep.window_weeks} semanas por datos incompletos (no se rellenan)`,
          )}
          : {rep.excluded.join(", ")}.
        </Warning>
      )}

      <div className="grid gap-4 xl:grid-cols-5">
        <Card title={tr(`Now · last ${rep.window_weeks} weeks`, `Ahora · últimas ${rep.window_weeks} semanas`)} className="xl:col-span-2">
          <NowBars rep={rep} />
          <p className="mt-2 text-xs text-neutral-500">
            {tr(
              `Class return minus the universe median (${signedPct(rep.median_window, 1)} over the window). 0% = it did like the typical ETF.`,
              `Retorno de la clase menos la mediana del universo (${signedPct(rep.median_window, 1)} en la ventana). 0% = le fue como al ETF típico.`,
            )}
          </p>
        </Card>
        <Card title={tr("By horizon", "Por horizonte")} className="xl:col-span-3">
          <ClassTable rep={rep} />
        </Card>
      </div>

      <Card
        title={tr(
          `How it has been moving · cumulative relative strength, ${rep.window_weeks} weeks`,
          `Cómo viene fluyendo · fuerza relativa acumulada, ${rep.window_weeks} semanas`,
        )}
        right={<span className="text-xs text-neutral-600">{tr("same scale in all six panels", "misma escala en los seis paneles")}</span>}
      >
        <SmallMultiples rep={rep} />
      </Card>

      <EtfTable rep={rep} />

      <Card title={tr("Method", "Método")}>
        <ul className="space-y-1 text-xs text-neutral-400">
          <li>
            {tr("Measure", "Medida")}: {rep.method.measure}.
          </li>
          <li>
            {tr("Class", "Clase")}: {rep.method.class}.
          </li>
          <li>
            {tr("Reference", "Referencia")}: {rep.method.reference}.
          </li>
          <li>
            {tr("Missing data", "Datos faltantes")}: {rep.method.missing}.
          </li>
          <li>
            {tr(
              "Source: Tiingo, total return with dividends. The measure is the same as",
              "Fuente: Tiingo, retorno total con dividendos. La medida es igual a la de",
            )}{" "}
            <code>marea.brief</code>, {tr("and comes from", "y sale de")} <code>python -m marea.strength</code>.
          </li>
        </ul>
      </Card>
    </>
  );
}

function NowBars({ rep }: { rep: StrengthReport }) {
  const { tr } = useLang();
  const data = rep.classes.map((c) => ({ label: c.label, v: c.rel_window }));
  const vals = data.map((d) => d.v).filter(isNum);
  const lo = Math.min(0, ...vals);
  const hi = Math.max(0, ...vals);
  // Margen para que la etiqueta de una barra negativa no pise el nombre de la clase.
  const pad = (hi - lo) * 0.3 || 0.01;
  const domain: [number, number] = [lo < 0 ? lo - pad : 0, hi > 0 ? hi + pad : 0];
  return (
    <ResponsiveContainer width="100%" height={36 * data.length + 20}>
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 16, bottom: 0, left: 4 }}>
        <CartesianGrid stroke={INK.grid} strokeDasharray="2 4" horizontal={false} />
        <XAxis
          type="number"
          domain={domain}
          ticks={niceTicks(lo, hi)}
          {...AXIS}
          tickFormatter={(v: number) => signedPct(v, 0)}
        />
        <YAxis type="category" dataKey="label" {...AXIS} width={128} />
        <ReferenceLine x={0} stroke={INK.axis} />
        <Tooltip
          {...TOOLTIP_STYLE}
          cursor={{ fill: INK.grid }}
          formatter={(value) => [signedPct(typeof value === "number" ? value : null, 2), tr("vs median", "vs mediana")]}
        />
        <Bar dataKey="v" barSize={14} radius={4} isAnimationActive={false}>
          {data.map((d) => (
            <Cell key={d.label} fill={BAR} />
          ))}
          <LabelList
            dataKey="v"
            content={BarLabel}
          />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

/** Etiqueta en la punta de la barra, hacia afuera según el signo. */
function BarLabel(props: unknown) {
  const { x, y, width, height, value } = props as {
    x: number;
    y: number;
    width: number;
    height: number;
    value: unknown;
  };
  const v = typeof value === "number" ? value : null;
  const neg = isNum(v) && v < 0;
  // En una barra negativa recharts entrega x en el cero y width negativo.
  const end = x + width;
  return (
    <text
      x={neg ? end - 6 : end + 6}
      y={y + height / 2}
      dy={4}
      textAnchor={neg ? "end" : "start"}
      fill={INK.text}
      fontSize={11}
    >
      {signedPct(v, 1)}
    </text>
  );
}

function RelCell({ v }: { v: number | null }) {
  const tone = !isNum(v) ? "text-neutral-600" : v >= 0 ? "text-neutral-100" : "text-neutral-400";
  return <td className={`py-1.5 pr-3 text-right ${tone}`}>{signedPct(v, 1)}</td>;
}

function ClassTable({ rep }: { rep: StrengthReport }) {
  const { tr } = useLang();
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-neutral-500">
            <th className="py-1 pr-3 font-normal">{tr("class", "clase")}</th>
            <th className="py-1 pr-3 font-normal">ETFs</th>
            {rep.table_weeks.map((w) => (
              <th key={w} className="py-1 pr-3 text-right font-normal">
                {w} {tr("wk", "sem")}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="font-mono">
          {rep.classes.map((c) => (
            <tr key={c.block} className="border-t border-neutral-800/70">
              <td className="py-1.5 pr-3 font-sans text-neutral-200">{c.label}</td>
              <td className="py-1.5 pr-3 text-xs text-neutral-500">{c.members.join(" ")}</td>
              {rep.table_weeks.map((w) => (
                <RelCell key={w} v={relOf(c, w)} />
              ))}
            </tr>
          ))}
          <tr className="border-t border-neutral-800 text-neutral-500">
            <td className="py-1.5 pr-3 font-sans text-xs" colSpan={2}>
              {tr("universe median (absolute return)", "mediana del universo (retorno absoluto)")}
            </td>
            {rep.table_weeks.map((w) => (
              <td key={w} className="py-1.5 pr-3 text-right text-xs">
                {signedPct(rep.median_by_weeks[String(w)], 1)}
              </td>
            ))}
          </tr>
        </tbody>
      </table>
      <p className="mt-2 text-xs text-neutral-500">
        {tr(
          "A class with one ETF (dollar = UUP) is that ETF. Sorted by the chart's window.",
          "Una clase con un ETF (dólar = UUP) es ese ETF. Ordenadas por la ventana del gráfico.",
        )}
      </p>
    </div>
  );
}

function relOf(c: StrengthClass, w: number): number | null {
  const v = (c as unknown as Record<string, number | null>)[`rel_${w}s`];
  return isNum(v) ? v : null;
}

function SmallMultiples({ rep }: { rep: StrengthReport }) {
  const { tr } = useLang();
  let lo = 0;
  let hi = 0;
  for (const p of rep.series) {
    for (const c of rep.classes) {
      const v = p[c.block];
      if (typeof v === "number") {
        lo = Math.min(lo, v);
        hi = Math.max(hi, v);
      }
    }
  }
  const ticks = niceTicks(lo, hi);
  const domain: [number, number] = [ticks[0] ?? lo, ticks[ticks.length - 1] ?? hi];

  return (
    <div className="grid gap-x-4 gap-y-3 sm:grid-cols-2 xl:grid-cols-3">
      {rep.classes.map((c) => (
        <div key={c.block}>
          <div className="flex items-baseline justify-between text-xs">
            <span className="text-neutral-300">{c.label}</span>
            <span className="font-mono text-neutral-100">{signedPct(c.rel_window, 1)}</span>
          </div>
          <ResponsiveContainer width="100%" height={130}>
            <LineChart data={rep.series} margin={{ top: 6, right: 6, bottom: 0, left: 0 }}>
              <CartesianGrid stroke={INK.grid} strokeDasharray="2 4" vertical={false} />
              <XAxis dataKey="day" {...AXIS} minTickGap={60} tickFormatter={(d: string) => d.slice(2, 7)} />
              <YAxis
                domain={domain}
                ticks={ticks}
                {...AXIS}
                width={44}
                tickFormatter={(v: number) => signedPct(v, 0)}
              />
              <ReferenceLine y={0} stroke={INK.axis} />
              <Tooltip
                {...TOOLTIP_STYLE}
                formatter={(value) => [
                  signedPct(typeof value === "number" ? value : null, 2),
                  tr("vs median", "vs mediana"),
                ]}
              />
              <Line
                type="monotone"
                dataKey={c.block}
                stroke={BAR}
                strokeWidth={2}
                dot={false}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      ))}
    </div>
  );
}

function EtfTable({ rep }: { rep: StrengthReport }) {
  const { tr } = useLang();
  const [open, setOpen] = useState(false);
  const label = Object.fromEntries(rep.classes.map((c) => [c.block, c.label]));
  return (
    <Card
      title={tr("Detail by ETF", "Detalle por ETF")}
      right={
        <button type="button" onClick={() => setOpen(!open)} className="text-xs text-neutral-400 hover:text-neutral-200">
          {open ? tr("hide", "ocultar") : tr(`show ${rep.etfs.length}`, `mostrar ${rep.etfs.length}`)}
        </button>
      }
    >
      {open ? (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-neutral-500">
                <th className="py-1 pr-3 font-normal">ETF</th>
                <th className="py-1 pr-3 font-normal">{tr("class", "clase")}</th>
                {rep.table_weeks.map((w) => (
                  <th key={w} className="py-1 pr-3 text-right font-normal">
                    {w} {tr("wk rel. (abs.)", "sem rel. (abs.)")}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="font-mono">
              {rep.etfs.map((e) => {
                const r = e as unknown as Record<string, number | null>;
                return (
                  <tr key={e.ticker} className="border-t border-neutral-800/70 align-top">
                    <td className="py-1.5 pr-3 font-sans">
                      <span className="text-neutral-200">{e.ticker}</span>{" "}
                      <span className="text-xs text-neutral-500">{e.name}</span>
                      {e.caveat && <div className="text-xs text-neutral-600">{e.caveat}</div>}
                    </td>
                    <td className="py-1.5 pr-3 font-sans text-xs text-neutral-500">
                      {label[e.block] ?? e.block}
                    </td>
                    {rep.table_weeks.map((w) => (
                      <td key={w} className="py-1.5 pr-3 text-right text-neutral-200">
                        {signedPct(r[`rel_${w}s`], 1)}{" "}
                        <span className="text-xs text-neutral-500">({signedPct(r[`ret_${w}s`], 1)})</span>
                      </td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-xs text-neutral-500">
          {tr(
            "Each ETF's return relative to the median and absolute, over the three windows.",
            "Retorno de cada ETF relativo a la mediana y absoluto, en las tres ventanas.",
          )}
        </p>
      )}
    </Card>
  );
}

/** Donde iría el pronóstico: el veredicto de la M3 y el régimen de liquidez. */
function OutlookCard() {
  const { lang, tr } = useLang();
  const v = useQuery({
    queryKey: ["macro", "ranking-verdict", lang],
    queryFn: () => api.rankingVerdict(lang),
    retry: false,
    staleTime: 3_600_000,
  });
  const liq = useQuery({
    queryKey: ["macro", "liquidity", lang],
    queryFn: () => api.liquidity(lang),
    retry: false,
    staleTime: 600_000,
  });

  return (
    <Card
      title={tr("And going forward?", "¿Y hacia adelante?")}
      right={<Badge tone="muted">{tr("no forecast · M3 gate not passed", "sin pronóstico · gate M3 no aprobado")}</Badge>}
    >
      {v.error && <ErrorBox>{String(v.error)}</ErrorBox>}
      {v.data && <Verdict v={v.data} />}
      <div className="mt-4 border-t border-neutral-800 pt-3 text-sm">
        <div className="text-xs text-neutral-500">{tr("Context · liquidity regime (monitoring)", "Contexto · régimen de liquidez (monitoreo)")}</div>
        {liq.data ? (
          <div className="mt-1 text-neutral-300">
            <span className="font-semibold text-neutral-100">
              {liq.data.regime.label ? (lang === "en" ? REGIME_EN[liq.data.regime.label] : liq.data.regime.label) : DASH}
            </span>{" "}
            · {tr("percentile", "percentil")} {num(liq.data.regime.composite_pct, 2)} · {tr("since", "desde")}{" "}
            {liq.data.regime.since ?? DASH}
            {liq.data.stale && (
              <span className="text-warn">
                {" "}
                · {tr(`stale report (${liq.data.age_days} days)`, `reporte vencido (${liq.data.age_days} días)`)}
              </span>
            )}
            <p className="mt-1 text-xs text-neutral-500">
              {tr(
                "It describes the state of liquidity, it does not anticipate returns. In M3 the liquidity block did not improve the ranking (see above). Details on the Liquidity page.",
                "Describe el estado de la liquidez, no anticipa retornos. En la M3 el bloque de liquidez no mejoró el orden (ver arriba). Detalle en la página Liquidez.",
              )}
            </p>
          </div>
        ) : (
          <div className="mt-1 text-xs text-neutral-500">
            {liq.error
              ? tr("No liquidity report: run `python -m marea.monitor`.", "Sin reporte de liquidez: correr `python -m marea.monitor`.")
              : tr("loading…", "cargando…")}
          </div>
        )}
      </div>
    </Card>
  );
}

function ci(c: [number, number]): string {
  return `[${signedNum(c[0])}, ${signedNum(c[1])}]`;
}

function signedNum(x: number): string {
  return `${x >= 0 ? "+" : ""}${x.toFixed(3)}`;
}

function Verdict({ v }: { v: RankingVerdict }) {
  const { tr } = useLang();
  const sign = v.ic_by_spy_sign;
  const lc = v.liquidity_contribution;
  return (
    <div className="space-y-2 text-sm text-neutral-300">
      <p>
        <span className="font-semibold text-neutral-100">
          {v.viable
            ? tr("The ranking passed its gate.", "El ranking pasó su gate.")
            : tr("There is no forecast with backing.", "No hay pronóstico con respaldo.")}
        </span>{" "}
        {tr(
          `We measured whether a model can anticipate which class beats which over the next ${v.horizon_weeks} weeks, with ${v.n_etfs} ETFs and ${v.n_test_dates} out-of-sample dates (${v.test_from} to ${v.test_to}). Result: ${v.reading}.`,
          `Se midió si un modelo puede anticipar qué clase le gana a cuál en las próximas ${v.horizon_weeks} semanas, con ${v.n_etfs} ETFs y ${v.n_test_dates} fechas fuera de muestra (${v.test_from} a ${v.test_to}). Resultado: ${v.reading}.`,
        )}
      </p>
      <ul className="space-y-1 font-mono text-xs text-neutral-400">
        <li>
          {tr("model IC", "IC del modelo")} {signedNum(v.ic_mean)} · {tr("95% CI", "IC95%")} {ci(v.ic_ci95)}{" "}
          <span className="font-sans text-neutral-500">{tr("(contains 0: does not rank)", "(contiene 0: no ordena)")}</span>
        </li>
        <li>
          vs {v.baseline}: {signedNum(v.baseline_ic_mean)} · {tr("paired difference", "diferencia pareada")}{" "}
          {signedNum(v.paired_diff)} {ci(v.paired_ci95)}{" "}
          <span className="font-sans text-neutral-500">{tr("(ties with the trivial rule)", "(empata con la regla trivial)")}</span>
        </li>
        <li>
          AUC {num(v.auc, 3)}
          {isNum(v.placebo_ic_mean) && <> · placebo {signedNum(v.placebo_ic_mean)}</>}
        </li>
        {lc && (
          <li>
            {tr("liquidity block contribution", "aporte del bloque de liquidez")} {signedNum(lc.diff_mean)} {ci(lc.ci95)}
          </li>
        )}
        {sign && (
          <li>
            {tr("IC by market direction", "IC según el mercado")}:{" "}
            {Object.entries(sign)
              .map(([k, s]) => `${k} ${signedNum(s.ic_mean)} (n=${s.n})`)
              .join(" · ")}
          </li>
        )}
      </ul>
      <p className="text-xs text-neutral-500">
        {tr(
          "That is why the panel draws no expected-change line: a projection without a passed gate would read as a forecast (rule 2). The relative strength above is the past, and M3 found no significant evidence that its ranking holds going forward.",
          "Por eso el panel no dibuja una línea de cambio esperado: una proyección sin gate aprobado se leería como pronóstico (regla 2). La fuerza relativa de arriba es el pasado, y la M3 no encontró evidencia significativa de que su orden se sostenga hacia adelante.",
        )}{" "}
        {tr("Artifact", "Artefacto")} <code>{v.artifact}</code>, {tr("pre-registration", "pre-registro")} <code>{v.prereg_commit}</code>.
      </p>
    </div>
  );
}
