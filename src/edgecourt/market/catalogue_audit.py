"""Auditoria del catalogo de tenis de Betfair (Phase 3.5-C2). SOLO LECTURA.

Proceso corto e independiente del collector: login -> catalogo -> libros ->
escritura atomica -> fin. No permanece residente, no comparte sesion con el
collector y **nunca abre PostgreSQL**: el cruce con `betfair_market` lo hace
despues `catalogue_report`, en modo solo lectura. Por eso este modulo no importa
nada de `edgecourt.db` ni `psycopg` (lo fija un test).

Pregunta que responde: que catalogo de tenis devuelve la sesion espanola
durante los torneos ATP 500/1000 (Pekin, Tokio, Shanghai), con que antelacion
aparecen los mercados y con que calidad de libro.

Almacenamiento (ver docs/investigations/2026-10-spanish-exchange-atp-catalogue.md):

    <out>/
      catalogues/<sha256>.json.gz          catalogo normalizado, direccionado por contenido
      YYYY-MM-DD/HHMM_books.json.gz        libros de ese slot (MATCH_ODDS)
      YYYY-MM-DD/HHMM_run.json             registro de la ejecucion (se escribe el ultimo)

Politica de idempotencia por slot de 30 minutos (UTC): **se conserva el primer
snapshot valido**. Si el slot ya tiene un `run.json` con `status = ok`, la
ejecucion termina sin llamar a Betfair. Un `run.json` con `status = error` no es
una observacion valida: un reintento en el mismo slot lo sustituye de forma
atomica y conserva el historial de intentos fallidos en `previous_attempts`.
Un cerrojo (`flock`) serializa ejecuciones simultaneas.

Nunca se escriben tokens, App Keys, contrasenas, cabeceras ni DSN.
"""

from __future__ import annotations

import contextlib
import fcntl
import gzip
import hashlib
import json
import os
import tempfile
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from edgecourt import __version__
from edgecourt.config import Settings
from edgecourt.logging_setup import get_logger, secrets_from_settings
from edgecourt.market.auth import SessionManager
from edgecourt.market.client import BETTING_API_BASE, MarketFilter, ReadOnlyBettingClient

log = get_logger("catalogue_audit")

SCHEMA_VERSION = 1
SLOT_MINUTES = 30

# Ventana oficial del experimento. Fuera de ella el comando no llama a Betfair,
# aunque el timer de systemd quede activo por error.
EXPERIMENT_START = datetime(2026, 9, 29, 0, 0, tzinfo=UTC)
EXPERIMENT_END = datetime(2026, 10, 19, 0, 0, tzinfo=UTC)

# listMarketCatalogue admite hasta 1000 resultados. Con las proyecciones del
# cliente (peso 0) no hay limite de peso que obligue a trocear.
CATALOGUE_MAX_RESULTS = 1000

STATUS_OK = "ok"
STATUS_ERROR = "error"
MAX_ERROR_LENGTH = 500


# --- Tiempo -------------------------------------------------------------------


def to_utc(moment: datetime) -> datetime:
    """Normaliza a UTC. Un datetime sin zona horaria se rechaza: seria ambiguo."""
    if moment.tzinfo is None:
        raise ValueError("se requiere un datetime con zona horaria")
    return moment.astimezone(UTC)


def slot_for(moment: datetime) -> datetime:
    """Inicio del slot de 30 minutos (UTC) al que pertenece `moment`."""
    utc = to_utc(moment).replace(second=0, microsecond=0)
    return utc - timedelta(minutes=utc.minute % SLOT_MINUTES)


def run_id_for(slot: datetime) -> str:
    return to_utc(slot).strftime("%Y%m%dT%H%MZ")


def iso_utc(moment: datetime) -> str:
    return to_utc(moment).isoformat(timespec="seconds").replace("+00:00", "Z")


# --- Consultas ----------------------------------------------------------------


def broad_filter() -> MarketFilter:
    """Todo el tenis visible: sin tipo de mercado, competicion ni ventana."""
    return MarketFilter(market_type_codes=None)


def match_odds_filter() -> MarketFilter:
    """Solo MATCH_ODDS, sin ventana temporal: define el conjunto de libros a pedir."""
    return MarketFilter()


# --- Normalizacion y hash -----------------------------------------------------


def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def normalise_catalogue(markets: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Orden estable: mercados por `marketId` y runners por `selectionId`."""
    normalised = []
    for market in markets:
        copy = dict(market)
        if isinstance(copy.get("runners"), list):
            copy["runners"] = sorted(copy["runners"], key=lambda r: str(r.get("selectionId")))
        normalised.append(copy)
    return sorted(normalised, key=lambda m: str(m.get("marketId")))


def catalogue_bytes(markets: Iterable[dict[str, Any]]) -> bytes:
    return _canonical(normalise_catalogue(markets))


def catalogue_hash(markets: Iterable[dict[str, Any]]) -> str:
    return hashlib.sha256(catalogue_bytes(markets)).hexdigest()


# --- Rutas y escritura atomica -------------------------------------------------


@dataclass(frozen=True)
class AuditPaths:
    root: Path

    def day_dir(self, slot: datetime) -> Path:
        return self.root / to_utc(slot).strftime("%Y-%m-%d")

    def run_path(self, slot: datetime) -> Path:
        return self.day_dir(slot) / f"{to_utc(slot):%H%M}_run.json"

    def books_path(self, slot: datetime) -> Path:
        return self.day_dir(slot) / f"{to_utc(slot):%H%M}_books.json.gz"

    def catalogue_path(self, digest: str) -> Path:
        return self.root / "catalogues" / f"{digest}.json.gz"

    @property
    def lock_path(self) -> Path:
        return self.root / ".lock"


def _mkdir_private(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        path.chmod(0o700)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Escribe en un temporal del mismo directorio y lo renombra (0600).

    Si el proceso muere a mitad, queda como mucho un `.tmp-*` que los lectores
    ignoran: nunca un fichero final a medias.
    """
    _mkdir_private(path.parent)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise


def gzip_bytes(data: bytes) -> bytes:
    """gzip determinista (mtime = 0): mismo contenido, mismos bytes."""
    return gzip.compress(data, mtime=0)


def load_run(path: Path) -> dict[str, Any] | None:
    """Lee un `run.json`. Ausente o ilegible cuenta como inexistente."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


@contextlib.contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    _mkdir_private(path.parent)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


# --- Sanitizado ---------------------------------------------------------------


def sanitise(message: str, secrets: Iterable[str]) -> str:
    """Elimina cualquier secreto conocido de un mensaje de error y lo acota."""
    clean = message
    for secret in sorted({s for s in secrets if s and len(s) >= 4}, key=len, reverse=True):
        clean = clean.replace(secret, "***")
    return clean[:MAX_ERROR_LENGTH]


# --- Ejecucion ----------------------------------------------------------------


@dataclass
class AuditOutcome:
    """Resultado de una invocacion. `exit_code` es lo que devuelve la CLI."""

    status: str  # ok | error | already_captured | outside_window | dry_run
    slot: datetime | None = None
    run_path: Path | None = None
    run: dict[str, Any] | None = None
    detail: str = ""
    plan: dict[str, Any] = field(default_factory=dict)

    @property
    def exit_code(self) -> int:
        return 1 if self.status == STATUS_ERROR else 0


def _delayed_flag(books: list[dict[str, Any]]) -> bool | str | None:
    flags = {book.get("isMarketDataDelayed") for book in books}
    if not books:
        return None
    if flags == {True}:
        return True
    if flags == {False}:
        return False
    return "mixed"


def _competitions(markets: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for market in markets:
        name = (market.get("competition") or {}).get("name") or "(sin competicion)"
        counts[name] = counts.get(name, 0) + 1
    return dict(sorted(counts.items()))


def _endpoint(jurisdiction: str) -> dict[str, Any]:
    return {
        "identity_host": f"identitysso-cert.betfair.{jurisdiction}",
        "betting_api": BETTING_API_BASE,
        "operations": ["listMarketCatalogue", "listMarketBook"],
    }


def run_audit(
    settings: Settings,
    *,
    out: Path,
    since: datetime = EXPERIMENT_START,
    until: datetime = EXPERIMENT_END,
    dry_run: bool = False,
    clock: Callable[[], datetime] | None = None,
    sessions: SessionManager | None = None,
    client: ReadOnlyBettingClient | None = None,
) -> AuditOutcome:
    """Una ejecucion completa. No lanza por fallos de Betfair: los registra."""
    clock = clock or (lambda: datetime.now(UTC))
    now = to_utc(clock())
    since, until = to_utc(since), to_utc(until)

    if not (since <= now < until):
        # Antes de cualquier llamada y de cualquier escritura.
        return AuditOutcome(
            status="outside_window",
            detail=f"fuera de la ventana [{iso_utc(since)}, {iso_utc(until)}): sin llamadas",
        )

    slot = slot_for(now)
    paths = AuditPaths(out)
    plan = {
        "slot": iso_utc(slot),
        "broad_payload": broad_filter().as_payload(),
        "match_odds_payload": match_odds_filter().as_payload(),
        "run_path": str(paths.run_path(slot)),
    }
    if dry_run:
        return AuditOutcome(status="dry_run", slot=slot, detail="sin llamadas", plan=plan)

    with _exclusive_lock(paths.lock_path):
        existing = load_run(paths.run_path(slot))
        if existing and existing.get("status") == STATUS_OK:
            return AuditOutcome(
                status="already_captured",
                slot=slot,
                run_path=paths.run_path(slot),
                run=existing,
                detail="el slot ya tiene un snapshot valido: se conserva el primero",
            )
        previous_attempts = []
        if existing and existing.get("status") == STATUS_ERROR:
            previous_attempts = list(existing.get("previous_attempts") or [])
            previous_attempts.append(
                {
                    "captured_at_utc": existing.get("captured_at_utc"),
                    "errors": existing.get("errors") or [],
                }
            )
        return _capture(settings, paths, slot, clock, previous_attempts, sessions, client)


def _capture(
    settings: Settings,
    paths: AuditPaths,
    slot: datetime,
    clock: Callable[[], datetime],
    previous_attempts: list[dict[str, Any]],
    sessions: SessionManager | None,
    client: ReadOnlyBettingClient | None,
) -> AuditOutcome:
    captured_at = to_utc(clock())
    started = time.monotonic()
    own_sessions = sessions is None
    sessions = sessions or SessionManager(settings)
    client = client or ReadOnlyBettingClient(sessions)
    secrets = set(secrets_from_settings(settings))

    run: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id_for(slot),
        "scheduled_slot_utc": iso_utc(slot),
        "captured_at_utc": iso_utc(captured_at),
        "jurisdiction": settings.betfair_jurisdiction,
        "endpoint": _endpoint(settings.betfair_jurisdiction),
        "edgecourt_version": __version__,
        "previous_attempts": previous_attempts,
    }

    try:
        broad = client.list_market_catalogue(broad_filter(), max_results=CATALOGUE_MAX_RESULTS)
        match_odds = client.list_market_catalogue(
            match_odds_filter(), max_results=CATALOGUE_MAX_RESULTS
        )
        mo_ids = sorted(str(m["marketId"]) for m in match_odds if m.get("marketId"))
        books = client.list_market_book(mo_ids) if mo_ids else []

        digest = catalogue_hash(broad)
        catalogue_file = paths.catalogue_path(digest)
        if not catalogue_file.exists():
            atomic_write_bytes(catalogue_file, gzip_bytes(catalogue_bytes(broad)))

        books_doc = {
            "schema_version": SCHEMA_VERSION,
            "run_id": run["run_id"],
            "market_ids": mo_ids,
            "books": sorted(books, key=lambda b: str(b.get("marketId"))),
        }
        books_raw = _canonical(books_doc)
        atomic_write_bytes(paths.books_path(slot), gzip_bytes(books_raw))

        run.update(
            {
                "status": STATUS_OK,
                "catalogue_hash": digest,
                "catalogue_truncated": len(broad) >= CATALOGUE_MAX_RESULTS,
                "tennis_market_count": len(broad),
                "match_odds_count": len(mo_ids),
                "competition_count": len(_competitions(broad)),
                "competitions": _competitions(broad),
                "book_market_count": len(books),
                "match_odds_market_ids": mo_ids,
                "books_file": paths.books_path(slot).name,
                "books_sha256": hashlib.sha256(books_raw).hexdigest(),
                "market_data_delayed": _delayed_flag(books),
                "errors": [],
            }
        )
    except Exception as exc:  # noqa: BLE001 - cualquier fallo queda registrado, no se propaga
        # El token de sesion solo existe tras el login: se anade aqui.
        secret_values = getattr(sessions, "secret_values", None)
        if callable(secret_values):
            with contextlib.suppress(Exception):
                secrets |= set(secret_values())
        run.update(
            {
                "status": STATUS_ERROR,
                "catalogue_hash": None,
                "tennis_market_count": None,
                "match_odds_count": None,
                "competition_count": None,
                "book_market_count": None,
                "errors": [sanitise(f"{type(exc).__name__}: {exc}", secrets)],
            }
        )
    finally:
        if own_sessions:
            with contextlib.suppress(Exception):
                sessions.close()

    run["duration_s"] = round(time.monotonic() - started, 3)
    # El run.json es el ultimo en escribirse: marca el slot como completado.
    atomic_write_bytes(paths.run_path(slot), json.dumps(run, indent=1, sort_keys=True).encode())
    log.info(
        "auditoria de catalogo",
        extra={
            "run_id": run["run_id"],
            "status": run["status"],
            "tennis_markets": run.get("tennis_market_count"),
            "match_odds": run.get("match_odds_count"),
        },
    )
    return AuditOutcome(
        status=run["status"],
        slot=slot,
        run_path=paths.run_path(slot),
        run=run,
        detail="; ".join(run.get("errors") or []),
    )
