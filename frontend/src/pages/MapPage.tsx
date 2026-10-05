import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { Badge, Card, ErrorBox, Loading, Row } from "../components/common/Card";
import { RsiChart, TechnicalChart } from "../components/map/TechnicalChart";
import { useTarget } from "../hooks/useTarget";
import { api } from "../lib/api";
import { SERIES } from "../lib/chartTheme";
import { num, pct, price, signedPct, stamp } from "../lib/format";
import type { RuleEvidence, TechnicalResponse, TechnicalTf } from "../types";

/**
 * Mapa técnico: dónde está la liquidez, qué order blocks siguen vivos, qué
 * soportes y resistencias tiene cerca el precio.
 *
 * **Es un mapa, no una señal**, y la página lo dice antes de dibujar nada.
 * Las dos reglas que se apoyan en estas zonas (barrido de liquidez y retorno
 * al order block) se midieron con criterio pre-registrado y no pasaron
 * (`docs/PREREGISTRO_TECNICO.md`). El §7 de ese documento decide cómo se
 * muestra: las zonas se dibujan igual, porque son el mapa que el usuario ya
 * mira, pero con la expectancy medida arriba y sin flechas de entrada.
 *
 * Lo que sí sirve de las zonas es **dónde poner el stop**: detrás de un nivel
 * y no en medio de la nada. Por eso cada nivel lleva un enlace a la
 * calculadora de riesgo con ese stop ya puesto.
 */

const TFS: TechnicalTf[] = ["15m", "1h", "4h"];
const TF_MS: Record<TechnicalTf, number> = { "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000 };
const RULE_NAME: Record<RuleEvidence["rule"], string> = {
  sweep: "Barrido de liquidez",
  order_block: "Retorno a order block",
};

export function MapPage() {
  const { symbol } = useTarget();
  const [tf, setTf] = useState<TechnicalTf>("15m");

  const map = useQuery({
    queryKey: ["technical", symbol, tf],
    queryFn: () => api.technical({ symbol, tf }),
    enabled: Boolean(symbol),
    refetchInterval: 60_000,
    retry: false,
  });

  const d = map.data;
  // La última vela CERRADA de 4h puede tener hasta 8 h sin ser vieja.
  const stale = d ? Date.now() - d.last_time > 2 * TF_MS[tf] + 3_600_000 : false;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div className="flex items-baseline gap-3">
          <h1 className="text-xl font-semibold text-neutral-100">Mapa técnico</h1>
          {d && (
            <span className={`text-xs ${stale ? "text-warn" : "text-neutral-500"}`}>
              {d.symbol} · {price(d.last_close)} · vela cerrada del {stamp(d.last_time)}
              {stale && " — vieja: levantá el backend con el feed en vivo"}
            </span>
          )}
        </div>
        <div className="flex overflow-hidden rounded border border-neutral-800">
          {TFS.map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setTf(t)}
              className={`px-3 py-1 font-mono text-xs ${
                tf === t ? "bg-neutral-800 text-accent" : "text-neutral-500 hover:bg-neutral-800/60"
              }`}
            >
              {t}
            </button>
          ))}
        </div>
      </div>

      {map.isLoading && <Loading what="el mapa" />}
      {map.error && <ErrorBox>{String(map.error)}</ErrorBox>}

      {d && (
        <>
          <Evidence data={d} />

          <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
            <Card
              className="xl:col-span-2"
              title={`${d.symbol} ${tf} · últimas ${d.series.time.length} velas`}
              right={<Legend />}
            >
              <TechnicalChart data={d} />
              <div className="mt-2 border-t border-neutral-800 pt-2">
                <div className="mb-1 text-[11px] uppercase tracking-wider text-neutral-500">
                  RSI 14 · {num(d.rsi, 1)}
                </div>
                <RsiChart data={d} />
              </div>
              {d.gaps_15m > 0 && (
                <p className="mt-2 text-[11px] text-warn">
                  ⚠ {d.gaps_15m} hueco(s) en las velas de 15m usadas: las zonas cercanas a un hueco
                  describen una ventana que no existió entera.
                </p>
              )}
            </Card>

            <div className="space-y-4">
              <Context data={d} />
              <Levels data={d} />
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function Evidence({ data }: { data: TechnicalResponse }) {
  const ev = data.evidence;
  if (!ev) {
    return (
      <Card>
        <p className="text-sm text-warn">
          No hay medición de las reglas técnicas en artifacts/. Sin ella, este mapa no tiene
          respaldo que mostrar.
        </p>
      </Card>
    );
  }
  return (
    <Card
      title="Lo que midió el backtest de estas zonas"
      right={<Badge tone="muted">mapa, no señal</Badge>}
    >
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        {ev.rules.map((r) => (
          <div key={r.rule} className="rounded border border-neutral-800 bg-neutral-950/60 p-3">
            <div className="flex items-baseline justify-between gap-2">
              <span className="text-sm text-neutral-200">{RULE_NAME[r.rule]}</span>
              <Badge tone={r.verdict === "PASA" ? "ok" : r.kind === "exploratoria" ? "muted" : "bad"}>
                {r.verdict === "EXPLORATORIA" ? "exploratoria" : r.verdict.toLowerCase()}
              </Badge>
            </div>
            <Row
              label="ganancia media por trade"
              value={`${signedR(r.mean_r)}  [${signedR(r.ci[0])}, ${signedR(r.ci[1])}]`}
              tone={r.mean_r < 0 ? "text-danger" : "text-accent"}
              hint={`IC ${pct(r.ci_level, 2)} por bloques. En R: −1 es perder lo arriesgado`}
            />
            <Row
              label="aciertos"
              value={`${pct(r.win_rate, 1)} · empate sin costos ${pct(r.breakeven_win_rate, 1)}`}
            />
            {r.placebo_mean_r !== null && (
              <Row
                label="mismo trade, momento al azar"
                value={signedR(r.placebo_mean_r)}
                hint="Placebo: si la regla no supera esto, la zona no aporta información"
              />
            )}
            <Row label="trades medidos · peor racha" value={`${r.n} · ${r.worst_streak} seguidas`} />
          </div>
        ))}
      </div>
      <p className="mt-3 text-[11px] text-neutral-500">
        ETHUSDT, ~743 días, objetivo {ev.r_multiple}R, salida a {ev.horizon} barras, costo{" "}
        {pct(ev.cost_pct, 2)} ida y vuelta. Entrar en estas zonas no mostró ventaja sobre entrar al
        azar. Las zonas sirven para ubicar el stop detrás de un nivel, no para decidir la
        dirección. Pre-registro: <span className="font-mono">{ev.preregistration}</span>.
      </p>
    </Card>
  );
}

function Context({ data }: { data: TechnicalResponse }) {
  const vs = (ema: number) => (data.last_close - ema) / ema;
  return (
    <Card title="Contexto" right={<Badge tone="muted">descriptivo</Badge>}>
      <Row
        label="RSI 14"
        value={num(data.rsi, 1)}
        tone={data.rsi >= 70 || data.rsi <= 30 ? "text-warn" : ""}
      />
      {(["20", "50", "200"] as const).map((k, i) => (
        <Row
          key={k}
          label={
            <span>
              <span style={{ color: SERIES[i] }}>━</span> precio vs EMA{k}
            </span>
          }
          value={`${signedPct(vs(data.emas[k]))} · ${price(data.emas[k])}`}
        />
      ))}
      <Row label="ATR 14" value={`${price(data.atr)} · ${pct(data.atr_pct)}`} />
      <p className="mt-2 text-[11px] text-neutral-600">
        RSI y medias ya entraron al gate de la Fase 4 como features y no anticipan dirección (AUC
        0,52). Describen dónde está el precio, no hacia dónde va.
      </p>
    </Card>
  );
}

type LevelRow = { price: number; label: string; detail: string };

function Levels({ data }: { data: TechnicalResponse }) {
  const buffer = 0.1 * data.atr; // el mismo margen del backtest
  const rows: LevelRow[] = [
    ...data.pools.map((p) => ({
      price: p.price,
      label: `liquidez ${p.side === "high" ? "↑" : "↓"}`,
      detail: p.equal >= 2 ? `${p.equal} pivotes iguales` : "pivote no tomado",
    })),
    ...data.sr.map((z) => ({
      price: z.price,
      label: z.kind === "support" ? "soporte" : "resistencia",
      detail: `${z.touches} toque${z.touches === 1 ? "" : "s"}`,
    })),
    ...data.order_blocks.flatMap((ob) => [
      {
        price: ob.direction === "long" ? ob.bottom : ob.top,
        label: `OB ${ob.direction === "long" ? "demanda" : "oferta"}`,
        detail: `${price(ob.bottom)}–${price(ob.top)} · vence en ${ob.expires_in_bars} velas`,
      },
    ]),
  ].sort((a, b) => b.price - a.price);

  const above = rows.filter((r) => r.price > data.last_close);
  const below = rows.filter((r) => r.price <= data.last_close);

  const link = (r: LevelRow) => {
    // Stop detrás del nivel: debajo de uno inferior (long), encima de uno superior (short).
    const stop = r.price > data.last_close ? r.price + buffer : r.price - buffer;
    return `/risk?entry=${data.last_close.toFixed(2)}&stop=${stop.toFixed(2)}`;
  };

  const line = (r: LevelRow) => (
    <div
      key={`${r.label}-${r.price}`}
      className="flex items-baseline justify-between gap-2 border-b border-neutral-900 py-1 text-xs"
    >
      <span className="font-mono text-neutral-200">{price(r.price)}</span>
      <span className="flex-1 truncate text-neutral-400" title={r.detail}>
        {r.label} <span className="text-neutral-600">· {r.detail}</span>
      </span>
      <span className="font-mono text-neutral-500">
        {signedPct((r.price - data.last_close) / data.last_close)}
      </span>
      <Link
        to={link(r)}
        className="rounded border border-neutral-800 px-1.5 py-0.5 font-mono text-[10px] text-neutral-400 hover:bg-neutral-800/60"
        title="Abrir la calculadora de riesgo con el stop detrás de este nivel"
      >
        stop aquí
      </Link>
    </div>
  );

  return (
    <Card title="Niveles cercanos">
      <div>{above.map(line)}</div>
      <div className="my-1 flex items-center gap-2 text-[11px] text-accent">
        <span className="h-px flex-1 bg-accent/40" />
        {price(data.last_close)}
        <span className="h-px flex-1 bg-accent/40" />
      </div>
      <div>{below.map(line)}</div>
      <p className="mt-2 text-[11px] text-neutral-600">
        "Stop aquí" abre la calculadora con la entrada en el último cierre y el stop 0,1·ATR detrás
        del nivel: debajo para un long, encima para un short.
      </p>
    </Card>
  );
}

function Legend() {
  return (
    <div className="flex flex-wrap items-center gap-3 font-mono text-[10px] text-neutral-500">
      {(["20", "50", "200"] as const).map((k, i) => (
        <span key={k}>
          <span style={{ color: SERIES[i] }}>━</span> EMA{k}
        </span>
      ))}
      <span>┈ liquidez</span>
      <span>● barrido</span>
      <span>─ S/R</span>
      <span>
        <span className="text-accent/70">═</span>/<span className="text-danger/70">═</span> OB
      </span>
    </div>
  );
}

function signedR(v: number): string {
  return `${v > 0 ? "+" : ""}${v.toFixed(2)} R`;
}
