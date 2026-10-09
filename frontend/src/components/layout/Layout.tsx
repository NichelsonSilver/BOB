import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";

import { useAppWebSocket } from "../../hooks/useWebSocket";
import { api } from "../../lib/api";

/**
 * Navegación de MAREA (M6.1, opción A de `docs/M6_ALCANCE.md` §4.1).
 *
 * Arriba lo que se lee para decidir; los desplegables agrupan cripto y lo
 * técnico. **Nada del forward se borró** (decisión del 07-10): la validación
 * de 15m vive en Laboratorio y el forward del panel diario tiene página
 * propia en Cripto.
 *
 * En pantallas chicas (bajo `md`) el menú es un panel que se abre con el botón
 * del encabezado y se cierra al navegar (M6.3): fijo, se comía la mitad del
 * ancho del celular.
 */
type Item = { to: string; label: string; note?: string };
type Group = { label: string; prefix: string; items: Item[] };

const TOP: Item[] = [
  { to: "/", label: "Inicio" },
  { to: "/liquidez", label: "Liquidez" },
  { to: "/flujos", label: "Flujos" },
];

const GROUPS: Group[] = [
  {
    label: "Cripto",
    prefix: "/cripto",
    items: [
      { to: "/cripto", label: "Resumen" },
      { to: "/cripto/panorama", label: "Panorama" },
      { to: "/cripto/panel", label: "Panel diario", note: "forward" },
      { to: "/cripto/riesgo", label: "Riesgo por trade" },
      { to: "/cripto/mapa", label: "Mapa técnico" },
      { to: "/cripto/senal-15m", label: "Señal 15m", note: "congelada" },
    ],
  },
  {
    label: "Laboratorio",
    prefix: "/lab",
    items: [
      { to: "/lab", label: "Resumen" },
      { to: "/lab/forward-15m", label: "Validación forward 15m" },
      { to: "/lab/backtest", label: "Backtest" },
      { to: "/lab/analisis", label: "Análisis" },
    ],
  },
];

const BOTTOM: Item[] = [{ to: "/ajustes", label: "Ajustes" }];

function linkClass({ isActive }: { isActive: boolean }): string {
  return `flex items-baseline justify-between gap-2 rounded-md px-3 py-1.5 text-sm transition ${
    isActive ? "bg-neutral-800 text-accent" : "text-neutral-300 hover:bg-neutral-800/60"
  }`;
}

function NavItem({ item, nested = false }: { item: Item; nested?: boolean }) {
  return (
    <NavLink to={item.to} end={item.to === "/" || item.to === "/cripto" || item.to === "/lab"} className={linkClass}>
      <span className={nested ? "pl-3" : ""}>{item.label}</span>
      {item.note && <span className="text-[10px] text-neutral-600">{item.note}</span>}
    </NavLink>
  );
}

function NavGroup({ group }: { group: Group }) {
  const { pathname } = useLocation();
  const active = pathname.startsWith(group.prefix);
  const [open, setOpen] = useState(active);
  const shown = open || active;
  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={shown}
        className={`flex w-full items-center justify-between rounded-md px-3 py-1.5 text-sm transition hover:bg-neutral-800/60 ${
          active ? "text-neutral-100" : "text-neutral-300"
        }`}
      >
        {group.label}
        <span className="text-xs text-neutral-500">{shown ? "▾" : "▸"}</span>
      </button>
      {shown && (
        <div className="mt-0.5 flex flex-col gap-0.5">
          {group.items.map((it) => (
            <NavItem key={it.to} item={it} nested />
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * Frescura de las fuentes de MAREA. Reemplaza al estado del feed de Binance
 * como indicador del encabezado: con el vivo congelado, la pregunta que
 * importa es "¿los datos están al día?", no "¿el socket está vivo?".
 */
function Freshness() {
  const q = useQuery({
    queryKey: ["macro", "freshness"],
    queryFn: () => api.freshness(),
    retry: false,
    staleTime: 300_000,
  });
  if (q.isLoading) return <span className="text-neutral-600">revisando datos…</span>;
  if (q.error || !q.data) return <span className="text-danger">sin acceso a los datos de MAREA</span>;
  const old = q.data.sources.filter((s) => s.stale);
  const tip = q.data.sources
    .map((s) => `${s.source}: ${s.last ?? "—"} (${s.age_days ?? "—"} d; atrasada desde ${s.stale_after_days})`)
    .join("\n");
  return (
    <span className="flex items-center gap-2" title={tip}>
      <span
        className={`inline-flex h-2 w-2 rounded-full ${old.length ? "bg-warn" : "bg-success"}`}
        aria-hidden
      />
      <span className="text-neutral-400">
        {old.length
          ? `atrasada: ${old.map((s) => `${s.source} (${s.age_days ?? "—"} d)`).join(", ")}`
          : "datos al día"}
      </span>
    </span>
  );
}

export function Layout() {
  // El socket alimenta la Señal 15m (precio en curso); se abre una sola vez acá.
  useAppWebSocket();
  const { pathname } = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  useEffect(() => setMenuOpen(false), [pathname]);

  return (
    <div className="flex h-full bg-surface">
      {menuOpen && (
        <div className="fixed inset-0 z-30 bg-black/60 md:hidden" onClick={() => setMenuOpen(false)} aria-hidden />
      )}
      <aside
        className={`fixed inset-y-0 left-0 z-40 w-60 shrink-0 overflow-y-auto border-r border-neutral-800 bg-neutral-900 p-4 transition-transform md:static md:translate-x-0 ${
          menuOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="mb-8">
          <span className="text-xl font-bold text-accent">MAREA</span>
          <div className="text-xs text-neutral-500">liquidez global y cripto</div>
        </div>
        <nav className="flex flex-col gap-1">
          {TOP.map((it) => (
            <NavItem key={it.to} item={it} />
          ))}
          <div className="my-2 border-t border-neutral-800" />
          {GROUPS.map((g) => (
            <NavGroup key={g.prefix} group={g} />
          ))}
          <div className="my-2 border-t border-neutral-800" />
          {BOTTOM.map((it) => (
            <NavItem key={it.to} item={it} />
          ))}
        </nav>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between gap-3 border-b border-neutral-800 bg-neutral-900 px-4 py-3 text-xs md:px-6">
          <div className="flex min-w-0 items-center gap-3">
            <button
              type="button"
              onClick={() => setMenuOpen(true)}
              aria-label="Abrir menú"
              aria-expanded={menuOpen}
              className="rounded-md border border-neutral-700 px-2 py-1 text-sm text-neutral-300 md:hidden"
            >
              ☰
            </button>
            <span className="font-bold text-accent md:hidden">MAREA</span>
            <Freshness />
          </div>
          <span className="hidden text-neutral-500 sm:inline">solo análisis — MAREA nunca ejecuta órdenes</span>
        </header>
        <main className="flex-1 overflow-auto p-4 md:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
