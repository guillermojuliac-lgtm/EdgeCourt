"""Barrera de seguridad: el codigo no debe poder enviar una apuesta real.

Este test no comprueba configuracion, comprueba el *codigo fuente*. Escanea
todo `src/` en busca de los endpoints y metodos de la API de Betfair capaces de
crear, modificar o cancelar ordenes. Si alguien los introduce -aunque sea detras
de un flag desactivado- este test falla y bloquea el avance de fase.

Brief §28: "No implementar accidentalmente ejecucion real."
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from edgecourt.config import PROJECT_ROOT

SRC = PROJECT_ROOT / "src"

# Operaciones de la Betting API de Betfair que mueven dinero real.
FORBIDDEN = (
    "placeOrders",
    "cancelOrders",
    "replaceOrders",
    "updateOrders",
    "place_order",
    "cancel_order",
    "replace_order",
    "/betting/rest/v1.0/placeOrders",
)


def _python_sources() -> list[Path]:
    return [p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts]


@pytest.mark.critical
def test_source_contains_no_order_placement_calls():
    offenders: list[str] = []
    this_file = Path(__file__).resolve()
    for path in _python_sources():
        if path.resolve() == this_file:
            continue
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN:
            for match in re.finditer(re.escape(token), text):
                line = text.count("\n", 0, match.start()) + 1
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{line} -> {token}")
    assert not offenders, (
        "Se ha encontrado codigo capaz de enviar ordenes reales a Betfair:\n" + "\n".join(offenders)
    )


@pytest.mark.critical
def test_no_module_declares_live_betting_mode():
    """Ningun modulo debe introducir un modo de apuesta alternativo."""
    pattern = re.compile(r"""betting_mode\s*[=:]\s*["'](?!paper["'])""")
    offenders = [
        str(path.relative_to(PROJECT_ROOT))
        for path in _python_sources()
        if pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"Modo de apuesta no-paper declarado en: {offenders}"
