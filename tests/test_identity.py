"""Identidad Betfair <-> dataset (Carril A, A2): determinista, sin fuzzy silencioso."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

from edgecourt.db import repositories
from edgecourt.identity import players, service, tournaments
from edgecourt.identity.players import PlayerIndex, normalise_name

pytest_plugins = ["tests.conftest_db"]


def _matches(rows: list[tuple[str, str, str, str, str, str, str]]) -> pd.DataFrame:
    """(fecha, torneo, superficie, id_a, nombre_a, id_b, nombre_b)."""
    return pd.DataFrame(
        rows,
        columns=[
            "date",
            "tourney_name",
            "surface",
            "player_a_id",
            "player_a_name",
            "player_b_id",
            "player_b_name",
        ],
    )


BASE = _matches(
    [
        ("2026-10-01", "Shanghai Masters", "hard", "N1", "Novak Djokovic", "H1", "Hubert Hurkacz"),
        (
            "2026-10-02",
            "Shanghai Masters",
            "hard",
            "A1",
            "Alex De Minaur",
            "F1",
            "Francisco Cerundolo",
        ),
        (
            "2026-10-03",
            "Shanghai Masters",
            "hard",
            "J1",
            "Juan Manuel Cerundolo",
            "L1",
            "Jiri Lehecka",
        ),
        ("2020-01-01", "Old Open", "hard", "O1", "Jugador Retirado", "O2", "Otro Retirado"),
    ]
)


# --- Nombres -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Novak Djokovic", "novak djokovic"),
        ("  Alex   De  Minaur ", "alex de minaur"),
        ("Jiří Lehečka", "jiri lehecka"),
        ("Felix Auger-Aliassime", "felix auger aliassime"),
        ("Christopher O'Connell", "christopher oconnell"),
    ],
)
def test_normalise_name(raw, expected):
    assert normalise_name(raw) == expected


def test_doubles_names_are_detected():
    assert players.is_doubles_name("Bolelli/Vavassori")
    assert not players.is_doubles_name("Novak Djokovic")


# --- Jugadores -----------------------------------------------------------------


@pytest.mark.critical
def test_exact_unique_name_resolves():
    resolution = PlayerIndex.build(BASE).resolve("Alex de Minaur")
    assert (resolution.status, resolution.player_id, resolution.via) == (
        players.RESOLVED,
        "A1",
        "exacto",
    )


@pytest.mark.critical
def test_unknown_name_is_not_guessed_but_hints_are_listed_without_applying_them():
    resolution = PlayerIndex.build(BASE, aliases={}).resolve("Manuel Cerundolo")
    assert resolution.status == players.UNMATCHED
    assert resolution.player_id is None
    assert {pid for pid, _ in resolution.candidates} == {"F1", "J1"}


@pytest.mark.critical
def test_two_active_players_with_the_same_name_are_ambiguous():
    frame = pd.concat(
        [
            BASE,
            _matches([("2026-10-04", "X", "hard", "D2", "Novak Djokovic", "Z9", "Alguien Mas")]),
        ],
        ignore_index=True,
    )
    resolution = PlayerIndex.build(frame).resolve("Novak Djokovic")
    assert resolution.status == players.AMBIGUOUS
    assert resolution.player_id is None
    assert {pid for pid, _ in resolution.candidates} == {"N1", "D2"}


def test_inactive_player_is_not_matched():
    assert PlayerIndex.build(BASE).resolve("Jugador Retirado").status == players.UNMATCHED


def test_doubles_are_not_resolved_as_players():
    assert PlayerIndex.build(BASE).resolve("Bolelli/Vavassori").status == players.DOUBLES


def test_explicit_alias_takes_precedence_and_requires_a_known_id():
    index = PlayerIndex.build(BASE, aliases={"Manuel Cerundolo": "J1"})
    resolution = index.resolve("Manuel Cerundolo")
    assert (resolution.status, resolution.player_id, resolution.via) == (
        players.RESOLVED,
        "J1",
        "alias",
    )
    unknown = PlayerIndex.build(BASE, aliases={"Manuel Cerundolo": "NO_EXISTE"})
    assert unknown.resolve("Manuel Cerundolo").status == players.UNMATCHED


# --- Torneos / superficie -------------------------------------------------------


def _tournaments(rows: list[tuple[str, str, str]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["date", "tourney_name", "surface"])


def test_surface_resolves_from_recent_editions_ignoring_generic_words():
    index = tournaments.SurfaceIndex.build(
        _tournaments(
            [
                ("2025-10-10", "Shanghai Masters", "hard"),
                ("2026-10-10", "Shanghai", "hard"),
            ]
        )
    )
    result = index.resolve("ATP Shanghai 2026")
    assert (result.status, result.surface, result.key) == (
        tournaments.RESOLVED,
        "hard",
        "shanghai",
    )


@pytest.mark.critical
def test_surface_is_ambiguous_when_recent_editions_disagree():
    index = tournaments.SurfaceIndex.build(
        _tournaments([("2025-05-01", "Sitio", "clay"), ("2026-05-01", "Sitio", "hard")])
    )
    assert index.resolve("ATP Sitio 2026").status == tournaments.AMBIGUOUS


def test_unknown_tournament_and_out_of_scope_competitions():
    index = tournaments.SurfaceIndex.build(_tournaments([("2026-01-01", "Otro", "hard")]))
    assert index.resolve("ATP Nuevo 2026").status == tournaments.UNKNOWN
    for name in ("Davis Cup", "WTA Wuhan 2026", "ITF W50 Heraklion GRE", "Laver Cup"):
        assert index.resolve(name).status == tournaments.OUT_OF_SCOPE


def test_old_editions_outside_the_recent_window_are_ignored():
    index = tournaments.SurfaceIndex.build(
        _tournaments([("2020-05-01", "Sitio", "clay"), ("2026-05-01", "Sitio", "hard")])
    )
    assert index.resolve("ATP Sitio 2026").surface == "hard"


# --- Servicio por mercado -------------------------------------------------------


def _runner(market, selection, name, competition="ATP Shanghai 2026"):
    return {
        "market_id": market,
        "selection_id": selection,
        "runner_name": name,
        "competition_name": competition,
    }


def _indexes():
    return PlayerIndex.build(BASE), tournaments.SurfaceIndex.build(BASE)


@pytest.mark.critical
def test_market_is_ready_only_when_both_players_and_surface_resolve():
    players_ix, surfaces_ix = _indexes()
    report = service.resolve_markets(
        [
            _runner("1.1", 1, "Novak Djokovic"),
            _runner("1.1", 2, "Hubert Hurkacz"),
            _runner("1.2", 1, "Novak Djokovic"),
            _runner("1.2", 2, "Manuel Cerundolo"),
            _runner("1.3", 1, "Bolelli/Vavassori"),
            _runner("1.3", 2, "Krajicek/Mektic"),
            _runner("1.4", 1, "Alguien", "WTA Wuhan 2026"),
            _runner("1.4", 2, "Otra", "WTA Wuhan 2026"),
            _runner("1.5", 1, "Novak Djokovic", "ATP Nuevo 2026"),
            _runner("1.5", 2, "Hubert Hurkacz", "ATP Nuevo 2026"),
            _runner("1.6", 1, "Novak Djokovic"),
            _runner("1.6", 2, "novak djokovic"),
            _runner("1.7", 1, "Novak Djokovic"),
        ],
        players_ix,
        surfaces_ix,
    )
    status = {m.market_id: m.status for m in report.markets}
    assert status == {
        "1.1": service.READY,
        "1.2": service.UNRESOLVED_PLAYER,
        "1.3": service.DOUBLES,
        "1.4": service.OUT_OF_SCOPE,
        "1.5": service.UNRESOLVED_SURFACE,
        "1.6": service.SAME_PLAYER,
        "1.7": service.BAD_SHAPE,
    }
    assert report.ready == 1
    assert "Manuel Cerundolo" in report.unresolved
    ready = next(m for m in report.markets if m.market_id == "1.1")
    assert ready.surface == "hard"


# --- Persistencia ---------------------------------------------------------------


def _seed_runner(db, player_id=None):
    start = datetime(2030, 1, 1, tzinfo=UTC)
    with db.transaction(), db.cursor() as cursor:
        repositories.upsert_event(
            cursor,
            {
                "event_id": "ev1",
                "event_name": "A v B",
                "competition_id": None,
                "competition_name": "ATP Shanghai 2026",
                "country_code": "ES",
                "timezone": "GMT",
                "open_date": start,
            },
        )
        repositories.upsert_market(
            cursor,
            {
                "market_id": "1.001",
                "event_id": "ev1",
                "market_name": "Match Odds",
                "market_type": "MATCH_ODDS",
                "market_start_time": start,
            },
        )
        repositories.upsert_runner(
            cursor,
            {
                "market_id": "1.001",
                "selection_id": 101,
                "runner_name": "Novak Djokovic",
                "sort_priority": 1,
                "handicap": 0,
            },
        )
        if player_id:
            cursor.execute("UPDATE betfair_runner SET player_id = %s", (player_id,))


@pytest.mark.integration
@pytest.mark.critical
def test_set_runner_player_id_fills_empty_and_never_overwrites(db):
    _seed_runner(db)
    with db.transaction(), db.cursor() as cursor:
        assert repositories.set_runner_player_id(cursor, "1.001", 101, "N1") is True
        # Una identidad ya fijada no se sobrescribe, ni siquiera con otro valor.
        assert repositories.set_runner_player_id(cursor, "1.001", 101, "OTRO") is False
        rows = repositories.runners_for_identity(cursor)
    assert [(r["player_id"], r["competition_name"]) for r in rows] == [("N1", "ATP Shanghai 2026")]


def test_confirmed_project_alias_points_to_juan_manuel_cerundolo():
    assert players.ALIASES["Manuel Cerundolo"] == "C0C8"
    assert normalise_name("Manuel Cerundolo") == "manuel cerundolo"


def test_empty_alias_dict_disables_aliases_instead_of_falling_back_to_the_defaults():
    index = PlayerIndex.build(BASE, aliases={})
    assert index.resolve("Manuel Cerundolo").status == players.UNMATCHED
