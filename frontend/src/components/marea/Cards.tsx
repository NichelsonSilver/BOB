import { Link } from "react-router-dom";

import { Badge } from "../common/Card";
import { BlockMissing, DetailCard } from "../common/DetailCard";
import { DASH, isNum, num, pct } from "../../lib/format";
import { isBlockError } from "../../types";
import type {
  LiquidityReport,
  PanelStatus,
  Panorama,
} from "../../types";

/**
 * Tarjetas de lectura simple de MAREA (M6.1). Las usan Inicio y Panorama, así
 * una misma cifra no puede salir distinta en dos páginas.
 *
 * Reglas de texto (`docs/M6_ALCANCE.md` §4.4): nada de σ, AUC ni IC arriba;
 * percentiles dichos como "más alto que el X% de…"; todo lo técnico en el
 * detalle. Ninguna tarjeta sugiere comprar ni vender (regla 2).
 */

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

const LIQ_TONE: Record<string, string> = {
  expansivo: "text-accent",
  neutro: "text-neutral-100",
  contractivo: "text-warn",
};

const LIQ_TEXT: Record<string, string> = {
  expansivo: "La liquidez global crece más que en la mayoría de las semanas de su historia.",
  neutro: "La liquidez global se mueve dentro de lo habitual.",
  contractivo: "La liquidez global crece menos (o se achica) que en la mayoría de las semanas de su historia.",
};

export function LiquidityCard({ rep }: { rep: LiquidityReport }) {
  const r = rep.regime;
  const label = r.label ?? "sin dato";
  return (
    <DetailCard
      title="Liquidez global"
      value={label}
      tone={LIQ_TONE[label] ?? ""}
      meaning={LIQ_TEXT[label] ?? "Sin lectura disponible."}
      notMeaning="Es un estado del momento, no un pronóstico de precios. Una etiqueta dura unas 3 semanas en promedio."
      asOf={rep.as_of}
      badge={rep.stale ? <Badge tone="warn">dato viejo</Badge> : undefined}
      detail={
        <div className="space-y-1">
          <div>
            percentil del compuesto: {num(r.composite_pct)} · desde {r.since ?? DASH} · hace 4 semanas:{" "}
            {r.label_4w_ago ?? DASH} · hace 13: {r.label_13w_ago ?? DASH}
          </div>
          <div>
            racha mediana de una etiqueta: {num(r.persistence.median_run_weeks, 0)} semanas. Cinco
            componentes con pesos iguales (Fed neta, M2, dólar, tasa real, NFCI).
          </div>
          <Link to="/liquidez" className="text-accent hover:underline">
            ver Liquidez →
          </Link>
        </div>
      }
    />
  );
}

export function RecessionCard({ p }: { p: Panorama }) {
  const r = p.recession;
  if (isBlockError(r)) return <BlockMissing title="Riesgo de recesión EE.UU." error={r.error} />;
  return (
    <DetailCard
      title="Riesgo de recesión EE.UU."
      value={share(r.probability)}
      unit={`a 12 meses (${r.target.slice(0, 7)})`}
      meaning="Probabilidad de recesión según la curva de tasas, con el modelo que publica la Fed de Nueva York."
      notMeaning={`Ojo: ${r.caveat}.`}
      asOf={r.as_of.slice(0, 7)}
      detail={
        <div className="space-y-1">
          <div>
            Probit de Estrella y Trubin: Φ(−0.5333 − 0.6330 · spread). Spread 10a − letra 3m:{" "}
            {num(r.spread)} pp. Reproducido desde FRED: 760 meses, diferencia máxima 0.23 pp con la NY Fed.
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

export function CycleCard({ p }: { p: Panorama }) {
  const c = p.btc_cycle;
  if (isBlockError(c)) return <BlockMissing title="Ciclo de BTC" error={c.error} />;
  return (
    <DetailCard
      title="Ciclo de BTC"
      value={isNum(c.risk_pit) ? Math.round(c.risk_pit * 100) : DASH}
      unit="/ 100"
      badge={c.zone ? <Badge>{c.zone}</Badge> : undefined}
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
            los días. En el techo de 2021 la versión con futuro marcaba 0.867 y la honesta 0.756.
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
            Fase 10. Referencias: 7.6% en el piso de 2022-11; 97.2% en 2024-12.
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
      meaning="Lo que pagan los que apuestan apalancados al alza en BTC. Más alto = más gente apalancada a favor de la subida."
      notMeaning={`Está por encima del ${share(f.btc_rank)} de los días desde ${f.rank_since.slice(0, 4)}.`}
      asOf={f.as_of}
      detail={
        <div className="space-y-1">
          <div>
            ETH: {num(f.eth_annual_pct, 2)}% al año. Diferencia ETH − BTC: {num(f.spread_eth_btc_pct, 2)} pp, por
            encima del {share(f.spread_rank)} de los días.
          </div>
          <div>Media de 7 días de la suma diaria de liquidaciones de Binance, anualizada (× 365).</div>
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

export function StablecoinsCard({ p }: { p: Panorama }) {
  const s = p.stablecoins;
  if (isBlockError(s)) return <BlockMissing title="Dinero listo para cripto" error={s.error} />;
  return (
    <DetailCard
      title="Dinero listo para cripto"
      value={usdB(s.supply_usd)}
      unit="mil millones USD en stablecoins"
      meaning={`Dólares digitales en circulación. En 30 días: ${signedUsdB(s.change_30d_usd)} mil millones (${pct(s.change_30d_pct, 1)}).`}
      notMeaning="Que entre dinero no garantiza que suba el precio."
      asOf={s.as_of}
      detail={
        <div className="space-y-1">
          <div>
            Emisión neta, media de 7 días: {signedUsdB(s.net_mints_7d_usd)} mil millones por día.
          </div>
          <div>
            SSR (capitalización de BTC / stablecoins): {num(s.ssr)}; por encima del {share(s.ssr_rank_since_2020)} de los
            días desde 2020. Bajo = mucho dinero disponible en relación al tamaño de BTC.
          </div>
          <div>{s.evidence}</div>
        </div>
      }
    />
  );
}

export function PanelCard({ st }: { st: PanelStatus }) {
  return (
    <DetailCard
      title="Estudio del panel cripto"
      value={`${st.valid} / ${st.interim_valid_days}`}
      unit="días válidos hasta la primera revisión"
      meaning="Validación en curso: el modelo del panel diario se está probando con datos que no existían cuando se entrenó. Todavía no hay resultado."
      notMeaning={`Al ritmo actual (${share(st.valid_rate)} de los días cuentan), la revisión llega el ${st.eta_interim_at_current_rate ?? DASH}.`}
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
