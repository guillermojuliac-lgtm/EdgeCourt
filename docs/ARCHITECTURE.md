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
| `market/` | Betfair solo lectura, snapshots, collector 24/7 | 8 ✅ |
| `value/` | edge y valor esperado | 9 |
| `risk/` | filtros, staking, límites duros | 10 |
| `paper/` | ledger inmutable y liquidación | 11 |
| `metrics/` | Brier, LogLoss, CLV, ROI, drawdown | 12 |
| `training/` | reentrenamiento y comparación P/C | 13–14 |
| `notifications/` | Telegram | 15 |

## Decisiones tomadas

### D2 revisada (2026-09-18): PostgreSQL operativo + Parquet analítico

El diseño original usaba Parquet como almacén canónico de todo. Al pasar el collector a
operación 24/7 esa elección dejó de servir: escrituras pequeñas y concurrentes, idempotencia y
lecturas de estado (¿qué mercados ya observé? ¿cuáles mostraron liquidez?) son justo lo que un
fichero columnar hace mal.

Reparto actual:

| | PostgreSQL | Parquet |
|---|---|---|
| **Rol** | estado operativo 24/7 | analítico e histórico |
| **Fuente de verdad de** | Betfair en vivo, predicciones, paper bets | dataset histórico de tenis |
| **Patrón** | escrituras pequeñas, concurrentes, idempotentes | lotes, inmutable |

**Nada se escribe dos veces.** El collector escribe solo en PostgreSQL; los Parquet de Betfair
se generan por exportación (`edgecourt db export-parquet`). Una doble escritura simultánea
acabaría divergiendo sin que nadie supiera cuál es la buena.

El pipeline histórico (`matches`, `elo`, `features`) **no cambia**: 113.544 partidos inmutables
no ganan nada en una base de datos operativa.

### Parquet canónico + DuckDB como motor (histórico de ML)

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

### D7 resuelta (PHASE 8): `httpx` directo, no `betfairlightweight`

El motivo decisivo no es el peso de la dependencia sino la **seguridad**. `betfairlightweight`
agrupa la colocación y cancelación de órdenes en el mismo objeto cliente que las consultas: la
capacidad de mover dinero real quedaría a un `import` de distancia dentro del proceso, aunque
nunca se llamase. Escribiendo las tres llamadas de lectura que necesitamos, ese código no
existe, y su ausencia es verificable automáticamente.

Coste asumido: mantener a mano el manejo de reintentos, lotes y errores de la API. Son ~250
líneas, cubiertas por tests, a cambio de que la garantía central del proyecto sea comprobable
en lugar de prometida.

## Decisiones pendientes

- **Uno o dos procesos de larga duración** (PHASE 15): se empieza con uno; se separa solo si
  aparece una razón concreta (cadencias incompatibles o aislamiento de fallos).

## Operación 24/7 *(pendiente, PHASE 15)*

Requisitos para todo proceso de larga duración: recuperación ante errores temporales, retry con
backoff exponencial, timeouts en toda llamada de red, apagado limpio ante SIGTERM y ausencia de
bucles agresivos. Las unidades systemd se muestran antes de instalarse; nunca se instalan solas.
