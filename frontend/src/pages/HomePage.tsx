import { useQuery } from "@tanstack/react-query";

import { ErrorBox, Loading } from "../components/common/Card";
import {
  CycleCard,
  LiquidityCard,
  PanelCard,
  RecessionCard,
  StablecoinsCard,
  StrengthCard,
} from "../components/marea/Cards";
import { useLang } from "../i18n";
import { api } from "../lib/api";

/**
 * Inicio de MAREA (M6.2). Seis lecturas, cada una con su número, una flecha de
 * cambio, una frase de qué significa y otra de qué no significa; lo técnico a
 * un clic. Nada de lo que se muestra acá es una recomendación (regla 2).
 *
 * Variante A, elegida por Nichelson el 2026-10-08 (`docs/M6_ALCANCE.md` §6):
 * seis tarjetas iguales, cada una con su gráfico chico.
 */
export function HomePage() {
  const { tr } = useLang();
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

  const liquidity = liq.isLoading ? (
    <Loading what={tr("liquidity", "la liquidez")} />
  ) : liq.data ? (
    <LiquidityCard rep={liq.data} chart />
  ) : (
    <ErrorBox>{String(liq.error)}</ErrorBox>
  );
  const panoCards = pano.data ? (
    <>
      <StablecoinsCard p={pano.data} chart />
      <CycleCard p={pano.data} chart />
      <RecessionCard p={pano.data} chart />
    </>
  ) : pano.isLoading ? (
    <Loading what={tr("the panorama (a few seconds the first time)", "el panorama (unos segundos la primera vez)")} />
  ) : (
    <ErrorBox>{String(pano.error)}</ErrorBox>
  );
  const strengthCard = strength.data ? (
    <StrengthCard rep={strength.data} chart />
  ) : strength.error ? (
    <ErrorBox>{String(strength.error)}</ErrorBox>
  ) : null;
  const panelCard = panel.data ? <PanelCard st={panel.data} /> : null;

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <header>
        <h1 className="text-2xl font-semibold text-neutral-100">{tr("Where the market stands", "Cómo está el mercado")}</h1>
        <p className="mt-1 text-sm text-neutral-400">
          {tr(
            "What the data measure today. It describes the moment; it does not say what to buy or where prices are going.",
            "Lo que miden los datos hoy. Describe el momento; no dice qué comprar ni hacia dónde va el precio.",
          )}
        </p>
      </header>

      <div className="grid items-start gap-4 md:grid-cols-2 xl:grid-cols-3">
        {liquidity}
        {panoCards}
        {strengthCard}
        {panelCard}
      </div>
    </div>
  );
}
