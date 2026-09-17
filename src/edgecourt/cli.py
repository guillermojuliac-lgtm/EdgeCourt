"""CLI de EdgeCourt.

Se usa `argparse` de la stdlib en lugar de typer/click (IMPLEMENTATION_PLAN.md
D4): los subcomandos anidados son suficientes y evitan una dependencia en la
ruta critica.

Los subcomandos de fases aun no implementadas estan declarados pero devuelven un
codigo de salida distinto de cero indicando la fase en la que llegaran. Asi la
CLI documenta el mapa del proyecto sin fingir capacidades que no tiene.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from edgecourt import __version__
from edgecourt.config import REAL_BETTING_ENABLED, Settings, get_settings
from edgecourt.logging_setup import get_logger, secrets_from_settings, setup_logging
from edgecourt.storage import dataset_summary

# Subcomandos previstos y fase en la que se implementan.
PENDING: dict[str, tuple[str, str]] = {
    "data import": ("PHASE 1", "Importar un dataset historico de tenis"),
    "elo build": ("PHASE 2", "Calcular Elo global y por superficie"),
    "features build": ("PHASE 3", "Generar la tabla de features"),
    "train": ("PHASE 4-5", "Entrenar un modelo challenger"),
    "backtest": ("PHASE 7", "Validacion temporal walk-forward"),
    "collector start": ("PHASE 8", "Recoger snapshots de cuotas de Betfair (solo lectura)"),
    "predict": ("PHASE 9", "Generar predicciones y evaluar value"),
    "paper status": ("PHASE 11", "Estado del ledger de paper betting"),
    "metrics": ("PHASE 12", "Calcular metricas: Brier, CLV, ROI, drawdown"),
    "model compare": ("PHASE 13", "Comparar Production vs Challenger"),
}


def _cmd_status(settings: Settings, _args: argparse.Namespace) -> int:
    """Muestra el estado del sistema: configuracion, datos y modelos."""
    log = get_logger("cli")
    log.info("status solicitado", extra={"environment": settings.environment})

    print(f"EdgeCourt v{__version__}")
    print()
    print("  Entorno            :", settings.environment)
    print("  Modo de apuesta    :", settings.betting_mode.upper())
    print("  Apuestas reales    :", "HABILITADAS" if REAL_BETTING_ENABLED else "NO IMPLEMENTADAS")
    print("  Nivel de log       :", settings.log_level)
    print()
    print("  Rutas")
    print("    data   :", settings.data_dir)
    print("    models :", settings.models_dir)
    print("    logs   :", settings.logs_dir)
    print()
    print("  Riesgo (paper)")
    print(f"    bankroll             : {settings.bankroll:.2f}")
    print(f"    stake maximo         : {settings.max_stake_percentage:.2%} del bankroll")
    print(f"    exposicion diaria    : {settings.max_daily_exposure:.2%}")
    print(f"    edge minimo (neto)   : {settings.minimum_edge:.2%}")
    print(f"    fraccion Kelly       : {settings.kelly_fraction:.2f}")
    print(f"    drawdown maximo      : {settings.maximum_drawdown:.2%}")
    print()
    print("  Datos")
    for label, path in (
        ("matches", settings.processed_dir / "matches"),
        ("odds", settings.odds_dir),
        ("results", settings.results_dir),
    ):
        info = dataset_summary(path)
        if info.get("rows"):
            print(f"    {label:<8}: {info['rows']:,} filas  ({info['bytes'] / 1e6:.1f} MB)")
        else:
            print(f"    {label:<8}: vacio")
    print()
    print("  Modelos")
    slots = (("production", settings.production_dir), ("challenger", settings.challenger_dir))
    for slot, path in slots:
        manifests = sorted(path.glob("*.json")) if path.exists() else []
        print(f"    {slot:<11}: {len(manifests)} modelo(s)")
    print()

    if settings.betting_mode != "paper" or REAL_BETTING_ENABLED:  # pragma: no cover - inalcanzable
        print("  ATENCION: configuracion incoherente con el modo paper.", file=sys.stderr)
        return 2
    return 0


def _cmd_pending(key: str) -> int:
    phase, description = PENDING[key]
    print(f"`edgecourt {key}` -> {description}")
    print(f"No implementado todavia. Llega en {phase} (ver IMPLEMENTATION_PLAN.md).")
    return 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="edgecourt",
        description="EdgeCourt - investigacion cuantitativa de tenis. PAPER BETTING UNICAMENTE.",
    )
    parser.add_argument("--version", action="version", version=f"edgecourt {__version__}")
    parser.add_argument(
        "--no-log-file",
        action="store_true",
        help="no escribir en los ficheros de log (util en tests y en uso interactivo)",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="<comando>")

    sub.add_parser("status", help="estado del sistema, configuracion y datos")

    # Subcomandos pendientes, agrupados por familia cuando tienen subniveles.
    groups: dict[str, argparse.ArgumentParser] = {}
    for key in PENDING:
        head, _, tail = key.partition(" ")
        if not tail:
            sub.add_parser(head, help=f"[{PENDING[key][0]}] {PENDING[key][1]}")
            continue
        if head not in groups:
            group_parser = sub.add_parser(head, help=f"operaciones de {head}")
            groups[head] = group_parser.add_subparsers(dest="subcommand", required=True)
        groups[head].add_parser(tail, help=f"[{PENDING[key][0]}] {PENDING[key][1]}")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    settings = get_settings()
    settings.ensure_directories()
    setup_logging(
        logs_dir=settings.logs_dir,
        level=settings.log_level,
        secrets=secrets_from_settings(settings),
        console=False,
    )

    key = args.command
    if getattr(args, "subcommand", None):
        key = f"{args.command} {args.subcommand}"

    if key == "status":
        return _cmd_status(settings, args)
    if key in PENDING:
        return _cmd_pending(key)

    parser.error(f"comando desconocido: {key}")
    return 1  # pragma: no cover - parser.error termina el proceso


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
