import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { type ReactNode, useEffect, useMemo, useState } from "react";

import { Badge, Card, ErrorBox, Row, Stat } from "../components/common/Card";
import { useTarget } from "../hooks/useTarget";
import { api } from "../lib/api";
import { isNum, multiplier, num, pct, price, stamp } from "../lib/format";
import { tickPrice, useAppStore } from "../stores/appStore";
import type { SizingResponse } from "../types";

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
 * liquidación vive en un solo lugar. Lo único que se guarda acá, en
 * localStorage, son capital y % de riesgo, que son preferencias del usuario.
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
    live !== null ? "precio en curso" : last ? `cierre de la vela del ${stamp(last.open_time)}` : "";

  useEffect(() => {
    if (entry === "" && refPrice !== null && !stale) setEntry(refPrice.toFixed(2));
  }, [entry, refPrice, stale]);

  const params = useDebounced({
    capital: parse(capital),
    risk_pct: parse(riskPct),
    entry: parse(entry),
    stop: parse(stop),
    leverage: parse(leverage),
    target: parse(target),
    maker_entry: maker,
  });
  const ready =
    params.capital !== undefined &&
    params.risk_pct !== undefined &&
    params.entry !== undefined &&
    params.stop !== undefined &&
    params.leverage !== undefined &&
    params.stop !== params.entry;

  const sizing = useQuery({
    queryKey: ["sizing", params],
    queryFn: () =>
      api.sizing({
        capital: params.capital!,
        risk_pct: params.risk_pct!,
        entry: params.entry!,
        stop: params.stop!,
        leverage: params.leverage!,
        target: params.target,
        maker_entry: params.maker_entry,
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
          <h1 className="text-xl font-semibold text-neutral-100">Riesgo por trade</h1>
          <span className={`text-xs ${stale ? "text-warn" : "text-neutral-500"}`}>
            {symbol ?? "—"} · {refPrice !== null ? `${price(refPrice)} (${refLabel})` : "sin precio"}
            {stale && " — viejo: levantá el backend con el feed en vivo o tipeá la entrada"}
          </span>
        </div>
        <Badge tone="neutral">BOB nunca ejecuta órdenes</Badge>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        {/* --- Entradas ---------------------------------------------------- */}
        <Card title="El trade que estás pensando">
          <div className="space-y-3">
            <Field label="Capital de la cuenta (USD)" value={capital} onChange={setCapital} />
            <Field
              label="Riesgo por trade (% de la cuenta)"
              value={riskPct}
              onChange={setRiskPct}
              hint="1–2% es lo prudente. Es lo que perdés si salta el stop, con costos incluidos."
            />
            <Field
              label="Entrada"
              value={entry}
              onChange={setEntry}
              right={
                refPrice !== null &&
                !stale && (
                  <MiniButton onClick={() => setEntry(refPrice.toFixed(2))}>usar actual</MiniButton>
                )
              }
            />
            <Field
              label="Stop"
              value={stop}
              onChange={setStop}
              hint="Debajo de la entrada = long. Encima = short."
            />
            <div className="flex flex-wrap gap-1">
              {[0.005, 0.01, 0.02].map((f) => (
                <span key={f} className="flex gap-1">
                  <MiniButton onClick={() => setStopAt(f, "long")}>long −{f * 100}%</MiniButton>
                  <MiniButton onClick={() => setStopAt(f, "short")}>short +{f * 100}%</MiniButton>
                </span>
              ))}
            </div>
            <Field label="Objetivo (opcional)" value={target} onChange={setTarget} />
            <Field
              label="Leverage"
              value={leverage}
              onChange={setLeverage}
              hint="No cambia cuánto perdés al stop: cambia el margen y dónde queda la liquidación."
            />
            <label className="flex items-center gap-2 text-xs text-neutral-400">
              <input type="checkbox" checked={maker} onChange={(e) => setMaker(e.target.checked)} />
              entrada con orden límite (maker)
            </label>
          </div>
        </Card>

        {/* --- Resultado --------------------------------------------------- */}
        <div className="space-y-4 xl:col-span-2">
          {!ready && (
            <Card>
              <p className="text-sm text-neutral-400">
                Completá entrada y stop. El tamaño sale del stop y del % que arriesgás, no del
                leverage.
              </p>
            </Card>
          )}
          {ready && sizing.error && <ErrorBox>{String(sizing.error)}</ErrorBox>}
          {s && <SizingResult s={s} />}
        </div>
      </div>
    </div>
  );
}

function SizingResult({ s }: { s: SizingResponse }) {
  const dirTone = s.direction === "long" ? "text-accent" : "text-danger";
  const danger = s.warnings.some((w) => w.level === "danger");
  return (
    <>
      <Card
        title={`Orden ${s.direction}`}
        right={danger ? <Badge tone="bad">no operable así</Badge> : <Badge tone="ok">dentro del riesgo</Badge>}
      >
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          <Stat
            label="Cantidad"
            value={<span className={dirTone}>{num(s.quantity, 3)} ETH</span>}
            sub="redondeada hacia abajo"
          />
          <Stat label="Notional" value={usd(s.notional)} />
          <Stat label="Margen" value={usd(s.margin)} sub={`a ${multiplier(s.leverage)}`} />
          <Stat
            label="Pérdida si salta el stop"
            value={<span className="text-danger">−{usd(s.loss_at_stop)}</span>}
            sub={`${pct(s.loss_at_stop_fraction)} de la cuenta`}
          />
        </div>
      </Card>

      {s.warnings.length > 0 && (
        <div className="space-y-2">
          {s.warnings.map((w) => (
            <div
              key={w.code}
              className={`rounded border px-3 py-2 text-sm ${
                w.level === "danger"
                  ? "border-danger/40 bg-danger/5 text-danger"
                  : "border-warn/40 bg-warn/5 text-warn"
              }`}
            >
              {w.message}
            </div>
          ))}
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Card title="Liquidación (margen aislado)">
          <Row label="precio de liquidación" value={price(s.liquidation_price)} />
          <Row label="distancia" value={pct(s.liquidation_pct)} />
          <Row
            label="distancia al stop"
            value={pct(s.stop_pct)}
          />
          <Row
            label="liquidación / stop"
            value={`${num(s.liquidation_to_stop, 1)}×`}
            tone={s.liquidation_to_stop < 1 ? "text-danger" : s.liquidation_to_stop < 1.5 ? "text-warn" : "text-accent"}
            hint="Debe ser ≥ 1,5×: así una mecha que se pase del stop te saca por el stop y no por liquidación"
          />
          <Row
            label="leverage máx. (liquidación ≥ 1,5× stop)"
            hint="Es un techo de seguridad contra la liquidación, no una recomendación: el tamaño ya está fijado por el riesgo"
            value={multiplier(Math.floor(s.max_safe_leverage * 10) / 10)}
          />
          <Row
            label="leverage mínimo para abrirlo"
            value={multiplier(Math.ceil(s.required_leverage * 10) / 10)}
            hint="Con menos, el margen no cabe en la cuenta"
          />
          <p className="mt-2 text-[11px] text-neutral-600">
            Estimación conservadora. Antes de entrar, contrastala con la que muestra el exchange.
            En cross la liquidación queda más lejos, pero lo que arriesgás pasa a ser la cuenta
            entera.
          </p>
        </Card>

        <Card title="Costos y objetivo">
          <Row label="costo ida y vuelta" value={`${pct(s.cost_pct, 3)} · ${usd(s.cost_usd)}`} />
          <Row
            label="costo / riesgo"
            value={pct(s.cost_share_of_risk, 0)}
            tone={s.cost_share_of_risk > 0.3 ? "text-warn" : ""}
            hint="Parte de lo que arriesgás que se lleva el exchange aunque el trade salga bien"
          />
          {s.target !== null ? (
            <>
              <Row label="ganancia al objetivo (neta)" value={usd(s.gain_at_target)} tone="text-accent" />
              <Row label="R neto" value={`${num(s.reward_risk_net, 2)} R`} />
              <Row
                label="% de aciertos para empatar"
                value={pct(s.breakeven_win_rate, 1)}
                hint="Por debajo de esto, repetir este trade pierde plata"
              />
            </>
          ) : (
            <p className="mt-2 text-xs text-neutral-500">
              Poné un objetivo para ver la ganancia neta y cuántas veces tenés que acertar para
              empatar.
            </p>
          )}
        </Card>
      </div>

      <Card title="Rachas de pérdidas con este riesgo">
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          {Object.entries(s.losing_streaks).map(([n, dd]) => (
            <Stat
              key={n}
              label={`${n} pérdidas seguidas`}
              value={<span className={dd > 0.25 ? "text-danger" : dd > 0.1 ? "text-warn" : ""}>−{pct(dd, 1)}</span>}
            />
          ))}
        </div>
        <p className="mt-3 text-[11px] text-neutral-500">
          Las rachas de 15 y 20 no son exageración: son las peores medidas en el backtest de
          barridos y order blocks sobre ETH (05-10). Esas reglas pierden −0,15 a −0,35 R por
          trade y no se distinguen de entrar al azar. Este cálculo protege la cuenta; no
          dice hacia dónde entrar.
        </p>
      </Card>
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
