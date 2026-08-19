import { create } from "zustand";

/**
 * Estado global mínimo del shell. Las fases 5-6 agregan aquí (o en stores
 * separados) la señal activa, el histórico y el estado de las fuentes.
 */
type AppState = {
  wsConnected: boolean;
  setConnected: (v: boolean) => void;
};

export const useAppStore = create<AppState>((set) => ({
  wsConnected: false,
  setConnected: (v) => set({ wsConnected: v }),
}));
