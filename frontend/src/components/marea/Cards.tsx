import { Link } from "react-router-dom";

import { Badge } from "../common/Card";
import { BlockMissing, Delta, DetailCard } from "../common/DetailCard";
import { CLASS_EN, EVIDENCE_EN, type Lang, SEASON_EN, ZONE_EN, translated, useLang } from "../../i18n";
import { SERIES } from "../../lib/chartTheme";
import { DASH, isNum, num, pct, signedPct } from "../../lib/format";
import { isBlockError } from "../../types";
import type { LiquidityReport, PanelStatus, Panorama, StrengthReport } from "../../types";
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
 *
 * Cada texto va en inglés y en español (`tr(en, es)`); las cifras son las
 * mismas en los dos idiomas.
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
function aboveBelow(lang: Lang, p: number | null | undefined, since: string): string {
  if (!isNum(p)) return "";
  if (lang === "en") {
    return p >= 0.5
      ? `Today it is higher than on ${rank(p)} of days since ${since}.`
      : `Today it is lower than on ${rank(1 - p)} of days since ${since}.`;
  }
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
const LIQ_WORD: Record<string, [en: string, es: string]> = {
  expansivo: ["Strong", "Fuerte"],
  neutro: ["Normal", "Normal"],
  contractivo: ["Weak", "Débil"],
};

/** La etiqueta técnica tal cual, para el detalle. */
const LIQ_TECH_EN: Record<string, string> = {
  expansivo: "expansionary",
  neutro: "neutral",
  contractivo: "contractionary",
};

function liqWord(lang: Lang, label: string | null | undefined): string {
  const w = label ? LIQ_WORD[label] : undefined;
  if (!w) return lang === "en" ? "no data" : "sin dato";
  return lang === "en" ? w[0] : w[1];
}

function liqTech(lang: Lang, label: string | null | undefined): string {
  if (!label) return DASH;
  return lang === "en" ? (LIQ_TECH_EN[label] ?? label) : label;
}

/** El percentil dicho en palabras, truncado: no se redondea a favor. */
function liqPace(lang: Lang, p: number | null): string {
  if (!isNum(p)) return lang === "en" ? "No reading available." : "Sin lectura disponible.";
  const up = Math.floor(p * 100);
  const down = Math.floor((1 - p) * 100);
  if (lang === "en") {
    return p >= 0.5
      ? `Global liquidity is growing faster than in ${up}% of previous weeks.`
      : `Global liquidity is growing more slowly than in ${down}% of previous weeks.`;
  }
  return p >= 0.5
    ? `La liquidez global avanza más rápido que en el ${up}% de las semanas anteriores.`
    : `La liquidez global avanza más lento que en el ${down}% de las semanas anteriores.`;
}

function LiqDelta({ rep }: { rep: LiquidityReport }) {
  const { lang, tr } = useLang();
  const now = rep.regime.composite_pct;
  const hist = rep.history.filter((h) => isNum(h.composite_pct));
  const past = pointBefore(hist, "date", rep.as_of, 28);
  if (!past || !isNum(now) || !isNum(past.composite_pct)) return null;
  const word = past.label ? liqWord(lang, past.label).toLowerCase() : DASH;
  return (
    <Delta dir={dirOf(now - past.composite_pct)}>
      {tr("4 weeks ago", "hace 4 semanas")}: {word}
    </Delta>
  );
}

function StaleBadge() {
  const { tr } = useLang();
  return <Badge tone="warn">{tr("stale data", "dato viejo")}</Badge>;
}

export function LiquidityCard({ rep, chart = false }: { rep: LiquidityReport; chart?: boolean }) {
  const { lang, tr } = useLang();
  const label = rep.regime.label ?? "";
  const spark = rep.history.filter((h) => isNum(h.composite_pct)).slice(-52);
  const weeks = num(rep.regime.persistence.median_run_weeks, 0);
  return (
    <DetailCard
      title={tr("Global liquidity", "Liquidez global")}
      value={liqWord(lang, label)}
      tone={LIQ_TONE[label] ?? ""}
      delta={<LiqDelta rep={rep} />}
      visual={
        chart ? (
          <Spark data={spark} x="date" y="composite_pct" domain={[0, 1]} bands span={tr("last year", "último año")} />
        ) : undefined
      }
      meaning={liqPace(lang, rep.regime.composite_pct)}
      notMeaning={tr(
        `It describes the moment, not a price forecast. The reading usually changes within about ${weeks} weeks.`,
        `Es un estado del momento, no un pronóstico de precios. La lectura suele cambiar en unas ${weeks} semanas.`,
      )}
      asOf={rep.as_of}
      badge={rep.stale ? <StaleBadge /> : undefined}
      detail={<LiquidityDetail rep={rep} />}
    />
  );
}

function LiquidityDetail({ rep }: { rep: LiquidityReport }) {
  const { lang, tr } = useLang();
  const r = rep.regime;
  return (
    <div className="space-y-1">
      <div>
        {tr(
          <>
            Technical label: {liqTech(lang, r.label)} (strong = expansionary, weak = contractionary, by thirds).
            Composite percentile: {num(r.composite_pct)}, against the weeks before it since{" "}
            {r.available_from ?? DASH}. Since {r.since ?? DASH} · 4 weeks ago: {liqTech(lang, r.label_4w_ago)} · 13
            weeks ago: {liqTech(lang, r.label_13w_ago)}.
          </>,
          <>
            Etiqueta técnica: {r.label ?? DASH} (fuerte = expansivo, débil = contractivo, por tercios). Percentil del
            compuesto: {num(r.composite_pct)}, contra las semanas anteriores desde {r.available_from ?? DASH}. Desde{" "}
            {r.since ?? DASH} · hace 4 semanas: {r.label_4w_ago ?? DASH} · hace 13: {r.label_13w_ago ?? DASH}.
          </>,
        )}
      </div>
      <div>
        {tr(
          `Median run of a label: ${num(r.persistence.median_run_weeks, 0)} weeks. Five equally weighted components (net Fed, M2, dollar, real rate, NFCI), 13-week change.`,
          `Racha mediana de una etiqueta: ${num(r.persistence.median_run_weeks, 0)} semanas. Cinco componentes con pesos iguales (Fed neta, M2, dólar, tasa real, NFCI), cambio a 13 semanas.`,
        )}
      </div>
      <Link to="/liquidez" className="text-accent hover:underline">
        {tr("see Liquidity →", "ver Liquidez →")}
      </Link>
    </div>
  );
}

export function RecessionCard({ p, chart = false }: { p: Panorama; chart?: boolean }) {
  const { lang, tr } = useLang();
  const r = p.recession;
  const title = tr("US recession risk", "Riesgo de recesión EE.UU.");
  if (isBlockError(r)) return <BlockMissing title={title} error={r.error} />;
  const h = r.history_12m;
  const past = h.length >= 4 ? h[h.length - 4] : undefined;
  const peak = r.peak_since_2022;
  return (
    <DetailCard
      title={title}
      value={share(r.probability)}
      unit={tr(`within 12 months (${r.target.slice(0, 7)})`, `a 12 meses (${r.target.slice(0, 7)})`)}
      delta={
        past ? (
          <Delta dir={dirOf(r.probability - past.probability)}>
            {tr("3 months ago", "hace 3 meses")}: {share(past.probability)}
          </Delta>
        ) : undefined
      }
      visual={
        chart ? (
          <Spark data={h} x="month" y="probability" domain={[0, 1]} span={tr("last 12 months", "últimos 12 meses")} />
        ) : undefined
      }
      meaning={tr(
        "Probability of a recession implied by the yield curve, using the model the New York Fed publishes.",
        "Probabilidad de recesión según la curva de tasas, con el modelo que publica la Fed de Nueva York.",
      )}
      notMeaning={tr(
        `Caution: it reached ${share(peak.probability)} in ${peak.month.slice(0, 7)} and no recession came.`,
        `Ojo: llegó a ${share(peak.probability)} en ${peak.month.slice(0, 7)} y la recesión no llegó.`,
      )}
      asOf={r.as_of.slice(0, 7)}
      detail={
        <div className="space-y-1">
          <div>
            {tr("Estrella–Trubin probit", "Probit de Estrella y Trubin")}: Φ(α + β · spread),{" "}
            {tr("with", "con")} α = {num(r.model.alpha, 4)} {tr("and", "y")} β = {num(r.model.beta, 4)}.{" "}
            {tr("10y − 3m bill spread", "Spread 10a − letra 3m")}: {num(r.spread)} pp.{" "}
            {tr(
              "Reproduced from FRED against the NY Fed file (details in",
              "Reproducido desde FRED contra el archivo de la NY Fed (detalle en",
            )}{" "}
            <span className="font-mono">marea/recession.py</span>).
          </div>
          <div>
            {tr("Sahm rule (unemployment)", "Regla de Sahm (desempleo)")}:{" "}
            {r.sahm ? `${num(r.sahm.value)} (${r.sahm.as_of.slice(0, 7)})` : DASH}.{" "}
            {tr("From 0.50 it has signalled recession.", "Desde 0.50 ha marcado recesión.")}
          </div>
          <div>{translated(lang, EVIDENCE_EN, r.evidence)}</div>
        </div>
      }
    />
  );
}

export function CycleCard({ p, chart = false }: { p: Panorama; chart?: boolean }) {
  const { lang, tr } = useLang();
  const c = p.btc_cycle;
  const title = tr("BTC cycle", "Ciclo de BTC");
  if (isBlockError(c)) return <BlockMissing title={title} error={c.error} />;
  const past = pointBefore(c.history, "day", c.as_of, 30);
  return (
    <DetailCard
      title={title}
      value={score(c.risk_pit)}
      unit="/ 100"
      badge={c.zone ? <Badge>{translated(lang, ZONE_EN, c.zone)}</Badge> : undefined}
      delta={
        past && isNum(past.risk_pit) && isNum(c.risk_pit) ? (
          <Delta dir={dirOf(c.risk_pit - past.risk_pit)}>
            {tr("30 days ago", "hace 30 días")}: {score(past.risk_pit)}
          </Delta>
        ) : undefined
      }
      visual={
        chart ? (
          <Spark data={c.history} x="day" y="risk_pit" domain={[0, 1]} span={tr("last year", "último año")} />
        ) : undefined
      }
      meaning={tr(
        "Where the BTC price sits within its own history: near 0 has been the low zone of the cycle; near 100, euphoria.",
        "Dónde está el precio de BTC dentro de su propia historia: cerca de 0 fue zona baja del ciclo; cerca de 100, euforia.",
      )}
      notMeaning={tr("It does not say where the price is going.", "No dice hacia dónde va el precio.")}
      asOf={c.as_of}
      detail={
        <div className="space-y-1">
          <div>
            {tr(
              `60% Mayer Multiple (price / 200-day average, today ${num(c.mayer)}) + 40% deviation from a log regression. Computed only with each date's past: ${num(c.risk_pit, 3)}.`,
              `60% Mayer Multiple (precio / media de 200 días, hoy ${num(c.mayer)}) + 40% desviación contra una regresión log. Calculado solo con el pasado de cada fecha: ${num(c.risk_pit, 3)}.`,
            )}
          </div>
          <div>
            {tr(
              `The same recipe looking at the future (like Outliers') gives ${num(c.risk_full, 3)}. Correlation between the two: ${num(c.pit_vs_full.correlation)}; they differ by more than 0.20 on ${share(c.pit_vs_full.share_far)} of days.`,
              `La misma receta mirando el futuro (como la de Outliers) da ${num(c.risk_full, 3)}. Correlación entre las dos: ${num(c.pit_vs_full.correlation)}; difieren en más de 0.20 el ${share(c.pit_vs_full.share_far)} de los días.`,
            )}
            {c.references.map((ref) => (
              <span key={ref.day}>
                {" "}
                {tr(
                  `At the ${ref.label === "techo de precio 2021" ? "2021 price top" : ref.label} (${ref.day}) the future-looking version read ${num(ref.risk_full, 3)} and the honest one ${num(ref.risk_pit, 3)}.`,
                  `En el ${ref.label} (${ref.day}) la versión con futuro marcaba ${num(ref.risk_full, 3)} y la honesta ${num(ref.risk_pit, 3)}.`,
                )}
              </span>
            ))}
          </div>
        </div>
      }
    />
  );
}

const BREADTH_REF_EN: Record<string, string> = { "piso 2022": "2022 bottom", "máximo 2024": "2024 high" };

export function BreadthCard({ p }: { p: Panorama }) {
  const { tr } = useLang();
  const b = p.breadth;
  const title = tr("Crypto breadth", "Amplitud cripto");
  if (isBlockError(b)) return <BlockMissing title={title} error={b.error} />;
  const refs = b.references
    .map((ref) =>
      tr(
        `${pct(ref.above_sma200, 1)} at the ${BREADTH_REF_EN[ref.label] ?? ref.label} (${ref.day})`,
        `${pct(ref.above_sma200, 1)} en el ${ref.label} (${ref.day})`,
      ),
    )
    .join("; ");
  return (
    <DetailCard
      title={title}
      value={share(b.above_sma200)}
      unit={tr("above their 200-day average", "sobre su media de 200 días")}
      meaning={tr(
        `Of the ${b.n_symbols} cryptos with enough liquidity, that share is above its long-term trend.`,
        `De las ${b.n_symbols} criptos con liquidez suficiente, esa fracción está por encima de su tendencia larga.`,
      )}
      notMeaning={tr(
        "It measures how widespread the rise is, not how long it will last.",
        "Mide cuán extendida está la subida, no cuánto va a durar.",
      )}
      asOf={b.as_of}
      detail={
        <div className="space-y-1">
          <div>
            {tr("Above the 20-week average", "Sobre la media de 20 semanas")}: {share(b.above_sma20w)}.
          </div>
          <div>
            {tr(
              "Universe: Binance perpetuals including delisted ones, with the pre-registered liquidity rule of Phase 10.",
              "Universo: perpetuos de Binance con deslistados incluidos y la regla de liquidez pre-registrada de la Fase 10.",
            )}
            {refs && ` ${tr("References", "Referencias")}: ${refs}.`}
          </div>
        </div>
      }
    />
  );
}

export function AltseasonCard({ p }: { p: Panorama }) {
  const { lang, tr } = useLang();
  const b = p.breadth;
  const title = tr("Altcoin season", "Temporada de altcoins");
  if (isBlockError(b)) return <BlockMissing title={title} error={b.error} />;
  return (
    <DetailCard
      title={title}
      value={isNum(b.altseason) ? Math.round(b.altseason * 100) : DASH}
      unit="/ 100"
      badge={<Badge>{translated(lang, SEASON_EN, b.altseason_label)}</Badge>}
      meaning={tr(
        "How many of the 50 most traded altcoins beat BTC over the last 90 days. From 75 people talk of altcoin season; below 25, of bitcoin season.",
        "Cuántas de las 50 altcoins más negociadas le ganaron a BTC en los últimos 90 días. Desde 75 se habla de temporada de alts; bajo 25, de BTC.",
      )}
      notMeaning={tr(
        "It says who did better over 90 days, not which one will rise.",
        "Dice a quién le fue mejor en 90 días, no cuál va a subir.",
      )}
      asOf={b.as_of}
      detail={
        <div>
          {tr(
            "The 50 are picked by median 30-day volume, not by market cap (its history is not free): declared proxy.",
            "Las 50 se eligen por volumen mediano de 30 días, no por capitalización (su historia no es gratis): proxy declarado.",
          )}
        </div>
      }
    />
  );
}

export function FundingCard({ p }: { p: Panorama }) {
  const { lang, tr } = useLang();
  const f = p.funding;
  const title = tr("Leverage (funding)", "Apalancamiento (funding)");
  if (isBlockError(f)) return <BlockMissing title={title} error={f.error} />;
  const since = f.rank_since.slice(0, 4);
  return (
    <DetailCard
      title={title}
      value={num(f.btc_annual_pct, 1)}
      unit={tr("% a year, BTC", "% al año, BTC")}
      meaning={`${tr(
        "What those betting on a BTC rise with borrowed money pay over a year.",
        "Lo que pagan, en un año, los que apuestan al alza en BTC con dinero prestado.",
      )} ${aboveBelow(lang, f.btc_rank, since)}`}
      notMeaning={tr(
        "It measures how much leverage is behind the rise, not where the price is going.",
        "Mide cuánto apalancamiento hay a favor de la subida, no hacia dónde va el precio.",
      )}
      asOf={f.as_of}
      detail={
        <div className="space-y-1">
          <div>
            {tr(
              `ETH: ${num(f.eth_annual_pct, 2)}% a year. ETH − BTC difference: ${num(f.spread_eth_btc_pct, 2)} pp, above ${rank(f.spread_rank)} of days.`,
              `ETH: ${num(f.eth_annual_pct, 2)}% al año. Diferencia ETH − BTC: ${num(f.spread_eth_btc_pct, 2)} pp, por encima del ${rank(f.spread_rank)} de los días.`,
            )}
          </div>
          <div>
            {tr(
              "Binance funding: sum of the rates charged each UTC day, 7-day average, times 365 (not compounded). Positive = longs pay shorts.",
              "Funding de Binance: suma de las tasas cobradas en cada día UTC, media de 7 días, por 365 (sin componer). Positivo = los largos les pagan a los cortos.",
            )}
          </div>
        </div>
      }
    />
  );
}

export function OptionsCard({ p }: { p: Panorama }) {
  const { tr } = useLang();
  const o = p.options;
  if (isBlockError(o)) return <BlockMissing title={tr("BTC options", "Opciones de BTC")} error={o.error} />;
  return (
    <DetailCard
      title={tr("BTC options (hedging)", "Opciones de BTC (cobertura)")}
      value={num(o.btc.pc_oi, 2)}
      unit={tr("puts per call", "puts por cada call")}
      meaning={tr(
        "Open downside-protection contracts for each upside bet, on Deribit.",
        "Contratos de protección a la baja abiertos por cada apuesta al alza, en Deribit.",
      )}
      notMeaning={tr(
        `No history yet (${o.btc.snapshots} snapshots since ${o.btc.first_snapshot.slice(0, 10)}): it cannot be called high or low.`,
        `Sin historia todavía (${o.btc.snapshots} fotos desde ${o.btc.first_snapshot.slice(0, 10)}): no se puede decir si es alto o bajo.`,
      )}
      asOf={o.btc.captured_at.slice(0, 10)}
      detail={
        <div className="space-y-1">
          <div>
            {tr(
              `Expiries ≤ 30 days: ${num(o.btc.pc_oi_near, 2)}. By 24h volume: ${num(o.btc.pc_volume, 2)}. Open interest: ${usdB(o.btc.oi_usd)} billion USD.`,
              `Vencimientos ≤ 30 días: ${num(o.btc.pc_oi_near, 2)}. Por volumen de 24 h: ${num(o.btc.pc_volume, 2)}. Interés abierto: ${usdB(o.btc.oi_usd)} mil millones USD.`,
            )}
          </div>
          <div>
            {tr(
              `ETH: ${num(o.eth.pc_oi, 2)} puts per call (${usdB(o.eth.oi_usd)} billion USD).`,
              `ETH: ${num(o.eth.pc_oi, 2)} puts por call (${usdB(o.eth.oi_usd)} mil millones USD).`,
            )}
          </div>
        </div>
      }
    />
  );
}

export function StablecoinsCard({ p, chart = false }: { p: Panorama; chart?: boolean }) {
  const { lang, tr } = useLang();
  const s = p.stablecoins;
  const title = tr("Money ready for crypto", "Dinero listo para cripto");
  if (isBlockError(s)) return <BlockMissing title={title} error={s.error} />;
  const ch = s.change_30d_usd;
  return (
    <DetailCard
      title={title}
      value={usdB(s.supply_usd)}
      unit={tr("billion USD in stablecoins", "mil millones USD en stablecoins")}
      delta={
        isNum(ch) ? (
          <Delta dir={ch > 0 ? "up" : ch < 0 ? "down" : "flat"}>
            {tr(
              `${signedUsdB(ch)} billion in 30 days (${signedPct(s.change_30d_pct, 1)})`,
              `${signedUsdB(ch)} mil millones en 30 días (${signedPct(s.change_30d_pct, 1)})`,
            )}
          </Delta>
        ) : undefined
      }
      visual={
        chart ? <Spark data={s.history} x="day" y="supply_usd" span={tr("last year", "último año")} /> : undefined
      }
      meaning={tr(
        "Digital dollars in circulation: money already inside crypto, available to buy.",
        "Dólares digitales en circulación: dinero que ya está dentro de cripto, disponible para comprar.",
      )}
      notMeaning={tr(
        "Money coming in does not guarantee that prices rise.",
        "Que entre dinero no garantiza que suba el precio.",
      )}
      asOf={s.as_of}
      detail={
        <div className="space-y-1">
          <div>
            {tr(
              `Net issuance, 7-day average: ${signedUsdB(s.net_mints_7d_usd)} billion per day.`,
              `Emisión neta, media de 7 días: ${signedUsdB(s.net_mints_7d_usd)} mil millones por día.`,
            )}
          </div>
          <div>
            {tr(
              `SSR (BTC market cap / stablecoins): ${num(s.ssr)}; above ${rank(s.ssr_rank_since_2020)} of days since 2020. Low = plenty of money available relative to BTC's size.`,
              `SSR (capitalización de BTC / stablecoins): ${num(s.ssr)}; por encima del ${rank(s.ssr_rank_since_2020)} de los días desde 2020. Bajo = mucho dinero disponible en relación al tamaño de BTC.`,
            )}
          </div>
          {chart && (
            <div>
              {tr(
                "The small chart does not start at zero: it shows the shape of the year, not the size of the change.",
                "El gráfico chico no parte de cero: muestra la forma del año, no el tamaño del cambio.",
              )}
            </div>
          )}
          <div>{translated(lang, EVIDENCE_EN, s.evidence)}</div>
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
  const { lang, tr } = useLang();
  const label = (c: { block: string; label: string }) => (lang === "en" ? (CLASS_EN[c.block] ?? c.label) : c.label);
  const rows = rep.classes
    .filter((c) => isNum(c.rel_4s))
    .sort((a, b) => (b.rel_4s as number) - (a.rel_4s as number));
  const title = tr("Asset classes · last month", "Clases de activo · último mes");
  if (rows.length < 4)
    return <BlockMissing title={title} error={tr("no 4-week relative strength", "sin fuerza relativa a 4 semanas")} />;
  const top = rows.slice(0, 2);
  const bottom = rows.slice(-2).reverse();
  const fmt = (c: (typeof rows)[number]) => `${label(c)} ${signedPct(c.rel_4s, 1)}`;
  const and = tr(" and ", " y ");
  const max = Math.max(...rows.map((c) => Math.abs(c.rel_4s as number))) || 1;
  return (
    <DetailCard
      title={title}
      value={label(top[0])}
      unit={tr("did best", "le fue mejor")}
      badge={rep.stale ? <StaleBadge /> : <Badge tone="muted">{tr("descriptive", "descriptivo")}</Badge>}
      visual={
        chart ? (
          <div className="space-y-1">
            {rows.map((c) => {
              const v = c.rel_4s as number;
              const w = `${(Math.abs(v) / max) * 50}%`;
              return (
                <div key={c.block} className="grid grid-cols-[minmax(0,7.5rem)_minmax(3rem,1fr)_3rem] items-center gap-2 text-[11px]">
                  <span className="truncate text-neutral-400">{label(c)}</span>
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
      meaning={tr(
        `Top: ${top.map(fmt).join(and)}. Bottom: ${bottom.map(fmt).join(and)}. Compared with the typical ETF.`,
        `Arriba: ${top.map(fmt).join(and)}. Abajo: ${bottom.map(fmt).join(and)}. Comparado con el ETF típico.`,
      )}
      notMeaning={tr(
        "It says who did better, not who will do better: we tested whether this ranking anticipates the following weeks and found no evidence that it does.",
        "Dice a quién le fue mejor, no a quién le va a ir mejor: se midió si este orden anticipa las semanas siguientes y no se encontró que lo haga.",
      )}
      asOf={rep.as_of}
      detail={
        <div className="space-y-1">
          <div>
            {tr(
              `Total return of each class over 20 sessions minus the median of the ${rep.etfs.length} ETFs. A class is the simple average of its ETFs; the dollar is UUP alone. Crypto is left out: it has its own section.`,
              `Retorno total de cada clase en 20 sesiones menos la mediana de los ${rep.etfs.length} ETFs. Una clase es el promedio simple de sus ETFs; el dólar es solo UUP. Cripto no entra: tiene su propia sección.`,
            )}
          </div>
          <div>
            {tr("At 13 and 26 weeks", "A 13 y 26 semanas")}:{" "}
            {rows.map((c) => `${label(c)} ${signedPct(c.rel_13s, 1)} / ${signedPct(c.rel_26s, 1)}`).join(" · ")}.
          </div>
          <div>
            {tr(
              "This is not money flow: ETF creations and redemptions are not in any free source. The ranking does not predict: it was tested and did not pass (see Lab).",
              "No es flujo de dinero: las creaciones y rescates de ETFs no están en ninguna fuente gratuita. El orden no predice: se midió y no pasó (ver Laboratorio).",
            )}
          </div>
          <Link to="/flujos" className="text-accent hover:underline">
            {tr("see Flows →", "ver Flujos →")}
          </Link>
        </div>
      }
    />
  );
}

export function PanelCard({ st }: { st: PanelStatus }) {
  const { tr } = useLang();
  const done = Math.min(st.valid / st.interim_valid_days, 1);
  const eta = st.eta_interim_at_current_rate ?? DASH;
  return (
    <DetailCard
      title={tr("Crypto panel study", "Estudio del panel cripto")}
      value={`${st.valid} / ${st.interim_valid_days}`}
      unit={tr("valid days until the first review", "días válidos hasta la primera revisión")}
      visual={
        <div className="h-2 overflow-hidden rounded bg-neutral-800" title={`${st.valid} / ${st.interim_valid_days}`}>
          <div className="h-full rounded" style={{ width: `${done * 100}%`, background: SERIES[0] }} />
        </div>
      }
      meaning={tr(
        `Validation in progress: the daily panel model is being tested on data that did not exist when it was trained. At the current pace (${rank(st.valid_rate)} of days count), the first review arrives on ${eta}.`,
        `Validación en curso: el modelo del panel diario se está probando con datos que no existían cuando se entrenó. Al ritmo actual (cuenta el ${rank(st.valid_rate)} de los días), la primera revisión llega el ${eta}.`,
      )}
      notMeaning={tr(
        "Progress says nothing about whether the model works: the result is not looked at before that review.",
        "El avance no dice si el modelo funciona: el resultado no se mira antes de esa revisión.",
      )}
      asOf={st.last_decision}
      detail={
        <div className="space-y-1">
          <div>
            {tr(
              `${st.decision_dates} decision days since ${st.first_decision}: ${st.valid} valid, ${st.backfill.length} late (backfill), ${st.never_emitted.length} never issued.`,
              `${st.decision_dates} días de decisión desde ${st.first_decision}: ${st.valid} válidos, ${st.backfill.length} tarde (backfill), ${st.never_emitted.length} sin emitir.`,
            )}
          </div>
          <div>
            {tr(
              `Final cutoff at ${st.cutoff_valid_days} valid days: ${st.eta_at_current_rate ?? DASH} at the current pace. The result (IC) is not shown before the interim review (Amendment 1).`,
              `Corte final a ${st.cutoff_valid_days} días válidos: ${st.eta_at_current_rate ?? DASH} al ritmo actual. El resultado (IC) no se muestra antes de la revisión intermedia (Enmienda 1).`,
            )}
          </div>
          <Link to="/cripto/panel" className="text-accent hover:underline">
            {tr("see Daily panel →", "ver Panel diario →")}
          </Link>
        </div>
      }
    />
  );
}

