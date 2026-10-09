import { useQuery } from "@tanstack/react-query";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceArea,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Badge, Card, ErrorBox, Loading, Warning } from "../components/common/Card";
import { type Lang, useLang } from "../i18n";
import { api } from "../lib/api";
import { INK, SERIES_BY_ROLE, STATUS, TOOLTIP_STYLE } from "../lib/chartTheme";
import { DASH, isNum, num, signedPct } from "../lib/format";
import type {
  CotRow,
  LiquidityHistoryPoint,
  LiquidityIndicator,
  LiquidityLabel,
  LiquidityReport,
} from "../types";

/**
 * MAREA — monitor de liquidez global (fase M2).
 *
 * **Mide, no pronostica.** La página dice en qué régimen está la liquidez y
 * cómo cambió; no dice qué va a pasar con ningún activo. Cómo les fue a las
 * clases de activo en cada régimen es la pregunta de la M3 y se contesta con
 * pre-registro y medición de potencia, no con una tabla de promedios puesta
 * al lado del régimen: esa tabla se leería como pronóstico sin haber pasado
 * por ningún gate (regla 2).
 *
 * Todo lo que se dibuja sale del reporte que calcula `python -m marea.monitor`
 * con lo que se sabía en cada fecha. Los nombres y notas de los indicadores
 * llegan del backend ya en el idioma pedido (`marea/i18n.py`); las etiquetas
 * de régimen son códigos y se traducen acá.
 */
export function MacroPage() {
  const { lang, tr } = useLang();
  const q = useQuery({
    queryKey: ["macro", "liquidity", lang],
    queryFn: () => api.liquidity(lang),
    retry: false,
    staleTime: 600_000,
  });

  if (q.isLoading) return <Loading what={tr("the liquidity monitor", "el monitor de liquidez")} />;
  if (q.error) return <ErrorBox>{String(q.error)}</ErrorBox>;
  const rep = q.data;
  if (!rep) return null;

  const groups: { key: LiquidityIndicator["group"]; title: string; note: string }[] = [
    {
      key: "nucleo",
      title: tr("Core · part of the composite", "Núcleo · entra al compuesto"),
      note: tr(
        "Five components with long, point-in-time history, equal weights.",
        "Cinco componentes con historia larga y point-in-time, pesos iguales.",
      ),
    },
    {
      key: "contexto",
      title: tr("Context · outside the composite", "Contexto · fuera del compuesto"),
      note: tr(
        "Each one is left out for a declared reason (see the row's note).",
        "Cada uno queda fuera por un motivo declarado (ver la nota de la fila).",
      ),
    },
    {
      key: "chile",
      title: tr("Chile lens · outside the composite", "Lente Chile · fuera del compuesto"),
      note: tr(
        "Declared publication lag: the BCCh does not publish vintages.",
        "Rezago de publicación declarado: el BCCh no publica vintages.",
      ),
    },
  ];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-neutral-100">{tr("Macro · liquidity", "Macro · liquidez")}</h1>
          <p className="text-xs text-neutral-500">
            {tr("known as of", "conocido al")} {rep.as_of} · {tr("computed", "calculado")}{" "}
            {rep.computed_at_utc.replace("T", " ")} UTC
          </p>
        </div>
        <Badge tone="muted" title={tr("Phase M2: the regime is measured, not forecast", "Fase M2: el régimen se mide, no se pronostica")}>
          {tr("monitoring · no forecast", "monitoreo · sin pronóstico")}
        </Badge>
      </div>

      {rep.stale && (
        <Warning>
          {tr(
            <>
              The report is {rep.age_days} days old. Weekly Fed data come out on Thursdays: run{" "}
              <code>uv run python -m marea.data.ingest todo</code> and then <code>uv run python -m marea.monitor</code>.
            </>,
            <>
              El reporte tiene {rep.age_days} días. Los datos semanales de la Fed salen los jueves: correr{" "}
              <code>uv run python -m marea.data.ingest todo</code> y luego <code>uv run python -m marea.monitor</code>.
            </>,
          )}
        </Warning>
      )}

      <RegimeCard rep={rep} />

      <div className="grid gap-4 xl:grid-cols-2">
        <Card title={tr("Composite percentile", "Percentil del compuesto")}>
          <CompositeChart history={rep.history} from={rep.regime.available_from} />
          <p className="mt-2 text-xs text-neutral-500">
            {tr(
              "Bands: bottom third contractionary, top third expansionary. Each point was computed with what was known that Friday and against its strict past.",
              "Bandas: tercio inferior contractivo, superior expansivo. Cada punto se calculó con lo que se sabía ese viernes y contra su pasado estricto.",
            )}
          </p>
        </Card>
        <Card title={tr("Fed net liquidity · billion USD", "Liquidez neta de la Fed · miles de millones USD")}>
          <LevelChart history={rep.history} />
          <p className="mt-2 text-xs text-neutral-500">
            {tr(
              "Balance sheet (WALCL) − Treasury account (TGA) − reverse repos (RRP). Since 2014: before that there was no daily overnight RRP.",
              "Balance (WALCL) − cuenta del Tesoro (TGA) − repos inversos (RRP). Desde 2014: antes no había RRP overnight diario.",
            )}
          </p>
        </Card>
      </div>

      {groups.map((g) => (
        <Card key={g.key} title={g.title} right={<span className="text-xs text-neutral-600">{g.note}</span>}>
          <IndicatorTable rows={rep.indicators.filter((i) => i.group === g.key)} />
        </Card>
      ))}

      <Card
        title={tr(
          "CFTC positioning · net speculative as % of open interest",
          "Posicionamiento CFTC · neto especulativo como % del interés abierto",
        )}
        right={<span className="text-xs text-neutral-600">{tr("concentration, not liquidity", "concentración, no liquidez")}</span>}
      >
        <CotTable rows={rep.cot} />
      </Card>

      <Card title={tr("Method", "Método")}>
        <ul className="space-y-1 text-xs text-neutral-400">
          <li>
            {tr("Signal", "Señal")}: {rep.method.signal}.{" "}
            {tr(
              `The ${rep.method.short_change_weeks}-week change is shown and not classified.`,
              `El cambio a ${rep.method.short_change_weeks} semanas se muestra y no se clasifica.`,
            )}
          </li>
          <li>
            {tr("Percentile", "Percentil")}: {rep.method.percentile};{" "}
            {tr(
              `at least ${rep.method.min_history_weeks} weeks of history.`,
              `mínimo ${rep.method.min_history_weeks} semanas de historia.`,
            )}
          </li>
          <li>
            {tr("Composite", "Compuesto")}: {rep.method.composite}.
          </li>
          <li>
            {tr("Weights", "Pesos")}: {rep.method.weights}.
          </li>
          <li>
            {tr(
              '"~" in the reading = data from before ALFRED\'s first vintage, with a measured fallback lag: not exactly point-in-time.',
              '"~" en la lectura = dato anterior a la primera vintage de ALFRED, con rezago de respaldo medido: no es point-in-time exacto.',
            )}
          </li>
        </ul>
      </Card>
    </div>
  );
}

const LABEL_TONE: Record<LiquidityLabel, string> = {
  expansivo: "text-accent",
  neutro: "text-neutral-300",
  contractivo: "text-danger",
};

const LABEL_EN: Record<LiquidityLabel, string> = {
  expansivo: "expansionary",
  neutro: "neutral",
  contractivo: "contractionary",
};

function labelText(lang: Lang, label: LiquidityLabel | null): string {
  if (!label) return DASH;
  return lang === "en" ? LABEL_EN[label] : label;
}

function LabelText({ label }: { label: LiquidityLabel | null }) {
  const { lang } = useLang();
  if (!label) return <span className="text-neutral-600">{DASH}</span>;
  return <span className={LABEL_TONE[label]}>{labelText(lang, label)}</span>;
}

function RegimeCard({ rep }: { rep: LiquidityReport }) {
  const { lang, tr } = useLang();
  const r = rep.regime;
  const core = rep.indicators.filter((i) => i.in_composite);
  const p = r.persistence;
  return (
    <Card title={tr("Liquidity regime", "Régimen de liquidez")}>
      <div className="flex flex-wrap items-end gap-8">
        <div>
          <div className="text-3xl font-semibold">
            <LabelText label={r.label} />
          </div>
          <div className="mt-1 text-xs text-neutral-500">
            {tr("composite percentile", "percentil del compuesto")} {num(r.composite_pct, 2)} · {tr("since", "desde")}{" "}
            {r.since ?? DASH}
          </div>
        </div>
        <div className="text-xs text-neutral-400">
          <div>
            {tr("4 weeks ago", "hace 4 semanas")}: <LabelText label={r.label_4w_ago} />
          </div>
          <div>
            {tr("13 weeks ago", "hace 13 semanas")}: <LabelText label={r.label_13w_ago} />
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {core.map((i) => (
            <Badge
              key={i.key}
              tone={i.label === "expansivo" ? "ok" : i.label === "contractivo" ? "bad" : "neutral"}
              title={`${tr("percentile", "percentil")} ${num(i.pct, 2)}`}
            >
              {i.name.split(" (")[0]}: {labelText(lang, i.label)}
            </Badge>
          ))}
        </div>
      </div>
      <p className="mt-3 text-xs text-neutral-500">
        {tr(
          `It describes the state of liquidity relative to its own history. It does not anticipate returns: how assets did in each regime is measured in M3, with pre-registration. Composite history since ${r.available_from ?? DASH}, i.e. few cycles: a "high" regime is high against those years.`,
          `Describe el estado de la liquidez respecto de su propia historia. No anticipa retornos: cómo les fue a los activos en cada régimen se mide en la M3, con pre-registro. Historia del compuesto desde ${r.available_from ?? DASH}, o sea pocos ciclos: un régimen "alto" lo es contra esos años.`,
        )}
      </p>
      <p className="mt-1 text-xs text-warn/80">
        {tr(
          `Measured persistence: ${p.changes} label changes in ${p.weeks} weeks; a label lasts ${num(p.median_run_weeks, 0)} weeks at the median (maximum ${p.max_run_weeks ?? DASH}). An isolated one-week change is not a regime turn.`,
          `Persistencia medida: ${p.changes} cambios de etiqueta en ${p.weeks} semanas; una etiqueta dura ${num(p.median_run_weeks, 0)} semanas en la mediana (máximo ${p.max_run_weeks ?? DASH}). Un cambio aislado de una semana no es un giro de régimen.`,
        )}
      </p>
    </Card>
  );
}

function fmtChange(v: number | null, kind: LiquidityIndicator["kind"], unit: string): string {
  if (!isNum(v)) return DASH;
  if (kind === "log") return signedPct(v, 2);
  const s = v.toFixed(2);
  return `${v > 0 ? "+" : ""}${s} ${unit === "%" ? "pp" : unit}`;
}

function IndicatorTable({ rows }: { rows: LiquidityIndicator[] }) {
  const { tr } = useLang();
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-neutral-500">
            <th className="py-1 pr-3 font-normal">{tr("indicator", "indicador")}</th>
            <th className="py-1 pr-3 font-normal">{tr("data as of", "dato del")}</th>
            <th className="py-1 pr-3 text-right font-normal">{tr("level", "nivel")}</th>
            <th className="py-1 pr-3 text-right font-normal">{tr("4 wk", "4 sem")}</th>
            <th className="py-1 pr-3 text-right font-normal">{tr("13 wk", "13 sem")}</th>
            <th className="py-1 pr-3 text-right font-normal">{tr("percentile", "percentil")}</th>
            <th className="py-1 pr-3 font-normal">{tr("reading", "lectura")}</th>
            <th className="py-1 font-normal">{tr("13 wk ago", "hace 13 sem")}</th>
          </tr>
        </thead>
        <tbody className="font-mono">
          {rows.map((i) => (
            <tr key={i.key} className="border-t border-neutral-800/70 align-top">
              <td className="py-1.5 pr-3 font-sans">
                <div className="text-neutral-200">{i.name}</div>
                {i.caveat && <div className="text-xs text-neutral-500">{i.caveat}</div>}
              </td>
              <td className="py-1.5 pr-3 text-neutral-400">{i.obs_date ?? DASH}</td>
              <td className="py-1.5 pr-3 text-right text-neutral-200" title={i.unit}>
                {isNum(i.level) ? i.level.toLocaleString("en-US", { maximumFractionDigits: 2 }) : DASH}
              </td>
              <td className="py-1.5 pr-3 text-right text-neutral-400">{fmtChange(i.chg_4w, i.kind, i.unit)}</td>
              <td className="py-1.5 pr-3 text-right text-neutral-200">{fmtChange(i.chg_13w, i.kind, i.unit)}</td>
              <td
                className="py-1.5 pr-3 text-right text-neutral-300"
                title={
                  i.pct_from
                    ? tr(`percentile available since ${i.pct_from}`, `percentil disponible desde ${i.pct_from}`)
                    : tr("not enough history", "sin historia suficiente")
                }
              >
                {num(i.pct, 2)}
              </td>
              <td className="py-1.5 pr-3 font-sans">
                {i.sign === 0 ? (
                  <span className="text-neutral-500">{tr("ambiguous", "ambiguo")}</span>
                ) : (
                  <LabelText label={i.label} />
                )}
                {!i.exact && i.obs_date && <span className="text-neutral-500"> ~</span>}
              </td>
              <td className="py-1.5 font-sans text-xs">
                <LabelText label={i.label_13w_ago} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CotTable({ rows }: { rows: CotRow[] }) {
  const { tr } = useLang();
  if (rows.length === 0) {
    return <p className="text-sm text-neutral-500">{tr("No COT data: run the ingestion.", "Sin datos COT: correr la ingesta.")}</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-neutral-500">
            <th className="py-1 pr-3 font-normal">{tr("market", "mercado")}</th>
            <th className="py-1 pr-3 font-normal">{tr("report (Tuesday)", "reporte (martes)")}</th>
            <th className="py-1 pr-3 text-right font-normal">{tr("net % OI", "neto % OI")}</th>
            <th className="py-1 pr-3 text-right font-normal">{tr("4 wk ago", "hace 4 sem")}</th>
            <th className="py-1 text-right font-normal">{tr("3-year percentile", "percentil 3 años")}</th>
          </tr>
        </thead>
        <tbody className="font-mono">
          {rows.map((c) => (
            <tr key={c.code} className="border-t border-neutral-800/70">
              <td className="py-1.5 pr-3 font-sans text-neutral-200">
                {c.name}
                {c.etf && <span className="ml-2 text-xs text-neutral-500">{c.etf}</span>}
              </td>
              <td
                className="py-1.5 pr-3 text-neutral-400"
                title={tr(`known on ${c.available_date}`, `se conoce el ${c.available_date}`)}
              >
                {c.report_date}
              </td>
              <td className="py-1.5 pr-3 text-right text-neutral-200">{signedPct(c.net_pct_oi, 1)}</td>
              <td className="py-1.5 pr-3 text-right text-neutral-400">{signedPct(c.net_pct_oi_4w, 1)}</td>
              <td className="py-1.5 text-right text-neutral-300">{num(c.pct_3y, 2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-2 text-xs text-neutral-500">
        {tr(
          "Speculator = leveraged funds in financials, managed money in commodities. An extreme percentile signals concentrated positioning, not direction.",
          "Especulador = fondos apalancados en financieros, managed money en commodities. Un percentil extremo indica posicionamiento concentrado, no dirección.",
        )}
      </p>
    </div>
  );
}

const AXIS = { stroke: INK.axis, tick: { fill: INK.muted, fontSize: 10 } };

function CompositeChart({ history, from }: { history: LiquidityHistoryPoint[]; from: string | null }) {
  const { tr } = useLang();
  const data = history.filter((h) => from !== null && h.date >= from);
  if (data.length < 2) {
    return (
      <p className="py-8 text-center text-sm text-neutral-500">{tr("No composite history.", "Sin historia del compuesto.")}</p>
    );
  }
  return (
    <ResponsiveContainer width="100%" height={220}>
      <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 4 }}>
        <ReferenceArea y1={2 / 3} y2={1} fill={STATUS.good} fillOpacity={0.06} />
        <ReferenceArea y1={0} y2={1 / 3} fill={STATUS.bad} fillOpacity={0.06} />
        <CartesianGrid stroke={INK.grid} strokeDasharray="2 4" vertical={false} />
        <XAxis dataKey="date" {...AXIS} minTickGap={50} />
        <YAxis domain={[0, 1]} ticks={[0, 1 / 3, 2 / 3, 1]} tickFormatter={(v: number) => v.toFixed(2)} {...AXIS} width={40} />
        <Tooltip
          {...TOOLTIP_STYLE}
          formatter={(value) => [num(typeof value === "number" ? value : null, 2), tr("percentile", "percentil")]}
        />
        <Line
          type="stepAfter"
          dataKey="composite_pct"
          stroke={SERIES_BY_ROLE.forecast}
          strokeWidth={1.5}
          dot={false}
          connectNulls={false}
          isAnimationActive={false}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}

function LevelChart({ history }: { history: LiquidityHistoryPoint[] }) {
  const { tr } = useLang();
  const data = history.filter((h) => isNum(h.fed_net));
  if (data.length < 2) {
    return (
      <p className="py-8 text-center text-sm text-neutral-500">{tr("No net liquidity data.", "Sin datos de liquidez neta.")}</p>
    );
  }
  return (
    <ResponsiveContainer width="100%" height={220}>
      <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 4 }}>
        <CartesianGrid stroke={INK.grid} strokeDasharray="2 4" vertical={false} />
        <XAxis dataKey="date" {...AXIS} minTickGap={50} />
        <YAxis
          {...AXIS}
          width={52}
          domain={["auto", "auto"]}
          tickFormatter={(v: number) => v.toLocaleString("en-US", { maximumFractionDigits: 0 })}
        />
        <Tooltip
          {...TOOLTIP_STYLE}
          formatter={(value) => [
            isNum(typeof value === "number" ? value : null)
              ? (value as number).toLocaleString("en-US", { maximumFractionDigits: 0 })
              : DASH,
            tr("Fed net", "Fed neta"),
          ]}
        />
        <Line
          type="monotone"
          dataKey="fed_net"
          stroke={SERIES_BY_ROLE.reference}
          strokeWidth={1.5}
          dot={false}
          isAnimationActive={false}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
