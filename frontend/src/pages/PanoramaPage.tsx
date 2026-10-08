import { useQuery } from "@tanstack/react-query";

import { ErrorBox, Loading } from "../components/common/Card";
import {
  AltseasonCard,
  BreadthCard,
  CycleCard,
  FundingCard,
  OptionsCard,
  RecessionCard,
  StablecoinsCard,
} from "../components/marea/Cards";
import { api } from "../lib/api";

/**
 * Cripto ▸ Panorama (M5 + M6.1). Ciclo, amplitud, apalancamiento, opciones y
 * dinero disponible, cada uno con su fecha y su respaldo a un clic. Todo es
 * monitoreo: ninguna tarjeta pronostica (`docs/M5_ALCANCE.md` §5).
 */
export function PanoramaPage() {
  const q = useQuery({
    queryKey: ["macro", "panorama"],
    queryFn: () => api.panorama(),
    retry: false,
    staleTime: 600_000,
  });
  if (q.isLoading) return <Loading what="el panorama (unos segundos la primera vez)" />;
  if (q.error || !q.data) return <ErrorBox>{String(q.error)}</ErrorBox>;
  const p = q.data;
  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <header>
        <h1 className="text-2xl font-semibold text-neutral-100">Panorama cripto</h1>
        <p className="mt-1 text-sm text-neutral-400">
          Dónde está el ciclo, cuán extendida está la subida y cuánto apalancamiento hay. Describe; no pronostica.
        </p>
      </header>
      <div className="grid items-start gap-4 md:grid-cols-2 xl:grid-cols-3">
        <CycleCard p={p} />
        <BreadthCard p={p} />
        <AltseasonCard p={p} />
        <StablecoinsCard p={p} />
        <FundingCard p={p} />
        <OptionsCard p={p} />
        <RecessionCard p={p} />
      </div>
    </div>
  );
}
