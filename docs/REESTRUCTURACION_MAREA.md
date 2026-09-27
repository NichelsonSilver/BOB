# Reestructuración: de BOB a MAREA

> Decisión de Nichelson, 2026-09-26. Este documento es el registro de la
> decisión y de sus límites; el `CLAUDE.md` la aplica.

## 1. Qué se decidió

BOB deja de ser el producto y pasa a ser **un módulo**. El producto nuevo se
llama **MAREA**: un analista de **liquidez global y de flujos entre clases de
activo**, con horizonte de **semanas a meses**.

> *La marea de liquidez levanta todos los botes.* Esa frase es la tesis del
> producto: la variable que más explica el movimiento conjunto de los activos
> de riesgo es la liquidez agregada, y lo que importa es seguir hacia dónde se
> mueve y qué clases de activo la reciben primero.

MAREA **nunca ejecuta órdenes** (la regla 1 no cambia). Sugiere
**inclinaciones entre clases de activo**, no operaciones.

## 2. Las respuestas que fijaron el diseño

| pregunta | decisión | consecuencia |
|---|---|---|
| ¿Dónde viven los agentes? | **Solo Claude Code** (`.claude/agents`, `.claude/skills`) | Cero costo de API en runtime. El análisis ocurre cuando Nichelson abre una sesión; el backend solo ingesta, calcula y sirve datos. Ningún LLM entra al camino de cálculo. |
| Horizonte | **Semanas a meses** | Datos semanales/mensuales; el rebalanceo de referencia es semanal. |
| Salida del motor | **Ranking relativo** entre clases de activo + **régimen de liquidez como monitoreo** | Extiende el método del panel diario, que es lo único que pasó un gate transversal. El régimen se mide, no se pronostica. |
| Vivo intradía 15m | **Congelado como módulo** | Código, tests y artefactos intactos; no corre en vivo. |
| Presupuesto de datos | **Solo gratis** | FRED, Treasury, CFTC, BCCh, Stooq, Binance. Lo que no exista gratis se aproxima con un proxy **declarado**. |
| Geografía | **Global + lente Chile** | Cobre, USD/CLP, IPSA (vía ECH) y tasas del BCCh. |
| Noticias | **Pegadas por Nichelson** (texto o URL) en una sesión | Skill `/noticia`; se persisten en un registro. |
| Nombre | **MAREA** | Ver §6 sobre qué se renombra y qué no. |

## 3. Arquitectura en cuatro capas

| capa | contenido | ¿pronostica? |
|---|---|---|
| **1. Liquidez** | Liquidez neta de la Fed (balance − TGA − RRP), M2 de las principales economías, dólar amplio, tasas reales, spreads de crédito, condiciones financieras, posicionamiento (CFTC COT) | **No.** Se mide y se clasifica el régimen. Monitoreo. |
| **2. Activos macro** | Ranking relativo sobre ~35 activos representativos (§4) | **Sí**, con pre-registro y gate propio (§5) |
| **3. Cripto (módulo BOB)** | Panel diario transversal (Fase 10), su validación forward (10-bis), y el 15m congelado | Ya lo tiene |
| **4. Narrativa** | Noticias ingestadas por Nichelson, contrastadas contra los datos | **No.** Peso bajo y declarado. |

### 3.1 Por qué la narrativa queda fuera del modelo

No es desconfianza estética. Una noticia no tiene un timestamp de publicación
auditable ni un vector causal reproducible, así que no puede entrar a un
walk-forward sin violar la regla 5, y un número que no pasó por el gate no
puede mostrarse como operable (regla 2). Además es la capa más manipulable del
sistema, y lo es **también cuando la trae el humano**: que Nichelson la haya
elegido no la hace más verdadera.

Su función es otra, y es útil: **marcar divergencias** entre lo que se dice y lo
que muestran los datos. "Se anuncia expansión de liquidez china" + "el dato de
M2 chino no se movió" es información; la noticia sola no lo es.

## 4. Universo inicial (proxies, todos con precio diario gratuito)

| bloque | instrumentos |
|---|---|
| Acciones | SPY, QQQ, IWM (EE.UU.), EFA (desarrollados ex-EE.UU.), EEM (emergentes), FXI (China), EWJ (Japón), **ECH (Chile)** |
| Renta fija | SHY, IEF, TLT (curva del Tesoro), TIP (real), LQD (grado de inversión), HYG (high yield) |
| Reservas de valor | GLD, SLV, BTC |
| Commodities | USO (petróleo), UNG (gas), DBA (agrícolas), **cobre** (futuro HG o CPER) |
| Real estate | VNQ (EE.UU.), REET (global) — **REITs como proxy declarado**: no son el mercado inmobiliario físico, que no tiene precio diario |
| Divisas | Índice dólar amplio, EUR, JPY, CNY, **CLP** |
| Cripto | BTC, ETH (el detalle vive en el módulo BOB) |

Series de liquidez y tasas: FRED (`WALCL`, `WTREGEN`, `RRPONTSYD`, `M2SL`,
`DGS10`, `DFII10`, `T10Y2Y`, `BAMLH0A0HYM2`, `NFCI`, `DTWEXBGS`), Treasury,
CFTC COT y BCCh.

**Pendiente de verificar antes de escribir una línea de ingesta (M1):** los
términos de uso de Stooq, y si FRED y BCCh exigen API key (FRED sí, gratuita).
No se usa `yfinance`: no es una API oficial y su ruptura es silenciosa.

## 5. La advertencia de potencia, antes de prometer el ranking

El panel cripto funcionó con **659 símbolos**. Acá son **~35**, y muy
correlacionados entre sí dentro de cada bloque. El número efectivo de apuestas
independientes puede ser de **un dígito**, y en ese caso el ranking macro no
tiene potencia para pasar ningún gate.

Por eso la **M3 empieza midiendo potencia**, exactamente como la Fase 10-bis:
número efectivo de independientes por PCA sobre retornos demediados por fecha,
y el IC mínimo detectable según la cantidad de fechas disponibles. **Si no
alcanza, el ranking no se construye**, el producto macro queda como monitoreo,
y el pronóstico vive donde ya está probado: en el módulo cripto. Ese desenlace
se considera legítimo de antemano, no un fracaso.

## 6. Qué se renombra y qué no

| cosa | decisión | por qué |
|---|---|---|
| Nombre del producto, docs, README, título del dashboard | **MAREA** | Es lo que cambió |
| Paquete Python `bob`, directorio `proyectos/bob`, DB | **No se tocan** hasta el corte de la Fase 10-bis | La tarea programada `BOB_panel_forward_diario` corre `bob.paper.panel_forward` desde una ruta absoluta. Renombrar rompe la corrida de 180 días, y cambiar su código a mitad de muestra la invalida. |
| Código macro nuevo | **Paquete hermano `marea`** en el mismo backend | Separa lo nuevo sin mover lo validado; reutiliza `bob.models.validation` y `bob.models.metrics` por import. |

`bob` queda como **nombre del módulo cripto** dentro de MAREA, que es además lo
que conserva la historia del repo legible.

## 7. Lo que no cambia

- Las reglas de operación 1 a 11 y las restricciones del `CLAUDE.md`.
- Todo número con su baseline al lado; todo pronóstico pre-registrado antes de
  medirse; validación temporal purgada sin excepciones.
- **La corrida forward del panel (Fase 10-bis)**: sigue programada y su
  comportamiento no cambia hasta el corte a 180 días. Su árbol de dependencias
  es ancho (llega hasta `signals/numeric.py`), así que la regla 12 del
  `CLAUDE.md` exige que todo commit que toque un módulo de ese árbol reproduzca
  bit a bit el artefacto primario del panel antes de entrar.
- El 15m no se borra: es el antecedente de todo lo que se aprendió sobre
  calibración, potencia y lookahead.

## 8. Fases

| fase | contenido | salida |
|---|---|---|
| **M0** | Esta decisión, `CLAUDE.md` nuevo, agentes y skills | Estructura lista, sin código de motor |
| **M1** | Ingesta macro: FRED, Treasury, CFTC, Stooq, BCCh en `marea/data/` | Series persistidas, huecos reportados, nunca rellenados |
| **M2** | Monitor de liquidez + página macro del dashboard | Régimen de liquidez medido, sin pronóstico |
| **M3** | Medición de potencia → pre-registro → gate del ranking | Veredicto, incluido "no hay potencia" |
| **M4** | Skill `/noticia` con registro persistente + memo semanal `/informe` | La capa narrativa operando con su peso declarado |

## 9. Tensión que queda nombrada

La memoria de LUPE registra que el descongelamiento de BOB (19-08) no vino con
el trade-off contra REALI que la regla del 06-08 exigía. Esta reestructuración
**agranda** el alcance. No le corresponde a este documento resolverlo, pero sí
dejarlo escrito: conviene fijar un hito de pausa para MAREA —por ejemplo, el
cierre de M2 o el de M3— en vez de dejar que el alcance nuevo crezca sin borde.
