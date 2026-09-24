# Pre-registro — Validación forward del panel transversal (Fase 10-bis)

> **Este documento se escribió y commiteó ANTES de emitir el primer
> pronóstico.** Es el requisito que el `CLAUDE.md` impone a cualquier
> medición con criterio: fijar el criterio y el número de pruebas antes de
> correrlas. Este archivo no se edita después de arrancar, salvo para
> enlazar el artefacto de cierre.

Fecha de registro: **2026-09-24**, sobre `main` en `a90d300`.

Precedentes y forma: `docs/PREREGISTRO_PANEL_DIARIO.md` (Fase 10) y
`docs/PREREGISTRO_FILTRO_RIESGO.md` (Fase 5-bis).

---

## 1. Qué se pregunta

La Fase 10 cerró con el panel transversal diario pasando sus tres criterios
pre-registrados sobre 284.763 filas y 2.571 fechas, con el placebo limpio y
11 de 13 variantes exploratorias sosteniendo el resultado. **Todo eso es
offline.**

La Fase 5 dejó escrita la lección que motiva esta fase: *offline y forward no
son lo mismo*. Allí el target de volatilidad sobrevivió, pero solo se supo
después de medirlo, y la lectura inicial del cono estuvo a punto de publicar
un falso hallazgo por no ponerle su incertidumbre.

> **H1.** El ordenamiento transversal que el panel produce —medido como IC de
> rango entre la probabilidad predicha y el retorno relativo realizado—
> **sigue siendo positivo cuando los pronósticos se emiten antes de que el
> resultado exista**, sobre datos que el modelo nunca vio.

La nula es que el IC forward sea cero: que el resultado offline viniera de una
fuga sutil, de una particularidad del período, o de alguna diferencia entre la
plomería del backtest y la del vivo.

---

## 2. La medición de potencia, hecha ANTES de fijar el criterio

Sobre las 1.667 fechas evaluadas del artefacto primario
(`panel-1d-H5-xs-20260922204554.json`), por fecha:

| serie | media | desvío | t por fecha |
|---|---|---|---|
| IC de rango | +0,10722 | 0,14947 | **0,7173** |
| spread neto del par decil | +1,3903% | 5,5122% | **0,2522** |

Los pronósticos se solapan —con `H = 5`, dos fechas consecutivas comparten 4
de sus 5 días de horizonte— así que el número de observaciones independientes
es `fechas / H`, y los ICs van por bootstrap de bloques móviles de largo `H`,
la misma maquinaria de `coverage_interval` que usan la Fase 5 y la 5-bis.

### 2.1 Qué se puede descartar según cuántos días pasen

| días | bloques | IC mínimo detectable | % del offline |
|---|---|---|---|
| 45 | 9 | 0,0977 | 91% |
| 90 | 18 | 0,0691 | 64% |
| **180** | **36** | **0,0488** | **46%** |
| 365 | 73 | 0,0343 | 32% |

### 2.2 El spread neto NO es confirmable forward, y eso se declara de entrada

Con `t = 0,2522` por fecha, para que su IC95% excluya 0 hacen falta **302
días si el efecto forward es idéntico al offline** — y **1.208 días (3,3
años) si cae a la mitad**, que es el desenlace más probable. Ninguna de las
dos es una espera razonable.

**Consecuencia registrada: el criterio económico de la Fase 10 no se traslada
a esta fase.** El spread neto se reporta siempre, con su IC por bloques, pero
**no puede aprobar ni reprobar nada**. Decir "el spread forward salió
positivo" sobre 180 días sería leer ruido: su IC95% a 36 bloques mide
±1,80pp sobre una media de 1,39pp.

Esto es la contracara de lo que la Fase 5 aprendió con el cono, y conviene que
quede en los mismos términos: **una métrica sin potencia no se convierte en
veredicto por reportarla con más decimales.**

### 2.3 Por qué NO se usa una comparación pareada, aunque el proyecto las prefiera

La intuición era que restarle al IC del modelo el del ranking trivial —igual
que la Fase 5-bis hace con EWMA— bajaría la varianza y compraría potencia.
**Medido, es al revés:**

| serie | desvío | t por fecha | días necesarios |
|---|---|---|---|
| IC del modelo (crudo) | 0,1495 | **0,7173** | **37** |
| IC modelo − momentum 30d | 0,2482 | 0,5668 | 60 |
| IC modelo − reversal 5d | 0,1963 | 0,3776 | 135 |

La causa está en la correlación: el IC del modelo contra el de los baselines
da **+0,217** y **−0,171** por fecha. Como `var(A−B) = var(A) + var(B) −
2·cov`, con covarianza casi nula y un baseline tan disperso como el modelo,
restar **agrega** varianza. El pareado ayuda cuando las dos series comparten
el ruido; acá no lo comparten.

Queda escrito porque la intuición era razonable y estaba equivocada, y porque
el proyecto usa pareado en todos lados: **no es una regla universal, es una
propiedad de las series concretas**, y hay que medirla antes de asumirla.

---

## 3. Configuración primaria — fijada antes de emitir

| parámetro | valor | por qué |
|---|---|---|
| corte primario | **180 días** con pronóstico válido | detecta el 46% del efecto offline; es el único corte que separa "se sostuvo a la mitad" de "se evaporó" |
| métrica confirmatoria | **IC de rango medio** | la única con potencia en un plazo razonable |
| horizonte `H` | **5 días** | el de la configuración primaria de la Fase 10 |
| universo, features, modelo | **idénticos a la primaria** | `price+xs`, 111 columnas, GBM, regla de pertenencia del §3.1 de la Fase 10 |
| corte de cartera | **deciles** | el de la primaria. Cambiarlo acá sería validar otra cosa |
| ICs | **bloques móviles de largo H sobre fechas** | mismo objeto estadístico que la Fase 5 y la 5-bis |

**Nada de esto se toca durante la corrida.** Cambiar el feature set, el
estimador o el universo a mitad de la acumulación mezcla dos modelos en la
misma muestra y la invalida — la lección de la Fase 4-bis, que por eso agregó
XGBoost *antes* de arrancar la validación de la Fase 5 y no después.

### 3.1 Miradas intermedias

Se declaran **descriptivas, sin veredicto**. El reporte puede correrse
cualquier día y va a imprimir el IC acumulado con su intervalo, pero **el
único corte que aprueba o reprueba es el de 180 días**. Mirar y parar cuando
el número gusta es el sesgo que este documento existe para impedir, y es un
riesgo real acá: a 45 días el IC acumulado va a ser ruidoso y va a tentar.

Si a 180 días el resultado sale **inconcluyente** —el IC95% contiene 0 pero el
punto estimado es positivo— la extensión se decide **explícitamente y por
escrito**, como hizo Nichelson el 04-09 con el cono (288 → 1.150), y el corte
de 180 queda congelado en su artefacto igual.

---

## 4. Criterio de aprobación — pre-registrado

A los 180 días con pronóstico válido, el ordenamiento se declara
**confirmado forward** si y solo si:

> **El IC de rango medio es positivo y su IC95% por bloques de fecha
> excluye 0.**

Uno solo, porque es el único con potencia. Se reporta siempre, apruebe o no:

1. **El IC forward contra el offline** (+0,1072), con el IC del forward. Si el
   offline cae **fuera** del intervalo forward, el efecto se degradó de forma
   medible y eso es un hallazgo aunque el criterio pase.
2. **El spread neto del par decil** con su IC — declarado sin potencia (§2.2).
3. **El IC de los dos rankings triviales** sobre las mismas fechas, como
   contexto. No es criterio: §2.3 muestra que la diferencia no tiene potencia.
4. **Cuántos días se perdieron y por qué**, separando "no se corrió el
   comando" de "faltó el dato", que es la distinción que la Fase 5 tuvo que
   reconstruir a mano de los logs.
5. **La fracción de pronósticos marcados `backfill`** (§5), que no entran a la
   muestra confirmatoria.

### Qué refutaría H1

Que el IC95% contenga 0 a los 180 días. Eso diría que el resultado offline
—que pasó tres criterios, un placebo y once variantes— no sobrevive fuera de
muestra, y **cerraría la línea del panel**. Sería un resultado publicable y
del mismo valor que el veredicto direccional del 25-08.

---

## 5. La trampa específica de esta fase: todo es recuperable

El panel usa **solo klines**, y Binance las sirve para siempre — por REST
mientras el símbolo cotice y por el archivo mensual cuando no. A diferencia de
la Fase 5, acá **no hay ventana de 30 días que se pierda** y no hace falta un
proceso vivo: alcanza un comando idempotente una vez por día.

Eso es una ventaja operativa enorme y **una trampa metodológica exacta**: si
cualquier día se puede reconstruir después, una corrida que se ponga al día
retroactivamente **es indistinguible de un backtest**. El valor entero de una
validación forward está en que el pronóstico se compromete *antes* de que el
resultado exista.

**Regla, y es la que sostiene toda la fase:**

> Un pronóstico para la fecha `t` entra a la muestra confirmatoria **solo si
> se computó antes del cierre de la barra `t+1`**, que es la barra de entrada.
> Cualquier otro se persiste igual, marcado `backfill=True`, y queda **fuera**
> del IC confirmatorio.

El registro guarda `created_at` en UTC junto al `open_time` de la barra, y el
reporte lo verifica en cada fila en vez de confiar en que el comando se corrió
a tiempo. Un pronóstico emitido tarde no es inválido como cálculo —es el mismo
número— pero **no es evidencia forward**, y mezclarlos sería exactamente la
contaminación que el tracker de la Fase 11 marcaba como ⚠ MUESTRA MIXTA.

---

## 6. Lo que esta fase NO pretende

- **No habilita el KPI 1.** Sigue sin pasar el gate y sigue en gris (regla 2).
- **No vuelve operable el panel**, ni siquiera si aprueba. El panel se opera
  **neutral al mercado** —largo y corto a la vez, doble fricción, 44,4% de
  rotación por rebalanceo— y su cola izquierda es catastrófica: el peor
  período de la muestra offline perdió **−68,8%** en cinco días. Confirmar que
  ordena no cambia ninguna de las dos cosas.
- **No mide la economía**, por falta de potencia (§2.2). Que el IC sobreviva
  no implica que el spread pague después de costos reales por símbolo.
- **No toca el vivo de 15m.** No cambia `model_version` del analista ni entra
  a `live/analyst.py`. Es una corrida paralela sobre otro objeto.
