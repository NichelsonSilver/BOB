import { useQuery } from "@tanstack/react-query";

import { Card, Stat } from "../components/common/Card";
import { api } from "../lib/api";

export function SettingsPage() {
  const settings = useQuery({
    queryKey: ["settings"],
    queryFn: api.getSettings,
  });
  const health = useQuery({
    queryKey: ["health-grvt"],
    queryFn: api.healthGrvt,
    refetchInterval: 15_000,
  });

  return (
    <div className="grid max-w-4xl gap-4">
      <h1 className="text-2xl font-semibold">Settings</h1>
      <p className="text-sm text-neutral-400">
        Credenciales y cuenta se configuran en{" "}
        <code className="rounded bg-neutral-800 px-1">backend/.env</code>.
        Esta página es solo lectura.
      </p>

      <Card title="Entorno">
        {settings.isLoading && <div>Cargando…</div>}
        {settings.error && (
          <div className="text-danger">
            Error: {(settings.error as Error).message}
          </div>
        )}
        {settings.data && (
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            <Stat label="Env" value={settings.data.env} />
            <Stat
              label="Trading account"
              value={
                <span className="text-sm break-all">
                  {settings.data.trading_account_id}
                </span>
              }
            />
            <Stat
              label="Bots activos"
              value={`${settings.data.runtime.active_bots} / ${settings.data.runtime.total_bots}`}
            />
          </div>
        )}
      </Card>

      <Card title="Límites globales">
        {settings.data && (
          <div className="grid grid-cols-3 gap-4">
            <Stat
              label="Max capital"
              value={`${settings.data.limits.max_total_capital} USDT`}
            />
            <Stat
              label="Max bots"
              value={settings.data.limits.max_concurrent_bots}
            />
            <Stat
              label="Max leverage"
              value={`${settings.data.limits.max_leverage}x`}
            />
          </div>
        )}
      </Card>

      <Card title="GRVT health">
        <pre className="overflow-auto text-xs text-neutral-300">
          {JSON.stringify(health.data ?? { loading: true }, null, 2)}
        </pre>
      </Card>
    </div>
  );
}
