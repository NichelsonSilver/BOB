import { useQuery } from "@tanstack/react-query";

import { Card, ErrorBox, Loading, Row, Warning } from "../components/common/Card";
import { api } from "../lib/api";
import { useLang } from "../i18n";
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
  const { tr } = useLang();
  const q = useQuery({
    queryKey: ["macro", "panel-status"],
    queryFn: () => api.panelStatus(),
    retry: false,
    staleTime: 600_000,
  });
  if (q.isLoading) return <Loading what={tr("the panel status", "el estado del panel")} />;
  if (q.error || !q.data) return <ErrorBox>{String(q.error)}</ErrorBox>;
  const st = q.data;
  // Truncado, como todos los porcentajes de lectura: 43.75% se dice 43%.
  const rate = Math.floor(st.valid_rate * 100);
  const none = tr("none", "ninguno");
  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <header>
        <h1 className="text-2xl font-semibold text-neutral-100">
          {tr("Daily panel · validation in progress", "Panel diario · validación en curso")}
        </h1>
        <p className="mt-1 text-sm text-neutral-400">
          {tr(
            "The crypto panel model passed its tests on historical data. Now it is being tested on days that did not exist yet when it was trained. Until the first review there is no result to show.",
            "El modelo del panel cripto pasó sus pruebas con datos históricos. Ahora se prueba con días que todavía no existían cuando se entrenó. Hasta la primera revisión no hay resultado que mostrar.",
          )}
        </p>
      </header>

      <Card title={tr("Progress", "Avance")}>
        <div className="space-y-4">
          <Bar value={st.valid} total={st.interim_valid_days} label={tr("until the interim review", "hasta la revisión intermedia")} />
          <Bar value={st.valid} total={st.cutoff_valid_days} label={tr("until the final cutoff", "hasta el corte final")} />
          <div className="grid gap-x-8 sm:grid-cols-2">
            <Row label={tr("valid days", "días válidos")} value={st.valid} />
            <Row label={tr("decision days", "días de decisión")} value={st.decision_dates} />
            <Row label={tr("days that count", "días que cuentan")} value={`${rate}%`} tone={rate < 70 ? "text-warn" : ""} />
            <Row label={tr("interim review (current pace)", "revisión intermedia (ritmo actual)")} value={st.eta_interim_at_current_rate ?? DASH} />
            <Row label={tr("final cutoff (current pace)", "corte final (ritmo actual)")} value={st.eta_at_current_rate ?? DASH} />
            <Row label={tr("final cutoff if every day counted", "corte final si todos contaran")} value={st.eta_if_every_day_valid} />
          </div>
        </div>
      </Card>

      {rate < 70 && (
        <Warning>
          {tr(
            "A day only counts if the forecast was issued within 2 hours after the daily close (00:00 UTC). The task runs at 21:05 Chile time; if the computer is off or asleep, the day comes out late (backfill) and does not count. At the current pace the review slips by many months.",
            "Solo cuenta un día si el pronóstico se emitió dentro de las 2 horas posteriores al cierre (00:00 UTC). La tarea corre a las 21:05 hora de Chile; si el equipo está apagado o dormido, el día sale tarde (backfill) y no cuenta. Con el ritmo de hoy la revisión se atrasa muchos meses.",
          )}
        </Warning>
      )}

      <Card title={tr("Days that did not count", "Días que no contaron")}>
        <Row label={tr("issued late (backfill)", "emitidos tarde (backfill)")} value={st.backfill.length ? st.backfill.join(", ") : none} />
        <Row label={tr("never issued", "nunca emitidos")} value={st.never_emitted.length ? st.never_emitted.join(", ") : none} />
      </Card>

      <Card title={tr("Result", "Resultado")}>
        <p className="text-sm text-neutral-400">
          {tr(
            "It is not shown before the interim review: looking earlier takes value away from the test. That is the rule of Amendment 1 (docs/ENMIENDA_PANEL_FORWARD_SECUENCIAL.md), and the server does not even send it.",
            "No se muestra antes de la revisión intermedia: mirarlo antes le quita valor a la prueba. Es la regla de la Enmienda 1 (docs/ENMIENDA_PANEL_FORWARD_SECUENCIAL.md), y el servidor ni siquiera lo envía.",
          )}
        </p>
      </Card>
    </div>
  );
}
