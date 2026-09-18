"""Tests de la capa de almacenamiento: Parquet + DuckDB."""

from __future__ import annotations

import pandas as pd
import pytest

from edgecourt.storage import (
    dataset_summary,
    duckdb_connection,
    query,
    read_parquet,
    write_parquet,
    write_partitioned_parquet,
)


@pytest.fixture
def sample_matches() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "match_id": ["2023-001", "2023-002", "2024-001", "2024-002"],
            "year": [2023, 2023, 2024, 2024],
            "surface": ["clay", "hard", "grass", "hard"],
            "target": [1, 0, 1, 1],
            "elo_diff": [55.5, -12.0, 3.25, -80.75],
        }
    )


def test_parquet_roundtrip_preserves_data(tmp_path, sample_matches):
    path = write_parquet(sample_matches, tmp_path / "matches.parquet")
    result = read_parquet(path)
    pd.testing.assert_frame_equal(result, sample_matches)


def test_write_parquet_is_atomic(tmp_path, sample_matches):
    """No debe quedar ningun fichero temporal tras la escritura."""
    write_parquet(sample_matches, tmp_path / "nested" / "matches.parquet")
    leftovers = [p.name for p in (tmp_path / "nested").iterdir() if p.name.startswith(".")]
    assert leftovers == []


def test_read_parquet_with_column_projection(tmp_path, sample_matches):
    path = write_parquet(sample_matches, tmp_path / "matches.parquet")
    result = read_parquet(path, columns=["match_id", "target"])
    assert list(result.columns) == ["match_id", "target"]


def test_read_missing_dataset_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_parquet(tmp_path / "no-existe.parquet")


def test_partitioned_write_and_read(tmp_path, sample_matches):
    path = write_partitioned_parquet(sample_matches, tmp_path / "ds", partition_cols=["year"])
    assert (path / "year=2023").is_dir()
    assert (path / "year=2024").is_dir()

    result = read_parquet(path)
    assert len(result) == 4
    assert set(result["year"].astype(int)) == {2023, 2024}


def test_partition_overwrite_only_touches_matching_partitions(tmp_path, sample_matches):
    path = tmp_path / "ds"
    write_partitioned_parquet(sample_matches, path, partition_cols=["year"])

    update = pd.DataFrame(
        {
            "match_id": ["2024-999"],
            "year": [2024],
            "surface": ["clay"],
            "target": [0],
            "elo_diff": [1.0],
        }
    )
    write_partitioned_parquet(update, path, partition_cols=["year"])

    result = read_parquet(path)
    assert len(result) == 3  # 2 de 2023 intactas + 1 nueva de 2024
    assert set(result[result["year"].astype(int) == 2023]["match_id"]) == {"2023-001", "2023-002"}


def test_duckdb_query_over_parquet(tmp_path, sample_matches):
    path = write_partitioned_parquet(sample_matches, tmp_path / "ds", partition_cols=["year"])
    result = query(
        "SELECT surface, count(*) AS n FROM matches GROUP BY 1 ORDER BY 1",
        views={"matches": path},
    )
    assert dict(zip(result["surface"], result["n"], strict=True)) == {
        "clay": 1,
        "grass": 1,
        "hard": 2,
    }


def test_duckdb_view_on_missing_path_raises(tmp_path):
    with pytest.raises(FileNotFoundError), duckdb_connection({"matches": tmp_path / "vacio"}):
        pass


def test_dataset_summary(tmp_path, sample_matches):
    assert dataset_summary(tmp_path / "nada")["exists"] is False

    path = write_parquet(sample_matches, tmp_path / "matches.parquet")
    info = dataset_summary(path)
    assert info["rows"] == 4
    assert info["columns"] == 5
    assert info["bytes"] > 0


def test_invalid_view_name_is_rejected(tmp_path, sample_matches):
    """El nombre de vista se interpola en SQL: debe validarse."""
    path = write_parquet(sample_matches, tmp_path / "m.parquet")
    with pytest.raises(ValueError, match="invalido"), duckdb_connection({"m; DROP TABLE x": path}):
        pass


def test_dataset_summary_ignores_non_parquet_files(tmp_path, sample_matches):
    """Un directorio de trabajo puede tener informes JSON junto a los Parquet."""
    write_partitioned_parquet(sample_matches, tmp_path / "ds", partition_cols=["year"])
    (tmp_path / "ds" / "report.json").write_text('{"generated": true}')

    info = dataset_summary(tmp_path / "ds")
    assert info["rows"] == 4


def test_dataset_summary_on_directory_without_parquet(tmp_path):
    (tmp_path / "results").mkdir()
    (tmp_path / "results" / "report.json").write_text("{}")

    info = dataset_summary(tmp_path / "results")
    assert info["exists"] is True
    assert info["rows"] == 0
