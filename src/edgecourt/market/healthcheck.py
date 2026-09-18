"""Verificacion de credenciales y acceso de lectura a Betfair.

Comprueba, en orden y parandose en el primer fallo:

1. que la configuracion esta completa y los ficheros existen;
2. que el par clave/certificado es coherente y cumple los requisitos;
3. que el login por certificado funciona;
4. que se puede leer el catalogo de mercados de tenis.

**No escribe nada en disco** -ni un solo snapshot- y cierra la sesion al
terminar. Esta pensado para ejecutarse antes de arrancar el collector por
primera vez, y despues cada vez que se toque la configuracion.

Ningun valor sensible se imprime jamas: de la app key se muestra solo su
longitud, y del token de sesion nada en absoluto.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from edgecourt.config import Settings
from edgecourt.logging_setup import get_logger
from edgecourt.market.auth import (
    AuthenticationError,
    MissingCredentialsError,
    SessionManager,
    _validate_credentials,
    cert_login_url,
)
from edgecourt.market.client import MarketFilter, ReadOnlyBettingClient

log = get_logger("collector.healthcheck")

# Margen con el que se avisa de que el certificado esta por caducar.
CERT_EXPIRY_WARNING = timedelta(days=30)


@dataclass(slots=True)
class Check:
    """Resultado de una comprobacion individual."""

    name: str
    ok: bool
    detail: str = ""

    @property
    def symbol(self) -> str:
        return "OK" if self.ok else "FALLO"


@dataclass(slots=True)
class HealthReport:
    checks: list[Check] = field(default_factory=list)
    markets_found: int = 0
    events_found: int = 0
    sample: list[str] = field(default_factory=list)
    login_endpoint: str = ""
    betting_endpoint: str = ""

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    def add(self, name: str, ok: bool, detail: str = "") -> Check:
        check = Check(name=name, ok=ok, detail=detail)
        self.checks.append(check)
        return check


def _check_configuration(settings: Settings, report: HealthReport) -> bool:
    try:
        _validate_credentials(settings)
    except MissingCredentialsError as exc:
        report.add("Configuracion", False, str(exc))
        return False

    report.add(
        "Configuracion",
        True,
        f"usuario definido, app key de {len(settings.betfair_app_key)} caracteres",
    )
    return True


def _openssl(args: list[str]) -> str | None:
    """Ejecuta openssl y devuelve su salida, o None si no esta disponible.

    Se usa `openssl` en lugar de anadir la dependencia `cryptography`: ya esta en
    cualquier Ubuntu, es el mismo binario con el que se genero el certificado y
    evita traer un paquete de tamano considerable solo para una comprobacion
    previa. Si faltase, la verificacion se omite y el login la hara de todos
    modos -con peor mensaje de error, pero sin dejar pasar nada malo-.
    """
    try:
        result = subprocess.run(  # noqa: S603 - argumentos fijos, sin shell
            ["openssl", *args],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def _modulus_digest(args: list[str]) -> str | None:
    """Huella del modulo RSA. Forma parte de la clave PUBLICA: no es un secreto."""
    output = _openssl(args)
    if output is None or "Modulus=" not in output:
        return None
    modulus = output.split("Modulus=", 1)[1].strip()
    return hashlib.sha256(modulus.encode("ascii", errors="ignore")).hexdigest()[:16]


def _check_certificate(settings: Settings, report: HealthReport) -> bool:
    """Comprueba el par clave/certificado sin exponer material sensible."""
    cert_path = Path(settings.betfair_cert_path)
    key_path = Path(settings.betfair_key_path)

    # Permisos: una clave privada legible por otros es un problema real.
    if key_path.stat().st_mode & 0o077:
        report.add(
            "Permisos de la clave",
            False,
            f"la clave es accesible por otros usuarios. Corrige con: chmod 600 {key_path}",
        )
        return False
    report.add("Permisos de la clave", True, "solo accesible por su propietario")

    # Una clave con passphrase no sirve para un proceso desatendido.
    head = key_path.read_text(encoding="utf-8", errors="ignore")[:200]
    if "ENCRYPTED" in head:
        report.add(
            "Clave sin passphrase",
            False,
            "la clave privada esta cifrada; un proceso 24/7 no puede teclear la contrasena",
        )
        return False
    report.add("Clave sin passphrase", True, "apta para ejecucion desatendida")

    key_digest = _modulus_digest(["rsa", "-in", str(key_path), "-noout", "-modulus"])
    cert_digest = _modulus_digest(["x509", "-in", str(cert_path), "-noout", "-modulus"])

    if key_digest is None or cert_digest is None:
        report.add(
            "Par clave/certificado",
            True,
            "no se pudo verificar (openssl no disponible); se comprobara en el login",
        )
        return True

    if key_digest != cert_digest:
        report.add(
            "Par clave/certificado",
            False,
            "el certificado no corresponde a esa clave privada: revisa las rutas",
        )
        return False
    report.add("Par clave/certificado", True, "coinciden")

    key_text = _openssl(["rsa", "-in", str(key_path), "-noout", "-text"]) or ""
    match = re.search(r"Private-Key:\s*\((\d+) bit", key_text)
    if match:
        bits = int(match.group(1))
        if bits < 2048:
            report.add("Tamano de clave", False, f"{bits} bits; Betfair exige RSA de 2048")
            return False
        report.add("Tamano de clave", True, f"RSA de {bits} bits")

    dates = _openssl(["x509", "-in", str(cert_path), "-noout", "-enddate"]) or ""
    expiry = _parse_openssl_date(dates)
    if expiry is not None:
        now = datetime.now(UTC)
        if expiry < now:
            report.add("Vigencia", False, f"el certificado caduco el {expiry.date()}")
            return False
        remaining = expiry - now
        if remaining < CERT_EXPIRY_WARNING:
            report.add("Vigencia", True, f"CADUCA PRONTO: {expiry.date()}")
        else:
            report.add("Vigencia", True, f"valido hasta {expiry.date()} ({remaining.days} dias)")

    return True


def _parse_openssl_date(output: str) -> datetime | None:
    """Convierte `notAfter=Sep 15 07:52:14 2036 GMT` en un datetime."""
    if "notAfter=" not in output:
        return None
    raw = output.split("notAfter=", 1)[1].strip()
    for fmt in ("%b %d %H:%M:%S %Y %Z", "%b %d %H:%M:%S %Y"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def run_healthcheck(settings: Settings, *, sample_size: int = 5) -> HealthReport:
    """Ejecuta la verificacion completa. No escribe nada en disco."""
    report = HealthReport()

    if not _check_configuration(settings, report):
        return report
    if not _check_certificate(settings, report):
        return report

    from edgecourt.market.client import BETTING_API_BASE

    report.login_endpoint = cert_login_url(settings.betfair_jurisdiction)
    report.betting_endpoint = BETTING_API_BASE
    report.add(
        "Jurisdiccion",
        True,
        f"'{settings.betfair_jurisdiction}' -> {report.login_endpoint}",
    )

    sessions = SessionManager(settings)
    try:
        try:
            sessions.current()
        except (AuthenticationError, OSError) as exc:
            report.add("Login por certificado", False, str(exc))
            return report
        report.add("Login por certificado", True, "sesion obtenida")

        client = ReadOnlyBettingClient(sessions)
        market_filter = MarketFilter()

        try:
            events = client.list_events(market_filter)
        except Exception as exc:  # noqa: BLE001 - se reporta, no se propaga
            report.add("Lectura de eventos", False, str(exc))
            return report
        report.events_found = len(events)
        report.add("Lectura de eventos", True, f"{len(events)} eventos de tenis visibles")

        try:
            catalogues = client.list_market_catalogue(market_filter, max_results=sample_size)
        except Exception as exc:  # noqa: BLE001
            report.add("Lectura de mercados", False, str(exc))
            return report
        report.markets_found = len(catalogues)
        report.add("Lectura de mercados", True, f"{len(catalogues)} mercados Match Odds")

        market_ids = [c["marketId"] for c in catalogues if c.get("marketId")]
        if market_ids:
            try:
                books = client.list_market_book(market_ids[:sample_size])
            except Exception as exc:  # noqa: BLE001
                report.add("Lectura de precios", False, str(exc))
                return report
            report.add("Lectura de precios", True, f"{len(books)} libros de precios")

        report.sample = [
            f"{(c.get('event') or {}).get('name', '?')}  —  inicio {c.get('marketStartTime', '?')}"
            for c in catalogues[:sample_size]
        ]
    finally:
        sessions.close()

    log.info(
        "healthcheck completado",
        extra={"ok": report.ok, "events": report.events_found, "markets": report.markets_found},
    )
    return report
