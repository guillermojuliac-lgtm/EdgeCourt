"""Capa de almacenamiento de EdgeCourt.

Decision de diseno (IMPLEMENTATION_PLAN.md D2): **Parquet es el almacen
canonico y DuckDB es el motor de consulta**, no un servidor de estado. No
existe un fichero de base de datos mutable que sea fuente de verdad; DuckDB se
abre en memoria y lee los Parquet directamente.

Ventajas para este proyecto:

* reproducibilidad: un dataset es un conjunto de ficheros, no el estado
  acumulado de una base de datos;
* consumo de RAM contenido: DuckDB proyecta y filtra sin materializar todo;
* portabilidad: copiar `data/` copia el sistema entero.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq

COMPRESSION = "zstd"

# Nombres de vista admitidos en DuckDB (se interpolan en SQL).
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def write_parquet(df: pd.DataFrame, path: Path, *, compression: str = COMPRESSION) -> Path:
    """Escribe un DataFrame como un unico Parquet, de forma atomica.

    Se escribe primero a un fichero temporal en el mismo directorio y despues se
    renombra: si el proceso muere a mitad, el fichero destino queda intacto en
    lugar de quedar truncado.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        pq.write_table(pa.Table.from_pandas(df, preserve_index=False), tmp, compression=compression)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return path


def read_parquet(path: Path, *, columns: Sequence[str] | None = None) -> pd.DataFrame:
    """Lee un Parquet o un directorio de Parquets particionados.

    `columns` permite no cargar en RAM lo que no se necesita (ver §27 del brief).
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"No existe el dataset: {path}")
    dataset = ds.dataset(path, format="parquet", partitioning="hive")
    return dataset.to_table(columns=list(columns) if columns else None).to_pandas()


def write_partitioned_parquet(
    df: pd.DataFrame,
    path: Path,
    *,
    partition_cols: Sequence[str],
    compression: str = COMPRESSION,
) -> Path:
    """Escribe un dataset particionado estilo hive (p. ej. `year=2024/`).

    Sobrescribe unicamente las particiones presentes en `df`; el resto del
    dataset no se toca.
    """
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    ds.write_dataset(
        pa.Table.from_pandas(df, preserve_index=False),
        base_dir=path,
        format="parquet",
        partitioning=list(partition_cols),
        partitioning_flavor="hive",
        existing_data_behavior="delete_matching",
        file_options=ds.ParquetFileFormat().make_write_options(compression=compression),
    )
    return path


@contextmanager
def duckdb_connection(views: dict[str, Path] | None = None) -> Iterator[duckdb.DuckDBPyConnection]:
    """Conexion DuckDB en memoria con vistas de solo lectura sobre Parquet.

    Ejemplo:
        with duckdb_connection({"matches": settings.processed_dir / "matches"}) as con:
            con.sql("SELECT surface, count(*) FROM matches GROUP BY 1").df()
    """
    con = duckdb.connect(database=":memory:")
    try:
        for name, location in (views or {}).items():
            if not _IDENTIFIER.fullmatch(name):
                raise ValueError(f"Nombre de vista invalido: {name!r}")
            location = Path(location)
            if not location.exists():
                raise FileNotFoundError(f"No existe el origen de la vista '{name}': {location}")
            glob = str(location / "**" / "*.parquet") if location.is_dir() else str(location)
            # DuckDB no admite parametros preparados en CREATE VIEW, asi que la
            # ruta se interpola escapando comillas simples; el nombre de la vista
            # se ha validado antes como identificador.
            literal = glob.replace("'", "''")
            con.execute(
                f"CREATE VIEW {name} AS "
                f"SELECT * FROM read_parquet('{literal}', hive_partitioning = true)"
            )
        yield con
    finally:
        con.close()


def query(sql: str, views: dict[str, Path], params: Sequence[Any] | None = None) -> pd.DataFrame:
    """Atajo para una consulta puntual sobre Parquet."""
    with duckdb_connection(views) as con:
        return con.execute(sql, list(params) if params else None).df()


def dataset_summary(path: Path) -> dict[str, Any]:
    """Metadatos baratos de un dataset, sin cargarlo en RAM."""
    path = Path(path)
    if not path.exists():
        return {"path": str(path), "exists": False}

    # Un directorio de trabajo puede contener ficheros que no son Parquet (por
    # ejemplo informes JSON), asi que se enumeran explicitamente en lugar de
    # dejar que pyarrow intente leer todo lo que encuentre.
    if path.is_dir():
        sources = sorted(path.rglob("*.parquet"))
        if not sources:
            return {"path": str(path), "exists": True, "rows": 0, "files": 0, "bytes": 0}
    else:
        sources = [path]

    dataset = ds.dataset(sources, format="parquet", partitioning="hive")
    return {
        "path": str(path),
        "exists": True,
        "rows": dataset.count_rows(),
        "columns": len(dataset.schema.names),
        "files": len(sources),
        "bytes": sum(f.stat().st_size for f in sources),
    }
