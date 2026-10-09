import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";

import { ErrorBox, Loading } from "../components/common/Card";
import {
  CycleCard,
  LiquidityCard,
  LiquidityHero,
  PanelCard,
  RecessionCard,
  StablecoinsCard,
  StrengthCard,
} from "../components/marea/Cards";
import { api } from "../lib/api";

/**
 * Inicio de MAREA (M6.2). Seis lecturas, cada una con su número, una flecha de
 * cambio, una frase de qué significa y otra de qué no significa; lo técnico a
 * un clic. Nada de lo que se muestra acá es una recomendación (regla 2).
 *
 * Mientras se elige (`docs/M6_ALCANCE.md` §4.3 y §6) hay dos variantes con las
 * mismas tarjetas y las mismas cifras; cambia solo el dibujo:
 *
 * - **A**: seis tarjetas iguales, cada una con su gráfico chico.
 * - **B**: la liquidez arriba como lectura principal con su historia completa,
 *   y las otras cinco sin gráfico.
 *
 * `?v=b` elige la B. El selector se borra cuando Nichelson decida.
 */
export function HomePage() {
  const [params, setParams] = useSearchParams();
  const variant = params.get("v") === "b" ? "b" : "a";

  const liq = useQuery({
    queryKey: ["macro", "liquidity"],
    queryFn: () => api.liquidity(),
    retry: false,
    staleTime: 600_000,
  });
  const pano = useQuery({
    queryKey: ["macro", "panorama"],
    queryFn: () => api.panorama(),
    retry: false,
    staleTime: 600_000,
  });
  const strength = useQuery({
    // Misma clave que Flujos con su ventana por defecto: una sola petición.
    queryKey: ["macro", "strength", 26],
    queryFn: () => api.strength(26),
    retry: false,
    staleTime: 600_000,
  });
  const panel = useQuery({
    queryKey: ["macro", "panel-status"],
    queryFn: () => api.panelStatus(),
    retry: false,
    staleTime: 600_000,
  });

  const chart = variant === "a";
  const liquidity = liq.isLoading ? (
    <Loading what="la liquidez" />
  ) : liq.data ? (
    variant === "a" ? (
      <LiquidityCard rep={liq.data} chart />
    ) : (
      <LiquidityHero rep={liq.data} />
    )
  ) : (
    <ErrorBox>{String(liq.error)}</ErrorBox>
  );
  const panoCards = pano.data ? (
    <>
      <StablecoinsCard p={pano.data} chart={chart} />
      <CycleCard p={pano.data} chart={chart} />
      <RecessionCard p={pano.data} chart={chart} />
    </>
  ) : pano.isLoading ? (
    <Loading what="el panorama (unos segundos la primera vez)" />
  ) : (
    <ErrorBox>{String(pano.error)}</ErrorBox>
  );
  const strengthCard = strength.data ? (
    <StrengthCard rep={strength.data} chart={chart} />
  ) : strength.error ? (
    <ErrorBox>{String(strength.error)}</ErrorBox>
  ) : null;
  const panelCard = panel.data ? <PanelCard st={panel.data} /> : null;

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-neutral-100">Cómo está el mercado</h1>
          <p className="mt-1 text-sm text-neutral-400">
            Lo que miden los datos hoy. Describe el momento; no dice qué comprar ni hacia dónde va el precio.
          </p>
        </div>
        <div className="flex items-center gap-2 text-xs text-neutral-500">
          <span>M6.2 · variante en evaluación</span>
          <div className="flex overflow-hidden rounded border border-neutral-800">
            {(["a", "b"] as const).map((v) => (
              <button
                key={v}
                type="button"
                onClick={() => setParams(v === "a" ? {} : { v })}
                className={`px-2.5 py-1 ${
                  v === variant ? "bg-neutral-800 text-neutral-100" : "text-neutral-400 hover:text-neutral-200"
                }`}
              >
                {v.toUpperCase()}
              </button>
            ))}
          </div>
        </div>
      </header>

      {variant === "a" ? (
        <div className="grid items-start gap-4 md:grid-cols-2 xl:grid-cols-3">
          {liquidity}
          {panoCards}
          {strengthCard}
          {panelCard}
        </div>
      ) : (
        <>
          {liquidity}
          <div className="grid items-start gap-4 md:grid-cols-3">{panoCards}</div>
          <div className="grid items-start gap-4 md:grid-cols-2">
            {strengthCard}
            {panelCard}
          </div>
        </>
      )}
    </div>
  );
}
