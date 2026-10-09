import { Badge, Card } from "../common/Card";
import { useLang } from "../../i18n";
import { count, num, pct } from "../../lib/format";
import type { RegimeState, RiskFilterEvidence, StopFilterEvidence } from "../../types";

/**
 * Filtro de régimen — **lo único del stack que pasó su criterio como regla de
 * decisión**, y por eso es lo que va arriba en esta página.
 *
 * El resto de la Signal page describe: niveles, cono, EV, distancia a
 * liquidación. Esto en cambio *decide*: dice "a este leverage, ahora no". La
 * diferencia importa para un usuario que ya fue liquidado una vez, porque es
 * lo único accionable sin leer el resto de la pantalla.
 *
 * Tres decisiones de esta vista, y las tres son la regla 2 aplicada:
 *
 * - **El estado nunca sale sin su evidencia medida.** Un "RÉGIMEN ELEVADO" a
 *   secas es una alarma; con "evita el 52% de las liquidaciones a 20x, no el
 *   100%" es un dato con el que se puede decidir. El gauge de Seguridad lleva
 *   su AUC al lado por el mismo motivo.
 * - **Los números salen de `/api/backtest/riskfilter`, no están escritos
 *   acá.** El día que se remida el filtro, el dashboard cambia solo. Si el
 *   lift estuviera hardcodeado, alguien tendría que acordarse de venir a
 *   actualizarlo — y nadie se acuerda.
 * - **Se dice lo que el filtro NO sabe.** No tiene opinión direccional y no
 *   puede tenerla: el KPI 1 no pasó el gate. Un usuario que lea "elevado" y
 *   entienda "va a bajar" estaría operando contra un modelo que no existe.
 */

function Meter({ percentile, q, muted = false }: { percentile: number; q: number; muted?: boolean }) {
  const { tr } = useLang();
  const p = Math.min(Math.max(percentile, 0), 100);
  const threshold = q * 100;
  const elevated = p >= threshold;

  return (
    <div className="mt-3">
      <div className="relative h-2 w-full rounded-full bg-neutral-800">
        <div
          className={`h-2 rounded-full ${muted ? "bg-neutral-600" : elevated ? "bg-warn" : "bg-accent"}`}
          style={{ width: `${p}%` }}
        />
        {/* La marca del umbral va SOBRE la barra: sin ella un 74% y un 82% se
            ven casi iguales, y son lados opuestos de la decisión. */}
        <div
          className="absolute top-[-3px] h-[14px] w-px bg-neutral-400"
          style={{ left: `${threshold}%` }}
          title={tr(`threshold: percentile ${threshold.toFixed(0)}`, `umbral: percentil ${threshold.toFixed(0)}`)}
        />
      </div>
      <div className="mt-1 flex justify-between text-[11px] text-neutral-600">
        <span>{tr("calm", "calmo")}</span>
        <span>
          {tr("threshold", "umbral")} p{threshold.toFixed(0)}
        </span>
        <span>{tr("agitated", "agitado")}</span>
      </div>
    </div>
  );
}

export function RegimeFilter({
  regime,
  evidence,
  stopEvidence = null,
  staleSince = null,
}: {
  regime: RegimeState | null;
  evidence: RiskFilterEvidence | null;
  /** La lectura con stop (05-10). Es la que le importa a quien opera con
   *  stop y leverage bajo; sin ella se cae al texto de alcance genérico. */
  stopEvidence?: StopFilterEvidence | null;
  /** Fecha legible del pronóstico cuando ya no está vigente (M6.3: la Señal
   *  15m congelada). El estado se dibuja en gris y fechado: "Régimen normal"
   *  en verde con datos de hace días se lee como el mercado de hoy. */
  staleSince?: string | null;
}) {
  const { tr } = useLang();
  const title = tr("Regime filter", "Filtro de régimen");
  if (!regime) {
    return (
      <Card title={title}>
        <p className="text-sm text-neutral-500">
          {tr(
            "This forecast predates Phase 5-bis and does not carry the filter's decision. It is not recomputed here: the threshold depended on the reference of the bundle in force when it was issued, and rebuilding it today would show a decision that was never made.",
            "Este pronóstico es anterior a la Fase 5-bis y no lleva la decisión del filtro. No se recalcula acá: el umbral dependía de la referencia del bundle vigente al emitir, y reconstruirlo hoy mostraría una decisión que nunca se tomó.",
          )}
        </p>
      </Card>
    );
  }

  const { elevated, percentile, sigma, threshold, n_reference } = regime;

  return (
    <Card
      title={title}
      right={
        evidence?.viable ? (
          <Badge tone="ok" title={tr("Passed both pre-registered criteria", "Pasó los dos criterios pre-registrados")}>
            {tr("validated", "validado")}
          </Badge>
        ) : (
          <Badge tone="muted">{tr("not measured", "sin medición")}</Badge>
        )
      }
    >
      <div className="flex items-baseline gap-3">
        <span
          className={`text-2xl font-semibold ${staleSince ? "text-neutral-500" : elevated ? "text-warn" : "text-accent"}`}
        >
          {elevated ? tr("Do not enter", "No entrar") : tr("Normal regime", "Régimen normal")}
        </span>
        <span className="text-sm text-neutral-500">
          {tr("percentile", "percentil")} {num(percentile, 0)}
        </span>
      </div>

      {staleSince && (
        <p className="mt-1 text-sm text-warn">
          {tr(
            `State as of ${staleSince}, not now: the 15m analysis is stopped.`,
            `Estado del ${staleSince}, no de ahora: el análisis de 15m está detenido.`,
          )}
        </p>
      )}
      <p className="mt-1 text-sm text-neutral-400">
        {elevated
          ? tr(
              "The coming volatility is in the top quintile of what this model forecasts. Wait, or enter with less leverage.",
              "La volatilidad que viene está en el quintil alto de lo que este modelo pronostica. Esperar, o entrar con menos leverage.",
            )
          : tr(
              "The forecast volatility is within the model's usual range.",
              "La volatilidad pronosticada está dentro del rango habitual del modelo.",
            )}
      </p>

      <Meter percentile={percentile} q={regime.q} muted={Boolean(staleSince)} />

      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
        <dt className="text-neutral-500">{tr("forecast sigma", "sigma pronosticada")}</dt>
        <dd className="text-right tabular-nums text-neutral-300">{pct(sigma, 3)}</dd>
        <dt className="text-neutral-500">{tr("filter threshold", "umbral del filtro")}</dt>
        <dd className="text-right tabular-nums text-neutral-300">{pct(threshold, 3)}</dd>
        <dt className="text-neutral-500">{tr("reference", "referencia")}</dt>
        <dd className="text-right tabular-nums text-neutral-300">
          {count(n_reference)} {tr("bars", "barras")}
        </dd>
      </dl>

      {evidence && (
        <div className="mt-3 border-t border-neutral-800 pt-3 text-xs text-neutral-500">
          <p>
            {tr(`Measured over ${count(evidence.n_bars)} bars:`, `Medido sobre ${count(evidence.n_bars)} barras:`)}{" "}
            <span className="text-neutral-300">
              {tr(
                `rejecting ${pct(evidence.reject_rate, 1)} of bars avoids ${pct(evidence.capture_rate, 1)} of liquidations at ${num(evidence.leverage, 0)}x`,
                `rechazando el ${pct(evidence.reject_rate, 1)} de las barras se evita el ${pct(evidence.capture_rate, 1)} de las liquidaciones a ${num(evidence.leverage, 0)}x`,
              )}
            </span>
            .{" "}
            {tr(
              `Lift ${num(evidence.lift, 2)} — 95% CI ${num(evidence.lift_ci95[0], 2)}–${num(evidence.lift_ci95[1], 2)}; rejecting at random would give exactly 1.00 and the same filter with EWMA gives ${num(evidence.baseline_lift, 2)}.`,
              `Lift ${num(evidence.lift, 2)} — IC95% ${num(evidence.lift_ci95[0], 2)}–${num(evidence.lift_ci95[1], 2)}; rechazar al azar daría exactamente 1.00 y el mismo filtro con EWMA da ${num(evidence.baseline_lift, 2)}.`,
            )}
          </p>
          <p className="mt-1">
            {tr(
              `Cost: it also rejects ${pct(evidence.false_reject_rate, 1)} of the bars that did not liquidate.`,
              `Costo: se rechaza también el ${pct(evidence.false_reject_rate, 1)} de las barras que no liquidaban.`,
            )}
          </p>
          <p className="mt-1">
            {tr(
              `Scope: that was measured at ${num(evidence.leverage, 0)}x without a stop. At 5x there was not a single liquidation in two years.`,
              `Alcance: eso se midió a ${num(evidence.leverage, 0)}x sin stop. A 5x no hubo una sola liquidación en dos años.`,
            )}
          </p>
          {stopEvidence && stopEvidence.reproduces && stopEvidence.c1_pass ? (
            <p className="mt-1">
              <span className="text-neutral-300">
                {tr(`With a ${pct(stopEvidence.stop_pct, 1)} stop`, `Con stop a ${pct(stopEvidence.stop_pct, 1)}`)}
              </span>
              {tr(
                ", which is what matters when trading with little leverage: in an elevated regime the stop is hit ",
                ", que es lo que te importa operando con poco leverage: en régimen elevado el stop salta ",
              )}
              <span className="text-neutral-300">
                {tr(
                  `${num(stopEvidence.hit_rate_ratio, 2)} times more often`,
                  `${num(stopEvidence.hit_rate_ratio, 2)} veces más seguido`,
                )}
              </span>{" "}
              {tr(
                `(${pct(stopEvidence.hit_rate_elevated, 0)} of setups within ${stopEvidence.horizon_bars} bars, against ${pct(stopEvidence.hit_rate_normal, 0)} in normal). Lift ${num(stopEvidence.lift, 2)}, 95% CI ${num(stopEvidence.lift_ci95[0], 2)}–${num(stopEvidence.lift_ci95[1], 2)}, better than the same filter with EWMA. The expected gain does not change (${num(stopEvidence.expectancy_r.elevado, 2)} R in elevated against ${num(stopEvidence.expectancy_r.normal, 2)} R in normal): both the stop and the target get hit more. What changes are the streaks.`,
                `(${pct(stopEvidence.hit_rate_elevated, 0)} de los setups en ${stopEvidence.horizon_bars} velas, contra ${pct(stopEvidence.hit_rate_normal, 0)} en normal). Lift ${num(stopEvidence.lift, 2)}, IC95% ${num(stopEvidence.lift_ci95[0], 2)}–${num(stopEvidence.lift_ci95[1], 2)}, mejor que el mismo filtro con EWMA. La ganancia esperada no cambia (${num(stopEvidence.expectancy_r.elevado, 2)} R en elevado contra ${num(stopEvidence.expectancy_r.normal, 2)} R en normal): se toca más el stop y también el objetivo. Lo que cambia son las rachas.`,
              )}
            </p>
          ) : (
            <p className="mt-1">
              {tr(
                "No measurement with a stop: there is no evidence of how a stop behaves in an elevated regime.",
                "Sin medición con stop: no hay evidencia de cómo se comporta un stop en régimen elevado.",
              )}
            </p>
          )}
          <p className="mt-2 text-neutral-600">
            {tr(
              <>
                The filter <strong>does not say which way to enter</strong> — it has no directional view, and KPI 1 still
                has not passed the gate. Nor does it make the EV positive. Criterion fixed before measuring in{" "}
              </>,
              <>
                El filtro <strong>no dice hacia dónde entrar</strong> — no tiene opinión direccional, y el KPI 1 sigue sin
                pasar el gate. Tampoco vuelve positivo el EV. Criterio fijado antes de medir en{" "}
              </>,
            )}
            {evidence.preregistration ?? tr("the pre-registration", "el pre-registro")}.
          </p>
        </div>
      )}
    </Card>
  );
}
