# EdgeCourt

Sistema ligero, modular y autónomo de **investigación cuantitativa** sobre mercados de tenis
de Betfair Exchange.

> **Estado actual: PHASE 3 completada.** Dataset histórico (113.544 partidos ATP, 1990–2026),
> benchmark Elo (+11,3 % de Brier skill sobre TEST) y 21 features sin leakage temporal.
> Siguiente: collector de Betfair. Ver [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md).

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

Capa aislada y **de solo lectura**: autenticación, eventos, mercados, back/lay, liquidez y
timestamps. Se guardan snapshots a 24h/12h/6h/1h/10m/cierre en la medida en que estén
disponibles, registrando siempre el timestamp real de observación.

## systemd

Se proporcionarán unidades de ejemplo en `deploy/`. **No se instalan automáticamente**: se
muestran primero para que sean revisadas.

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
