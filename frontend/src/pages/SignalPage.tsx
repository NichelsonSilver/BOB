import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { Badge, Card, ErrorBox, Loading, Row } from "../components/common/Card";
import { TargetBar } from "../components/layout/TargetBar";
import { AnalystStrip, coverageLabel } from "../components/signal/AnalystStrip";
import { FeedStatus } from "../components/layout/FeedStatus";
import { MarketNarrative } from "../components/signal/MarketNarrative";
import { PriceChart } from "../components/signal/PriceChart";
import { ProjectionPanel } from "../components/signal/ProjectionPanel";
import { RegimeFilter } from "../components/signal/RegimeFilter";
import { SecurityGauge } from "../components/signal/SecurityGauge";
import { SigmaTimeline } from "../components/signal/SigmaTimeline";
import { useTarget } from "../hooks/useTarget";
import { api } from "../lib/api";
import { count, num, pct, price, stamp } from "../lib/format";
import { tickPrice, useAppStore } from "../stores/appStore";

/**
 * Página principal, construida alrededor del KPI 2 por la decisión del
 * 2026-08-25: el producto se apoya en el target de volatilidad, que pasó el
 * gate, y no en el de dirección, que no lo pasó.
 *
 * Orden de lectura, de arriba abajo y de mayor a menor respaldo empírico:
 * niveles y riesgo (validados) → cono con su cobertura medida (validado) →
 * seguimiento de la sigma (validado) → KPI 1 en gris (no validado, abajo).
 * Poner la Seguridad arriba y grande, como pedía el diseño original, sería
 * darle al número menos respaldado el lugar de más autoridad.
 */
export function SignalPage() {
  const { symbol, timeframe, settings } = useTarget();
  const leverage = useAppStore((s) => s.leverage);
  const setLeverage = useAppStore((s) => s.setLeverage);
  const coneAlpha = useAppStore((s) => s.coneAlpha);
  const setConeAlpha = useAppStore((s) => s.setConeAlpha);
  const lastTick = useAppStore((s) => s.lastTick);
  const analysisError = useAppStore((s) => s.analysisError);
  const [direction, setDirection] = useState("long");

  const target = { symbol, timeframe };
  const enabled = Boolean(symbol && timeframe);

  const latest = useQuery({
    queryKey: ["latest", symbol, timeframe],
    queryFn: () => api.latest(target),
    enabled,
  });
  const projection = useQuery({
    queryKey: ["projection", symbol, timeframe, leverage],
    queryFn: () => api.projection({ ...target, leverage }),
    enabled,
    retry: false, // un 409 de horizonte no se arregla reintentando
  });
  const candles = useQuery({
    queryKey: ["candles", symbol, timeframe, 240],
    queryFn: () => api.candles({ ...target, limit: 240 }),
    enabled,
  });
  const gate = useQuery({
    queryKey: ["gate", settings?.runtime.live_features],
    queryFn: () => api.gate(),
    enabled,
    retry: false,
    staleTime: 300_000, // los artefactos no cambian solos
  });
  const riskFilter = useQuery({
    queryKey: ["riskfilter"],
    queryFn: () => api.riskFilter(),
    enabled,
    retry: false,
    staleTime: 300_000, // los artefactos no cambian solos
  });
  const narrative = useQuery({
    queryKey: ["narrative", symbol, latest.data?.forecast?.open_time],
    queryFn: () => api.narrative({ symbol }),
    enabled: enabled && Boolean(latest.data?.forecast),
    retry: false,
  });
  const stopFilter = useQuery({
    queryKey: ["stopfilter"],
    queryFn: () => api.stopFilter(),
    enabled,
    retry: false,
    staleTime: 300_000,
  });
  const recent = useQuery({
    queryKey: ["history", symbol, timeframe, 96],
    queryFn: () => api.history({ ...target, limit: 96 }),
    enabled,
  });
  const coverage = useQuery({
    queryKey: ["coverage", symbol, timeframe, false],
    queryFn: () => api.coverage({ ...target, baselines: false }),
    enabled,
    staleTime: 60_000,
  });

  if (!enabled) return <Loading what="la configuración del backend" />;
  if (latest.isLoading) return <Loading what="el último pronóstico" />;
  if (latest.error) return <ErrorBox>{String(latest.error)}</ErrorBox>;

  const forecast = latest.data?.forecast ?? null;
  const live = tickPrice(lastTick);
  const setup = projection.data?.projections[direction] ?? null;
  const cone =
    forecast?.cones.find((c) => c.alpha.toFixed(2) === coneAlpha) ?? null;

  // El vivo de 15m está congelado desde el 26-09 (CLAUDE.md, decisión MAREA):
  // esta página puede mostrar un pronóstico de hace días. Se dice arriba y con
  // la edad exacta, porque un nivel viejo leído como actual es peor que nada.
  const ageHours = forecast ? (Date.now() - forecast.open_time) / 3_600_000 : null;

  return (
    <div className="space-y-4">
      <div className="space-y-2 rounded border border-neutral-800 bg-neutral-900/60 px-3 py-2">
        <div className="text-xs text-neutral-400">
          <span className="font-semibold text-warn">Módulo congelado.</span> El análisis en vivo de 15m no corre
          desde el 26-09; lo que sigue es el último pronóstico emitido
          {ageHours !== null && ageHours > 1
            ? `, de hace ${ageHours < 48 ? `${Math.round(ageHours)} h` : `${Math.round(ageHours / 24)} días`}`
            : ""}
          . La calculadora de Riesgo no depende de él.
        </div>
        <FeedStatus />
      </div>
      {/* --- Cabecera ---------------------------------------------------- */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-baseline gap-3">
          <h1 className="text-xl font-semibold text-neutral-100">{symbol}</h1>
          <span className="font-mono text-2xl text-accent">
            {live !== null ? price(live) : price(forecast?.reference_price)}
          </span>
          <span className="text-xs text-neutral-500">
            {live !== null ? "precio en curso" : "última barra cerrada"}
          </span>
        </div>
        <TargetBar />
      </div>

      <AnalystStrip
        forecast={forecast}
        analyst={latest.data?.analyst ?? null}
        liveEnabled={latest.data?.live_analyst_enabled ?? false}
      />

      {analysisError && (
        <ErrorBox>El analista reportó: {analysisError}</ErrorBox>
      )}

      {!forecast && (
        <Card>
          <p className="text-sm text-neutral-400">
            Todavía no hay pronósticos de {symbol} {timeframe}. El analista
            necesita ~80s para el ajuste inicial y emite en cada vela cerrada.
          </p>
        </Card>
      )}

      {forecast && (
        <>
          {/* El filtro va PRIMERO porque es lo único accionable: el resto de
              la página describe, esto decide. Ver RegimeFilter.tsx. */}
          {narrative.data && <MarketNarrative data={narrative.data} />}

          <RegimeFilter
            regime={forecast.regime}
            evidence={riskFilter.data ?? null}
            stopEvidence={stopFilter.data ?? null}
            staleSince={forecast.is_stale ? stamp(forecast.open_time) : null}
          />

          {/* --- Chart + proyección ------------------------------------- */}
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
            <Card
              className="xl:col-span-2"
              title="Precio y niveles del setup"
              right={
                <div className="flex items-center gap-2">
                  <DirectionToggle value={direction} onChange={setDirection} />
                  <select
                    aria-label="nivel del cono"
                    value={coneAlpha}
                    onChange={(e) => setConeAlpha(e.target.value)}
                    className="rounded border border-neutral-800 bg-neutral-900 px-2 py-1 font-mono text-[11px] text-neutral-300"
                  >
                    {forecast.cones.map((c) => (
                      <option key={c.alpha} value={c.alpha.toFixed(2)}>
                        cono {Math.round(c.nominal * 100)}%
                      </option>
                    ))}
                  </select>
                </div>
              }
            >
              {candles.isLoading ? (
                <Loading what="las velas" />
              ) : (
                <PriceChart
                  candles={candles.data?.candles ?? []}
                  projection={setup}
                  cone={cone}
                />
              )}
              {(candles.data?.gaps.length ?? 0) > 0 && (
                <p className="mt-2 text-[11px] text-warn">
                  ⚠ {candles.data?.gaps.length} hueco(s) en el rango dibujado: el
                  precio salta sin que haya habido movimiento, y el analista no
                  emite sobre una ventana con barras faltantes.
                </p>
              )}
            </Card>

            <Card title={`Setup ${direction}`}>
              {projection.error ? (
                <ErrorBox>{String(projection.error)}</ErrorBox>
              ) : setup ? (
                <ProjectionPanel
                  projection={setup}
                  leverage={leverage}
                  onLeverage={setLeverage}
                  maxLeverage={
                    settings?.venues[settings.default_venue]?.max_leverage ?? 125
                  }
                />
              ) : (
                <Loading what="la proyección" />
              )}
            </Card>
          </div>

          {/* --- Cono + sigma ------------------------------------------- */}
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
            <Card
              title="Cono de precio"
              right={<Badge tone="ok">validado forward</Badge>}
            >
              <div className="space-y-3">
                {forecast.cones.map((band) => {
                  const key = band.alpha.toFixed(2);
                  const measured = coverage.data?.cones[key];
                  const ci = coverage.data?.cone_ci[key];
                  return (
                    <div
                      key={key}
                      className="rounded border border-neutral-800 bg-neutral-950/60 p-2"
                    >
                      <Row
                        label={`nominal ${Math.round(band.nominal * 100)}%`}
                        value={`${price(band.price_lo)} — ${price(band.price_hi)}`}
                      />
                      <Row
                        label="cobertura medida"
                        value={coverageLabel(measured?.empirical_coverage, ci)}
                        hint="IC por bootstrap de bloques: los pronósticos se solapan y tratarlos como independientes encoge el error estándar"
                      />
                    </div>
                  );
                })}
                <div className="border-t border-neutral-800 pt-2">
                  <Row
                    label="sigma pronosticada"
                    value={pct(forecast.sigma_forecast, 3)}
                  />
                  <Row
                    label="sigma realizada pasada"
                    value={pct(forecast.sigma_backward, 3)}
                  />
                  <Row
                    label="razón"
                    value={num(forecast.sigma_ratio, 3)}
                    hint=">1 = viene más movimiento del habitual"
                  />
                  <Row
                    label="muestra forward"
                    value={`${coverage.data?.n_resolved ?? "—"} resueltos · ${num(
                      coverage.data?.n_blocks,
                      0,
                    )} bloques`}
                  />
                </div>
              </div>
            </Card>

            <Card
              className="xl:col-span-2"
              title="Seguimiento in-live — sigma pronosticada vs realizada"
              right={
                <span className="font-mono text-[11px] text-neutral-600">
                  últimas {recent.data?.forecasts.length ?? 0} barras
                </span>
              }
            >
              {recent.isLoading ? (
                <Loading what="el histórico reciente" />
              ) : (
                <SigmaTimeline rows={recent.data?.forecasts ?? []} />
              )}
            </Card>
          </div>

          {/* --- KPI 1, abajo y en gris --------------------------------- */}
          <Card>
            <SecurityGauge
              probabilities={forecast.probabilities}
              breakeven={Object.fromEntries(
                Object.entries(projection.data?.projections ?? {}).map(
                  ([d, p]) => [d, p.breakeven_probability],
                ),
              )}
              calibrated={forecast.probability_calibrated}
              gate={gate.data}
            />
          </Card>

          <p className="text-center text-[11px] text-neutral-600">
            modelo <span className="font-mono">{forecast.model_version}</span> ·
            entrenado hasta {stamp(forecast.fit_through_ms)} sobre{" "}
            {count(forecast.n_train)} barras · BOB nunca ejecuta
            órdenes
          </p>
        </>
      )}
    </div>
  );
}

function DirectionToggle({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="flex overflow-hidden rounded border border-neutral-800">
      {["long", "short"].map((d) => (
        <button
          key={d}
          type="button"
          onClick={() => onChange(d)}
          className={`px-2.5 py-1 text-[11px] uppercase tracking-wider transition ${
            value === d
              ? d === "long"
                ? "bg-accent/15 text-accent"
                : "bg-danger/15 text-danger"
              : "text-neutral-500 hover:bg-neutral-800/60"
          }`}
        >
          {d}
        </button>
      ))}
    </div>
  );
}
