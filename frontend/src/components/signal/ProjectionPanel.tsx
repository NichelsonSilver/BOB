import { Badge, Row, Warning } from "../common/Card";
import {
  multiplier,
  num,
  pct,
  price,
  signClass,
  sigmas,
  signedPct,
} from "../../lib/format";
import type { SetupProjection } from "../../types";

/**
 * KPI 2 — la proyección. Es el panel que sostiene el producto desde la
 * decisión del 2026-08-25: se apoya en el target de volatilidad, que sí pasó
 * el gate.
 *
 * **El EV siempre sale con su probabilidad de equilibrio al lado.** Es un
 * hallazgo de la Fase 5 con consecuencia de diseño, no una preferencia: para
 * un camino sin deriva con barreras en +a y −b, P(tocar arriba primero) =
 * b/(a+b), y entonces el EV bruto es exactamente 0 para *todo* a y b — el
 * neto es −costo. Mover el TP o cambiar el ratio R:B no lo levanta: reordena
 * probabilidad y pago en la proporción exacta que deja el bruto en cero.
 * Mostrar el EV solo lo convertiría en una promesa; mostrarlo contra el
 * equilibrio lo deja en lo que es, un listón que hay que superar.
 *
 * Lo que sí entrega valor operativo, y por eso ocupa la mitad del panel:
 * barreras escaladas a la volatilidad que viene, distancia a liquidación en
 * sigmas y leverage máximo seguro.
 */
export function ProjectionPanel({
  projection,
  leverage,
  onLeverage,
  maxLeverage,
}: {
  projection: SetupProjection;
  leverage: number;
  onLeverage: (v: number) => void;
  maxLeverage: number;
}) {
  const p = projection;
  const noLiquidation = p.liquidation_price <= 0;
  const liqTone = p.liquidation_before_stop
    ? "text-danger"
    : p.liq_distance_sigmas < 3
      ? "text-warn"
      : "text-neutral-200";

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-xs uppercase tracking-wider text-neutral-500">
          KPI 2 — Proyección
        </h2>
        <Badge tone={p.is_actionable ? "ok" : "muted"}>
          {p.is_actionable ? "EV positivo" : "EV no positivo"}
        </Badge>
      </div>

      {/* --- Niveles ---------------------------------------------------- */}
      <div className="grid grid-cols-3 gap-2 text-center">
        <Level label="Stop loss" value={price(p.stop_loss)} sub={pct(-p.sl_pct)} tone="text-danger" />
        <Level label="Entrada" value={price(p.entry_price)} sub={`R:B ${num(p.risk_reward, 2)}`} />
        <Level label="Take profit" value={price(p.take_profit)} sub={signedPct(p.tp_pct)} tone="text-accent" />
      </div>

      {/* --- EV contra el equilibrio ------------------------------------ */}
      <div className="rounded border border-neutral-800 bg-neutral-950/60 p-3">
        <div className="flex items-end justify-between gap-4">
          <div>
            <div className="text-xs uppercase tracking-wider text-neutral-500">
              EV neto (sin leverage)
            </div>
            <div className={`font-mono text-2xl ${signClass(p.net_ev_pct)}`}>
              {signedPct(p.net_ev_pct, 3)}
            </div>
          </div>
          <div className="text-right">
            <div className="text-xs uppercase tracking-wider text-neutral-500">
              hace falta acertar
            </div>
            <div className="font-mono text-lg text-warn">
              {pct(p.breakeven_probability, 1)}
            </div>
            <div className="font-mono text-[11px] text-neutral-500">
              el modelo dice {pct(p.probability, 1)} ({num(p.edge_pp, 1)}pp)
            </div>
          </div>
        </div>
        <div className="mt-2 border-t border-neutral-800 pt-2">
          <Row label="EV bruto" value={signedPct(p.gross_ev_pct, 3)} />
          <Row
            label="costo (fees + slippage + funding)"
            value={`−${pct(p.cost_pct, 3)}`}
            hint="entra en el etiquetado, no se descuenta después"
          />
          <Row
            label="horizonte"
            value={`${p.horizon_bars} barras · ${num(p.horizon_hours, 1)} h`}
          />
          <Row
            label="sigma del horizonte"
            value={pct(p.sigma_horizon, 3)}
            hint="pronosticada por el modelo que sí pasó el gate"
          />
        </div>
      </div>

      {/* --- Leverage y liquidación ------------------------------------- */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <label
            htmlFor="leverage"
            className="text-xs uppercase tracking-wider text-neutral-500"
          >
            Leverage
          </label>
          <span className="font-mono text-sm text-neutral-200">
            {multiplier(leverage, 0)}
          </span>
        </div>
        <input
          id="leverage"
          type="range"
          min={1}
          max={20}
          step={1}
          value={leverage}
          onChange={(e) => onLeverage(Number(e.target.value))}
          className="w-full accent-[#3cd9a8]"
        />
        <div className="flex justify-between font-mono text-[10px] text-neutral-600">
          <span>1x</span>
          <span>
            máx seguro {multiplier(p.max_safe_leverage)} · venue{" "}
            {multiplier(maxLeverage, 0)}
          </span>
          <span>20x</span>
        </div>
      </div>

      <div className="rounded border border-neutral-800 bg-neutral-950/60 p-3">
        <Row
          label="precio de liquidación"
          value={
            // A 1x con margen aislado la fórmula da exactamente 0: un long
            // solo se liquida si el precio llega a cero. Mostrar "0.00" es
            // correcto y se lee como un dato faltante, que es peor que no
            // mostrarlo — el usuario es averso al riesgo y este es el número
            // que vino a buscar.
            noLiquidation ? "no alcanzable" : price(p.liquidation_price)
          }
          tone={noLiquidation ? "text-accent" : liqTone}
          hint={
            noLiquidation
              ? "a este leverage la posición solo se liquidaría con el precio en cero (margen aislado)"
              : undefined
          }
        />
        <Row
          label="distancia a liquidación"
          value={
            noLiquidation
              ? "todo el precio"
              : `${pct(p.liq_distance_pct)} · ${sigmas(p.liq_distance_sigmas)}`
          }
          tone={noLiquidation ? "text-accent" : liqTone}
          hint="en sigmas del horizonte: cuántos movimientos típicos caben antes"
        />
        <Row
          label="P(tocar liquidación)"
          value={pct(p.prob_touch_liquidation, 3)}
          tone={liqTone}
        />
        <Row label="margen inicial" value={pct(p.margin_pct, 1)} />
        <Row
          label="ROE esperado"
          value={signedPct(p.roe_pct, 3)}
          tone={signClass(p.roe_pct)}
        />
        <Row
          label="ROE en TP / en SL"
          value={`${signedPct(p.roe_tp_pct)} / ${signedPct(p.roe_sl_pct)}`}
        />
      </div>

      {p.warnings.length > 0 && (
        <div className="space-y-2">
          {p.warnings.map((w) => (
            <Warning key={w}>{w}</Warning>
          ))}
        </div>
      )}
    </div>
  );
}

function Level({
  label,
  value,
  sub,
  tone = "text-neutral-100",
}: {
  label: string;
  value: string;
  sub: string;
  tone?: string;
}) {
  return (
    <div className="rounded border border-neutral-800 bg-neutral-950/60 px-2 py-2">
      <div className="text-[10px] uppercase tracking-wider text-neutral-500">
        {label}
      </div>
      <div className={`font-mono text-sm ${tone}`}>{value}</div>
      <div className="font-mono text-[11px] text-neutral-500">{sub}</div>
    </div>
  );
}
