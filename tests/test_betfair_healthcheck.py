"""Tests del healthcheck de Betfair.

Lo esencial que se protege aqui: que **no escribe nada en disco** y que **no
filtra secretos** en su salida.
"""

from __future__ import annotations

import subprocess

import httpx
import pytest
from tests.factories_betfair import catalogue, market_book

from edgecourt.market import auth, healthcheck

FAKE_TOKEN = "token-de-prueba-no-real-0123456789"
FAKE_PASSWORD = "contrasena-ficticia-de-prueba"
FAKE_APP_KEY = "app-key-ficticia-1234"


def _generate_keypair(directory):
    """Genera un par clave/certificado real para las comprobaciones de openssl."""
    key = directory / "client-2048.key"
    csr = directory / "client-2048.csr"
    crt = directory / "client-2048.crt"
    quiet = {"capture_output": True, "check": True}
    subprocess.run(["openssl", "genrsa", "-out", str(key), "2048"], **quiet)
    subprocess.run(
        ["openssl", "req", "-new", "-key", str(key), "-out", str(csr), "-subj", "/CN=test"],
        **quiet,
    )
    subprocess.run(
        [
            "openssl",
            "x509",
            "-req",
            "-days",
            "365",
            "-in",
            str(csr),
            "-signkey",
            str(key),
            "-out",
            str(crt),
        ],
        **quiet,
    )
    key.chmod(0o600)
    return crt, key


@pytest.fixture
def real_certificate(tmp_path):
    if subprocess.run(["which", "openssl"], capture_output=True).returncode != 0:
        pytest.skip("openssl no disponible")
    return _generate_keypair(tmp_path)


@pytest.fixture
def credentialed(settings_factory, real_certificate):
    crt, key = real_certificate
    return settings_factory(
        betfair_username="usuario-ficticio",
        betfair_password=FAKE_PASSWORD,
        betfair_app_key=FAKE_APP_KEY,
        betfair_cert_path=crt,
        betfair_key_path=key,
    )


def _handler(request):
    url = str(request.url)
    if "certlogin" in url:
        return httpx.Response(200, json={"loginStatus": "SUCCESS", "sessionToken": FAKE_TOKEN})
    if "listEvents" in url:
        return httpx.Response(200, json=[{"event": {"id": "1", "name": "A v B"}}])
    if "listMarketCatalogue" in url:
        return httpx.Response(200, json=[catalogue()])
    if "listMarketBook" in url:
        return httpx.Response(200, json=[market_book()])
    return httpx.Response(200, json={"status": "SUCCESS"})


@pytest.fixture
def patched_sessions(monkeypatch):
    """Sustituye el cliente HTTP por uno simulado, conservando la logica real."""
    original = auth.SessionManager._default_client_factory

    def factory(self):
        auth._validate_credentials(self._settings)
        return httpx.Client(transport=httpx.MockTransport(_handler))

    monkeypatch.setattr(auth.SessionManager, "_default_client_factory", factory)
    yield
    monkeypatch.setattr(auth.SessionManager, "_default_client_factory", original)


# --- Comportamiento principal -------------------------------------------------


def test_healthcheck_passes_with_valid_setup(credentialed, patched_sessions):
    report = healthcheck.run_healthcheck(credentialed)
    assert report.ok, [f"{c.name}: {c.detail}" for c in report.checks if not c.ok]
    assert report.events_found == 1
    assert report.markets_found == 1
    assert report.sample


@pytest.mark.critical
def test_healthcheck_writes_nothing_to_disk(credentialed, patched_sessions, tmp_path):
    """Verificar no puede tener efectos secundarios: ni un solo fichero nuevo."""
    credentialed.ensure_directories()
    before = {p for p in tmp_path.rglob("*") if p.is_file()}

    healthcheck.run_healthcheck(credentialed)

    after = {p for p in tmp_path.rglob("*") if p.is_file()}
    assert after == before, f"el healthcheck escribio: {sorted(after - before)}"


@pytest.mark.critical
def test_healthcheck_never_prints_secrets(credentialed, patched_sessions):
    """Ni la contrasena, ni la app key, ni el token pueden aparecer en la salida."""
    report = healthcheck.run_healthcheck(credentialed)
    text = " ".join(f"{c.name} {c.detail}" for c in report.checks) + " ".join(report.sample)

    assert FAKE_PASSWORD not in text
    assert FAKE_APP_KEY not in text
    assert FAKE_TOKEN not in text
    # De la app key solo puede revelarse su longitud.
    assert str(len(FAKE_APP_KEY)) in text


# --- Comprobaciones individuales ---------------------------------------------


def test_missing_configuration_stops_before_the_network(settings_factory):
    report = healthcheck.run_healthcheck(settings_factory())
    assert not report.ok
    assert len(report.checks) == 1
    assert "BETFAIR_USERNAME" in report.checks[0].detail


@pytest.mark.critical
def test_world_readable_key_is_rejected(credentialed, patched_sessions):
    """Una clave privada legible por otros usuarios es un problema real."""
    credentialed.betfair_key_path.chmod(0o644)
    report = healthcheck.run_healthcheck(credentialed)

    assert not report.ok
    failed = [c for c in report.checks if not c.ok]
    assert any("Permisos" in c.name for c in failed)
    assert any("chmod 600" in c.detail for c in failed)


@pytest.mark.critical
def test_mismatched_key_and_certificate_are_detected(credentialed, tmp_path, patched_sessions):
    """Un certificado de otra clave es un error facil de cometer y dificil de ver."""
    other = tmp_path / "otro"
    other.mkdir()
    _, other_key = _generate_keypair(other)

    settings = credentialed.model_copy(update={"betfair_key_path": other_key})
    report = healthcheck.run_healthcheck(settings)

    assert not report.ok
    assert any("no corresponde" in c.detail for c in report.checks if not c.ok)


def test_encrypted_key_is_rejected(credentialed, tmp_path):
    """Una clave con passphrase no sirve para un proceso desatendido."""
    encrypted = tmp_path / "cifrada.key"
    encrypted.write_text(
        "-----BEGIN RSA PRIVATE KEY-----\nProc-Type: 4,ENCRYPTED\nDEK-Info: AES-128-CBC\n"
    )
    encrypted.chmod(0o600)

    settings = credentialed.model_copy(update={"betfair_key_path": encrypted})
    report = healthcheck.run_healthcheck(settings)

    assert not report.ok
    assert any("cifrada" in c.detail for c in report.checks if not c.ok)


def test_certificate_validity_is_reported(credentialed, patched_sessions):
    report = healthcheck.run_healthcheck(credentialed)
    vigencia = [c for c in report.checks if c.name == "Vigencia"]
    assert vigencia and vigencia[0].ok
    assert "valido hasta" in vigencia[0].detail or "CADUCA" in vigencia[0].detail


def test_key_size_is_reported(credentialed, patched_sessions):
    report = healthcheck.run_healthcheck(credentialed)
    size = [c for c in report.checks if c.name == "Tamano de clave"]
    assert size and "2048" in size[0].detail


# --- Fallos de red y de sesion -----------------------------------------------


def test_failed_login_is_reported_not_raised(credentialed, monkeypatch):
    def failing(self):
        auth._validate_credentials(self._settings)
        return httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json={"loginStatus": "INVALID_USERNAME_OR_PASSWORD"})
            )
        )

    monkeypatch.setattr(auth.SessionManager, "_default_client_factory", failing)
    report = healthcheck.run_healthcheck(credentialed)

    assert not report.ok
    login = [c for c in report.checks if c.name == "Login por certificado"]
    assert login and not login[0].ok
    assert FAKE_PASSWORD not in login[0].detail


def test_read_failure_is_reported_not_raised(credentialed, monkeypatch):
    def handler(request):
        if "certlogin" in str(request.url):
            return httpx.Response(200, json={"loginStatus": "SUCCESS", "sessionToken": FAKE_TOKEN})
        return httpx.Response(
            400, json={"detail": {"APINGException": {"errorCode": "INVALID_APP_KEY"}}}
        )

    def factory(self):
        auth._validate_credentials(self._settings)
        return httpx.Client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(auth.SessionManager, "_default_client_factory", factory)
    monkeypatch.setattr(auth.time, "sleep", lambda _s: None)

    report = healthcheck.run_healthcheck(credentialed)
    assert not report.ok
    assert any("INVALID_APP_KEY" in c.detail for c in report.checks if not c.ok)
