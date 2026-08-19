type Props = {
  title: string;
  phase: string;
  description: string;
};

/** Placeholder de página pendiente — se reemplaza en Fase 6. */
export function PagePlaceholder({ title, phase, description }: Props) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
      <h1 className="text-2xl font-semibold text-neutral-100">{title}</h1>
      <span className="rounded-full border border-warn/40 px-3 py-1 text-xs text-warn">
        pendiente — {phase}
      </span>
      <p className="max-w-md text-sm text-neutral-400">{description}</p>
    </div>
  );
}
