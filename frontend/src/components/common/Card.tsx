import type { ReactNode } from "react";

export function Card({
  title,
  right,
  children,
  className = "",
}: {
  title?: string;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`rounded-lg border border-neutral-800 bg-neutral-900/70 p-4 ${className}`}
    >
      {(title || right) && (
        <header className="mb-3 flex items-baseline justify-between gap-2">
          {title && (
            <h2 className="text-xs uppercase tracking-wider text-neutral-500">
              {title}
            </h2>
          )}
          {right}
        </header>
      )}
      {children}
    </section>
  );
}

export function Stat({
  label,
  value,
  sub,
  tone = "",
}: {
  label: ReactNode;
  value: ReactNode;
  sub?: ReactNode;
  tone?: string;
}) {
  return (
    <div>
      <div className="text-xs uppercase tracking-wider text-neutral-500">
        {label}
      </div>
      <div className={`mt-1 font-mono text-lg ${tone || "text-neutral-100"}`}>
        {value}
      </div>
      {sub && <div className="mt-0.5 text-xs text-neutral-500">{sub}</div>}
    </div>
  );
}

export type BadgeTone = "neutral" | "ok" | "warn" | "bad" | "muted";

const BADGE_TONES: Record<BadgeTone, string> = {
  neutral: "border-neutral-700 text-neutral-300",
  ok: "border-accent/40 text-accent",
  warn: "border-warn/40 text-warn",
  bad: "border-danger/40 text-danger",
  // El gris del KPI sin calibrar (regla 2). Es un tono propio y no "neutral"
  // porque significa algo concreto: el número existe y no es operable.
  muted: "border-neutral-800 bg-neutral-800/40 text-neutral-500",
};

export function Badge({
  tone = "neutral",
  children,
  title,
}: {
  tone?: BadgeTone;
  children: ReactNode;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] ${BADGE_TONES[tone]}`}
    >
      {children}
    </span>
  );
}

/**
 * Aviso operativo. Los `warnings` de una proyección son parte del resultado y
 * no de la vista —si la liquidación llega antes que el stop, eso no es un
 * detalle que se pueda olvidar de dibujar—, así que este componente existe
 * para que ningún panel tenga excusa para no mostrarlos.
 */
export function Warning({ children }: { children: ReactNode }) {
  return (
    <div className="flex gap-2 rounded border border-warn/30 bg-warn/5 px-3 py-2 text-xs text-warn">
      <span aria-hidden>⚠</span>
      <span>{children}</span>
    </div>
  );
}

export function ErrorBox({ children }: { children: ReactNode }) {
  return (
    <div className="rounded border border-danger/40 bg-danger/5 px-3 py-2 text-sm text-danger">
      {children}
    </div>
  );
}

export function Loading({ what }: { what: string }) {
  return (
    <div className="animate-pulse text-sm text-neutral-500">Cargando {what}…</div>
  );
}

/** Fila etiqueta → valor, monoespaciada. La unidad de casi todas las tablas. */
export function Row({
  label,
  value,
  tone = "",
  hint,
}: {
  label: ReactNode;
  value: ReactNode;
  tone?: string;
  hint?: string;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1" title={hint}>
      <span className="text-xs text-neutral-500">{label}</span>
      <span className={`font-mono text-sm ${tone || "text-neutral-200"}`}>
        {value}
      </span>
    </div>
  );
}
