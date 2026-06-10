import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../../lib/api";

type Tab = "resumen" | "ordenes" | "transacciones" | "parametros" | "financiacion";

const TABS: { id: Tab; label: string }[] = [
  { id: "resumen", label: "Resumen" },
  { id: "ordenes", label: "Órdenes" },
  { id: "transacciones", label: "Transacciones" },
  { id: "parametros", label: "Parámetros" },
  { id: "financiacion", label: "Financiación" },
];

export function BotDetailModal({
  botId,
  onClose,
}: {
  botId: string;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<Tab>("resumen");
  const botQ = useQuery({
    queryKey: ["bot", botId],
    queryFn: () => api.getBot(botId),
    refetchInterval: 3_000,
  });
  const fillsQ = useQuery({
    queryKey: ["fills", botId],
    queryFn: () => api.listFills({ bot_id: botId, limit: 100 }),
    enabled: tab === "transacciones",
  });
  const pnlQ = useQuery({
    queryKey: ["bot-pnl", botId],
    queryFn: () => api.getPnl(botId),
  });
  const fundingQ = useQuery({
    queryKey: ["funding", botQ.data?.symbol],
    queryFn: () => api.getFundingRate(botQ.data!.symbol, 20),
    enabled: tab === "financiacion" && Boolean(botQ.data?.symbol),
  });

  const bot = botQ.data;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-3xl rounded-2xl border border-neutral-800 bg-neutral-900 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-start justify-between border-b border-neutral-800 p-5">
          <div>
            <h2 className="text-lg font-semibold text-neutral-100">
              {bot ? `${bot.bot_id} — ${bot.symbol} ${bot.direction.toUpperCase()}` : "…"}
            </h2>
            <div className="mt-1 text-xs text-neutral-500">
              {bot && (
                <>
                  mode: {bot.mode} · state: {bot.state}
                </>
              )}
            </div>
          </div>
          <button
            onClick={onClose}
            className="rounded-md border border-neutral-700 bg-neutral-800 p-1.5 text-neutral-400 hover:bg-neutral-700"
            aria-label="Cerrar"
          >
            ✕
          </button>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 border-b border-neutral-800 px-5 pt-3">
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`rounded-t-md px-3 py-2 text-sm transition ${
                tab === t.id
                  ? "border-b-2 border-accent text-accent"
                  : "text-neutral-400 hover:text-neutral-200"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        {/* Body */}
        <div className="max-h-[60vh] overflow-auto p-5">
          {tab === "resumen" && bot && (
            <div className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
              <DetailRow
                label="Estado"
                value={
                  <span
                    className={`rounded-full border px-2 py-0.5 text-xs font-semibold uppercase ${
                      bot.state === "running"
                        ? "border-accent/40 text-accent"
                        : "border-neutral-600 text-neutral-400"
                    }`}
                  >
                    {bot.state}
                  </span>
                }
              />
              <DetailRow
                label="PnL Total"
                value={
                  <span className="font-mono text-neutral-100">
                    ${Number(pnlQ.data?.realized_pnl ?? bot.realized_pnl ?? 0).toFixed(2)}
                  </span>
                }
              />
              <DetailRow
                label="Grid Profit"
                value={
                  <span className="font-mono text-accent">
                    ${Number(bot.realized_pnl ?? 0).toFixed(2)}
                  </span>
                }
              />
              <DetailRow label="Trend PnL" value={<span className="font-mono">$0.00</span>} />
              <DetailRow
                label="Posición"
                value={<span className="font-mono">{bot.last_price ? "—" : "—"}</span>}
              />
              <DetailRow
                label="Precio Entrada"
                value={
                  <span className="font-mono">
                    ${Number(bot.last_price ?? 0).toFixed(2)}
                  </span>
                }
              />
              <DetailRow label="Rango" value={<span className="font-mono">${bot.price_low} – ${bot.price_high}</span>} />
              <DetailRow label="Grids" value={<span className="font-mono">{bot.n_grids}</span>} />
              <DetailRow label="Rondas" value={<span className="font-mono">{bot.grid_trades_count ?? 0}</span>} />
              <DetailRow label="Órdenes vivas" value={<span className="font-mono">{bot.live_orders_count ?? 0}</span>} />
              <DetailRow label="Volumen" value={<span className="font-mono">${Number(bot.total_volume ?? 0).toFixed(2)}</span>} />
            </div>
          )}

          {tab === "ordenes" && bot && (
            <div className="text-sm text-neutral-500">
              {bot.live_orders_count
                ? `${bot.live_orders_count} órdenes vivas. Vista detallada en próxima iteración.`
                : "No hay órdenes vivas en este bot."}
            </div>
          )}

          {tab === "transacciones" && (
            <FillsTable fills={fillsQ.data} loading={fillsQ.isLoading} />
          )}

          {tab === "parametros" && bot && (
            <div className="grid grid-cols-2 gap-x-6 gap-y-3 font-mono text-sm">
              <DetailRow label="symbol" value={bot.symbol} />
              <DetailRow label="direction" value={bot.direction} />
              <DetailRow label="mode" value={bot.mode} />
              <DetailRow label="price_low" value={bot.price_low} />
              <DetailRow label="price_high" value={bot.price_high} />
              <DetailRow label="n_grids" value={String(bot.n_grids)} />
              <DetailRow label="investment_usdt" value={bot.investment_usdt} />
              <DetailRow label="leverage" value={`${bot.leverage}x`} />
            </div>
          )}

          {tab === "financiacion" && (
            <FundingView data={fundingQ.data} loading={fundingQ.isLoading} />
          )}
        </div>
      </div>
    </div>
  );
}

function DetailRow({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div className="flex items-baseline justify-between border-b border-neutral-800/60 pb-1">
      <span className="text-xs text-neutral-500">{label}:</span>
      <span>{value}</span>
    </div>
  );
}

function FillsTable({
  fills,
  loading,
}: {
  fills: Array<{
    price: string;
    quantity: string;
    side: string;
    pnl: string;
    fee: string;
    filled_at: string;
  }> | undefined;
  loading: boolean;
}) {
  if (loading) return <div className="text-sm text-neutral-500">Cargando fills…</div>;
  if (!fills || fills.length === 0)
    return <div className="text-sm text-neutral-500">No hay fills todavía.</div>;
  return (
    <table className="w-full text-sm">
      <thead className="text-xs uppercase tracking-wider text-neutral-500">
        <tr>
          <th className="px-2 py-1 text-left">fecha</th>
          <th className="px-2 py-1 text-left">side</th>
          <th className="px-2 py-1 text-right">precio</th>
          <th className="px-2 py-1 text-right">qty</th>
          <th className="px-2 py-1 text-right">pnl</th>
        </tr>
      </thead>
      <tbody className="font-mono">
        {fills.map((f, i) => {
          const pnl = Number(f.pnl);
          return (
            <tr key={i} className="border-b border-neutral-800/60">
              <td className="px-2 py-1 text-xs text-neutral-400">{f.filled_at.slice(0, 19)}</td>
              <td className="px-2 py-1">
                <span
                  className={
                    f.side === "BUY" || f.side === "buy"
                      ? "text-accent"
                      : "text-danger"
                  }
                >
                  {f.side.toUpperCase()}
                </span>
              </td>
              <td className="px-2 py-1 text-right">{f.price}</td>
              <td className="px-2 py-1 text-right">{f.quantity}</td>
              <td
                className={`px-2 py-1 text-right ${
                  pnl >= 0 ? "text-accent" : "text-danger"
                }`}
              >
                {pnl.toFixed(2)}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function FundingView({
  data,
  loading,
}: {
  data: Record<string, unknown> | undefined;
  loading: boolean;
}) {
  if (loading) return <div className="text-sm text-neutral-500">Cargando funding…</div>;
  if (!data) return <div className="text-sm text-neutral-500">Sin datos de funding.</div>;
  const rows = Array.isArray(data?.["history"])
    ? (data["history"] as Array<Record<string, unknown>>)
    : [];
  if (rows.length === 0)
    return <div className="text-sm text-neutral-500">No hay historial de funding.</div>;
  return (
    <table className="w-full text-sm">
      <thead className="text-xs uppercase tracking-wider text-neutral-500">
        <tr>
          <th className="px-2 py-1 text-left">timestamp</th>
          <th className="px-2 py-1 text-right">rate</th>
        </tr>
      </thead>
      <tbody className="font-mono">
        {rows.slice(0, 20).map((r, i) => (
          <tr key={i} className="border-b border-neutral-800/60">
            <td className="px-2 py-1 text-xs text-neutral-400">
              {String(r["timestamp"] ?? r["funding_time"] ?? "")}
            </td>
            <td className="px-2 py-1 text-right">
              {String(r["funding_rate"] ?? r["rate"] ?? "—")}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
