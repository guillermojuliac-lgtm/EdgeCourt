"""Logging estructurado de EdgeCourt.

Decisiones (ver IMPLEMENTATION_PLAN.md D5):

* `logging` de la stdlib + un formatter JSON propio. No se usa `structlog`:
  no aporta lo suficiente para justificar una dependencia mas.
* Ficheros separados por responsabilidad, con rotacion automatica.
* Un filtro de redaccion se aplica a *todos* los handlers, de modo que ningun
  secreto conocido pueda acabar escrito en disco aunque alguien lo pase por
  error a un log.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REDACTED = "***REDACTED***"

MAX_BYTES = 10 * 1024 * 1024  # 10 MiB por fichero
BACKUP_COUNT = 5

# Nombres de logger por responsabilidad -> fichero de destino.
LOG_FILES: dict[str, str] = {
    "edgecourt": "application.log",
    "edgecourt.collector": "collector.log",
    "edgecourt.training": "training.log",
}

# Patrones que se redactan siempre, aunque el valor no este en la configuracion
# (por ejemplo si alguien pega un token en un mensaje de error).
_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Token de bot de Telegram: <digitos>:<35 chars base64url>
    re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b"),
    # Asignaciones tipo password=..., token: ..., app_key="..."
    re.compile(
        r"(?i)\b(password|passwd|token|api[_-]?key|app[_-]?key|secret|session[_-]?token)"
        r"\b\s*[=:]\s*[\"']?([^\s\"',}]+)"
    ),
)

# Atributos internos de LogRecord que no forman parte del payload del usuario.
_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


def _redact_text(text: str, literals: frozenset[str]) -> str:
    """Sustituye secretos en un texto libre."""
    for literal in literals:
        if literal and literal in text:
            text = text.replace(literal, REDACTED)
    for pattern in _PATTERNS:
        if pattern.groups >= 2:
            text = pattern.sub(lambda m: f"{m.group(1)}={REDACTED}", text)
        else:
            text = pattern.sub(REDACTED, text)
    return text


class SecretRedactionFilter(logging.Filter):
    """Elimina secretos del mensaje y de los campos extra de cada registro."""

    def __init__(self, literals: frozenset[str] = frozenset()) -> None:
        super().__init__()
        # Se ignoran cadenas muy cortas: redactarlas produciria falsos positivos
        # masivos (por ejemplo, una contrasena vacia o de 2 caracteres).
        self._literals = frozenset(s for s in literals if len(s) >= 6)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact_text(str(record.msg), self._literals)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: _redact_text(str(v), self._literals) for k, v in record.args.items()
                }
            else:
                record.args = tuple(_redact_text(str(a), self._literals) for a in record.args)
        for key, value in list(record.__dict__.items()):
            if key not in _RESERVED and isinstance(value, str):
                record.__dict__[key] = _redact_text(value, self._literals)
        return True


class JsonFormatter(logging.Formatter):
    """Serializa cada registro como una linea JSON."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        # Campos pasados via logger.info("...", extra={...}).
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value

        return json.dumps(payload, ensure_ascii=False, default=str)


def _file_handler(path: Path, level: int, redactor: SecretRedactionFilter) -> logging.Handler:
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    handler.setLevel(level)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(redactor)
    return handler


def running_under_systemd() -> bool:
    """Detecta si el proceso lo ha lanzado systemd.

    `INVOCATION_ID` la define systemd para cada unidad y `JOURNAL_STREAM` aparece
    cuando la salida esta conectada al journal. Sirve para decidir si conviene
    emitir tambien por stdout, que es lo que `journalctl` recoge.
    """
    return bool(os.environ.get("INVOCATION_ID") or os.environ.get("JOURNAL_STREAM"))


def setup_logging(
    logs_dir: Path,
    level: str = "INFO",
    secrets: frozenset[str] = frozenset(),
    console: bool | None = None,
) -> None:
    """Configura el arbol de loggers de EdgeCourt. Idempotente.

    Args:
        logs_dir: directorio donde se escriben los ficheros de log.
        level: nivel minimo global.
        secrets: valores literales que nunca deben aparecer en los logs.
        console: si ademas se escribe por consola. Con `None` se decide solo:
            activado bajo systemd, para que los registros lleguen al journal.
    """
    if console is None:
        console = running_under_systemd()
    logs_dir.mkdir(parents=True, exist_ok=True)
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    redactor = SecretRedactionFilter(secrets)

    def _console_handler() -> logging.Handler:
        handler = logging.StreamHandler()
        handler.setLevel(numeric_level)
        # Bajo systemd se emite JSON, que el journal indexa y permite filtrar;
        # en uso interactivo, un formato legible.
        handler.setFormatter(
            JsonFormatter()
            if running_under_systemd()
            else logging.Formatter("%(levelname)-8s %(name)s: %(message)s")
        )
        handler.addFilter(redactor)
        return handler

    for logger_name, filename in LOG_FILES.items():
        logger = logging.getLogger(logger_name)
        # Idempotencia: reconfigurar no debe duplicar handlers.
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()
        logger.setLevel(numeric_level)
        logger.addHandler(_file_handler(logs_dir / filename, numeric_level, redactor))

        # Los loggers especificos no propagan, para que cada linea del collector
        # no acabe duplicada en application.log. Pero entonces tampoco llegarian
        # al resto de destinos, asi que se les adjuntan explicitamente:
        #
        #   - errors.log, que debe recoger los avisos y errores de TODO el arbol;
        #   - la consola, que bajo systemd es lo que ve `journalctl`.
        #
        # Sin esto, los ciclos y los fallos del collector solo existirian en su
        # propio fichero, y `journalctl -u edgecourt-collector` no serviria para
        # supervisar el servicio.
        logger.addHandler(_file_handler(logs_dir / "errors.log", logging.WARNING, redactor))
        if console:
            logger.addHandler(_console_handler())

        logger.propagate = logger_name == "edgecourt"

    root = logging.getLogger("edgecourt")
    root.propagate = False


def get_logger(name: str = "edgecourt") -> logging.Logger:
    """Devuelve un logger bajo el arbol `edgecourt`."""
    if name == "edgecourt" or name.startswith("edgecourt."):
        return logging.getLogger(name)
    return logging.getLogger(f"edgecourt.{name}")


def secrets_from_settings(settings: Any) -> frozenset[str]:
    """Extrae de la configuracion los valores que nunca deben registrarse."""
    fields = (
        "betfair_password",
        "betfair_username",
        "betfair_app_key",
        "telegram_bot_token",
        "telegram_chat_id",
    )
    return frozenset(
        str(value) for field in fields if (value := getattr(settings, field, "")) not in ("", None)
    )
