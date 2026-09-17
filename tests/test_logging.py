"""Tests de logging: estructura JSON, rotacion y redaccion de secretos."""

from __future__ import annotations

import json

import pytest

from edgecourt.logging_setup import (
    REDACTED,
    SecretRedactionFilter,
    get_logger,
    secrets_from_settings,
    setup_logging,
)


def _read_log(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


@pytest.mark.critical
def test_logging_redacts_known_secrets(tmp_path):
    secret = "sup3r-s3cret-betfair-password"
    setup_logging(tmp_path, secrets=frozenset({secret}), console=False)
    get_logger().info("login fallido con %s", secret, extra={"app_key": secret})

    records = _read_log(tmp_path / "application.log")
    raw = (tmp_path / "application.log").read_text(encoding="utf-8")
    assert secret not in raw
    assert REDACTED in records[0]["message"]
    assert records[0]["app_key"] == REDACTED


@pytest.mark.critical
def test_logging_redacts_telegram_token_by_pattern(tmp_path):
    """Un token nunca configurado tambien se redacta, por patron."""
    setup_logging(tmp_path, console=False)
    token = "123456789:AAHfSHFJKhfsdkjfhSDKJFHsdkjfh_sdkjfh12"
    get_logger().error(f"fallo al llamar a la API con token {token}")

    raw = (tmp_path / "application.log").read_text(encoding="utf-8")
    assert token not in raw
    assert REDACTED in raw


def test_assignment_patterns_are_redacted():
    redactor = SecretRedactionFilter()
    import logging

    record = logging.LogRecord("x", logging.INFO, "f", 1, 'password="hunter2secret"', None, None)
    redactor.filter(record)
    assert "hunter2secret" not in record.msg


def test_log_lines_are_valid_json_with_extras(tmp_path):
    setup_logging(tmp_path, console=False)
    get_logger("features").info("features generadas", extra={"rows": 1234, "surface": "clay"})

    records = _read_log(tmp_path / "application.log")
    assert records[-1]["rows"] == 1234
    assert records[-1]["surface"] == "clay"
    assert records[-1]["logger"] == "edgecourt.features"
    assert records[-1]["level"] == "INFO"
    assert "timestamp" in records[-1]


def test_specific_loggers_write_to_their_own_file(tmp_path):
    setup_logging(tmp_path, console=False)
    get_logger("collector").info("snapshot recogido")
    get_logger("training").info("entrenamiento iniciado")

    assert "snapshot recogido" in (tmp_path / "collector.log").read_text()
    assert "entrenamiento iniciado" in (tmp_path / "training.log").read_text()
    # No se duplican en application.log.
    application = (tmp_path / "application.log").read_text()
    assert "snapshot recogido" not in application


def test_warnings_and_errors_reach_errors_log(tmp_path):
    setup_logging(tmp_path, console=False)
    get_logger().info("esto es rutina")
    get_logger().error("esto es grave")

    errors = (tmp_path / "errors.log").read_text()
    assert "esto es grave" in errors
    assert "esto es rutina" not in errors


def test_setup_is_idempotent(tmp_path):
    """Reconfigurar no debe duplicar handlers ni lineas."""
    for _ in range(3):
        setup_logging(tmp_path, console=False)
    get_logger().info("mensaje unico")
    assert len(_read_log(tmp_path / "application.log")) == 1


def test_secrets_from_settings(settings_factory):
    settings = settings_factory(betfair_password="abcdef123456", betfair_app_key="key-abcdef")
    secrets = secrets_from_settings(settings)
    assert "abcdef123456" in secrets
    assert "key-abcdef" in secrets
    assert "" not in secrets
