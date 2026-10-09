import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { CalibrationCurve } from "../components/backtest/CalibrationCurve";
import { Badge, Card, ErrorBox, Loading, Row } from "../components/common/Card";
import { type Lang, useLang } from "../i18n";
import { api } from "../lib/api";
import { count, num, pValue, pct } from "../lib/format";
import type { GateVerdict, RegressionMetrics, RunSummary } from "../types";

/**
 * Página 3 — el gate y sus artefactos.
 *
 * **Es de solo lectura y eso es una decisión, no una etapa pendiente.** Un
 * walk-forward son ~10 ajustes de boosting sobre 69k barras; lanzarlo desde
 * acá le competiría el CPU al analista en vivo justo mientras corre la
 * validación forward. Los runs se corren por CLI y quedan versionados en
 * `backend/artifacts/`; esta página los lee.
 *
 * El veredicto tampoco se calcula acá: viene de la API, que a su vez lo saca
 * de `RunSummary`, que importa los umbrales de `models/experiment.py`. Tres
 * capas y una sola definición del gate.
 */
export function BacktestPage() {
  const { tr } = useLang();
  const [selected, setSelected] = useState<string | null>(null);

  const runs = useQuery({ queryKey: ["runs"], queryFn: api.runs, staleTime: 300_000 });
  const gate = useQuery({
    queryKey: ["gate", "backtest"],
    queryFn: () => api.gate(),
    retry: false,
    staleTime: 300_000,
  });
  const forward = useQuery({
    queryKey: ["forward"],
    queryFn: api.forward,
    retry: false,
    staleTime: 300_000,
  });

  const runId = selected ?? gate.data?.run_id ?? runs.data?.runs.at(-1)?.run_id ?? null;
  const detail = useQuery({
    queryKey: ["run", runId],
    queryFn: () => api.run(runId!),
    enabled: Boolean(runId),
    staleTime: 300_000,
  });

  if (runs.isLoading) return <Loading what={tr("the gate artifacts", "los artefactos del gate")} />;
  if (runs.error) return <ErrorBox>{String(runs.error)}</ErrorBox>;

  const artifact = detail.data?.artifact;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold text-neutral-100">{tr("15m gate backtest", "Backtest del gate 15m")}</h1>
        <p className="text-xs text-neutral-500">
          {tr(
            `${runs.data?.n ?? 0} versioned runs · read-only (runs are made with`,
            `${runs.data?.n ?? 0} runs versionados · solo lectura (los runs se corren con`,
          )}{" "}
          <span className="font-mono">bob.backtest.runner</span>)
        </p>
      </div>

      {/* --- Veredicto de la configuración que corre en vivo -------------- */}
      {gate.data && (
        <Card
          title={tr(
            `Gate verdict — ${gate.data.variant}/${gate.data.vol_kind} (the one that issues live)`,
            `Veredicto del gate — ${gate.data.variant}/${gate.data.vol_kind} (la que emite en vivo)`,
          )}
          right={
            <Badge tone={gate.data.gate.habilitado ? "ok" : "bad"}>
              {gate.data.gate.habilitado
                ? tr("enabled", "habilitado")
                : tr("NOT enabled — no directional signal", "NO habilitado — sin señal direccional")}
            </Badge>
          }
        >
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <div>
              <h3 className="mb-2 text-xs uppercase tracking-wider text-neutral-500">
                {tr("Target 1 — direction (did not pass)", "Target 1 — dirección (no pasó)")}
              </h3>
              <Criteria verdict={gate.data.gate} />
              <table className="mt-2 w-full text-xs">
                <thead>
                  <tr className="text-neutral-600">
                    <th className="py-1 text-left font-normal">{tr("direction", "dirección")}</th>
                    <th className="text-right font-normal">AUC</th>
                    <th className="text-right font-normal">BSS</th>
                    <th className="text-right font-normal">calib</th>
                  </tr>
                </thead>
                <tbody className="font-mono">
                  {Object.entries(gate.data.directions).map(([d, m]) => (
                    <tr key={d} className="border-t border-neutral-800">
                      <td className="py-1 text-neutral-300">{d}</td>
                      <td
                        className={`text-right ${
                          m.auc > gate.data!.gate.thresholds.min_auc
                            ? "text-accent"
                            : "text-danger"
                        }`}
                      >
                        {num(m.auc, 3)}
                      </td>
                      <td
                        className={`text-right ${
                          m.bss > gate.data!.gate.thresholds.min_bss
                            ? "text-accent"
                            : "text-danger"
                        }`}
                      >
                        {num(m.bss, 4)}
                      </td>
                      <td className="text-right text-accent">
                        {num(m.calibration_error_pp, 1)}pp
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div>
              <h3 className="mb-2 text-xs uppercase tracking-wider text-neutral-500">
                {tr(
                  "Target 2 — volatility (passed, and it supports the product)",
                  "Target 2 — volatilidad (sí pasó, y sostiene el producto)",
                )}
              </h3>
              <VolatilityTable vol={gate.data.volatility} />
            </div>
          </div>

          <p className="mt-3 border-t border-neutral-800 pt-3 text-[11px] leading-relaxed text-neutral-500">
            {tr(
              'Both criteria, in every direction. Calibration says "when I say 70%, I am right 70% of the time"; discrimination says "I know which cases are the 70% ones". A model that always predicts the base rate is perfectly calibrated by construction and useless — passing only the first does not allow trading.',
              'Los dos criterios, en todas las direcciones. La calibración dice "cuando digo 70%, acierto 70%"; la discriminación dice "sé cuáles son los casos de 70%". Un modelo que predice siempre la tasa base está perfectamente calibrado por construcción y es inútil — pasar solo el primero no habilita operar.',
            )}
          </p>
        </Card>
      )}

      {/* --- Escalera de ablación --------------------------------------- */}
      <Card title={tr("Ablation — do the new families help?", "Ablación — ¿aportan las familias nuevas?")}>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-xs">
            <thead>
              <tr className="text-neutral-600">
                <th className="py-1 text-left font-normal">run</th>
                <th className="text-right font-normal">feat</th>
                <th className="text-right font-normal">AUC long</th>
                <th className="text-right font-normal">AUC short</th>
                <th className="text-right font-normal">BSS long</th>
                <th className="text-right font-normal">BSS short</th>
                <th className="text-right font-normal">R² vol</th>
                <th className="text-right font-normal">{tr("verdict", "veredicto")}</th>
              </tr>
            </thead>
            <tbody className="font-mono">
              {(runs.data?.runs ?? []).map((r) => (
                <RunRow
                  key={r.run_id}
                  run={r}
                  selected={r.run_id === runId}
                  onSelect={() => setSelected(r.run_id)}
                />
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-3 border-t border-neutral-800 pt-3 text-[11px] leading-relaxed text-neutral-500">
          {tr(
            <>
              The derivatives and order-book families <strong>worsen</strong> discrimination, monotonically with the
              number of features and in both directions at once. That refutes the hypothesis behind Phase 2b: the gate
              was not failing for lack of data. The 730 days of derivatives and book are real infrastructure; their
              premise was false.
            </>,
            <>
              Las familias de derivados y libro <strong>empeoran</strong> la discriminación, de forma monótona con el
              número de features y en las dos direcciones a la vez. Eso refuta la hipótesis que motivó la Fase 2b: el
              gate no fallaba por falta de datos. Los 730 días de derivados y libro son infraestructura real; su premisa
              era falsa.
            </>,
          )}
        </p>
      </Card>

      {/* --- Detalle del run seleccionado -------------------------------- */}
      {detail.isLoading && <Loading what={tr("the run detail", "el detalle del run")} />}
      {artifact && (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          {Object.entries(artifact.directions).map(([d, dir]) => (
            <Card
              key={d}
              title={`${tr("Reliability curve", "Curva de fiabilidad")} — ${d}`}
              right={
                <span className="font-mono text-[11px] text-neutral-600">
                  {tr("mean error", "error medio")} {num(dir.model.mean_calibration_error_pp, 2)}pp · AUC{" "}
                  {num(dir.model.auc, 3)}
                </span>
              }
            >
              <CalibrationCurve
                buckets={dir.model.buckets}
                baseRate={dir.model.base_rate}
              />
              <div className="mt-2 grid grid-cols-2 gap-x-6">
                <Row label={tr("samples", "muestras")} value={count(dir.n_samples)} />
                <Row label={tr("break-even prob.", "prob. de equilibrio")} value={pct(dir.breakeven_prob, 1)} />
                <Row label="Brier" value={num(dir.model.brier, 4)} />
                <Row label="ECE" value={num(dir.model.ece, 4)} />
              </div>
            </Card>
          ))}
        </div>
      )}

      {/* --- El corte forward congelado ---------------------------------- */}
      {forward.data && (
        <Card
          title={tr("Frozen forward cut", "Corte forward congelado")}
          right={
            <span className="font-mono text-[11px] text-neutral-600">
              {forward.data.artifact_id}
            </span>
          }
        >
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <div>
              <Row
                label={tr("resolved / open / gaps", "resueltos / abiertos / huecos")}
                value={`${forward.data.report.n_resolved} / ${forward.data.report.n_open} / ${forward.data.report.n_gap}`}
              />
              <Row
                label={tr("range", "rango")}
                value={`${forward.data.report.date_from} → ${forward.data.report.date_to}`}
              />
              <Row
                label="R² vs EWMA"
                value={num(forward.data.report.volatility?.r2_vs_baseline, 3)}
                hint={tr(
                  "the comparable number: R² against the sample's own mean drops by arithmetic in a short window",
                  "el número comparable: el R² contra la media de la propia muestra cae por aritmética en una ventana corta",
                )}
              />
              <Row
                label="QLIKE"
                value={num(forward.data.report.volatility?.qlike, 4)}
                hint={tr(
                  "its average is dominated by the tail: it depends on the window as much as R² does (Phase 5, point 13)",
                  "su media la domina la cola: depende de la ventana tanto como el R² (Fase 5, punto 13)",
                )}
              />
            </div>
            <div>
              {Object.entries(forward.data.report.cones).map(([alpha, metrics]) => {
                const ci = forward.data!.report.cone_ci[alpha];
                return (
                  <Row
                    key={alpha}
                    label={`${tr("cone", "cono")} ${pct(metrics.nominal_coverage, 0)}`}
                    value={`${pct(metrics.empirical_coverage, 1)}${
                      ci ? ` · ${tr("95% CI", "IC95")} [${pct(ci[0], 1)}, ${pct(ci[1], 1)}]` : ""
                    }`}
                  />
                );
              })}
              <Row
                label={tr("independent blocks", "bloques independientes")}
                value={num(forward.data.report.n_blocks, 1)}
                hint={tr(
                  "with H=16 two consecutive forecasts share 15 of 16 bars: they are not independent observations",
                  "con H=16 dos pronósticos consecutivos comparten 15 de 16 barras: no son observaciones independientes",
                )}
              />
              <Row
                label="model_version"
                value={forward.data.report.model_versions.join(", ")}
                tone={
                  forward.data.report.model_versions.length > 1 ? "text-warn" : ""
                }
              />
            </div>
          </div>
        </Card>
      )}
    </div>
  );
}

/**
 * El veredicto de Diebold-Mariano viene escrito en español en el artefacto
 * (`DieboldMariano.verdict()`). Se traduce por su forma, y lo que no se
 * reconoce se muestra tal cual.
 */
function dmVerdict(lang: Lang, v: string): string {
  if (lang === "es") return v;
  return v
    .replace("gana el modelo de forma significativa", "the model wins significantly")
    .replace("gana el baseline de forma significativa", "the baseline wins significantly")
    .replace("diferencia NO significativa", "difference NOT significant")
    .replace("indistinguible de suerte", "indistinguishable from luck")
    .replace("sin datos suficientes para concluir", "not enough data to conclude");
}

function Criteria({ verdict }: { verdict: GateVerdict }) {
  const { tr } = useLang();
  return (
    <div className="flex gap-2">
      <Badge tone={verdict.calibra ? "ok" : "bad"}>
        {tr("calibration", "calibración")} {verdict.calibra ? "✓" : "✗"} (&lt;
        {verdict.thresholds.max_calibration_error_pp}pp)
      </Badge>
      <Badge tone={verdict.discrimina ? "ok" : "bad"}>
        {tr("discrimination", "discriminación")} {verdict.discrimina ? "✓" : "✗"} (AUC &gt;
        {verdict.thresholds.min_auc}, BSS &gt; {verdict.thresholds.min_bss})
      </Badge>
    </div>
  );
}

function VolatilityTable({
  vol,
}: {
  vol: {
    model: RegressionMetrics | null;
    baseline_ewma: RegressionMetrics | null;
    baseline_garch: RegressionMetrics | null;
    baseline_har: RegressionMetrics | null;
    dm_vs_ewma: { p_value: number; verdict: string } | null;
    dm_vs_har: { p_value: number; verdict: string } | null;
  };
}) {
  const { lang, tr } = useLang();
  const model = tr("model", "modelo");
  const rows: [string, RegressionMetrics | null][] = [
    [model, vol.model],
    ["EWMA", vol.baseline_ewma],
    ["GARCH(1,1)", vol.baseline_garch],
    ["HAR-RV", vol.baseline_har],
  ];
  return (
    <>
      <table className="w-full text-xs">
        <thead>
          <tr className="text-neutral-600">
            <th className="py-1 text-left font-normal" />
            <th className="text-right font-normal">RMSE</th>
            <th className="text-right font-normal">R²</th>
            <th className="text-right font-normal">QLIKE</th>
          </tr>
        </thead>
        <tbody className="font-mono">
          {rows.map(([label, m]) => (
            <tr key={label} className="border-t border-neutral-800">
              <td
                className={`py-1 ${
                  label === model ? "text-accent" : "text-neutral-400"
                }`}
              >
                {label}
              </td>
              <td className="text-right text-neutral-300">{num(m?.rmse, 5)}</td>
              <td className="text-right text-neutral-300">{num(m?.r2, 3)}</td>
              <td className="text-right text-neutral-300">{num(m?.qlike, 4)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="mt-2 space-y-1 text-[11px] text-neutral-500">
        {vol.dm_vs_ewma && (
          <div>
            Diebold-Mariano vs EWMA: p={pValue(vol.dm_vs_ewma.p_value)} — {dmVerdict(lang, vol.dm_vs_ewma.verdict)}
          </div>
        )}
        {vol.dm_vs_har && (
          <div>
            vs HAR-RV: p={pValue(vol.dm_vs_har.p_value)} — {dmVerdict(lang, vol.dm_vs_har.verdict)}
          </div>
        )}
      </div>
    </>
  );
}

function RunRow({
  run,
  selected,
  onSelect,
}: {
  run: RunSummary;
  selected: boolean;
  onSelect: () => void;
}) {
  const { tr } = useLang();
  return (
    <tr
      onClick={onSelect}
      className={`cursor-pointer border-t border-neutral-800 transition hover:bg-neutral-800/40 ${
        selected ? "bg-neutral-800/60" : ""
      }`}
    >
      <td className="py-1.5 text-neutral-300">
        {run.variant === "sin-etiqueta" ? tr("unlabelled", "sin-etiqueta") : run.variant}
        <span className="text-neutral-600">/{run.vol_kind}</span>
        <div className="text-[10px] text-neutral-600">{run.run_id}</div>
      </td>
      <td className="text-right text-neutral-400">{run.n_features}</td>
      <td className="text-right text-neutral-300">
        {num(run.directions.long?.auc, 3)}
      </td>
      <td className="text-right text-neutral-300">
        {num(run.directions.short?.auc, 3)}
      </td>
      <td className="text-right text-neutral-300">
        {num(run.directions.long?.bss, 4)}
      </td>
      <td className="text-right text-neutral-300">
        {num(run.directions.short?.bss, 4)}
      </td>
      <td className="text-right text-neutral-300">
        {num(run.volatility.model?.r2, 3)}
      </td>
      <td className="text-right">
        <Badge tone={run.gate.habilitado ? "ok" : "muted"}>
          {run.gate.habilitado ? tr("enabled", "habilitado") : "✗"}
        </Badge>
      </td>
    </tr>
  );
}
