# Estrategia v1 del Carril A — CONGELADA (provisional)

> **CONGELADA el 2026-11-03 como versión de arranque PROVISIONAL**, aprobada por el responsable con
> los valores de la §1. Es el paso A0 del [plan del carril A](PHASE_A_PAPER_LANE_PLAN.md)
> ([DEC-022](../DECISIONS.md#dec-022)). El hash del fichero se registra en el plan y en el
> changelog (no dentro de este fichero). **Este fichero no se edita más:** cualquier cambio será la
> versión v2, decidida a partir de los estudios del carril B y nunca retocando v1 con resultados
> paper. Las apuestas de v1 quedan separadas de las de v2.
>
> **Supuestos sin verificar al congelarla:** comisión del 5 % (valor por defecto del proyecto, no
> confirmado contra la cuenta `.es`); stake mínimo de la cuenta (pendiente); historial mínimo de un
> jugador para predecir (se fija tras medir la cobertura en A1, **antes de la primera apuesta**).
> Un valor corregido de comisión o de stake mínimo se registra como **corrección de supuesto** en
> el plan, no como cambio de estrategia.
> Datos de apoyo: observaciones del collector desde el 2026-10-05 13:17 UTC de mercados ya
> terminados, y la vista de cierre ([DEC-021](../DECISIONS.md#dec-021)). Son **descriptivos**; no
> usan resultados de partidos.

## 1. Resumen de la propuesta

| # | Parámetro | Propuesta v1 |
|---|---|---|
| 1 | Alcance | ATP **individuales**, prematch, `MATCH_ODDS`; ambos jugadores con identidad segura |
| 2 | Probabilidad de mercado (desvigado) | **Shin sobre los mejores precios BACK** de los dos runners |
| 3 | Momento de apostar | **Una sola decisión por mercado**: primera observación con precios entre 20 y 60 min antes del inicio publicado |
| 4 | Umbral | `EV neto ≥ +3 %` **y** `edge ≥ 3 pp`; si `edge > 15 pp`, **no se apuesta** y se registra como anomalía |
| 5 | Comisión | La real de la cuenta `.es` (**por confirmar**); referencia del proyecto: 5 % |
| 6 | Stake | **Plano**: 1 % del bankroll **inicial** (10 € sobre 1.000), limitado a lo visible al mejor BACK |
| 7 | Lado y número | Solo `BACK`; como mucho una apuesta por mercado (el runner de mayor EV) |
| 8 | Límites | Los de `config.py`: mercado ≤ 2 %, día ≤ 5 %, drawdown 20 % → parar y avisar |
| 9 | Ejecución supuesta | Casada al precio visto; se registra además una variante **pesimista** (1 tick peor) |
| 10 | Congelación | Versión `v1` + hash del documento; los cambios son `v2` |

## 2. Detalle y evidencia

### 2.1 Desvigado: Shin sobre BACK
En un Exchange la suma de las probabilidades BACK supera 1 por el **spread**, no por un margen del
operador; el punto medio entre BACK y LAY es la referencia más neutra, pero no siempre hay LAY.
Medido sobre el cierre fiable de 225 mercados de dos runners (diferencia en la probabilidad del
favorito, puntos porcentuales):

| Método frente al punto medio | P50 | P90 | Máx. |
|---|---|---|---|
| Proporcional sobre BACK | 0,55 | 1,44 | 4,12 |
| **Shin sobre BACK** | **0,12** | **0,56** | 2,37 |

(Sobre 276 cierres, proporcional frente a potencia y a Shin difieren hasta 2,1 y 1,3 pp en el P90.)
Shin reproduce el punto medio mucho mejor, con una fórmula cerrada para dos resultados y solo con
precios BACK. **Alternativas:** proporcional (más simple, peor); punto medio (mejor en teoría, pero
exige LAY y la ausencia de LAY es frecuente en libros finos). El mismo método se usa para la
entrada y para el cierre: `CLV = p_cierre − p_entrada` en una apuesta BACK (`METRICS.md`).

### 2.2 Momento: una decisión a 20–60 min
Con precios en los mercados terminados, la cobertura es **estable** a lo largo del tiempo: 94–96 %
de las observaciones tienen precios entre 5 y 120 min antes; la liquidez al mejor BACK (mediana
~160–190 € sumando ambos runners) y el spread del peor runner (~10 %) apenas cambian. No hay un
momento «mejor» por liquidez, así que el criterio es otro:
- **Dejar al menos 20 min hasta el cierre** para que el CLV sea medible (apostar en el cierre da
  CLV nulo por construcción).
- **No apostar a más de 60 min**: el inicio publicado cambia en el 80 % de los mercados y se vuelve
  inestable cuanto más lejos.
- **Una sola decisión**: se evalúa la primera observación válida de la ventana; si no hay, no hay
  apuesta. Evita repetir el mismo error varias veces.

### 2.3 Umbral y anomalías
- `edge = p_modelo − p_mercado_shin` (en el runner apostado); `EV neto = p·(cuota−1)·(1−c) − (1−p)`.
- **3 pp** de edge supera la calibración del modelo en TEST (ECE 1,35 %) y la discrepancia entre
  métodos de desvigado (P90 0,56 pp). **+3 %** de EV neto exige un margen real tras la comisión.
- **Tope de 15 pp:** un edge enorme es mucho más probable que sea un error de identidad o de datos
  que una ventaja real; se registra para revisar y no se apuesta.
- **Limitación:** son valores de juicio, **no calibrados con datos**. El modelo aún no se ha
  comparado con el mercado (Phase 7). Se espera pocas apuestas y se asume la maldición del ganador:
  parte de los «edges» serán error del modelo.

### 2.4 Comisión
`BETFAIR_COMMISSION` vale 0,05 por defecto en `config.py`, como supuesto del proyecto. **No está
verificada contra la cuenta española.** Debe confirmarse antes de congelar.

### 2.5 Stake y ejecución
- Plano sobre el bankroll **inicial** (sin componer) para que las apuestas sean comparables; Kelly
  necesita probabilidades calibradas y estables, que aún no hay.
- Tope por **tamaño visible** al mejor BACK de ese runner (la mediana del mejor nivel de ambos lados
  es de ~20 €): si la profundidad es menor que el stake, se reduce; por debajo de un mínimo se omite.
  El mínimo de apuesta de la cuenta **no está verificado**.
- Se asume casada al precio visto (con 1–180 s de retraso). Se anota también la variante a un tick
  peor; el libro congelado ya contiene los datos para recalcularla.

### 2.6 Liquidación y casos límite
- Resultado: ganador/perdedor del mercado cerrado; `void` si el mercado se anula o un runner se
  retira (`REMOVED`).
- **Betfair solo conserva los libros de mercados cerrados 1–2 días:** un mercado sin liquidar a las
  48 h pasa a «sin resolver» y avisa; no se inventa un resultado.
- Aplazamientos: la apuesta sigue abierta mientras el mercado esté abierto; el CLV usa el cierre de
  DEC-021 y se marca no fiable si está lejos del inicio.

## 3. Reglas de honestidad (de DEC-022)
1. Esta estrategia **no se modifica** con resultados paper. Cambios solo antes de la primera apuesta
   o como `v2`.
2. Ver **cuántos candidatos** produce la regla en mercados futuros (`paper candidates`) es
   legítimo; ajustarla para tener más o menos apuestas, no.
3. Todo informe lleva «paper · datos retrasados · ejecución supuesta» y el tamaño de muestra.
4. El resultado paper **no es evidencia a favor** de la hipótesis (`METRICS.md`).

## 4. Pendiente tras la congelación
- [x] Aprobar la §1 (aprobada por el responsable, 2026-11-03).
- [x] Registrar el hash del fichero como `v1` (en el plan y el changelog).
- [ ] Confirmar la **comisión** de la cuenta `.es` y el **stake mínimo** (supuestos sin verificar).
- [ ] Fijar el mínimo de **historial** de un jugador (tras A1, antes de la primera apuesta).
