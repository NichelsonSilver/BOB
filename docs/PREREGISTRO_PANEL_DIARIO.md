# Pre-registro — Panel transversal diario (Fase 10)

> **Este documento se escribió y commiteó ANTES de correr la medición.**
> Es el requisito que el `CLAUDE.md` impone a cualquier barrido: *"si algún
> día se retoma, hay que fijar el criterio y el número de pruebas ANTES de
> correrlas"*. Lo que sigue fija los dos. El resultado va en un artefacto
> aparte; este archivo no se edita después de correr, salvo para enlazarlo.

Fecha de registro: **2026-09-22**, sobre `main` en `e7311ed`.

Precedente y forma: `docs/PREREGISTRO_FILTRO_RIESGO.md` (Fase 5-bis).

---

## 1. Qué se pregunta, y por qué esta pregunta y no otra

El proyecto llegó hasta acá con un veredicto duro y bien medido: **en ETHUSDT
a 15m no hay edge direccional**, y no por falta de datos — la Fase 2b agregó
730 días de derivados y microestructura y el gate empeoró de forma monótona.
La Fase 5 cerró el forward confirmando el álgebra: sin edge direccional el EV
bruto de una barrera simétrica es exactamente 0 y el neto es −costo.

Al explorar velas **diarias** aparecieron cuatro hechos medidos que, juntos,
describen un problema distinto — no una segunda oportunidad para el mismo.

### 1.1 En diario el costo deja de ser el término dominante

Las barreras del proyecto son múltiplos de sigma, así que crecen con √H
mientras el costo round-trip sigue fijo en 0,140%:

| tf | H | TP=SL | tasa base (long) | P_equilibrio | margen a superar |
|---|---|---|---|---|---|
| 15m | 16 barras | 0,50% | 43,9% | 61,6% | **17,7 pp** |
| 1d | 5 días | 3,77% | 45,8% | 51,6% | 5,9 pp |
| 1d | 10 días | 5,34% | 49,7% | 51,2% | **1,4 pp** |

### 1.2 Pero el diario en un solo símbolo no tiene potencia

Los setups solapan H barras. Con 2.491 velas diarias de ETHUSDT perpetuo
(2019-11-27 en adelante, que es todo lo que existe):

| escenario | n bruto | n efectivo | IC95% del AUC |
|---|---|---|---|
| 15m, H=16 | 69.119 | 4.320 | ±0,017 |
| 1d, H=5 | 2.491 | 498 | ±0,051 |
| 1d, H=16 | 2.491 | 156 | ±0,091 |

**El gate sobre ETHUSDT diario sería inconcluyente por construcción**: no
distingue el umbral 0,55 del piso económico. Y el fold 1 del walk-forward
entrenaría con ~415 filas.

### 1.3 Lo que parecía margen positivo en diario es deriva, no edge

A H=16 el margen long sale **+0,2 pp**. Contrastado contra su espejo:

| H | long | short | suma |
|---|---|---|---|
| 10 | −1,4 pp | −8,8 pp | −10,2 pp |
| 16 | **+0,2 pp** | −8,1 pp | −7,9 pp |
| 30 | +0,4 pp | −7,6 pp | −7,3 pp |

Si hubiera edge, long y short no serían espejo. Lo son. Es que ETH subió
entre 2019 y 2026, y ni eso resiste: retorno log medio diario +0,117%,
**t = 1,35, no significativa**; el IC95% de la tasa base long a H=16 es
[43,2%, 59,1%] y contiene el equilibrio. **No se registra ninguna hipótesis
sobre esto.**

### 1.4 La sección transversal solo paga si la pregunta es relativa

Medido sobre 238 perpetuos cripto × 2.456 fechas (342.769 symbol-days):

| | correlación media entre pares | símbolos independientes | n_eff (H=5) | IC95% AUC |
|---|---|---|---|---|
| retornos crudos — *"¿sube este símbolo?"* | **+0,422** | **2,4 de 238** | 1.158 | ±0,033 |
| demediados por fecha — *"¿sube más que el resto?"* | −0,007 | 238 de 238 | 66.803 | **±0,004** |

PC1 explica el **48,4%** de la varianza del panel crudo y solo **6,8%** del
demediado. Preguntarle a 238 símbolos si suben es casi preguntárselo a dos.
Preguntarles cuál sube *más* son 238 preguntas distintas por día.

**Esto fija la pregunta de este pre-registro, y descarta la otra.** No se
registra ninguna hipótesis sobre dirección absoluta en el panel: la medición
de arriba dice que no habría potencia para resolverla.

---

## 2. Hipótesis

> **H1.** Un modelo entrenado sobre el panel diario, usando únicamente
> información disponible causalmente en la fecha `t`, ordena los símbolos del
> universo por su **retorno relativo** a `H` días — demediado dentro de la
> sección transversal de esa fecha — mejor de lo que lo hace el mejor
> **baseline transversal trivial** (momentum y reversal por ranking de
> retorno pasado), fuera de muestra.

La nula **no** es "el modelo no ordena". Un modelo que capture solo el factor
de mercado ordena algo por construcción, y el momentum transversal es un
efecto documentado que se obtiene con una línea de código. **La nula es que
el modelo no hace mejor que el ranking trivial.**

---

## 3. Diseño de la medición

### 3.1 El universo, y el sesgo que lo acecha

El archivo `data.binance.vision` guarda **864 símbolos USDT** de futuros UM.
El `exchangeInfo` vivo lista como `TRADING` + `PERPETUAL` + `USDT` solo 525.
La diferencia no es ruido:

- **179 son `TRADIFI_PERPETUAL`** — perpetuos de acciones e índices
  (`AAPLUSDT`, `NVDAUSDT`, …). **Se excluyen**: otra clase de activo, otro
  calendario, otro régimen. Binance los marca explícitamente, así que la
  exclusión es por bandera del exchange y no por heurística de nombre.
- **129 están en `SETTLING`** — en proceso de deslistado.
- **31 desaparecieron por completo del `exchangeInfo`**, entre ellos
  **`LUNAUSDT`**, `BTCSTUSDT`, `BZRXUSDT`, `MATICUSDT`, `SRMUSDT`.

`LUNAUSDT` es el argumento entero en una palabra: el colapso de Terra de mayo
de 2022, el mayor reventón de la muestra. **Un panel armado con los símbolos
vivos de hoy lo omite en silencio**, junto con todo lo demás que se murió — y
lo que se muere es justamente lo que cae. El universo se reconstruye desde el
archivo, no desde el `exchangeInfo`.

**Regla de pertenencia, evaluada en cada fecha `t` con datos ≤ `t−1`:**

1. `contractType == PERPETUAL` (cripto; excluye `TRADIFI_PERPETUAL`)
2. quote `USDT`
3. al menos **180 días** de velas diarias previas a `t`
4. volumen en USD mediano de los **30 días previos a `t`** por encima de
   **5 millones** — corte medido: deja un ancho mediano de **117 símbolos**
5. **la pertenencia nunca se decide con datos de `t` ni posteriores**

Un símbolo que entra al universo y después se deslista **permanece** hasta su
última vela. Su setup se resuelve al último precio disponible, marcado
`delisted`. Descartarlo sería reintroducir el sesgo de supervivencia por la
puerta de atrás, que es el error que este diseño existe para evitar.

### 3.2 El target

Para el símbolo `s` en la fecha `t`, con entrada al **`open` de `t+1`** —
misma convención que el gate y que la Fase 5-bis, para que los números sean
comparables:

```
r(s,t) = log( close[s, t+H] / open[s, t+1] )
y(s,t) = 1  si  r(s,t) > mediana_transversal( r(·,t) )
         0  en otro caso
```

Binario, **tasa base exactamente 50% por construcción**, y neutral al mercado
por construcción también: el factor común de la fecha se cancela en la
comparación contra la mediana. AUC y BSS quedan directamente comparables con
los del gate de 15m.

### 3.3 Features

Tres bloques, y el tercero es el que la hipótesis pone a prueba:

1. **Serie de tiempo por símbolo** — las 55 existentes de `signals/features.py`,
   **reparametrizadas para diario** (ver §6.1: hoy degeneran).
2. **Contexto de mercado** — retorno y volatilidad del factor común (mediana
   de la sección), y el beta rodante de cada símbolo contra él.
3. **Transversales** — el rango percentil de cada feature del bloque 1
   *dentro de la sección transversal de su fecha*. Es la única información
   que el modelo de 15m no podía tener, y por lo tanto la única capaz de
   explicar un resultado distinto.

Ningún feature puede usar información de `t` o posterior. El test de
no-lookahead del proyecto (mutar el futuro no altera el pasado) se extiende
al eje transversal: **mutar los datos de un símbolo no debe alterar las
features de otro en fechas anteriores**.

### 3.4 Validación

Walk-forward purgado **partido por FECHA, no por fila**: todos los símbolos
comparten la frontera del fold. Partir por fila pondría el mismo día en train
y test para símbolos distintos, y con correlación transversal de +0,42 en
crudo eso es fuga directa.

- purga de `H` días a cada lado de la frontera
- embargo del 1%, como el gate
- 6 folds, como el gate

### 3.5 Incertidumbre

Bootstrap de **bloques móviles sobre FECHAS**, largo `H`, remuestreando el
día entero con todos sus símbolos. Nunca por símbolo: dentro de una fecha los
residuos siguen correlacionados por sector, y remuestrear símbolos sueltos
encogería el error estándar fingiendo independencia que no existe. Es la
misma maquinaria de `coverage_interval` que la Fase 5 usó para dar vuelta la
lectura del cono, aplicada al eje que acá solapa.

### 3.6 El criterio económico

El `CLAUDE.md` es explícito: *"maximizar AUC es mirar la variable
equivocada; lo que hay que maximizar es la masa de probabilidad calibrada por
encima del equilibrio"*. Para un spread transversal el análogo es el retorno
neto de un par largo/corto.

Medido sobre el panel, con el oráculo (decil alto − decil bajo, conociendo el
futuro) como techo:

| H | dispersión XS | spread del oráculo | costo del par | ratio |
|---|---|---|---|---|
| 1 | 3,58% | 12,25% | 0,28% | 43,8× |
| **5** | **7,97%** | **27,30%** | **0,28%** | **97,5×** |
| 10 | 11,14% | 38,44% | 0,28% | 137,3× |

Y de ahí sale el piso económico, que es el contraste que ordena toda la fase:

| | AUC mínimo para pagar el costo |
|---|---|
| 15m direccional | **0,63** (medido: 0,52) |
| **panel diario H=5** | **0,5047** |

---

## 4. Configuración primaria — fijada antes de correr

| parámetro | valor | por qué |
|---|---|---|
| horizonte `H` | **5 días** | 438 bloques de fecha independientes; oráculo 97,5× el costo; 73 rebalanceos/año |
| universo | **cripto perpetuo, ≥180d, >$5M/día** | ancho mediano 117 símbolos |
| target | **sobre la mediana transversal** | tasa base 50% exacta, neutral al mercado |
| corte de cartera | **deciles** | 10% por lado |
| estimador | **GBM sklearn** | el del gate; no se cambia para no mezclar |
| folds | **6, purgados por fecha** | el del gate |
| baseline primario | **el mejor de {momentum XS 30d, reversal XS 5d}** | el trivial que hay que superar |
| baseline secundario | **el mismo modelo sin features transversales** | aísla lo que aporta la sección |

### Cómo se eligió `H`, y por qué eso no es hacer trampa

Se miraron **potencia y factibilidad** antes de registrar — cantidad de
bloques independientes, dispersión transversal, ancho del universo por umbral
de liquidez — igual que la Fase 5-bis contó eventos de liquidación antes de
fijar el leverage. Lo que sería trampa es mirar el **resultado** — el IC, el
AUC, el spread realizado de algún ranking — y elegir `H` después. **Ningún
ranking se corrió antes de commitear este archivo**, ni el trivial.

### Número de pruebas declarado

- **1 confirmatoria**: H1 en la configuración primaria.
- **18 exploratorias**, declaradas acá y reportadas como tales pase lo que
  pase: `H ∈ {3, 10}` (2), cortes de cartera quintiles y ventiles (2),
  umbrales de liquidez $1M/$20M (2), sub-períodos anuales (6), con y sin los
  símbolos deslistados (1), sin el bloque de features transversales (1),
  ponderación por unicidad on/off (1), XGBoost como estimador (1),
  universo sin filtro de liquidez (1), target por tercil en vez de mediana (1).

---

## 5. Criterios de aprobación — pre-registrados

El panel se declara **viable** si y solo si, en la configuración primaria,
se cumplen **los tres**:

1. **Discrimina.** AUC > 0,55 con BSS > 0, out-of-sample. Es el gate del
   proyecto y no se mueve.
2. **Supera al ranking trivial.** El IC de rango del modelo excede al del
   mejor baseline transversal, con IC95% por bloques de fecha de la
   **diferencia pareada** excluyendo 0. Pareada, no dos intervalos
   marginales: dos ICs que se solapan no permiten concluir que no hay
   diferencia — mismo motivo por el que el proyecto usa Diebold-Mariano.
3. **Paga.** El spread neto del par decil, descontado el costo de las dos
   patas, es positivo con IC95% por bloques de fecha excluyendo 0.

Se reporta siempre, aprobado o no:

4. **El spread bruto contra el del oráculo** — qué fracción del techo se
   capturó. Es el número que dice si hay producto o hay ruido que pasó tres
   tests.
5. **La rotación**: cuántos símbolos cambian de cartera por rebalanceo. Un
   spread positivo que exige rotar todo cada 5 días no es operable a mano.

### Qué refutaría H1

Que la diferencia pareada contra el ranking trivial no excluya 0. Eso diría
que el momentum transversal — una línea de código — captura todo lo que hay, y
que el stack no aporta. **Sería un resultado publicable y cerraría la fase**,
igual que el veredicto direccional del 25-08.

---

## 6. Las trampas conocidas, y por qué acá son peores

### 6.0 La dificultad se invirtió respecto de 15m

En 15m el listón era AUC 0,63 y el modelo medía 0,52: **ningún sesgo
metodológico plausible cerraba esa brecha**, así que los errores de método
eran casi inofensivos para la conclusión. Acá el piso económico es **0,5047**
y la resolución estadística **±0,004**. Son del mismo orden.

**Eso significa que un sesgo pequeño produce exactamente el tamaño de efecto
que buscamos.** La pregunta deja de ser *"¿hay señal?"* y pasa a ser
*"¿puedo probar que esto no es un artefacto?"*. Todo lo que sigue existe por
esa razón, y por eso los guardarraíles son más caros que el modelo.

### 6.1 El feature engine degenera en diario, y no falla

`window_bars` traduce horas a barras con piso de 2. En diario, ventanas de
1h, 4h, 12h y 24h **caen todas a 2 barras**. Medido sobre las 2.491 velas
diarias reales de ETHUSDT:

- **25 pares de columnas duplicados exactos** (`mom_1h` ≡ `mom_4h` ≡
  `mom_12h` ≡ `mom_24h`; `rv_1h` ≡ `rv_4h`; …)
- **4 columnas constantes** — `vol_term_structure` y `ema_cross` por la
  degeneración; `hour_sin`/`hour_cos` correctamente, porque todas las velas
  diarias abren a las 00:00 UTC
- **55 features → ~34 distintas**

Y el diseño HAR se arma con las mismas ventanas: en diario da (2, 3, 7) en
vez del canónico de Corsi (1, 5, 22). **Nada de esto falla ruidosamente**:
entrena igual y reporta métricas. Reparametrizar es prerrequisito de correr,
no una mejora opcional.

### 6.2 El lookahead entra por la regla de universo

El lookahead clásico lo cazan los tests existentes. El de un panel es otro:
decidir *qué símbolos están en el universo hoy* usando información de mañana.
Un filtro de liquidez calculado sobre la muestra completa, o un "símbolos con
≥2 años de historia" evaluado al final, seleccionan a los sobrevivientes con
el futuro. Por eso la pertenencia se evalúa en cada fecha con datos ≤ `t−1`,
y por eso el deslistado permanece hasta su última vela.

### 6.3 El precio rancio

Un símbolo sin volumen puede repetir el `close` varios días. Su retorno sale
0 y después salta, lo que fabrica reversal transversal de la nada. El filtro
de $5M/día lo mitiga; además se reporta, por fold, la fracción de
símbolo-días con retorno exactamente 0.

### 6.4 El factor de mercado se cuela igual

Demediar por fecha quita el factor común, pero no los betas: en un día de
caída general, los símbolos de beta alto quedan abajo de la mediana por
construcción. Si el modelo aprende a predecir beta, "predice" el signo del
mercado disfrazado de transversal. Por eso el beta rodante entra como feature
explícito — para que sea atribuible — y se reporta el desempeño condicionado
al signo del retorno del factor.

---

## 7. Lo que este pre-registro NO pretende

- **No habilita el KPI 1.** Sigue sin pasar el gate y sigue en gris (regla 2).
  Esta es otra pregunta, sobre otro objeto, a otra escala.
- **No vuelve positivo el EV de un trade direccional.** Eso sigue siendo
  álgebra imposible sin edge (Fase 5, hallazgo (a)).
- **No es una estrategia operable aunque apruebe.** Un spread transversal se
  opera **neutral al mercado** — largo y corto a la vez, el doble de patas y
  de fricción —, lo que es una forma de operar distinta de todo lo que BOB
  asumió hasta hoy y de cómo opera Nichelson. Si aprueba, esa conversación es
  una decisión de producto, no una consecuencia automática.
- **No mide forward.** Es medición offline. La Fase 5 enseñó que offline y
  forward no son lo mismo; la validación forward sería una fase aparte.
- **No toca el vivo.** Nada de esto cambia `model_version` ni entra al
  analista mientras no apruebe y se decida explícitamente.
