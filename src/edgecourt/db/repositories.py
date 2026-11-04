"""Acceso a datos del almacenamiento operativo.

SQL explicito, sin ORM. Toda escritura es **idempotente**: reejecutar un ciclo
del collector tras un fallo parcial no duplica nada.

Regla de transaccionalidad: observacion y precios se escriben **juntos o nada**,
para que nunca exista una fila que declare `has_prices = true` sin sus precios.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

import psycopg
from psycopg.types.json import Jsonb

from edgecourt.logging_setup import get_logger
from edgecourt.market.cadence import MarketState

if TYPE_CHECKING:
    from edgecourt.models.base import ModelManifest
    from edgecourt.prediction.service import PredictionRow

log = get_logger("db.repositories")

PRICE_COLUMNS: tuple[str, ...] = tuple(
    f"{side}_{kind}_{level}"
    for side in ("back", "lay")
    for level in (1, 2, 3)
    for kind in ("price", "size")
)


@dataclass(slots=True)
class ObservationPayload:
    """Una observacion de mercado con sus precios, lista para persistir."""

    market_id: str
    observed_at: datetime
    capture_key: str
    snapshot_label: str
    minutes_to_start: float
    market_status: str
    inplay: bool
    bet_delay: int | None
    active_runners: int | None
    total_matched: float | None
    collector_run_id: uuid.UUID
    runners: list[dict[str, Any]] = field(default_factory=list)

    # --- Metricas derivadas -------------------------------------------------

    @property
    def runners_with_prices(self) -> int:
        return sum(1 for r in self.runners if _has_any_price(r))

    @property
    def has_prices(self) -> bool:
        return self.runners_with_prices > 0

    @property
    def total_available(self) -> float:
        return float(
            sum(
                _as_float(r.get(column)) or 0.0
                for r in self.runners
                for column in PRICE_COLUMNS
                if column.endswith(("_size_1", "_size_2", "_size_3"))
            )
        )

    @property
    def has_liquidity(self) -> bool:
        """'Hay algo', no 'hay suficiente'.

        El umbral de negocio (MINIMUM_LIQUIDITY) se aplica al consultar, nunca
        al guardar: cocinarlo aqui impediria recalibrarlo despues con los datos
        ya recogidos.
        """
        return self.total_available > 0.0

    @property
    def best_back_available(self) -> float | None:
        values = [_as_float(r.get("back_size_1")) for r in self.runners]
        present = [v for v in values if v is not None]
        return float(sum(present)) if present else None

    @property
    def best_lay_available(self) -> float | None:
        values = [_as_float(r.get("lay_size_1")) for r in self.runners]
        present = [v for v in values if v is not None]
        return float(sum(present)) if present else None

    @property
    def max_spread_pct(self) -> float | None:
        """Peor spread relativo entre los runners con precio en ambos lados."""
        spreads: list[float] = []
        for runner in self.runners:
            back = _as_float(runner.get("back_price_1"))
            lay = _as_float(runner.get("lay_price_1"))
            if back and lay and back > 0:
                spreads.append((lay - back) / back * 100.0)
        return max(spreads) if spreads else None


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if result != result else result  # descarta NaN


def _has_any_price(runner: dict[str, Any]) -> bool:
    return any(
        _as_float(runner.get(column)) is not None for column in ("back_price_1", "lay_price_1")
    )


# ---------------------------------------------------------------------------
# Catalogo
# ---------------------------------------------------------------------------


def upsert_event(cursor: psycopg.Cursor, event: dict[str, Any]) -> None:
    """Inserta o refresca un evento. `first_seen_at` no se toca al actualizar."""
    cursor.execute(
        """
        INSERT INTO betfair_event (
            event_id, event_name, competition_id, competition_name,
            country_code, timezone, open_date
        ) VALUES (%(event_id)s, %(event_name)s, %(competition_id)s,
                  %(competition_name)s, %(country_code)s, %(timezone)s, %(open_date)s)
        ON CONFLICT (event_id) DO UPDATE SET
            event_name       = EXCLUDED.event_name,
            competition_id   = EXCLUDED.competition_id,
            competition_name = EXCLUDED.competition_name,
            country_code     = EXCLUDED.country_code,
            timezone         = EXCLUDED.timezone,
            open_date        = EXCLUDED.open_date,
            last_seen_at     = now()
        """,
        event,
    )


def upsert_market(cursor: psycopg.Cursor, market: dict[str, Any]) -> None:
    cursor.execute(
        """
        INSERT INTO betfair_market (
            market_id, event_id, market_name, market_type, market_start_time
        ) VALUES (%(market_id)s, %(event_id)s, %(market_name)s,
                  %(market_type)s, %(market_start_time)s)
        ON CONFLICT (market_id) DO UPDATE SET
            market_name       = EXCLUDED.market_name,
            market_start_time = EXCLUDED.market_start_time,
            last_seen_at      = now()
        """,
        market,
    )


def upsert_runner(cursor: psycopg.Cursor, runner: dict[str, Any]) -> None:
    """Inserta o refresca un runner.

    `player_id` no se toca nunca aqui: lo rellenara PHASE 8b tras un
    emparejamiento explicito, y un refresco del catalogo no debe borrarlo.
    """
    cursor.execute(
        """
        INSERT INTO betfair_runner (
            market_id, selection_id, runner_name, sort_priority, handicap
        ) VALUES (%(market_id)s, %(selection_id)s, %(runner_name)s,
                  %(sort_priority)s, %(handicap)s)
        ON CONFLICT (market_id, selection_id) DO UPDATE SET
            runner_name   = EXCLUDED.runner_name,
            sort_priority = EXCLUDED.sort_priority
        """,
        runner,
    )


# ---------------------------------------------------------------------------
# Observaciones y precios
# ---------------------------------------------------------------------------


def save_observation(cursor: psycopg.Cursor, payload: ObservationPayload) -> int:
    """Persiste una observacion y sus precios. Idempotente.

    Ante conflicto se **actualiza**, no se ignora: un reintento puede traer mas
    informacion que la primera captura (por ejemplo precios donde antes no
    habia), y esa version mas completa debe prevalecer.

    Devuelve el `observation_id`.
    """
    cursor.execute(
        """
        INSERT INTO market_observation (
            market_id, observed_at, capture_key, snapshot_label, minutes_to_start,
            market_status, inplay, bet_delay, active_runners, total_matched,
            has_prices, has_liquidity, runners_with_prices, total_available,
            best_back_available, best_lay_available, max_spread_pct, collector_run_id
        ) VALUES (
            %(market_id)s, %(observed_at)s, %(capture_key)s, %(snapshot_label)s,
            %(minutes_to_start)s, %(market_status)s, %(inplay)s, %(bet_delay)s,
            %(active_runners)s, %(total_matched)s, %(has_prices)s, %(has_liquidity)s,
            %(runners_with_prices)s, %(total_available)s, %(best_back_available)s,
            %(best_lay_available)s, %(max_spread_pct)s, %(collector_run_id)s
        )
        ON CONFLICT (market_id, capture_key, observed_at) DO UPDATE SET
            market_status       = EXCLUDED.market_status,
            inplay              = EXCLUDED.inplay,
            total_matched       = EXCLUDED.total_matched,
            has_prices          = EXCLUDED.has_prices,
            has_liquidity       = EXCLUDED.has_liquidity,
            runners_with_prices = EXCLUDED.runners_with_prices,
            total_available     = EXCLUDED.total_available,
            best_back_available = EXCLUDED.best_back_available,
            best_lay_available  = EXCLUDED.best_lay_available,
            max_spread_pct      = EXCLUDED.max_spread_pct
        RETURNING observation_id
        """,
        {
            "market_id": payload.market_id,
            "observed_at": payload.observed_at,
            "capture_key": payload.capture_key,
            "snapshot_label": payload.snapshot_label,
            "minutes_to_start": payload.minutes_to_start,
            "market_status": payload.market_status,
            "inplay": payload.inplay,
            "bet_delay": payload.bet_delay,
            "active_runners": payload.active_runners,
            "total_matched": payload.total_matched,
            "has_prices": payload.has_prices,
            "has_liquidity": payload.has_liquidity,
            "runners_with_prices": payload.runners_with_prices,
            "total_available": payload.total_available,
            "best_back_available": payload.best_back_available,
            "best_lay_available": payload.best_lay_available,
            "max_spread_pct": payload.max_spread_pct,
            "collector_run_id": payload.collector_run_id,
        },
    )
    observation_id = cursor.fetchone()["observation_id"]

    for runner in payload.runners:
        if not _has_any_price(runner):
            continue
        _save_runner_price(cursor, observation_id, payload.observed_at, runner)

    return observation_id


def _save_runner_price(
    cursor: psycopg.Cursor, observation_id: int, observed_at: datetime, runner: dict[str, Any]
) -> None:
    columns = ", ".join(PRICE_COLUMNS)
    placeholders = ", ".join(f"%({column})s" for column in PRICE_COLUMNS)
    updates = ", ".join(f"{column} = EXCLUDED.{column}" for column in PRICE_COLUMNS)

    parameters: dict[str, Any] = {
        "observation_id": observation_id,
        "observed_at": observed_at,
        "selection_id": runner["selection_id"],
        "runner_status": runner.get("runner_status") or "UNKNOWN",
        "last_price_traded": _as_float(runner.get("last_price_traded")),
        "runner_total_matched": _as_float(runner.get("runner_total_matched")),
    }
    for column in PRICE_COLUMNS:
        parameters[column] = _as_float(runner.get(column))

    cursor.execute(
        f"""
        INSERT INTO runner_price (
            observation_id, observed_at, selection_id, runner_status,
            last_price_traded, runner_total_matched, {columns}
        ) VALUES (
            %(observation_id)s, %(observed_at)s, %(selection_id)s, %(runner_status)s,
            %(last_price_traded)s, %(runner_total_matched)s, {placeholders}
        )
        ON CONFLICT (observation_id, observed_at, selection_id) DO UPDATE SET
            runner_status        = EXCLUDED.runner_status,
            last_price_traded    = EXCLUDED.last_price_traded,
            runner_total_matched = EXCLUDED.runner_total_matched,
            {updates}
        """,
        parameters,
    )


def load_market_states(cursor: psycopg.Cursor, market_ids: list[str]) -> dict[str, MarketState]:
    """Estado de cada mercado, para decidir la cadencia de observacion."""
    if not market_ids:
        return {}

    cursor.execute(
        """
        SELECT market_id,
               bool_or(has_prices)                  AS has_shown_prices,
               bool_or(has_liquidity)               AS has_shown_liquidity,
               max(observed_at)                     AS last_observed_at,
               array_agg(DISTINCT snapshot_label)   AS labels
        FROM market_observation
        WHERE market_id = ANY(%s)
        GROUP BY market_id
        """,
        (market_ids,),
    )
    states: dict[str, MarketState] = {}
    for row in cursor.fetchall():
        states[row["market_id"]] = MarketState(
            market_id=row["market_id"],
            has_shown_prices=bool(row["has_shown_prices"]),
            has_shown_liquidity=bool(row["has_shown_liquidity"]),
            last_observed_at=row["last_observed_at"],
            captured_labels=frozenset(row["labels"] or []),
        )
    return states


def runners_for_identity(cursor: psycopg.Cursor) -> list[dict[str, Any]]:
    """Runners con su competicion, para emparejar identidades. Solo lectura."""
    cursor.execute(
        """
        SELECT r.market_id, r.selection_id, r.runner_name, r.player_id,
               e.competition_name, m.market_start_time
        FROM betfair_runner r
        JOIN betfair_market m USING (market_id)
        JOIN betfair_event e USING (event_id)
        ORDER BY r.market_id, r.selection_id
        """
    )
    return cursor.fetchall()


def set_runner_player_id(
    cursor: psycopg.Cursor, market_id: str, selection_id: int, player_id: str
) -> bool:
    """Rellena `player_id` SOLO si esta vacio. Nunca sobrescribe una identidad ya fijada."""
    cursor.execute(
        """
        UPDATE betfair_runner SET player_id = %s
        WHERE market_id = %s AND selection_id = %s AND player_id IS NULL
        """,
        (player_id, market_id, selection_id),
    )
    return cursor.rowcount == 1


def production_model(cursor: psycopg.Cursor) -> dict[str, Any] | None:
    """El modelo registrado en `production` (como maximo uno, lo garantiza un indice unico)."""
    cursor.execute(
        """
        SELECT model_id, feature_hash, artifact_sha256, artifact_path, promoted_at, promoted_by
        FROM model_version WHERE slot = 'production'
        """
    )
    return cursor.fetchone()


def promote_model(
    cursor: psycopg.Cursor,
    manifest: ModelManifest,
    *,
    artifact_path: str,
    promoted_by: str,
    notes: str | None = None,
) -> str | None:
    """Registra `manifest` como `production`; el anterior pasa a `retired`.

    Es la PROMOCION MANUAL de DEC-013: `promoted_by` y `promoted_at` quedan guardados
    (`CHECK promotion_requires_who`). Devuelve el `model_id` retirado, o None si no habia.
    Debe ejecutarse en una transaccion: retira y promueve juntos o nada.
    """
    previous = production_model(cursor)
    if previous is not None and previous["model_id"] == manifest.model_id:
        raise ValueError(f"{manifest.model_id} ya esta en production")
    if previous is not None:
        cursor.execute(
            "UPDATE model_version SET slot = 'retired' WHERE model_id = %s",
            (previous["model_id"],),
        )
    cursor.execute(
        """
        INSERT INTO model_version (
            model_id, model_type, slot, trained_at, train_first_year, train_last_year,
            n_train_rows, feature_names, feature_hash, artifact_sha256, artifact_path,
            hyperparameters, validation_metrics, promoted_at, promoted_by, notes
        ) VALUES (
            %(model_id)s, %(model_type)s, 'production', %(trained_at)s::timestamptz,
            %(first)s, %(last)s, %(rows)s, %(features)s, %(feature_hash)s, %(sha)s, %(path)s,
            %(hyper)s, %(metrics)s, now(), %(by)s, %(notes)s
        )
        ON CONFLICT (model_id) DO UPDATE SET
            slot = 'production', promoted_at = now(), promoted_by = EXCLUDED.promoted_by,
            artifact_path = EXCLUDED.artifact_path
        """,
        {
            "model_id": manifest.model_id,
            "model_type": manifest.model_type,
            "trained_at": manifest.trained_at,
            "first": manifest.train_first_year,
            "last": manifest.train_last_year,
            "rows": manifest.n_train_rows,
            "features": list(manifest.feature_names),
            "feature_hash": manifest.feature_hash,
            "sha": manifest.artifact_sha256,
            "path": artifact_path,
            "hyper": Jsonb(manifest.hyperparameters),
            "metrics": Jsonb(manifest.validation_metrics),
            "by": promoted_by,
            "notes": notes,
        },
    )
    return None if previous is None else previous["model_id"]


def markets_for_prediction(
    cursor: psycopg.Cursor,
    *,
    now: datetime,
    horizon_hours: float,
    max_observation_age_hours: float = 6.0,
) -> list[dict[str, Any]]:
    """Mercados MATCH_ODDS que aun no han empezado, un registro por corredor. Solo lectura.

    Cada registro lleva la ultima observacion prematch con precios de su mercado
    (`observation_id` NULL si no hay ninguna reciente: el servicio la cuenta como excluida).
    """
    cursor.execute(
        """
        SELECT m.market_id, m.market_start_time, e.competition_name,
               r.selection_id, r.runner_name, r.sort_priority, r.player_id,
               o.observation_id, o.observed_at, o.minutes_to_start
        FROM betfair_market m
        JOIN betfair_event e USING (event_id)
        JOIN betfair_runner r USING (market_id)
        LEFT JOIN LATERAL (
            SELECT observation_id, observed_at, minutes_to_start
            FROM market_observation obs
            WHERE obs.market_id = m.market_id
              AND obs.observed_at BETWEEN %(since)s AND %(now)s
              AND NOT obs.inplay AND obs.market_status = 'OPEN' AND obs.has_prices
            ORDER BY obs.observed_at DESC
            LIMIT 1
        ) o ON true
        WHERE m.market_type = 'MATCH_ODDS' AND m.settled_at IS NULL
          AND m.market_start_time > %(now)s AND m.market_start_time <= %(until)s
        ORDER BY m.market_id, r.selection_id
        """,
        {
            "now": now,
            "until": now + timedelta(hours=horizon_hours),
            "since": now - timedelta(hours=max_observation_age_hours),
        },
    )
    return cursor.fetchall()


def insert_prediction(cursor: psycopg.Cursor, row: PredictionRow) -> bool:
    """Inserta una prediccion. Idempotente por (modelo, observacion, corredor)."""
    cursor.execute(
        """
        INSERT INTO prediction (
            model_id, observation_id, observed_at, market_id, selection_id,
            probability, feature_hash, features
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (model_id, observation_id, selection_id) DO NOTHING
        """,
        (
            row.model_id,
            row.observation_id,
            row.observed_at,
            row.market_id,
            row.selection_id,
            row.probability,
            row.feature_hash,
            Jsonb(row.features),
        ),
    )
    return cursor.rowcount == 1


def closing_observations(
    cursor: psycopg.Cursor, *, started_before: datetime | None = None
) -> list[dict[str, Any]]:
    """Cierre de cada mercado segun DEC-021 (vista `market_closing_observation`).

    Solo lectura. `started_before` limita a mercados cuyo inicio publicado es
    anterior a ese instante; sin el, los mercados que aun no han empezado
    aparecen como «no fiables» sin serlo.
    """
    sql = """
        SELECT v.*, m.market_start_time
        FROM market_closing_observation v
        JOIN betfair_market m USING (market_id)
    """
    params: tuple[Any, ...] = ()
    if started_before is not None:
        sql += " WHERE m.market_start_time < %s"
        params = (started_before,)
    cursor.execute(sql + " ORDER BY v.market_id", params)
    return cursor.fetchall()


def closing_summary(
    cursor: psycopg.Cursor, *, started_before: datetime | None = None
) -> dict[str, int]:
    """Recuento de mercados con cierre fiable, no fiable y sin cierre."""
    sql = """
        SELECT count(*)                                           AS markets,
               count(*) FILTER (WHERE close_reliable)             AS reliable,
               count(*) FILTER (WHERE close_reliable IS FALSE)    AS unreliable,
               count(*) FILTER (WHERE closing_observation_id IS NULL) AS no_close
        FROM market_closing_observation v
        JOIN betfair_market m USING (market_id)
    """
    params: tuple[Any, ...] = ()
    if started_before is not None:
        sql += " WHERE m.market_start_time < %s"
        params = (started_before,)
    cursor.execute(sql, params)
    row = cursor.fetchone()
    return {key: int(row[key]) for key in ("markets", "reliable", "unreliable", "no_close")}


def _utc_month_start(moment: datetime) -> date:
    """Primer dia del mes UTC de `moment`. Un datetime sin zona se interpreta como UTC."""
    utc = moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
    return date(utc.year, utc.month, 1)


def _next_month(first_day: date) -> date:
    return date(first_day.year + (first_day.month == 12), first_day.month % 12 + 1, 1)


def ensure_partitions(cursor: psycopg.Cursor, month: datetime) -> list[str]:
    """Garantiza las particiones mensuales del mes UTC de `month` y del SIGUIENTE.

    La politica es UTC (DEC-019): el mes se calcula en UTC aqui y los limites los
    fija `ensure_month_partition` en UTC, sin depender de la zona de la sesion.
    Crear tambien el mes siguiente hace que el cambio de mes no dependa del primer
    ciclo posterior a la medianoche (incidente del 2026-10-01). Idempotente.
    """
    current = _utc_month_start(month)
    created: list[str] = []
    for table in ("market_observation", "runner_price"):
        for first_day in (current, _next_month(current)):
            cursor.execute("SELECT ensure_month_partition(%s, %s) AS name", (table, first_day))
            created.append(cursor.fetchone()["name"])
    return created


def liquidity_emergence(cursor: psycopg.Cursor) -> list[dict[str, Any]]:
    """Curva de aparicion de liquidez respecto a la hora de inicio."""
    cursor.execute("SELECT * FROM liquidity_emergence")
    return cursor.fetchall()


def snapshot_coverage(cursor: psycopg.Cursor) -> list[dict[str, Any]]:
    cursor.execute("SELECT * FROM snapshot_coverage ORDER BY snapshot_label")
    return cursor.fetchall()
