# Modelos y features

> Estado: PHASE 0. El catálogo de features se rellena en PHASE 3 y los resultados
> comparativos a partir de PHASE 4. Aquí se fijan de antemano las reglas del juego.

## Catálogo de modelos

| ID | Modelo | Papel | Fase |
|---|---|---|---|
| **MODEL 0** | Probabilidad implícita de Betfair | **Benchmark a batir.** Es el precio que debemos considerar eficiente por defecto. | 8 |
| **MODEL 1** | Elo global + Surface Elo | Baseline independiente del ML. Si el ML no lo bate, el ML sobra. | 2 |
| **MODEL 2** | Regresión logística | Baseline lineal, interpretable, sobre las features de PHASE 3. | 4 |
| **MODEL 3** | XGBoost | Captura no linealidades e interacciones. | 5 |

LightGBM, CatBoost y ensembles quedan **fuera de alcance** hasta que exista una justificación
concreta. Añadir modelos multiplica las oportunidades de sobreajustar por selección.

## Contrato de un modelo de probabilidad

Todo modelo expone la misma interfaz, lo que los hace intercambiables y comparables:

```python
predict_proba(features) -> (p_a, p_b)    con p_a + p_b == 1
```

Reglas:

- Un modelo **nunca** ve cuotas, bankroll ni stake. Solo devuelve probabilidad.
- Un modelo **nunca** ve datos con fecha ≥ la del partido que predice.
- Toda salida se somete a `test_probability_sum`.

## Versionado

Cada modelo entrenado se acompaña de un manifiesto JSON: identificador, fecha de
entrenamiento, rango temporal de los datos, lista y hash de features, hiperparámetros, hash del
artefacto binario y métricas de validación. Las predicciones del ledger guardan
`model_version`, de modo que cualquier resultado sea atribuible a un modelo concreto.

El binario del modelo **no se versiona en git** (ver `.gitignore`): se identifica por hash.

## Política de features

Tres reglas, en orden de importancia:

1. **Nada que no se conociera antes del partido.** Toda estadística de rendimiento entra
   únicamente como agregado histórico desplazado (`shift(1)` por jugador en orden cronológico).
   Las columnas `raw_*` de `match_facts` no son accesibles al generador de features.
2. **Toda feature es relativa A−B** y antisimétrica: intercambiar A y B debe invertir el signo.
   Verificado por `test_features_antisymmetry`.
3. **Ninguna feature entra "porque está disponible".** Cada una se documenta abajo con
   definición, ventana, justificación y política de nulos. Sin justificación, no entra.

## Catálogo de features *(PHASE 3)*

Previstas, pendientes de implementar y documentar una por una:

| Grupo | Features |
|---|---|
| Ranking | `ranking_diff`, `rank_points_diff` |
| Elo | `elo_diff`, `surface_elo_diff` |
| Forma | `winrate_last_{5,10,20}_diff`, `surface_winrate_diff` |
| Servicio | `first_serve_pct_diff`, `first_serve_points_won_diff`, `second_serve_points_won_diff` |
| Resto | `return_points_won_diff` |
| Puntos de break | `break_points_saved_diff`, `break_points_converted_diff` |
| Ritmo | `aces_rate_diff`, `double_fault_rate_diff` |
| Fatiga | `days_since_last_match_diff`, `matches_last_{7,14}_days_diff`, `minutes_played_last_7_days_diff` |
| H2H | `head_to_head_before_match` |
| Contexto | `tournament_level`, `surface`, `round`, `indoor` |

**Todas las del grupo Servicio/Resto/Break/Ritmo son medias móviles de partidos anteriores.**
Nunca el valor del partido en curso. Este es el punto donde el proyecto se rompería en silencio.

## Elo *(PHASE 2)*

Cuatro ratings por jugador: global, hard, clay, grass. Actualización en **una sola pasada
cronológica** siguiendo `order_key`. Para cada partido se persiste el estado *previo*:
`elo_a_before`, `elo_b_before`, `elo_diff`, `surface_elo_a_before`, `surface_elo_b_before`,
`surface_elo_diff`.

El Elo es el primer benchmark serio: un modelo de ML que no lo supere de forma estable no
justifica su complejidad.

## Calibración *(PHASE 6)*

- **Sigmoid/Platt** cuando la muestra es limitada.
- **Isotónica** cuando hay muestra suficiente (más flexible, más propensa a sobreajustar).
- Ajustada sobre un **conjunto de calibración temporalmente separado**, nunca sobre el de
  entrenamiento.
- Se reporta ECE y curva de calibración antes y después.

## Validación

Validación **cronológica siempre**; el split aleatorio está prohibido como validación
principal. Walk-forward a partir de PHASE 7: reentrenar por ventana y evaluar la siguiente,
sin que ninguna ventana de entrenamiento contenga datos posteriores a su fecha de evaluación.

Los hiperparámetros se buscan **solo sobre VALIDATION**, con presupuesto de iteraciones
registrado, para que el grado de búsqueda sea auditable.
