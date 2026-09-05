import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { INK, SERIES_BY_ROLE, TOOLTIP_STYLE } from "../../lib/chartTheme";
import { pct, stamp } from "../../lib/format";
import type { ForecastRow } from "../../types";

/**
 * La evolución in-live, barra a barra: sigma pronosticada contra sigma
 * realizada.
 *
 * CLAUDE.md pedía acá el timeline del KPI de Seguridad. Se dibuja **este** en
 * su lugar por la decisión del 2026-08-25: el KPI 1 no pasó discriminación,
 * así que una serie de su evolución sería una línea que se mueve mucho y no
 * mide nada. La sigma sí pasó el gate y sigue pasándolo forward, y su
 * seguimiento responde la pregunta que importa mientras hay un trade abierto:
 * ¿el movimiento que viene se parece al que el modelo dimensionó?
 *
 * Un solo eje, en porcentaje: las dos series son la misma magnitud. La
 * realizada tiene huecos donde el horizonte todavía no cerró, y esos huecos
 * se dejan como corte de la línea en vez de interpolarse — unir dos puntos a
 * través de un pronóstico sin resolver dibujaría un dato que no existe.
 */
export function SigmaTimeline({ rows }: { rows: ForecastRow[] }) {
  const data = [...rows]
    .sort((a, b) => a.open_time - b.open_time)
    .map((r) => ({
      open_time: r.open_time,
      pronosticada: r.sigma_forecast,
      realizada: r.realized_vol,
    }));

  if (data.length < 2) {
    return (
      <div className="py-8 text-center text-sm text-neutral-500">
        Hacen falta al menos dos pronósticos para dibujar la evolución.
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={200}>
      <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 4 }}>
        <CartesianGrid stroke={INK.grid} strokeDasharray="2 4" vertical={false} />
        <XAxis
          dataKey="open_time"
          tickFormatter={stamp}
          stroke={INK.axis}
          tick={{ fill: INK.muted, fontSize: 10 }}
          minTickGap={40}
        />
        <YAxis
          tickFormatter={(v: number) => pct(v, 1)}
          stroke={INK.axis}
          tick={{ fill: INK.muted, fontSize: 10 }}
          width={52}
        />
        <Tooltip
          {...TOOLTIP_STYLE}
          labelFormatter={(v: number) => stamp(v)}
          formatter={(value, name) => [
            pct(typeof value === "number" ? value : null, 3),
            String(name),
          ]}
        />
        <Legend
          wrapperStyle={{ fontSize: 11, color: INK.muted }}
          iconType="plainline"
        />
        <Line
          type="monotone"
          dataKey="pronosticada"
          name="sigma pronosticada"
          stroke={SERIES_BY_ROLE.forecast}
          strokeWidth={2}
          dot={false}
          isAnimationActive={false}
        />
        <Line
          type="monotone"
          dataKey="realizada"
          name="sigma realizada"
          stroke={SERIES_BY_ROLE.realized}
          strokeWidth={2}
          dot={false}
          // Sin `connectNulls`: un horizonte que no cerró es un hueco, y unir
          // la línea a través de él inventaría una medición.
          connectNulls={false}
          isAnimationActive={false}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
