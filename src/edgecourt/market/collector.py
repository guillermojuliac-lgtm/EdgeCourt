"""Bucle del collector de cuotas. Proceso de larga duracion, **solo lectura**.

Requisitos de operacion 24/7 (brief §20), todos implementados aqui:

* recuperacion ante errores temporales, sin morir por un ciclo fallido;
* espera exponencial acotada tras fallos consecutivos;
* timeouts en toda llamada de red (los aplica el cliente);
* apagado limpio ante SIGTERM, cerrando sesion en Betfair;
* sin bucles agresivos: entre ciclos se espera, y la espera es interrumpible.

El collector **no decide nada**. Observa el mercado y escribe lo observado.
"""

from __future__ import annotations

import signal
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from edgecourt.config import Settings
from edgecourt.logging_setup import get_logger
from edgecourt.market import snapshots
from edgecourt.market.auth import MissingCredentialsError, SessionManager
from edgecourt.market.client import MarketFilter, ReadOnlyBettingClient

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
    rows_written: int = 0
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
        clock=None,
    ) -> None:
        self._settings = settings
        self._sessions = sessions or SessionManager(settings)
        self._client = client or ReadOnlyBettingClient(self._sessions)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._stop = threading.Event()
        self._consecutive_failures = 0

    # -- Ciclo ----------------------------------------------------------------

    def _catalogue_window(self, now: datetime) -> MarketFilter:
        horizon = now + timedelta(
            hours=max(snapshots.SNAPSHOT_TARGETS.values()) / 60 + LOOKAHEAD_MARGIN_HOURS
        )
        return MarketFilter(
            market_start_from=now.isoformat().replace("+00:00", "Z"),
            market_start_to=horizon.isoformat().replace("+00:00", "Z"),
        )

    def run_cycle(self) -> CycleResult:
        """Ejecuta un ciclo completo: mirar que toca, capturarlo y guardarlo."""
        result = CycleResult()
        now = self._clock()

        catalogues = self._client.list_market_catalogue(self._catalogue_window(now))
        result.markets_seen = len(catalogues)
        if not catalogues:
            return result

        # Se consultan solo las particiones de hoy y manana: un partido no puede
        # tener capturas fuera de esa ventana.
        dates = sorted(
            {(now + timedelta(days=offset)).date().isoformat() for offset in (-1, 0, 1, 2)}
        )
        captured = snapshots.existing_snapshot_ids(self._settings.odds_dir, dates=dates)

        due = snapshots.due_snapshots(catalogues, now=now, already_captured=captured)
        result.snapshots_due = len(due)
        if not due:
            return result

        by_market = {c.get("marketId"): c for c in catalogues}
        rows: list[dict[str, Any]] = []

        # Se agrupa por hito para que cada lote de listMarketBook comparta
        # etiqueta: asi una sola respuesta no se reparte entre dos hitos.
        for label in snapshots.SNAPSHOT_TARGETS:
            market_ids = [plan.market_id for plan in due if plan.label == label]
            if not market_ids:
                continue

            books = self._client.list_market_book(market_ids)
            observed_at = self._clock()
            for book in books:
                catalogue = by_market.get(book.get("marketId"))
                if catalogue is None:
                    continue
                rows.extend(
                    snapshots.normalise_market_book(
                        book, catalogue, label=label, observed_at=observed_at
                    )
                )
            result.labels[label] = len(market_ids)

        frame = snapshots.to_frame(rows)
        result.rows_written = snapshots.append_snapshots(self._settings.odds_dir, frame)
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

        `max_cycles` existe para los tests y para ejecuciones puntuales; en
        produccion se deja en None y el proceso corre hasta recibir SIGTERM.
        """
        interval = float(self._settings.collector_interval_seconds)
        cycles = 0
        log.info(
            "collector iniciado",
            extra={
                "interval_s": interval,
                "mode": self._settings.betting_mode,
                "read_only": True,
            },
        )

        try:
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
                            "rows": result.rows_written,
                            "labels": result.labels,
                        },
                    )
                except MissingCredentialsError:
                    # Un fallo de configuracion no es temporal: reintentarlo cada
                    # minuto durante horas solo esconde el problema en los logs.
                    # Se propaga para que el operador lo vea de inmediato.
                    log.error("faltan credenciales de Betfair, abortando")
                    raise
                except Exception as exc:  # noqa: BLE001 - un ciclo no puede matar el proceso
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
        finally:
            self._sessions.close()
            log.info("collector detenido", extra={"cycles": cycles})

        return cycles

    def _wait_seconds(self, interval: float) -> float:
        """Espera entre ciclos, alargada si se acumulan fallos consecutivos."""
        if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES_BEFORE_LONG_WAIT:
            return LONG_WAIT_SECONDS
        if self._consecutive_failures:
            return min(interval * (2**self._consecutive_failures), LONG_WAIT_SECONDS)
        return interval
