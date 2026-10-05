"""Bucle del collector de cuotas. Proceso de larga duracion, **solo lectura**
frente a Betfair y **escritura transaccional** frente a PostgreSQL.

PostgreSQL es la fuente de verdad operativa: cada observacion se escribe alli, de
forma idempotente y dentro de una transaccion por mercado. Parquet deja de ser
destino primario y pasa a ser formato de exportacion y archivo analitico
(`edgecourt db export-parquet`).

Requisitos de operacion 24/7 (brief §20), todos implementados aqui:

* recuperacion ante errores temporales, sin morir por un ciclo fallido;
* espera exponencial acotada tras fallos consecutivos;
* timeouts en toda llamada de red (los aplica el cliente);
* apagado limpio ante SIGTERM, cerrando sesion en Betfair y la conexion a la BD;
* sin bucles agresivos: entre ciclos se espera, y la espera es interrumpible.

El collector **no decide nada**. Observa el mercado y escribe lo observado. No
existe aqui, ni en ninguna parte del proyecto, codigo capaz de enviar una apuesta.
"""

from __future__ import annotations

import signal
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg

from edgecourt.config import Settings
from edgecourt.db.connection import connect, dsn_from_env, transaction
from edgecourt.db.locks import singleton
from edgecourt.db.repositories import (
    ensure_partitions,
    load_market_states,
    save_observation,
    upsert_event,
    upsert_market,
    upsert_runner,
)
from edgecourt.logging_setup import get_logger
from edgecourt.market import persistence, snapshots
from edgecourt.market.auth import MissingCredentialsError, SessionManager
from edgecourt.market.cadence import DEFAULT_CADENCE, CadenceRule, plan_captures
from edgecourt.market.client import MarketFilter, ReadOnlyBettingClient
from edgecourt.systemd_notify import SystemdNotifier

log = get_logger("collector")

# Margen sobre el hito mas lejano (24h) para que un mercado entre en el catalogo
# antes de que venza su primera captura.
LOOKAHEAD_MARGIN_HOURS = 2

MAX_CONSECUTIVE_FAILURES_BEFORE_LONG_WAIT = 5
LONG_WAIT_SECONDS = 300.0


@dataclass(slots=True)
class CycleResult:
    """Lo ocurrido en un ciclo. Es lo que se registra y lo que se testea."""

    markets_seen: int = 0
    snapshots_due: int = 0
    observations_written: int = 0
    with_prices: int = 0
    without_prices: int = 0
    # Observaciones que PostgreSQL rechazo por sus datos. No abortan el ciclo.
    failed_markets: list[str] = field(default_factory=list)
    labels: dict[str, int] = field(default_factory=dict)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class Collector:
    """Recolecta snapshots de cuotas de los mercados de tenis."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: ReadOnlyBettingClient | None = None,
        sessions: SessionManager | None = None,
        connection: psycopg.Connection | None = None,
        clock=None,
        cadence: tuple[CadenceRule, ...] = DEFAULT_CADENCE,
        adaptive_enabled: bool = True,
        notifier: SystemdNotifier | None = None,
    ) -> None:
        self._settings = settings
        # Sin NOTIFY_SOCKET (fuera de systemd) el notificador es un no-op.
        self._notifier = notifier or SystemdNotifier.from_env()
        self._sessions = sessions or SessionManager(settings)
        self._client = client or ReadOnlyBettingClient(self._sessions)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._cadence = cadence
        self._adaptive_enabled = adaptive_enabled
        self._stop = threading.Event()
        self._consecutive_failures = 0
        self._run_id = uuid.uuid4()

        # La conexion puede inyectarse (tests) o abrirse bajo demanda.
        self._connection = connection
        self._owns_connection = connection is None
        self._connection_context = None

    # -- Conexion -------------------------------------------------------------

    @property
    def run_id(self) -> uuid.UUID:
        """Identifica esta ejecucion en cada fila escrita, para poder auditarla."""
        return self._run_id

    def connection(self) -> psycopg.Connection:
        """Conexion a PostgreSQL, abierta la primera vez que se necesita."""
        if self._connection is None:
            self._connection_context = connect(dsn_from_env(self._settings))
            self._connection = self._connection_context.__enter__()
        return self._connection

    def _close_connection(self) -> None:
        if self._owns_connection and self._connection_context is not None:
            self._connection_context.__exit__(None, None, None)
            self._connection_context = None
            self._connection = None

    # -- Ciclo ----------------------------------------------------------------

    def _catalogue_window(self, now: datetime) -> MarketFilter:
        horizon = now + timedelta(
            hours=max(snapshots.SNAPSHOT_TARGETS.values()) / 60 + LOOKAHEAD_MARGIN_HOURS
        )
        return MarketFilter(
            market_start_from=now.isoformat().replace("+00:00", "Z"),
            market_start_to=horizon.isoformat().replace("+00:00", "Z"),
        )

    def _sync_catalogue(self, catalogues: list[dict[str, Any]], now: datetime) -> None:
        """Refresca eventos, mercados y runners. Idempotente.

        Va en su propia transaccion: el catalogo debe quedar disponible aunque
        despues falle la lectura de precios de algun mercado.
        """
        connection = self.connection()
        with transaction(connection) as cursor:
            ensure_partitions(cursor, now)
            for catalogue in catalogues:
                event = persistence.event_from_catalogue(catalogue)
                market = persistence.market_from_catalogue(catalogue)
                if event is None or market is None:
                    continue
                upsert_event(cursor, event)
                upsert_market(cursor, market)
                for runner in persistence.runners_from_catalogue(catalogue):
                    upsert_runner(cursor, runner)

    def run_cycle(self) -> CycleResult:
        """Ejecuta un ciclo completo: mirar que toca, capturarlo y guardarlo."""
        result = CycleResult()
        now = self._clock()

        catalogues = self._client.list_market_catalogue(self._catalogue_window(now))
        result.markets_seen = len(catalogues)
        if not catalogues:
            return result

        self._sync_catalogue(catalogues, now)

        connection = self.connection()
        market_ids = [c["marketId"] for c in catalogues if c.get("marketId")]
        with connection.cursor() as cursor:
            states = load_market_states(cursor, market_ids)
        connection.commit()

        planned = plan_captures(
            catalogues,
            states,
            now=now,
            rules=self._cadence,
            adaptive_enabled=self._adaptive_enabled,
        )
        result.snapshots_due = len(planned)
        if not planned:
            return result

        by_market = {c.get("marketId"): c for c in catalogues}
        keys = {plan.market_id: key for plan, key in planned}

        # Se agrupa por hito para que cada lote de listMarketBook comparta
        # etiqueta: una sola respuesta no puede repartirse entre dos hitos.
        by_label: dict[str, list[str]] = {}
        for plan, _ in planned:
            by_label.setdefault(plan.label, []).append(plan.market_id)

        for label, ids in by_label.items():
            books = self._client.list_market_book(ids)
            observed_at = self._clock()
            result.labels[label] = len(ids)

            for book in books:
                catalogue = by_market.get(book.get("marketId"))
                if catalogue is None:
                    continue
                payload = persistence.observation_from_book(
                    book,
                    catalogue,
                    label=label,
                    capture_key=keys.get(book.get("marketId"), label),
                    observed_at=observed_at,
                    collector_run_id=self._run_id,
                )
                if payload is None:
                    continue

                # Una transaccion por mercado: nunca puede quedar una observacion
                # que declare has_prices sin sus filas de precio.
                #
                # Aislamiento de fallos (incidente del 2026-09-29): si PostgreSQL
                # rechaza los DATOS de un mercado (desbordamiento, CHECK...), solo
                # se deshace la transaccion de ese mercado y el ciclo sigue con los
                # demas. Antes la excepcion salia del bucle y se perdian todos los
                # mercados posteriores del ciclo. Los errores de conexion
                # (OperationalError, InterfaceError) se siguen propagando: con la
                # conexion rota no tiene sentido seguir, y el bucle principal la
                # recupera como hasta ahora.
                try:
                    with transaction(connection) as cursor:
                        save_observation(cursor, payload)
                except (psycopg.DataError, psycopg.IntegrityError) as exc:
                    result.failed_markets.append(payload.market_id)
                    log.error(
                        "observacion rechazada por PostgreSQL",
                        extra={
                            "market_id": payload.market_id,
                            "snapshot_label": payload.snapshot_label,
                            "error_type": type(exc).__name__,
                            "error": str(exc).splitlines()[0][:200],
                        },
                    )
                    continue

                result.observations_written += 1
                if payload.has_prices:
                    result.with_prices += 1
                else:
                    result.without_prices += 1

        return result

    # -- Bucle de larga duracion ---------------------------------------------

    def request_stop(self) -> None:
        """Pide el apagado. Seguro de llamar desde un manejador de senales."""
        self._stop.set()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def install_signal_handlers(self) -> None:
        """Convierte SIGTERM y SIGINT en una parada ordenada."""

        def handler(signum, _frame):  # pragma: no cover - depende del SO
            log.info("senal recibida, apagando", extra={"signal": signum})
            self.request_stop()

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):  # pragma: no cover - hilos secundarios
                log.warning("no se pudo instalar el manejador", extra={"signal": sig})

    def run(self, *, max_cycles: int | None = None) -> int:
        """Bucle principal. Devuelve el numero de ciclos ejecutados.

        Toma un bloqueo exclusivo mientras corre: dos collectors simultaneos no
        corromperian nada -las escrituras son idempotentes- pero duplicarian el
        consumo de cuota de la API de Betfair.
        """
        interval = float(self._settings.collector_interval_seconds)
        cycles = 0
        log.info(
            "collector iniciado",
            extra={
                "interval_s": interval,
                "mode": self._settings.betting_mode,
                "read_only": True,
                "run_id": str(self._run_id),
            },
        )

        try:
            with singleton(self.connection()):
                # READY=1 solo cuando el proceso esta preparado para operar:
                # configuracion cargada (Settings ya existe), PostgreSQL accesible
                # (la conexion se acaba de abrir) y bloqueo singleton tomado. No
                # se espera a Betfair: la sesion es perezosa y una caida temporal
                # de Betfair no debe impedir arrancar; de eso se ocupa el watchdog.
                self._notifier.ready()
                cycles = self._loop(max_cycles, interval)
        finally:
            self._notifier.stopping()
            self._sessions.close()
            self._close_connection()
            log.info("collector detenido", extra={"cycles": cycles})

        return cycles

    def _loop(self, max_cycles: int | None, interval: float) -> int:
        """Itera hasta que se pida parar o se alcance `max_cycles`."""
        cycles = 0
        while not self._stop.is_set():
            if max_cycles is not None and cycles >= max_cycles:
                break

            try:
                result = self.run_cycle()
                self._consecutive_failures = 0
                log.info(
                    "ciclo completado",
                    extra={
                        "markets": result.markets_seen,
                        "due": result.snapshots_due,
                        "written": result.observations_written,
                        "with_prices": result.with_prices,
                        "without_prices": result.without_prices,
                        "failed_markets": result.failed_markets,
                        "labels": result.labels,
                    },
                )
                # Unico punto de latido: el ciclo termino sin excepcion. Un ciclo
                # con 0 mercados, 0 observaciones o 0 precios tambien es sano. Si
                # el ciclo falla, o el bucle se cuelga, no se llega aqui y systemd
                # agota WatchdogSec.
                self._notifier.watchdog()
            except MissingCredentialsError:
                # Un fallo de configuracion no es temporal: reintentarlo cada
                # minuto durante horas solo esconde el problema en los logs.
                log.error("faltan credenciales de Betfair, abortando")
                raise
            except Exception as exc:  # noqa: BLE001 - un ciclo no puede matar el proceso
                self._rollback()
                self._consecutive_failures += 1
                log.error(
                    "ciclo fallido",
                    extra={
                        "error": str(exc),
                        "consecutive_failures": self._consecutive_failures,
                    },
                    exc_info=True,
                )

            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                break
            self._stop.wait(self._wait_seconds(interval))

        return cycles

    def _rollback(self) -> None:
        """Deja la conexion utilizable tras un fallo a mitad de ciclo."""
        if self._connection is None:
            return
        try:
            self._connection.rollback()
        except psycopg.Error:  # pragma: no cover - la conexion ya no sirve
            self._close_connection()

    def _wait_seconds(self, interval: float) -> float:
        """Espera entre ciclos, alargada si se acumulan fallos consecutivos."""
        if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES_BEFORE_LONG_WAIT:
            return LONG_WAIT_SECONDS
        if self._consecutive_failures:
            return min(interval * (2**self._consecutive_failures), LONG_WAIT_SECONDS)
        return interval
