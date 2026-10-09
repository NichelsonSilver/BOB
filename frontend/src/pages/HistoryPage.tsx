import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { Badge, Card, ErrorBox, Loading, Row, Warning } from "../components/common/Card";
import { TargetBar } from "../components/layout/TargetBar";
import { useTarget } from "../hooks/useTarget";
import { api } from "../lib/api";
import { count, num, pct, pValue, price, stamp } from "../lib/format";
import type { ForecastRow } from "../types";

const PAGE = 50;

/**
 * Página 4 — lo emitido en vivo y cómo le fue.
 *
 * Es donde vive la regla de honestidad: la precisión forward al lado de la del
 * backtest, medida con **las mismas funciones de métrica** (`paper/tracker.py`
 * llama a `models/metrics.py`, igual que el gate). Traducir entre las dos era
 * la forma más fácil de comparar dos cosas distintas creyendo que se comparaba
 * una.
 *
 * Dos advertencias que la página muestra siempre y no como nota al pie:
 *
 * - **El R² contra la media de la propia muestra no es comparable entre
 *   ventanas.** Sobre 288 barras dio +0.240 contra el +0.400 del walk-forward
 *   y parecía degradación; no lo era. El R² se mide contra la dispersión del
 *   target *en esa ventana*, y tres días tienen una fracción de la dispersión
 *   de dos años. El número comparable es el R² contra EWMA, que **mejoró**.
 * - **Los pronósticos se solapan.** Con H=16 dos consecutivos comparten 15 de
 *   16 barras de horizonte. Tratar los aciertos del cono como independientes
 *   daba un IC que excluía el nominal — un falso hallazgo de bajo-cobertura
 *   con apariencia de rigor. El IC de bloques lo da vuelta.
 */
export function HistoryPage() {
  const { symbol, timeframe } = useTarget();
  const [offset, setOffset] = useState(0);
  const [status, setStatus] = useState<string>("");
  const enabled = Boolean(symbol && timeframe);
  const target = { symbol, timeframe };

  const coverage = useQuery({
    queryKey: ["coverage", symbol, timeframe, true],
    queryFn: () => api.coverage({ ...target, baselines: true }),
    enabled,
    staleTime: 60_000,
  });
  const history = useQuery({
    queryKey: ["history", symbol, timeframe, PAGE, offset, status],
    queryFn: () =>
      api.history({ ...target, limit: PAGE, offset, status: status || undefined }),
    enabled,
  });

  if (!enabled) return <Loading what="la configuración del backend" />;

  const report = coverage.data;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-neutral-100">Validación forward 15m</h1>
          <p className="text-xs text-neutral-500">
            {history.data
              ? `${history.data.total} pronósticos · ${Object.entries(
                  history.data.counts,
                )
                  .map(([k, v]) => `${v} ${k}`)
                  .join(" · ")}`
              : "—"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <a
            href={api.csvUrl({ ...target, status: status || undefined })}
            className="rounded border border-neutral-800 px-2 py-1 text-xs text-neutral-300 hover:bg-neutral-800/60"
          >
            exportar CSV
          </a>
          <TargetBar />
        </div>
      </div>

      {coverage.error && <ErrorBox>{String(coverage.error)}</ErrorBox>}

      {report?.mixed_sample && (
        <Warning>
          <strong>Muestra mixta</strong>: conviven {report.model_versions.length}{" "}
          versiones del modelo ({report.model_versions.join(", ")}). La cobertura
          promediada no describe a ninguna de las dos — hay que purgar los
          registros viejos antes de leer estos números como un veredicto.
        </Warning>
      )}

      {/* --- Cobertura forward ------------------------------------------ */}
      {coverage.isLoading && <Loading what="el reporte de cobertura" />}
      {report && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <Card
            title="Muestra forward"
            right={
              <Badge tone={report.n_gap > 0 ? "warn" : "neutral"}>
                {report.n_gap} con hueco
              </Badge>
            }
          >
            <Row label="resueltos" value={count(report.n_resolved)} />
            <Row label="abiertos" value={count(report.n_open)} />
            <Row
              label="bloques independientes"
              value={num(report.n_blocks, 1)}
              hint="n / horizonte: los pronósticos se solapan"
            />
            <Row label="desde (UTC)" value={report.date_from} />
            <Row label="hasta (UTC)" value={report.date_to} />
            <Row
              label="razón realizada/pronosticada"
              value={`${num(report.vol_ratio_mean, 3)} (mediana ${num(
                report.vol_ratio_median,
                3,
              )})`}
              hint=">1 = la sigma salió corta en las colas"
            />
          </Card>

          <Card title="Volatilidad — modelo contra baselines">
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
                <tr className="border-t border-neutral-800">
                  <td className="py-1 text-accent">modelo</td>
                  <td className="text-right text-neutral-300">
                    {num(report.volatility?.rmse, 5)}
                  </td>
                  <td className="text-right text-neutral-300">
                    {num(report.volatility?.r2, 3)}
                  </td>
                  <td className="text-right text-neutral-300">
                    {num(report.volatility?.qlike, 4)}
                  </td>
                </tr>
                {Object.entries(report.baselines).map(([name, m]) => (
                  <tr key={name} className="border-t border-neutral-800">
                    <td className="py-1 text-neutral-400">{name}</td>
                    <td className="text-right text-neutral-300">{num(m.rmse, 5)}</td>
                    <td className="text-right text-neutral-300">{num(m.r2, 3)}</td>
                    <td className="text-right text-neutral-300">{num(m.qlike, 4)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="mt-2 border-t border-neutral-800 pt-2">
              <Row
                label="R² vs EWMA"
                value={num(report.volatility?.r2_vs_baseline, 3)}
                tone="text-accent"
                hint="el número comparable entre ventanas"
              />
              {report.dm_vs_ewma && (
                <Row
                  label="DM vs EWMA"
                  value={`p=${pValue(report.dm_vs_ewma.p_value)}`}
                />
              )}
              {report.dm_vs_har && (
                <Row
                  label="DM vs HAR"
                  value={`p=${pValue(report.dm_vs_har.p_value)}`}
                />
              )}
              <Row
                label="Mincer-Zarnowitz β"
                value={num(report.volatility?.mincer_zarnowitz_beta, 3)}
                hint="1.0 = insesgado y eficiente"
              />
            </div>
            {!report.with_baselines && (
              <p className="mt-2 text-[11px] text-warn">
                Sin baselines: el R² de arriba se lee solo contra la media de
                esta muestra y no es comparable con el del walk-forward.
              </p>
            )}
          </Card>

          <Card title="Cono — cobertura medida">
            {Object.entries(report.cones).map(([alpha, m]) => {
              const ci = report.cone_ci[alpha];
              const dentro =
                ci && m.nominal_coverage >= ci[0] && m.nominal_coverage <= ci[1];
              return (
                <div
                  key={alpha}
                  className="mb-2 rounded border border-neutral-800 bg-neutral-950/60 p-2"
                >
                  <Row
                    label={`nominal ${pct(m.nominal_coverage, 0)}`}
                    value={pct(m.empirical_coverage, 1)}
                  />
                  <Row
                    label="IC95 (bloques)"
                    value={ci ? `[${pct(ci[0], 1)}, ${pct(ci[1], 1)}]` : "—"}
                    tone={dentro ? "text-accent" : "text-warn"}
                  />
                  <Row label="ancho medio" value={num(m.mean_width, 4)} />
                  <Row label="Winkler" value={num(m.winkler_score, 4)} />
                  {ci && (
                    <p className="mt-1 text-[10px] text-neutral-500">
                      {dentro
                        ? "el nominal cae dentro del intervalo: la cobertura es compatible con lo prometido"
                        : "el nominal queda fuera del intervalo"}
                    </p>
                  )}
                </div>
              );
            })}
          </Card>
        </div>
      )}

      {/* --- Tabla ------------------------------------------------------- */}
      <Card
        title="Pronósticos emitidos"
        right={
          <select
            aria-label="estado"
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setOffset(0);
            }}
            className="rounded border border-neutral-800 bg-neutral-900 px-2 py-1 font-mono text-[11px] text-neutral-300"
          >
            <option value="">todos</option>
            <option value="resolved">resueltos</option>
            <option value="open">abiertos</option>
            <option value="gap">con hueco</option>
          </select>
        }
      >
        {history.isLoading ? (
          <Loading what="el historial" />
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[820px] text-xs">
                <thead>
                  <tr className="text-neutral-600">
                    <th className="py-1 text-left font-normal">barra (local)</th>
                    <th className="text-right font-normal">precio</th>
                    <th className="text-right font-normal">σ pronosticada</th>
                    <th className="text-right font-normal">σ realizada</th>
                    <th className="text-right font-normal">razón</th>
                    <th className="text-right font-normal">retorno</th>
                    <th className="text-center font-normal">cono</th>
                    <th className="text-right font-normal">estado</th>
                  </tr>
                </thead>
                <tbody className="font-mono">
                  {(history.data?.forecasts ?? []).map((row) => (
                    <HistoryRow key={row.forecast_id} row={row} />
                  ))}
                </tbody>
              </table>
            </div>

            <div className="mt-3 flex items-center justify-between border-t border-neutral-800 pt-3 text-xs">
              <button
                type="button"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PAGE))}
                className="rounded border border-neutral-800 px-2 py-1 text-neutral-300 disabled:opacity-30"
              >
                ← anteriores
              </button>
              <span className="text-neutral-500">
                {offset + 1}–{offset + (history.data?.forecasts.length ?? 0)} de{" "}
                {history.data?.total ?? 0}
              </span>
              <button
                type="button"
                disabled={
                  offset + PAGE >= (history.data?.total ?? 0) ||
                  (history.data?.forecasts.length ?? 0) < PAGE
                }
                onClick={() => setOffset(offset + PAGE)}
                className="rounded border border-neutral-800 px-2 py-1 text-neutral-300 disabled:opacity-30"
              >
                siguientes →
              </button>
            </div>
          </>
        )}
      </Card>
    </div>
  );
}

function HistoryRow({ row }: { row: ForecastRow }) {
  const hits = Object.entries(row.cone_hits).sort();
  return (
    <tr className="border-t border-neutral-800">
      <td className="py-1 text-neutral-300">{stamp(row.open_time)}</td>
      <td className="text-right text-neutral-400">{price(row.reference_price)}</td>
      <td className="text-right text-neutral-300">{pct(row.sigma_forecast, 3)}</td>
      <td className="text-right text-neutral-300">{pct(row.realized_vol, 3)}</td>
      <td
        className={`text-right ${
          row.vol_ratio == null
            ? "text-neutral-600"
            : row.vol_ratio > 1.5 || row.vol_ratio < 0.66
              ? "text-warn"
              : "text-neutral-300"
        }`}
      >
        {num(row.vol_ratio, 2)}
      </td>
      <td className="text-right text-neutral-400">{pct(row.realized_return, 3)}</td>
      <td className="text-center">
        {hits.length === 0 ? (
          <span className="text-neutral-600">—</span>
        ) : (
          <span className="inline-flex gap-1">
            {hits.map(([alpha, hit]) => (
              <span
                key={alpha}
                title={`nominal ${(1 - Number(alpha)) * 100}%`}
                className={hit ? "text-accent" : "text-danger"}
              >
                {hit ? "●" : "○"}
              </span>
            ))}
          </span>
        )}
      </td>
      <td className="text-right">
        <Badge
          tone={
            row.status === "resolved" ? "ok" : row.status === "gap" ? "warn" : "muted"
          }
        >
          {row.status}
        </Badge>
      </td>
    </tr>
  );
}
