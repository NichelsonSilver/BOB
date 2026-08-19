import { PagePlaceholder } from "../components/common/PagePlaceholder";

/** Fase 6: señales emitidas + outcome del paper tracking, export CSV. */
export function HistoryPage() {
  return (
    <PagePlaceholder
      title="History"
      phase="Fase 6"
      description="Tabla de señales con su outcome forward (paper tracking) y la precisión acumulada comparada contra la del backtest."
    />
  );
}
