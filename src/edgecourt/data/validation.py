"""Validacion de integridad de `match_facts`.

Estas comprobaciones existen porque la fuente de referencia del sector
desaparecio y la fuente viva tiene procedencia mixta y no auditable
(docs/DATA.md). Cuando no hay un original contra el que contrastar, la
integridad hay que deducirla de la coherencia interna de los datos.

Dos niveles:

* `ERROR`   - la fila es inutilizable; se cuenta y se puede descartar.
* `WARNING` - sospechoso pero no invalidante; se reporta y se vigila.

La validacion **no lanza excepciones por datos sucios**: devuelve un informe. Un
dataset historico real siempre tiene ruido, y abortar la ingesta por una fila
mal formada de 1997 seria peor que registrarla.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import pandas as pd

from edgecourt.data import schema
from edgecourt.logging_setup import get_logger

log = get_logger("data.validation")

Severity = Literal["ERROR", "WARNING"]

# Rangos plausibles. Generosos a proposito: buscamos imposibles, no atipicos.
AGE_RANGE = (14.0, 55.0)
HEIGHT_RANGE = (150, 220)
MINUTES_RANGE = (1, 400)
RANK_MAX = 3000


@dataclass(frozen=True, slots=True)
class Issue:
    """Un problema detectado, con su recuento."""

    severity: Severity
    code: str
    message: str
    count: int

    def __str__(self) -> str:
        return f"[{self.severity}] {self.code}: {self.message} ({self.count} filas)"


@dataclass(slots=True)
class ValidationReport:
    """Resultado de validar un dataset."""

    rows: int
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "ERROR"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "WARNING"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def add(self, severity: Severity, code: str, message: str, mask: pd.Series) -> None:
        count = int(mask.fillna(False).sum())
        if count:
            self.issues.append(Issue(severity, code, message, count))

    def summary(self) -> str:
        if not self.issues:
            return f"{self.rows} filas validadas sin incidencias."
        lines = [
            f"{self.rows} filas validadas: {len(self.errors)} errores, {len(self.warnings)} avisos."
        ]
        lines += [f"  {issue}" for issue in self.issues]
        return "\n".join(lines)


def _le(left: pd.Series, right: pd.Series) -> pd.Series:
    """`left > right` solo donde ambos existen (los nulos no son violaciones)."""
    both = left.notna() & right.notna()
    return both & (left > right)


def validate_match_facts(df: pd.DataFrame) -> ValidationReport:
    """Comprueba la coherencia interna del dataset canonico."""
    report = ValidationReport(rows=len(df))

    # --- Estructura ----------------------------------------------------------
    missing_columns = set(schema.MATCH_FACTS_COLUMNS) - set(df.columns)
    if missing_columns:
        raise ValueError(f"Faltan columnas del esquema canonico: {sorted(missing_columns)}")

    for column in schema.REQUIRED_NON_NULL:
        report.add("ERROR", "null_obligatorio", f"'{column}' nulo", df[column].isna())

    report.add("ERROR", "match_id_duplicado", "match_id repetido", df["match_id"].duplicated())

    report.add(
        "ERROR",
        "mismo_jugador",
        "player_a_id == player_b_id",
        df["player_a_id"].notna() & (df["player_a_id"] == df["player_b_id"]),
    )

    # --- Vocabularios --------------------------------------------------------
    report.add(
        "WARNING",
        "superficie_desconocida",
        "surface fuera del vocabulario",
        df["surface"].notna() & ~df["surface"].isin(schema.SURFACES),
    )
    report.add(
        "WARNING",
        "ronda_desconocida",
        "round fuera del vocabulario",
        df["round"].notna() & ~df["round"].isin(schema.ROUNDS),
    )
    report.add(
        "ERROR",
        "estado_invalido",
        "completion_status fuera del vocabulario",
        ~df["completion_status"].isin(schema.COMPLETION_STATUSES),
    )
    report.add("ERROR", "target_invalido", "target distinto de 0/1", ~df["target"].isin([0, 1]))

    # --- Coherencia aritmetica de las estadisticas ---------------------------
    # Un fallo aqui indica corrupcion de la fuente, no un partido raro.
    for side in ("a", "b"):
        svpt = df[f"raw_{side}_svpt"]
        first_in = df[f"raw_{side}_first_in"]
        report.add("ERROR", "stats_first_in", f"raw_{side}_first_in > svpt", _le(first_in, svpt))
        report.add(
            "ERROR",
            "stats_first_won",
            f"raw_{side}_first_won > first_in",
            _le(df[f"raw_{side}_first_won"], first_in),
        )
        report.add(
            "ERROR",
            "stats_second_won",
            f"raw_{side}_second_won > segundos servicios",
            _le(df[f"raw_{side}_second_won"], svpt - first_in),
        )
        report.add(
            "ERROR",
            "stats_bp",
            f"raw_{side}_bp_saved > bp_faced",
            _le(df[f"raw_{side}_bp_saved"], df[f"raw_{side}_bp_faced"]),
        )
        report.add(
            "ERROR", "stats_aces", f"raw_{side}_aces > svpt", _le(df[f"raw_{side}_aces"], svpt)
        )
        report.add("ERROR", "stats_dfs", f"raw_{side}_dfs > svpt", _le(df[f"raw_{side}_dfs"], svpt))
        report.add(
            "WARNING",
            "stats_negativas",
            f"estadisticas negativas en lado {side}",
            pd.concat(
                [df[c] < 0 for c in schema.RAW_STAT_COLUMNS if c.startswith(f"raw_{side}_")], axis=1
            ).any(axis=1),
        )

    # --- Rangos plausibles ---------------------------------------------------
    for side in ("a", "b"):
        age = df[f"player_{side}_age"]
        report.add(
            "WARNING",
            "edad_implausible",
            f"player_{side}_age fuera de {AGE_RANGE}",
            age.notna() & ((age < AGE_RANGE[0]) | (age > AGE_RANGE[1])),
        )
        height = df[f"player_{side}_height"]
        report.add(
            "WARNING",
            "altura_implausible",
            f"player_{side}_height fuera de {HEIGHT_RANGE}",
            height.notna() & ((height < HEIGHT_RANGE[0]) | (height > HEIGHT_RANGE[1])),
        )
        rank = df[f"player_{side}_rank"]
        report.add(
            "WARNING",
            "ranking_implausible",
            f"player_{side}_rank fuera de (1, {RANK_MAX})",
            rank.notna() & ((rank < 1) | (rank > RANK_MAX)),
        )

    minutes = df["minutes"]
    report.add(
        "WARNING",
        "duracion_implausible",
        f"minutes fuera de {MINUTES_RANGE}",
        minutes.notna() & ((minutes < MINUTES_RANGE[0]) | (minutes > MINUTES_RANGE[1])),
    )

    # --- Coherencia de sets --------------------------------------------------
    completed = df["completion_status"] == "completed"
    sets_total = df["sets_a"].fillna(0) + df["sets_b"].fillna(0)
    report.add(
        "WARNING",
        "sets_implausibles",
        "partido completo con numero de sets imposible",
        completed & df["sets_a"].notna() & ((sets_total < 2) | (sets_total > 5)),
    )
    # El ganador es, por construccion, quien tiene mas sets.
    winner_sets = df["sets_a"].where(df["target"] == 1, df["sets_b"])
    loser_sets = df["sets_b"].where(df["target"] == 1, df["sets_a"])
    report.add(
        "ERROR",
        "ganador_incoherente",
        "el ganador no tiene mas sets que el perdedor (partido completo)",
        completed & winner_sets.notna() & loser_sets.notna() & (winner_sets <= loser_sets),
    )

    # --- Equilibrio del target ----------------------------------------------
    if len(df) >= 1000:
        mean = float(df["target"].mean())
        if abs(mean - 0.5) > 0.02:
            report.issues.append(
                Issue(
                    "ERROR",
                    "target_desequilibrado",
                    f"P(target=1) = {mean:.4f}, deberia ser ~0.5 (fallo de aleatorizacion A/B)",
                    len(df),
                )
            )

    log.info(
        "validacion completada",
        extra={"rows": len(df), "errors": len(report.errors), "warnings": len(report.warnings)},
    )
    return report


def year_coverage(df: pd.DataFrame) -> pd.DataFrame:
    """Partidos por ano y cobertura de campos clave. Detecta huecos y saltos."""
    stats_columns = [c for c in schema.RAW_STAT_COLUMNS if c in df.columns]
    frame = pd.DataFrame(
        {
            "year": df["year"],
            "has_stats": df[stats_columns].notna().any(axis=1),
            "has_rank": df["player_a_rank"].notna() & df["player_b_rank"].notna(),
            "has_indoor": df["indoor"].notna(),
            "has_minutes": df["minutes"].notna(),
        }
    )
    coverage = frame.groupby("year", dropna=True).agg(
        matches=("has_stats", "size"),
        pct_stats=("has_stats", lambda s: round(100 * s.mean(), 1)),
        pct_rank=("has_rank", lambda s: round(100 * s.mean(), 1)),
        pct_indoor=("has_indoor", lambda s: round(100 * s.mean(), 1)),
        pct_minutes=("has_minutes", lambda s: round(100 * s.mean(), 1)),
    )
    return coverage.reset_index()


def _normalise_name(series: pd.Series) -> pd.Series:
    """Normaliza nombres para comparar entre fuentes con formatos distintos."""
    return (
        series.fillna("")
        .astype(str)
        .str.normalize("NFKD")
        .str.encode("ascii", errors="ignore")
        .str.decode("ascii")
        .str.lower()
        .str.replace(r"[^a-z ]", "", regex=True)
        .str.split()
        .map(lambda parts: " ".join(sorted(parts)))
    )


def _key_compact(df: pd.DataFrame) -> pd.Series:
    """Clave de emparejamiento tolerante al espaciado de los nombres."""
    a = _normalise_name(df["player_a_name"]).str.replace(" ", "", regex=False)
    b = _normalise_name(df["player_b_name"]).str.replace(" ", "", regex=False)
    pair = pd.concat([a, b], axis=1).min(axis=1) + "|" + pd.concat([a, b], axis=1).max(axis=1)
    return df["date"].dt.strftime("%Y%m%d").fillna("") + "|" + pair


def cross_check(primary: pd.DataFrame, reference: pd.DataFrame) -> dict[str, object]:
    """Contrasta la fuente primaria contra la de referencia en el solapamiento.

    Los identificadores de jugador **no son compatibles** entre fuentes
    (TennisMyLife usa codigos ATP alfanumericos; el formato Sackmann, enteros),
    asi que el emparejamiento se hace por fecha, superficie y nombres
    normalizados y ordenados alfabeticamente -lo que ademas hace la comparacion
    insensible al lado A/B, que en cada fuente se sortea por separado.

    Devuelve tasas, no un veredicto: la interpretacion es humana.
    """
    if primary.empty or reference.empty:
        return {
            "overlap_years": [],
            "matched": 0,
            "primary_only": len(primary),
            "reference_only": len(reference),
        }

    def _key(df: pd.DataFrame) -> pd.Series:
        names = pd.DataFrame(
            {"a": _normalise_name(df["player_a_name"]), "b": _normalise_name(df["player_b_name"])}
        )
        pair = names.min(axis=1) + "|" + names.max(axis=1)
        return df["date"].dt.strftime("%Y%m%d").fillna("") + "|" + pair

    years = sorted(set(primary["year"].dropna()) & set(reference["year"].dropna()))
    if not years:
        return {
            "overlap_years": [],
            "matched": 0,
            "primary_only": len(primary),
            "reference_only": len(reference),
        }

    p = primary[primary["year"].isin(years)].copy()
    r = reference[reference["year"].isin(years)].copy()
    p["_key"] = _key(p)
    r["_key"] = _key(r)

    p_keys, r_keys = set(p["_key"]), set(r["_key"])
    common = p_keys & r_keys

    # Segunda medida con los nombres colapsados (sin espacios). Las dos fuentes
    # difieren en el espaciado de apellidos compuestos y transliteraciones
    # ("ramos vinolas" / "ramosvinolas", "chun hsin" / "chunhsin"), lo que hunde
    # artificialmente la tasa de emparejamiento estricta. La diferencia entre
    # ambas tasas mide cuanto del desajuste es de nomenclatura y no de cobertura.
    p_compact = set(_key_compact(p))
    r_compact = set(_key_compact(r))
    common_compact = p_compact & r_compact

    # Concordancia del ganador en los partidos emparejados.
    p_winner = p.drop_duplicates("_key").set_index("_key")
    r_winner = r.drop_duplicates("_key").set_index("_key")
    shared = sorted(common)
    if shared:
        p_win_name = _normalise_name(
            p_winner.loc[shared, "player_a_name"].where(
                p_winner.loc[shared, "target"] == 1, p_winner.loc[shared, "player_b_name"]
            )
        )
        r_win_name = _normalise_name(
            r_winner.loc[shared, "player_a_name"].where(
                r_winner.loc[shared, "target"] == 1, r_winner.loc[shared, "player_b_name"]
            )
        )
        winner_agreement = float((p_win_name.to_numpy() == r_win_name.to_numpy()).mean())
    else:
        winner_agreement = float("nan")

    return {
        "overlap_years": [int(y) for y in years],
        "primary_rows": len(p),
        "reference_rows": len(r),
        "matched": len(common),
        "match_rate_primary": round(100 * len(common) / max(len(p_keys), 1), 2),
        "match_rate_reference": round(100 * len(common) / max(len(r_keys), 1), 2),
        "matched_compact": len(common_compact),
        "match_rate_primary_compact": round(100 * len(common_compact) / max(len(p_compact), 1), 2),
        "primary_only": len(p_keys - r_keys),
        "reference_only": len(r_keys - p_keys),
        "winner_agreement_pct": round(100 * winner_agreement, 2) if shared else None,
    }
