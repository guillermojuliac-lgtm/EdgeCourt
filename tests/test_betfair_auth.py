"""Tests de la autenticacion. Sin red y sin credenciales reales.

Las credenciales de los tests son valores ficticios generados aqui mismo; no
existe ningun secreto en el repositorio.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from edgecourt.market import auth

FAKE_TOKEN = "token-de-prueba-no-real-0123456789"


@pytest.fixture
def credentialed(settings_factory, tmp_path):
    """Settings con credenciales ficticias y ficheros de certificado vacios."""
    cert = tmp_path / "client.crt"
    key = tmp_path / "client.key"
    cert.write_text("no-es-un-certificado-real")
    key.write_text("no-es-una-clave-real")
    return settings_factory(
        betfair_username="usuario-ficticio",
        betfair_password="contrasena-ficticia-de-prueba",
        betfair_app_key="app-key-ficticia",
        betfair_cert_path=cert,
        betfair_key_path=key,
    )


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# --- Validacion de credenciales ----------------------------------------------


@pytest.mark.critical
def test_missing_credentials_fail_clearly_and_name_the_variables(settings_factory):
    """Sin credenciales debe fallar rapido, diciendo QUE falta y sin revelar nada."""
    with pytest.raises(auth.MissingCredentialsError) as excinfo:
        auth.login(settings_factory())

    message = str(excinfo.value)
    assert "BETFAIR_USERNAME" in message
    assert "BETFAIR_APP_KEY" in message
    assert "BETFAIR_CERT_PATH" in message


def test_missing_certificate_file_is_detected(credentialed, tmp_path):
    credentialed.betfair_cert_path.unlink()
    with pytest.raises(auth.MissingCredentialsError, match="BETFAIR_CERT_PATH"):
        auth.login(credentialed)


# --- Login --------------------------------------------------------------------


def test_successful_login_returns_a_session(credentialed):
    def handler(request):
        assert request.url.host == "identitysso-cert.betfair.com"
        return httpx.Response(200, json={"loginStatus": "SUCCESS", "sessionToken": FAKE_TOKEN})

    session = auth.login(credentialed, client=_client(handler))
    assert session.token == FAKE_TOKEN
    assert session.app_key == "app-key-ficticia"
    assert not session.is_expired


@pytest.mark.parametrize(
    "status", ["INVALID_USERNAME_OR_PASSWORD", "ACCOUNT_NOW_LOCKED", "CERT_AUTH_REQUIRED"]
)
def test_rejected_login_raises(credentialed, status):
    handler = lambda request: httpx.Response(200, json={"loginStatus": status})  # noqa: E731
    with pytest.raises(auth.AuthenticationError, match=status):
        auth.login(credentialed, client=_client(handler))


def test_login_without_token_raises(credentialed):
    handler = lambda request: httpx.Response(200, json={"loginStatus": "SUCCESS"})  # noqa: E731
    with pytest.raises(auth.AuthenticationError, match="token"):
        auth.login(credentialed, client=_client(handler))


def test_network_failure_raises_authentication_error(credentialed):
    def handler(request):
        raise httpx.ConnectError("sin red")

    with pytest.raises(auth.AuthenticationError, match="contactar"):
        auth.login(credentialed, client=_client(handler))


# --- Secretos -----------------------------------------------------------------


@pytest.mark.critical
def test_session_never_reveals_the_token_in_its_representation():
    session = auth.Session(
        token=FAKE_TOKEN,
        app_key="app-key-ficticia",
        created_at=datetime.now(UTC),
        last_keep_alive=datetime.now(UTC),
    )
    assert FAKE_TOKEN not in repr(session)
    assert "app-key-ficticia" not in repr(session)


@pytest.mark.critical
def test_login_errors_do_not_leak_the_password(credentialed):
    """Un fallo de login no puede acabar exponiendo la contrasena en el mensaje."""
    handler = lambda r: httpx.Response(200, json={"loginStatus": "INVALID_USERNAME_OR_PASSWORD"})  # noqa: E731
    with pytest.raises(auth.AuthenticationError) as excinfo:
        auth.login(credentialed, client=_client(handler))
    assert "contrasena-ficticia-de-prueba" not in str(excinfo.value)


@pytest.mark.critical
def test_secret_values_include_the_session_token(credentialed):
    """El token debe entrar en el filtro de redaccion de logs en cuanto existe."""
    handler = lambda r: httpx.Response(  # noqa: E731
        200, json={"loginStatus": "SUCCESS", "sessionToken": FAKE_TOKEN}
    )
    manager = auth.SessionManager(credentialed, client_factory=lambda: _client(handler))
    manager.current()
    secrets = manager.secret_values()
    assert FAKE_TOKEN in secrets
    assert "contrasena-ficticia-de-prueba" in secrets


# --- Ciclo de vida de la sesion ----------------------------------------------


def test_session_needs_keep_alive_after_the_interval():
    old = datetime.now(UTC) - auth.KEEP_ALIVE_INTERVAL - timedelta(minutes=1)
    session = auth.Session(FAKE_TOKEN, "k", created_at=old, last_keep_alive=old)
    assert session.needs_keep_alive


def test_session_expires_after_max_age():
    old = datetime.now(UTC) - auth.SESSION_MAX_AGE - timedelta(minutes=1)
    session = auth.Session(FAKE_TOKEN, "k", created_at=old, last_keep_alive=datetime.now(UTC))
    assert session.is_expired


def test_manager_reuses_a_valid_session(credentialed):
    logins = []

    def handler(request):
        if "certlogin" in str(request.url):
            logins.append(1)
            return httpx.Response(200, json={"loginStatus": "SUCCESS", "sessionToken": FAKE_TOKEN})
        return httpx.Response(200, json={"status": "SUCCESS"})

    manager = auth.SessionManager(credentialed, client_factory=lambda: _client(handler))
    manager.current()
    manager.current()
    manager.current()
    assert len(logins) == 1


def test_manager_reauthenticates_when_session_is_invalidated(credentialed):
    logins = []

    def handler(request):
        if "certlogin" in str(request.url):
            logins.append(1)
            return httpx.Response(200, json={"loginStatus": "SUCCESS", "sessionToken": FAKE_TOKEN})
        return httpx.Response(200, json={"status": "SUCCESS"})

    manager = auth.SessionManager(credentialed, client_factory=lambda: _client(handler))
    manager.current()
    manager.invalidate()
    manager.current()
    assert len(logins) == 2


def test_keep_alive_failure_triggers_relogin(credentialed):
    logins = []

    def handler(request):
        if "certlogin" in str(request.url):
            logins.append(1)
            return httpx.Response(200, json={"loginStatus": "SUCCESS", "sessionToken": FAKE_TOKEN})
        return httpx.Response(200, json={"status": "FAIL", "error": "NO_SESSION"})

    manager = auth.SessionManager(credentialed, client_factory=lambda: _client(handler))
    session = manager.current()
    # Se fuerza que toque keep-alive.
    session.last_keep_alive = datetime.now(UTC) - auth.KEEP_ALIVE_INTERVAL - timedelta(minutes=1)
    manager.current()
    assert len(logins) == 2


def test_close_logs_out_and_is_idempotent(credentialed):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if "certlogin" in str(request.url):
            return httpx.Response(200, json={"loginStatus": "SUCCESS", "sessionToken": FAKE_TOKEN})
        return httpx.Response(200, json={"status": "SUCCESS"})

    manager = auth.SessionManager(credentialed, client_factory=lambda: _client(handler))
    manager.current()
    manager.close()
    manager.close()
    assert any("logout" in url for url in calls)


def test_backoff_is_exponential_and_capped(monkeypatch):
    waited = []
    monkeypatch.setattr(auth.time, "sleep", waited.append)

    delays = [auth.wait_with_backoff(attempt) for attempt in range(1, 10)]
    assert delays[:4] == [2.0, 4.0, 8.0, 16.0]
    assert max(delays) <= 300.0
    assert delays == sorted(delays)


@pytest.mark.critical
def test_manager_reports_missing_credentials_not_a_file_error(settings_factory):
    """Sin configurar, el error debe nombrar las variables que faltan.

    Regresion: el contexto TLS se construia antes de validar, de modo que un
    `BETFAIR_CERT_PATH` vacio producia un FileNotFoundError opaco en vez de un
    mensaje accionable.
    """
    manager = auth.SessionManager(settings_factory())
    with pytest.raises(auth.MissingCredentialsError) as excinfo:
        manager.current()
    assert "BETFAIR_CERT_PATH" in str(excinfo.value)
