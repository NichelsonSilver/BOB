# MAREA — monitor de liquidez (fase M2)

> Cerrada el 2026-10-05. **Mide, no pronostica.** Código: `marea/liquidity.py`
> (puro) y `marea/monitor.py` (I/O). Página **Macro** del dashboard.

    cd backend
    uv run python -m marea.data.ingest todo     # datos, ~2 min
    uv run python -m marea.monitor              # reporte, ~12 s; lo guarda para la API
    uv run python -m marea.monitor --as-of 2022-06-17   # lo que se sabía ese día

## Decisiones fijadas antes de calcular (Nichelson, 05-10)

| pregunta | decisión |
|---|---|
| Cómo se clasifica | Percentil de la señal en su propia historia, point-in-time, pasado estricto; tercil → etiqueta |
| Agregado principal | Liquidez neta de la Fed como núcleo; bancos centrales globales en USD como contexto |
| Retornos por régimen | **No.** Es M3, con pre-registro: una tabla así se leería como pronóstico |

## Método

- **Señal**: cambio a 13 semanas (log para niveles, diferencia para tasas e
  índices), orientado de modo que alto = expansivo. El de 4 semanas se muestra
  y no se clasifica.
- **Point-in-time en las dos puntas**: el cambio que se ve el día `d` compara
  la última observación conocida en `d` con la de 13 semanas antes **en la
  misma vintage**. Reutiliza `pit.as_of`: una sola definición de "qué se sabía".
- **Dato vencido = NaN**: si la última observación conocida es más vieja que
  rezago máximo medido + un período + 7 días, no describe el presente.
- **Percentil** contra el pasado estricto, ventana expansiva, mínimo 156
  semanas. Empates cuentan la mitad.
- **Compuesto**: promedio con pesos iguales declarados de cinco componentes
  —Fed neta, M2, dólar amplio, tasa real, NFCI—; exige los cinco. La etiqueta
  sale del percentil del compuesto en su propia historia, porque el promedio de
  percentiles se concentra en 0,5.
- Nada se ajustó a retornos de activos.

Fuera del compuesto, cada uno con su motivo: bancos centrales globales
(contiene a la Fed y se mueve con el tipo de cambio), spread HY (FRED sirve 3
años por licencia ICE: sin historia para percentil; el crédito entra vía NFCI),
pendiente 10a−2a (signo ambiguo, no se clasifica), VIX (riesgo), Chile (rezago
declarado) y COT (concentración contra 3 años, no liquidez).

## Verificación

- **Invariante point-in-time sobre la DB real**: el reporte reconstruido al
  2020-04-17 y al 2022-06-17 reproduce la historia del reporte del 2026-10-05
  **fila por fila** (0 diferencias). Hay un test que fija lo mismo sobre datos
  sintéticos, y otro que muta el futuro y exige el pasado intacto.
- Lecturas contra episodios conocidos, como control de cordura y no de
  calibración: abril–junio 2020 expansivo (0,82 → 1,00), 2022 contractivo
  (43 de 52 semanas), marzo 2023 neutro.
- Cobertura: 100% `liquidity.py`, 98% `monitor.py`.

## Límites medidos

- **Historia corta del compuesto**: desde 2020-02. La Fed neta exige RRP
  diario (desde 2014) y cada percentil pide 3 años: 2014 → 2017 → 2020. Un
  régimen "alto" lo es contra 2017–2026, dominado por el QE de 2020 y el QT
  de 2022.
- **La etiqueta es inestable**: 63 cambios en 347 semanas, racha mediana de
  **3 semanas** (máxima 47). Para un horizonte de semanas a meses es ruidoso.
  No se suavizó: agregar histéresis o una media **después** de ver esto sería
  ajustar el método a la muestra. Si se quiere, se decide como regla nueva,
  declarada, y se reporta su persistencia contra esta.
- BoJ es mensual con hasta 65 días de rezago: el agregado global llega con
  fecha del dato de hace dos meses.
