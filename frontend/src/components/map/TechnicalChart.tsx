import {
  ColorType,
  LineStyle,
  createChart,
  type IChartApi,
  type SeriesMarker,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef } from "react";

import { INK, SERIES, STATUS } from "../../lib/chartTheme";
import type { TechnicalResponse } from "../../types";

/**
 * Velas con el mapa técnico encima: medias, zonas de liquidez, soportes y
 * resistencias, order blocks vivos y los barridos de la ventana.
 *
 * Decisiones de color:
 *
 * - **Medias = paleta de series** (`SERIES`), en orden fijo 20/50/200. Son
 *   tres identidades dentro del gráfico, no estados.
 * - **Zonas = gris, no verde ni rojo.** Liquidez, S/R y barridos no tienen
 *   dirección validada (el backtest no pasó). Pintarlos con los colores de
 *   long/short sería dibujar una señal que no existe.
 * - **Order blocks = verde/rojo tenue**, porque su dirección es parte de su
 *   definición (zona de demanda u oferta), no un pronóstico. Van tenues para
 *   que no se lean como TP/SL.
 * - **Los barridos van como puntos, no como flechas.** Una flecha en un chart
 *   de trading se lee "entrá acá", y la regla midió −0,35 R por trade.
 */

const ZONE = "#9a9d9d";
const SR = "#6b6f6f";

const ts = (ms: number) => (ms / 1000) as UTCTimestamp;

function baseChart(el: HTMLDivElement, height: number): IChartApi {
  return createChart(el, {
    height,
    layout: {
      background: { type: ColorType.Solid, color: "transparent" },
      textColor: INK.muted,
      fontFamily: "JetBrains Mono, Consolas, monospace",
    },
    grid: { vertLines: { color: INK.grid }, horzLines: { color: INK.grid } },
    rightPriceScale: { borderColor: "#262626" },
    timeScale: { borderColor: "#262626", timeVisible: true },
    crosshair: { mode: 1 },
  });
}

/** Ajusta el ancho al contenedor; devuelve la limpieza. */
function followWidth(el: HTMLDivElement, chart: IChartApi): () => void {
  const ro = new ResizeObserver((entries) => {
    const width = entries[0]?.contentRect.width;
    if (width) chart.applyOptions({ width });
  });
  ro.observe(el);
  return () => ro.disconnect();
}

export function TechnicalChart({
  data,
  height = 420,
}: {
  data: TechnicalResponse;
  height?: number;
}) {
  const el = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!el.current) return;
    const chart = baseChart(el.current, height);
    const s = data.series;

    const candles = chart.addCandlestickSeries({
      upColor: STATUS.good,
      downColor: STATUS.bad,
      borderUpColor: STATUS.good,
      borderDownColor: STATUS.bad,
      wickUpColor: STATUS.good,
      wickDownColor: STATUS.bad,
    });
    candles.setData(
      s.time.map((t, i) => ({
        time: ts(t),
        open: s.open[i],
        high: s.high[i],
        low: s.low[i],
        close: s.close[i],
      })),
    );

    (["ema20", "ema50", "ema200"] as const).forEach((key, k) => {
      const line = chart.addLineSeries({
        color: SERIES[k],
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      line.setData(s.time.map((t, i) => ({ time: ts(t), value: s[key][i] })));
    });

    // Order blocks: dos bordes desde que nacieron hasta la última barra.
    const first = s.time[0];
    const last = s.time[s.time.length - 1];
    for (const ob of data.order_blocks) {
      const color = (ob.direction === "long" ? STATUS.good : STATUS.bad) + "aa";
      const from = Math.max(ob.candle_time, first);
      if (from >= last) continue;
      for (const price of [ob.top, ob.bottom]) {
        const edge = chart.addLineSeries({
          color,
          lineWidth: 2,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
        edge.setData([
          { time: ts(from), value: price },
          { time: ts(last), value: price },
        ]);
      }
    }

    for (const p of data.pools) {
      candles.createPriceLine({
        price: p.price,
        color: ZONE,
        lineWidth: p.equal >= 2 ? 2 : 1,
        lineStyle: LineStyle.Dotted,
        axisLabelVisible: false,
        title: `liq ${p.side === "high" ? "↑" : "↓"}${p.equal >= 2 ? ` ×${p.equal}` : ""}`,
      });
    }
    for (const z of data.sr) {
      candles.createPriceLine({
        price: z.price,
        color: SR,
        lineWidth: 1,
        lineStyle: LineStyle.Solid,
        axisLabelVisible: false,
        title: `${z.kind === "support" ? "S" : "R"} ${z.touches}t`,
      });
    }

    const markers: SeriesMarker<UTCTimestamp>[] = data.sweeps.map((sw) => ({
      time: ts(sw.time),
      position: sw.direction === "short" ? "aboveBar" : "belowBar",
      color: ZONE,
      shape: "circle",
      // Sin texto: con varios barridos cerca, las etiquetas se pisan. La
      // leyenda dice qué es el punto.
      size: 0.6,
    }));
    candles.setMarkers(markers.sort((a, b) => a.time - b.time));

    chart.timeScale().fitContent();
    const unfollow = followWidth(el.current, chart);
    return () => {
      unfollow();
      chart.remove();
    };
  }, [data, height]);

  return <div ref={el} className="w-full" style={{ height }} />;
}

/** RSI en su propio gráfico: una sola escala por eje (chartTheme.ts). */
export function RsiChart({ data, height = 120 }: { data: TechnicalResponse; height?: number }) {
  const el = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!el.current) return;
    const chart = baseChart(el.current, height);
    const line = chart.addLineSeries({
      color: INK.text,
      lineWidth: 1,
      priceLineVisible: false,
      // Escala fija 0–100: autoescalada, una semana tranquila hace ver un
      // RSI de 55 como un extremo.
      autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }),
    });
    const pts: { time: UTCTimestamp; value: number }[] = [];
    data.series.time.forEach((t, i) => {
      const v = data.series.rsi[i];
      if (v !== null) pts.push({ time: ts(t), value: v });
    });
    line.setData(pts);
    for (const lvl of [30, 70]) {
      line.createPriceLine({
        price: lvl,
        color: SR,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: "",
      });
    }
    chart.timeScale().fitContent();
    const unfollow = followWidth(el.current, chart);
    return () => {
      unfollow();
      chart.remove();
    };
  }, [data, height]);

  return <div ref={el} className="w-full" style={{ height }} />;
}
