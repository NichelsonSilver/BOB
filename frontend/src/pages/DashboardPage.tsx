import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Controller, useForm } from "react-hook-form";
import { z } from "zod";

import { BotDetailModal } from "../components/bots/BotDetailModal";
import {
  api,
  type BotPreview,
  type BotStatus,
  type CreateBotBody,
  type RangeSuggestion,
} from "../lib/api";
import { useBotsStore } from "../stores/botsStore";

const schema = z.object({
  bot_id: z.string().min(1, "requerido"),
  symbol: z.string().min(1),
  direction: z.enum(["long", "short", "neutral"]),
  price_low: z.string().refine((v) => Number(v) > 0, "> 0"),
  price_high: z.string().refine((v) => Number(v) > 0, "> 0"),
  n_grids: z.coerce.number().int().min(2).max(500),
  investment_usdt: z.string().refine((v) => Number(v) > 0, "> 0"),
  leverage: z.coerce.number().int().min(1).max(20),
  spacing: z.enum(["arithmetic", "geometric"]),
  mode: z.enum(["paper", "live"]),
});

type FormValues = z.infer<typeof schema>;

export function DashboardPage() {
  const qc = useQueryClient();
  const [createdMsg, setCreatedMsg] = useState<string | null>(null);
  const [detailBotId, setDetailBotId] = useState<string | null>(null);

  const symbolsQ = useQuery({ queryKey: ["symbols"], queryFn: api.getSymbols });
  const pnlQ = useQuery({ queryKey: ["pnl-global"], queryFn: () => api.getPnl() });
  const botsFromWs = useBotsStore((s) => s.bots);
  const botsQ = useQuery({
    queryKey: ["bots"],
    queryFn: api.listBots,
    refetchInterval: 5_000,
  });
  const bots = botsFromWs.length ? botsFromWs : botsQ.data ?? [];

  const {
    register,
    handleSubmit,
    watch,
    control,
    formState: { errors, isSubmitting },
    reset,
    setValue,
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      bot_id: "",
      symbol: "BTC_USDT_Perp",
      direction: "long",
      price_low: "",
      price_high: "",
      n_grids: 20,
      investment_usdt: "100",
      leverage: 5,
      spacing: "arithmetic",
      mode: "paper",
    },
  });

  const values = watch();
  const { symbol, mode, leverage, n_grids } = values;

  const [step, setStep] = useState<string>("");
  const [inputMode, setInputMode] = useState<"step" | "range" | null>(null);

  const tickerQ = useQuery({
    queryKey: ["ticker", symbol],
    queryFn: () => api.getTicker(symbol),
    refetchInterval: 5_000,
    enabled: Boolean(symbol),
  });
  const markPrice = tickerQ.data
    ? Number((tickerQ.data as Record<string, unknown>).mark_price ?? 0)
    : null;

  const [rangeMode, setRangeMode] = useState<"percentile" | "minmax" | "atr">(
    "percentile",
  );
  const [rangeDays, setRangeDays] = useState<number>(30);

  const suggestionQ = useQuery({
    queryKey: [
      "range-suggestion",
      symbol,
      rangeMode,
      rangeDays,
      values.investment_usdt,
      values.leverage,
    ],
    queryFn: () =>
      api.getRangeSuggestion({
        symbol,
        mode: rangeMode,
        days: rangeDays,
        investment_usdt: Number(values.investment_usdt) || 100,
        leverage: values.leverage,
      }),
    enabled: Boolean(symbol),
    staleTime: 60_000,
  });

  // Autofill on first suggestion for this symbol (or when the user switches
  // symbol). Doesn't overwrite user's manual edits on re-fetch.
  const [autofilledFor, setAutofilledFor] = useState<string | null>(null);
  useEffect(() => {
    if (!suggestionQ.data) return;
    if (autofilledFor === symbol) return;
    const s = suggestionQ.data;
    setValue("price_low", s.price_low);
    setValue("price_high", s.price_high);
    setValue("n_grids", s.suggested_n_grids);
    const derivedStep = (Number(s.price_high) - Number(s.price_low)) / s.suggested_n_grids;
    setStep(derivedStep.toFixed(2));
    setInputMode("range");
    setAutofilledFor(symbol);
  }, [suggestionQ.data, symbol, autofilledFor, setValue]);

  const applySuggestion = () => {
    const s = suggestionQ.data;
    if (!s) return;
    setValue("price_low", s.price_low);
    setValue("price_high", s.price_high);
    setValue("n_grids", s.suggested_n_grids);
    const derivedStep = (Number(s.price_high) - Number(s.price_low)) / s.suggested_n_grids;
    setStep(derivedStep.toFixed(2));
    setInputMode("range");
  };

  const previewQ = useQuery({
    queryKey: ["bot-preview", values],
    queryFn: () => api.previewBot(values as CreateBotBody),
    enabled:
      Number(values.price_low) > 0 &&
      Number(values.price_high) > Number(values.price_low) &&
      Number(values.investment_usdt) > 0 &&
      values.n_grids >= 2,
    staleTime: 1_000,
  });

  const preview = previewQ.data;

  // ── Dynamic mode handlers ──────────────────────────────────────
  const handleStepChange = (newStep: string) => {
    setStep(newStep);
    setInputMode("step");
    const stepNum = Number(newStep);
    const mark = markPrice;
    const n = values.n_grids;
    if (!mark || stepNum <= 0 || n < 2) return;
    const half = (stepNum * n) / 2;
    setValue("price_low", (mark - half).toFixed(2));
    setValue("price_high", (mark + half).toFixed(2));
  };

  const handlePriceLowChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setValue("price_low", e.target.value);
    setInputMode("range");
    const low = Number(e.target.value);
    const high = Number(values.price_high);
    if (low > 0 && high > low && values.n_grids >= 2)
      setStep(((high - low) / values.n_grids).toFixed(2));
  };

  const handlePriceHighChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setValue("price_high", e.target.value);
    setInputMode("range");
    const high = Number(e.target.value);
    const low = Number(values.price_low);
    if (low > 0 && high > low && values.n_grids >= 2)
      setStep(((high - low) / values.n_grids).toFixed(2));
  };

  const handleNGridsChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const n = Number(e.target.value);
    setValue("n_grids", n);
    if (inputMode === "step") {
      const stepNum = Number(step);
      const mark = markPrice;
      if (!mark || stepNum <= 0) return;
      setValue("price_low", (mark - (stepNum * n) / 2).toFixed(2));
      setValue("price_high", (mark + (stepNum * n) / 2).toFixed(2));
    } else {
      const low = Number(values.price_low);
      const high = Number(values.price_high);
      if (low > 0 && high > low && n >= 2)
        setStep(((high - low) / n).toFixed(2));
    }
  };

  // Auto-maximize n_grids in step mode when preview updates max
  useEffect(() => {
    if (!preview?.max_grids_allowed || inputMode !== "step") return;
    const maxN = preview.max_grids_allowed;
    if (n_grids === maxN) return;
    setValue("n_grids", maxN);
    const stepNum = Number(step);
    const mark = markPrice;
    if (!mark || stepNum <= 0) return;
    setValue("price_low", (mark - (stepNum * maxN) / 2).toFixed(2));
    setValue("price_high", (mark + (stepNum * maxN) / 2).toFixed(2));
  }, [preview?.max_grids_allowed, inputMode]);

  const createMut = useMutation({
    mutationFn: (body: CreateBotBody) => api.createBot(body),
    onSuccess: (r) => {
      setCreatedMsg(`Bot creado: ${r.bot_id} (${r.mode})`);
      qc.invalidateQueries({ queryKey: ["bots"] });
      reset({
        ...values,
        bot_id: "",
      });
    },
    onError: (e) => setCreatedMsg(`Error: ${(e as Error).message}`),
  });

  const onSubmit = async (v: FormValues) => {
    if (v.mode === "live") {
      const ok = window.confirm(
        `LIVE en GRVT testnet.\n\nSe colocarán hasta ${v.n_grids} órdenes reales.\nSímbolo: ${v.symbol}\nInversión: ${v.investment_usdt} USDT @ ${v.leverage}x\n\n¿Confirmás?`,
      );
      if (!ok) return;
    }
    createMut.mutate(v as CreateBotBody);
  };

  // KPIs
  const totalPnl = Number(pnlQ.data?.realized_pnl ?? "0");
  const totalVolume = Number(pnlQ.data?.gross_volume ?? "0");
  const gridProfit = totalPnl; // Same source — fills-based realized PnL
  const balanceTotal = 778.86; // Placeholder; wire to /api/settings or account later
  const trendPnl = 0; // Not tracked yet — placeholder
  const totalDisplay = gridProfit + trendPnl;

  return (
    <div className="mx-auto grid max-w-6xl gap-6">
      {/* Header + live badge */}
      <div className="flex flex-col items-center gap-2 pt-2">
        <h1 className="flex items-center gap-2 text-2xl font-semibold text-neutral-100">
          <span aria-hidden>⚡</span> GRVT Grid Bot Dashboard
        </h1>
        <span className="inline-flex items-center gap-1 rounded-full border border-accent/40 bg-accent/10 px-3 py-1 text-xs font-semibold text-accent">
          <span className="h-1.5 w-1.5 rounded-full bg-accent" aria-hidden />
          Live Trading
        </span>
      </div>

      {/* KPI cards */}
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard
          icon="💰"
          label="BALANCE TOTAL"
          value={`$${balanceTotal.toFixed(2)}`}
          tone="neutral"
        />
        <KpiCard
          icon="🧮"
          label="GRID PROFIT"
          value={`$${gridProfit.toFixed(2)}`}
          tone={gridProfit >= 0 ? "accent" : "danger"}
        />
        <KpiCard
          icon="📈"
          label="TREND PNL"
          value={`$${trendPnl.toFixed(2)}`}
          tone={trendPnl >= 0 ? "accent" : "danger"}
        />
        <KpiCard
          icon="🎯"
          label="TOTAL PNL"
          value={`$${totalDisplay.toFixed(2)}`}
          tone={totalDisplay >= 0 ? "accent" : "danger"}
          sub={`volumen: ${totalVolume.toFixed(2)} USDT`}
        />
      </div>

      {/* Create bot card */}
      <section className="rounded-2xl border border-neutral-800 bg-neutral-900/70 p-5">
        <header className="mb-1 flex items-center gap-2">
          <span aria-hidden>🤖</span>
          <h2 className="text-base font-semibold text-neutral-100">Crear Nuevo Bot</h2>
        </header>
        <p className="mb-4 text-xs text-neutral-500">
          Configurá estrategia de grid trading. Bot se crea PAUSADO.
        </p>

        <form onSubmit={handleSubmit(onSubmit)} className="grid gap-4 lg:grid-cols-2">
          {/* Left column */}
          <div className="grid gap-3">
            <Controller
              name="mode"
              control={control}
              render={({ field }) => (
                <ModeToggle value={field.value} onChange={field.onChange} />
              )}
            />

            <Field label="Par de Trading">
              <select {...register("symbol")} className={inputCls}>
                {(symbolsQ.data?.symbols ?? [symbol]).map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </Field>

            <Field label="Dirección">
              <select {...register("direction")} className={inputCls}>
                <option value="long">Long (Alcista)</option>
                <option value="short">Short (Bajista)</option>
                <option value="neutral">Neutral</option>
              </select>
            </Field>

            <Field label={`Leverage: ${leverage}x`}>
              <input
                type="range"
                min={1}
                max={20}
                step={1}
                {...register("leverage", { valueAsNumber: true })}
                className="w-full accent-accent"
              />
              <div className="mt-1 flex justify-between text-xs text-neutral-500">
                <span>1x</span>
                <span>20x</span>
              </div>
            </Field>

            <Field
              label={
                <span className="flex items-center gap-1">
                  {`Número de Rejillas: ${n_grids}`}
                  {inputMode === "step" && (
                    <span className="rounded bg-accent/20 px-1 text-[10px] font-bold text-accent">
                      AUTO
                    </span>
                  )}
                </span>
              }
              error={errors.n_grids?.message}
            >
              <input
                type="range"
                min={2}
                max={preview?.max_grids_allowed ?? 100}
                step={1}
                value={n_grids}
                onChange={handleNGridsChange}
                className="w-full accent-accent"
              />
              <div className="mt-1 flex justify-between text-xs text-neutral-500">
                <span>2</span>
                <span>
                  máx: {preview?.max_grids_allowed ?? "—"}
                </span>
              </div>
            </Field>

            <Field label="bot_id" error={errors.bot_id?.message}>
              <input
                {...register("bot_id")}
                className={inputCls}
                placeholder="p.ej. btc-grid-01"
              />
            </Field>
          </div>

          {/* Right column */}
          <div className="grid gap-3">
            <SuggestionBox
              loading={suggestionQ.isFetching}
              data={suggestionQ.data}
              mode={rangeMode}
              days={rangeDays}
              onMode={setRangeMode}
              onDays={setRangeDays}
              onApply={applySuggestion}
            />

            {/* Mark price reference */}
            <div className="flex items-center justify-between rounded-md border border-neutral-800 bg-neutral-950/50 px-3 py-2 text-xs">
              <span className="text-neutral-500">Precio Mark</span>
              <span className="font-mono font-semibold text-accent">
                {markPrice != null ? `$${markPrice.toLocaleString()}` : "—"}
              </span>
            </div>

            {/* Step input — MODE A */}
            <Field
              label={
                <span className="flex items-center gap-1">
                  Step (distancia entre niveles, $)
                  {inputMode === "range" && (
                    <span className="rounded bg-neutral-700/60 px-1 text-[10px] font-bold text-neutral-400">
                      AUTO
                    </span>
                  )}
                </span>
              }
            >
              <input
                type="number"
                value={step}
                onChange={(e) => handleStepChange(e.target.value)}
                onFocus={() => setInputMode("step")}
                className={inputCls}
                placeholder="ej. 100"
                min={0}
              />
            </Field>

            {/* Price bounds — MODE B */}
            <Field
              label={
                <span className="flex items-center gap-1">
                  Precio Inferior ($)
                  {inputMode === "step" && (
                    <span className="rounded bg-accent/20 px-1 text-[10px] font-bold text-accent">
                      AUTO
                    </span>
                  )}
                </span>
              }
              error={errors.price_low?.message}
            >
              <input
                type="number"
                value={values.price_low}
                onChange={handlePriceLowChange}
                onFocus={() => setInputMode("range")}
                className={inputCls}
                min={0}
              />
            </Field>
            <Field
              label={
                <span className="flex items-center gap-1">
                  Precio Superior ($)
                  {inputMode === "step" && (
                    <span className="rounded bg-accent/20 px-1 text-[10px] font-bold text-accent">
                      AUTO
                    </span>
                  )}
                </span>
              }
              error={errors.price_high?.message}
            >
              <input
                type="number"
                value={values.price_high}
                onChange={handlePriceHighChange}
                onFocus={() => setInputMode("range")}
                className={inputCls}
                min={0}
              />
            </Field>

            <Field
              label="Inversión (USDT)"
              error={errors.investment_usdt?.message}
            >
              <input {...register("investment_usdt")} className={inputCls} />
            </Field>

            <PreviewPanel preview={preview} loading={previewQ.isFetching} />
          </div>

          <div className="lg:col-span-2">
            <button
              type="submit"
              disabled={isSubmitting || createMut.isPending}
              className={`w-full rounded-md px-3 py-3 font-semibold tracking-wide transition disabled:opacity-50 ${
                mode === "live"
                  ? "bg-danger text-white hover:bg-red-500"
                  : "bg-accent text-neutral-900 hover:bg-cyan-300"
              }`}
            >
              {createMut.isPending
                ? "creando…"
                : mode === "live"
                ? "CREAR BOT LIVE (GRVT)"
                : "CREAR BOT (PAUSADO)"}
            </button>
            {createdMsg && (
              <div className="mt-2 text-xs text-neutral-400">{createdMsg}</div>
            )}
          </div>
        </form>
      </section>

      {/* Active bots section */}
      <section className="rounded-2xl border border-neutral-800 bg-neutral-900/70 p-5">
        <header className="mb-1 flex items-center gap-2">
          <span aria-hidden>🛡</span>
          <h2 className="text-base font-semibold text-neutral-100">Bots Activos</h2>
        </header>
        <p className="mb-4 text-xs text-neutral-500">
          Gestión de estrategias de trading
        </p>

        {bots.length === 0 ? (
          <div className="rounded-md border border-dashed border-neutral-800 p-8 text-center text-sm text-neutral-500">
            No hay bots activos. Creá uno arriba.
          </div>
        ) : (
          <div className="grid gap-3">
            {bots.map((bot) => (
              <BotRowCard
                key={bot.bot_id}
                bot={bot}
                onOpen={() => setDetailBotId(bot.bot_id)}
              />
            ))}
          </div>
        )}
      </section>

      {detailBotId && (
        <BotDetailModal
          botId={detailBotId}
          onClose={() => setDetailBotId(null)}
        />
      )}
    </div>
  );
}

function KpiCard({
  icon,
  label,
  value,
  sub,
  tone = "neutral",
}: {
  icon: string;
  label: string;
  value: string;
  sub?: string;
  tone?: "neutral" | "accent" | "danger";
}) {
  const toneCls =
    tone === "accent"
      ? "text-accent"
      : tone === "danger"
      ? "text-danger"
      : "text-neutral-100";
  return (
    <div className="rounded-2xl border border-neutral-800 bg-neutral-900/70 p-4">
      <div className="flex items-center gap-2 text-xs uppercase tracking-wider text-neutral-500">
        <span aria-hidden>{icon}</span> {label}
      </div>
      <div className={`mt-2 font-mono text-3xl font-bold ${toneCls}`}>{value}</div>
      {sub && <div className="mt-1 text-xs text-neutral-500">{sub}</div>}
    </div>
  );
}

function SuggestionBox({
  loading,
  data,
  mode,
  days,
  onMode,
  onDays,
  onApply,
}: {
  loading: boolean;
  data: RangeSuggestion | undefined;
  mode: "percentile" | "minmax" | "atr";
  days: number;
  onMode: (m: "percentile" | "minmax" | "atr") => void;
  onDays: (d: number) => void;
  onApply: () => void;
}) {
  return (
    <div className="rounded-md border border-neutral-700 bg-neutral-950/60 p-3">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-xs font-semibold uppercase tracking-wider text-accent">
          Sugerencia desde 1D
        </span>
        <button
          type="button"
          onClick={onApply}
          disabled={!data || loading}
          className="rounded border border-accent/50 bg-accent/10 px-2 py-0.5 text-xs text-accent hover:bg-accent/20 disabled:opacity-40"
        >
          Aplicar
        </button>
      </div>
      <div className="mb-2 flex items-center gap-2 text-xs">
        <label className="flex items-center gap-1 text-neutral-400">
          modo
          <select
            value={mode}
            onChange={(e) => onMode(e.target.value as typeof mode)}
            className="rounded border border-neutral-700 bg-neutral-950 px-1 py-0.5 text-neutral-100"
          >
            <option value="percentile">percentile p10/p90</option>
            <option value="minmax">min/max</option>
            <option value="atr">atr ±3σ</option>
          </select>
        </label>
        <label className="flex items-center gap-1 text-neutral-400">
          días
          <select
            value={days}
            onChange={(e) => onDays(Number(e.target.value))}
            className="rounded border border-neutral-700 bg-neutral-950 px-1 py-0.5 text-neutral-100"
          >
            {[7, 14, 30, 60, 90].map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </label>
      </div>
      {loading && (
        <div className="text-xs text-neutral-500">Calculando sugerencia…</div>
      )}
      {data && !loading && (
        <div className="grid grid-cols-2 gap-y-1 text-xs">
          <SuggestionRow label="Low" value={`$${data.price_low}`} />
          <SuggestionRow label="High" value={`$${data.price_high}`} />
          <SuggestionRow label="Grids sugeridos" value={String(data.suggested_n_grids)} />
          <SuggestionRow
            label="Volatilidad"
            value={`${Number(data.volatility_pct).toFixed(2)}%`}
          />
          <SuggestionRow label="ATR" value={`$${Number(data.atr).toFixed(2)}`} />
          <SuggestionRow label="Muestra" value={`${data.sample_size}d`} />
        </div>
      )}
    </div>
  );
}

function SuggestionRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between pr-3">
      <span className="text-neutral-500">{label}:</span>
      <span className="font-mono text-neutral-100">{value}</span>
    </div>
  );
}

function ModeToggle({
  value,
  onChange,
}: {
  value: "paper" | "live";
  onChange: (v: "paper" | "live") => void;
}) {
  return (
    <div className="grid grid-cols-2 overflow-hidden rounded-md border border-neutral-700">
      {(["paper", "live"] as const).map((m) => {
        const active = value === m;
        const base = "py-2 text-xs font-semibold transition";
        const color = active
          ? m === "live"
            ? "bg-danger text-white"
            : "bg-accent text-neutral-900"
          : "bg-neutral-950 text-neutral-400 hover:bg-neutral-800";
        return (
          <button
            key={m}
            type="button"
            className={`${base} ${color}`}
            onClick={() => onChange(m)}
          >
            {m === "paper" ? "PAPER (simulado)" : "LIVE (GRVT testnet)"}
          </button>
        );
      })}
    </div>
  );
}

function PreviewPanel({
  preview,
  loading,
}: {
  preview: BotPreview | undefined;
  loading: boolean;
}) {
  if (!preview && loading) {
    return (
      <div className="rounded-md border border-accent/30 bg-accent/5 p-3 text-sm text-neutral-500">
        Calculando preview…
      </div>
    );
  }
  if (!preview) {
    return (
      <div className="rounded-md border border-neutral-800 bg-neutral-950 p-3 text-sm text-neutral-500">
        Completá precio e inversión para ver el preview.
      </div>
    );
  }

  const fmt = (v: string | number, digits = 2) => {
    const n = Number(v);
    if (!Number.isFinite(n)) return "—";
    return `$${n.toFixed(digits)}`;
  };

  return (
    <div className="rounded-md border border-accent/40 bg-accent/5 p-3">
      <div className="mb-2 text-xs font-bold uppercase tracking-wider text-accent">
        PREVIEW DE LA ESTRATEGIA
      </div>
      <div className="grid grid-cols-2 gap-y-2 text-sm">
        <PreviewRow
          label="Profit/Grid Estimado"
          value={fmt(preview.profit_per_grid_usdt, 4)}
        />
        <PreviewRow
          label="Precio de Liquidación"
          value={fmt(preview.liquidation_price, 2)}
        />
        <PreviewRow
          label="Margen Requerido"
          value={fmt(preview.margin_required, 2)}
        />
        <PreviewRow
          label="Cantidad/Grid"
          value={preview.qty_per_grid}
          mono
        />
        <PreviewRow
          label="Máx Grids Permitidos"
          value={String(preview.max_grids_allowed)}
        />
        <PreviewRow
          label="Inversión/Grid"
          value={fmt(preview.inversion_per_grid, 2)}
        />
      </div>
      {preview.warnings.length > 0 && (
        <ul className="mt-3 grid gap-1 rounded border border-yellow-600/40 bg-yellow-600/10 p-2 text-xs text-yellow-300">
          {preview.warnings.map((w) => (
            <li key={w}>⚠ {w}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function PreviewRow({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex items-baseline justify-between gap-4 pr-3">
      <span className="text-xs text-neutral-400">{label}:</span>
      <span
        className={`font-semibold ${mono ? "font-mono" : ""} text-neutral-100`}
      >
        {value}
      </span>
    </div>
  );
}

function BotRowCard({
  bot,
  onOpen,
}: {
  bot: BotStatus;
  onOpen: () => void;
}) {
  const qc = useQueryClient();
  const pauseMut = useMutation({
    mutationFn: () => api.pauseBot(bot.bot_id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["bots"] }),
  });
  const resumeMut = useMutation({
    mutationFn: () => api.resumeBot(bot.bot_id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["bots"] }),
  });
  const stopMut = useMutation({
    mutationFn: () => api.stopBot(bot.bot_id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["bots"] }),
  });

  const pnl = Number(bot.realized_pnl ?? "0");
  const volume = Number(bot.total_volume ?? "0");
  const stateColor =
    bot.state === "running"
      ? "border-accent/40 text-accent"
      : bot.state === "paused"
      ? "border-yellow-500/40 text-yellow-400"
      : "border-neutral-600 text-neutral-400";

  return (
    <div className="rounded-lg border border-neutral-800 bg-neutral-950/50 p-4">
      <div className="mb-3 h-px bg-gradient-to-r from-accent/60 to-transparent" />
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="text-sm font-semibold tracking-wide text-neutral-100">
            {bot.symbol} {bot.direction.toUpperCase()}
            <span className="ml-2 text-xs text-neutral-500">
              · {bot.bot_id} · {bot.mode}
            </span>
          </div>
        </div>
        <span
          className={`rounded-full border px-2 py-0.5 text-xs font-semibold uppercase ${stateColor}`}
        >
          {bot.state}
        </span>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-x-6 gap-y-2 text-sm md:grid-cols-3">
        <BotStat label="PnL" value={`$${pnl.toFixed(2)}`} tone={pnl >= 0 ? "accent" : "danger"} />
        <BotStat label="Leverage" value={`${bot.leverage}x`} />
        <BotStat
          label="Grid Profit"
          value={`$${pnl.toFixed(2)}`}
          tone={pnl >= 0 ? "accent" : "danger"}
        />
        <BotStat label="Trend PnL" value="$0.00" />
        <BotStat
          label="Inversión"
          value={`$${Number(bot.investment_usdt).toFixed(0)}`}
        />
        <BotStat label="Volumen" value={`$${volume.toFixed(0)}`} />
      </div>

      <div className="mt-4 flex gap-2">
        {bot.state === "running" ? (
          <button
            onClick={() => pauseMut.mutate()}
            disabled={pauseMut.isPending}
            className="rounded-md border border-neutral-700 bg-neutral-800 px-4 py-1.5 text-xs font-semibold hover:bg-neutral-700 disabled:opacity-50"
          >
            PAUSAR
          </button>
        ) : bot.state === "paused" ? (
          <button
            onClick={() => resumeMut.mutate()}
            disabled={resumeMut.isPending}
            className="rounded-md border border-accent/50 bg-accent/10 px-4 py-1.5 text-xs font-semibold text-accent hover:bg-accent/20 disabled:opacity-50"
          >
            REANUDAR
          </button>
        ) : null}
        <button
          onClick={onOpen}
          className="rounded-md border border-neutral-700 bg-neutral-800 px-4 py-1.5 text-xs font-semibold hover:bg-neutral-700"
        >
          DETALLES
        </button>
        <button
          onClick={() => {
            if (window.confirm(`¿Cerrar el bot ${bot.bot_id}?`)) stopMut.mutate();
          }}
          disabled={stopMut.isPending}
          className="rounded-md bg-danger px-4 py-1.5 text-xs font-semibold text-white hover:bg-red-500 disabled:opacity-50"
        >
          CERRAR
        </button>
      </div>
    </div>
  );
}

function BotStat({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: string;
  tone?: "neutral" | "accent" | "danger";
}) {
  const toneCls =
    tone === "accent"
      ? "text-accent"
      : tone === "danger"
      ? "text-danger"
      : "text-neutral-100";
  return (
    <div className="flex items-baseline justify-between border-b border-neutral-800/60 pb-1">
      <span className="text-xs text-neutral-500">{label}:</span>
      <span className={`font-mono text-sm font-semibold ${toneCls}`}>{value}</span>
    </div>
  );
}

const inputCls =
  "w-full rounded-md border border-neutral-700 bg-neutral-950 px-3 py-2 font-mono text-sm text-neutral-100 focus:border-accent focus:outline-none";

function Field({
  label,
  error,
  children,
}: {
  label: React.ReactNode;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-xs font-medium text-neutral-300">{label}</span>
      {children}
      {error && <span className="text-xs text-danger">{error}</span>}
    </label>
  );
}
