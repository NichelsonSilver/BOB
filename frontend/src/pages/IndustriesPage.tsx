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
import { INK, SERIES, SERIES_BY_ROLE, TOOLTIP_STYLE } from "../lib/chartTheme";
import { DASH, isNum, signedPct } from "../lib/format";
import type { DefiCategory, IndustriesReport, IndustryDefi, IndustryEquity } from "../types";

/**
 * MAREA — crecimiento del mercado por industria (M7).
 *
 * Dos mercados, cada uno con lo que da gratis su fuente: los 11 sectores del
 * S&P 500 (ETFs sectoriales de Tiingo) y DeFi por categoría (TVL de
 * DefiLlama). **No es flujo de dinero**: un precio que sube agranda el sector
 * o el TVL sin que entre un dólar. **Tampoco es pronóstico**: la M3 midió que
 * el orden de la fuerza relativa no se sostiene hacia adelante.
 *
 * Sectores en paneles chicos con la misma escala, igual que Flujos: dos series
 * por panel (el sector y SPY como referencia) en vez de once colores en un
 * gráfico. La paleta validada tiene tres.
 */

const WINDOWS = [13, 26, 52] as const;
const AXIS = { stroke: INK.axis, tick: { fill: INK.muted, fontSize: 10 } };
const SECTOR = SERIES[0];
const BENCH = SERIES_BY_ROLE.reference;

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

/** Miles de millones de USD, con un decimal. */
function usdBn(v: number | null | undefined): string {
  return isNum(v) ? `${(v / 1e9).toFixed(1)}` : DASH;
}

function cell(row: Record<string, unknown>, key: string): number | null {
  const v = row[key];
  return typeof v === "number" ? v : null;
}

export function IndustriesPage() {
  const { lang, tr } = useLang();
  const [weeks, setWeeks] = useState<number>(26);
  const q = useQuery({
    queryKey: ["macro", "industries", weeks, lang],
    queryFn: () => api.industries(weeks, lang),
    retry: false,
    staleTime: 600_000,
  });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-neutral-100">
            {tr("Industries · market growth", "Industrias · crecimiento del mercado")}
          </h1>
          <p className="text-xs text-neutral-500">
            {tr(
              "US stock sectors and DeFi categories, each measured with its free source",
              "Sectores de la bolsa de EE.UU. y categorías DeFi, cada uno medido con su fuente gratuita",
            )}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <WindowPicker value={weeks} onChange={setWeeks} />
          <Badge
            tone="muted"
            title={tr("Growth of value, not net money flow; not a forecast", "Crecimiento de valor, no flujo neto de dinero; no es pronóstico")}
          >
            {tr("descriptive · not money flow", "descriptivo · no es flujo de dinero")}
          </Badge>
        </div>
      </div>

      {q.isLoading && <Loading what={tr("industry growth", "el crecimiento por industria")} />}
      {q.error && <ErrorBox>{String(q.error)}</ErrorBox>}
      {q.data && (
        <>
          <EquitySection rep={q.data} />
          <DefiSection defi={q.data.defi} />
          <MethodCard rep={q.data} />
        </>
      )}
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

// --- Bolsa por sector -------------------------------------------------------------

function EquitySection({ rep }: { rep: IndustriesReport }) {
  const { tr } = useLang();
  const eq = rep.equity;
  if (!eq) {
    return (
      <Card title={tr("US stock market by sector", "Bolsa de EE.UU. por sector")}>
        <p className="text-sm text-neutral-400">
          {tr("No sector prices", "Sin precios de los sectores")} ({rep.equity_error}).{" "}
          {tr("Run", "Correr")} <code>uv run python -m marea.data.ingest etf</code>.
        </p>
      </Card>
    );
  }
  return (
    <>
      {eq.stale && (
        <Warning>
          {tr(`Prices are ${eq.age_days} days old. Run`, `Los precios tienen ${eq.age_days} días. Correr`)}{" "}
          <code>uv run python -m marea.data.ingest etf</code>.
        </Warning>
      )}
      {(eq.excluded.length > 0 || eq.missing.length > 0) && (
        <Warning>
          {eq.excluded.length > 0 &&
            tr(
              `Left out of the ${eq.window_weeks}-week window for incomplete data: ${eq.excluded.join(", ")}. `,
              `Fuera de la ventana de ${eq.window_weeks} semanas por datos incompletos: ${eq.excluded.join(", ")}. `,
            )}
          {eq.missing.length > 0 &&
            tr(`Never downloaded: ${eq.missing.join(", ")}.`, `Nunca bajados: ${eq.missing.join(", ")}.`)}
        </Warning>
      )}

      <div className="grid gap-4 xl:grid-cols-5">
        <Card
          title={tr(`US sectors · last ${eq.window_weeks} weeks`, `Sectores de EE.UU. · últimas ${eq.window_weeks} semanas`)}
          className="min-w-0 xl:col-span-2"
        >
          <GrowthBars
            data={eq.sectors.map((s) => ({ label: s.label, v: s.ret_window }))}
            reference={eq.benchmark_window}
            valueLabel={tr("growth", "crecimiento")}
          />
          <p className="mt-2 text-xs text-neutral-500">
            {tr(
              `Total return of each sector ETF, as of ${eq.as_of}. The line marks the S&P 500 (SPY, ${signedPct(eq.benchmark_window, 1)}): to its right, the sector grew more than the market.`,
              `Retorno total del ETF de cada sector, al ${eq.as_of}. La línea marca el S&P 500 (SPY, ${signedPct(eq.benchmark_window, 1)}): a su derecha, el sector creció más que el mercado.`,
            )}
          </p>
        </Card>
        <Card title={tr("By horizon", "Por horizonte")} className="min-w-0 xl:col-span-3">
          <SectorTable eq={eq} />
        </Card>
      </div>

      <Card
        title={tr(
          `How each sector grew · cumulative, ${eq.window_weeks} weeks`,
          `Cómo creció cada sector · acumulado, ${eq.window_weeks} semanas`,
        )}
        right={<SectorLegend />}
      >
        <SectorMultiples eq={eq} />
      </Card>
    </>
  );
}

function SectorLegend() {
  const { tr } = useLang();
  return (
    <span className="flex items-center gap-3 text-xs text-neutral-400">
      <span className="flex items-center gap-1">
        <span className="inline-block h-0.5 w-4" style={{ background: SECTOR }} />
        {tr("sector", "sector")}
      </span>
      <span className="flex items-center gap-1">
        <span className="inline-block h-0.5 w-4" style={{ background: BENCH }} />
        S&amp;P 500
      </span>
      <span className="text-neutral-600">{tr("same scale in all panels", "misma escala en todos los paneles")}</span>
    </span>
  );
}

function SectorTable({ eq }: { eq: IndustryEquity }) {
  const { tr } = useLang();
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-neutral-500">
            <th className="py-1 pr-3 font-normal">{tr("sector", "sector")}</th>
            {eq.table_weeks.map((w) => (
              <th key={w} className="py-1 pr-3 text-right font-normal">
                {w} {tr("wk", "sem")}
              </th>
            ))}
            <th className="py-1 pr-3 text-right font-normal">
              {tr(`vs S&P · ${eq.window_weeks} wk`, `vs S&P · ${eq.window_weeks} sem`)}
            </th>
          </tr>
        </thead>
        <tbody className="font-mono">
          {eq.sectors.map((s) => (
            <tr key={s.ticker} className="border-t border-neutral-800/70 align-top">
              <td className="py-1.5 pr-3 font-sans">
                <span className="text-neutral-200">{s.label}</span>{" "}
                <span className="text-xs text-neutral-500">{s.ticker}</span>
                {s.caveat && <div className="text-xs text-neutral-600">{s.caveat}</div>}
              </td>
              {eq.table_weeks.map((w) => (
                <PctCell key={w} v={cell(s, `ret_${w}s`)} />
              ))}
              <PctCell v={s.rel_window} />
            </tr>
          ))}
          <tr className="border-t border-neutral-800 text-neutral-500">
            <td className="py-1.5 pr-3 font-sans text-xs">S&amp;P 500 (SPY)</td>
            {eq.table_weeks.map((w) => (
              <td key={w} className="py-1.5 pr-3 text-right text-xs">
                {signedPct(eq.benchmark_by_weeks[String(w)], 1)}
              </td>
            ))}
            <td />
          </tr>
        </tbody>
      </table>
      <p className="mt-2 text-xs text-neutral-500">
        {tr(
          "Sorted by growth over the chart's window. Total return with dividends.",
          "Ordenados por el crecimiento en la ventana del gráfico. Retorno total con dividendos.",
        )}
      </p>
    </div>
  );
}

function PctCell({ v }: { v: number | null }) {
  const tone = !isNum(v) ? "text-neutral-600" : v >= 0 ? "text-neutral-100" : "text-neutral-400";
  return <td className={`py-1.5 pr-3 text-right ${tone}`}>{signedPct(v, 1)}</td>;
}

function SectorMultiples({ eq }: { eq: IndustryEquity }) {
  const { tr } = useLang();
  const keys = [eq.benchmark, ...eq.sectors.map((s) => s.ticker)];
  let lo = 0;
  let hi = 0;
  for (const p of eq.series) {
    for (const k of keys) {
      const v = p[k];
      if (typeof v === "number") {
        lo = Math.min(lo, v);
        hi = Math.max(hi, v);
      }
    }
  }
  const ticks = niceTicks(lo, hi);
  const domain: [number, number] = [ticks[0] ?? lo, ticks[ticks.length - 1] ?? hi];

  return (
    <div className="grid gap-x-4 gap-y-3 sm:grid-cols-2 xl:grid-cols-4">
      {eq.sectors.map((s) => (
        <div key={s.ticker}>
          <div className="flex items-baseline justify-between text-xs">
            <span className="text-neutral-300">
              {s.label} <span className="text-neutral-600">{s.ticker}</span>
            </span>
            <span className="font-mono text-neutral-100">{signedPct(s.ret_window, 1)}</span>
          </div>
          <ResponsiveContainer width="100%" height={120}>
            <LineChart data={eq.series} margin={{ top: 6, right: 6, bottom: 0, left: 0 }}>
              <CartesianGrid stroke={INK.grid} strokeDasharray="2 4" vertical={false} />
              <XAxis dataKey="day" {...AXIS} minTickGap={60} tickFormatter={(d: string) => d.slice(2, 7)} />
              <YAxis domain={domain} ticks={ticks} {...AXIS} width={44} tickFormatter={(v: number) => signedPct(v, 0)} />
              <ReferenceLine y={0} stroke={INK.axis} />
              <Tooltip
                {...TOOLTIP_STYLE}
                formatter={(value, name) => [
                  signedPct(typeof value === "number" ? value : null, 2),
                  name === eq.benchmark ? "S&P 500" : s.label,
                ]}
              />
              <Line type="monotone" dataKey={eq.benchmark} stroke={BENCH} strokeWidth={1.5} dot={false} isAnimationActive={false} />
              <Line type="monotone" dataKey={s.ticker} stroke={SECTOR} strokeWidth={2} dot={false} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      ))}
      <p className="self-center text-xs text-neutral-500">
        {tr(
          "Cumulative total return since the start of the window. Each panel repeats the S&P 500 as the reference.",
          "Retorno total acumulado desde el inicio de la ventana. Cada panel repite el S&P 500 como referencia.",
        )}
      </p>
    </div>
  );
}

// --- DeFi por categoría -------------------------------------------------------------

function DefiSection({ defi }: { defi: IndustryDefi | null }) {
  const { tr } = useLang();
  if (!defi) {
    return (
      <Card title={tr("DeFi by category", "DeFi por categoría")}>
        <p className="text-sm text-neutral-400">
          {tr("No snapshots yet. Run", "Todavía no hay fotos. Correr")}{" "}
          <code>uv run python -m marea.data.ingest industrias</code>{" "}
          {tr("(it also runs inside the daily task).", "(también corre dentro de la tarea diaria).")}
        </p>
      </Card>
    );
  }
  const named = defi.categories.filter((c) => !c.other);
  return (
    <>
      {defi.stale && (
        <Warning>
          {tr(
            `The latest DeFi snapshot is ${defi.age_days} days old: the daily task did not run. A missing day cannot be recovered later.`,
            `La última foto de DeFi tiene ${defi.age_days} días: la tarea diaria no corrió. Un día que falta no se recupera después.`,
          )}
        </Warning>
      )}
      <div className="grid gap-4 xl:grid-cols-5">
        <Card title={tr("DeFi categories · last 30 days", "Categorías DeFi · últimos 30 días")} className="min-w-0 xl:col-span-2">
          <GrowthBars
            data={named.map((c) => ({ label: c.category, v: c.growth_30d }))}
            reference={null}
            valueLabel={tr("TVL change", "cambio del TVL")}
          />
          <p className="mt-2 text-xs text-neutral-500">
            {tr(
              "TVL change over 30 days, same protocols only: a protocol DefiLlama just listed does not count as new money.",
              "Cambio del TVL en 30 días, solo con los mismos protocolos: uno que DefiLlama recién listó no cuenta como dinero nuevo.",
            )}
          </p>
        </Card>
        <Card
          title={tr("Size and growth", "Tamaño y crecimiento")}
          className="min-w-0 xl:col-span-3"
          right={
            <span className="text-xs text-neutral-600">
              {tr("snapshot", "foto")} {defi.captured_at.replace("T", " ").slice(0, 16)} UTC
            </span>
          }
        >
          <DefiTable defi={defi} />
        </Card>
      </div>
      <Card title={tr("How it has been growing · TVL by category", "Cómo viene creciendo · TVL por categoría")}>
        <DefiHistory defi={defi} />
      </Card>
    </>
  );
}

function DefiTable({ defi }: { defi: IndustryDefi }) {
  const { tr } = useLang();
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-neutral-500">
            <th className="py-1 pr-3 font-normal">{tr("category", "categoría")}</th>
            <th className="py-1 pr-3 text-right font-normal">{tr("TVL (USD bn)", "TVL (mil M USD)")}</th>
            <th className="py-1 pr-3 text-right font-normal">{tr("share", "peso")}</th>
            <th className="py-1 pr-3 text-right font-normal">1 d</th>
            <th className="py-1 pr-3 text-right font-normal">7 d</th>
            <th className="py-1 pr-3 text-right font-normal">30 d</th>
          </tr>
        </thead>
        <tbody className="font-mono">
          {defi.categories.map((c: DefiCategory) => (
            <tr key={c.category} className="border-t border-neutral-800/70">
              <td className="py-1.5 pr-3 font-sans">
                <span className={c.other ? "text-neutral-400" : "text-neutral-200"}>{c.category}</span>{" "}
                <span className="text-xs text-neutral-500">
                  {c.other
                    ? tr(`${c.members} categories`, `${c.members} categorías`)
                    : tr(`${c.protocols} protocols`, `${c.protocols} protocolos`)}
                </span>
              </td>
              <td className="py-1.5 pr-3 text-right text-neutral-200">{usdBn(c.tvl)}</td>
              <td className="py-1.5 pr-3 text-right text-neutral-400">
                {isNum(c.share) ? `${(100 * c.share).toFixed(1)}%` : DASH}
              </td>
              <PctCell v={c.growth_1d} />
              <PctCell v={c.growth_7d} />
              <PctCell v={c.growth_30d} />
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-2 text-xs text-neutral-500">
        {tr(
          `${defi.categories_total} categories, ${usdBn(defi.tvl_sum)} bn in total. The total double counts (liquid staking deposited in lending counts in both), so it is not DeFi's total TVL. CEXs are left out.`,
          `${defi.categories_total} categorías, ${usdBn(defi.tvl_sum)} mil millones en total. La suma tiene doble conteo (liquid staking depositado en lending cuenta en las dos), así que no es el TVL total de DeFi. Los CEX quedan fuera.`,
        )}
      </p>
    </div>
  );
}

function DefiHistory({ defi }: { defi: IndustryDefi }) {
  const { tr } = useLang();
  if (defi.snapshots < 2) {
    return (
      <p className="text-sm text-neutral-400">
        {tr(
          `One snapshot so far (${defi.first_snapshot}). DefiLlama does not give category history for free, so the series is built with one snapshot per day: the line appears from the second one, and the 1-, 7- and 30-day growth above already works from the first.`,
          `Hay una foto por ahora (${defi.first_snapshot}). DefiLlama no da historia por categoría gratis, así que la serie se arma con una foto por día: la línea aparece desde la segunda, y el crecimiento a 1, 7 y 30 días de arriba ya funciona desde la primera.`,
        )}
      </p>
    );
  }
  const names = defi.categories.filter((c) => !c.other).map((c) => c.category);
  return (
    <>
      <div className="grid gap-x-4 gap-y-3 sm:grid-cols-2 xl:grid-cols-4">
        {names.map((n) => (
          <div key={n}>
            <div className="flex items-baseline justify-between text-xs">
              <span className="text-neutral-300">{n}</span>
              <span className="font-mono text-neutral-100">{usdBn(cell(defi.history[defi.history.length - 1] ?? {}, n))}</span>
            </div>
            <ResponsiveContainer width="100%" height={110}>
              <LineChart data={defi.history} margin={{ top: 6, right: 6, bottom: 0, left: 0 }}>
                <CartesianGrid stroke={INK.grid} strokeDasharray="2 4" vertical={false} />
                <XAxis dataKey="day" {...AXIS} minTickGap={40} tickFormatter={(d: string) => d.slice(5)} />
                <YAxis {...AXIS} width={40} domain={["auto", "auto"]} tickFormatter={(v: number) => usdBn(v)} />
                <Tooltip
                  {...TOOLTIP_STYLE}
                  formatter={(value) => [`${usdBn(typeof value === "number" ? value : null)} ${tr("bn", "mil M")}`, n]}
                />
                <Line type="monotone" dataKey={n} stroke={SECTOR} strokeWidth={2} dot={defi.history.length < 15} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        ))}
      </div>
      <p className="mt-2 text-xs text-neutral-500">
        {tr(
          `${defi.snapshots} daily snapshots since ${defi.first_snapshot}. Each panel has its own scale (categories differ in size by 10×): read the shape, not the height.`,
          `${defi.snapshots} fotos diarias desde ${defi.first_snapshot}. Cada panel tiene su propia escala (las categorías difieren 10× en tamaño): se lee la forma, no la altura.`,
        )}
      </p>
    </>
  );
}

// --- Piezas comunes ------------------------------------------------------------------

function GrowthBars({
  data,
  reference,
  valueLabel,
}: {
  data: { label: string; v: number | null }[];
  reference: number | null;
  valueLabel: string;
}) {
  const vals = data.map((d) => d.v).filter(isNum);
  if (isNum(reference)) vals.push(reference);
  const lo = Math.min(0, ...vals);
  const hi = Math.max(0, ...vals);
  // Margen para que la etiqueta de una barra negativa no pise el nombre.
  const pad = (hi - lo) * 0.3 || 0.01;
  const domain: [number, number] = [lo < 0 ? lo - pad : 0, hi > 0 ? hi + pad : 0];
  return (
    <ResponsiveContainer width="100%" height={30 * data.length + 24}>
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 16, bottom: 0, left: 4 }}>
        <CartesianGrid stroke={INK.grid} strokeDasharray="2 4" horizontal={false} />
        <XAxis type="number" domain={domain} ticks={niceTicks(lo, hi)} {...AXIS} tickFormatter={(v: number) => signedPct(v, 0)} />
        <YAxis type="category" dataKey="label" {...AXIS} width={118} interval={0} />
        <ReferenceLine x={0} stroke={INK.axis} />
        {isNum(reference) && <ReferenceLine x={reference} stroke={BENCH} strokeDasharray="4 3" />}
        <Tooltip
          {...TOOLTIP_STYLE}
          cursor={{ fill: INK.grid }}
          formatter={(value) => [signedPct(typeof value === "number" ? value : null, 2), valueLabel]}
        />
        <Bar dataKey="v" barSize={12} radius={4} isAnimationActive={false}>
          {data.map((d) => (
            <Cell key={d.label} fill={SECTOR} />
          ))}
          <LabelList dataKey="v" content={BarLabel} />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

/** Etiqueta en la punta de la barra, hacia afuera según el signo. */
function BarLabel(props: unknown) {
  const { x, y, width, height, value } = props as { x: number; y: number; width: number; height: number; value: unknown };
  const v = typeof value === "number" ? value : null;
  const neg = isNum(v) && v < 0;
  const end = x + width;
  return (
    <text x={neg ? end - 6 : end + 6} y={y + height / 2} dy={4} textAnchor={neg ? "end" : "start"} fill={INK.text} fontSize={11}>
      {signedPct(v, 1)}
    </text>
  );
}

function MethodCard({ rep }: { rep: IndustriesReport }) {
  const { tr } = useLang();
  const m = rep.method;
  return (
    <Card title={tr("Method", "Método")}>
      <ul className="space-y-1 text-xs text-neutral-400">
        <li>{tr("Sector growth", "Crecimiento de un sector")}: {m.equity_growth}.</li>
        <li>{tr("Against the market", "Contra el mercado")}: {m.equity_relative}.</li>
        <li>{tr("DeFi category", "Categoría DeFi")}: {m.defi_tvl}.</li>
        <li>{tr("DeFi growth", "Crecimiento DeFi")}: {m.defi_growth}.</li>
        <li>{tr("Double counting", "Doble conteo")}: {m.defi_double_count}.</li>
        <li className="text-neutral-300">{m.not_flow}.</li>
        <li>
          {tr(
            "Not a forecast: M3 measured that the ranking of relative strength does not hold going forward (see Flows). Sources: Tiingo (sector ETFs) and DefiLlama (protocol list). Comes from",
            "No es pronóstico: la M3 midió que el orden de la fuerza relativa no se sostiene hacia adelante (ver Flujos). Fuentes: Tiingo (ETFs sectoriales) y DefiLlama (listado de protocolos). Sale de",
          )}{" "}
          <code>python -m marea.industries</code>.
        </li>
      </ul>
    </Card>
  );
}
