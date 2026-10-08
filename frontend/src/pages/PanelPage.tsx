import { useQuery } from "@tanstack/react-query";

import { Card, ErrorBox, Loading, Row, Warning } from "../components/common/Card";
import { api } from "../lib/api";
import { DASH } from "../lib/format";

/**
 * Cripto ▸ Panel diario: estado de la validación forward (Fase 10-bis).
 *
 * **Muestra el avance, nunca el resultado.** La Enmienda 1 fija un análisis
 * interino a las 90 fechas válidas y prohíbe mirar el IC antes; el endpoint
 * ni siquiera lo trae. Lo que sí se ve es lo que Nichelson puede mejorar: el
 * ritmo de días válidos, que depende de que el equipo esté encendido a las
 * 21:05.
 */
function Bar({ value, total, label }: { value: number; total: number; label: string }) {
  const w = Math.min(100, (value / total) * 100);
  return (
    <div>
      <div className="mb-1 flex justify-between text-xs text-neutral-400">
        <span>{label}</span>
        <span className="font-mono">
          {value} / {total}
        </span>
      </div>
      <div className="h-2 rounded bg-neutral-800">
        <div className="h-2 rounded bg-accent" style={{ width: `${w}%` }} />
      </div>
    </div>
  );
}

export function PanelPage() {
  const q = useQuery({
    queryKey: ["macro", "panel-status"],
    queryFn: () => api.panelStatus(),
    retry: false,
    staleTime: 600_000,
  });
  if (q.isLoading) return <Loading what="el estado del panel" />;
  if (q.error || !q.data) return <ErrorBox>{String(q.error)}</ErrorBox>;
  const st = q.data;
  const rate = Math.round(st.valid_rate * 100);
  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <header>
        <h1 className="text-2xl font-semibold text-neutral-100">Panel diario · validación en curso</h1>
        <p className="mt-1 text-sm text-neutral-400">
          El modelo del panel cripto pasó sus pruebas con datos históricos. Ahora se prueba con días que todavía no
          existían cuando se entrenó. Hasta la primera revisión no hay resultado que mostrar.
        </p>
      </header>

      <Card title="Avance">
        <div className="space-y-4">
          <Bar value={st.valid} total={st.interim_valid_days} label="hasta la revisión intermedia" />
          <Bar value={st.valid} total={st.cutoff_valid_days} label="hasta el corte final" />
          <div className="grid gap-x-8 sm:grid-cols-2">
            <Row label="días válidos" value={st.valid} />
            <Row label="días de decisión" value={st.decision_dates} />
            <Row label="días que cuentan" value={`${rate}%`} tone={rate < 70 ? "text-warn" : ""} />
            <Row label="revisión intermedia (ritmo actual)" value={st.eta_interim_at_current_rate ?? DASH} />
            <Row label="corte final (ritmo actual)" value={st.eta_at_current_rate ?? DASH} />
            <Row label="corte final si todos contaran" value={st.eta_if_every_day_valid} />
          </div>
        </div>
      </Card>

      {rate < 70 && (
        <Warning>
          Solo cuenta un día si el pronóstico se emitió dentro de las 2 horas posteriores al cierre (00:00 UTC). La
          tarea corre a las 21:05 hora de Chile; si el equipo está apagado o dormido, el día sale tarde (backfill) y no
          cuenta. Con el ritmo de hoy la revisión se atrasa muchos meses.
        </Warning>
      )}

      <Card title="Días que no contaron">
        <Row label="emitidos tarde (backfill)" value={st.backfill.length ? st.backfill.join(", ") : "ninguno"} />
        <Row label="nunca emitidos" value={st.never_emitted.length ? st.never_emitted.join(", ") : "ninguno"} />
      </Card>

      <Card title="Resultado">
        <p className="text-sm text-neutral-400">
          No se muestra antes de la revisión intermedia: mirarlo antes le quita valor a la prueba. Es la regla de la
          Enmienda 1 (docs/ENMIENDA_PANEL_FORWARD_SECUENCIAL.md), y el servidor ni siquiera lo envía.
        </p>
      </Card>
    </div>
  );
}
