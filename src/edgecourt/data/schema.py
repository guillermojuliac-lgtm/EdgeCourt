"""Esquema canonico `match_facts`.

Un partido, una fila, inmutable. Este modulo define **solo** la forma de los
datos; la transformacion vive en `ingest.py` y las comprobaciones en
`validation.py`.

Convencion critica (ver docs/DATA.md): las columnas con prefijo `raw_` contienen
estadisticas **del propio partido**. Son legitimas en `match_facts` -son un
hecho historico- pero usarlas como feature del partido que se predice es data
leakage directo. `FORBIDDEN_AS_FEATURE` las enumera y `features/` solo puede
consumirlas a traves de agregacion temporal desplazada.
"""

from __future__ import annotations

from typing import Final

# --- Vocabularios controlados ------------------------------------------------

# Carpet desaparecio del circuito hacia 2010 pero existe en el historico.
SURFACES: Final[tuple[str, ...]] = ("hard", "clay", "grass", "carpet")

# Superficies con Elo propio (brief §6). Carpet queda fuera: muestra residual y
# circuito extinto, un Elo especifico seria ruido.
ELO_SURFACES: Final[tuple[str, ...]] = ("hard", "clay", "grass")

TOURNEY_LEVELS: Final[tuple[str, ...]] = (
    "grand_slam",
    "masters",
    "atp500",
    "atp250",
    "finals",
    "olympics",
    "davis_cup",
    "other",
)

# Mapa desde los codigos crudos (formato Sackmann/TML) al vocabulario canonico.
# TML mezcla el esquema antiguo (A = cualquier torneo ATP) con el moderno
# (250/500), asi que ambos deben contemplarse.
RAW_LEVEL_MAP: Final[dict[str, str]] = {
    "G": "grand_slam",
    "M": "masters",
    "500": "atp500",
    "250": "atp250",
    "F": "finals",
    "O": "olympics",
    "D": "davis_cup",
    "A": "other",
    "C": "other",
}

ROUNDS: Final[tuple[str, ...]] = (
    "R128",
    "R64",
    "R32",
    "R16",
    "RR",
    "QF",
    "SF",
    "BR",
    "F",
)

# Orden cronologico dentro de un torneo. Es parte de `order_key` y, por tanto,
# determina el orden en que el Elo procesa los partidos (ver R2 del plan).
# RR (round robin) precede a las semifinales; BR (bronze) es simultaneo a la final.
ROUND_ORDER: Final[dict[str, int]] = {
    "R128": 1,
    "R64": 2,
    "R32": 3,
    "R16": 4,
    "RR": 4,
    "QF": 5,
    "SF": 6,
    "BR": 7,
    "F": 7,
}

COMPLETION_STATUSES: Final[tuple[str, ...]] = (
    "completed",
    "retired",
    "walkover",
    "defaulted",
    "unknown",
)

# --- Columnas ----------------------------------------------------------------

IDENTITY_COLUMNS: Final[tuple[str, ...]] = (
    "match_id",
    "date",
    "year",
    "tourney_id",
    "tourney_name",
    "tourney_level",
    "surface",
    "indoor",
    "round",
    "round_order",
    "match_num",
    "best_of",
)


def _player_columns(side: str) -> tuple[str, ...]:
    return (
        f"player_{side}_id",
        f"player_{side}_name",
        f"player_{side}_rank",
        f"player_{side}_rank_points",
        f"player_{side}_age",
        f"player_{side}_hand",
        f"player_{side}_height",
    )


PLAYER_COLUMNS: Final[tuple[str, ...]] = _player_columns("a") + _player_columns("b")

OUTCOME_COLUMNS: Final[tuple[str, ...]] = (
    "target",
    "score",
    "sets_a",
    "sets_b",
    "minutes",
    "completion_status",
)

# Estadisticas del propio partido. PROHIBIDAS como feature directa.
RAW_STAT_SUFFIXES: Final[tuple[str, ...]] = (
    "aces",
    "dfs",
    "svpt",
    "first_in",
    "first_won",
    "second_won",
    "sv_games",
    "bp_saved",
    "bp_faced",
)

RAW_STAT_COLUMNS: Final[tuple[str, ...]] = tuple(
    f"raw_{side}_{suffix}" for side in ("a", "b") for suffix in RAW_STAT_SUFFIXES
)

FORBIDDEN_AS_FEATURE: Final[frozenset[str]] = frozenset(
    RAW_STAT_COLUMNS + ("target", "score", "sets_a", "sets_b", "minutes", "completion_status")
)

MATCH_FACTS_COLUMNS: Final[tuple[str, ...]] = (
    IDENTITY_COLUMNS + PLAYER_COLUMNS + OUTCOME_COLUMNS + RAW_STAT_COLUMNS
)

# --- Tipos -------------------------------------------------------------------

# Se usan tipos nullable de pandas (Int16/Float32/boolean) porque los datos
# historicos tienen ausencias legitimas: un ranking inexistente no es un 0.
DTYPES: Final[dict[str, str]] = (
    {
        "match_id": "string",
        "date": "datetime64[ns]",
        "year": "int16",
        "tourney_id": "string",
        "tourney_name": "string",
        "tourney_level": "string",
        "surface": "string",
        "indoor": "boolean",
        "round": "string",
        "round_order": "int8",
        "match_num": "Int32",
        "best_of": "Int8",
        "target": "int8",
        "score": "string",
        "sets_a": "Int8",
        "sets_b": "Int8",
        "minutes": "Int16",
        "completion_status": "string",
    }
    | {
        f"player_{s}_{f}": t
        for s in ("a", "b")
        for f, t in (
            ("id", "string"),
            ("name", "string"),
            ("rank", "Int16"),
            ("rank_points", "Int32"),
            ("age", "Float32"),
            ("hand", "string"),
            ("height", "Int16"),
        )
    }
    | {col: "Int16" for col in RAW_STAT_COLUMNS}
)

# Columnas sin las que una fila no es utilizable.
REQUIRED_NON_NULL: Final[tuple[str, ...]] = (
    "match_id",
    "date",
    "tourney_id",
    "surface",
    "round",
    "player_a_id",
    "player_b_id",
    "target",
)

# Ano a partir del cual las estadisticas de servicio/resto tienen cobertura
# (~88% en adelante; antes de 1991 es 0%). Verificado el 2026-09-18.
FIRST_YEAR_WITH_STATS: Final[int] = 1991
