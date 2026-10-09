import { Badge, Card } from "../common/Card";
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
          title={`umbral: percentil ${threshold.toFixed(0)}`}
        />
      </div>
      <div className="mt-1 flex justify-between text-[11px] text-neutral-600">
        <span>calmo</span>
        <span>umbral p{threshold.toFixed(0)}</span>
        <span>agitado</span>
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
  if (!regime) {
    return (
      <Card title="Filtro de régimen">
        <p className="text-sm text-neutral-500">
          Este pronóstico es anterior a la Fase 5-bis y no lleva la decisión del
          filtro. No se recalcula acá: el umbral dependía de la referencia del
          bundle vigente al emitir, y reconstruirlo hoy mostraría una decisión
          que nunca se tomó.
        </p>
      </Card>
    );
  }

  const { elevated, percentile, sigma, threshold, n_reference } = regime;

  return (
    <Card
      title="Filtro de régimen"
      right={
        evidence?.viable ? (
          <Badge tone="ok" title="Pasó los dos criterios pre-registrados">
            validado
          </Badge>
        ) : (
          <Badge tone="muted">sin medición</Badge>
        )
      }
    >
      <div className="flex items-baseline gap-3">
        <span
          className={`text-2xl font-semibold ${
            staleSince ? "text-neutral-500" : elevated ? "text-warn" : "text-accent"
          }`}
        >
          {elevated ? "No entrar" : "Régimen normal"}
        </span>
        <span className="text-sm text-neutral-500">
          percentil {num(percentile, 0)}
        </span>
      </div>

      {staleSince && (
        <p className="mt-1 text-sm text-warn">
          Estado del {staleSince}, no de ahora: el análisis de 15m está detenido.
        </p>
      )}
      <p className="mt-1 text-sm text-neutral-400">
        {elevated
          ? "La volatilidad que viene está en el quintil alto de lo que este modelo pronostica. Esperar, o entrar con menos leverage."
          : "La volatilidad pronosticada está dentro del rango habitual del modelo."}
      </p>

      <Meter percentile={percentile} q={regime.q} muted={Boolean(staleSince)} />

      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
        <dt className="text-neutral-500">sigma pronosticada</dt>
        <dd className="text-right tabular-nums text-neutral-300">
          {pct(sigma, 3)}
        </dd>
        <dt className="text-neutral-500">umbral del filtro</dt>
        <dd className="text-right tabular-nums text-neutral-300">
          {pct(threshold, 3)}
        </dd>
        <dt className="text-neutral-500">referencia</dt>
        <dd className="text-right tabular-nums text-neutral-300">
          {count(n_reference)} barras
        </dd>
      </dl>

      {evidence && (
        <div className="mt-3 border-t border-neutral-800 pt-3 text-xs text-neutral-500">
          <p>
            Medido sobre {count(evidence.n_bars)} barras:{" "}
            <span className="text-neutral-300">
              rechazando el {pct(evidence.reject_rate, 1)} de las barras
              se evita el {pct(evidence.capture_rate, 1)} de las
              liquidaciones a {num(evidence.leverage, 0)}x
            </span>
            . Lift {num(evidence.lift, 2)} — IC95%{" "}
            {num(evidence.lift_ci95[0], 2)}–{num(evidence.lift_ci95[1], 2)};
            rechazar al azar daría exactamente 1.00 y el mismo filtro con EWMA
            da {num(evidence.baseline_lift, 2)}.
          </p>
          <p className="mt-1">
            Costo: se rechaza también el{" "}
            {pct(evidence.false_reject_rate, 1)} de las barras que no
            liquidaban.
          </p>
          <p className="mt-1">
            Alcance: eso se midió a {num(evidence.leverage, 0)}x sin stop. A
            5x no hubo una sola liquidación en dos años.
          </p>
          {stopEvidence && stopEvidence.reproduces && stopEvidence.c1_pass ? (
            <p className="mt-1">
              <span className="text-neutral-300">Con stop a {pct(stopEvidence.stop_pct, 1)}</span>,
              que es lo que te importa operando con poco leverage: en régimen
              elevado el stop salta{" "}
              <span className="text-neutral-300">
                {num(stopEvidence.hit_rate_ratio, 2)} veces más seguido
              </span>{" "}
              ({pct(stopEvidence.hit_rate_elevated, 0)} de los setups en{" "}
              {stopEvidence.horizon_bars} velas, contra{" "}
              {pct(stopEvidence.hit_rate_normal, 0)} en normal). Lift{" "}
              {num(stopEvidence.lift, 2)}, IC95%{" "}
              {num(stopEvidence.lift_ci95[0], 2)}–{num(stopEvidence.lift_ci95[1], 2)},
              mejor que el mismo filtro con EWMA. La ganancia esperada no cambia
              ({num(stopEvidence.expectancy_r.elevado, 2)} R en elevado contra{" "}
              {num(stopEvidence.expectancy_r.normal, 2)} R en normal): se toca
              más el stop y también el objetivo. Lo que cambia son las rachas.
            </p>
          ) : (
            <p className="mt-1">
              Sin medición con stop: no hay evidencia de cómo se comporta un
              stop en régimen elevado.
            </p>
          )}
          <p className="mt-2 text-neutral-600">
            El filtro <strong>no dice hacia dónde entrar</strong> — no tiene
            opinión direccional, y el KPI 1 sigue sin pasar el gate. Tampoco
            vuelve positivo el EV. Criterio fijado antes de medir en{" "}
            {evidence.preregistration ?? "el pre-registro"}.
          </p>
        </div>
      )}
    </Card>
  );
}
