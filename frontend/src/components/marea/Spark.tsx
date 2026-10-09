import {
  Line,
  LineChart,
  ReferenceArea,
  ResponsiveContainer,
  XAxis,
  YAxis,
} from "recharts";

import { SERIES, STATUS } from "../../lib/chartTheme";
import { isNum } from "../../lib/format";

/**
 * La línea chica de cada tarjeta de la portada (M6.2). Sin ejes: lo que se lee
 * es la forma del último tramo. El rango que cubre va escrito debajo, porque
 * una línea sin período no se puede leer.
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
