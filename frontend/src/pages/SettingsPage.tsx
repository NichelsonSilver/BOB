import { useQuery } from "@tanstack/react-query";

import { Badge, Card, ErrorBox, Loading, Row } from "../components/common/Card";
import { Glossary } from "../components/marea/Glossary";
import { api } from "../lib/api";
import { multiplier, num, pct } from "../lib/format";
import { useAppStore } from "../stores/appStore";

/**
 * Página 5 — Settings. **Espejo de solo lectura de lo que el backend corre.**
 *
 * El diseño original pedía un formulario (watchlist, umbral, n del HMM,
 * TP/SL/H, Telegram). Es de solo lectura por una razón fechada: mientras corre
 * la validación forward, cambiar `feature_set` o `vol_kind` en caliente mete
 * dos `model_version` en la misma muestra y la cobertura medida deja de
 * describir a ninguno de los dos. Es el mismo motivo por el que XGBoost se
 * agregó ANTES de arrancar la acumulación — con 2 registros el cambio era
 * gratis, con 1.150 cuesta la corrida.
 *
 * Lo que sí es editable es lo que no toca ningún modelo: leverage del slider,
 * nivel del cono, símbolo y timeframe que se están mirando. Eso vive en el
 * navegador (localStorage) y no viaja al servidor.
 *
 * Las credenciales no se muestran nunca — la API solo dice si están
 * configuradas.
 */
/**
 * Ajustes (M6.5): el glosario arriba, porque es lo que sirve a cualquier
 * lector; la configuración efectiva del módulo de 15m abajo, tal como estaba.
 */
export function SettingsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-neutral-100">Ajustes</h1>
        <p className="text-xs text-neutral-500">Glosario y configuración del módulo de 15m.</p>
      </div>
      <Glossary />
      <ConfigSection />
    </div>
  );
}

function ConfigSection() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["settings"],
    queryFn: api.settings,
    staleTime: 60_000,
  });
  const leverage = useAppStore((s) => s.leverage);
  const coneAlpha = useAppStore((s) => s.coneAlpha);

  if (isLoading) return <Loading what="la configuración" />;
  if (error) return <ErrorBox>{String(error)}</ErrorBox>;
  if (!data) return null;

  const venue = data.venues[data.default_venue];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold text-neutral-100">Configuración del módulo 15m</h2>
        <Badge tone="muted">solo lectura</Badge>
      </div>

      <Card>
        <p className="text-sm text-neutral-400">{data.editable_reason}.</p>
        <p className="mt-2 text-xs text-neutral-500">
          Se cambian en <span className="font-mono">backend/.env</span> y
          requieren reiniciar el backend. Antes de tocar{" "}
          <span className="font-mono">live_features</span> o{" "}
          <span className="font-mono">vol_kind</span> con una muestra forward en
          curso, purgar los registros viejos —
          <span className="font-mono">scripts/purge_stale_forecasts.py</span>— o
          la muestra queda mixta.
        </p>
      </Card>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 xl:grid-cols-3">
        <Card title="Runtime">
          <Row label="watchlist" value={data.runtime.watchlist.join(", ")} />
          <Row label="timeframe" value={data.runtime.default_timeframe} />
          <Row
            label="feed"
            value={`${data.runtime.feed_mode}${
              data.runtime.live_data ? "" : " (apagado)"
            }`}
            tone={data.runtime.live_data ? "" : "text-warn"}
          />
          <Row
            label="analista"
            value={data.runtime.live_analyst ? "encendido" : "apagado"}
            tone={data.runtime.live_analyst ? "" : "text-warn"}
          />
          <Row label="familias en vivo" value={data.runtime.live_features} />
          <Row
            label="reajuste cada"
            value={`${data.runtime.refit_every_bars} barras`}
          />
          <Row
            label="tracker cada"
            value={`${data.runtime.tracker_interval_min} min`}
          />
          <Row
            label="snapshots"
            value={`${data.runtime.snapshot_period} cada ${data.runtime.snapshot_interval_min} min`}
          />
          <Row
            label="umbral de señal"
            value={pct(data.runtime.signal_threshold, 0)}
            hint="sin efecto mientras el KPI 1 no pase el gate"
          />
        </Card>

        <Card title="Modelo">
          <Row label="versión" value={data.model.model_version} />
          <Row label="clasificador (KPI 1)" value={data.model.model_kind} />
          <Row label="volatilidad (KPI 2)" value={data.model.vol_kind} />
          <Row label="direcciones" value={data.model.directions.join(", ")} />
          <Row
            label="niveles del cono"
            value={data.model.conformal_alphas
              .map((a) => `${Math.round((1 - a) * 100)}%`)
              .join(" / ")}
          />
          <Row label="folds" value={String(data.model.n_splits)} />
          <Row label="embargo" value={pct(data.model.embargo_frac, 1)} />
          <Row label="semilla" value={String(data.model.seed)} />
        </Card>

        <Card title="Setup (barreras del etiquetado)">
          <Row label="TP" value={`${num(data.barrier.tp_mult, 2)} σ`} />
          <Row label="SL" value={`${num(data.barrier.sl_mult, 2)} σ`} />
          <Row label="horizonte" value={`${data.barrier.horizon_bars} barras`} />
          <Row
            label="ventana de σ"
            value={`${data.barrier.vol_window_bars} barras`}
          />
          <Row label="fees ida y vuelta" value={pct(data.barrier.fee_roundtrip_pct, 3)} />
          <Row label="slippage" value={pct(data.barrier.slippage_pct, 3)} />
          <Row label="funding / 8h" value={pct(data.barrier.funding_pct_per_8h, 3)} />
          <p className="mt-2 border-t border-neutral-800 pt-2 text-[11px] text-neutral-500">
            La sigma es la del horizonte completo, no la de una barra. Con sigma
            por barra el costo pesaría ~5:1 contra el setup y sería imposible de
            ganar por aritmética, no por falta de señal.
          </p>
        </Card>

        <Card title="Gate — umbrales de salida">
          <Row label="AUC mínimo" value={num(data.gate.min_auc, 2)} />
          <Row label="BSS mínimo" value={num(data.gate.min_bss, 2)} />
          <Row
            label="error de calibración máximo"
            value={`${num(data.gate.max_calibration_error_pp, 1)}pp`}
          />
          <p className="mt-2 border-t border-neutral-800 pt-2 text-[11px] text-neutral-500">
            Los dos criterios, en todas las direcciones. Ninguna señal se muestra
            como operable sin los dos.
          </p>
        </Card>

        <Card title={`Venue de ejecución — ${venue?.name ?? "—"}`}>
          <Row label="fee maker / taker" value={`${pct(venue?.maker_fee, 3)} / ${pct(venue?.taker_fee, 3)}`} />
          <Row label="slippage de referencia" value={pct(venue?.slippage_pct, 3)} />
          <Row label="funding cada" value={`${num(venue?.funding_interval_hours, 0)} h`} />
          <Row label="leverage máximo del venue" value={multiplier(venue?.max_leverage, 0)} />
          <Row label="MMR base" value={pct(data.leverage.default_mmr, 2)} />
          <Row
            label="buffer de seguridad"
            value={`${num(data.leverage.safety_buffer, 1)}×`}
            hint="cuántas veces el SL tiene que caber antes de la liquidación"
          />
          <p className="mt-2 border-t border-neutral-800 pt-2 text-[11px] text-neutral-500">
            {venue?.notes}
          </p>
        </Card>

        <Card title="Preferencias de vista (este navegador)">
          <Row label="leverage del slider" value={multiplier(leverage, 0)} />
          <Row
            label="nivel del cono"
            value={`${Math.round((1 - Number(coneAlpha)) * 100)}%`}
          />
          <p className="mt-2 border-t border-neutral-800 pt-2 text-[11px] text-neutral-500">
            Se guardan en localStorage y no viajan al servidor: no cambian ningún
            modelo ni la muestra que se está midiendo.
          </p>
        </Card>

        <Card title="Integraciones">
          <Row
            label="Telegram"
            value={
              data.integrations.telegram_configured ? "configurado" : "sin configurar"
            }
            tone={data.integrations.telegram_configured ? "text-accent" : ""}
          />
          <Row
            label="Outliers Club"
            value={
              data.integrations.outliers_configured ? "configurado" : "sin configurar"
            }
            tone={data.integrations.outliers_configured ? "text-accent" : ""}
          />
          <p className="mt-2 border-t border-neutral-800 pt-2 text-[11px] text-neutral-500">
            Las credenciales viven solo en <span className="font-mono">.env</span>{" "}
            local y no salen del disco: la API dice si están, nunca cuáles son.
            Las alertas llegan en la Fase 7.
          </p>
        </Card>
      </div>
    </div>
  );
}
