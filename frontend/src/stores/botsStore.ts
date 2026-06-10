import { create } from "zustand";

import type { BotStatus } from "../lib/api";

type BotsState = {
  bots: BotStatus[];
  wsConnected: boolean;
  setSnapshot: (rows: BotStatus[]) => void;
  setConnected: (v: boolean) => void;
};

export const useBotsStore = create<BotsState>((set) => ({
  bots: [],
  wsConnected: false,
  setSnapshot: (rows) => set({ bots: rows }),
  setConnected: (v) => set({ wsConnected: v }),
}));
