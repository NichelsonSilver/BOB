import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { Badge } from "../common/Card";
import { BlockMissing, Delta, DetailCard } from "../common/DetailCard";
import { SERIES } from "../../lib/chartTheme";
import { DASH, isNum, num, pct, signedPct } from "../../lib/format";
import { isBlockError } from "../../types";
import type {
  LiquidityReport,
  PanelStatus,
  Panorama,
  StrengthReport,
} from "../../types";
import { Spark } from "./Spark";

/**
 * Tarjetas de lectura simple de MAREA (M6.1). Las usan Inicio y Panorama, así
 * una misma cifra no puede salir distinta en dos páginas.
 *
 * Reglas de texto (`docs/M6_ALCANCE.md` §4.4): nada de σ, AUC ni IC arriba;
 * percentiles dichos como "más alto que el X% de…"; todo lo técnico en el
 * detalle. Ninguna tarjeta sugiere comprar ni vender (regla 2).
 *
 * `chart` agrega el gráfico chico de la portada (M6.2, variante A elegida).
 * Con o sin él, el número y las frases son los mismos: cambia el dibujo, no la
 * cifra.
 */

/** Diferencia bajo la cual la flecha va horizontal. Elección de lectura, no de método. */
const FLAT = 0.05;

function dirOf(d: number): "up" | "down" | "flat" {
  return Math.abs(d) < FLAT ? "flat" : d > 0 ? "up" : "down";
}

/** Último punto con fecha ≤ `asOf − days`. Las series vienen ordenadas. */
function pointBefore<T>(rows: T[], key: keyof T, asOf: string, days: number): T | undefined {
  const cut = new Date(Date.parse(asOf) - days * 86_400_000).toISOString().slice(0, 10);
  for (let i = rows.length - 1; i >= 0; i--) {
    if (String(rows[i][key]) <= cut) return rows[i];
  }
  return undefined;
}

/** Fracción 0–1 → "0–100". */
function score(v: number | null | undefined): string {
  return isNum(v) ? String(Math.round(v * 100)) : DASH;
}


function usdB(v: number | null | undefined, digits = 1): string {
  return isNum(v) ? `${(v / 1e9).toFixed(digits)}` : DASH;
}

function signedUsdB(v: number | null | undefined): string {
  if (!isNum(v)) return DASH;
  const s = (v / 1e9).toFixed(2);
  return v > 0 ? `+${s}` : s;
}

function share(v: number | null | undefined): string {
  return isNum(v) ? `${Math.round(v * 100)}%` : DASH;
}

/**
 * Percentil dicho como "más que el X% de los días": truncado, igual que el de
 * liquidez. Redondear 0.996 a "100%" diría "nunca estuvo más alto" sin ser cierto.
 */
function rank(v: number | null | undefined): string {
  return isNum(v) ? `${Math.floor(v * 100)}%` : DASH;
}

/**
 * Un percentil dicho hacia el lado que corresponde: "más alto que el 84%" o
 * "más bajo que el 83%", no "más alto que el 16%". `p` es la fracción de días
 * estrictamente por debajo, así que el lado de abajo es `1 − p` (incluye los
 * empates, que no favorecen a nadie).
 */
function aboveBelow(p: number | null | undefined, since: string): string {
  if (!isNum(p)) return "";
  return p >= 0.5
    ? `Hoy es más alto que en el ${rank(p)} de los días desde ${since}.`
    : `Hoy es más bajo que en el ${rank(1 - p)} de los días desde ${since}.`;
}

const LIQ_TONE: Record<string, string> = {
  expansivo: "text-accent",
  neutro: "text-neutral-100",
  contractivo: "text-warn",
};

/** La etiqueta técnica dicha en simple (§4.4). Son tercios del percentil. */
const LIQ_WORD: Record<string, string> = {
  expansivo: "Fuerte",
  neutro: "Normal",
  contractivo: "Débil",
};

/** El percentil dicho en palabras, truncado: no se redondea a favor. */
function liqPace(p: number | null): string {
  if (!isNum(p)) return "Sin lectura disponible.";
  return p >= 0.5
    ? `La liquidez global avanza más rápido que en el ${Math.floor(p * 100)}% de las semanas anteriores.`
    : `La liquidez global avanza más lento que en el ${Math.floor((1 - p) * 100)}% de las semanas anteriores.`;
}

function liqDelta(rep: LiquidityReport): ReactNode {
  const now = rep.regime.composite_pct;
  const hist = rep.history.filter((h) => isNum(h.composite_pct));
  const past = pointBefore(hist, "date", rep.as_of, 28);
  if (!past || !isNum(now) || !isNum(past.composite_pct)) return undefined;
  const word = past.label ? LIQ_WORD[past.label].toLowerCase() : DASH;
  return <Delta dir={dirOf(now - past.composite_pct)}>hace 4 semanas: {word}</Delta>;
}


export function LiquidityCard({ rep, chart = false }: { rep: LiquidityReport; chart?: boolean }) {
  const label = rep.regime.label ?? "sin dato";
  const spark = rep.history.filter((h) => isNum(h.composite_pct)).slice(-52);
  return (
    <DetailCard
      title="Liquidez global"
      value={LIQ_WORD[label] ?? "sin dato"}
      tone={LIQ_TONE[label] ?? ""}
      delta={liqDelta(rep)}
      visual={
        chart ? <Spark data={spark} x="date" y="composite_pct" domain={[0, 1]} bands span="último año" /> : undefined
      }
      meaning={liqPace(rep.regime.composite_pct)}
      notMeaning={`Es un estado del momento, no un pronóstico de precios. La lectura suele cambiar en unas ${num(
        rep.regime.persistence.median_run_weeks,
        0,
      )} semanas.`}
      asOf={rep.as_of}
      badge={rep.stale ? <Badge tone="warn">dato viejo</Badge> : undefined}
      detail={<LiquidityDetail rep={rep} />}
    />
  );
}

function LiquidityDetail({ rep }: { rep: LiquidityReport }) {
  const r = rep.regime;
  return (
    <div className="space-y-1">
      <div>
        Etiqueta técnica: {r.label ?? DASH} (fuerte = expansivo, débil = contractivo, por tercios). Percentil del
        compuesto: {num(r.composite_pct)}, contra las semanas anteriores desde {r.available_from ?? DASH}. Desde{" "}
        {r.since ?? DASH} · hace 4 semanas: {r.label_4w_ago ?? DASH} · hace 13: {r.label_13w_ago ?? DASH}.
      </div>
      <div>
        Racha mediana de una etiqueta: {num(r.persistence.median_run_weeks, 0)} semanas. Cinco componentes con
        pesos iguales (Fed neta, M2, dólar, tasa real, NFCI), cambio a 13 semanas.
      </div>
      <Link to="/liquidez" className="text-accent hover:underline">
        ver Liquidez →
      </Link>
    </div>
  );
}

export function RecessionCard({ p, chart = false }: { p: Panorama; chart?: boolean }) {
  const r = p.recession;
  if (isBlockError(r)) return <BlockMissing title="Riesgo de recesión EE.UU." error={r.error} />;
  const h = r.history_12m;
  const past = h.length >= 4 ? h[h.length - 4] : undefined;
  return (
    <DetailCard
      title="Riesgo de recesión EE.UU."
      value={share(r.probability)}
      unit={`a 12 meses (${r.target.slice(0, 7)})`}
      delta={
        past ? (
          <Delta dir={dirOf(r.probability - past.probability)}>hace 3 meses: {share(past.probability)}</Delta>
        ) : undefined
      }
      visual={chart ? <Spark data={h} x="month" y="probability" domain={[0, 1]} span="últimos 12 meses" /> : undefined}
      meaning="Probabilidad de recesión según la curva de tasas, con el modelo que publica la Fed de Nueva York."
      notMeaning={`Ojo: ${r.caveat}.`}
      asOf={r.as_of.slice(0, 7)}
      detail={
        <div className="space-y-1">
          <div>
            Probit de Estrella y Trubin: Φ(α + β · spread), con α = {num(r.model.alpha, 4)} y β ={" "}
            {num(r.model.beta, 4)}. Spread 10a −
            letra 3m: {num(r.spread)} pp. Reproducido desde FRED contra el archivo de la NY Fed (detalle en{" "}
            <span className="font-mono">marea/recession.py</span>).
          </div>
          <div>
            Regla de Sahm (desempleo): {r.sahm ? `${num(r.sahm.value)} (${r.sahm.as_of.slice(0, 7)})` : DASH}.
            Desde 0.50 ha marcado recesión.
          </div>
          <div>{r.evidence}</div>
        </div>
      }
    />
  );
}

export function CycleCard({ p, chart = false }: { p: Panorama; chart?: boolean }) {
  const c = p.btc_cycle;
  if (isBlockError(c)) return <BlockMissing title="Ciclo de BTC" error={c.error} />;
  const past = pointBefore(c.history, "day", c.as_of, 30);
  return (
    <DetailCard
      title="Ciclo de BTC"
      value={score(c.risk_pit)}
      unit="/ 100"
      badge={c.zone ? <Badge>{c.zone}</Badge> : undefined}
      delta={
        past && isNum(past.risk_pit) && isNum(c.risk_pit) ? (
          <Delta dir={dirOf(c.risk_pit - past.risk_pit)}>hace 30 días: {score(past.risk_pit)}</Delta>
        ) : undefined
      }
      visual={chart ? <Spark data={c.history} x="day" y="risk_pit" domain={[0, 1]} span="último año" /> : undefined}
      meaning="Dónde está el precio de BTC dentro de su propia historia: cerca de 0 fue zona baja del ciclo; cerca de 100, euforia."
      notMeaning="No dice hacia dónde va el precio."
      asOf={c.as_of}
      detail={
        <div className="space-y-1">
          <div>
            60% Mayer Multiple (precio / media de 200 días, hoy {num(c.mayer)}) + 40% desviación contra una
            regresión log. Calculado solo con el pasado de cada fecha: {num(c.risk_pit, 3)}.
          </div>
          <div>
            La misma receta mirando el futuro (como la de Outliers) da {num(c.risk_full, 3)}. Correlación entre
            las dos: {num(c.pit_vs_full.correlation)}; difieren en más de 0.20 el {share(c.pit_vs_full.share_far)} de
            los días.
            {c.references.map((ref) => (
              <span key={ref.day}>
                {" "}
                En el {ref.label} ({ref.day}) la versión con futuro marcaba {num(ref.risk_full, 3)} y la honesta{" "}
                {num(ref.risk_pit, 3)}.
              </span>
            ))}
          </div>
        </div>
      }
    />
  );
}

export function BreadthCard({ p }: { p: Panorama }) {
  const b = p.breadth;
  if (isBlockError(b)) return <BlockMissing title="Amplitud cripto" error={b.error} />;
  return (
    <DetailCard
      title="Amplitud cripto"
      value={share(b.above_sma200)}
      unit="sobre su media de 200 días"
      meaning={`De las ${b.n_symbols} criptos con liquidez suficiente, esa fracción está por encima de su tendencia larga.`}
      notMeaning="Mide cuán extendida está la subida, no cuánto va a durar."
      asOf={b.as_of}
      detail={
        <div className="space-y-1">
          <div>Sobre la media de 20 semanas: {share(b.above_sma20w)}.</div>
          <div>
            Universo: perpetuos de Binance con deslistados incluidos y la regla de liquidez pre-registrada de la
            Fase 10.
            {b.references.length > 0 &&
              ` Referencias: ${b.references.map((ref) => `${pct(ref.above_sma200, 1)} en el ${ref.label} (${ref.day})`).join("; ")}.`}
          </div>
        </div>
      }
    />
  );
}

export function AltseasonCard({ p }: { p: Panorama }) {
  const b = p.breadth;
  if (isBlockError(b)) return <BlockMissing title="Temporada de altcoins" error={b.error} />;
  return (
    <DetailCard
      title="Temporada de altcoins"
      value={isNum(b.altseason) ? Math.round(b.altseason * 100) : DASH}
      unit="/ 100"
      badge={<Badge>{b.altseason_label}</Badge>}
      meaning="Cuántas de las 50 altcoins más negociadas le ganaron a BTC en los últimos 90 días. Desde 75 se habla de temporada de alts; bajo 25, de BTC."
      notMeaning="Dice a quién le fue mejor en 90 días, no cuál va a subir."
      asOf={b.as_of}
      detail={
        <div>
          Las 50 se eligen por volumen mediano de 30 días, no por capitalización (su historia no es gratis): proxy
          declarado.
        </div>
      }
    />
  );
}

export function FundingCard({ p }: { p: Panorama }) {
  const f = p.funding;
  if (isBlockError(f)) return <BlockMissing title="Apalancamiento (funding)" error={f.error} />;
  return (
    <DetailCard
      title="Apalancamiento (funding)"
      value={num(f.btc_annual_pct, 1)}
      unit="% al año, BTC"
      meaning={`Lo que pagan, en un año, los que apuestan al alza en BTC con dinero prestado. ${aboveBelow(
        f.btc_rank,
        f.rank_since.slice(0, 4),
      )}`}
      notMeaning="Mide cuánto apalancamiento hay a favor de la subida, no hacia dónde va el precio."
      asOf={f.as_of}
      detail={
        <div className="space-y-1">
          <div>
            ETH: {num(f.eth_annual_pct, 2)}% al año. Diferencia ETH − BTC: {num(f.spread_eth_btc_pct, 2)} pp, por
            encima del {rank(f.spread_rank)} de los días.
          </div>
          <div>
            Funding de Binance: suma de las tasas cobradas en cada día UTC, media de 7 días, por 365 (sin componer).
            Positivo = los largos les pagan a los cortos.
          </div>
        </div>
      }
    />
  );
}

export function OptionsCard({ p }: { p: Panorama }) {
  const o = p.options;
  if (isBlockError(o)) return <BlockMissing title="Opciones de BTC" error={o.error} />;
  return (
    <DetailCard
      title="Opciones de BTC (cobertura)"
      value={num(o.btc.pc_oi, 2)}
      unit="puts por cada call"
      meaning="Contratos de protección a la baja abiertos por cada apuesta al alza, en Deribit."
      notMeaning={`Sin historia todavía (${o.btc.snapshots} fotos desde ${o.btc.first_snapshot.slice(0, 10)}): no se puede decir si es alto o bajo.`}
      asOf={o.btc.captured_at.slice(0, 10)}
      detail={
        <div className="space-y-1">
          <div>
            Vencimientos ≤ 30 días: {num(o.btc.pc_oi_near, 2)}. Por volumen de 24 h: {num(o.btc.pc_volume, 2)}.
            Interés abierto: {usdB(o.btc.oi_usd)} mil millones USD.
          </div>
          <div>
            ETH: {num(o.eth.pc_oi, 2)} puts por call ({usdB(o.eth.oi_usd)} mil millones USD).
          </div>
        </div>
      }
    />
  );
}

export function StablecoinsCard({ p, chart = false }: { p: Panorama; chart?: boolean }) {
  const s = p.stablecoins;
  if (isBlockError(s)) return <BlockMissing title="Dinero listo para cripto" error={s.error} />;
  const ch = s.change_30d_usd;
  return (
    <DetailCard
      title="Dinero listo para cripto"
      value={usdB(s.supply_usd)}
      unit="mil millones USD en stablecoins"
      delta={
        isNum(ch) ? (
          <Delta dir={ch > 0 ? "up" : ch < 0 ? "down" : "flat"}>
            {signedUsdB(ch)} mil millones en 30 días ({signedPct(s.change_30d_pct, 1)})
          </Delta>
        ) : undefined
      }
      visual={chart ? <Spark data={s.history} x="day" y="supply_usd" span="último año" /> : undefined}
      meaning="Dólares digitales en circulación: dinero que ya está dentro de cripto, disponible para comprar."
      notMeaning="Que entre dinero no garantiza que suba el precio."
      asOf={s.as_of}
      detail={
        <div className="space-y-1">
          <div>Emisión neta, media de 7 días: {signedUsdB(s.net_mints_7d_usd)} mil millones por día.</div>
          <div>
            SSR (capitalización de BTC / stablecoins): {num(s.ssr)}; por encima del {rank(s.ssr_rank_since_2020)} de los
            días desde 2020. Bajo = mucho dinero disponible en relación al tamaño de BTC.
          </div>
          {chart && <div>El gráfico chico no parte de cero: muestra la forma del año, no el tamaño del cambio.</div>}
          <div>{s.evidence}</div>
        </div>
      }
    />
  );
}

/**
 * A qué clase de activo le fue mejor y peor en el último mes: retorno de la
 * clase menos el del ETF típico. Descriptivo; la M3 midió que el orden no
 * predice, y la tarjeta lo dice.
 */
export function StrengthCard({ rep, chart = false }: { rep: StrengthReport; chart?: boolean }) {
  const rows = rep.classes
    .filter((c) => isNum(c.rel_4s))
    .sort((a, b) => (b.rel_4s as number) - (a.rel_4s as number));
  if (rows.length < 4) return <BlockMissing title="Clases de activo" error="sin fuerza relativa a 4 semanas" />;
  const top = rows.slice(0, 2);
  const bottom = rows.slice(-2).reverse();
  const fmt = (c: (typeof rows)[number]) => `${c.label} ${signedPct(c.rel_4s, 1)}`;
  const max = Math.max(...rows.map((c) => Math.abs(c.rel_4s as number))) || 1;
  return (
    <DetailCard
      title="Clases de activo · último mes"
      value={top[0].label}
      unit="le fue mejor"
      badge={rep.stale ? <Badge tone="warn">dato viejo</Badge> : <Badge tone="muted">descriptivo</Badge>}
      visual={
        chart ? (
          <div className="space-y-1">
            {rows.map((c) => {
              const v = c.rel_4s as number;
              const w = `${(Math.abs(v) / max) * 50}%`;
              return (
                <div key={c.block} className="grid grid-cols-[minmax(0,7.5rem)_minmax(3rem,1fr)_3rem] items-center gap-2 text-[11px]">
                  <span className="truncate text-neutral-400">{c.label}</span>
                  <div className="relative h-2.5">
                    <div className="absolute inset-y-0 left-1/2 w-px bg-neutral-700" />
                    <div
                      className="absolute inset-y-0 rounded-sm"
                      style={{ background: SERIES[0], width: w, left: v >= 0 ? "50%" : `calc(50% - ${w})` }}
                    />
                  </div>
                  <span className="text-right font-mono text-neutral-300">{signedPct(v, 1)}</span>
                </div>
              );
            })}
          </div>
        ) : undefined
      }
      meaning={`Arriba: ${top.map(fmt).join(" y ")}. Abajo: ${bottom.map(fmt).join(" y ")}. Comparado con el ETF típico.`}
      notMeaning="Dice a quién le fue mejor, no a quién le va a ir mejor: se midió si este orden anticipa las semanas siguientes y no se encontró que lo haga."
      asOf={rep.as_of}
      detail={
        <div className="space-y-1">
          <div>
            Retorno total de cada clase en 20 sesiones menos la mediana de los {rep.etfs.length} ETFs. Una clase es
            el promedio simple de sus ETFs; el dólar es solo UUP. Cripto no entra: tiene su propia sección.
          </div>
          <div>
            A 13 y 26 semanas:{" "}
            {rows.map((c) => `${c.label} ${signedPct(c.rel_13s, 1)} / ${signedPct(c.rel_26s, 1)}`).join(" · ")}.
          </div>
          <div>
            No es flujo de dinero: las creaciones y rescates de ETFs no están en ninguna fuente gratuita. El orden no
            predice: se midió y no pasó (ver Laboratorio).
          </div>
          <Link to="/flujos" className="text-accent hover:underline">
            ver Flujos →
          </Link>
        </div>
      }
    />
  );
}

export function PanelCard({ st }: { st: PanelStatus }) {
  const done = Math.min(st.valid / st.interim_valid_days, 1);
  return (
    <DetailCard
      title="Estudio del panel cripto"
      value={`${st.valid} / ${st.interim_valid_days}`}
      unit="días válidos hasta la primera revisión"
      visual={
        <div className="h-2 overflow-hidden rounded bg-neutral-800" title={`${st.valid} de ${st.interim_valid_days}`}>
          <div className="h-full rounded" style={{ width: `${done * 100}%`, background: SERIES[0] }} />
        </div>
      }
      meaning={`Validación en curso: el modelo del panel diario se está probando con datos que no existían cuando se entrenó. Al ritmo actual (cuenta el ${rank(st.valid_rate)} de los días), la primera revisión llega el ${st.eta_interim_at_current_rate ?? DASH}.`}
      notMeaning="El avance no dice si el modelo funciona: el resultado no se mira antes de esa revisión."
      asOf={st.last_decision}
      detail={
        <div className="space-y-1">
          <div>
            {st.decision_dates} días de decisión desde {st.first_decision}: {st.valid} válidos,{" "}
            {st.backfill.length} tarde (backfill), {st.never_emitted.length} sin emitir.
          </div>
          <div>
            Corte final a {st.cutoff_valid_days} días válidos: {st.eta_at_current_rate ?? DASH} al ritmo actual. El
            resultado (IC) no se muestra antes de la revisión intermedia (Enmienda 1).
          </div>
          <Link to="/cripto/panel" className="text-accent hover:underline">
            ver Panel diario →
          </Link>
        </div>
      }
    />
  );
}
