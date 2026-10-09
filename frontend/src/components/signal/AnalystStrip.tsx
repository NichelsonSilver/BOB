import { Badge } from "../common/Card";
import { type Lang, useLang } from "../../i18n";
import { count, num, stamp } from "../../lib/format";
import type { AnalystStatus, Forecast } from "../../types";

/**
 * La franja que responde "¿esto es de ahora?".
 *
 * Existe por los tres bugs de frescura de la Fase 5. Ninguno hacía caer al
 * backend: en los tres el dashboard habría mostrado un número correcto de
 * otro momento —el analista emitía una vez por día en vez de 96, o una barra
 * sí y otra no, o nada desde un hueco de dos velas— y desde afuera se veía
 * igual que un backend sano. Un backend en verde que no produce nada.
 *
 * Por eso acá no alcanza con "conectado": se muestra la edad del último
 * pronóstico en barras, si el modelo está ajustado o reajustando, cuántas
 * barras pasaron desde el ajuste, y si quedó pendiente una reparación de
 * velas — que es la diferencia entre "un hueco que se arregla solo en la
 * próxima barra" y "Binance dejó de mandar datos".
 */
export function AnalystStrip({
  forecast,
  analyst,
  liveEnabled,
}: {
  forecast: Forecast | null;
  analyst: AnalystStatus | null;
  liveEnabled: boolean;
}) {
  const { tr } = useLang();
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-neutral-800 bg-neutral-900/40 px-3 py-2">
      {forecast ? (
        <Badge tone={forecast.is_stale ? "warn" : "ok"}>
          {forecast.is_stale
            ? tr(`late forecast · ${forecast.age_bars} bars`, `pronóstico atrasado · ${forecast.age_bars} barras`)
            : tr("forecast up to date", "pronóstico al día")}
        </Badge>
      ) : (
        <Badge tone="warn">{tr("no forecasts", "sin pronósticos")}</Badge>
      )}

      {forecast && (
        <span className="font-mono text-[11px] text-neutral-500">
          {tr("bar", "barra")} {stamp(forecast.open_time)} · {tr("issued", "emitido")}{" "}
          {stamp(Date.parse(forecast.emitted_at))}
        </span>
      )}

      <span className="flex-1" />

      {analyst ? (
        <>
          {!analyst.fitted && <Badge tone="warn">{tr("fitting (~80s)", "ajustando (~80s)")}</Badge>}
          {analyst.refitting && <Badge tone="neutral">{tr("refitting", "reajustando")}</Badge>}
          {analyst.candle_repair_pending && (
            <Badge
              tone="warn"
              title={tr(
                "the candle repair ran out of network; it is retried on the next closed bar",
                "la reparación de velas se quedó sin red; se reintenta en la próxima barra cerrada",
              )}
            >
              {tr("repair pending", "reparación pendiente")}
            </Badge>
          )}
          <span className="font-mono text-[11px] text-neutral-500">
            {analyst.feature_set}/{analyst.vol_kind} ·{" "}
            {tr(
              `${count(analyst.n_train)} bars · ${analyst.bars_since_fit} since the fit`,
              `${count(analyst.n_train)} barras · ${analyst.bars_since_fit} desde el ajuste`,
            )}
          </span>
          <span
            className="font-mono text-[11px] text-neutral-600"
            title={tr("version of the model that issued this forecast", "versión del modelo que emitió este pronóstico")}
          >
            {analyst.model_version}
          </span>
        </>
      ) : (
        <Badge tone="muted">
          {liveEnabled
            ? tr("analyst not available in this process", "analista no disponible en este proceso")
            : tr("analyst off (offline mode)", "analista apagado (modo offline)")}
        </Badge>
      )}
    </div>
  );
}

/** Cobertura medida del cono, con su intervalo. Nunca el punto solo. */
export function coverageLabel(
  empirical: number | null | undefined,
  ci: [number, number] | undefined,
  lang: Lang = "es",
): string {
  if (empirical == null) return "—";
  const point = `${(empirical * 100).toFixed(1)}%`;
  if (!ci) return point;
  return `${point} · ${lang === "en" ? "95% CI" : "IC95"} [${num(ci[0] * 100, 1)}, ${num(ci[1] * 100, 1)}]`;
}
