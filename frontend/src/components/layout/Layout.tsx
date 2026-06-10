import { NavLink, Outlet } from "react-router-dom";

import { useBotsWebSocket } from "../../hooks/useWebSocket";
import { useBotsStore } from "../../stores/botsStore";
import { KillSwitchButton } from "../kill-switch/KillSwitchButton";

const navItems = [
  { to: "/", label: "Dashboard" },
  { to: "/history", label: "History" },
  { to: "/points", label: "Points" },
  { to: "/settings", label: "Settings" },
];

export function Layout() {
  useBotsWebSocket();
  const wsConnected = useBotsStore((s) => s.wsConnected);
  const activeBots = useBotsStore(
    (s) => s.bots.filter((b) => b.state === "running").length,
  );

  return (
    <div className="flex h-full">
      <aside className="w-56 border-r border-neutral-800 bg-neutral-900 p-4">
        <div className="mb-8 flex items-baseline gap-2">
          <span className="text-xl font-bold text-accent">BOB</span>
          <span className="text-xs text-neutral-500">grid trading</span>
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
          <div className="flex items-center gap-4 text-sm">
            <span
              className={`inline-flex h-2 w-2 rounded-full ${
                wsConnected ? "bg-success" : "bg-danger"
              }`}
              aria-hidden
            />
            <span className="text-neutral-400">
              {wsConnected ? "ws: live" : "ws: disconnected"}
            </span>
            <span className="text-neutral-500">|</span>
            <span className="text-neutral-400">
              active bots: <span className="text-neutral-100">{activeBots}</span>
            </span>
          </div>
          <KillSwitchButton />
        </header>
        <main className="flex-1 overflow-auto p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
