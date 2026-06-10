import { useState } from "react";

import { api } from "../../lib/api";

export function KillSwitchButton() {
  const [busy, setBusy] = useState(false);
  const [lastResult, setLastResult] = useState<string | null>(null);

  const onClick = async () => {
    if (!window.confirm("¿Detener TODOS los bots ahora?")) return;
    setBusy(true);
    try {
      const res = await api.killSwitch();
      setLastResult(`stopped ${res.bots_stopped}`);
    } catch (e) {
      setLastResult((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex items-center gap-3">
      {lastResult && (
        <span className="text-xs text-neutral-400">{lastResult}</span>
      )}
      <button
        type="button"
        onClick={onClick}
        disabled={busy}
        className="rounded-md bg-danger px-3 py-1.5 text-sm font-semibold text-white shadow hover:bg-red-600 disabled:opacity-50"
      >
        {busy ? "stopping…" : "KILL SWITCH"}
      </button>
    </div>
  );
}
