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
from edgecourt.data import splits as splits_module
from edgecourt.features import pipeline as features_pipeline
from edgecourt.logging_setup import get_logger, secrets_from_settings, setup_logging
from edgecourt.storage import dataset_summary

# Subcomandos previstos y fase en la que se implementan.
PENDING: dict[str, tuple[str, str]] = {
    "backtest": ("PHASE 7", "Validacion temporal walk-forward"),
    "predict": ("PHASE 9", "Generar predicciones y evaluar value"),
    "paper status": ("PHASE 11", "Estado del ledger de paper betting"),
    "metrics": ("PHASE 12", "Calcular metricas: Brier, CLV, ROI, drawdown"),
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


def _cmd_elo_build(settings: Settings, _args: argparse.Namespace) -> int:
    """Calcula el Elo global y por superficie sobre todo el historico."""
    result = features_pipeline.build_elo(settings)
    print(f"Elo calculado: {result.rows:,} partidos")
    print(f"  destino  : {result.destination}")
    print(f"  jugadores: {result.players:,}")
    print(f"  anos     : {result.first_year}-{result.last_year}")
    return 0


def _print_metrics_table(rows: list[dict]) -> None:
    header = f"  {'modelo':<16}{'n':>8}{'Brier':>10}{'LogLoss':>10}{'Acc':>9}{'AUC':>8}{'ECE':>8}"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for row in rows:
        print(
            f"  {row['model']:<16}{row['n']:>8,}{row['brier']:>10.5f}{row['log_loss']:>10.5f}"
            f"{row['accuracy']:>9.4f}{row['roc_auc']:>8.4f}{row['ece']:>8.4f}"
        )


def _print_calibration(table: list[dict]) -> None:
    print(f"    {'bucket':<14}{'n':>8}{'%':>7}{'predicha':>11}{'observada':>11}{'gap':>9}")
    for row in table:
        print(
            f"    {row['bucket']:<14}{row['n']:>8,}{row['pct_of_total']:>7.1f}"
            f"{row['mean_predicted']:>11.4f}{row['observed_freq']:>11.4f}{row['gap']:>9.4f}"
        )


def _cmd_elo_evaluate(settings: Settings, args: argparse.Namespace) -> int:
    """Evalua el benchmark Elo sobre los conjuntos temporales."""
    split_names = tuple(args.splits)
    results = features_pipeline.evaluate_elo(
        settings, split_names=split_names, min_matches=args.min_matches
    )

    print("BENCHMARK ELO")
    print(f"  minimo de partidos previos por jugador: {args.min_matches}")
    print()
    for split_name in split_names:
        data = results["splits"][split_name]
        print(f"{split_name.upper()}  ({data['n_matches']:,} partidos)")
        _print_metrics_table(data["metrics"])
        print()
        if args.calibration:
            for model, table in data["calibration"].items():
                print(f"  calibracion: {model}")
                _print_calibration(table)
                print()

    consulted = splits_module.count_test_evaluations(settings.results_dir)
    if "test" in split_names:
        print(f"Evaluaciones sobre TEST registradas hasta ahora: {consulted}")
    return 0


def _cmd_features_build(settings: Settings, _args: argparse.Namespace) -> int:
    """Genera la tabla de features sin leakage temporal."""
    result = features_pipeline.build_feature_table(settings)
    print(f"Features generadas: {result.rows:,} partidos x {result.features} features")
    print(f"  destino: {result.destination}")
    print()
    print(f"  {'feature':<34}{'cobertura':>11}{'media':>12}{'desv.':>12}")
    print("  " + "-" * 67)
    for row in result.coverage.to_dict(orient="records"):
        print(
            f"  {row['feature']:<34}{row['coverage_pct']:>10.1f}%"
            f"{row['mean']:>12.4f}{row['std']:>12.4f}"
        )
    return 0


def _cmd_collector_start(settings: Settings, args: argparse.Namespace) -> int:
    """Arranca el collector de cuotas. Solo lectura, sin capacidad de apostar."""
    from edgecourt.db.connection import dsn_from_env, redact_dsn
    from edgecourt.market.auth import MissingCredentialsError
    from edgecourt.market.collector import Collector

    print("EdgeCourt collector — Betfair SOLO LECTURA")
    print(f"  modo           : {settings.betting_mode.upper()}")
    print(f"  apuestas reales: {'HABILITADAS' if REAL_BETTING_ENABLED else 'NO IMPLEMENTADAS'}")
    print(f"  intervalo      : {settings.collector_interval_seconds:.0f}s")
    print(f"  destino        : {redact_dsn(dsn_from_env(settings))}")
    print("  Parquet es formato de exportacion: 'edgecourt db export-parquet'")
    print()

    collector = Collector(settings)
    collector.install_signal_handlers()
    try:
        cycles = collector.run(max_cycles=args.max_cycles)
    except MissingCredentialsError as exc:
        print(f"No se puede arrancar: {exc}", file=sys.stderr)
        print("Consulta la seccion 'Betfair' del README para configurarlas.", file=sys.stderr)
        return 5
    print(f"Detenido tras {cycles} ciclo(s). run_id: {collector.run_id}")
    return 0


def _cmd_collector_status(settings: Settings, _args: argparse.Namespace) -> int:
    """Cobertura de observaciones recogidas, leida de PostgreSQL."""
    from edgecourt.db.repositories import snapshot_coverage

    with _db_connection(settings) as connection, connection.cursor() as cursor:
        rows = snapshot_coverage(cursor)

    if not rows:
        print("Todavia no hay observaciones recogidas.")
        return 0

    print("Cobertura de observaciones (fuente: PostgreSQL)")
    header = f"  {'hito':<10}{'obs':>8}{'mercados':>10}{'c/precios':>11}{'c/liquidez':>12}"
    print(header)
    print("  " + "-" * (len(header) - 2))
    total = con_precios = 0
    for row in rows:
        print(
            f"  {row['snapshot_label']:<10}{row['observaciones']:>8,}{row['mercados']:>10,}"
            f"{row['con_precios']:>11,}{row['con_liquidez']:>12,}"
        )
        total += row["observaciones"]
        con_precios += row["con_precios"]
    print()
    print(
        f"  Total: {total:,} observaciones, {con_precios:,} con precios "
        f"({100 * con_precios / total:.1f}%)"
    )
    print()
    print("Una observacion sin precios NO es un fallo: es un mercado abierto sin")
    print("libro todavia, y es el dato que permite medir cuando aparece la liquidez.")
    print("Los hitos perdidos nunca se rellenan a posteriori.")
    return 0


def _cmd_db_export_parquet(settings: Settings, args: argparse.Namespace) -> int:
    """Exporta observaciones de PostgreSQL a Parquet. No borra nada."""
    from datetime import date, timedelta

    from edgecourt.db.export_parquet import export_range, verify_export

    end = date.fromisoformat(args.to_date) if args.to_date else date.today() + timedelta(days=1)
    start = date.fromisoformat(args.from_date) if args.from_date else date(2000, 1, 1)

    print("Exportacion PostgreSQL -> Parquet")
    print(f"  rango  : [{start}, {end})")
    print(f"  destino: {settings.odds_dir}")
    print("  La exportacion NO elimina nada de PostgreSQL.")
    print()

    with _db_connection(settings) as connection:
        result = export_range(connection, settings.odds_dir, start=start, end=end)
        if result.rows == 0:
            print("  No hay observaciones en ese rango.")
            return 0

        print(f"  filas escritas        : {result.rows:,}")
        print(f"  observaciones         : {result.observations:,}")
        print(f"    con precios         : {result.with_prices:,}")
        print(f"    sin precios         : {result.without_prices:,}")
        print(f"  ficheros              : {len(result.files)}")
        print(f"  bytes                 : {result.bytes_written:,}")
        print(f"  sha256                : {result.sha256[:16]}...")
        print()

        check = verify_export(connection, settings.odds_dir, start=start, end=end)
        print("Verificacion:")
        print(f"  observaciones en la BD    : {check['observations_in_db']:,}")
        print(f"  observaciones en el fichero: {check['observations_in_file']:,}")
        if check["ok"]:
            print("  RESULTADO: la exportacion esta completa")
            return 0
        print("  RESULTADO: la exportacion NO cuadra", file=sys.stderr)
        return 4


def _cmd_train(settings: Settings, args: argparse.Namespace) -> int:
    """Entrena la regresion logistica y la compara con los benchmarks Elo."""
    from edgecourt.training import pipeline as training

    print("Entrenando regresion logistica")
    print(f"  TRAIN      : {splits_module.SPLITS['train'][0]}-{splits_module.SPLITS['train'][1]}")
    print(f"  VALIDATION : {splits_module.SPLITS['validation'][0]}")
    print(f"  ranura     : {args.slot}")
    print(f"  min. partidos previos: {args.min_matches}")
    print()

    result = training.train_logistic(settings, min_matches=args.min_matches, slot=args.slot)

    print(f"Modelo: {result.model_id}")
    print(f"  filas de entrenamiento: {result.n_train:,}")
    print(f"  C elegido (solo VALIDATION): {result.model.config.C}")
    print()
    print("  Busqueda de hiperparametros (VALIDATION)")
    print(f"    {'C':>8}{'Brier':>10}{'LogLoss':>10}{'ECE':>9}")
    for row in result.search.to_dict(orient="records"):
        print(f"    {row['C']:>8}{row['brier']:>10.5f}{row['log_loss']:>10.5f}{row['ece']:>9.4f}")
    print()
    print("  Coeficientes (escalados, top 10)")
    for row in result.coefficients.head(10).to_dict(orient="records"):
        print(f"    {row['feature']:<34}{row['coefficient_scaled']:>10.4f}")
    return 0


def _cmd_model_compare(settings: Settings, args: argparse.Namespace) -> int:
    """Compara el ultimo challenger con los benchmarks Elo."""
    from edgecourt.models.registry import latest_model
    from edgecourt.training import pipeline as training

    loaded = latest_model(settings.models_dir / args.slot)
    if loaded is None:
        print(f"No hay ningun modelo en la ranura '{args.slot}'.", file=sys.stderr)
        return 4
    model, manifest = loaded

    results = training.compare_against_elo(
        settings,
        model,
        split_names=tuple(args.splits),
        min_matches=args.min_matches,
        model_id=manifest.model_id,
    )

    print(f"COMPARACION — {manifest.model_id}")
    print(
        f"  entrenado con {manifest.train_first_year}-{manifest.train_last_year} "
        f"({manifest.n_train_rows:,} partidos)"
    )
    print(f"  minimo de partidos previos: {args.min_matches}")
    print()

    for split_name in args.splits:
        data = results["splits"][split_name]
        print(f"{split_name.upper()}  ({data['n_matches']:,} partidos)")
        header = (
            f"  {'modelo':<22}{'n':>8}{'Brier':>10}{'LogLoss':>10}"
            f"{'Acc':>9}{'AUC':>8}{'ECE':>8}{'vs Elo':>9}"
        )
        print(header)
        print("  " + "-" * (len(header) - 2))
        for row in data["metrics"]:
            print(
                f"  {row['model']:<22}{row['n']:>8,}{row['brier']:>10.5f}{row['log_loss']:>10.5f}"
                f"{row['accuracy']:>9.4f}{row['roc_auc']:>8.4f}{row['ece']:>8.4f}"
                f"{row['brier_skill_vs_elo']:>9.4f}"
            )
        print()
        if args.calibration:
            for name, table in data["calibration"].items():
                print(f"  calibracion: {name}")
                _print_calibration(table)
                print()
    return 0


def _cmd_betfair_check(settings: Settings, args: argparse.Namespace) -> int:
    """Verifica credenciales y acceso de lectura. NO escribe nada en disco."""
    from edgecourt.market.healthcheck import run_healthcheck

    print("VERIFICACION DE BETFAIR — solo lectura, sin escribir nada")
    print(f"  modo           : {settings.betting_mode.upper()}")
    print(f"  apuestas reales: {'HABILITADAS' if REAL_BETTING_ENABLED else 'NO IMPLEMENTADAS'}")
    print(f"  jurisdiccion   : {settings.betfair_jurisdiction}")
    print(f"  certificado    : {settings.betfair_cert_path}")
    print(f"  clave          : {settings.betfair_key_path}")
    print()

    report = run_healthcheck(settings, sample_size=args.sample)

    width = max(len(check.name) for check in report.checks) if report.checks else 20
    for check in report.checks:
        print(f"  [{check.symbol:^5}] {check.name:<{width}}  {check.detail}")

    if report.login_endpoint:
        print()
        print("  Endpoints utilizados:")
        print(f"    login   : {report.login_endpoint}")
        print(f"    betting : {report.betting_endpoint}")

    if report.sample:
        print()
        print("  Muestra de mercados visibles:")
        for line in report.sample:
            print(f"    - {line}")

    print()
    if report.ok:
        print("Todo correcto. Ya puedes arrancar el collector:")
        print("  uv run edgecourt collector start")
        return 0

    print("Hay comprobaciones fallidas. Revisa docs/BETFAIR_SETUP.md.", file=sys.stderr)
    return 5


def _db_connection(settings: Settings):
    """Abre conexion a PostgreSQL con un mensaje claro si falta configuracion."""
    from edgecourt.db.connection import connect, dsn_from_env

    return connect(dsn_from_env(settings))


def _cmd_db_migrate(settings: Settings, args: argparse.Namespace) -> int:
    """Aplica las migraciones pendientes."""
    from edgecourt.db.connection import dsn_from_env, redact_dsn, server_version
    from edgecourt.db.migrate import current_version, discover, migrate, pending

    dsn = dsn_from_env(settings)
    print(f"Base de datos: {redact_dsn(dsn)}")

    with _db_connection(settings) as connection:
        print(f"  servidor : {server_version(connection).split(',')[0]}")
        migrations = discover()
        to_apply = pending(connection, migrations)

        if args.dry_run:
            print(f"  version actual: {current_version(connection)}")
            if not to_apply:
                print("  sin migraciones pendientes")
                return 0
            print(f"  pendientes ({len(to_apply)}):")
            for migration in to_apply:
                print(f"    {migration.version:03d}  {migration.name}")
            return 0

        applied = migrate(connection)
        if not applied:
            print(f"  esquema al dia (version {current_version(connection)})")
            return 0
        for migration in applied:
            print(f"  aplicada {migration.version:03d}  {migration.name}")
        print(f"  version final: {current_version(connection)}")
    return 0


def _cmd_db_status(settings: Settings, _args: argparse.Namespace) -> int:
    """Estado del esquema y recuento de filas por tabla."""
    from edgecourt.db.connection import dsn_from_env, redact_dsn
    from edgecourt.db.migrate import current_version, discover, pending

    print(f"Base de datos: {redact_dsn(dsn_from_env(settings))}")
    with _db_connection(settings) as connection:
        version = current_version(connection)
        outstanding = pending(connection, discover())
        print(f"  version de esquema : {version}")
        print(f"  migraciones pendientes: {len(outstanding)}")
        print(f"  retencion configurada : {settings.retention_days} dias")
        print()

        tables = [
            "betfair_event",
            "betfair_market",
            "betfair_runner",
            "market_observation",
            "runner_price",
            "model_version",
            "prediction",
            "paper_bet",
            "bet_settlement",
            "match_result",
            "archive_run",
        ]
        print(f"  {'tabla':<22}{'filas':>12}")
        print("  " + "-" * 34)
        with connection.cursor() as cursor:
            for table in tables:
                try:
                    cursor.execute(f"SELECT count(*) AS n FROM {table}")
                    print(f"  {table:<22}{cursor.fetchone()['n']:>12,}")
                except Exception:  # noqa: BLE001 - tabla aun no creada
                    connection.rollback()
                    print(f"  {table:<22}{'(no existe)':>12}")
    return 0


def _cmd_db_import_parquet(settings: Settings, args: argparse.Namespace) -> int:
    """Migra los snapshots de Parquet a PostgreSQL. No destructivo."""
    from edgecourt.db.import_parquet import import_snapshots, verify_import

    print("Migracion de snapshots Parquet -> PostgreSQL")
    print(f"  origen : {settings.odds_dir}")
    print("  Los Parquet originales NO se modifican ni se borran.")
    print()

    with _db_connection(settings) as connection:
        report = import_snapshots(connection, settings.odds_dir)
        for key, value in report.as_dict().items():
            print(
                f"  {key:<28}: {value:>8,}" if isinstance(value, int) else f"  {key:<28}: {value}"
            )

        if report.skipped:
            print()
            print("  Filas omitidas:")
            for item in report.skipped[:10]:
                print(f"    - {item}")

        print()
        print("Verificacion:")
        result = verify_import(connection, settings.odds_dir)
        if not result.get("parquet_exists"):
            print("  no hay Parquet de snapshots que verificar")
            return 0
        print(f"  filas en Parquet        : {result['parquet_rows']:,}")
        print(f"  observaciones esperadas : {result['expected_observations']:,}")
        print(f"  observaciones en la BD  : {result['observations_in_db']:,}")
        print(f"  precios en la BD        : {result['runner_prices_in_db']:,}")
        if result["ok"]:
            print("  RESULTADO: todas las capturas del Parquet estan en PostgreSQL")
            return 0
        print(f"  RESULTADO: faltan {len(result['missing'])} capturas", file=sys.stderr)
        for item in result["missing"][:10]:
            print(f"    - {item}", file=sys.stderr)
        return 4


def _cmd_db_liquidity(settings: Settings, _args: argparse.Namespace) -> int:
    """Curva de aparicion de liquidez respecto a la hora de inicio."""
    from edgecourt.db.repositories import liquidity_emergence

    with _db_connection(settings) as connection, connection.cursor() as cursor:
        rows = liquidity_emergence(cursor)

    if not rows:
        print("Todavia no hay observaciones.")
        return 0

    print("Aparicion de liquidez respecto a market_start_time")
    print(
        f"  {'min. antes':>18}{'obs':>8}{'mercados':>10}{'%precios':>10}"
        f"{'%liquidez':>11}{'liq.mediana':>13}"
    )
    print("  " + "-" * 68)
    for row in rows:
        rango = f"{row['minutos_antes_min']:.0f}-{row['minutos_antes_max']:.0f}"
        print(
            f"  {rango:>18}{row['observaciones']:>8,}{row['mercados']:>10,}"
            f"{float(row['pct_con_precios']) * 100:>9.1f}%"
            f"{float(row['pct_con_liquidez']) * 100:>10.1f}%"
            f"{float(row['liquidez_mediana'] or 0):>13,.2f}"
        )
    print()
    print("Esta tabla es la base para decidir MINIMUM_LIQUIDITY con datos.")
    return 0


def _cmd_collector_health(settings: Settings, args: argparse.Namespace) -> int:
    """Estado de salud del collector. Apto para supervision desatendida."""
    from edgecourt.db.health import collect_health

    with _db_connection(settings) as connection:
        report = collect_health(connection, stale_after_minutes=args.stale_minutes)

    def _edad(momento, minutos):
        if momento is None:
            return "nunca"
        if minutos < 60:
            return f"{momento:%Y-%m-%d %H:%M:%S} UTC  (hace {minutos:.0f} min)"
        return f"{momento:%Y-%m-%d %H:%M:%S} UTC  (hace {minutos / 60:.1f} h)"

    print("SALUD DEL COLLECTOR")
    print(f"  estado                      : {'OK' if report.healthy else 'CON AVISOS'}")
    print(
        f"  ultima observacion          : "
        f"{_edad(report.last_observation_at, report.minutes_since_last_observation or 0)}"
    )
    print(
        f"  ultima observacion CON precios: "
        f"{_edad(report.last_priced_observation_at, report.minutes_since_last_priced or 0)}"
    )
    print()
    print(f"  observaciones totales       : {report.observations_total:,}")
    print(f"  observaciones ultimas 24h   : {report.observations_24h:,}")
    print(f"    de ellas con precios      : {report.priced_24h:,}")
    print(f"  mercados en catalogo        : {report.markets_total:,}")
    print(f"    proximos (sin empezar)    : {report.markets_upcoming:,}")
    print(f"  ejecuciones distintas (24h) : {report.runs_24h}")

    if report.warnings:
        print()
        print("  AVISOS:")
        for warning in report.warnings:
            print(f"    - {warning}")

    print()
    print("  Nota: una observacion sin precios no es un fallo. Si los mercados no")
    print("  tienen libro, registrar que estaban vacios es el comportamiento correcto.")

    return 0 if report.healthy else 1


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

    train = sub.add_parser("train", help="entrenar un modelo challenger")
    train.add_argument("--slot", default="challenger", choices=["challenger", "production"])
    train.add_argument("--min-matches", type=int, default=10, dest="min_matches")

    db = sub.add_parser("db", help="almacenamiento operativo en PostgreSQL")
    db_sub = db.add_subparsers(dest="subcommand", required=True)
    mig = db_sub.add_parser("migrate", help="aplicar migraciones pendientes")
    mig.add_argument("--dry-run", action="store_true", help="solo mostrar que se aplicaria")
    db_sub.add_parser("status", help="version de esquema y recuento de filas")
    db_sub.add_parser("import-parquet", help="migrar snapshots Parquet a PostgreSQL")
    db_sub.add_parser("liquidity", help="curva de aparicion de liquidez")
    exp = db_sub.add_parser("export-parquet", help="exportar observaciones a Parquet")
    exp.add_argument("--from-date", dest="from_date", default=None, help="AAAA-MM-DD inclusive")
    exp.add_argument("--to-date", dest="to_date", default=None, help="AAAA-MM-DD exclusivo")

    betfair = sub.add_parser("betfair", help="utilidades de la capa Betfair (solo lectura)")
    betfair_sub = betfair.add_subparsers(dest="subcommand", required=True)
    check_cmd = betfair_sub.add_parser(
        "check", help="verificar credenciales y acceso de lectura, sin escribir nada"
    )
    check_cmd.add_argument(
        "--sample", type=int, default=5, help="mercados de muestra a consultar (por defecto 5)"
    )

    collector = sub.add_parser("collector", help="collector de cuotas de Betfair (solo lectura)")
    collector_sub = collector.add_subparsers(dest="subcommand", required=True)
    start = collector_sub.add_parser("start", help="arrancar el collector")
    start.add_argument(
        "--max-cycles",
        type=int,
        default=None,
        dest="max_cycles",
        help="terminar tras N ciclos (por defecto corre hasta recibir SIGTERM)",
    )
    collector_sub.add_parser("status", help="cobertura de observaciones recogidas")
    health = collector_sub.add_parser("health", help="estado de salud para supervision")
    health.add_argument(
        "--stale-minutes",
        type=float,
        default=180.0,
        dest="stale_minutes",
        help="minutos sin observaciones tras los que se avisa (por defecto 180)",
    )

    feats = sub.add_parser("features", help="generacion de features")
    feats_sub = feats.add_subparsers(dest="subcommand", required=True)
    feats_sub.add_parser("build", help="generar la tabla de features")

    model = sub.add_parser("model", help="operaciones sobre modelos")
    model_sub = model.add_subparsers(dest="subcommand", required=True)
    compare = model_sub.add_parser("compare", help="comparar un modelo con los benchmarks Elo")
    compare.add_argument("--slot", default="challenger", choices=["challenger", "production"])
    compare.add_argument(
        "--splits",
        nargs="+",
        default=["validation", "test"],
        choices=["train", "validation", "test", "live"],
    )
    compare.add_argument("--min-matches", type=int, default=10, dest="min_matches")
    compare.add_argument("--no-calibration", action="store_false", dest="calibration")

    elo = sub.add_parser("elo", help="Elo global y por superficie")
    elo_sub = elo.add_subparsers(dest="subcommand", required=True)
    elo_sub.add_parser("build", help="calcular el Elo sobre todo el historico")

    evaluate = elo_sub.add_parser("evaluate", help="evaluar el benchmark Elo")
    evaluate.add_argument(
        "--splits",
        nargs="+",
        default=["validation", "test"],
        choices=["train", "validation", "test", "live"],
    )
    evaluate.add_argument("--min-matches", type=int, default=10, dest="min_matches")
    evaluate.add_argument("--no-calibration", action="store_false", dest="calibration")

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
    # `console=None` deja que se decida solo: activado bajo systemd para que los
    # registros lleguen al journal, desactivado en uso interactivo.
    setup_logging(
        logs_dir=settings.logs_dir,
        level=settings.log_level,
        secrets=secrets_from_settings(settings),
        console=None,
    )

    key = args.command
    if getattr(args, "subcommand", None):
        key = f"{args.command} {args.subcommand}"

    handlers = {
        "status": _cmd_status,
        "data fetch": _cmd_data_fetch,
        "data import": _cmd_data_import,
        "data check": _cmd_data_check,
        "train": _cmd_train,
        "model compare": _cmd_model_compare,
        "db migrate": _cmd_db_migrate,
        "db status": _cmd_db_status,
        "db import-parquet": _cmd_db_import_parquet,
        "db liquidity": _cmd_db_liquidity,
        "db export-parquet": _cmd_db_export_parquet,
        "betfair check": _cmd_betfair_check,
        "collector start": _cmd_collector_start,
        "collector status": _cmd_collector_status,
        "collector health": _cmd_collector_health,
        "features build": _cmd_features_build,
        "elo build": _cmd_elo_build,
        "elo evaluate": _cmd_elo_evaluate,
    }
    if key in handlers:
        return handlers[key](settings, args)
    if key in PENDING:
        return _cmd_pending(key)

    parser.error(f"comando desconocido: {key}")
    return 1  # pragma: no cover - parser.error termina el proceso


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
