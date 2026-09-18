"""Tests de los snapshots: normalizacion, planificacion y deduplicacion."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest
from tests.factories_betfair import catalogue, market_book

from edgecourt.market import snapshots

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)


# --- Normalizacion ------------------------------------------------------------


def test_normalisation_produces_one_row_per_runner():
    rows = snapshots.normalise_market_book(
        market_book(), catalogue(now=NOW), label="1h", observed_at=NOW
    )
    assert len(rows) == 2
    assert {r["runner_name"] for r in rows} == {"Carlos Alcaraz", "Jannik Sinner"}


def test_normalisation_captures_back_and_lay_depth():
    rows = snapshots.normalise_market_book(
        market_book(back_a=1.72, lay_a=1.74), catalogue(now=NOW), label="1h", observed_at=NOW
    )
    row = next(r for r in rows if r["selection_id"] == 1001)
    assert row["back_price_1"] == 1.72
    assert row["lay_price_1"] == 1.74
    assert row["back_price_2"] == 1.70
    assert row["lay_price_3"] == 1.78
    assert row["back_size_1"] == 500.0


@pytest.mark.critical
def test_observed_at_is_the_real_time_not_the_target():
    """El brief exige el timestamp real de observacion, no el planificado.

    Aqui la captura del hito "24h" ocurre de hecho a 90 minutos del inicio (por
    ejemplo tras un reinicio). El dato debe reflejar la realidad.
    """
    observed = NOW
    cat = catalogue(now=NOW, start_in_minutes=90)
    rows = snapshots.normalise_market_book(market_book(), cat, label="24h", observed_at=observed)
    assert rows[0]["observed_at"] == observed
    assert rows[0]["snapshot_label"] == "24h"
    assert rows[0]["minutes_to_start"] == pytest.approx(90.0, abs=0.1)


def test_minutes_to_start_is_computed_from_the_market_start():
    rows = snapshots.normalise_market_book(
        market_book(), catalogue(now=NOW, start_in_minutes=360), label="6h", observed_at=NOW
    )
    assert rows[0]["minutes_to_start"] == pytest.approx(360.0, abs=0.1)


def test_missing_depth_is_padded_with_nulls():
    book = market_book()
    book["runners"][0]["ex"]["availableToBack"] = [{"price": 1.5, "size": 10.0}]
    rows = snapshots.normalise_market_book(book, catalogue(now=NOW), label="1h", observed_at=NOW)
    row = next(r for r in rows if r["selection_id"] == 1001)
    assert row["back_price_1"] == 1.5
    assert row["back_price_2"] is None
    assert row["back_price_3"] is None


def test_market_without_id_yields_nothing():
    assert snapshots.normalise_market_book({}, {}, label="1h", observed_at=NOW) == []


def test_inplay_and_status_are_recorded():
    rows = snapshots.normalise_market_book(
        market_book(status="SUSPENDED", inplay=True),
        catalogue(now=NOW),
        label="close",
        observed_at=NOW,
    )
    assert rows[0]["market_status"] == "SUSPENDED"
    assert rows[0]["inplay"] is True
    assert rows[0]["bet_delay"] == 5


def test_frame_has_the_full_schema():
    rows = snapshots.normalise_market_book(
        market_book(), catalogue(now=NOW), label="1h", observed_at=NOW
    )
    frame = snapshots.to_frame(rows)
    assert list(frame.columns) == list(snapshots.SNAPSHOT_COLUMNS)


def test_empty_frame_still_has_the_schema():
    frame = snapshots.to_frame([])
    assert list(frame.columns) == list(snapshots.SNAPSHOT_COLUMNS)
    assert frame.empty


# --- Planificacion ------------------------------------------------------------


@pytest.mark.parametrize(
    ("minutes_left", "expected"),
    [
        (24 * 60, "24h"),
        (12 * 60, "12h"),
        (6 * 60, "6h"),
        (60, "1h"),
        (10, "10m"),
        (1, "close"),
    ],
)
def test_each_target_is_detected_at_its_time(minutes_left, expected):
    due = snapshots.due_snapshots([catalogue(now=NOW, start_in_minutes=minutes_left)], now=NOW)
    assert [p.label for p in due] == [expected]


def test_no_snapshot_is_due_between_targets():
    """A 4 horas del inicio no toca ningun hito."""
    due = snapshots.due_snapshots([catalogue(now=NOW, start_in_minutes=240)], now=NOW)
    assert due == []


@pytest.mark.critical
def test_missed_targets_are_not_backfilled():
    """Un snapshot de 24h tomado a 3h del inicio seria un dato falso.

    Si el collector estuvo caido y se perdio la ventana, ese hito se queda vacio.
    La cobertura incompleta es un hecho que se reporta, no algo que se rellene.
    """
    due = snapshots.due_snapshots([catalogue(now=NOW, start_in_minutes=180)], now=NOW)
    assert "24h" not in [p.label for p in due]
    assert "12h" not in [p.label for p in due]


def test_already_captured_targets_are_skipped():
    cat = catalogue(now=NOW, start_in_minutes=60)
    captured = {snapshots.snapshot_id(cat["marketId"], 1001, "1h")}
    assert snapshots.due_snapshots([cat], now=NOW, already_captured=captured) == []


def test_started_markets_are_ignored():
    due = snapshots.due_snapshots([catalogue(now=NOW, start_in_minutes=-5)], now=NOW)
    assert due == []


def test_market_without_start_time_is_ignored():
    cat = catalogue(now=NOW)
    del cat["marketStartTime"]
    assert snapshots.due_snapshots([cat], now=NOW) == []


def test_tolerance_is_tighter_close_to_the_start():
    """Cerca del inicio el precio se mueve mas: la ventana debe estrecharse."""
    assert snapshots.SNAPSHOT_TOLERANCE["24h"] > snapshots.SNAPSHOT_TOLERANCE["1h"]
    assert snapshots.SNAPSHOT_TOLERANCE["1h"] > snapshots.SNAPSHOT_TOLERANCE["10m"]


# --- Persistencia y deduplicacion --------------------------------------------


def _frame(label: str, market_id: str = "1.234567890") -> pd.DataFrame:
    rows = snapshots.normalise_market_book(
        market_book(market_id), catalogue(market_id, now=NOW), label=label, observed_at=NOW
    )
    return snapshots.to_frame(rows)


def test_snapshots_are_persisted_partitioned_by_date(tmp_path):
    written = snapshots.append_snapshots(tmp_path, _frame("1h"))
    assert written == 2
    dataset = tmp_path / snapshots.SNAPSHOTS_DATASET
    assert (dataset / "date=2026-09-18").is_dir()


@pytest.mark.critical
def test_repeated_capture_of_the_same_target_does_not_duplicate(tmp_path):
    """Si el collector reintenta, la captura sustituye, no duplica."""
    snapshots.append_snapshots(tmp_path, _frame("1h"))
    snapshots.append_snapshots(tmp_path, _frame("1h"))

    from edgecourt.storage import read_parquet

    stored = read_parquet(tmp_path / snapshots.SNAPSHOTS_DATASET)
    assert len(stored) == 2
    assert stored["snapshot_id"].is_unique


def test_different_targets_coexist(tmp_path):
    snapshots.append_snapshots(tmp_path, _frame("1h"))
    snapshots.append_snapshots(tmp_path, _frame("10m"))

    from edgecourt.storage import read_parquet

    stored = read_parquet(tmp_path / snapshots.SNAPSHOTS_DATASET)
    assert len(stored) == 4
    assert set(stored["snapshot_label"]) == {"1h", "10m"}


def test_existing_ids_are_read_back(tmp_path):
    snapshots.append_snapshots(tmp_path, _frame("1h"))
    ids = snapshots.existing_snapshot_ids(tmp_path)
    assert len(ids) == 2
    assert all(i.endswith(":1h") for i in ids)


def test_existing_ids_can_be_limited_to_dates(tmp_path):
    snapshots.append_snapshots(tmp_path, _frame("1h"))
    assert snapshots.existing_snapshot_ids(tmp_path, dates=["2026-09-18"])
    assert not snapshots.existing_snapshot_ids(tmp_path, dates=["2020-01-01"])


def test_existing_ids_on_empty_store(tmp_path):
    assert snapshots.existing_snapshot_ids(tmp_path) == set()


def test_appending_an_empty_frame_is_a_noop(tmp_path):
    assert snapshots.append_snapshots(tmp_path, snapshots.to_frame([])) == 0


def test_coverage_report_counts_by_target(tmp_path):
    snapshots.append_snapshots(tmp_path, _frame("1h"))
    snapshots.append_snapshots(tmp_path, _frame("10m"))
    snapshots.append_snapshots(tmp_path, _frame("1h", market_id="1.999999999"))

    report = snapshots.coverage_report(tmp_path)
    by_label = dict(zip(report["snapshot_label"], report["markets"], strict=True))
    assert by_label["1h"] == 2
    assert by_label["10m"] == 1
    # El informe se ordena del hito mas lejano al mas cercano.
    assert list(report["snapshot_label"]) == ["1h", "10m"]


def test_coverage_report_on_empty_store(tmp_path):
    assert snapshots.coverage_report(tmp_path).empty
