# Enmienda 1 al pre-registro de la validación forward del panel (Fase 10-bis)

> Enmienda a `docs/PREREGISTRO_PANEL_FORWARD.md` (`99532e8`), que no se edita.
> **Se escribe y commitea ANTES de que nadie haya mirado el IC forward.**
> Nichelson lo declaró el 2026-10-05 ("no miré el IC"), y el asistente
> tampoco lo miró: `marea.brief` reporta solo el estado operativo de la
> corrida, a propósito. Si esa condición fuera falsa, esta enmienda no vale y
> rige el diseño original.

Fecha: **2026-10-05**. Estado de la corrida al escribirla: 4 fechas válidas de
12, sin ningún reporte de IC impreso desde el arranque.

---

## 1. Por qué se enmienda

El diseño original tiene **un solo corte**, a 180 fechas válidas, con las
miradas intermedias "descriptivas, sin veredicto" (§3.1). Dos hechos medidos
después de arrancar lo vuelven caro:

1. **El ritmo de fechas válidas es de 33%** (4 de 12; 6 salieron backfill y 2
   nunca se emitieron). La causa es operativa: el portátil está en Modern
   Standby a la hora de la tarea y los temporizadores de reactivación están
   desactivados. A este ritmo, el corte llega en **2028-03**.
2. Con un solo corte, **los tres escenarios esperan lo mismo**: si el efecto
   se sostuvo entero, si cayó a la mitad o si se evaporó. Solo el segundo
   necesita de verdad los 180 días.

Un diseño secuencial con barreras fijadas de antemano permite parar antes en
los otros dos **sin inflar el error tipo I**. Lo que este documento impide es
lo contrario: mirar el número intermedio y decidir según cómo salió. Las
reglas de abajo son mecánicas.

## 2. Qué cambia

Se pasa de un corte único a **dos análisis**, a las **90** y a las **180
fechas válidas** (no backfill). La estadística es la del §4 original:

    z = IC medio por fecha / SE

`SE` es el error estándar de la media por **bootstrap de bloques móviles de
largo H = 5** sobre las fechas válidas, con 20.000 remuestreos y semilla 0.
Es el mismo objeto estadístico de la Fase 5, la 5-bis y el pre-registro
original.

| análisis | fechas válidas | regla |
|---|---|---|
| **interino** | **90** | **confirma** si `z ≥ 2,967` · **para por futilidad** si `IC medio ≤ 0` · en otro caso, sigue |
| **final** | **180** | **confirma** si `z ≥ 1,968` · en otro caso, **no confirma** |

- **Barreras**: gasto de α tipo O'Brien-Fleming (Lan-DeMets), una cola,
  α = 0,025. Es el mismo nivel que el criterio original: "IC positivo e IC95%
  excluye 0". La fracción de información a las 90 fechas (0,499) sale de los
  errores estándar que el propio pre-registro midió por bootstrap (§2.1: MDE
  0,0691 a 90 días y 0,0488 a 180, divididos por 2,80). En IC, las barreras
  equivalen a **0,0732 a las 90** y **0,0343 a las 180**.
- **La futilidad es vinculante**: si a las 90 el IC medio es ≤ 0, la línea
  del panel se cierra ahí, como "no se sostuvo forward". No se extiende para
  ver si se recupera.
- **Lo que no cambia**: la métrica, el horizonte, el universo, el modelo, la
  regla de gracia de 2 h y la definición de backfill (§5), la extensión
  explícita y por escrito si el final sale inconcluyente (§3.1), y todo lo
  que se reporta sin decidir (§4, puntos 1 a 5).

## 3. Lo que cuesta y lo que compra

Simulación de 400.000 trayectorias, con la correlación entre los dos análisis
fijada por la fracción de información (aritmética; no usa datos del forward):

| efecto real | P(confirmar) original | P(confirmar) enmienda | se decide a las 90 | fechas válidas esperadas |
|---|---|---|---|---|
| nulo (IC 0) | 0,025 | **0,025** | 50% (por futilidad) | 180 → **135** |
| mitad del offline (0,054) | 0,868 | **0,864** | 23% | 180 → **160** |
| el offline entero (0,107) | 1,000 | **1,000** | 92% | 180 → **98** |

- **Costo**: −0,4 pp de potencia en el escenario de efecto a la mitad.
- **Compra**: si el efecto se sostuvo, la confirmación llega casi siempre a
  la mitad del camino; si se evaporó, la mitad de las veces el cierre llega
  también ahí. Solo el caso intermedio sigue hasta las 180.

**Alternativa considerada y descartada**: agregar una mirada a las 45
fechas, solo con futilidad (su barrera de eficacia, IC ≥ 0,151, es
inalcanzable en la práctica). Bajaría las fechas esperadas bajo el nulo de
135 a **101**, pero la potencia con el efecto a la mitad caería de 0,864 a
**0,831**. Se descarta: con 45 fechas, un IC transitoriamente negativo es
demasiado probable aunque el efecto exista.

## 4. Cómo se ejecuta sin tocar la corrida (regla 12)

El análisis interino **no se implementa en `bob/paper/panel_forward.py`**,
que está en el árbol congelado de la regla 12. Se escribe en `marea/` como
lector de solo lectura de `panelforecastrecord`, y reutiliza las funciones de
resolución y de IC de `bob.paper.panel_forward` por import, sin modificarlas.

Antes de su primer uso, **ese lector tiene que reproducir el reporte
descriptivo de `panel_forward --report`** sobre las mismas fechas. Esa
comparación se corre a las 90 fechas válidas y no antes. Hasta ese momento
nadie corre `--report` ni el lector.

## 5. Lo que esta enmienda NO hace

- **No arregla el ritmo.** Con 33% de fechas válidas, las 90 llegarían hacia
  **2027-06-20**; con todas válidas, hacia el **2026-12-30**. La palanca de
  calendario sigue siendo la tarea programada.
- **No mira el IC antes de las 90 fechas.** Las miradas descriptivas del §3.1
  original quedan suspendidas: con una regla de parada vigente, mirar entre
  análisis ya no es inofensivo.
- **No toca el modelo, las emisiones ni `model_version`.**
- **No cambia lo que el panel es**: aunque confirme, sigue sin ser operable
  para Nichelson (neutral al mercado, 44,4% de rotación, peor período
  −68,8%).
