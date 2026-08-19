import { PagePlaceholder } from "../components/common/PagePlaceholder";

/** Fase 6: configurar/correr backtests; la curva de calibración es lo central. */
export function BacktestPage() {
  return (
    <PagePlaceholder
      title="Backtest"
      phase="Fase 6"
      description="Equity curve, métricas walk-forward y curva de calibración (predicho vs observado por bucket). Es el gate que habilita señales operables."
    />
  );
}
