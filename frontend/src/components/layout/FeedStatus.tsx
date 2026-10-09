import { useLang } from "../../i18n";
import { useAppStore } from "../../stores/appStore";

/**
 * Estado del socket del dashboard y del feed de Binance (regla 8).
 *
 * Hasta la M6 vivía en el encabezado de todas las páginas. Con el vivo de 15m
 * congelado, el feed sale apagado siempre, y mostrarlo arriba de Liquidez o de
 * Flujos solo decía "algo está caído" donde nada lo está. Ahora vive en la
 * única página que lo usa: la Señal 15m.
 */
export function FeedStatus() {
  const { tr } = useLang();
  const wsConnected = useAppStore((s) => s.wsConnected);
  const conn = useAppStore((s) => s.connStatus);
  const feedDown = conn !== null && !conn.connected;
  return (
    <div className="flex flex-wrap items-center gap-4 text-xs">
      <span className="flex items-center gap-2">
        <span
          className={`inline-flex h-2 w-2 rounded-full ${wsConnected ? "bg-success" : "bg-danger"}`}
          aria-hidden
        />
        <span className="text-neutral-400">
          {wsConnected ? "dashboard: live" : tr("dashboard: disconnected", "dashboard: desconectado")}
        </span>
      </span>
      {conn ? (
        <span
          className="flex items-center gap-2"
          title={
            conn.mute_streams.length > 0
              ? tr(
                  `muted streams: ${conn.mute_streams.join(", ")} — filled in via REST`,
                  `streams mudos: ${conn.mute_streams.join(", ")} — se rellenan por REST`,
                )
              : undefined
          }
        >
          <span
            className={`inline-flex h-2 w-2 rounded-full ${feedDown ? "bg-danger" : "bg-success"}`}
            aria-hidden
          />
          <span className="font-mono text-neutral-500">{conn.source}</span>
          {conn.reconnects > 0 && (
            <span className="text-neutral-600">
              {conn.reconnects} {tr("reconnections", "reconexiones")}
            </span>
          )}
        </span>
      ) : (
        <span className="text-neutral-500">
          {tr("Binance feed: off (live module frozen)", "feed de Binance: apagado (vivo congelado)")}
        </span>
      )}
      {conn?.last_error && (
        <span className="text-warn" title={conn.last_error}>
          {tr("last error", "último error")}: {conn.last_error.slice(0, 60)}
        </span>
      )}
    </div>
  );
}
