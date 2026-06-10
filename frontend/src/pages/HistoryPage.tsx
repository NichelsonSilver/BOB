import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Card, Stat } from "../components/common/Card";
import { api } from "../lib/api";
import { useBotsStore } from "../stores/botsStore";

export function HistoryPage() {
  const bots = useBotsStore((s) => s.bots);
  const [filter, setFilter] = useState<string>("");

  const botFilter = filter || undefined;

  const pnl = useQuery({
    queryKey: ["pnl", botFilter],
    queryFn: () => api.getPnl(botFilter),
  });
  const curve = useQuery({
    queryKey: ["equity", botFilter],
    queryFn: () => api.getEquityCurve(botFilter),
  });
  const fills = useQuery({
    queryKey: ["fills", botFilter],
    queryFn: () => api.listFills({ bot_id: botFilter, limit: 100 }),
  });

  const chartData = (curve.data ?? []).map((p) => ({
    t: p.timestamp,
    pnl: Number(p.cumulative_pnl),
  }));

  return (
    <div className="grid gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">History</h1>
        <label className="flex items-center gap-2 text-sm">
          <span className="text-neutral-500">bot:</span>
          <select
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="rounded-md border border-neutral-700 bg-neutral-900 px-2 py-1 text-sm"
          >
            <option value="">todos</option>
            {bots.map((b) => (
              <option key={b.bot_id} value={b.bot_id}>
                {b.bot_id}
              </option>
            ))}
          </select>
        </label>
      </div>

      <Card title="Resumen PnL">
        {pnl.data ? (
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            <Stat label="Realized PnL" value={pnl.data.realized_pnl} />
            <Stat label="Fees" value={pnl.data.total_fees} />
            <Stat label="Volume" value={pnl.data.gross_volume} />
            <Stat label="Fills" value={pnl.data.fill_count} />
          </div>
        ) : (
          <div>Cargando…</div>
        )}
      </Card>

      <Card title="Equity curve">
        <div className="h-64">
          {chartData.length === 0 ? (
            <div className="flex h-full items-center justify-center text-sm text-neutral-500">
              Sin datos todavía
            </div>
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData}>
                <CartesianGrid stroke="#262626" />
                <XAxis
                  dataKey="t"
                  tick={{ fill: "#737373", fontSize: 11 }}
                  tickFormatter={(v) => new Date(v).toLocaleTimeString()}
                />
                <YAxis tick={{ fill: "#737373", fontSize: 11 }} />
                <Tooltip
                  contentStyle={{
                    background: "#171717",
                    border: "1px solid #404040",
                  }}
                />
                <Line
                  type="monotone"
                  dataKey="pnl"
                  stroke="#22d3ee"
                  dot={false}
                  strokeWidth={2}
                />
              </LineChart>
            </ResponsiveContainer>
          )}
        </div>
      </Card>

      <Card title="Fills (últimos 100)">
        <div className="overflow-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wider text-neutral-500">
                <th className="px-2 py-2">time</th>
                <th className="px-2 py-2">bot</th>
                <th className="px-2 py-2">side</th>
                <th className="px-2 py-2 text-right">price</th>
                <th className="px-2 py-2 text-right">qty</th>
                <th className="px-2 py-2 text-right">fee</th>
                <th className="px-2 py-2 text-right">pnl</th>
                <th className="px-2 py-2">mode</th>
              </tr>
            </thead>
            <tbody className="font-mono">
              {(fills.data ?? []).map((f) => (
                <tr
                  key={f.client_order_id}
                  className="border-t border-neutral-800"
                >
                  <td className="px-2 py-1 text-xs">
                    {new Date(f.filled_at).toLocaleString()}
                  </td>
                  <td className="px-2 py-1">{f.bot_id}</td>
                  <td
                    className={`px-2 py-1 ${
                      f.side === "buy" ? "text-success" : "text-danger"
                    }`}
                  >
                    {f.side}
                  </td>
                  <td className="px-2 py-1 text-right">{f.price}</td>
                  <td className="px-2 py-1 text-right">{f.quantity}</td>
                  <td className="px-2 py-1 text-right">{f.fee}</td>
                  <td className="px-2 py-1 text-right">{f.pnl}</td>
                  <td className="px-2 py-1 text-neutral-500">{f.mode}</td>
                </tr>
              ))}
              {(fills.data ?? []).length === 0 && (
                <tr>
                  <td
                    colSpan={8}
                    className="py-4 text-center text-sm text-neutral-500"
                  >
                    Sin fills
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
