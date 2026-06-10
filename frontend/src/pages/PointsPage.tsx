import { useQuery } from "@tanstack/react-query";

import { Card, Stat } from "../components/common/Card";
import { api } from "../lib/api";

export function PointsPage() {
  const total = useQuery({ queryKey: ["points"], queryFn: api.getPoints });
  const byBot = useQuery({
    queryKey: ["points-by-bot"],
    queryFn: api.getPointsByBot,
  });

  const entries = Object.entries(byBot.data ?? {});

  return (
    <div className="grid gap-4">
      <div>
        <h1 className="text-2xl font-semibold">Points</h1>
        <p className="mt-1 text-sm text-neutral-500">
          Estimación local basada en volumen. Los números reales de GRVT se
          integran en Fase 9.
        </p>
      </div>

      <Card title="Total estimado">
        {total.data ? (
          <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
            <Stat
              label="Points"
              value={total.data.estimated_points}
              sub="volume / 10"
            />
            <Stat label="Volume" value={total.data.gross_volume} />
            <Stat label="Maker rebates" value={total.data.maker_rebates} />
            <Stat label="Taker fees" value={total.data.taker_fees} />
            <Stat label="Fills" value={total.data.fill_count} />
          </div>
        ) : (
          <div>Cargando…</div>
        )}
      </Card>

      <Card title="Por bot">
        {entries.length === 0 ? (
          <div className="py-4 text-sm text-neutral-500">Sin datos</div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wider text-neutral-500">
                <th className="px-2 py-2">bot</th>
                <th className="px-2 py-2 text-right">volume</th>
                <th className="px-2 py-2 text-right">rebates</th>
                <th className="px-2 py-2 text-right">fees</th>
                <th className="px-2 py-2 text-right">fills</th>
                <th className="px-2 py-2 text-right">points</th>
              </tr>
            </thead>
            <tbody className="font-mono">
              {entries.map(([botId, p]) => (
                <tr key={botId} className="border-t border-neutral-800">
                  <td className="px-2 py-1">{botId}</td>
                  <td className="px-2 py-1 text-right">{p.gross_volume}</td>
                  <td className="px-2 py-1 text-right">{p.maker_rebates}</td>
                  <td className="px-2 py-1 text-right">{p.taker_fees}</td>
                  <td className="px-2 py-1 text-right">{p.fill_count}</td>
                  <td className="px-2 py-1 text-right">
                    {p.estimated_points}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
