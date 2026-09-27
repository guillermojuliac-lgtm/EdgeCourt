"""keepAlive preventivo de la sesion de Betfair (Phase 3.5-A).

En el exchange espanol la sesion caduca a los 20 minutos. Con el keepAlive a
1 hora, la sesion caducaba siempre antes y cada 20 minutos habia que
reautenticar tras un INVALID_SESSION_INFORMATION. Estos tests fijan que:

* la sesion espanola se renueva antes de los 20 minutos;
* no se renueva en cada llamada;
* un keepAlive correcto conserva la sesion (sin login nuevo);
* un keepAlive fallido no tumba el collector y la reautenticacion sigue siendo
  la segunda barrera;
* INVALID_SESSION_INFORMATION sigue provocando la recuperacion;
* nada de esto toca endpoints que no sean de identidad o de lectura.

Sin red y sin credenciales reales.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from edgecourt.market import auth
from edgecourt.market.client import MarketFilter, ReadOnlyBettingClient

FAKE_TOKEN = "token-de-prueba-no-real-0123456789"
SPANISH_TIMEOUT = timedelta(minutes=20)


@pytest.fixture
def spanish_settings(settings_factory, tmp_path):
    """Settings de una cuenta espanola con credenciales ficticias."""
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
        betfair_jurisdiction="es",
    )


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(auth.time, "sleep", lambda _s: None)


class _Clock:
    """Reloj controlable para simular el paso del tiempo sin esperar."""

    def __init__(self) -> None:
        self.now = datetime(2026, 9, 27, 10, 0, tzinfo=UTC)

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture
def clock(monkeypatch):
    fake = _Clock()

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fake.now

    monkeypatch.setattr(auth, "datetime", _FakeDatetime)
    return fake


class _Betfair:
    """Servidor simulado de identidad + Betting API que registra cada llamada."""

    def __init__(self, clock: _Clock | None = None) -> None:
        self.clock = clock
        self.calls: list[tuple[str, str]] = []  # (operacion, url)
        self.keep_alive_times: list[datetime] = []
        self.keep_alive_response = lambda: httpx.Response(200, json={"status": "SUCCESS"})
        self.login_response = lambda: httpx.Response(
            200, json={"loginStatus": "SUCCESS", "sessionToken": FAKE_TOKEN}
        )
        self.api_responses: list[httpx.Response] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "certlogin" in url:
            self.calls.append(("login", url))
            return self.login_response()
        if url.endswith("/keepAlive"):
            self.calls.append(("keepAlive", url))
            if self.clock is not None:
                self.keep_alive_times.append(self.clock.now)
            return self.keep_alive_response()
        if url.endswith("/logout"):
            self.calls.append(("logout", url))
            return httpx.Response(200, json={"status": "SUCCESS"})
        self.calls.append(("api", url))
        if self.api_responses:
            return self.api_responses.pop(0)
        return httpx.Response(200, json=[])

    def count(self, operation: str) -> int:
        return sum(1 for op, _ in self.calls if op == operation)


def _manager(settings, betfair: _Betfair) -> auth.SessionManager:
    return auth.SessionManager(
        settings,
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(betfair.handler)),
    )


# --- Intervalo ----------------------------------------------------------------


@pytest.mark.critical
@pytest.mark.parametrize("jurisdiction", ["es", "it"])
def test_segregated_exchange_keep_alive_is_below_the_documented_timeout(jurisdiction):
    """El intervalo debe quedar por debajo de los 20 minutos documentados."""
    interval = auth.keep_alive_interval(jurisdiction)
    timeout = auth.SESSION_TIMEOUT_BY_JURISDICTION[jurisdiction]
    assert timeout == SPANISH_TIMEOUT
    assert interval < timeout
    # Margen suficiente para varios ciclos de ~60 s.
    assert timeout - interval >= timedelta(minutes=3)


def test_other_jurisdictions_keep_the_default_interval():
    assert auth.keep_alive_interval("com") == auth.KEEP_ALIVE_INTERVAL
    assert auth.keep_alive_interval("com.au") == auth.KEEP_ALIVE_INTERVAL


def test_spanish_session_needs_keep_alive_before_twenty_minutes(clock):
    session = auth.Session(
        FAKE_TOKEN, "k", created_at=clock.now, last_keep_alive=clock.now, jurisdiction="es"
    )
    clock.advance(minutes=14)
    assert not session.needs_keep_alive
    clock.advance(minutes=1)
    assert session.needs_keep_alive


# --- Comportamiento del SessionManager -----------------------------------------


@pytest.mark.critical
def test_spanish_session_is_renewed_before_it_expires_over_an_hour(spanish_settings, clock):
    """Simula una hora de ciclos de 60 s: ningun hueco sin renovar llega a 20 min."""
    betfair = _Betfair(clock)
    manager = _manager(spanish_settings, betfair)

    manager.current()
    renewals = [clock.now]  # el login cuenta como renovacion
    for _ in range(60):
        clock.advance(seconds=60)
        manager.current()
    renewals += betfair.keep_alive_times

    gaps = [b - a for a, b in zip(renewals, renewals[1:], strict=False)]
    assert gaps, "debe haber al menos un keepAlive en una hora"
    assert max(gaps) < SPANISH_TIMEOUT
    assert betfair.count("login") == 1  # nunca hizo falta reautenticar


@pytest.mark.critical
def test_keep_alive_is_not_sent_on_every_cycle(spanish_settings, clock):
    betfair = _Betfair(clock)
    manager = _manager(spanish_settings, betfair)

    manager.current()
    for _ in range(60):
        clock.advance(seconds=60)
        manager.current()

    # 61 llamadas en una hora, pero solo 4 keepAlive (minutos 15, 30, 45 y 60).
    assert betfair.count("keepAlive") == 4


def test_successful_keep_alive_preserves_the_session(spanish_settings, clock):
    betfair = _Betfair(clock)
    manager = _manager(spanish_settings, betfair)

    first = manager.current()
    clock.advance(minutes=15)
    second = manager.current()

    assert second is first
    assert second.last_keep_alive == clock.now
    assert betfair.count("keepAlive") == 1
    assert betfair.count("login") == 1


def test_keep_alive_does_not_reveal_the_token_in_logs(spanish_settings, clock, caplog):
    betfair = _Betfair(clock)
    betfair.keep_alive_response = lambda: httpx.Response(
        200, json={"token": FAKE_TOKEN, "status": "SUCCESS", "error": ""}
    )
    manager = _manager(spanish_settings, betfair)
    manager.current()
    clock.advance(minutes=15)

    with caplog.at_level("INFO"):
        manager.current()

    assert "sesion renovada (keepAlive)" in caplog.text
    assert FAKE_TOKEN not in caplog.text


# --- Fallos: la reautenticacion sigue siendo la segunda barrera ---------------


def test_rejected_keep_alive_falls_back_to_relogin(spanish_settings, clock):
    betfair = _Betfair(clock)
    betfair.keep_alive_response = lambda: httpx.Response(
        200, json={"status": "FAIL", "error": "NO_SESSION"}
    )
    manager = _manager(spanish_settings, betfair)

    manager.current()
    clock.advance(minutes=15)
    manager.current()

    assert betfair.count("keepAlive") == 1
    assert betfair.count("login") == 2


def test_non_json_keep_alive_response_is_handled(spanish_settings, clock):
    """Una pagina de error de un proxy no puede lanzar una excepcion sin control."""
    betfair = _Betfair(clock)
    betfair.keep_alive_response = lambda: httpx.Response(502, text="<html>Bad gateway</html>")
    manager = _manager(spanish_settings, betfair)

    manager.current()
    clock.advance(minutes=15)
    manager.current()  # no debe lanzar

    assert betfair.count("login") == 2


def test_non_json_success_status_is_handled(spanish_settings, clock):
    betfair = _Betfair(clock)
    betfair.keep_alive_response = lambda: httpx.Response(200, text="no es json")
    manager = _manager(spanish_settings, betfair)

    manager.current()
    clock.advance(minutes=15)
    manager.current()

    assert betfair.count("login") == 2


@pytest.mark.critical
def test_keep_alive_and_relogin_failure_is_recoverable(spanish_settings, clock):
    """Si fallan keepAlive y login, el error es recuperable y el siguiente intento funciona."""
    betfair = _Betfair(clock)

    def network_down():
        raise httpx.ConnectError("sin red")

    manager = _manager(spanish_settings, betfair)
    manager.current()

    clock.advance(minutes=15)
    betfair.keep_alive_response = network_down
    betfair.login_response = network_down
    with pytest.raises(auth.AuthenticationError):
        manager.current()

    # Vuelve la red: el siguiente ciclo recupera la sesion.
    betfair.keep_alive_response = lambda: httpx.Response(200, json={"status": "SUCCESS"})
    betfair.login_response = lambda: httpx.Response(
        200, json={"loginStatus": "SUCCESS", "sessionToken": FAKE_TOKEN}
    )
    clock.advance(seconds=60)
    session = manager.current()
    assert session.token == FAKE_TOKEN


@pytest.mark.critical
def test_invalid_session_still_triggers_recovery(spanish_settings, clock):
    """La barrera existente sigue intacta: INVALID_SESSION -> invalidar -> login."""
    betfair = _Betfair(clock)
    betfair.api_responses = [
        httpx.Response(
            200,
            json={"detail": {"APINGException": {"errorCode": "INVALID_SESSION_INFORMATION"}}},
        ),
        httpx.Response(200, json=[]),
    ]
    manager = _manager(spanish_settings, betfair)
    client = ReadOnlyBettingClient(manager)

    assert client.list_events(MarketFilter()) == []
    assert betfair.count("login") == 2
    assert betfair.count("api") == 2


# --- Barrera de solo lectura ----------------------------------------------------


@pytest.mark.critical
def test_session_management_only_talks_to_identity_and_read_endpoints(spanish_settings, clock):
    """El keepAlive no abre ninguna ruta nueva: solo identidad (login/keepAlive/logout)."""
    betfair = _Betfair(clock)
    manager = _manager(spanish_settings, betfair)

    manager.current()
    for _ in range(40):
        clock.advance(seconds=60)
        manager.current()
    manager.close()

    hosts = {httpx.URL(url).host for _, url in betfair.calls}
    assert hosts <= {"identitysso-cert.betfair.es", "identitysso.betfair.es"}
    assert {op for op, _ in betfair.calls} <= {"login", "keepAlive", "logout"}
