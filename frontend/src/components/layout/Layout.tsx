import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";

import { useAppWebSocket } from "../../hooks/useWebSocket";
import { type Lang, pick, useHtmlLang, useLang } from "../../i18n";
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
 *
 * Cada etiqueta lleva sus dos idiomas (`[en, es]`); el botón EN / ES del
 * encabezado elige cuál se ve. Inglés por defecto.
 */
type Text = [en: string, es: string];
type Item = { to: string; label: Text; note?: Text };
type Group = { label: Text; prefix: string; items: Item[] };

const TOP: Item[] = [
  { to: "/", label: ["Home", "Inicio"] },
  { to: "/liquidez", label: ["Liquidity", "Liquidez"] },
  { to: "/flujos", label: ["Flows", "Flujos"] },
];

const GROUPS: Group[] = [
  {
    label: ["Crypto", "Cripto"],
    prefix: "/cripto",
    items: [
      { to: "/cripto", label: ["Overview", "Resumen"] },
      { to: "/cripto/panorama", label: ["Panorama", "Panorama"] },
      { to: "/cripto/panel", label: ["Daily panel", "Panel diario"], note: ["forward", "forward"] },
      { to: "/cripto/riesgo", label: ["Trade risk", "Riesgo por trade"] },
      { to: "/cripto/mapa", label: ["Technical map", "Mapa técnico"] },
      { to: "/cripto/senal-15m", label: ["15m signal", "Señal 15m"], note: ["frozen", "congelada"] },
    ],
  },
  {
    label: ["Lab", "Laboratorio"],
    prefix: "/lab",
    items: [
      { to: "/lab", label: ["Overview", "Resumen"] },
      { to: "/lab/forward-15m", label: ["15m forward validation", "Validación forward 15m"] },
      { to: "/lab/backtest", label: ["Backtest", "Backtest"] },
      { to: "/lab/analisis", label: ["Analysis", "Análisis"] },
    ],
  },
];

const BOTTOM: Item[] = [{ to: "/ajustes", label: ["Settings", "Ajustes"] }];

const ROOTS = new Set(["/", "/cripto", "/lab"]);

function linkClass({ isActive }: { isActive: boolean }): string {
  return `flex items-baseline justify-between gap-2 rounded-md px-3 py-1.5 text-sm transition ${
    isActive ? "bg-neutral-800 text-accent" : "text-neutral-300 hover:bg-neutral-800/60"
  }`;
}

function NavItem({ item, nested = false }: { item: Item; nested?: boolean }) {
  const { lang } = useLang();
  return (
    <NavLink to={item.to} end={ROOTS.has(item.to)} className={linkClass}>
      <span className={nested ? "pl-3" : ""}>{pick(lang, ...item.label)}</span>
      {item.note && <span className="text-[10px] text-neutral-600">{pick(lang, ...item.note)}</span>}
    </NavLink>
  );
}

function NavGroup({ group }: { group: Group }) {
  const { lang } = useLang();
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
        {pick(lang, ...group.label)}
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
  const { tr } = useLang();
  const q = useQuery({
    queryKey: ["macro", "freshness"],
    queryFn: () => api.freshness(),
    retry: false,
    staleTime: 300_000,
  });
  if (q.isLoading) return <span className="text-neutral-600">{tr("checking data…", "revisando datos…")}</span>;
  if (q.error || !q.data)
    return <span className="text-danger">{tr("MAREA data unavailable", "sin acceso a los datos de MAREA")}</span>;
  const old = q.data.sources.filter((s) => s.stale);
  const tip = q.data.sources
    .map((s) =>
      tr(
        `${s.source}: ${s.last ?? "—"} (${s.age_days ?? "—"} d; stale after ${s.stale_after_days})`,
        `${s.source}: ${s.last ?? "—"} (${s.age_days ?? "—"} d; atrasada desde ${s.stale_after_days})`,
      ),
    )
    .join("\n");
  return (
    <span className="flex items-center gap-2" title={tip}>
      <span
        className={`inline-flex h-2 w-2 rounded-full ${old.length ? "bg-warn" : "bg-success"}`}
        aria-hidden
      />
      <span className="text-neutral-400">
        {old.length
          ? `${tr("stale", "atrasada")}: ${old.map((s) => `${s.source} (${s.age_days ?? "—"} d)`).join(", ")}`
          : tr("data up to date", "datos al día")}
      </span>
    </span>
  );
}

/** Botón EN / ES. Marca el idioma activo y deja elegir el otro. */
function LangToggle() {
  const { lang, setLang } = useLang();
  const opt = (l: Lang, label: string) => (
    <button
      type="button"
      onClick={() => setLang(l)}
      aria-pressed={lang === l}
      className={`px-2 py-0.5 ${lang === l ? "bg-neutral-700 text-neutral-100" : "text-neutral-400 hover:text-neutral-200"}`}
    >
      {label}
    </button>
  );
  return (
    <div
      role="group"
      aria-label={lang === "en" ? "Language" : "Idioma"}
      className="flex overflow-hidden rounded-md border border-neutral-700 text-[11px] font-medium"
    >
      {opt("en", "EN")}
      {opt("es", "ES")}
    </div>
  );
}

/** Título de la pestaña: la página y después MAREA (M6.6). Sale del mismo menú, así no hay dos listas. */
function pageTitle(pathname: string, lang: Lang): string {
  const all = [
    ...TOP.map((it) => ({ to: it.to, label: pick(lang, ...it.label) })),
    ...GROUPS.flatMap((g) =>
      g.items.map((it) => ({ to: it.to, label: `${pick(lang, ...g.label)} · ${pick(lang, ...it.label)}` })),
    ),
    ...BOTTOM.map((it) => ({ to: it.to, label: pick(lang, ...it.label) })),
  ];
  const hit = all.find((it) => it.to === pathname);
  return hit && hit.to !== "/"
    ? `${hit.label} — MAREA`
    : pick(lang, "MAREA — global liquidity and crypto", "MAREA — liquidez global y cripto");
}

export function Layout() {
  // El socket alimenta la Señal 15m (precio en curso); se abre una sola vez acá.
  useAppWebSocket();
  useHtmlLang();
  const { lang, tr } = useLang();
  const { pathname } = useLocation();
  useEffect(() => {
    document.title = pageTitle(pathname, lang);
  }, [pathname, lang]);
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
          <div className="text-xs text-neutral-500">{tr("global liquidity and crypto", "liquidez global y cripto")}</div>
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
              aria-label={tr("Open menu", "Abrir menú")}
              aria-expanded={menuOpen}
              className="rounded-md border border-neutral-700 px-2 py-1 text-sm text-neutral-300 md:hidden"
            >
              ☰
            </button>
            <span className="font-bold text-accent md:hidden">MAREA</span>
            <Freshness />
          </div>
          <div className="flex shrink-0 items-center gap-3">
            <span className="hidden text-neutral-500 lg:inline">
              {tr("analysis only — MAREA never places orders", "solo análisis — MAREA nunca ejecuta órdenes")}
            </span>
            <LangToggle />
          </div>
        </header>
        <main className="flex-1 overflow-auto p-4 md:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
