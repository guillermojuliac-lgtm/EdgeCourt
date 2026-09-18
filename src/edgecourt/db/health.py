"""Estado de salud del collector, leido de PostgreSQL.

Pensado para supervisar una ejecucion desatendida: responde a "sigue vivo y
escribiendo?" sin necesidad de leer logs.

Distingue deliberadamente dos preguntas que no son la misma:

* **Esta escribiendo?** -> hay observaciones recientes;
* **Esta capturando precios?** -> hay observaciones recientes *con precios*.

La segunda puede ser negativa sin que nada falle: si los mercados no tienen
libro, el collector hace su trabajo correctamente y registra que estaban vacios.
Confundir ambas cosas llevaria a perseguir averias inexistentes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg


@dataclass(slots=True)
class HealthReport:
    now: datetime
    last_observation_at: datetime | None = None
    last_priced_observation_at: datetime | None = None
    observations_total: int = 0
    observations_24h: int = 0
    priced_24h: int = 0
    markets_total: int = 0
    markets_upcoming: int = 0
    runs_24h: int = 0
    last_run_id: str | None = None
    partition_default_rows: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def minutes_since_last_observation(self) -> float | None:
        if self.last_observation_at is None:
            return None
        return (self.now - self.last_observation_at).total_seconds() / 60.0

    @property
    def minutes_since_last_priced(self) -> float | None:
        if self.last_priced_observation_at is None:
            return None
        return (self.now - self.last_priced_observation_at).total_seconds() / 60.0

    @property
    def healthy(self) -> bool:
        return not self.warnings

    def as_dict(self) -> dict[str, Any]:
        return {
            "healthy": self.healthy,
            "last_observation_at": self.last_observation_at,
            "minutes_since_last_observation": self.minutes_since_last_observation,
            "last_priced_observation_at": self.last_priced_observation_at,
            "observations_total": self.observations_total,
            "observations_24h": self.observations_24h,
            "priced_24h": self.priced_24h,
            "markets_total": self.markets_total,
            "markets_upcoming": self.markets_upcoming,
            "runs_24h": self.runs_24h,
            "warnings": self.warnings,
        }


def collect_health(
    connection: psycopg.Connection,
    *,
    now: datetime | None = None,
    stale_after_minutes: float = 180.0,
) -> HealthReport:
    """Reune el estado del collector.

    `stale_after_minutes` es generoso a proposito: con la politica de captura por
    hitos, un mercado lejano puede pasar horas sin generar observaciones sin que
    nada vaya mal. Avisar antes produciria falsas alarmas.
    """
    now = now or datetime.now(UTC)
    report = HealthReport(now=now)
    since = now - timedelta(hours=24)

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT max(observed_at) AS ultima,
                   count(*) AS total,
                   count(*) FILTER (WHERE observed_at >= %(since)s) AS ultimas_24h,
                   count(*) FILTER (
                       WHERE observed_at >= %(since)s AND has_prices
                   ) AS con_precios_24h,
                   max(observed_at) FILTER (WHERE has_prices) AS ultima_con_precios,
                   count(DISTINCT collector_run_id) FILTER (
                       WHERE observed_at >= %(since)s
                   ) AS ejecuciones
            FROM market_observation
            """,
            {"since": since},
        )
        row = cursor.fetchone()
        report.last_observation_at = row["ultima"]
        report.observations_total = row["total"]
        report.observations_24h = row["ultimas_24h"]
        report.priced_24h = row["con_precios_24h"]
        report.last_priced_observation_at = row["ultima_con_precios"]
        report.runs_24h = row["ejecuciones"]

        cursor.execute(
            """
            SELECT count(*) AS total,
                   count(*) FILTER (WHERE market_start_time > %(now)s) AS proximos
            FROM betfair_market
            """,
            {"now": now},
        )
        row = cursor.fetchone()
        report.markets_total = row["total"]
        report.markets_upcoming = row["proximos"]

        # Filas en la particion por defecto significan que falto crear la del mes.
        cursor.execute("SELECT count(*) AS n FROM market_observation_default")
        report.partition_default_rows = cursor.fetchone()["n"]

    connection.commit()

    # --- Avisos --------------------------------------------------------------
    if report.observations_total == 0:
        report.warnings.append("no hay ninguna observacion registrada")
    elif (
        minutes := report.minutes_since_last_observation
    ) is not None and minutes > stale_after_minutes:
        report.warnings.append(
            f"la ultima observacion es de hace {minutes / 60:.1f} h "
            f"(umbral: {stale_after_minutes / 60:.1f} h)"
        )

    if report.markets_upcoming == 0 and report.markets_total > 0:
        report.warnings.append(
            "no hay mercados proximos en el catalogo: puede ser normal fuera de temporada"
        )

    if report.partition_default_rows:
        report.warnings.append(
            f"{report.partition_default_rows} filas en la particion por defecto: "
            "falta crear la particion del mes"
        )

    return report
