"""Protocolo `sd_notify` de systemd, solo con biblioteca estandar.

Permite que el collector le diga a systemd "estoy listo" (`READY=1`), "sigo
completando ciclos" (`WATCHDOG=1`) y "me estoy parando" (`STOPPING=1`).

Fuera de systemd (sin `NOTIFY_SOCKET`) todo es un no-op: el collector se comporta
exactamente igual que sin este modulo. Un fallo al enviar (socket cerrado, permisos)
tampoco propaga excepciones: la notificacion es un servicio de supervision y jamas
debe tumbar el bucle que supervisa.

Los mensajes son constantes sin datos dinamicos: nunca contienen configuracion,
credenciales ni contenido de respuestas de Betfair.
"""

from __future__ import annotations

import os
import socket

from edgecourt.logging_setup import get_logger

log = get_logger("systemd_notify")

READY = "READY=1"
WATCHDOG = "WATCHDOG=1"
STOPPING = "STOPPING=1"


class SystemdNotifier:
    """Envia datagramas al `NOTIFY_SOCKET`. Sin socket, no hace nada."""

    def __init__(self, address: str | None) -> None:
        self._address = address or None
        self._warned = False

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> SystemdNotifier:
        env = os.environ if environ is None else environ
        return cls(env.get("NOTIFY_SOCKET"))

    @property
    def enabled(self) -> bool:
        return self._address is not None

    def notify(self, message: str) -> bool:
        """Envia `message`. Devuelve si se entrego; nunca lanza."""
        if self._address is None:
            return False
        address = self._address
        # Los sockets abstractos de Linux se anuncian con '@' inicial.
        if address.startswith("@"):
            address = "\0" + address[1:]
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM | socket.SOCK_CLOEXEC) as sock:
                sock.connect(address)
                sock.sendall(message.encode("ascii"))
        except OSError as exc:
            if not self._warned:
                self._warned = True
                log.warning(
                    "no se pudo notificar a systemd",
                    extra={"error_type": type(exc).__name__, "notification": message},
                )
            return False
        return True

    def ready(self) -> bool:
        return self.notify(READY)

    def watchdog(self) -> bool:
        return self.notify(WATCHDOG)

    def stopping(self) -> bool:
        return self.notify(STOPPING)
