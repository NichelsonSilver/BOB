import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { type ReactNode, useEffect, useMemo, useState } from "react";

import { Badge, Card, ErrorBox, Row, Stat } from "../components/common/Card";
import { RegimeFilter } from "../components/signal/RegimeFilter";
import { useTarget } from "../hooks/useTarget";
import { useLang } from "../i18n";
import { api } from "../lib/api";
import { isNum, multiplier, num, pct, price, stamp } from "../lib/format";
import { tickPrice, useAppStore } from "../stores/appStore";
import type { SizingResponse, StopFilterEvidence } from "../types";

/**
 * Calculadora de riesgo: cuánto comprar dado cuánto se está dispuesto a perder.
 *
 * El orden de los campos es el argumento de la página. Primero va la cuenta y
 * el % que se arriesga, después el stop, y recién al final el leverage. Con
 * 500 USD y sin edge medido (el gate direccional y las reglas técnicas del
 * 05-10 no pasaron), el tamaño por riesgo fijo es lo único que convierte una
 * mala racha en un mal mes en vez de una cuenta liquidada.
 *
 * Toda la aritmética viene del servidor (`/api/risk/sizing`): la fórmula de
 * liquidación vive en un solo lugar. Los avisos también llegan del servidor,
 * en el idioma pedido. Lo único que se guarda acá, en localStorage, son
 * capital y % de riesgo, que son preferencias del usuario.
 */

const PREFS_KEY = "bob.risk.v1";

type Prefs = { capital: string; riskPct: string; leverage: string };

function loadPrefs(): Prefs {
  const fallback = { capital: "500", riskPct: "1", leverage: "5" };
  try {
    const raw = localStorage.getItem(PREFS_KEY);
    return raw ? { ...fallback, ...(JSON.parse(raw) as Partial<Prefs>) } : fallback;
  } catch {
    return fallback;
  }
}

function savePrefs(p: Prefs) {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify(p));
  } catch {
    /* sin storage: la página funciona igual, solo no recuerda */
  }
}

function parse(v: string): number | undefined {
  const n = Number(v.replace(",", "."));
  return Number.isFinite(n) && n > 0 ? n : undefined;
}

/** Espera a que el usuario deje de tipear antes de pedir. */
function useDebounced<T>(value: T, ms = 250): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setV(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return v;
}

const usd = (v: number | null | undefined) => (isNum(v) ? `${v.toFixed(2)} USD` : "—");

export function RiskPage() {
  const { lang, tr } = useLang();
  const { symbol, timeframe } = useTarget();
  const lastTick = useAppStore((s) => s.lastTick);
  const live = tickPrice(lastTick);

  const initial = useMemo(loadPrefs, []);
  const [capital, setCapital] = useState(initial.capital);
  const [riskPct, setRiskPct] = useState(initial.riskPct);
  const [leverage, setLeverage] = useState(initial.leverage);
  // `/risk?entry=&stop=&target=` prellena el trade: sirve para enlazar un
  // setup desde otra página sin tener que retipearlo.
  const url = useMemo(() => new URLSearchParams(window.location.search), []);
  const [entry, setEntry] = useState(url.get("entry") ?? "");
  const [stop, setStop] = useState(url.get("stop") ?? "");
  const [target, setTarget] = useState(url.get("target") ?? "");
  const [maker, setMaker] = useState(false);

  useEffect(() => savePrefs({ capital, riskPct, leverage }), [capital, riskPct, leverage]);

  // Precio de referencia: el tick en vivo, o el cierre de la última vela.
  const lastCandle = useQuery({
    queryKey: ["candles", symbol, timeframe, 2],
    queryFn: () => api.candles({ symbol, timeframe, limit: 2 }),
    enabled: Boolean(symbol && timeframe),
    staleTime: 30_000,
  });
  const last = lastCandle.data?.candles.at(-1);
  const refPrice = live ?? last?.close ?? null;
  // Con el backend apagado la última vela de la DB puede tener semanas: usarla
  // de entrada sin decirlo dimensionaría el trade contra un precio que ya no
  // existe. Se dice de cuándo es, y en ámbar si tiene más de una hora.
  const stale = live === null && last !== undefined && Date.now() - last.open_time > 3_600_000;
  const refLabel =
    live !== null
      ? tr("live price", "precio en curso")
      : last
        ? tr(`close of the ${stamp(last.open_time)} bar`, `cierre de la vela del ${stamp(last.open_time)}`)
        : "";

  useEffect(() => {
    if (entry === "" && refPrice !== null && !stale) setEntry(refPrice.toFixed(2));
  }, [entry, refPrice, stale]);

  // Contexto del analista: sigma del horizonte y estado del filtro. Solo se
  // usa si el pronóstico está fresco; uno viejo describe un mercado que ya
  // pasó, y meterlo en la cuenta sería peor que no tenerlo.
  const latest = useQuery({
    queryKey: ["latest", symbol, timeframe],
    queryFn: () => api.latest({ symbol, timeframe }),
    enabled: Boolean(symbol && timeframe),
    refetchInterval: 60_000,
    retry: false,
  });
  const riskFilter = useQuery({
    queryKey: ["riskfilter"],
    queryFn: () => api.riskFilter(),
    retry: false,
    staleTime: 300_000,
  });
  const stopFilter = useQuery({
    queryKey: ["stopfilter"],
    queryFn: () => api.stopFilter(),
    retry: false,
    staleTime: 300_000,
  });
  const forecast = latest.data?.forecast ?? null;
  const fresh = forecast !== null && !forecast.is_stale;

  const params = useDebounced({
    capital: parse(capital),
    risk_pct: parse(riskPct),
    entry: parse(entry),
    stop: parse(stop),
    leverage: parse(leverage),
    target: parse(target),
    maker_entry: maker,
    sigma_horizon: fresh ? forecast.sigma_forecast : undefined,
    regime_elevated: fresh ? forecast.regime?.elevated : undefined,
  });
  const ready =
    params.capital !== undefined &&
    params.risk_pct !== undefined &&
    params.entry !== undefined &&
    params.stop !== undefined &&
    params.leverage !== undefined &&
    params.stop !== params.entry;

  const sizing = useQuery({
    queryKey: ["sizing", params, lang],
    queryFn: () =>
      api.sizing({
        capital: params.capital!,
        risk_pct: params.risk_pct!,
        entry: params.entry!,
        stop: params.stop!,
        leverage: params.leverage!,
        target: params.target,
        maker_entry: params.maker_entry,
        sigma_horizon: params.sigma_horizon,
        regime_elevated: params.regime_elevated,
        lang,
      }),
    enabled: ready,
    retry: false,
    placeholderData: keepPreviousData,
  });

  /** Atajo: stop a un % de la entrada, del lado que se elija. */
  const setStopAt = (frac: number, side: "long" | "short") => {
    const e = parse(entry);
    if (e === undefined) return;
    setStop((side === "long" ? e * (1 - frac) : e * (1 + frac)).toFixed(2));
  };

  const s = ready ? sizing.data : undefined;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div className="flex items-baseline gap-3">
          <h1 className="text-xl font-semibold text-neutral-100">{tr("Trade risk", "Riesgo por trade")}</h1>
          <span className={`text-xs ${stale ? "text-warn" : "text-neutral-500"}`}>
            {symbol ?? "—"} · {refPrice !== null ? `${price(refPrice)} (${refLabel})` : tr("no price", "sin precio")}
            {stale &&
              tr(
                " — stale: start the backend with the live feed or type the entry",
                " — viejo: levantá el backend con el feed en vivo o tipeá la entrada",
              )}
          </span>
        </div>
        <Badge tone="neutral">{tr("MAREA never places orders", "MAREA nunca ejecuta órdenes")}</Badge>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        {/* --- Entradas ---------------------------------------------------- */}
        <Card title={tr("The trade you are considering", "El trade que estás pensando")}>
          <div className="space-y-3">
            <Field label={tr("Account capital (USD)", "Capital de la cuenta (USD)")} value={capital} onChange={setCapital} />
            <Field
              label={tr("Risk per trade (% of account)", "Riesgo por trade (% de la cuenta)")}
              value={riskPct}
              onChange={setRiskPct}
              hint={tr(
                "1–2% is the prudent range. It is what you lose if the stop is hit, costs included.",
                "1–2% es lo prudente. Es lo que perdés si salta el stop, con costos incluidos.",
              )}
            />
            <Field
              label={tr("Entry", "Entrada")}
              value={entry}
              onChange={setEntry}
              right={
                refPrice !== null &&
                !stale && (
                  <MiniButton onClick={() => setEntry(refPrice.toFixed(2))}>{tr("use current", "usar actual")}</MiniButton>
                )
              }
            />
            <Field
              label="Stop"
              value={stop}
              onChange={setStop}
              hint={tr("Below the entry = long. Above = short.", "Debajo de la entrada = long. Encima = short.")}
            />
            <div className="flex flex-wrap gap-1">
              {[0.005, 0.01, 0.02].map((f) => (
                <span key={f} className="flex gap-1">
                  <MiniButton onClick={() => setStopAt(f, "long")}>long −{f * 100}%</MiniButton>
                  <MiniButton onClick={() => setStopAt(f, "short")}>short +{f * 100}%</MiniButton>
                </span>
              ))}
            </div>
            <Field label={tr("Target (optional)", "Objetivo (opcional)")} value={target} onChange={setTarget} />
            <Field
              label="Leverage"
              value={leverage}
              onChange={setLeverage}
              hint={tr(
                "It does not change how much you lose at the stop: it changes the margin and where liquidation sits.",
                "No cambia cuánto perdés al stop: cambia el margen y dónde queda la liquidación.",
              )}
            />
            <label className="flex items-center gap-2 text-xs text-neutral-400">
              <input type="checkbox" checked={maker} onChange={(e) => setMaker(e.target.checked)} />
              {tr("entry with a limit order (maker)", "entrada con orden límite (maker)")}
            </label>
          </div>
        </Card>

        {/* --- Resultado --------------------------------------------------- */}
        <div className="space-y-4 xl:col-span-2">
          {fresh ? (
            <RegimeFilter
              regime={forecast.regime}
              evidence={riskFilter.data ?? null}
              stopEvidence={stopFilter.data ?? null}
            />
          ) : (
            <Card
              title={tr("Regime filter", "Filtro de régimen")}
              right={<Badge tone="muted">{tr("no state", "sin estado")}</Badge>}
            >
              <p className="text-sm text-neutral-500">
                {tr(
                  `The analyst is not issuing${forecast ? ` (last forecast from ${stamp(forecast.open_time)})` : ""}, so there is no filter state and no sigma to compare the stop with. Start the backend with the live feed: it takes ~80 s to fit and issues on every closed ${timeframe ?? "15m"} bar.`,
                  `El analista no está emitiendo${forecast ? ` (último pronóstico del ${stamp(forecast.open_time)})` : ""}, así que no hay estado del filtro ni sigma para comparar el stop. Levantá el backend con el feed en vivo: tarda ~80 s en ajustar y emite en cada vela cerrada de ${timeframe ?? "15m"}.`,
                )}
              </p>
            </Card>
          )}
          {!ready && (
            <Card>
              <p className="text-sm text-neutral-400">
                {tr(
                  "Fill in the entry and the stop. The size comes from the stop and the % you risk, not from the leverage.",
                  "Completá entrada y stop. El tamaño sale del stop y del % que arriesgás, no del leverage.",
                )}
              </p>
            </Card>
          )}
          {ready && sizing.error && <ErrorBox>{String(sizing.error)}</ErrorBox>}
          {s && (
            <SizingResult
              s={s}
              calibration={stopFilter.data ?? null}
              horizonHours={
                fresh && lastCandle.data ? (forecast.horizon_bars * lastCandle.data.interval_ms) / 3_600_000 : null
              }
            />
          )}
        </div>
      </div>
    </div>
  );
}

function SizingResult({
  s,
  horizonHours,
  calibration,
}: {
  s: SizingResponse;
  horizonHours: number | null;
  calibration: StopFilterEvidence | null;
}) {
  const { tr } = useLang();
  const gap = (k: string) => calibration?.calibration_gap[k];
  const touchHint = calibration
    ? tr(
        `Random-walk formula on the forecast sigma. Measured with a ${pct(calibration.stop_pct, 1)} stop: observed − predicted gap of ${pct(gap("normal"), 1)} in normal and ${pct(gap("elevado"), 1)} in elevated (calibrated). It sizes, it does not predict direction.`,
        `Fórmula de camino aleatorio sobre la sigma pronosticada. Medido con stop a ${pct(calibration.stop_pct, 1)}: brecha observado − predicho ${pct(gap("normal"), 1)} en normal y ${pct(gap("elevado"), 1)} en elevado (calibrada). Dimensiona, no predice dirección.`,
      )
    : tr(
        "Random-walk formula on the forecast sigma; no calibration measurement. It sizes, it does not predict direction.",
        "Fórmula de camino aleatorio sobre la sigma pronosticada; sin medición de calibración. Dimensiona, no predice dirección.",
      );
  const dirTone = s.direction === "long" ? "text-accent" : "text-danger";
  const danger = s.warnings.some((w) => w.level === "danger");
  return (
    <>
      <Card
        title={tr(`${s.direction} order`, `Orden ${s.direction}`)}
        right={
          danger ? (
            <Badge tone="bad">{tr("not tradable like this", "no operable así")}</Badge>
          ) : (
            <Badge tone="ok">{tr("within risk", "dentro del riesgo")}</Badge>
          )
        }
      >
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          <Stat
            label={tr("Quantity", "Cantidad")}
            value={<span className={dirTone}>{num(s.quantity, 3)} ETH</span>}
            sub={tr("rounded down", "redondeada hacia abajo")}
          />
          <Stat label="Notional" value={usd(s.notional)} />
          <Stat label={tr("Margin", "Margen")} value={usd(s.margin)} sub={`${tr("at", "a")} ${multiplier(s.leverage)}`} />
          <Stat
            label={tr("Loss if the stop is hit", "Pérdida si salta el stop")}
            value={<span className="text-danger">−{usd(s.loss_at_stop)}</span>}
            sub={tr(
              `${pct(s.loss_at_stop_fraction)} of the account`,
              `${pct(s.loss_at_stop_fraction)} de la cuenta`,
            )}
          />
        </div>
      </Card>

      {s.warnings.length > 0 && (
        <div className="space-y-2">
          {s.warnings.map((w) => (
            <div
              key={w.code}
              className={`rounded border px-3 py-2 text-sm ${
                w.level === "danger" ? "border-danger/40 bg-danger/5 text-danger" : "border-warn/40 bg-warn/5 text-warn"
              }`}
            >
              {w.message}
            </div>
          ))}
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Card title={tr("Liquidation (isolated margin)", "Liquidación (margen aislado)")}>
          <Row label={tr("liquidation price", "precio de liquidación")} value={price(s.liquidation_price)} />
          <Row label={tr("distance", "distancia")} value={pct(s.liquidation_pct)} />
          <Row label={tr("distance to stop", "distancia al stop")} value={pct(s.stop_pct)} />
          <Row
            label={tr("liquidation / stop", "liquidación / stop")}
            value={`${num(s.liquidation_to_stop, 1)}×`}
            tone={s.liquidation_to_stop < 1 ? "text-danger" : s.liquidation_to_stop < 1.5 ? "text-warn" : "text-accent"}
            hint={tr(
              "Should be ≥ 1.5×: that way a wick past the stop takes you out at the stop and not by liquidation",
              "Debe ser ≥ 1.5×: así una mecha que se pase del stop te saca por el stop y no por liquidación",
            )}
          />
          <Row
            label={tr("max leverage (liquidation ≥ 1.5× stop)", "leverage máx. (liquidación ≥ 1.5× stop)")}
            hint={tr(
              "It is a safety ceiling against liquidation, not a recommendation: the size is already set by the risk",
              "Es un techo de seguridad contra la liquidación, no una recomendación: el tamaño ya está fijado por el riesgo",
            )}
            value={multiplier(Math.floor(s.max_safe_leverage * 10) / 10)}
          />
          <Row
            label={tr("minimum leverage to open it", "leverage mínimo para abrirlo")}
            value={multiplier(Math.ceil(s.required_leverage * 10) / 10)}
            hint={tr("With less, the margin does not fit in the account", "Con menos, el margen no cabe en la cuenta")}
          />
          <p className="mt-2 text-[11px] text-neutral-600">
            {tr(
              "Conservative estimate. Before entering, check it against the one the exchange shows. In cross margin liquidation sits further away, but what you risk becomes the whole account.",
              "Estimación conservadora. Antes de entrar, contrastala con la que muestra el exchange. En cross la liquidación queda más lejos, pero lo que arriesgás pasa a ser la cuenta entera.",
            )}
          </p>
        </Card>

        <Card title={tr("Costs and target", "Costos y objetivo")}>
          <Row label={tr("round-trip cost", "costo ida y vuelta")} value={`${pct(s.cost_pct, 3)} · ${usd(s.cost_usd)}`} />
          <Row
            label={tr("cost / risk", "costo / riesgo")}
            value={pct(s.cost_share_of_risk, 0)}
            tone={s.cost_share_of_risk > 0.3 ? "text-warn" : ""}
            hint={tr(
              "Share of what you risk that the exchange takes even if the trade goes well",
              "Parte de lo que arriesgás que se lleva el exchange aunque el trade salga bien",
            )}
          />
          {s.stop_sigmas !== null && horizonHours !== null && (
            <>
              <Row
                label={tr(`stop in σ (${horizonHours.toFixed(0)} h)`, `stop en σ (${horizonHours.toFixed(0)} h)`)}
                value={`${num(s.stop_sigmas, 2)}σ`}
                tone={s.stop_sigmas < 0.5 ? "text-warn" : ""}
                hint={tr(
                  "Distance to the stop in units of the sigma forecast for the horizon",
                  "Distancia al stop en unidades de la sigma pronosticada para el horizonte",
                )}
              />
              <Row
                label={tr("P(noise hits the stop)", "P(el ruido toca el stop)")}
                value={`≈ ${pct(s.p_touch_stop, 0)}`}
                hint={touchHint}
              />
              {s.p_touch_target !== null && (
                <Row
                  label={tr("P(noise hits the target)", "P(el ruido toca el objetivo)")}
                  value={`≈ ${pct(s.p_touch_target, 0)}`}
                />
              )}
            </>
          )}
          {s.target !== null ? (
            <>
              <Row
                label={tr("gain at target (net)", "ganancia al objetivo (neta)")}
                value={usd(s.gain_at_target)}
                tone="text-accent"
              />
              <Row label={tr("net R", "R neto")} value={`${num(s.reward_risk_net, 2)} R`} />
              <Row
                label={tr("% wins to break even", "% de aciertos para empatar")}
                value={pct(s.breakeven_win_rate, 1)}
                hint={tr(
                  "Below this, repeating this trade loses money",
                  "Por debajo de esto, repetir este trade pierde plata",
                )}
              />
            </>
          ) : (
            <p className="mt-2 text-xs text-neutral-500">
              {tr(
                "Set a target to see the net gain and how often you need to be right to break even.",
                "Poné un objetivo para ver la ganancia neta y cuántas veces tenés que acertar para empatar.",
              )}
            </p>
          )}
        </Card>
      </div>

      <Card title={tr("Losing streaks with this risk", "Rachas de pérdidas con este riesgo")}>
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          {Object.entries(s.losing_streaks).map(([n, dd]) => (
            <Stat
              key={n}
              label={tr(`${n} losses in a row`, `${n} pérdidas seguidas`)}
              value={<span className={dd > 0.25 ? "text-danger" : dd > 0.1 ? "text-warn" : ""}>−{pct(dd, 1)}</span>}
            />
          ))}
        </div>
        <p className="mt-3 text-[11px] text-neutral-500">
          {tr(
            "Streaks of 15 and 20 are not an exaggeration: they are the worst ones measured in the backtest of sweeps and order blocks on ETH (05-10). Those rules lose",
            "Las rachas de 15 y 20 no son exageración: son las peores medidas en el backtest de barridos y order blocks sobre ETH (05-10). Esas reglas pierden",
          )}{" "}
          <TechnicalLoss />{" "}
          {tr(
            "per trade and are indistinguishable from entering at random. This calculation protects the account; it does not say which way to enter.",
            "por trade y no se distinguen de entrar al azar. Este cálculo protege la cuenta; no dice hacia dónde entrar.",
          )}
        </p>
      </Card>
    </>
  );
}

/** Rango de pérdida por trade de las reglas técnicas, leído del artefacto vía el Laboratorio. */
function TechnicalLoss() {
  const { tr } = useLang();
  const q = useQuery({ queryKey: ["lab", "experiments"], queryFn: () => api.labExperiments(), retry: false });
  const tech = q.data?.experiments.find((e) => e.id === "technical");
  const rules = Array.isArray(tech?.figures.rules) ? (tech.figures.rules as { mean_r?: unknown }[]) : [];
  const rs = rules.map((r) => r.mean_r).filter((v): v is number => isNum(v as number));
  if (!rs.length) return <>{tr("what was measured in the Lab", "lo medido en el Laboratorio")}</>;
  return (
    <>
      {tr("between", "entre")} {num(Math.min(...rs), 2)} {tr("and", "y")} {num(Math.max(...rs), 2)} R
    </>
  );
}

function Field({
  label,
  value,
  onChange,
  hint,
  right,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  hint?: string;
  right?: ReactNode;
}) {
  return (
    <label className="block">
      <span className="flex items-baseline justify-between text-xs uppercase tracking-wider text-neutral-500">
        {label}
        {right}
      </span>
      <input
        inputMode="decimal"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="mt-1 w-full rounded border border-neutral-800 bg-neutral-950 px-2 py-1.5 font-mono text-sm text-neutral-100 focus:border-accent/60 focus:outline-none"
      />
      {hint && <span className="mt-0.5 block text-[11px] text-neutral-600">{hint}</span>}
    </label>
  );
}

function MiniButton({ onClick, children }: { onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded border border-neutral-800 px-1.5 py-0.5 font-mono text-[10px] normal-case tracking-normal text-neutral-400 hover:bg-neutral-800/60"
    >
      {children}
    </button>
  );
}
