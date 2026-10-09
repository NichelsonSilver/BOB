import { useState } from "react";

/**
 * Glosario de MAREA (M6.5, `docs/M6_ALCANCE.md` §4.4). Cubre las palabras que
 * quedan en la portada y en los detalles después de simplificar el texto.
 *
 * **No lleva cifras.** Define palabras; los números de cada lectura viven en
 * su tarjeta y salen de la API. Una definición con un número adentro queda
 * vieja sin que nada falle.
 */
type Term = { term: string; plain: string; more?: string };

const GROUPS: { title: string; terms: Term[] }[] = [
  {
    title: "Lecturas de la portada",
    terms: [
      {
        term: "Liquidez global",
        plain: "Cuánto dinero hay disponible en el sistema financiero y si está creciendo o achicándose.",
        more: "MAREA combina cinco series con el mismo peso: el balance neto de la Fed, la masa monetaria M2, el dólar, la tasa real a 10 años y las condiciones financieras (NFCI).",
      },
      {
        term: "Fuerte / Normal / Débil",
        plain: "Si la liquidez avanza más rápido, igual o más lento que en la mayoría de las semanas anteriores.",
        more: "Se compara con el cambio a 13 semanas de toda la historia anterior a la fecha, partida en tercios. Técnicamente: expansivo, neutro, contractivo.",
      },
      {
        term: "Más que el X% de las semanas (percentil)",
        plain: "Ordenando todas las semanas anteriores, cuántas quedaron por debajo de la de hoy.",
        more: "Se trunca: si es 82.9 se dice 82, para no redondear a favor.",
      },
      {
        term: "Stablecoins",
        plain: "Criptomonedas atadas al dólar (USDT, USDC…). Su total en circulación es dinero que ya está dentro de cripto.",
      },
      {
        term: "Ciclo de BTC",
        plain: "Qué tan caro está BTC comparado con su propia historia, de 0 a 100.",
        more: "Se calcula solo con datos que existían en cada fecha. La versión que usa datos posteriores se ve mejor en los techos, pero no se puede reproducir en el momento.",
      },
      {
        term: "Riesgo de recesión",
        plain: "Probabilidad de que EE.UU. entre en recesión dentro de 12 meses, según la diferencia entre la tasa a 10 años y la de 3 meses.",
        more: "Es el modelo que publica la Fed de Nueva York, reproducido con datos públicos. No es un pronóstico de MAREA.",
      },
      {
        term: "Clases de activo",
        plain: "Grandes grupos de inversión: acciones, bonos, oro, cobre, dólar, bienes raíces… Se siguen con ETFs.",
      },
    ],
  },
  {
    title: "Cripto",
    terms: [
      {
        term: "Funding",
        plain: "Pago periódico entre quienes apuestan al alza y a la baja en un futuro perpetuo. Si es positivo, los que apuestan al alza le pagan al resto.",
      },
      {
        term: "Apalancamiento (leverage)",
        plain: "Operar con dinero prestado. A 10x, un movimiento de 1% en el precio es 10% de la cuenta.",
      },
      {
        term: "Liquidación",
        plain: "Cierre forzado de una posición apalancada cuando la pérdida se come el margen.",
      },
      {
        term: "Stop",
        plain: "Precio en el que se decide salir de una operación para limitar la pérdida.",
      },
      {
        term: "R",
        plain: "La pérdida aceptada en una operación, usada como unidad. Ganar 2 R es ganar el doble de lo que se arriesgó.",
      },
      {
        term: "Volatilidad (sigma, σ)",
        plain: "Cuánto se mueve el precio, sin importar hacia dónde.",
      },
      {
        term: "Cono de precio",
        plain: "Rango donde debería quedar el precio con una probabilidad dada (por ejemplo 80%).",
      },
      {
        term: "Régimen elevado",
        plain: "Momento en que se espera más movimiento que lo habitual: el stop salta más seguido y las liquidaciones se concentran ahí.",
      },
      {
        term: "Amplitud",
        plain: "Qué fracción de las criptomonedas está por encima de su promedio de largo plazo.",
      },
      {
        term: "Temporada de altcoins",
        plain: "Cuántas de las principales altcoins le ganaron a BTC en los últimos 90 días.",
      },
      {
        term: "Puts por call",
        plain: "Contratos de protección contra caídas abiertos por cada apuesta a la subida, en el mercado de opciones.",
      },
    ],
  },
  {
    title: "Cómo se mide (Laboratorio)",
    terms: [
      {
        term: "Pre-registro",
        plain: "Escribir y guardar el criterio de éxito antes de correr una prueba, para no acomodarlo después de ver el resultado.",
      },
      {
        term: "Backtest",
        plain: "Probar un modelo sobre el pasado, entrenándolo solo con lo que existía antes de cada fecha.",
      },
      {
        term: "Forward",
        plain: "Probar un modelo con datos que todavía no existían cuando emitió su pronóstico. Es la prueba más exigente.",
      },
      {
        term: "Placebo",
        plain: "La misma prueba con los resultados mezclados al azar. Si ahí también aparece ventaja, la prueba está mal hecha.",
      },
      {
        term: "Línea de comparación (baseline)",
        plain: "Un método simple y conocido. Un modelo tiene que ganarle para valer algo.",
      },
      {
        term: "AUC",
        plain: "Qué tan bien separa un modelo los casos que salen bien de los que salen mal. 0.5 es azar, 1 es perfecto.",
      },
      {
        term: "IC (coeficiente de información)",
        plain: "Correlación entre el orden que predijo el modelo y el orden que ocurrió. 0 es azar.",
        more: "No confundir con IC95%, que es el intervalo de confianza.",
      },
      {
        term: "IC95% (intervalo de confianza)",
        plain: "Rango donde probablemente está el valor real. Si incluye el cero, no se puede afirmar que haya efecto.",
      },
      {
        term: "Potencia",
        plain: "Qué tan chico puede ser un efecto y aun así detectarlo con los datos disponibles.",
      },
      {
        term: "Sin datos suficientes / sin potencia",
        plain: "Todavía no se puede saber si hay efecto. No es lo mismo que demostrar que no lo hay.",
      },
      {
        term: "Backfill",
        plain: "Un pronóstico calculado tarde, cuando el resultado ya empezaba a conocerse. Se guarda, pero no cuenta para la prueba.",
      },
    ],
  },
];

export function Glossary() {
  const [filter, setFilter] = useState("");
  const f = filter.trim().toLowerCase();
  return (
    <section id="glosario" className="rounded-lg border border-neutral-800 bg-neutral-900/70 p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="text-base font-semibold text-neutral-100">Glosario</h2>
        <input
          type="search"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="buscar una palabra"
          className="w-full rounded-md border border-neutral-700 bg-neutral-950 px-2 py-1 text-sm text-neutral-200 sm:w-56"
        />
      </div>
      <div className="mt-4 grid gap-6 lg:grid-cols-3">
        {GROUPS.map((g) => {
          const terms = g.terms.filter(
            (t) => !f || `${t.term} ${t.plain} ${t.more ?? ""}`.toLowerCase().includes(f),
          );
          if (!terms.length) return null;
          return (
            <div key={g.title}>
              <h3 className="mb-2 text-xs uppercase tracking-wider text-neutral-500">{g.title}</h3>
              <dl className="space-y-3">
                {terms.map((t) => (
                  <div key={t.term}>
                    <dt className="text-sm font-medium text-neutral-200">{t.term}</dt>
                    <dd className="text-sm text-neutral-400">{t.plain}</dd>
                    {t.more && <dd className="mt-0.5 text-xs text-neutral-500">{t.more}</dd>}
                  </div>
                ))}
              </dl>
            </div>
          );
        })}
      </div>
    </section>
  );
}
