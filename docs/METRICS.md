# Métricas

> Estado: PHASE 0. Definiciones fijadas **antes** de ver resultados, deliberadamente.

## Jerarquía: qué métrica decide

No todas las métricas tienen el mismo poder para responder a la pregunta del proyecto. El
orden importa, y se fija ahora para no elegir después la que mejor quede.

| Nivel | Métrica | Por qué |
|---|---|---|
| **1** | Calibración (ECE, curva) | Se calcula sobre **todas** las predicciones (~150/día), no solo sobre las apostadas. Máxima potencia estadística y condición necesaria: sin probabilidades calibradas, cualquier edge es ruido. |
| **1** | CLV medio | Señal por apuesta mucho menos ruidosa que el P&L. Es la evidencia más eficiente de que detectamos precio erróneo. |
| **2** | Brier skill score vs mercado | Mide si aportamos algo *sobre* el precio, que es el benchmark real. |
| **3** | ROI / yield después de costes | Lo que importa en última instancia, pero con potencia estadística muy baja a corto plazo (ver abajo). |
| **4** | Drawdown, profit factor | Tolerabilidad, no evidencia. |

### Por qué el ROI es de nivel 3

Con stake plano y cuotas medias ~2.0, la desviación típica del retorno por apuesta es ≈1 unidad.
Detectar un yield real de +3% con potencia ~80% y α=5% requiere del orden de:

```
n ≈ (2.8 · σ / yield)²  =  (2.8 · 1.0 / 0.03)²  ≈  8.700 apuestas liquidadas
```

A 6 apuestas al día son unos **4 años**. Cualquier lectura del ROI antes de eso es
**no concluyente**, gane o pierda. Esto se recuerda en todos los informes.

---

## Definiciones

### Probabilísticas

- **Brier Score** — `mean((p̂ − y)²)`. Menor es mejor. Referencia: 0.25 es la moneda.
- **Brier Skill Score** — `1 − BS_modelo / BS_referencia`. La referencia por defecto es
  **el mercado**, no la moneda: superar a la moneda no demuestra nada.
- **Log Loss** — `−mean(y·log p̂ + (1−y)·log(1−p̂))`. Penaliza la confianza equivocada.
  Las probabilidades se recortan a `[ε, 1−ε]` para evitar infinitos.
- **ROC-AUC** — capacidad de ordenación. Se reporta, pero **no se optimiza**: un modelo puede
  tener AUC alta y estar mal calibrado, y para apostar la calibración es lo que importa.
- **Calibration Error (ECE)** — se agrupan las predicciones en deciles de probabilidad y se
  promedia `|frecuencia observada − probabilidad media|` ponderando por tamaño de grupo. Se
  reporta siempre junto al número de observaciones por decil.

### De mercado

- **Probabilidad implícita bruta** — `1 / odds`. La suma sobre las selecciones supera 1: ese
  exceso es el *overround*.
- **Probabilidad desvigada** — normalización que reparte el overround. El método elegido se
  documenta en PHASE 9; métodos distintos dan resultados distintos y la elección se fija una
  vez, no por conveniencia.
- **Spread** — diferencia entre el mejor back y el mejor lay. Un spread ancho indica que el
  precio observado es menos informativo.

### Económicas

- **Edge** — `probabilidad_modelo − probabilidad_mercado`, en espacio de probabilidad.
- **Expected Value (bruto)** — `p·(odds−1) − (1−p)` por unidad apostada.
- **Expected Value después de costes** — el anterior descontando la comisión de Betfair sobre
  ganancias netas del mercado. **Es la cifra sobre la que se aplican los filtros.** El EV bruto
  es meramente informativo: un edge de +5% puede evaporarse con comisión y spread.
- **ROI / yield** — `beneficio_neto / total_apostado`. Se reporta siempre con `n` y con
  intervalo de confianza.
- **Profit factor** — `ganancias_brutas / pérdidas_brutas`.
- **Maximum drawdown** — máxima caída desde un máximo previo de la curva de capital,
  en porcentaje del bankroll.

### CLV (Closing Line Value)

Se calcula en **espacio de probabilidad desvigada**, no en cuotas, porque una diferencia de
cuota no significa lo mismo a 1.2 que a 8.0:

```
CLV = p_cierre_desvigada − p_entrada_desvigada        (para una apuesta back)
```

Un CLV medio positivo con significancia estadística indica que sistemáticamente conseguimos
mejor precio que el cierre del mercado, y es la evidencia más eficiente disponible de que el
modelo detecta precio erróneo. La convención de signo y el método de desvigado se fijan en
PHASE 12 y no se cambian después.

---

## Segmentación obligatoria

Todo informe segmenta por: **superficie**, **nivel de torneo**, **rango de cuota**,
**rango de edge**, **modelo** y **mes**.

**Los segmentos negativos se publican siempre.** Un resultado agregado positivo que depende
de un solo segmento, de un mes concreto o de un puñado de apuestas se reporta como
**no concluyente**, no como éxito.

## Criterio de decisión final

EdgeCourt se considerará evidencia **a favor** de la hipótesis solo si se cumple *todo* a la vez:

1. calibración out-of-sample buena y estable;
2. Brier skill score positivo frente al mercado en el subconjunto apostable;
3. CLV medio positivo con significancia estadística sobre muestra suficiente;
4. ROI después de costes no negativo, con drawdown tolerable;
5. estabilidad mes a mes.

Si alguno falla, la conclusión honesta es "no hay evidencia suficiente" — que es un resultado
válido y el más probable a priori.
