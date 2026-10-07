import { NavLink, Outlet } from "react-router-dom";

import { useAppWebSocket } from "../../hooks/useWebSocket";
import { useAppStore } from "../../stores/appStore";

const navItems = [
  { to: "/macro", label: "Macro" },
  { to: "/flows", label: "Flujos" },
  { to: "/", label: "Signal" },
  { to: "/map", label: "Mapa" },
  { to: "/risk", label: "Riesgo" },
  { to: "/analysis", label: "Analysis" },
  { to: "/backtest", label: "Backtest" },
  { to: "/history", label: "History" },
  { to: "/settings", label: "Settings" },
];

export function Layout() {
  useAppWebSocket();
  const wsConnected = useAppStore((s) => s.wsConnected);
  const conn = useAppStore((s) => s.connStatus);

  // Regla 8: el estado de cada conexión es parte de lo que el usuario debe
  // ver. Se separan a propósito el socket del dashboard y el feed de Binance:
  // el navegador puede estar perfectamente conectado a un backend que perdió
  // el feed, y en ese caso el precio que se muestra no es el de ahora.
  const feedDown = conn !== null && !conn.connected;

  return (
    <div className="flex h-full bg-surface">
      <aside className="w-56 border-r border-neutral-800 bg-neutral-900 p-4">
        <div className="mb-8 flex items-baseline gap-2">
          <span className="text-xl font-bold text-accent">MAREA</span>
          <span className="text-xs text-neutral-500">liquidez global · cripto: BOB</span>
        </div>
        <nav className="flex flex-col gap-1">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) =>
                `rounded-md px-3 py-2 text-sm transition ${
                  isActive
                    ? "bg-neutral-800 text-accent"
                    : "text-neutral-300 hover:bg-neutral-800/60"
                }`
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
      </aside>

      <div className="flex flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-neutral-800 bg-neutral-900 px-6 py-3">
          <div className="flex items-center gap-4 text-xs">
            <span className="flex items-center gap-2">
              <span
                className={`inline-flex h-2 w-2 rounded-full ${
                  wsConnected ? "bg-success" : "bg-danger"
                }`}
                aria-hidden
              />
              <span className="text-neutral-400">
                {wsConnected ? "dashboard: live" : "dashboard: desconectado"}
              </span>
            </span>

            {conn && (
              <span
                className="flex items-center gap-2"
                title={
                  conn.mute_streams.length > 0
                    ? `streams mudos: ${conn.mute_streams.join(", ")} — se rellenan por REST`
                    : undefined
                }
              >
                <span
                  className={`inline-flex h-2 w-2 rounded-full ${
                    feedDown ? "bg-danger" : "bg-success"
                  }`}
                  aria-hidden
                />
                <span className="font-mono text-neutral-500">{conn.source}</span>
                {conn.reconnects > 0 && (
                  <span className="text-neutral-600">
                    {conn.reconnects} reconexiones
                  </span>
                )}
              </span>
            )}

            {conn?.last_error && (
              <span className="text-warn" title={conn.last_error}>
                último error: {conn.last_error.slice(0, 60)}
              </span>
            )}
          </div>
          <span className="text-xs text-neutral-500">
            solo análisis — BOB nunca ejecuta órdenes
          </span>
        </header>
        <main className="flex-1 overflow-auto p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
