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
from datetime import date

from edgecourt import __version__
from edgecourt.config import REAL_BETTING_ENABLED, Settings, get_settings
from edgecourt.data import pipeline
from edgecourt.logging_setup import get_logger, secrets_from_settings, setup_logging
from edgecourt.storage import dataset_summary

# Subcomandos previstos y fase en la que se implementan.
PENDING: dict[str, tuple[str, str]] = {
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
    ):
        info = dataset_summary(path)
        if info.get("rows"):
            print(f"    {label:<8}: {info['rows']:,} filas  ({info['bytes'] / 1e6:.1f} MB)")
        else:
            print(f"    {label:<8}: vacio")
    reports = sorted(settings.results_dir.glob("*.json")) if settings.results_dir.exists() else []
    print(f"    {'informes':<8}: {len(reports)}")
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


def _cmd_data_fetch(settings: Settings, args: argparse.Namespace) -> int:
    """Descarga los CSV crudos. Accion explicita y con confirmacion de alcance."""
    years = range(args.from_year, args.to_year + 1)
    print(f"Descargando {years.start}-{years.stop - 1} a {settings.raw_dir}")
    print("  primaria  : TennisMyLife (stats.tennismylife.org)")
    if not args.no_reference:
        print("  referencia: mirror archivistico de datos de Jeff Sackmann (solo contraste)")
    print("  uso no comercial, con atribucion. Ver docs/DATA.md")
    print()

    counts = pipeline.fetch(
        settings, years=years, include_reference=not args.no_reference, force=args.force
    )
    for source, n in counts.items():
        print(f"  {source:<18}: {n} fichero(s)")
    return 0


def _cmd_data_import(settings: Settings, args: argparse.Namespace) -> int:
    """Construye el dataset canonico match_facts."""
    result = pipeline.build_match_facts(settings, min_year=args.from_year, max_year=args.to_year)
    s = result.summary

    print(f"match_facts construido: {result.rows:,} partidos")
    print(f"  destino            : {result.destination}")
    print(f"  anos               : {s['year_min']}-{s['year_max']}")
    print(f"  duplicados          : {result.removed_duplicates}")
    print(f"  P(target=1)         : {s['target_mean']:.4f}   (debe ser ~0.5)")
    print(f"  con stats de partido: {s['pct_with_match_stats']:.1f}%")
    print(f"  con ranking ambos   : {s['pct_with_rank']:.1f}%")
    print(f"  con indoor          : {s['pct_with_indoor']:.1f}%")
    print(f"  superficies         : {s['surface_counts']}")
    print(f"  finalizacion        : {s['completion_counts']}")
    print()
    print(result.report.summary())
    return 0 if result.report.ok else 4


def _cmd_data_check(settings: Settings, args: argparse.Namespace) -> int:
    """Contrasta el dataset canonico con la fuente de referencia."""
    result = pipeline.cross_check_against_reference(
        settings, min_year=args.from_year, max_year=args.to_year
    )
    if not result["overlap_years"]:
        print("No hay solapamiento con la fuente de referencia.")
        return 4
    print(
        f"Contraste sobre {len(result['overlap_years'])} anos "
        f"({min(result['overlap_years'])}-{max(result['overlap_years'])})"
    )
    print(f"  partidos primaria    : {result['primary_rows']:,}")
    print(f"  partidos referencia  : {result['reference_rows']:,}")
    print(f"  emparejados          : {result['matched']:,}")
    print(f"  cobertura primaria   : {result['match_rate_primary']}%")
    print(f"  cobertura (nombres compactos): {result['match_rate_primary_compact']}%")
    print(f"  cobertura referencia : {result['match_rate_reference']}%")
    print(f"  solo en primaria     : {result['primary_only']:,}")
    print(f"  solo en referencia   : {result['reference_only']:,}")
    print(f"  acuerdo en ganador   : {result['winner_agreement_pct']}%")
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

    data = sub.add_parser("data", help="ingesta y validacion de datos historicos")
    data_sub = data.add_subparsers(dest="subcommand", required=True)

    fetch = data_sub.add_parser("fetch", help="descargar CSV historicos (accion explicita)")
    fetch.add_argument("--from-year", type=int, default=1991, dest="from_year")
    fetch.add_argument("--to-year", type=int, default=date.today().year, dest="to_year")
    fetch.add_argument(
        "--no-reference", action="store_true", help="no descargar la fuente de contraste"
    )
    fetch.add_argument("--force", action="store_true", help="volver a descargar aunque ya exista")

    imp = data_sub.add_parser("import", help="construir el dataset canonico match_facts")
    imp.add_argument("--from-year", type=int, default=2000, dest="from_year")
    imp.add_argument("--to-year", type=int, default=None, dest="to_year")

    check = data_sub.add_parser("check", help="contrastar con la fuente de referencia")
    check.add_argument("--from-year", type=int, default=2000, dest="from_year")
    check.add_argument("--to-year", type=int, default=None, dest="to_year")

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

    handlers = {
        "status": _cmd_status,
        "data fetch": _cmd_data_fetch,
        "data import": _cmd_data_import,
        "data check": _cmd_data_check,
    }
    if key in handlers:
        return handlers[key](settings, args)
    if key in PENDING:
        return _cmd_pending(key)

    parser.error(f"comando desconocido: {key}")
    return 1  # pragma: no cover - parser.error termina el proceso


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
