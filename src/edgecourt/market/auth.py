"""Autenticacion contra Betfair. SOLO LECTURA.

Este modulo obtiene y mantiene vivo un token de sesion. No contiene ninguna
operacion de apuesta, y el paquete `market` en su conjunto tampoco: ver
`tests/test_no_real_betting_surface.py`, que escanea el arbol y falla si
aparece cualquier endpoint de ejecucion.

**Decision D7 (resuelta en PHASE 8): se usa `httpx` directo y no
`betfairlightweight`.** El motivo no es el peso de la dependencia sino la
seguridad: esa libreria agrupa las operaciones de colocacion y cancelacion de
ordenes en el mismo objeto cliente que las de consulta, con lo que la capacidad
de enviar apuestas reales quedaria a un `import` de distancia dentro del
proceso. Escribiendo las tres llamadas de lectura que necesitamos, el codigo
capaz de apostar sencillamente no existe, y eso es verificable de forma
automatica (ver los tests de barrera).

Nota deliberada: este modulo evita incluso *escribir* los nombres de esas
operaciones, para que el test que escanea el arbol pueda ser estricto y no
necesite listas de excepciones.

**Los secretos nunca se escriben en disco ni en logs.** Llegan por variables de
entorno, viven en memoria el tiempo de la peticion y el token de sesion se
registra en el filtro de redaccion en cuanto se obtiene.
"""

from __future__ import annotations

import ssl
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from edgecourt.config import Settings
from edgecourt.logging_setup import get_logger

log = get_logger("collector.auth")

# El servicio de identidad de Betfair esta segmentado por jurisdiccion. Una
# cuenta espanola que se autentique contra el endpoint global recibe
# `AUTHORIZED_ONLY_FOR_DOMAIN_ES`; lo mismo ocurre con Italia y Rumania.
# Endpoints verificados en la documentacion oficial (Non-Interactive bot login).
#
# La Betting API (api.betfair.com/exchange/betting) NO se deriva de aqui: la
# documentacion no indica que cambie por jurisdiccion, asi que se deja como
# estaba y se comprueba empiricamente en el healthcheck.
JURISDICTIONS: tuple[str, ...] = ("com", "es", "it", "ro", "com.au")


def cert_login_url(jurisdiction: str = "com") -> str:
    """Endpoint de login por certificado de la jurisdiccion indicada."""
    if jurisdiction not in JURISDICTIONS:
        raise ValueError(
            f"Jurisdiccion no soportada: {jurisdiction!r}. Validas: {', '.join(JURISDICTIONS)}"
        )
    return f"https://identitysso-cert.betfair.{jurisdiction}/api/certlogin"


def identity_url(operation: str, jurisdiction: str = "com") -> str:
    """Endpoint de keepAlive o logout de la jurisdiccion indicada."""
    if jurisdiction not in JURISDICTIONS:
        raise ValueError(f"Jurisdiccion no soportada: {jurisdiction!r}")
    return f"https://identitysso.betfair.{jurisdiction}/api/{operation}"


# Betfair invalida la sesion tras 4 horas de inactividad para la API de apuestas.
# Se renueva con bastante antelacion para no depender de la frontera exacta.
KEEP_ALIVE_INTERVAL = timedelta(hours=1)
SESSION_MAX_AGE = timedelta(hours=8)

REQUEST_TIMEOUT_SECONDS = 30.0


class AuthenticationError(RuntimeError):
    """La autenticacion ha fallado de forma no recuperable."""


class MissingCredentialsError(AuthenticationError):
    """Faltan credenciales o el certificado. No es un fallo de red."""


@dataclass(slots=True)
class Session:
    """Token de sesion vivo. El token nunca se imprime ni se serializa."""

    token: str
    app_key: str
    created_at: datetime
    last_keep_alive: datetime
    jurisdiction: str = "com"

    @property
    def age(self) -> timedelta:
        return datetime.now(UTC) - self.created_at

    @property
    def needs_keep_alive(self) -> bool:
        return datetime.now(UTC) - self.last_keep_alive >= KEEP_ALIVE_INTERVAL

    @property
    def is_expired(self) -> bool:
        return self.age >= SESSION_MAX_AGE

    def headers(self) -> dict[str, str]:
        return {
            "X-Application": self.app_key,
            "X-Authentication": self.token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"Session(app_key=***, token=***, age={self.age})"


def _validate_credentials(settings: Settings) -> None:
    """Comprueba que todo lo necesario esta presente, sin revelar ningun valor."""
    missing: list[str] = []
    if not settings.betfair_username:
        missing.append("BETFAIR_USERNAME")
    if not settings.betfair_password:
        missing.append("BETFAIR_PASSWORD")
    if not settings.betfair_app_key:
        missing.append("BETFAIR_APP_KEY")
    if settings.betfair_cert_path is None:
        missing.append("BETFAIR_CERT_PATH")
    if settings.betfair_key_path is None:
        missing.append("BETFAIR_KEY_PATH")
    if missing:
        raise MissingCredentialsError(
            "Faltan variables de entorno para autenticarse en Betfair: "
            + ", ".join(missing)
            + ". Ver README (seccion Betfair) y .env.example."
        )

    for label, path in (
        ("BETFAIR_CERT_PATH", settings.betfair_cert_path),
        ("BETFAIR_KEY_PATH", settings.betfair_key_path),
    ):
        if path is not None and not Path(path).is_file():
            raise MissingCredentialsError(f"{label} apunta a un fichero que no existe: {path}")


def build_ssl_context(settings: Settings) -> ssl.SSLContext:
    """Contexto TLS con el certificado de cliente que exige el login no interactivo."""
    context = ssl.create_default_context()
    context.load_cert_chain(
        certfile=str(settings.betfair_cert_path), keyfile=str(settings.betfair_key_path)
    )
    return context


def login(settings: Settings, *, client: httpx.Client | None = None) -> Session:
    """Inicia sesion por certificado (modo no interactivo, apto para 24/7).

    Devuelve una `Session`. Las credenciales no se almacenan en ningun sitio: se
    envian una vez y se descartan.
    """
    _validate_credentials(settings)

    owns_client = client is None
    if client is None:
        client = httpx.Client(verify=build_ssl_context(settings), timeout=REQUEST_TIMEOUT_SECONDS)

    try:
        login_url = cert_login_url(settings.betfair_jurisdiction)
        response = client.post(
            login_url,
            data={
                "username": settings.betfair_username,
                "password": settings.betfair_password,
            },
            headers={
                "X-Application": settings.betfair_app_key,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as exc:
        raise AuthenticationError(f"No se pudo contactar con el login de Betfair: {exc}") from exc
    finally:
        if owns_client:
            client.close()

    status = payload.get("loginStatus")
    if status != "SUCCESS":
        # `loginStatus` es un codigo de Betfair, nunca contiene credenciales.
        message = f"Login rechazado por Betfair: {status}"
        if isinstance(status, str) and status.startswith("AUTHORIZED_ONLY_FOR_DOMAIN_"):
            domain = status.rsplit("_", 1)[-1].lower()
            message += (
                f". La cuenta pertenece a la jurisdiccion '{domain}': "
                f"define BETFAIR_JURISDICTION={domain} en tu .env"
            )
        raise AuthenticationError(message)

    token = payload.get("sessionToken")
    if not token:
        raise AuthenticationError("Betfair no devolvio token de sesion")

    now = datetime.now(UTC)
    log.info(
        "sesion iniciada",
        extra={"login_status": status, "jurisdiction": settings.betfair_jurisdiction},
    )
    return Session(
        token=token,
        app_key=settings.betfair_app_key,
        created_at=now,
        last_keep_alive=now,
        jurisdiction=settings.betfair_jurisdiction,
    )


def keep_alive(session: Session, *, client: httpx.Client) -> bool:
    """Renueva la sesion. Devuelve False si Betfair ya no la reconoce."""
    try:
        response = client.post(
            identity_url("keepAlive", session.jurisdiction), headers=session.headers()
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as exc:
        log.warning("keep-alive fallido", extra={"error": str(exc)})
        return False

    if payload.get("status") != "SUCCESS":
        log.warning("keep-alive rechazado", extra={"status": payload.get("status")})
        return False

    session.last_keep_alive = datetime.now(UTC)
    return True


def logout(session: Session, *, client: httpx.Client) -> None:
    """Cierra la sesion. Se llama siempre al apagar, tambien ante SIGTERM."""
    try:
        client.post(identity_url("logout", session.jurisdiction), headers=session.headers())
        log.info("sesion cerrada")
    except httpx.HTTPError as exc:  # pragma: no cover - best effort
        log.warning("logout fallido", extra={"error": str(exc)})


class SessionManager:
    """Mantiene una sesion valida, renovandola o rehaciendola segun haga falta."""

    def __init__(self, settings: Settings, *, client_factory=None) -> None:
        self._settings = settings
        self._session: Session | None = None
        self._lock = threading.Lock()
        self._client_factory = client_factory or self._default_client_factory
        self._client: httpx.Client | None = None

    def _default_client_factory(self) -> httpx.Client:
        # Se validan las credenciales ANTES de construir el contexto TLS: con
        # `BETFAIR_CERT_PATH` sin definir, `load_cert_chain` lanzaria un
        # FileNotFoundError opaco en lugar de decir que falta la variable.
        _validate_credentials(self._settings)
        return httpx.Client(
            verify=build_ssl_context(self._settings), timeout=REQUEST_TIMEOUT_SECONDS
        )

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    def current(self) -> Session:
        """Devuelve una sesion valida, autenticando o renovando si es necesario."""
        with self._lock:
            if self._session is None or self._session.is_expired:
                self._session = login(self._settings, client=self.client)
                return self._session

            if self._session.needs_keep_alive and not keep_alive(self._session, client=self.client):
                log.info("la sesion ya no es valida, reautenticando")
                self._session = login(self._settings, client=self.client)
            return self._session

    def invalidate(self) -> None:
        """Descarta la sesion actual para forzar una reautenticacion.

        La llama el cliente cuando Betfair responde que la sesion ya no vale, en
        lugar de reintentar indefinidamente con un token muerto.
        """
        with self._lock:
            self._session = None

    def close(self) -> None:
        """Cierra sesion y conexiones. Idempotente."""
        with self._lock:
            if self._session is not None and self._client is not None:
                logout(self._session, client=self._client)
            self._session = None
            if self._client is not None:
                self._client.close()
                self._client = None

    def secret_values(self) -> frozenset[str]:
        """Valores que el filtro de logs debe redactar mientras la sesion viva."""
        values = {self._settings.betfair_app_key, self._settings.betfair_password}
        if self._session is not None:
            values.add(self._session.token)
        return frozenset(v for v in values if v)


def wait_with_backoff(attempt: int, *, base: float = 2.0, cap: float = 300.0) -> float:
    """Espera exponencial acotada. Devuelve los segundos esperados."""
    delay = min(base * (2 ** max(attempt - 1, 0)), cap)
    time.sleep(delay)
    return delay
