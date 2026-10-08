import { useState, type ReactNode } from "react";

/**
 * Tarjeta de lectura simple con su respaldo técnico a un clic (M6.1, opción A
 * de `docs/M6_ALCANCE.md` §4.2).
 *
 * Lo de arriba lo tiene que poder leer cualquiera: un número grande, una frase
 * de qué significa y, cuando hace falta, una de qué NO significa. Lo técnico
 * —método, intervalos, n, artefacto— va en `detail` y se abre con "Ver
 * detalle". No se esconde en otra página: queda pegado al número al que
 * respalda.
 *
 * Simplificar el texto no cambia el número (§2 del alcance): `value` se pasa
 * ya formateado por quien llama, con las mismas reglas de `lib/format.ts`.
 */
export function DetailCard({
  title,
  value,
  unit,
  tone = "",
  badge,
  meaning,
  notMeaning,
  asOf,
  detail,
  children,
}: {
  title: string;
  value: ReactNode;
  unit?: ReactNode;
  tone?: string;
  badge?: ReactNode;
  meaning: ReactNode;
  notMeaning?: ReactNode;
  /** Fecha del dato, siempre visible: un número sin fecha no se puede leer. */
  asOf?: string | null;
  detail?: ReactNode;
  children?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <section className="flex flex-col rounded-lg border border-neutral-800 bg-neutral-900/70 p-4">
      <header className="mb-2 flex items-baseline justify-between gap-2">
        <h2 className="text-xs uppercase tracking-wider text-neutral-500">{title}</h2>
        {badge}
      </header>
      <div className="flex items-baseline gap-2">
        <span className={`font-mono text-3xl ${tone || "text-neutral-100"}`}>{value}</span>
        {unit && <span className="text-sm text-neutral-400">{unit}</span>}
      </div>
      <p className="mt-2 text-sm text-neutral-300">{meaning}</p>
      {notMeaning && <p className="mt-1 text-xs text-neutral-500">{notMeaning}</p>}
      {children && <div className="mt-3">{children}</div>}
      <footer className="mt-auto flex items-center justify-between gap-2 pt-3 text-[11px] text-neutral-600">
        <span>{asOf ? `dato al ${asOf}` : ""}</span>
        {detail && (
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            className="rounded px-2 py-0.5 text-neutral-400 hover:bg-neutral-800 hover:text-accent"
            aria-expanded={open}
          >
            {open ? "Ocultar detalle" : "Ver detalle"}
          </button>
        )}
      </footer>
      {open && detail && (
        <div className="mt-2 border-t border-neutral-800 pt-3 text-xs text-neutral-400">
          {detail}
        </div>
      )}
    </section>
  );
}

/** Error de un bloque: se dice qué falta, no se dibuja un cero. */
export function BlockMissing({ title, error }: { title: string; error: string }) {
  return (
    <section className="rounded-lg border border-neutral-800 bg-neutral-900/40 p-4">
      <h2 className="text-xs uppercase tracking-wider text-neutral-500">{title}</h2>
      <p className="mt-2 text-sm text-neutral-500">Sin dato todavía.</p>
      <p className="mt-1 font-mono text-[11px] text-neutral-600">{error}</p>
    </section>
  );
}
