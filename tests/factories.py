"""Factorias de datos crudos para los tests.

Reproducen el formato de los CSV de TennisMyLife / Sackmann, para poder
probar la ingesta sin depender de ficheros descargados.
"""

from __future__ import annotations

import pandas as pd


def _raw_match(**overrides) -> dict:
    """Una fila cruda en formato TennisMyLife / Sackmann."""
    base = {
        "tourney_id": "2023-560",
        "tourney_name": "Barcelona",
        "surface": "Clay",
        "draw_size": "48",
        "tourney_level": "500",
        "indoor": "O",
        "tourney_date": "20230417",
        "match_num": "1",
        "winner_id": "AB12",
        "winner_seed": "1",
        "winner_entry": "",
        "winner_name": "Winner Player",
        "winner_hand": "R",
        "winner_ht": "185",
        "winner_ioc": "ESP",
        "winner_age": "25.5",
        "winner_rank": "3",
        "winner_rank_points": "5000",
        "loser_id": "CD34",
        "loser_seed": "",
        "loser_entry": "",
        "loser_name": "Loser Player",
        "loser_hand": "L",
        "loser_ht": "180",
        "loser_ioc": "ARG",
        "loser_age": "28.1",
        "loser_rank": "40",
        "loser_rank_points": "1100",
        "score": "6-4 6-3",
        "best_of": "3",
        "round": "QF",
        "minutes": "95",
        "w_ace": "5",
        "w_df": "2",
        "w_svpt": "70",
        "w_1stIn": "45",
        "w_1stWon": "35",
        "w_2ndWon": "15",
        "w_SvGms": "10",
        "w_bpSaved": "3",
        "w_bpFaced": "4",
        "l_ace": "3",
        "l_df": "4",
        "l_svpt": "75",
        "l_1stIn": "40",
        "l_1stWon": "28",
        "l_2ndWon": "16",
        "l_SvGms": "10",
        "l_bpSaved": "2",
        "l_bpFaced": "6",
    }
    return base | overrides


def _letters(index: int) -> str:
    """Convierte un entero en un nombre alfabetico unico ('aaa', 'aab', ...).

    Los nombres de prueba no pueden llevar digitos: la normalizacion que usa el
    contraste entre fuentes descarta todo lo que no sea una letra, asi que
    "Winner 1" y "Winner 2" colapsarian en el mismo nombre.
    """
    letters = ""
    for _ in range(3):
        index, remainder = divmod(index, 26)
        letters = chr(ord("a") + remainder) + letters
    return letters.capitalize()


def _raw_frame(n: int = 1, **overrides) -> pd.DataFrame:
    rows = []
    for i in range(n):
        row = _raw_match(**overrides)
        suffix = _letters(i)
        row["match_num"] = str(i + 1)
        row["winner_id"] = f"W{i:03d}"
        row["loser_id"] = f"L{i:03d}"
        row["winner_name"] = f"Winner {suffix}"
        row["loser_name"] = f"Loser {suffix}"
        rows.append(row)
    return pd.DataFrame(rows, dtype=str)
