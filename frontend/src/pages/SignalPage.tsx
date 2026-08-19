import { PagePlaceholder } from "../components/common/PagePlaceholder";

/**
 * Página principal (Fase 6): gauge de Seguridad con precisión histórica del
 * bucket, chart de velas con entrada/TP/SL/liquidación, card de EV con y sin
 * leverage, duración estimada de régimen y timeline in-live del KPI.
 */
export function SignalPage() {
  return (
    <PagePlaceholder
      title="Signal"
      phase="Fase 6"
      description="Gauge de Seguridad calibrada, chart con TP/SL/liquidación y EV del setup. Requiere el gate de Fase 4 (backtest calibrado) antes de mostrar señales operables."
    />
  );
}
