import { useState } from "react";

import { type Lang, pick, useLang } from "../../i18n";

/**
 * Glosario de MAREA (M6.5, `docs/M6_ALCANCE.md` §4.4). Cubre las palabras que
 * quedan en la portada y en los detalles después de simplificar el texto.
 *
 * **No lleva cifras.** Define palabras; los números de cada lectura viven en
 * su tarjeta y salen de la API. Una definición con un número adentro queda
 * vieja sin que nada falle.
 *
 * Cada entrada va en inglés y en español: `[en, es]`.
 */
type Text = [en: string, es: string];
type Term = { term: Text; plain: Text; more?: Text };

const GROUPS: { title: Text; terms: Term[] }[] = [
  {
    title: ["Home page readings", "Lecturas de la portada"],
    terms: [
      {
        term: ["Global liquidity", "Liquidez global"],
        plain: [
          "How much money is available in the financial system and whether it is growing or shrinking.",
          "Cuánto dinero hay disponible en el sistema financiero y si está creciendo o achicándose.",
        ],
        more: [
          "MAREA combines five equally weighted series: the Fed's net balance sheet, the M2 money supply, the dollar, the 10-year real rate and financial conditions (NFCI).",
          "MAREA combina cinco series con el mismo peso: el balance neto de la Fed, la masa monetaria M2, el dólar, la tasa real a 10 años y las condiciones financieras (NFCI).",
        ],
      },
      {
        term: ["Strong / Normal / Weak", "Fuerte / Normal / Débil"],
        plain: [
          "Whether liquidity is growing faster than, like, or more slowly than in most previous weeks.",
          "Si la liquidez avanza más rápido, igual o más lento que en la mayoría de las semanas anteriores.",
        ],
        more: [
          "It is compared with the 13-week change over the whole history before that date, split into thirds. Technically: expansionary, neutral, contractionary.",
          "Se compara con el cambio a 13 semanas de toda la historia anterior a la fecha, partida en tercios. Técnicamente: expansivo, neutro, contractivo.",
        ],
      },
      {
        term: ["Higher than X% of weeks (percentile)", "Más que el X% de las semanas (percentil)"],
        plain: [
          "Lining up all previous weeks, how many ended below today's.",
          "Ordenando todas las semanas anteriores, cuántas quedaron por debajo de la de hoy.",
        ],
        more: [
          "It is truncated: 82.9 is shown as 82, so as not to round in our favour.",
          "Se trunca: si es 82.9 se dice 82, para no redondear a favor.",
        ],
      },
      {
        term: ["Stablecoins", "Stablecoins"],
        plain: [
          "Cryptocurrencies pegged to the dollar (USDT, USDC…). Their total in circulation is money already inside crypto.",
          "Criptomonedas atadas al dólar (USDT, USDC…). Su total en circulación es dinero que ya está dentro de cripto.",
        ],
      },
      {
        term: ["BTC cycle", "Ciclo de BTC"],
        plain: [
          "How expensive BTC is compared with its own history, from 0 to 100.",
          "Qué tan caro está BTC comparado con su propia historia, de 0 a 100.",
        ],
        more: [
          "It is computed only with data that existed on each date. The version that uses later data looks better at the tops, but cannot be reproduced in the moment.",
          "Se calcula solo con datos que existían en cada fecha. La versión que usa datos posteriores se ve mejor en los techos, pero no se puede reproducir en el momento.",
        ],
      },
      {
        term: ["Recession risk", "Riesgo de recesión"],
        plain: [
          "Probability that the US enters a recession within 12 months, from the gap between the 10-year and 3-month rates.",
          "Probabilidad de que EE.UU. entre en recesión dentro de 12 meses, según la diferencia entre la tasa a 10 años y la de 3 meses.",
        ],
        more: [
          "It is the model the New York Fed publishes, reproduced with public data. It is not a MAREA forecast.",
          "Es el modelo que publica la Fed de Nueva York, reproducido con datos públicos. No es un pronóstico de MAREA.",
        ],
      },
      {
        term: ["Asset classes", "Clases de activo"],
        plain: [
          "Broad investment groups: equities, bonds, gold, copper, the dollar, real estate… Tracked through ETFs.",
          "Grandes grupos de inversión: acciones, bonos, oro, cobre, dólar, bienes raíces… Se siguen con ETFs.",
        ],
      },
    ],
  },
  {
    title: ["Crypto", "Cripto"],
    terms: [
      {
        term: ["Funding", "Funding"],
        plain: [
          "Periodic payment between those betting up and those betting down on a perpetual future. When positive, those betting up pay the rest.",
          "Pago periódico entre quienes apuestan al alza y a la baja en un futuro perpetuo. Si es positivo, los que apuestan al alza le pagan al resto.",
        ],
      },
      {
        term: ["Leverage", "Apalancamiento (leverage)"],
        plain: [
          "Trading with borrowed money. At 10x, a 1% price move is 10% of the account.",
          "Operar con dinero prestado. A 10x, un movimiento de 1% en el precio es 10% de la cuenta.",
        ],
      },
      {
        term: ["Liquidation", "Liquidación"],
        plain: [
          "Forced closing of a leveraged position when the loss eats the margin.",
          "Cierre forzado de una posición apalancada cuando la pérdida se come el margen.",
        ],
      },
      {
        term: ["Stop", "Stop"],
        plain: [
          "Price at which you decide to exit a trade to limit the loss.",
          "Precio en el que se decide salir de una operación para limitar la pérdida.",
        ],
      },
      {
        term: ["R", "R"],
        plain: [
          "The loss accepted on a trade, used as a unit. Winning 2 R means winning twice what was risked.",
          "La pérdida aceptada en una operación, usada como unidad. Ganar 2 R es ganar el doble de lo que se arriesgó.",
        ],
      },
      {
        term: ["Volatility (sigma, σ)", "Volatilidad (sigma, σ)"],
        plain: ["How much the price moves, regardless of direction.", "Cuánto se mueve el precio, sin importar hacia dónde."],
      },
      {
        term: ["Price cone", "Cono de precio"],
        plain: [
          "Range where the price should end up with a given probability (for example 80%).",
          "Rango donde debería quedar el precio con una probabilidad dada (por ejemplo 80%).",
        ],
      },
      {
        term: ["Elevated regime", "Régimen elevado"],
        plain: [
          "A moment when more movement than usual is expected: stops get hit more often and liquidations cluster there.",
          "Momento en que se espera más movimiento que lo habitual: el stop salta más seguido y las liquidaciones se concentran ahí.",
        ],
      },
      {
        term: ["Breadth", "Amplitud"],
        plain: [
          "What share of cryptocurrencies is above its long-term average.",
          "Qué fracción de las criptomonedas está por encima de su promedio de largo plazo.",
        ],
      },
      {
        term: ["Altcoin season", "Temporada de altcoins"],
        plain: [
          "How many of the main altcoins beat BTC over the last 90 days.",
          "Cuántas de las principales altcoins le ganaron a BTC en los últimos 90 días.",
        ],
      },
      {
        term: ["Puts per call", "Puts por call"],
        plain: [
          "Downside-protection contracts open for each upside bet, in the options market.",
          "Contratos de protección contra caídas abiertos por cada apuesta a la subida, en el mercado de opciones.",
        ],
      },
    ],
  },
  {
    title: ["How things are measured (Lab)", "Cómo se mide (Laboratorio)"],
    terms: [
      {
        term: ["Pre-registration", "Pre-registro"],
        plain: [
          "Writing down and saving the success criterion before running a test, so it cannot be adjusted after seeing the result.",
          "Escribir y guardar el criterio de éxito antes de correr una prueba, para no acomodarlo después de ver el resultado.",
        ],
      },
      {
        term: ["Backtest", "Backtest"],
        plain: [
          "Testing a model on the past, training it only with what existed before each date.",
          "Probar un modelo sobre el pasado, entrenándolo solo con lo que existía antes de cada fecha.",
        ],
      },
      {
        term: ["Forward", "Forward"],
        plain: [
          "Testing a model on data that did not exist yet when it issued its forecast. It is the toughest test.",
          "Probar un modelo con datos que todavía no existían cuando emitió su pronóstico. Es la prueba más exigente.",
        ],
      },
      {
        term: ["Placebo", "Placebo"],
        plain: [
          "The same test with the outcomes shuffled at random. If an edge shows up there too, the test is broken.",
          "La misma prueba con los resultados mezclados al azar. Si ahí también aparece ventaja, la prueba está mal hecha.",
        ],
      },
      {
        term: ["Baseline", "Línea de comparación (baseline)"],
        plain: [
          "A simple, well-known method. A model has to beat it to be worth anything.",
          "Un método simple y conocido. Un modelo tiene que ganarle para valer algo.",
        ],
      },
      {
        term: ["AUC", "AUC"],
        plain: [
          "How well a model separates cases that turn out well from those that turn out badly. 0.5 is chance, 1 is perfect.",
          "Qué tan bien separa un modelo los casos que salen bien de los que salen mal. 0.5 es azar, 1 es perfecto.",
        ],
      },
      {
        term: ["IC (information coefficient)", "IC (coeficiente de información)"],
        plain: [
          "Correlation between the ranking the model predicted and the ranking that happened. 0 is chance.",
          "Correlación entre el orden que predijo el modelo y el orden que ocurrió. 0 es azar.",
        ],
        more: [
          "Not to be confused with the 95% CI (confidence interval). In Spanish both are abbreviated “IC”.",
          "No confundir con IC95%, que es el intervalo de confianza.",
        ],
      },
      {
        term: ["95% CI (confidence interval)", "IC95% (intervalo de confianza)"],
        plain: [
          "Range where the true value probably lies. If it includes zero, no effect can be claimed.",
          "Rango donde probablemente está el valor real. Si incluye el cero, no se puede afirmar que haya efecto.",
        ],
      },
      {
        term: ["Power", "Potencia"],
        plain: [
          "How small an effect can be and still be detected with the available data.",
          "Qué tan chico puede ser un efecto y aun así detectarlo con los datos disponibles.",
        ],
      },
      {
        term: ["Not enough data / underpowered", "Sin datos suficientes / sin potencia"],
        plain: [
          "We cannot tell yet whether there is an effect. That is not the same as showing there is none.",
          "Todavía no se puede saber si hay efecto. No es lo mismo que demostrar que no lo hay.",
        ],
      },
      {
        term: ["Backfill", "Backfill"],
        plain: [
          "A forecast computed late, when the outcome was already becoming known. It is kept, but does not count for the test.",
          "Un pronóstico calculado tarde, cuando el resultado ya empezaba a conocerse. Se guarda, pero no cuenta para la prueba.",
        ],
      },
    ],
  },
];

function matches(lang: Lang, t: Term, f: string): boolean {
  if (!f) return true;
  return `${pick(lang, ...t.term)} ${pick(lang, ...t.plain)} ${t.more ? pick(lang, ...t.more) : ""}`
    .toLowerCase()
    .includes(f);
}

export function Glossary() {
  const { lang, tr } = useLang();
  const [filter, setFilter] = useState("");
  const f = filter.trim().toLowerCase();
  return (
    <section id="glosario" className="rounded-lg border border-neutral-800 bg-neutral-900/70 p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="text-base font-semibold text-neutral-100">{tr("Glossary", "Glosario")}</h2>
        <input
          type="search"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder={tr("search for a word", "buscar una palabra")}
          className="w-full rounded-md border border-neutral-700 bg-neutral-950 px-2 py-1 text-sm text-neutral-200 sm:w-56"
        />
      </div>
      <div className="mt-4 grid gap-6 lg:grid-cols-3">
        {GROUPS.map((g) => {
          const terms = g.terms.filter((t) => matches(lang, t, f));
          if (!terms.length) return null;
          return (
            <div key={g.title[1]}>
              <h3 className="mb-2 text-xs uppercase tracking-wider text-neutral-500">{pick(lang, ...g.title)}</h3>
              <dl className="space-y-3">
                {terms.map((t) => (
                  <div key={t.term[1]}>
                    <dt className="text-sm font-medium text-neutral-200">{pick(lang, ...t.term)}</dt>
                    <dd className="text-sm text-neutral-400">{pick(lang, ...t.plain)}</dd>
                    {t.more && <dd className="mt-0.5 text-xs text-neutral-500">{pick(lang, ...t.more)}</dd>}
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
