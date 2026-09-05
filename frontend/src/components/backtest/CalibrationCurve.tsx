import {
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";

import { INK, SERIES_BY_ROLE, TOOLTIP_STYLE } from "../../lib/chartTheme";
import { count, pct } from "../../lib/format";
import type { CalibrationBucket } from "../../types";

/**
 * La curva de fiabilidad: lo que el modelo dijo contra lo que pasó.
 *
 * Es el gráfico central de la página de Backtest porque es el que responde la
 * pregunta del proyecto: "cuando BOB dice 70%, ¿acierta 70%?". La diagonal es
 * la calibración perfecta; cada punto es un bucket, y el **tamaño del punto es
 * su n**. Sin eso la curva engaña: un bucket con 4 casos se dibuja igual de
 * grande que uno con 20.000 y la vista se va detrás del ruido.
 *
 * Un solo eje y una sola escala: los dos son probabilidades en [0,1].
 *
 * Lo que esta curva NO puede decir es si el modelo sirve. Un clasificador que
 * predice siempre la tasa base cae exactamente sobre la diagonal y no
 * distingue nada. Por eso el gate pide los dos criterios y esta página muestra
 * el AUC al lado.
 */
export function CalibrationCurve({
  buckets,
  baseRate,
}: {
  buckets: CalibrationBucket[];
  baseRate: number;
}) {
  const data = buckets.map((b) => ({
    predicted: b.mean_predicted,
    observed: b.observed_rate,
    n: b.n,
    error_pp: b.error_pp,
  }));

  const diagonal = [
    { predicted: 0, ideal: 0 },
    { predicted: 1, ideal: 1 },
  ];

  return (
    <ResponsiveContainer width="100%" height={260}>
      <ComposedChart margin={{ top: 8, right: 12, bottom: 18, left: 4 }}>
        <CartesianGrid stroke={INK.grid} strokeDasharray="2 4" />
        <XAxis
          type="number"
          dataKey="predicted"
          domain={[0, 1]}
          tickFormatter={(v: number) => pct(v, 0)}
          stroke={INK.axis}
          tick={{ fill: INK.muted, fontSize: 10 }}
          label={{
            value: "probabilidad predicha",
            position: "insideBottom",
            offset: -10,
            fill: INK.muted,
            fontSize: 11,
          }}
        />
        <YAxis
          type="number"
          dataKey="observed"
          domain={[0, 1]}
          tickFormatter={(v: number) => pct(v, 0)}
          stroke={INK.axis}
          tick={{ fill: INK.muted, fontSize: 10 }}
          width={48}
        />
        <ZAxis type="number" dataKey="n" range={[30, 400]} name="n" />

        {/* Calibración perfecta */}
        <Line
          data={diagonal}
          dataKey="ideal"
          stroke={INK.axis}
          strokeDasharray="4 4"
          strokeWidth={1}
          dot={false}
          isAnimationActive={false}
          legendType="none"
        />
        {/* La tasa base: donde caería un modelo que no distingue nada */}
        <ReferenceLine
          y={baseRate}
          stroke={SERIES_BY_ROLE.reference}
          strokeDasharray="2 3"
          label={{
            value: `tasa base ${pct(baseRate, 1)}`,
            fill: SERIES_BY_ROLE.reference,
            fontSize: 10,
            position: "insideBottomRight",
          }}
        />
        <Tooltip
          {...TOOLTIP_STYLE}
          cursor={{ stroke: INK.axis }}
          formatter={(value, name) => {
            const v = typeof value === "number" ? value : null;
            if (name === "n") return [count(v), "casos"];
            return [pct(v, 1), name === "observed" ? "observado" : "predicho"];
          }}
        />
        <Scatter
          data={data}
          fill={SERIES_BY_ROLE.forecast}
          fillOpacity={0.75}
          stroke={INK.surface}
          strokeWidth={2}
          isAnimationActive={false}
        />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
