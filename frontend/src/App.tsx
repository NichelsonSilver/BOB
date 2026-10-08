import { Navigate, Route, Routes, useLocation } from "react-router-dom";

import { Layout } from "./components/layout/Layout";
import { AnalysisPage } from "./pages/AnalysisPage";
import { BacktestPage } from "./pages/BacktestPage";
import { FlowsPage } from "./pages/FlowsPage";
import { HistoryPage } from "./pages/HistoryPage";
import { HomePage } from "./pages/HomePage";
import { MacroPage } from "./pages/MacroPage";
import { MapPage } from "./pages/MapPage";
import { PanelPage } from "./pages/PanelPage";
import { PanoramaPage } from "./pages/PanoramaPage";
import { RiskPage } from "./pages/RiskPage";
import { SettingsPage } from "./pages/SettingsPage";
import { SignalPage } from "./pages/SignalPage";

/**
 * Rutas de MAREA (M6.1). Las viejas redirigen a las nuevas conservando la
 * query: `/risk?entry=&stop=` sigue prellenando la calculadora.
 */
const LEGACY: Record<string, string> = {
  "/macro": "/liquidez",
  "/flows": "/flujos",
  "/map": "/cripto/mapa",
  "/risk": "/cripto/riesgo",
  "/signal": "/cripto/senal-15m",
  "/history": "/lab/forward-15m",
  "/backtest": "/lab/backtest",
  "/analysis": "/lab/analisis",
  "/settings": "/ajustes",
};

function Legacy({ to }: { to: string }) {
  const { search } = useLocation();
  return <Navigate to={`${to}${search}`} replace />;
}

export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<HomePage />} />
        <Route path="/liquidez" element={<MacroPage />} />
        <Route path="/flujos" element={<FlowsPage />} />
        <Route path="/cripto/panorama" element={<PanoramaPage />} />
        <Route path="/cripto/panel" element={<PanelPage />} />
        <Route path="/cripto/riesgo" element={<RiskPage />} />
        <Route path="/cripto/mapa" element={<MapPage />} />
        <Route path="/cripto/senal-15m" element={<SignalPage />} />
        <Route path="/lab/forward-15m" element={<HistoryPage />} />
        <Route path="/lab/backtest" element={<BacktestPage />} />
        <Route path="/lab/analisis" element={<AnalysisPage />} />
        <Route path="/ajustes" element={<SettingsPage />} />
        {Object.entries(LEGACY).map(([from, to]) => (
          <Route key={from} path={from} element={<Legacy to={to} />} />
        ))}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
