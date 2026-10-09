import { useAppStore } from "../../stores/appStore";
import { useTarget } from "../../hooks/useTarget";
import { useLang } from "../../i18n";

/**
 * Selector de símbolo y timeframe. Vive en el layout porque la elección es
 * transversal: cambiar de par en Signal y volver a encontrar History mirando
 * otro sería desorientador.
 *
 * Las opciones salen de `/api/settings` (watchlist y timeframes conocidos) y
 * no de una lista en el código: el proyecto es agnóstico del símbolo y el
 * dashboard tiene que enterarse de un cambio en el `.env` sin recompilarse.
 */
export function TargetBar() {
  const { tr } = useLang();
  const { watchlist, timeframes, symbol, timeframe } = useTarget();
  const setSymbol = useAppStore((s) => s.setSymbol);
  const setTimeframe = useAppStore((s) => s.setTimeframe);

  const select =
    "rounded border border-neutral-800 bg-neutral-900 px-2 py-1 font-mono text-xs text-neutral-200 focus:border-neutral-600 focus:outline-none";

  return (
    <div className="flex items-center gap-2">
      <select
        aria-label={tr("symbol", "símbolo")}
        className={select}
        value={symbol ?? ""}
        onChange={(e) => setSymbol(e.target.value || null)}
      >
        {watchlist.length === 0 && <option value="">—</option>}
        {watchlist.map((s) => (
          <option key={s} value={s}>
            {s}
          </option>
        ))}
      </select>
      <select
        aria-label="timeframe"
        className={select}
        value={timeframe ?? ""}
        onChange={(e) => setTimeframe(e.target.value || null)}
      >
        {timeframes.length === 0 && <option value="">—</option>}
        {timeframes.map((t) => (
          <option key={t} value={t}>
            {t}
          </option>
        ))}
      </select>
    </div>
  );
}
