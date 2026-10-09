import { useQuery } from "@tanstack/react-query";

import { Badge, Card, ErrorBox, Loading, Row } from "../components/common/Card";
import { TargetBar } from "../components/layout/TargetBar";
import { useTarget } from "../hooks/useTarget";
import { api } from "../lib/api";
import { num, stamp } from "../lib/format";
import type { FeatureItem } from "../types";

/**
 * Página 2 — el desglose: qué está leyendo el modelo ahora mismo y cuánto
 * aportó cada familia fuera de muestra.
 *
 * Dos cosas que esta página dice explícitamente en vez de dibujar:
 *
 * - **La importancia por permutación no es ganancia fuera de muestra.** Está
 *   medido en este mismo proyecto: en la variante `price+deriv` la familia
 *   `derivados` marcó 0.00151 de importancia —segundo lugar— y al agregar el
 *   libro cayó a 0.00006 mientras el AUC bajaba. El modelo *usa* las columnas;
 *   usarlas no es acertar más.
 * - **El KPI 3 no se emite en vivo.** El HMM existe y está testeado
 *   (`models/hmm.py`), pero el analista no publica régimen ni duración
 *   esperada. Inventar acá una duración a partir de otra cosa sería
 *   exactamente el tipo de número sin respaldo que el proyecto no muestra.
 */
export function AnalysisPage() {
  const { symbol, timeframe } = useTarget();
  const enabled = Boolean(symbol && timeframe);
  const target = { symbol, timeframe };

  const features = useQuery({
    queryKey: ["features", symbol, timeframe],
    queryFn: () => api.features(target),
    enabled,
    retry: false,
  });
  const gate = useQuery({
    queryKey: ["gate", "analysis"],
    queryFn: () => api.gate(),
    enabled,
    retry: false,
    staleTime: 300_000,
  });
  const run = useQuery({
    queryKey: ["run", gate.data?.run_id],
    queryFn: () => api.run(gate.data!.run_id),
    enabled: Boolean(gate.data?.run_id),
    staleTime: 300_000,
  });

  if (!enabled) return <Loading what="la configuración del backend" />;

  const families = features.data?.families ?? {};
  const familyImportance = run.data?.artifact.family_importance ?? {};
  const topFeatures = run.data?.artifact.importance_top ?? [];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-neutral-100">Análisis del modelo 15m</h1>
          <p className="text-xs text-neutral-500">
            {features.data
              ? `${features.data.n_features} features · set ${features.data.feature_set} · barra ${stamp(
                  features.data.open_time,
                )}`
              : "—"}
          </p>
        </div>
        <TargetBar />
      </div>

      {features.error && <ErrorBox>{String(features.error)}</ErrorBox>}

      {features.data && features.data.n_missing > 0 && (
        <Card>
          <p className="text-sm text-warn">
            ⚠ {features.data.n_missing} de {features.data.n_features} features sin
            dato en esta barra. Un salto en este número es la firma de una familia
            que dejó de publicarse — y es lo que deja mudo al analista, porque no
            se pronostica sobre un hueco.
          </p>
        </Card>
      )}

      {/* --- Aporte por familia ----------------------------------------- */}
      <Card
        title="Aporte de cada familia (permutación, fuera de muestra)"
        right={
          gate.data && (
            <span className="font-mono text-[11px] text-neutral-600">
              {gate.data.run_id}
            </span>
          )
        }
      >
        {run.isLoading ? (
          <Loading what="el artefacto del gate" />
        ) : Object.keys(familyImportance).length === 0 ? (
          <p className="text-sm text-neutral-500">
            Sin artefacto del gate para esta configuración.
          </p>
        ) : (
          <>
            <ImportanceBars values={familyImportance} />
            <p className="mt-3 border-t border-neutral-800 pt-3 text-[11px] leading-relaxed text-neutral-500">
              Importancia positiva ≠ ganancia fuera de muestra. Medido en este
              proyecto: en <span className="font-mono">price+deriv</span> la
              familia derivados marcó 0.00151 —segundo lugar— y en{" "}
              <span className="font-mono">full</span> cayó a 0.00006 mientras el
              AUC bajaba y la familia libro salía negativa. El modelo usa las
              columnas; usarlas no es acertar más.
            </p>
          </>
        )}
      </Card>

      {/* --- Lectura actual, familia por familia ------------------------- */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 2xl:grid-cols-3">
        {Object.entries(families)
          .sort(([a], [b]) => a.localeCompare(b))
          .map(([family, items]) => (
            <Card
              key={family}
              title={family}
              right={
                <Badge tone={items.some((i) => i.value === null) ? "warn" : "neutral"}>
                  {items.length}
                </Badge>
              }
            >
              <FeatureList items={items} />
            </Card>
          ))}
      </div>

      {/* --- Top features ----------------------------------------------- */}
      {topFeatures.length > 0 && (
        <Card title="Features más importantes del run (permutación)">
          <div className="grid grid-cols-1 gap-x-6 sm:grid-cols-2 lg:grid-cols-3">
            {topFeatures.slice(0, 15).map(([name, value]) => (
              <Row key={name} label={name} value={num(value, 5)} />
            ))}
          </div>
        </Card>
      )}

      {/* --- KPI 3: lo que todavía no hay ------------------------------- */}
      <Card title="KPI 3 — duración estimada de régimen">
        <p className="text-sm text-neutral-400">
          No se emite en vivo. El HMM gaussiano existe y está testeado
          (<span className="font-mono">models/hmm.py</span>: Baum-Welch propio,
          filtrado causal separado del suavizado, n por BIC/ICL), pero el
          analista todavía no publica régimen ni duración esperada, así que acá
          no hay nada que mostrar. Estimarla a partir de otra cosa sería un
          número sin respaldo, que es justo lo que este dashboard no hace.
        </p>
      </Card>
    </div>
  );
}

function FeatureList({ items }: { items: FeatureItem[] }) {
  return (
    <div className="max-h-64 overflow-auto pr-1">
      {items.map((item) => (
        <Row
          key={item.name}
          label={item.name}
          value={item.value === null ? "sin dato" : num(item.value, 4)}
          tone={item.value === null ? "text-warn" : ""}
        />
      ))}
    </div>
  );
}

/**
 * Barras horizontales de importancia. Una sola serie, así que no lleva
 * leyenda: el título nombra lo que se está midiendo. Las negativas apuntan a
 * la izquierda y se pintan distinto porque significan algo concreto —
 * permutar esa familia MEJORÓ el modelo, o sea que estorbaba.
 */
function ImportanceBars({ values }: { values: Record<string, number> }) {
  const entries = Object.entries(values).sort((a, b) => b[1] - a[1]);
  const scale = Math.max(...entries.map(([, v]) => Math.abs(v)), 1e-9);

  return (
    <div className="space-y-1.5">
      {entries.map(([family, value]) => {
        const width = (Math.abs(value) / scale) * 50;
        const negative = value < 0;
        return (
          <div key={family} className="flex items-center gap-2">
            <span className="w-32 shrink-0 text-xs text-neutral-400">{family}</span>
            <div className="relative h-3 flex-1 overflow-hidden rounded bg-neutral-950">
              <div className="absolute inset-y-0 left-1/2 w-px bg-neutral-700" />
              <div
                className="absolute inset-y-0 rounded-sm"
                style={{
                  width: `${width}%`,
                  left: negative ? `${50 - width}%` : "50%",
                  background: negative ? "#b8820d" : "#4a8fe0",
                }}
              />
            </div>
            <span className="w-20 shrink-0 text-right font-mono text-[11px] text-neutral-400">
              {num(value, 5)}
            </span>
          </div>
        );
      })}
    </div>
  );
}
