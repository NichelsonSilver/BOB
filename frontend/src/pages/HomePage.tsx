import { useQuery } from "@tanstack/react-query";

import { ErrorBox, Loading } from "../components/common/Card";
import {
  CycleCard,
  LiquidityCard,
  PanelCard,
  RecessionCard,
  StablecoinsCard,
} from "../components/marea/Cards";
import { api } from "../lib/api";

/**
 * Inicio de MAREA (M6.1). Pocas tarjetas, cada una con su número, una frase
 * de qué significa y otra de qué no significa; lo técnico a un clic.
 *
 * Es la primera versión: qué tarjetas quedan, en qué orden y con qué gráfico
 * se decide en la M6.2 comparando variantes (`docs/M6_ALCANCE.md` §4.3).
 * Nada de lo que se muestra acá es una recomendación (regla 2).
 */
export function HomePage() {
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
  const panel = useQuery({
    queryKey: ["macro", "panel-status"],
    queryFn: () => api.panelStatus(),
    retry: false,
    staleTime: 600_000,
  });

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <header>
        <h1 className="text-2xl font-semibold text-neutral-100">Cómo está el mercado</h1>
        <p className="mt-1 text-sm text-neutral-400">
          Lo que miden los datos hoy. Describe el momento; no dice qué comprar ni hacia dónde va el precio.
        </p>
      </header>

      <div className="grid items-start gap-4 md:grid-cols-2 xl:grid-cols-3">
        {liq.isLoading ? (
          <Loading what="la liquidez" />
        ) : liq.data ? (
          <LiquidityCard rep={liq.data} />
        ) : (
          <ErrorBox>{String(liq.error)}</ErrorBox>
        )}
        {pano.isLoading && <Loading what="el panorama (unos segundos la primera vez)" />}
        {pano.error && <ErrorBox>{String(pano.error)}</ErrorBox>}
        {pano.data && (
          <>
            <StablecoinsCard p={pano.data} />
            <CycleCard p={pano.data} />
            <RecessionCard p={pano.data} />
          </>
        )}
        {panel.data && <PanelCard st={panel.data} />}
      </div>
    </div>
  );
}
