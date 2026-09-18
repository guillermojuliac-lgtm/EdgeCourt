"""Ingesta: CSV crudo -> `match_facts` canonico.

Tres transformaciones no triviales ocurren aqui, y las tres existen para
proteger la validez estadistica del proyecto:

1. **Aleatorizacion A/B** (riesgo R3). Los CSV crudos traen columnas
   `winner_*` / `loser_*`. Copiarlas tal cual haria que `target` fuese siempre 1
   y el modelo aprenderia el orden de las columnas en lugar de tenis. La
   asignacion se decide por hash determinista del `match_id`: reproducible entre
   ejecuciones y equilibrada.

2. **Clave de orden total** (riesgo R2). Dos partidos del mismo jugador en la
   misma fecha podrian ordenarse de forma que el Elo de uno incorpore el
   resultado del otro. `(date, tourney_id, round_order, match_num)` define un
   orden determinista y es la unica clave que el actualizador de Elo puede usar.

3. **Clasificacion de finalizacion** (riesgo R9). Un abandono en el primer set
   no es un resultado predecible: se marca para poder excluirlo despues.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pandas as pd

from edgecourt.data import schema
from edgecourt.logging_setup import get_logger

log = get_logger("data.ingest")

# Columnas del formato crudo (Sackmann / TennisMyLife).
_RAW_STAT_MAP: dict[str, str] = {
    "ace": "aces",
    "df": "dfs",
    "svpt": "svpt",
    "1stIn": "first_in",
    "1stWon": "first_won",
    "2ndWon": "second_won",
    "SvGms": "sv_games",
    "bpSaved": "bp_saved",
    "bpFaced": "bp_faced",
}

_SCORE_SPECIAL = re.compile(r"\b(RET|W/O|WO|DEF|ABD|ABN|Walkover|Default)\b", re.IGNORECASE)
_SET_TOKEN = re.compile(r"^(\d+)-(\d+)(?:\(\d+\))?$")


def _build_match_id(df: pd.DataFrame) -> pd.Series:
    """Identificador estable y unico de un partido.

    No se usa `tourney_id + match_num`: en la fuente primaria `match_num` esta
    vacio en cientos de partidos reales (todo Cincinnati 2025, por ejemplo), lo
    que colapsaria esos partidos en un unico id nulo y los haria desaparecer al
    deduplicar. Ademas se han observado colisiones puntuales de esa pareja.

    En su lugar se usa un hash de contenido sobre los campos que identifican un
    partido de forma natural -torneo, fecha, ronda y los dos jugadores-,
    prefijado con el torneo para que el identificador siga siendo legible. Es
    determinista entre ejecuciones, asi que el dataset es reproducible.

    Dos filas con el mismo identificador son, por definicion, el mismo partido
    repetido, y deduplicarlas es correcto.
    """
    parts = [
        df["tourney_id"].fillna("").astype(str).str.strip(),
        df["tourney_date"].fillna("").astype(str).str.strip(),
        df.get("round", pd.Series("", index=df.index)).fillna("").astype(str).str.strip(),
        df["winner_id"].fillna("").astype(str).str.strip(),
        df["loser_id"].fillna("").astype(str).str.strip(),
    ]
    payload = parts[0].str.cat(parts[1:], sep="|")
    digest = payload.map(
        lambda value: hashlib.blake2b(value.encode("utf-8"), digest_size=6).hexdigest()
    )
    return (parts[0].where(parts[0] != "", "unknown") + "-" + digest).astype("string")


def _assign_side_a_to_winner(match_ids: pd.Series) -> pd.Series:
    """Decide, por partido, si el ganador ocupa el lado A.

    Hash determinista: la misma entrada produce siempre el mismo reparto, de modo
    que el dataset es reproducible y las ejecuciones son comparables. Se usa
    blake2b truncado a un bit; `hash()` de Python no sirve porque esta
    aleatorizado entre procesos.
    """
    return match_ids.map(
        lambda mid: hashlib.blake2b(str(mid).encode("utf-8"), digest_size=8).digest()[0] & 1 == 1
    )


def _classify_completion(score: pd.Series) -> pd.Series:
    """Clasifica como termino el partido a partir del texto del marcador."""
    text = score.fillna("").astype(str).str.strip()
    status = pd.Series("completed", index=text.index, dtype="object")

    status[text == ""] = "unknown"
    special = text.str.extract(_SCORE_SPECIAL, expand=False)
    normalised = special.fillna("").str.upper()
    status[normalised == "RET"] = "retired"
    status[normalised.isin(["W/O", "WO", "WALKOVER"])] = "walkover"
    status[normalised.isin(["DEF", "DEFAULT"])] = "defaulted"
    status[normalised.isin(["ABD", "ABN"])] = "unknown"
    return status


def _count_sets(score: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Cuenta sets ganados por el ganador y por el perdedor.

    Solo se cuentan los sets completos y bien formados; los marcadores truncados
    por abandono se cuentan hasta donde son legibles.
    """

    def _count(raw: object) -> tuple[int | None, int | None]:
        if not isinstance(raw, str) or not raw.strip():
            return None, None
        winner_sets = loser_sets = 0
        for token in raw.split():
            match = _SET_TOKEN.match(token)
            if not match:
                continue
            a, b = int(match.group(1)), int(match.group(2))
            if a > b:
                winner_sets += 1
            elif b > a:
                loser_sets += 1
        if winner_sets == 0 and loser_sets == 0:
            return None, None
        return winner_sets, loser_sets

    counted = score.map(_count)
    return (
        counted.map(lambda x: x[0]).astype("Int8"),
        counted.map(lambda x: x[1]).astype("Int8"),
    )


def _normalise_surface(raw: pd.Series) -> pd.Series:
    surface = raw.fillna("").astype(str).str.strip().str.lower()
    return surface.where(surface.isin(schema.SURFACES), other=pd.NA)


def _normalise_indoor(raw: pd.Series | None, index: pd.Index) -> pd.Series:
    """`indoor` viene como 'I'/'O' en TennisMyLife y no existe en Sackmann."""
    if raw is None:
        return pd.Series(pd.NA, index=index, dtype="boolean")
    text = raw.fillna("").astype(str).str.strip().str.upper()
    result = pd.Series(pd.NA, index=index, dtype="boolean")
    result[text == "I"] = True
    result[text == "O"] = False
    return result


def _parse_date(raw: pd.Series) -> pd.Series:
    """`tourney_date` viene como entero AAAAMMDD.

    Es la fecha de **inicio del torneo**, no la del partido: el formato no ofrece
    nada mejor. Consecuencia asumida y documentada: dentro de un torneo, el orden
    lo aporta `round_order`, no la fecha.
    """
    return pd.to_datetime(raw.astype("string").str.strip(), format="%Y%m%d", errors="coerce")


def normalise_raw_matches(raw: pd.DataFrame, *, source: str) -> pd.DataFrame:
    """Convierte un DataFrame crudo en `match_facts` canonico."""
    df = raw.copy()
    df.columns = [c.strip() for c in df.columns]

    required = {"tourney_id", "match_num", "winner_id", "loser_id", "tourney_date"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Faltan columnas obligatorias en el origen: {sorted(missing)}")

    out = pd.DataFrame(index=df.index)

    # --- Identidad -----------------------------------------------------------
    out["match_id"] = _build_match_id(df)
    out["date"] = _parse_date(df["tourney_date"])
    out["year"] = out["date"].dt.year.astype("Int16")
    out["tourney_id"] = df["tourney_id"].astype("string").str.strip()
    out["tourney_name"] = df.get("tourney_name", pd.NA)
    out["tourney_level"] = (
        df.get("tourney_level", pd.Series(pd.NA, index=df.index))
        .astype("string")
        .str.strip()
        .map(schema.RAW_LEVEL_MAP)
        .fillna("other")
    )
    out["surface"] = _normalise_surface(df.get("surface", pd.Series(pd.NA, index=df.index)))
    out["indoor"] = _normalise_indoor(df.get("indoor"), df.index)
    out["round"] = df.get("round", pd.Series(pd.NA, index=df.index)).astype("string").str.strip()
    out["round_order"] = out["round"].map(schema.ROUND_ORDER).fillna(0).astype("int8")
    out["match_num"] = pd.to_numeric(df["match_num"], errors="coerce").astype("Int32")
    out["best_of"] = pd.to_numeric(df.get("best_of"), errors="coerce").astype("Int8")

    # --- Aleatorizacion A/B --------------------------------------------------
    winner_is_a = _assign_side_a_to_winner(out["match_id"])
    out["target"] = winner_is_a.astype("int8")

    def _pick(winner_col: str, loser_col: str) -> pd.Series:
        """Devuelve el valor del lado A (ganador o perdedor segun el sorteo)."""
        w = df.get(winner_col)
        loser = df.get(loser_col)
        if w is None or loser is None:
            return pd.Series(pd.NA, index=df.index)
        return w.where(winner_is_a, loser)

    def _pick_b(winner_col: str, loser_col: str) -> pd.Series:
        w = df.get(winner_col)
        loser = df.get(loser_col)
        if w is None or loser is None:
            return pd.Series(pd.NA, index=df.index)
        return loser.where(winner_is_a, w)

    player_fields = (
        ("id", "winner_id", "loser_id"),
        ("name", "winner_name", "loser_name"),
        ("rank", "winner_rank", "loser_rank"),
        ("rank_points", "winner_rank_points", "loser_rank_points"),
        ("age", "winner_age", "loser_age"),
        ("hand", "winner_hand", "loser_hand"),
        ("height", "winner_ht", "loser_ht"),
    )
    for field, wcol, lcol in player_fields:
        out[f"player_a_{field}"] = _pick(wcol, lcol)
        out[f"player_b_{field}"] = _pick_b(wcol, lcol)

    # --- Resultado -----------------------------------------------------------
    out["score"] = df.get("score", pd.Series(pd.NA, index=df.index)).astype("string")
    out["completion_status"] = _classify_completion(out["score"])
    winner_sets, loser_sets = _count_sets(out["score"])
    out["sets_a"] = winner_sets.where(winner_is_a, loser_sets)
    out["sets_b"] = loser_sets.where(winner_is_a, winner_sets)
    out["minutes"] = pd.to_numeric(df.get("minutes"), errors="coerce").astype("Int16")

    # --- Estadisticas del partido (prohibidas como feature) ------------------
    for raw_suffix, canonical in _RAW_STAT_MAP.items():
        w_col, l_col = f"w_{raw_suffix}", f"l_{raw_suffix}"
        w_values = pd.to_numeric(df.get(w_col), errors="coerce")
        l_values = pd.to_numeric(df.get(l_col), errors="coerce")
        if w_values is None or l_values is None:
            out[f"raw_a_{canonical}"] = pd.NA
            out[f"raw_b_{canonical}"] = pd.NA
            continue
        out[f"raw_a_{canonical}"] = w_values.where(winner_is_a, l_values).astype("Int16")
        out[f"raw_b_{canonical}"] = l_values.where(winner_is_a, w_values).astype("Int16")

    out = out.reindex(columns=list(schema.MATCH_FACTS_COLUMNS))
    out = _apply_dtypes(out)
    out = _order_chronologically(out)

    log.info(
        "normalizacion completada",
        extra={
            "source": source,
            "rows": len(out),
            "years": f"{out['year'].min()}-{out['year'].max()}",
        },
    )
    return out


def _apply_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    for column, dtype in schema.DTYPES.items():
        if column not in df.columns:
            continue
        try:
            if dtype.startswith(("Int", "int")) or dtype.startswith(("Float", "float")):
                df[column] = pd.to_numeric(df[column], errors="coerce").astype(dtype)
            else:
                df[column] = df[column].astype(dtype)
        except (TypeError, ValueError) as exc:  # pragma: no cover - defensivo
            raise ValueError(f"No se pudo convertir '{column}' a {dtype}: {exc}") from exc
    return df


def _order_chronologically(df: pd.DataFrame) -> pd.DataFrame:
    """Ordena por la clave total `(date, tourney_id, round_order, match_num)`.

    Es la unica ordenacion que el calculo de Elo puede usar (riesgo R2).
    """
    return df.sort_values(
        ["date", "tourney_id", "round_order", "match_num"],
        kind="mergesort",  # estable: mismo orden ante claves identicas
        na_position="last",
    ).reset_index(drop=True)


def load_raw_csv(path: Path) -> pd.DataFrame:
    """Lee un CSV crudo sin inferir tipos agresivamente."""
    return pd.read_csv(path, dtype=str, keep_default_na=True, na_values=[""], encoding="utf-8")


def ingest_directory(
    directory: Path, *, source: str, min_year: int | None = None, max_year: int | None = None
) -> pd.DataFrame:
    """Lee todos los CSV anuales de un directorio y devuelve `match_facts`."""
    files = sorted(p for p in directory.glob("*.csv") if not p.name.startswith("_"))
    if not files:
        raise FileNotFoundError(f"No hay CSV en {directory}. Ejecuta antes `edgecourt data fetch`.")

    frames: list[pd.DataFrame] = []
    for path in files:
        year = _year_from_filename(path.name)
        if year is not None and min_year is not None and year < min_year:
            continue
        if year is not None and max_year is not None and year > max_year:
            continue
        frames.append(load_raw_csv(path))

    if not frames:
        raise ValueError(f"Ningun fichero de {directory} cae en el rango {min_year}-{max_year}")

    combined = pd.concat(frames, ignore_index=True)
    return normalise_raw_matches(combined, source=source)


def _year_from_filename(name: str) -> int | None:
    match = re.search(r"(\d{4})", name)
    return int(match.group(1)) if match else None


def deduplicate(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Elimina partidos con `match_id` repetido, conservando el primero."""
    duplicated = df["match_id"].duplicated(keep="first")
    removed = int(duplicated.sum())
    if removed:
        log.warning("match_id duplicados eliminados", extra={"removed": removed})
    return df.loc[~duplicated].reset_index(drop=True), removed


def summarise(df: pd.DataFrame) -> dict[str, object]:
    """Resumen de cobertura para el informe de ingesta."""
    stats_columns = [c for c in schema.RAW_STAT_COLUMNS if c in df.columns]
    has_stats = (
        df[stats_columns].notna().any(axis=1) if stats_columns else pd.Series(False, index=df.index)
    )
    return {
        "rows": len(df),
        "year_min": int(df["year"].min()) if len(df) else None,
        "year_max": int(df["year"].max()) if len(df) else None,
        "target_mean": float(df["target"].mean()) if len(df) else None,
        "pct_with_match_stats": float(has_stats.mean() * 100) if len(df) else 0.0,
        "pct_with_rank": float(
            (df["player_a_rank"].notna() & df["player_b_rank"].notna()).mean() * 100
        )
        if len(df)
        else 0.0,
        "pct_with_indoor": float(df["indoor"].notna().mean() * 100) if len(df) else 0.0,
        "completion_counts": {
            str(k): int(v) for k, v in df["completion_status"].value_counts().items()
        },
        "surface_counts": {
            str(k): int(v) for k, v in df["surface"].value_counts(dropna=False).items()
        },
    }


__all__ = [
    "deduplicate",
    "ingest_directory",
    "load_raw_csv",
    "normalise_raw_matches",
    "summarise",
]
