# Registro de decisiones (ADR simplificado)

Solo se registran decisiones **verificables** en el código, en los commits, en la documentación
existente o en una instrucción explícita del responsable del proyecto. Cada una indica su
fuente.

**Reglas**
- Una decisión no se edita para cambiar su sentido. Si cambia, se marca como **Sustituida por
  DEC-XXX** y se añade una nueva.
- Los identificadores `D1`–`D12` de [`IMPLEMENTATION_PLAN.md`](../IMPLEMENTATION_PLAN.md) §1 son
  el registro original. Aquí se referencian, no se duplican palabra por palabra.
- Las decisiones de modelado de detalle (K del Elo, antisimetría de la LogReg, ventanas de
  features) viven en [`MODELS.md`](MODELS.md).

**Estados:** `Vigente` · `Vigente (implícita)` (se deduce del código, pero no hay un documento
que la formule) · `Temporal` (con condición explícita de revisión) · `Sustituida`.

## Índice

| ID | Decisión | Estado |
|---|---|---|
| [DEC-001](#dec-001) | Solo paper betting; sin ninguna vía de apuestas reales | Vigente |
| [DEC-002](#dec-002) | Objetivo inicial: individuales ATP, pre-partido | Vigente (implícita) |
| [DEC-003](#dec-003) | El proyecto intenta refutar la hipótesis; criterio de éxito fijado de antemano | Vigente |
| [DEC-004](#dec-004) | PostgreSQL como fuente de verdad operativa | Vigente |
| [DEC-005](#dec-005) | Parquet (+ DuckDB) para histórico y ML; Parquet de Betfair solo por exportación | Vigente |
| [DEC-006](#dec-006) | Captura híbrida: hitos fijos + cadencia adaptive | Vigente |
| [DEC-007](#dec-007) | No filtrar observaciones sin precios; el umbral de liquidez se aplica al consultar | Vigente |
| [DEC-008](#dec-008) | Collector como servicio systemd, con un único proceso garantizado | Vigente |
| [DEC-009](#dec-009) | Cliente Betfair con `httpx` directo, no `betfairlightweight` | Vigente |
| [DEC-010](#dec-010) | Validación temporal estricta con split fijo | Vigente |
| [DEC-011](#dec-011) | TEST no se usa para ajustar; cada consulta queda registrada | Vigente |
| [DEC-012](#dec-012) | `MINIMUM_LIQUIDITY` = 50 no se modifica todavía | Temporal |
| [DEC-013](#dec-013) | Ningún modelo se promueve automáticamente | Vigente |
| [DEC-014](#dec-014) | Retención de 90 días solo después de un archivo verificado | Vigente (no activada) |
| [DEC-015](#dec-015) | No avanzar a Phase 4 hasta cerrar Phase 3.5 | Temporal |
| [DEC-016](#dec-016) | Ledger inmutable en PostgreSQL (sustituye al JSONL de D3) | Vigente |
| [DEC-017](#dec-017) | Python 3.13 gestionado por `uv`; stdlib antes que dependencias | Vigente |
| [DEC-018](#dec-018) | TennisMyLife como fuente primaria; mirror de Sackmann solo como contraste | Vigente |

---

<a id="dec-001"></a>
### DEC-001 — Solo paper betting; sin ninguna vía de apuestas reales

- **Fecha:** 2026-09-17 · **Estado:** Vigente
- **Contexto:** es un proyecto de investigación sobre la existencia de edge. Una ruta accidental
  a dinero real es el riesgo más grave (R10).
- **Decisión:**
  - `BETTING_MODE` es `Literal["paper"]`: cualquier otro valor impide arrancar.
  - `REAL_BETTING_ENABLED = False`.
  - No existe código capaz de enviar, modificar ni cancelar órdenes.
- **Razón:** una convención no es una garantía. La ausencia de código se puede verificar
  automáticamente.
- **Alternativas consideradas:** un flag de configuración para activar el modo live, descartado
  porque dejaría la capacidad a una variable de distancia.
- **Consecuencias:**
  - Tests `test_no_real_betting_surface.py`: escaneo de endpoints de órdenes, librerías de
    trading prohibidas (ni importadas ni instaladas) y cliente limitado a 3 operaciones de
    lectura.
  - Apostar de verdad exigiría escribir código nuevo, no cambiar configuración.
- **Revisar si:** existiera evidencia completa según el criterio de [DEC-003](#dec-003). Aun así,
  habría que hacerlo con una decisión nueva y explícita.
- **Fuente:** `src/edgecourt/config.py`, commit `d8f0885`, README («Qué NO es EdgeCourt»),
  riesgo R10.

<a id="dec-002"></a>
### DEC-002 — Objetivo inicial: individuales ATP, pre-partido

- **Fecha:** 2026-09-18 (se deduce de PHASE 1 y PHASE 8) · **Estado:** Vigente (implícita)
- **Contexto:** hay que acotar qué partidos puede predecir el modelo.
- **Decisión (tal como está implementada):**
  - El dataset histórico es **solo ATP individual**: 113.544 partidos de TennisMyLife ATP,
    1990–2026.
  - Las features dependen del ranking ATP.
  - El collector solo planifica capturas **antes** del inicio (`minutes_left < 0` → no captura).
- **Razón:** no está formulada explícitamente en ningún documento. Se deduce de las fuentes
  elegidas (`data/sources.py`) y de la política de captura.
- **Alternativas consideradas:** no documentadas.
- **Consecuencias:**
  - El collector captura **todo** `MATCH_ODDS` de tenis, incluidos WTA y dobles, que el modelo no
    puede predecir. En la Semana 1, solo 33 de 62 mercados eran individuales masculinos.
  - La muestra no contuvo torneos ATP regulares (ver Phase 3.5-C).
  - El emparejamiento de jugadores (PHASE 8b) solo tiene sentido para ese subconjunto.
- **Revisar si:** Phase 3.5-C concluye que los mercados ATP regulares no están disponibles, o que
  su calidad no permite evaluar el modelo.
- **Fuente:** `docs/DATA.md` (resultado de la ingesta), `src/edgecourt/data/sources.py`,
  `src/edgecourt/market/cadence.py` (`plan_captures`), commit `ed7cb77`.

<a id="dec-003"></a>
### DEC-003 — El proyecto intenta refutar la hipótesis; criterio de éxito fijado de antemano

- **Fecha:** 2026-09-17 · **Estado:** Vigente
- **Contexto:** es fácil encontrar «edge» a posteriori eligiendo la métrica o el segmento que
  mejor sale.
- **Decisión:**
  - Métricas de nivel 1: calibración y CLV.
  - El ROI es de nivel 3, por su baja potencia estadística: hacen falta unas 8.700 apuestas.
  - Solo hay evidencia a favor si se cumplen **a la vez** los 5 criterios de
    `docs/METRICS.md`.
  - Los segmentos negativos se publican.
- **Razón:** evitar el autoengaño por selección de resultados.
- **Alternativas consideradas:** usar el ROI como métrica principal, descartado por falta de
  potencia estadística (R6).
- **Consecuencias:**
  - El CLV exige un closing price fiable, lo que motiva Phase 3.5-B.
  - Sin histórico del exchange, la validación económica solo puede hacerse hacia delante (R4).
- **Revisar si:** nunca con resultados ya vistos.
- **Fuente:** `IMPLEMENTATION_PLAN.md` §6, `docs/METRICS.md`.

<a id="dec-004"></a>
### DEC-004 — PostgreSQL como fuente de verdad operativa

- **Fecha:** 2026-09-18 · **Estado:** Vigente · **Sustituye en parte a:** D2 (Parquet canónico
  de todo)
- **Contexto:** el collector 24/7 necesita escrituras pequeñas, concurrentes e idempotentes, y
  lecturas de estado (qué mercados se han observado y cuáles mostraron liquidez). Un fichero
  columnar hace mal justo eso.
- **Decisión:**
  - El collector escribe **solo** en PostgreSQL (local, versión 18), de forma transaccional por
    observación e idempotente por `(market_id, capture_key, observed_at)`.
  - Las predicciones, las paper bets y la liquidación también vivirán ahí.
- **Razón:** es el tipo de carga para la que está hecha una base de datos transaccional. **Nada
  se escribe dos veces:** una doble escritura acabaría divergiendo.
- **Alternativas consideradas:** mantener Parquet como destino primario, descartado por el patrón
  de escritura; DuckDB persistente, descartado en D2.
- **Consecuencias:**
  - Esquema versionado en `migrations/` (001–003).
  - Tests de integración contra una base **distinta** de la operativa: un validador impide que
    `EDGECOURT_TEST_DSN` coincida con `DATABASE_URL`.
- **Revisar si:** el volumen o la operación lo desbordan. No es previsible a esta escala.
- **Fuente:** `docs/ARCHITECTURE.md` («D2 revisada»), commits `025bab2`, `7eea268` y `34ac7a6`,
  [architecture/POSTGRESQL.md](architecture/POSTGRESQL.md).

<a id="dec-005"></a>
### DEC-005 — Parquet (+ DuckDB) para histórico y ML; Parquet de Betfair solo por exportación

- **Fecha:** 2026-09-17 (D2), matizada el 2026-09-18 · **Estado:** Vigente
- **Contexto:** el dataset histórico (113.544 partidos, Elo y features) es inmutable y se
  procesa por lotes.
- **Decisión:**
  - El pipeline histórico usa Parquet como almacén y DuckDB en memoria como motor de consulta.
  - Los Parquet de Betfair se generan desde PostgreSQL con `edgecourt db export-parquet`, como
    formato analítico y de archivo.
- **Razón:** reproducibilidad por copia de ficheros y ausencia de estado mutable.
- **Alternativas consideradas:** DuckDB persistente, descartado porque añade estado mutable; meter
  el histórico en PostgreSQL, descartado porque no aporta nada.
- **Consecuencias:** hay dos almacenes con responsabilidades separadas. `data/` y `models/` quedan
  fuera de git (D8).
- **Revisar si:** el ML necesita leer datos operativos a gran escala.
- **Fuente:** `IMPLEMENTATION_PLAN.md` D2, `docs/ARCHITECTURE.md`, `src/edgecourt/db/export_parquet.py`.

<a id="dec-006"></a>
### DEC-006 — Captura híbrida: hitos fijos + cadencia adaptive

- **Fecha:** 2026-09-18 · **Estado:** Vigente (con un defecto conocido, ver Phase 3.5-B)
- **Contexto:**
  - Observar cada minuto multiplicaría el volumen por ~240, con filas casi idénticas.
  - Observar solo 6 hitos deja sin datos el tramo final, donde se forma el cierre.
- **Decisión:**
  - Los hitos 24h, 12h, 6h, 1h, 10m y close se capturan **siempre**, con tolerancias de ±45, 30,
    20, 8, 3 y 2 min.
  - La cadencia adaptive solo se activa si el mercado ya ha mostrado precios o liquidez: cada
    30, 10, 5 y 1 min a menos de 360, 90, 30 y 10 min del inicio.
  - Si coinciden un hito y la cadencia adaptive, gana el hito.
- **Razón:** concentrar la resolución donde se mueve el precio sin gastar cuota de API en libros
  vacíos.
- **Alternativas consideradas:** captura por minuto (volumen excesivo) y solo hitos (tramo final
  ciego).
- **Consecuencias:**
  - El estado de cadencia se lee de PostgreSQL, así que sobrevive a reinicios.
  - **Defecto conocido:** los hitos se calculan contra la hora de inicio que se conoce en ese
    momento. Cuando `market_start_time` se retrasa, `close` no es un cierre, y la ventana de 0–10
    min se repite (~37 observaciones por mercado frente a ~10).
- **Revisar si:** Phase 3.5-B redefine el closing price.
- **Fuente:** `src/edgecourt/market/cadence.py`, `src/edgecourt/market/snapshots.py`, commits
  `025bab2` y `34ac7a6`, [auditoría Semana 1](audits/2026-09-week1-market-audit.md) §7.

<a id="dec-007"></a>
### DEC-007 — No filtrar observaciones sin precios; el umbral de liquidez se aplica al consultar

- **Fecha:** 2026-09-18 · **Estado:** Vigente
- **Contexto:** un mercado `OPEN` sin BACK/LAY es información válida: permite medir cuándo aparece
  la liquidez.
- **Decisión:**
  - `market_observation` tiene una fila **siempre** que se observa un mercado; `runner_price`,
    solo cuando hay precios.
  - `has_liquidity` significa «hay algo», no «hay suficiente».
  - `MINIMUM_LIQUIDITY` se aplica en las consultas, nunca al guardar.
- **Razón:** poder recalibrar el umbral con los datos recogidos, sin haberlos perdido al guardar.
- **Alternativas consideradas:** guardar solo los libros con precio, descartado porque impide
  medir la curva de aparición de liquidez.
- **Consecuencias:**
  - Existen los CHECK `observation_liquidity_implies_prices` y `observation_prices_imply_runners`.
  - Existe la vista `liquidity_emergence`.
  - La auditoría de la Semana 1 pudo medir la cobertura por hito gracias a esto.
- **Revisar si:** el volumen lo hiciera insostenible. No es el caso: 1.497 filas en 9 días.
- **Fuente:** `migrations/001_initial_schema.sql`, `src/edgecourt/db/repositories.py`, commit
  `025bab2`.

<a id="dec-008"></a>
### DEC-008 — Collector como servicio systemd, con un único proceso garantizado

- **Fecha:** 2026-09-18 · **Estado:** Vigente
- **Contexto:** la recolección debe ser desatendida y 24/7 (R4: el tiempo de muestra no se
  recupera).
- **Decisión:**
  - Unidad `edgecourt-collector.service`:
    - usuario sin privilegios (no root) y binario del `.venv` sin `uv run`;
    - `Restart=on-failure` con `RestartSec=60`, y límite de 5 reinicios en 10 min, en `[Unit]`;
    - endurecimiento (`ProtectSystem=strict`, `ProtectHome=read-only`, escritura solo en `data/`
      y `logs/`, `UMask=0077`);
    - logs JSON al journal.
  - Las credenciales se leen del `.env` del proyecto, **no** con `EnvironmentFile`.
  - Un advisory lock de PostgreSQL impide un segundo collector: si se lanza otro a mano, sale con
    código 6.
- **Razón:**
  - Es la supervisión nativa del sistema.
  - El advisory lock se libera solo si el proceso muere, sin restos que limpiar.
  - No meter secretos en un fichero legible por todos.
- **Alternativas consideradas:** fichero PID para el singleton, descartado porque deja restos.
- **Consecuencias:**
  - `scripts/install_service.sh` muestra la unidad y pide confirmación; nada se instala solo.
  - `deploy/README.md` y `docs/BETFAIR_SETUP.md` describen todavía un despliegue distinto
    (usuario `edgecourt`, `/opt/edgecourt`, `/etc/edgecourt/edgecourt.env`). Ver
    [problemas conocidos](PROJECT_STATUS.md#problemas-conocidos).
- **Revisar si:** se separan el collector y el predictor (D9, PHASE 15).
- **Fuente:** `deploy/edgecourt-collector.service`, commits `9bfebca` y `c292cbd`,
  `src/edgecourt/db/locks.py`.

<a id="dec-009"></a>
### DEC-009 — Cliente Betfair con `httpx` directo, no `betfairlightweight`

- **Fecha:** 2026-09-18 · **Estado:** Vigente (D7 resuelta)
- **Contexto:** hace falta un cliente de la Betting API de Betfair.
- **Decisión:** usar `httpx` directo, con solo 3 operaciones (`listEvents`,
  `listMarketCatalogue`, `listMarketBook`) y login por certificado.
- **Razón:** seguridad. `betfairlightweight` agrupa la colocación de órdenes en el mismo objeto
  cliente, lo que dejaría esa capacidad a un `import` de distancia.
- **Alternativas consideradas:** `betfairlightweight`.
- **Consecuencias:** reintentos, lotes y errores se mantienen a mano (~250 líneas con tests).
- **Revisar si:** nunca por comodidad. Solo si la garantía de solo lectura se pudiera mantener
  igual.
- **Fuente:** `docs/ARCHITECTURE.md` («D7 resuelta»), commit `ba92168`.

<a id="dec-010"></a>
### DEC-010 — Validación temporal estricta con split fijo

- **Fecha:** 2026-09-18 · **Estado:** Vigente
- **Contexto:** en series temporales, el split aleatorio filtra información del futuro.
- **Decisión:**
  - Split fijo e inmutable: TRAIN 2000–2022 · VALIDATION 2023 · TEST 2024–2025 · LIVE 2026 en
    adelante.
  - El split aleatorio está prohibido como validación principal.
  - Walk-forward en la evaluación.
- **Razón:** evitar el leakage temporal (R1, R2 y R8).
- **Alternativas consideradas:** k-fold aleatorio, descartado.
- **Consecuencias:**
  - El split está en código (`data/splits.py`).
  - Los tests de envenenamiento protegen el Elo, las features y la LogReg.
- **Revisar si:** nunca para el periodo ya fijado. Un split nuevo para datos nuevos exigiría una
  decisión nueva.
- **Fuente:** `docs/DATA.md` («Split temporal»), `docs/MODELS.md` («Validación»), commit
  `c3fae0a`.

<a id="dec-011"></a>
### DEC-011 — TEST no se usa para ajustar; cada consulta queda registrada

- **Fecha:** 2026-09-18 · **Estado:** Vigente
- **Contexto:** consultar TEST repetidamente es leakage humano (R8).
- **Decisión:**
  - Todos los hiperparámetros se eligen solo sobre VALIDATION.
  - Cada evaluación sobre TEST se registra en `data/results/test_evaluations.jsonl`, con fecha,
    modelo y motivo.
- **Razón:** que el grado de exposición a TEST sea auditable.
- **Alternativas consideradas:** ninguna documentada.
- **Consecuencias:** a 2026-09-27 hay **3** evaluaciones registradas: 2 del benchmark Elo
  (PHASE 2) y 1 de la LogReg (PHASE 4).
- **Revisar si:** nunca.
- **Fuente:** `docs/DATA.md`, `docs/MODELS.md`, `data/results/test_evaluations.jsonl` (no
  versionado).

<a id="dec-012"></a>
### DEC-012 — `MINIMUM_LIQUIDITY` = 50 no se modifica todavía

- **Fecha:** 2026-09-18, reafirmada el 2026-09-27 · **Estado:** Temporal
- **Contexto:** la auditoría de la Semana 1 muestra que 50 € no discrimina ningún mercado útil.
  Pero la muestra es pequeña (62 mercados) y atípica (solo competiciones por equipos o de
  exhibición).
- **Decisión:** mantener `MINIMUM_LIQUIDITY` = 50 (valor por defecto de `config.py`, también el
  efectivo) sin cambios, y no convertir las cifras de calidad en reglas permanentes.
- **Razón:** fijar un umbral con una muestra no representativa sería sobreajustar a una semana
  concreta. Además, gracias a [DEC-007](#dec-007) el umbral se puede recalcular después sin
  perder datos.
- **Alternativas consideradas:** subir el umbral ya, pospuesto.
- **Consecuencias:** el umbral actual no filtra la calidad real. El Value y el Risk Engine no
  deben apoyarse en él como garantía de ejecutabilidad.
- **Revisar si:** Phase 3.5 dispone de muestra de torneos regulares (3.5-C/D).
- **Fuente:** commits `025bab2`, `34ac7a6` y `9bfebca` («MINIMUM_LIQUIDITY sigue en 50»),
  instrucción del responsable del proyecto del 2026-09-27.

<a id="dec-013"></a>
### DEC-013 — Ningún modelo se promueve automáticamente

- **Fecha:** 2026-09-17 · **Estado:** Vigente
- **Contexto:** una promoción automática podría poner en producción un modelo sobreajustado sin
  revisión.
- **Decisión:**
  - El entrenamiento guarda en `models/challenger/`.
  - La promoción a `production` es manual.
  - En base de datos: índice único con como máximo un modelo en `production`, y
    `CHECK promotion_requires_who` (quién y cuándo).
- **Razón:** la promoción exige juicio humano (PHASE 13: `KEEP_PRODUCTION` / `REVIEW_CHALLENGER`).
- **Alternativas consideradas:** promoción automática por métrica, descartada.
- **Consecuencias:** `models/production/` está vacío a 2026-09-27. Hay 2 LogReg en `challenger/`.
- **Revisar si:** nunca, para la promoción automática.
- **Fuente:** README («Qué NO es EdgeCourt»), `docs/MODELS.md` («Versionado»),
  `migrations/001_initial_schema.sql`.

<a id="dec-014"></a>
### DEC-014 — Retención de 90 días solo después de un archivo verificado

- **Fecha:** 2026-09-18 · **Estado:** Vigente, **no activada**
- **Contexto:** PostgreSQL es operativo, no un archivo histórico. Pero borrar datos sin copia
  sería irreversible.
- **Decisión:**
  - `RETENTION_DAYS` = 90 (rango 7–3650).
  - Nada se purga sin una fila verificada en `archive_run`. La función SQL `assert_archived()`
    aborta cualquier purga no respaldada.
  - Las particiones son mensuales, para archivar con DETACH + DROP.
- **Razón:** que una purga sea imposible sin una exportación comprobada.
- **Alternativas consideradas:** `DELETE` masivo, descartado porque hincha la tabla y no deja
  garantía.
- **Consecuencias:**
  - A 2026-09-27 hay **0** filas en `archive_run` y no existe comando de purga: la retención no
    se ha activado.
  - `prediction` referencia las observaciones sin FK, para poder purgarlas.
- **Revisar si:** antes de activar la primera purga.
- **Fuente:** `src/edgecourt/config.py`, `migrations/002_*.sql` y `migrations/003_retention_guard.sql`,
  commits `025bab2` y `9bfebca` («No se ha activado retención ni purgado»).

<a id="dec-015"></a>
### DEC-015 — No avanzar a Phase 4 hasta cerrar Phase 3.5

- **Fecha:** 2026-09-27 · **Estado:** Temporal
- **Contexto:** la auditoría de la Semana 1 revela que `close` no es un cierre fiable y que la
  calidad y la disponibilidad del mercado son inciertas.
- **Decisión:** no empezar el trabajo pendiente de Phase 4 (modelos de probabilidad: XGBoost y
  siguientes) ni fases posteriores hasta cumplir el criterio de salida de
  [Phase 3.5](phases/PHASE_03_5_MARKET_VALIDATION.md).
- **Razón:** un Value Engine o un CLV construidos sobre capturas defectuosas darían resultados
  indistinguibles de artefactos.
- **Alternativas consideradas:** avanzar en paralelo en el modelado, descartado por el
  responsable del proyecto.
- **Consecuencias:**
  - La LogReg de PHASE 4 (numeración del plan) ya está hecha: no se deshace.
  - El collector sigue recogiendo datos (3.5-D).
- **Revisar si:** se cumple el criterio de salida de Phase 3.5.
- **Fuente:** instrucción del responsable del proyecto (2026-09-27).

<a id="dec-016"></a>
### DEC-016 — Ledger inmutable en PostgreSQL (sustituye al JSONL de D3)

- **Fecha:** 2026-09-18 · **Estado:** Vigente · **Sustituye a:** D3 (JSONL append-only)
- **Contexto:** D3 proponía un JSONL con `prev_hash`. Con PostgreSQL como almacén operativo
  ([DEC-004](#dec-004)), el ledger pasa a la base de datos.
- **Decisión:** `paper_bet` y `bet_settlement` son de solo inserción. La inmutabilidad tiene tres
  capas:
  1. la aplicación no emite `UPDATE` ni `DELETE`;
  2. unas `RULE ... DO INSTEAD NOTHING` anulan esas operaciones;
  3. cadena de hashes (`prev_hash` / `row_hash`).

  Además:
  - la liquidación va en una tabla aparte;
  - `paper_bet` congela el libro de precios en `jsonb`.
- **Razón:** una manipulación directa, incluso con superusuario, debe ser **detectable**.
  Separar la liquidación evita tener que actualizar la apuesta.
- **Alternativas consideradas:** JSONL (D3), sustituido; tabla con una convención de no
  actualizar, descartada porque una convención no es una garantía.
- **Consecuencias:**
  - Las tablas existen pero están vacías: el módulo `paper/` aún no está implementado.
  - `docs/ARCHITECTURE.md` sigue describiendo el JSONL. Ver
    [problemas conocidos](PROJECT_STATUS.md#problemas-conocidos).
- **Revisar si:** al implementar el paper betting.
- **Fuente:** `migrations/001_initial_schema.sql` y `migrations/002_*.sql`, commit `025bab2`.

<a id="dec-017"></a>
### DEC-017 — Python 3.13 gestionado por `uv`; stdlib antes que dependencias

- **Fecha:** 2026-09-17 · **Estado:** Vigente (D1, D4–D6)
- **Contexto:** el Python del sistema es 3.14, con riesgo de que no haya wheels maduras para
  XGBoost o scikit-learn.
- **Decisión:**
  - Python `>=3.13,<3.14`, gestionado por `uv`.
  - Stdlib antes que dependencias: `argparse`, `logging` con formatter JSON propio y `httpx`
    directo para Telegram.
  - Las dependencias se añaden en su fase, no antes.
- **Razón:** reproducibilidad y menos piezas en la ruta crítica.
- **Alternativas consideradas:** typer o click, structlog, python-telegram-bot.
- **Consecuencias:** hay 9 dependencias de ejecución (`pyproject.toml`).
- **Revisar si:** una dependencia aporta algo que la stdlib no puede dar.
- **Fuente:** `IMPLEMENTATION_PLAN.md` D1 y D4–D6, `pyproject.toml`.

<a id="dec-018"></a>
### DEC-018 — TennisMyLife como fuente primaria; mirror de Sackmann solo como contraste

- **Fecha:** 2026-09-18 · **Estado:** Vigente (D11)
- **Contexto:** los repositorios originales de Jeff Sackmann desaparecieron (verificado el
  2026-09-18).
- **Decisión:**
  - TennisMyLife como fuente primaria: es la única viva con estadísticas y aporta `indoor`.
  - El mirror archivístico solo sirve para auditar.
  - Uso no comercial, con atribución.
- **Razón:** sin una fuente viva no se pueden predecir partidos futuros.
- **Alternativas consideradas:** las de la tabla de `docs/DATA.md`.
- **Consecuencias:**
  - Hay un punto único de fallo (R12), mitigado con los crudos guardados y su SHA-256.
  - El contraste dio un 100 % de acuerdo en el ganador sobre 101.025 partidos.
- **Revisar si:** TennisMyLife deja de actualizarse.
- **Fuente:** `docs/DATA.md`, commit `ed7cb77`.

---

## Hechos verificados sin una decisión documentada

Son ciertos hoy, pero **no** hay ningún documento, commit ni instrucción que explique su porqué.
No se registran como decisiones para no inventar su justificación. Si se confirman, se les
asigna un DEC.

| Hecho | Verificación |
|---|---|
| **Sin Redis** | No aparece en el código, en `pyproject.toml` ni en la documentación |
| **Sin Docker para EdgeCourt** | No hay Dockerfile ni compose. PostgreSQL corre nativo (`postgresql.service`, en un puerto no estándar porque el 5432 lo ocupaba otro PostgreSQL en contenedor, según el commit `7eea268`) |
| Tipo de Application Key en uso (*delayed* o *live*) | **No verificado.** La documentación recomienda empezar con la *delayed*. Afecta a la fiabilidad del CLV |

## Decisiones pendientes

| Tema | Dónde |
|---|---|
| Definición de closing price | Phase 3.5-B |
| Qué mercados son el objetivo realista (ATP regular, otros, viabilidad) | Phase 3.5-C |
| Intervalo de `keepAlive` | Phase 3.5-A |
| Uno o dos procesos de larga duración (D9) | `docs/ARCHITECTURE.md`, PHASE 15 del plan |
| Método de desvigado y convención de signo del CLV | `docs/METRICS.md` (se fijará en la fase de Value/CLV) |
