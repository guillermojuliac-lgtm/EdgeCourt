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


# Librerias que incluyen capacidad de ejecucion de apuestas. Importar cualquiera
# de ellas pondria `place_orders()` a un import de distancia dentro del proceso,
# aunque nunca se llamase. La decision D7 (docs/ARCHITECTURE.md) es no usarlas y
# hablar con la API REST de lectura directamente.
FORBIDDEN_IMPORTS = (
    "betfairlightweight",
    "betfair_apiclient",
    "flumine",
)


@pytest.mark.critical
def test_no_trading_library_is_imported():
    offenders: list[str] = []
    this_file = Path(__file__).resolve()
    for path in _python_sources():
        if path.resolve() == this_file:
            continue
        text = path.read_text(encoding="utf-8")
        for module in FORBIDDEN_IMPORTS:
            for pattern in (f"import {module}", f"from {module}"):
                if pattern in text:
                    offenders.append(f"{path.relative_to(PROJECT_ROOT)} -> {pattern}")
    assert not offenders, (
        "Se importa una libreria con capacidad de ejecutar apuestas:\n" + "\n".join(offenders)
    )


@pytest.mark.critical
def test_trading_libraries_are_not_installed():
    """Ni siquiera deben estar en el entorno: lo que no esta no se puede llamar."""
    import importlib.util

    installed = [m for m in FORBIDDEN_IMPORTS if importlib.util.find_spec(m) is not None]
    assert not installed, f"Librerias de trading instaladas en el entorno: {installed}"


@pytest.mark.critical
def test_market_package_exposes_no_write_operations():
    """La capa Betfair solo puede ofrecer operaciones de consulta."""
    from edgecourt.market import client

    write_operations = {
        "placeOrders",
        "cancelOrders",
        "replaceOrders",
        "updateOrders",
        "createDeveloperAppKeys",
        "transferFunds",
    }
    source = (SRC / "edgecourt" / "market").rglob("*.py")
    found: list[str] = []
    for path in source:
        text = path.read_text(encoding="utf-8")
        for operation in write_operations:
            if operation in text:
                found.append(f"{path.name} -> {operation}")
    assert not found, f"La capa de mercado expone operaciones de escritura: {found}"

    # Y el cliente no debe tener ningun metodo publico que sugiera escritura.
    public = [m for m in dir(client.ReadOnlyBettingClient) if not m.startswith("_")]
    for method in public:
        assert not any(
            verb in method.lower() for verb in ("place", "cancel", "replace", "update", "bet")
        ), f"Metodo sospechoso en el cliente de lectura: {method}"
    assert set(public) == {"list_events", "list_market_catalogue", "list_market_book"}
