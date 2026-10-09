import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceArea,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { INK, SERIES, STATUS, TOOLTIP_STYLE } from "../../lib/chartTheme";
import { isNum } from "../../lib/format";

/**
 * Gráficos de la portada (M6.2). Dos piezas:
 *
 * - `Spark`: la línea chica de cada tarjeta en la variante A. Sin ejes: lo que
 *   se lee es la forma del último tramo. El rango que cubre va escrito debajo,
 *   porque una línea sin período no se puede leer.
 * - `LiquidityChart`: el gráfico grande de la variante B, con ejes.
 *
 * Un solo color de serie (`SERIES[0]`). Las bandas del compuesto de liquidez
 * usan los colores de estado con opacidad mínima, igual que en Liquidez, para
 * que la misma banda signifique lo mismo en las dos páginas.
 */

// `object` y no un Record: las filas traen campos anidados que el gráfico ignora.
type Point = object;

export function Spark({
  data,
  x,
  y,
  domain,
  span,
  bands = false,
}: {
  data: Point[];
  x: string;
  y: string;
  /** Escala fija cuando la medida la tiene (0–1); si no, la del tramo. */
  domain?: [number, number];
  /** Texto del período: "último año", "últimas 52 semanas". */
  span: string;
  /** Tercios del compuesto de liquidez (débil / normal / fuerte). */
  bands?: boolean;
}) {
  const val = (d: Point, k: string) => (d as Record<string, unknown>)[k];
  const pts = data.filter((d) => isNum(val(d, y) as number | null));
  if (pts.length < 2) return null;
  const first = String(val(pts[0], x)).slice(0, 7);
  return (
    <div>
      <ResponsiveContainer width="100%" height={56}>
        <LineChart data={pts} margin={{ top: 4, right: 4, bottom: 4, left: 4 }}>
          {bands && <ReferenceArea y1={2 / 3} y2={1} fill={STATUS.good} fillOpacity={0.07} />}
          {bands && <ReferenceArea y1={0} y2={1 / 3} fill={STATUS.bad} fillOpacity={0.07} />}
          <YAxis hide domain={domain ?? ["auto", "auto"]} />
          <XAxis hide dataKey={x} />
          <Line
            type="monotone"
            dataKey={y}
            stroke={SERIES[0]}
            strokeWidth={1.5}
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
      <div className="flex justify-between text-[10px] text-neutral-600">
        <span>{first}</span>
        <span>{span}</span>
        <span>hoy</span>
      </div>
    </div>
  );
}

const AXIS = { stroke: INK.axis, tick: { fill: INK.muted, fontSize: 10 } };

const BAND_NAME = (v: number) => (v >= 2 / 3 ? "fuerte" : v < 1 / 3 ? "débil" : "normal");

export function LiquidityChart({
  data,
  height = 220,
}: {
  data: { date: string; composite_pct: number | null }[];
  height?: number;
}) {
  if (data.length < 2) return null;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 4 }}>
        <ReferenceArea y1={2 / 3} y2={1} fill={STATUS.good} fillOpacity={0.06} />
        <ReferenceArea y1={0} y2={1 / 3} fill={STATUS.bad} fillOpacity={0.06} />
        <CartesianGrid stroke={INK.grid} strokeDasharray="2 4" vertical={false} />
        <XAxis dataKey="date" {...AXIS} minTickGap={60} tickFormatter={(d: string) => d.slice(0, 7)} />
        <YAxis
          domain={[0, 1]}
          ticks={[1 / 6, 0.5, 5 / 6]}
          tickFormatter={(v: number) => BAND_NAME(v)}
          {...AXIS}
          width={48}
        />
        <Tooltip
          {...TOOLTIP_STYLE}
          formatter={(value) => {
            const v = typeof value === "number" ? value : null;
            return [isNum(v) ? `${Math.floor(v * 100)} / 100 (${BAND_NAME(v)})` : "—", "liquidez"];
          }}
        />
        <Line
          type="stepAfter"
          dataKey="composite_pct"
          stroke={SERIES[0]}
          strokeWidth={1.5}
          dot={false}
          connectNulls={false}
          isAnimationActive={false}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
