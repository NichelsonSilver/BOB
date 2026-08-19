import { Navigate, Route, Routes } from "react-router-dom";

import { Layout } from "./components/layout/Layout";
import { AnalysisPage } from "./pages/AnalysisPage";
import { BacktestPage } from "./pages/BacktestPage";
import { HistoryPage } from "./pages/HistoryPage";
import { SettingsPage } from "./pages/SettingsPage";
import { SignalPage } from "./pages/SignalPage";

export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<SignalPage />} />
        <Route path="/analysis" element={<AnalysisPage />} />
        <Route path="/backtest" element={<BacktestPage />} />
        <Route path="/history" element={<HistoryPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
