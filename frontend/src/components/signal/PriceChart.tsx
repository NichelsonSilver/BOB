import {
  ColorType,
  LineStyle,
  createChart,
  type CandlestickData,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useMemo, useRef } from "react";

import { useLang } from "../../i18n";

import { SERIES_BY_ROLE, STATUS } from "../../lib/chartTheme";
import type { Candle, ConeBand, SetupProjection } from "../../types";

/**
 * Velas con los niveles del setup encima: entrada, TP, SL, liquidación y el
 * cono conformal del nivel elegido.
 *
 * La liquidación se dibuja con línea sólida y las demás punteadas, a
 * propósito. Para un usuario averso al riesgo que ya pasó por una
 * liquidación, ese nivel no es "un dato más del setup": es el único del que
 * no se vuelve. Cuando queda dentro del rango visible del chart, se ve como
 * lo que es.
 */

// Las velas y los niveles usan la paleta de ESTADO (long/short, TP/SL,
// alerta): acá el color significa dirección y consecuencia, no identidad de
// serie. El cono es lo único que es una serie, y toma el azul validado.
const COLORS = {
  up: STATUS.good,
  down: STATUS.bad,
  entry: "#a3a3a3",
  tp: STATUS.good,
  sl: STATUS.bad,
  liq: STATUS.warn,
  cone: SERIES_BY_ROLE.forecast,
};

export function PriceChart({
  candles,
  projection,
  cone,
  height = 340,
}: {
  candles: Candle[];
  projection: SetupProjection | null;
  cone: ConeBand | null;
  height?: number;
}) {
  const { lang } = useLang();
  const container = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const lines = useRef<IPriceLine[]>([]);

  const data = useMemo<CandlestickData[]>(
    () =>
      candles.map((c) => ({
        time: (c.open_time / 1000) as UTCTimestamp,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      })),
    [candles],
  );

  useEffect(() => {
    if (!container.current) return;
    const created = createChart(container.current, {
      height,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "#8a8a8a",
        fontFamily: "JetBrains Mono, Consolas, monospace",
      },
      grid: {
        vertLines: { color: "#1a1c1c" },
        horzLines: { color: "#1a1c1c" },
      },
      rightPriceScale: { borderColor: "#262626" },
      timeScale: { borderColor: "#262626", timeVisible: true },
      crosshair: { mode: 1 },
    });
    const candleSeries = created.addCandlestickSeries({
      upColor: COLORS.up,
      downColor: COLORS.down,
      borderUpColor: COLORS.up,
      borderDownColor: COLORS.down,
      wickUpColor: COLORS.up,
      wickDownColor: COLORS.down,
    });
    chart.current = created;
    series.current = candleSeries;

    const resize = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width;
      if (width) created.applyOptions({ width });
    });
    resize.observe(container.current);

    return () => {
      resize.disconnect();
      created.remove();
      chart.current = null;
      series.current = null;
      lines.current = [];
    };
  }, [height]);

  useEffect(() => {
    if (!series.current || data.length === 0) return;
    series.current.setData(data);
    chart.current?.timeScale().fitContent();
  }, [data]);

  useEffect(() => {
    const target = series.current;
    if (!target) return;

    for (const line of lines.current) target.removePriceLine(line);
    lines.current = [];

    const add = (
      price: number,
      color: string,
      title: string,
      style: LineStyle,
      width: 1 | 2,
    ) => {
      lines.current.push(
        target.createPriceLine({
          price,
          color,
          lineWidth: width,
          lineStyle: style,
          axisLabelVisible: true,
          title,
        }),
      );
    };

    if (projection) {
      add(projection.entry_price, COLORS.entry, lang === "en" ? "entry" : "entrada", LineStyle.Dotted, 1);
      add(projection.take_profit, COLORS.tp, "TP", LineStyle.Dashed, 1);
      add(projection.stop_loss, COLORS.sl, "SL", LineStyle.Dashed, 1);
      // Sólida y más gruesa: es el nivel del que no se vuelve.
      add(
        projection.liquidation_price,
        COLORS.liq,
        `liq ${projection.leverage}x`,
        LineStyle.Solid,
        2,
      );
    }
    if (cone) {
      const label = `${lang === "en" ? "cone" : "cono"} ${(cone.nominal * 100).toFixed(0)}%`;
      add(cone.price_hi, COLORS.cone, `${label} ↑`, LineStyle.LargeDashed, 1);
      add(cone.price_lo, COLORS.cone, `${label} ↓`, LineStyle.LargeDashed, 1);
    }
  }, [projection, cone, lang]);

  return <div ref={container} className="w-full" style={{ height }} />;
}
