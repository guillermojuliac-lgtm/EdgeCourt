# EdgeCourt

Sistema ligero, modular y autónomo de **investigación cuantitativa** sobre mercados de tenis
de Betfair Exchange.

> **Estado actual: PHASES 0–4 y 8 completadas.** Dataset histórico (113.544 partidos ATP),
> benchmark Elo, regresión logística (+3,6 % de Brier skill sobre TEST), 21 features sin
> leakage y collector de Betfair operativo escribiendo en PostgreSQL.
> Ver [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md).

---

## Qué es EdgeCourt

Una herramienta para responder con datos a una única pregunta:

> ¿Podemos generar probabilidades de tenis suficientemente buenas como para identificar de
> manera consistente situaciones donde el precio de Betfair ofrece esperanza matemática
> positiva **después de costes**?

El objetivo es **intentar refutar** esa hipótesis, no confirmarla.

## Qué NO es EdgeCourt

- **No apuesta con dinero real.** No existe ninguna función capaz de enviar una orden a
  Betfair. `BETTING_MODE` solo admite el valor `paper`, y un test (`test_no_real_betting_surface.py`)
  falla si alguien introduce código de colocación de órdenes en el árbol.
- No es un tipster ni un generador de señales para terceros.
- No es una plataforma web. Solo hay CLI.
- No usa LLMs para predecir. Las probabilidades salen de modelos estadísticos auditables.
- No promueve modelos automáticamente. La promoción de un Challenger requiere una persona.

---

## Arquitectura

```
datos históricos ─► features (sin leakage) ─► modelo ─► calibración
                                                            │
        snapshots Betfair ─► probabilidad de mercado ────────┤
                                                            ▼
                                                     VALUE ENGINE
                                                            ▼
                                                      RISK ENGINE
                                                            ▼
                                            PAPER LEDGER (append-only)
                                                            ▼
                                       MÉTRICAS: Brier, CLV, ROI, drawdown
```

Separación de responsabilidades innegociable:

- El **modelo** solo devuelve probabilidades. Nunca decide cuánto apostar.
- El **Value Engine** solo compara con el mercado. Nunca decide apostar.
- El **Risk Engine** es el único que produce un stake, y siempre con límites duros.

Detalle en [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## Instalación

Requisitos: Linux, `curl`, y nada más. `uv` gestiona Python 3.13 por sí mismo.

```bash
# 1. uv (modo usuario, sin sudo)
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

# 2. Entorno y dependencias
cd EdgeCourt
uv sync --all-groups

# 3. Configuración
cp .env.example .env     # editar; el .env nunca se versiona

# 4. Comprobación
uv run pytest
uv run edgecourt status
```

## Configuración

Toda la configuración va por variables de entorno o por un `.env` local, y se valida con
Pydantic en el arranque: un valor incoherente impide arrancar en lugar de degradarse en
silencio. Ver [`.env.example`](.env.example) para el listado completo.

Validaciones de seguridad relevantes:

| Regla | Efecto |
|---|---|
| `BETTING_MODE` ∈ {`paper`} | cualquier otro valor aborta el arranque |
| `KELLY_FRACTION` ≤ 0.25 | Full Kelly es imposible por construcción |
| `MAX_STAKE_PERCENTAGE` ≤ `MAX_MARKET_EXPOSURE` ≤ `MAX_DAILY_EXPOSURE` | límites coherentes |
| `TELEGRAM_ENABLED=true` | exige token y chat id |

Los secretos nunca se escriben en los logs: un filtro de redacción se aplica a todos los
handlers, tanto por valor conocido como por patrón.

## Uso

```bash
uv run edgecourt status          # estado del sistema
uv run edgecourt data fetch      # descargar CSV historicos (accion explicita)
uv run edgecourt data import     # construir el dataset canonico match_facts
uv run edgecourt data check      # contrastar con la fuente de referencia
uv run edgecourt elo build       # calcular Elo global y por superficie
uv run edgecourt elo evaluate    # benchmark Elo con calibracion por buckets
uv run edgecourt features build  # generar la tabla de features
uv run edgecourt collector start # recoger cuotas de Betfair (solo lectura)
uv run edgecourt collector status # cobertura de observaciones (desde PostgreSQL)
uv run edgecourt db migrate      # aplicar migraciones
uv run edgecourt db status       # estado del esquema operativo
uv run edgecourt db liquidity    # curva de aparicion de liquidez
uv run edgecourt db export-parquet # exportar observaciones a Parquet
uv run edgecourt train           # PHASE 4-5
uv run edgecourt backtest        # PHASE 7
uv run edgecourt collector start # PHASE 8
uv run edgecourt predict         # PHASE 9
uv run edgecourt paper status    # PHASE 11
uv run edgecourt metrics         # PHASE 12
uv run edgecourt model compare   # PHASE 13
```

Los subcomandos aún no implementados salen con código 3 e indican su fase.

## Datos

Parquet es el almacén canónico; DuckDB es el motor de consulta sobre esos ficheros. No hay
base de datos mutable que sea fuente de verdad, de modo que un dataset es reproducible por
copia. Fuentes, esquema, licencias y política de exclusiones: [`docs/DATA.md`](docs/DATA.md).

**No se descarga ningún dataset automáticamente.** `edgecourt data fetch` es un comando
explícito que declara sus fuentes antes de empezar.

Fuente primaria: **TennisMyLife**. Fuente de contraste: mirror archivístico de los datos de
**Jeff Sackmann**. Uso **no comercial**, con atribución a ambos. Los repositorios originales
de Sackmann desaparecieron en 2026; el detalle de la investigación de fuentes, sus licencias
y los riesgos está en [`docs/DATA.md`](docs/DATA.md).

## Entrenamiento, backtesting y métricas

- Validación **siempre cronológica**. El split aleatorio está prohibido como validación principal.
- El backtest temporal valida **calidad probabilística**, no rentabilidad: no disponemos de
  histórico de cuotas de Betfair, así que cualquier ROI retrospectivo sería ficción.
- Métricas primarias: **calibración** y **CLV**. El ROI paper es secundario por potencia
  estadística (ver `docs/METRICS.md`).

Detalle en [`docs/MODELS.md`](docs/MODELS.md) y [`docs/METRICS.md`](docs/METRICS.md).

## Paper betting

Ledger append-only con hash encadenado: una modificación retrospectiva de una predicción ya
registrada es *detectable*, no solo desaconsejada. La liquidación nunca reescribe la predicción.

## Betfair

Capa **de solo lectura por construcción**. EdgeCourt consulta eventos, mercados, precios back y
lay, liquidez y timestamps. No existe ninguna función capaz de enviar, modificar o cancelar una
apuesta, y tres tests lo verifican de forma automática:

- `test_source_contains_no_order_placement_calls` — escanea el árbol buscando endpoints de ejecución.
- `test_no_trading_library_is_imported` / `test_trading_libraries_are_not_installed` — impiden
  que entre una librería que traiga esa capacidad consigo.
- `test_market_package_exposes_no_write_operations` — el cliente solo puede exponer
  `list_events`, `list_market_catalogue` y `list_market_book`.

Por eso se usa `httpx` directo y **no** `betfairlightweight`: esa librería agrupa la colocación
de órdenes en el mismo objeto cliente que las consultas, lo que dejaría la capacidad de apostar
a un `import` de distancia. Escribiendo las tres llamadas que necesitamos, ese código no existe.

### Snapshots

Se apunta a capturar cada mercado a **24 h, 12 h, 6 h, 1 h, 10 min y cierre** antes del
inicio, con ventanas de tolerancia que se estrechan al acercarse (±45 min a 24 h, ±3 min a
10 min). Se guardan tres niveles de profundidad de back y lay, liquidez, estado del mercado y
retardo de apuesta.

Tres reglas que determinan la calidad del dato:

1. **Se registra el timestamp real de observación**, nunca el planificado. Cada fila lleva
   `observed_at`, `snapshot_label` (el hito al que apuntaba) y `minutes_to_start` (la distancia
   real). El análisis debe usar `minutes_to_start`.
2. **Un hito perdido no se rellena a posteriori.** Si el proceso estuvo caído, ese snapshot se
   queda vacío. Un "snapshot de 24 h" tomado a 3 h del inicio sería un dato falso.
3. **Deduplicación por `market_id:selection_id:label`.** Un reintento sustituye la captura, no
   la duplica.

`edgecourt collector status` muestra la cobertura por hito, huecos incluidos.

### Obtener las credenciales

Cuatro pasos. **Ningún secreto se escribe nunca en el código ni se comparte por chat.**

#### 1. Cuenta y verificación

Necesitas una cuenta de Betfair verificada según su política KYC.

#### 2. Application Key

Se obtiene con la operación `createDeveloperAppKeys` desde la *Accounts API Demo Tool* del
[portal de desarrolladores](https://developer.betfair.com/):

1. Inicia sesión en Betfair en otra pestaña.
2. Abre la herramienta de demo de la Accounts API y elige `createDeveloperAppKeys`.
3. Refresca para que se rellene tu token de sesión.
4. Introduce un nombre de aplicación único a nivel global (p. ej. `edgecourt-<algo-tuyo>`).
5. Ejecuta.

Se generan **dos** claves:

| Clave | Estado | Datos | Coste |
|---|---|---|---|
| **Delayed** | activa | retrasados | gratis |
| **Live** | inactiva | tiempo real | **tasa única de activación de £499** |

> **Empieza con la Delayed Key.** Para los hitos lejanos (24 h, 12 h, 6 h) el retraso es
> irrelevante: el precio no se mueve de forma apreciable en segundos cuando faltan horas. Te
> permite validar el collector entero, acumular muestra y comprobar que el pipeline funciona
> sin gastar nada.
>
> La Live Key solo se justifica cuando la investigación demuestre que hace falta precisión en
> los hitos cercanos. Ten en cuenta que **el CLV medido con datos retrasados es menos fiable**,
> porque el precio de cierre es justo el que más se mueve: trátalo como orientativo hasta
> tener datos en vivo.

Verifica el importe y las condiciones en el portal antes de decidir: pueden haber cambiado.

#### 3. Certificado autofirmado (login no interactivo)

Imprescindible para 24/7: sin él la sesión caduca y el proceso no puede renovarse solo.
Betfair exige **RSA de 2048 bits**.

```bash
mkdir -p ~/.config/edgecourt/betfair && cd ~/.config/edgecourt/betfair

openssl genrsa -out client-2048.key 2048
openssl req -new -key client-2048.key -out client-2048.csr
openssl x509 -req -days 3650 -in client-2048.csr -signkey client-2048.key -out client-2048.crt

chmod 600 client-2048.key client-2048.crt
```

En el `openssl req` puedes dejar todos los campos en blanco salvo *Common Name*, donde conviene
poner algo identificable. **No pongas contraseña a la clave**: un proceso desatendido no puede
teclearla al arrancar.

Después, súbelo a tu cuenta:

1. Ve a `https://myaccount.betfair.com/accountdetails/mysecurity?showAPI=1`
   (`.es`, `.com.au` o `.it` según tu jurisdicción).
2. Busca la sección **"Automated Betting Program Access"** y pulsa **Edit**.
3. Sube **`client-2048.crt`** — el `.crt`, **no** el `.csr`.
4. Pulsa **Upload Certificate**.

#### 4. Configurar EdgeCourt

```bash
cp .env.example .env
```

Rellena en `.env`: `BETFAIR_USERNAME`, `BETFAIR_PASSWORD`, `BETFAIR_APP_KEY`,
`BETFAIR_CERT_PATH` y `BETFAIR_KEY_PATH` (rutas absolutas). El `.env` está en `.gitignore` y
los certificados viven fuera del repositorio.

Comprueba con un único ciclo:

```bash
uv run edgecourt collector start --max-cycles 1
```

Si falta alguna variable, sale con código **5** y dice exactamente cuál. Cuando funcione:

```bash
uv run edgecourt collector start     # corre hasta recibir SIGTERM
uv run edgecourt collector status    # cobertura de snapshots
```

## systemd

Unidades de ejemplo en [`deploy/`](deploy/). **No se instalan automáticamente**: se muestran
primero para que sean revisadas. `edgecourt-collector.service` incluye apagado limpio por
SIGTERM, límite de reinicios para que un fallo de credenciales no entre en bucle, y
endurecimiento (`ProtectSystem=strict`, `NoNewPrivileges`, sin acceso de escritura fuera de
`data/` y `logs/`).

## Telegram

Notificaciones opcionales y limitadas a eventos relevantes: resumen diario, errores graves,
pérdida de conexión prolongada, nuevo Challenger, métricas anómalas, drawdown límite. Nunca
una notificación por snapshot.

## Desarrollo

```bash
uv run pytest                    # suite completa
uv run pytest -m critical        # solo los tests que bloquean avance de fase
uv run ruff check . && uv run ruff format --check .
```

Un fallo en un test marcado `critical` bloquea el avance a la siguiente fase.
