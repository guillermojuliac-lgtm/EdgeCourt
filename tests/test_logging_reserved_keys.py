"""Protege contra un fallo silencioso y facil de repetir del logging estandar.

`logging` rechaza con KeyError cualquier clave de `extra` que colisione con un
atributo de `LogRecord`. Lo traicionero es que **solo ocurre si el registro
llega a crearse**: con el logging por debajo de INFO la llamada no falla, asi
que el error no aparece en los tests y si en produccion.

Ocurrio de verdad: `extra={"name": ...}` en el runner de migraciones habria
roto la aplicacion del esquema en cuanto el logging estuviera a INFO, que es el
valor por defecto de EdgeCourt.
"""

from __future__ import annotations

import ast
import logging

import pytest

from edgecourt.config import PROJECT_ROOT

# Atributos que `logging.Logger.makeRecord` considera reservados.
RESERVED_LOG_RECORD_KEYS = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


def _extra_keys_in_source() -> list[tuple[str, int, str]]:
    findings: list[tuple[str, int, str]] = []
    for path in (PROJECT_ROOT / "src").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg == "extra" and isinstance(keyword.value, ast.Dict):
                    for key in keyword.value.keys:
                        if isinstance(key, ast.Constant) and isinstance(key.value, str):
                            findings.append(
                                (str(path.relative_to(PROJECT_ROOT)), key.lineno, key.value)
                            )
    return findings


@pytest.mark.critical
def test_no_reserved_keys_in_logging_extra():
    offenders = [
        f"{path}:{line} -> '{key}'"
        for path, line, key in _extra_keys_in_source()
        if key in RESERVED_LOG_RECORD_KEYS
    ]
    assert not offenders, (
        "Claves reservadas de LogRecord usadas en extra=; provocan KeyError "
        "en cuanto el logging esta a nivel INFO:\n  " + "\n  ".join(offenders)
    )


@pytest.mark.critical
def test_logging_at_info_does_not_break(tmp_path):
    """Comprobacion funcional: con el logging a INFO nada debe estallar."""
    from edgecourt.logging_setup import get_logger, setup_logging

    setup_logging(tmp_path, level="INFO", console=False)
    log = get_logger("db.migrate")

    log.info("prueba", extra={"version": 1, "migration_name": "initial_schema"})

    content = (tmp_path / "application.log").read_text()
    assert "initial_schema" in content


def test_reserved_key_would_be_detected():
    """El test anterior solo vale si de verdad detecta el problema."""
    import logging as logging_module

    logger = logging_module.getLogger("prueba.reservada")
    logger.setLevel(logging_module.INFO)
    logger.addHandler(logging_module.NullHandler())

    with pytest.raises(KeyError):
        logger.info("x", extra={"name": "colision"})
