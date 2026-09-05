import { useQuery } from "@tanstack/react-query";

import { api } from "../lib/api";
import { useAppStore } from "../stores/appStore";

/**
 * Qué símbolo y timeframe está mirando el usuario.
 *
 * La preferencia guardada gana; si no hay ninguna, mandan los defaults del
 * backend. Ese orden importa: hardcodear "ETHUSDT/15m" en el frontend haría
 * que cambiar la watchlist del `.env` no se notara en el dashboard, y el
 * proyecto es explícitamente agnóstico del símbolo.
 */
export function useTarget() {
  const symbolPref = useAppStore((s) => s.symbol);
  const timeframePref = useAppStore((s) => s.timeframe);

  const settings = useQuery({
    queryKey: ["settings"],
    queryFn: api.settings,
    staleTime: 60_000,
  });

  const watchlist = settings.data?.runtime.watchlist ?? [];
  const timeframes = settings.data?.runtime.known_timeframes ?? [];

  return {
    symbol: symbolPref ?? watchlist[0] ?? undefined,
    timeframe: timeframePref ?? settings.data?.runtime.default_timeframe ?? undefined,
    watchlist,
    timeframes,
    settings: settings.data,
    isLoading: settings.isLoading,
    error: settings.error,
  };
}
