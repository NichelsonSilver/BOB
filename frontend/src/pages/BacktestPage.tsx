import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { CalibrationCurve } from "../components/backtest/CalibrationCurve";
import { Badge, Card, ErrorBox, Loading, Row } from "../components/common/Card";
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

  if (runs.isLoading) return <Loading what="los artefactos del gate" />;
  if (runs.error) return <ErrorBox>{String(runs.error)}</ErrorBox>;

  const artifact = detail.data?.artifact;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold text-neutral-100">Backtest del gate 15m</h1>
        <p className="text-xs text-neutral-500">
          {runs.data?.n ?? 0} runs versionados · solo lectura (los runs se corren
          con <span className="font-mono">bob.backtest.runner</span>)
        </p>
      </div>

      {/* --- Veredicto de la configuración que corre en vivo -------------- */}
      {gate.data && (
        <Card
          title={`Veredicto del gate — ${gate.data.variant}/${gate.data.vol_kind} (la que emite en vivo)`}
          right={
            <Badge tone={gate.data.gate.habilitado ? "ok" : "bad"}>
              {gate.data.gate.habilitado
                ? "habilitado"
                : "NO habilitado — sin señal direccional"}
            </Badge>
          }
        >
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <div>
              <h3 className="mb-2 text-xs uppercase tracking-wider text-neutral-500">
                Target 1 — dirección (no pasó)
              </h3>
              <Criteria verdict={gate.data.gate} />
              <table className="mt-2 w-full text-xs">
                <thead>
                  <tr className="text-neutral-600">
                    <th className="py-1 text-left font-normal">dirección</th>
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
                Target 2 — volatilidad (sí pasó, y sostiene el producto)
              </h3>
              <VolatilityTable vol={gate.data.volatility} />
            </div>
          </div>

          <p className="mt-3 border-t border-neutral-800 pt-3 text-[11px] leading-relaxed text-neutral-500">
            Los dos criterios, en todas las direcciones. La calibración dice
            "cuando digo 70%, acierto 70%"; la discriminación dice "sé cuáles son
            los casos de 70%". Un modelo que predice siempre la tasa base está
            perfectamente calibrado por construcción y es inútil — pasar solo el
            primero no habilita operar.
          </p>
        </Card>
      )}

      {/* --- Escalera de ablación --------------------------------------- */}
      <Card title="Ablación — ¿aportan las familias nuevas?">
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
                <th className="text-right font-normal">veredicto</th>
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
          Las familias de derivados y libro <strong>empeoran</strong> la
          discriminación, de forma monótona con el número de features y en las
          dos direcciones a la vez. Eso refuta la hipótesis que motivó la Fase
          2b: el gate no fallaba por falta de datos. Los 730 días de derivados y
          libro son infraestructura real; su premisa era falsa.
        </p>
      </Card>

      {/* --- Detalle del run seleccionado -------------------------------- */}
      {detail.isLoading && <Loading what="el detalle del run" />}
      {artifact && (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          {Object.entries(artifact.directions).map(([d, dir]) => (
            <Card
              key={d}
              title={`Curva de fiabilidad — ${d}`}
              right={
                <span className="font-mono text-[11px] text-neutral-600">
                  error medio {num(dir.model.mean_calibration_error_pp, 2)}pp · AUC{" "}
                  {num(dir.model.auc, 3)}
                </span>
              }
            >
              <CalibrationCurve
                buckets={dir.model.buckets}
                baseRate={dir.model.base_rate}
              />
              <div className="mt-2 grid grid-cols-2 gap-x-6">
                <Row label="muestras" value={count(dir.n_samples)} />
                <Row label="prob. de equilibrio" value={pct(dir.breakeven_prob, 1)} />
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
          title="Corte forward congelado"
          right={
            <span className="font-mono text-[11px] text-neutral-600">
              {forward.data.artifact_id}
            </span>
          }
        >
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <div>
              <Row
                label="resueltos / abiertos / huecos"
                value={`${forward.data.report.n_resolved} / ${forward.data.report.n_open} / ${forward.data.report.n_gap}`}
              />
              <Row
                label="rango"
                value={`${forward.data.report.date_from} → ${forward.data.report.date_to}`}
              />
              <Row
                label="R² vs EWMA"
                value={num(forward.data.report.volatility?.r2_vs_baseline, 3)}
                hint="el número comparable: el R² contra la media de la propia muestra cae por aritmética en una ventana corta"
              />
              <Row
                label="QLIKE"
                value={num(forward.data.report.volatility?.qlike, 4)}
                hint="no depende de la dispersión de la muestra"
              />
            </div>
            <div>
              {Object.entries(forward.data.report.cones).map(([alpha, metrics]) => {
                const ci = forward.data!.report.cone_ci[alpha];
                return (
                  <Row
                    key={alpha}
                    label={`cono ${pct(metrics.nominal_coverage, 0)}`}
                    value={`${pct(metrics.empirical_coverage, 1)}${
                      ci ? ` · IC95 [${pct(ci[0], 1)}, ${pct(ci[1], 1)}]` : ""
                    }`}
                  />
                );
              })}
              <Row
                label="bloques independientes"
                value={num(forward.data.report.n_blocks, 1)}
                hint="con H=16 dos pronósticos consecutivos comparten 15 de 16 barras: no son 288 observaciones independientes"
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

function Criteria({ verdict }: { verdict: GateVerdict }) {
  return (
    <div className="flex gap-2">
      <Badge tone={verdict.calibra ? "ok" : "bad"}>
        calibración {verdict.calibra ? "✓" : "✗"} (&lt;
        {verdict.thresholds.max_calibration_error_pp}pp)
      </Badge>
      <Badge tone={verdict.discrimina ? "ok" : "bad"}>
        discriminación {verdict.discrimina ? "✓" : "✗"} (AUC &gt;
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
  const rows: [string, RegressionMetrics | null][] = [
    ["modelo", vol.model],
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
                  label === "modelo" ? "text-accent" : "text-neutral-400"
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
            Diebold-Mariano vs EWMA: p={pValue(vol.dm_vs_ewma.p_value)} —{" "}
            {vol.dm_vs_ewma.verdict}
          </div>
        )}
        {vol.dm_vs_har && (
          <div>
            vs HAR-RV: p={pValue(vol.dm_vs_har.p_value)} — {vol.dm_vs_har.verdict}
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
  return (
    <tr
      onClick={onSelect}
      className={`cursor-pointer border-t border-neutral-800 transition hover:bg-neutral-800/40 ${
        selected ? "bg-neutral-800/60" : ""
      }`}
    >
      <td className="py-1.5 text-neutral-300">
        {run.variant}
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
          {run.gate.habilitado ? "habilitado" : "✗"}
        </Badge>
      </td>
    </tr>
  );
}
