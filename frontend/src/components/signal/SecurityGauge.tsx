import { Badge } from "../common/Card";
import { count, num, pct, pValue } from "../../lib/format";
import type { DirectionMetrics, GateResponse } from "../../types";

/**
 * KPI 1 — Seguridad. **Se dibuja en gris y no se puede operar contra él.**
 *
 * Eso no es una elección estética: es la regla 2 de CLAUDE.md aplicada a la
 * letra. El gate de la Fase 4 midió que el target de dirección calibra
 * (4,0pp / 5,1pp) y **no discrimina** (AUC 0,519 / 0,533), y un modelo que
 * reproduce la tasa base está perfectamente calibrado por construcción y no
 * distingue nada. Mientras no pase los dos criterios, el número existe, se
 * registra para poder medirlo, y no habilita una entrada.
 *
 * Dos decisiones de esta vista:
 *
 * - **El veredicto viene de `/api/backtest/gate`, no está escrito acá.** El
 *   día que el motor direccional pase los dos criterios, el gauge se enciende
 *   solo. Si el "no habilitado" estuviera hardcodeado en este archivo,
 *   alguien tendría que acordarse de venir a borrarlo.
 * - **La marca del equilibrio va sobre el arco.** El número que importa no es
 *   la probabilidad sino su distancia al equilibrio: con barreras simétricas
 *   y costos, hace falta acertar ~60% para no perder plata. Mostrar 50% sin
 *   esa referencia se lee como "moneda al aire", cuando en realidad es
 *   "pierde".
 */

const R = 52;
const CX = 70;
const CY = 66;
const START = Math.PI; // 180° — semicírculo de izquierda a derecha
const SWEEP = Math.PI;

function polar(fraction: number): [number, number] {
  const angle = START + SWEEP * Math.min(Math.max(fraction, 0), 1);
  return [CX + R * Math.cos(angle), CY + R * Math.sin(angle)];
}

function arc(from: number, to: number): string {
  const [x1, y1] = polar(from);
  const [x2, y2] = polar(to);
  return `M ${x1} ${y1} A ${R} ${R} 0 0 1 ${x2} ${y2}`;
}

function Dial({
  probability,
  breakeven,
  live,
}: {
  probability: number;
  breakeven: number;
  live: boolean;
}) {
  const angle = START + SWEEP * Math.min(Math.max(breakeven, 0), 1);
  // Gris mientras el KPI no esté habilitado; si algún día lo está, el color
  // lo decide la posición respecto del equilibrio y no un umbral cosmético.
  //
  // El gris es CLARO y no oscuro: "sin calibrar" significa que el número no
  // habilita operar, no que haya que esconderlo. Con `#4b5563` sobre el
  // riel `#1f2937` el arco quedaba invisible y el gauge parecía vacío —
  // que es una forma distinta de mentir, por omisión.
  const stroke = !live
    ? "#8b93a1"
    : probability >= breakeven
      ? "#3cd9a8"
      : "#f07045";

  return (
    <svg viewBox="0 0 140 78" className="h-[78px] w-[140px]" role="img">
      <path
        d={arc(0, 1)}
        fill="none"
        stroke="#1f2937"
        strokeWidth={10}
        strokeLinecap="round"
      />
      <path
        d={arc(0, probability)}
        fill="none"
        stroke={stroke}
        strokeWidth={10}
        strokeLinecap="round"
      />
      {/* El listón a superar: una marca radial que cruza el riel entero, para
          que se lea como umbral y no como un pelo suelto al costado. */}
      <line
        x1={CX + (R - 8) * Math.cos(angle)}
        y1={CY + (R - 8) * Math.sin(angle)}
        x2={CX + (R + 8) * Math.cos(angle)}
        y2={CY + (R + 8) * Math.sin(angle)}
        stroke="#f5b731"
        strokeWidth={2.5}
        strokeLinecap="round"
      />
      <text
        x={CX}
        y={CY - 8}
        textAnchor="middle"
        className="fill-neutral-100 font-mono"
        fontSize={20}
      >
        {pct(probability, 1)}
      </text>
      <text
        x={CX}
        y={CY + 8}
        textAnchor="middle"
        className="fill-neutral-500 font-mono"
        fontSize={9}
      >
        equilibrio {pct(breakeven, 1)}
      </text>
    </svg>
  );
}

function GateLine({ metrics, gate }: { metrics: DirectionMetrics; gate: GateResponse }) {
  const t = gate.gate.thresholds;
  const auc = metrics.auc > t.min_auc;
  const bss = metrics.bss > t.min_bss;
  const cal = metrics.calibration_error_pp < t.max_calibration_error_pp;
  return (
    <div className="flex flex-wrap gap-1.5 text-[11px]">
      <Badge tone={auc ? "ok" : "bad"} title={`umbral > ${t.min_auc}`}>
        AUC {num(metrics.auc, 3)}
      </Badge>
      <Badge tone={bss ? "ok" : "bad"} title={`umbral > ${t.min_bss}`}>
        BSS {num(metrics.bss, 4)}
      </Badge>
      <Badge
        tone={cal ? "ok" : "bad"}
        title={`umbral < ${t.max_calibration_error_pp}pp`}
      >
        calib {num(metrics.calibration_error_pp, 1)}pp
      </Badge>
      <Badge tone="neutral" title="Diebold-Mariano contra la tasa base">
        DM p={pValue(metrics.dm_p)}
      </Badge>
    </div>
  );
}

export function SecurityGauge({
  probabilities,
  breakeven,
  calibrated,
  gate,
}: {
  probabilities: Record<string, number>;
  breakeven: Record<string, number>;
  calibrated: boolean;
  gate: GateResponse | undefined;
}) {
  const habilitado = calibrated && (gate?.gate.habilitado ?? false);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-baseline gap-2">
          <h2 className="text-xs uppercase tracking-wider text-neutral-500">
            KPI 1 — Seguridad
          </h2>
          <span className="text-[11px] text-neutral-600">
            P(TP antes que SL)
          </span>
        </div>
        <Badge tone={habilitado ? "ok" : "muted"}>
          {habilitado ? "habilitado" : "experimental — sin calibrar"}
        </Badge>
      </div>

      <div className="grid grid-cols-2 gap-3">
        {Object.entries(probabilities).map(([direction, probability]) => (
          <div key={direction} className="space-y-2">
            <div className="text-xs uppercase tracking-wider text-neutral-400">
              {direction}
            </div>
            <Dial
              probability={probability}
              breakeven={breakeven[direction] ?? 0.5}
              live={habilitado}
            />
            {gate?.directions[direction] && (
              <GateLine metrics={gate.directions[direction]} gate={gate} />
            )}
          </div>
        ))}
      </div>

      {/* La regla de honestidad: el número nunca sale solo. */}
      <p className="border-t border-neutral-800 pt-3 text-[11px] leading-relaxed text-neutral-500">
        {gate ? (
          <>
            No operable: el walk-forward purgado sobre {count(gate.n_bars)}{" "}
            barras ({gate.date_from} → {gate.date_to}, variante{" "}
            <span className="font-mono">{gate.variant}</span>/
            <span className="font-mono">{gate.vol_kind}</span>){" "}
            {gate.gate.calibra ? "calibra" : "no calibra"} pero{" "}
            {gate.gate.discrimina ? "discrimina" : "no discrimina"}. Hacen falta los
            dos. El número se registra con su feature vector para poder medirlo, y
            no se emite señal direccional contra él.
          </>
        ) : (
          "Sin artefacto del gate para esta configuración: el KPI no tiene respaldo medido y por eso no se muestra como operable."
        )}
      </p>
    </div>
  );
}
