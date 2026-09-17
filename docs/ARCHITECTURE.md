# Arquitectura

> Estado: PHASE 0. Este documento crece con cada fase. Las secciones marcadas
> *(pendiente)* describen intención de diseño, no código existente.

## Principio rector

Cada etapa tiene **una** responsabilidad y un contrato de entrada/salida estrecho. La razón no
es estética: es que cada etapa debe poder ser testeada y refutada por separado. Un sistema en
el que el modelo decide el stake no permite saber si el problema está en la probabilidad o en
la gestión de riesgo.

```
match_facts (Parquet inmutable)
      │
      ▼  una sola pasada cronológica
   Elo / Surface Elo
      │
      ▼  barrera anti-leakage: solo datos con fecha < T
   feature builder
      │
      ▼  contrato: devuelve P(A), P(B) con P(A)+P(B)=1
   probability model
      │
      ▼
   calibration
      │            Betfair snapshots (solo lectura)
      │                     │
      │                     ▼  back/lay, spread, overround, comisión
      └──────────────► market probability
                            │
                            ▼  edge, EV, EV después de costes
                      VALUE ENGINE
                            │
                            ▼  filtros + stake + caps duros
                       RISK ENGINE
                            │
                            ▼  append-only, hash encadenado
                      PAPER LEDGER
                            │
                            ▼
                    settlement → MÉTRICAS
```

## Reglas de separación

| Componente | Puede | No puede |
|---|---|---|
| Probability model | devolver probabilidades | conocer cuotas, bankroll o stake |
| Value Engine | calcular edge y EV | decidir si se apuesta, conocer el bankroll |
| Risk Engine | aplicar filtros y calcular stake | modificar probabilidades |
| Paper Ledger | registrar y liquidar | alterar una entrada ya escrita |
| Capa Betfair | leer | escribir órdenes (no existe el código) |

## Módulos

| Módulo | Responsabilidad | Fase |
|---|---|---|
| `config.py` | configuración tipada y validada | 0 ✅ |
| `logging_setup.py` | logging JSON, rotación, redacción de secretos | 0 ✅ |
| `storage.py` | Parquet (canónico) + DuckDB (consulta) | 0 ✅ |
| `cli.py` | interfaz de línea de comandos | 0 ✅ |
| `data/` | ingesta y esquema canónico | 1 |
| `features/` | Elo y feature engineering sin leakage | 2–3 |
| `models/` | LogReg, XGBoost, versionado | 4–5 |
| `calibration/` | Platt / isotónica, curvas, ECE | 6 |
| `market/` | Betfair solo lectura, probabilidad de mercado | 8, 12 |
| `value/` | edge y valor esperado | 9 |
| `risk/` | filtros, staking, límites duros | 10 |
| `paper/` | ledger inmutable y liquidación | 11 |
| `metrics/` | Brier, LogLoss, CLV, ROI, drawdown | 12 |
| `training/` | reentrenamiento y comparación P/C | 13–14 |
| `notifications/` | Telegram | 15 |

## Decisiones tomadas

### Parquet canónico + DuckDB como motor (no como estado)

DuckDB se abre en memoria y lee los Parquet. No hay fichero `.db` que sea fuente de verdad.
Consecuencias: un dataset se reproduce copiando ficheros; el consumo de RAM queda acotado
porque DuckDB proyecta y filtra sin materializar; y no existe estado acumulado que pueda
divergir silenciosamente de los datos.

*Alternativa descartada:* DuckDB persistente como base de datos. Aporta transaccionalidad que
aquí no necesitamos y añade un estado mutable que complica la reproducibilidad.

### Ledger append-only con hash encadenado

La inmutabilidad del registro de apuestas simuladas es un requisito duro: si se pudiera
reescribir una predicción después de conocer el resultado, todas las métricas serían inválidas.
Un fichero JSONL con `prev_hash` convierte una manipulación en algo **detectable**.

*Alternativa descartada:* tabla en base de datos con convención de "no actualizar". Una
convención no es una garantía.

### stdlib sobre dependencias

`argparse` en lugar de typer/click; `logging` + formatter propio en lugar de structlog;
`httpx` directo en lugar de un cliente de Telegram. Tres dependencias menos en la ruta
crítica, ninguna funcionalidad perdida.

### Python 3.13 gestionado por uv

El sistema trae 3.14, para el que las wheels de XGBoost/scikit-learn pueden no estar maduras.
`uv` fija un 3.13 propio del proyecto sin tocar el Python del sistema.

## Decisiones pendientes

- **`betfairlightweight` vs `httpx` directo** (PHASE 8): depende de si usamos login por
  certificado, requerido para operación 24/7 no interactiva. Se documentará aquí al decidirse.
- **Uno o dos procesos de larga duración** (PHASE 15): se empieza con uno; se separa solo si
  aparece una razón concreta (cadencias incompatibles o aislamiento de fallos).

## Operación 24/7 *(pendiente, PHASE 15)*

Requisitos para todo proceso de larga duración: recuperación ante errores temporales, retry con
backoff exponencial, timeouts en toda llamada de red, apagado limpio ante SIGTERM y ausencia de
bucles agresivos. Las unidades systemd se muestran antes de instalarse; nunca se instalan solas.
