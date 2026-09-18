"""Tests del Elo. Los dos bloqueantes de PHASE 2 son:

* `test_elo_is_chronological`
* `test_elo_no_future_data`

Si cualquiera de los dos falla, todo lo que produzca este modulo es invalido y
no se avanza de fase.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from tests.factories import _raw_frame

from edgecourt.data import ingest
from edgecourt.features.elo import (
    INITIAL_RATING,
    EloConfig,
    compute_elo,
    elo_probability,
    expected_score,
)


def _facts(n: int = 20, **overrides) -> pd.DataFrame:
    return ingest.normalise_raw_matches(_raw_frame(n, **overrides), source="test")


def _chain(players: list[tuple[str, str]], surface: str = "Clay") -> pd.DataFrame:
    """Construye una secuencia de partidos cronologica entre jugadores dados."""
    rows = []
    for i, (winner, loser) in enumerate(players):
        frame = _raw_frame(1, surface=surface)
        row = frame.iloc[0].to_dict()
        row["winner_id"] = winner
        row["loser_id"] = loser
        row["winner_name"] = f"Player {winner}"
        row["loser_name"] = f"Player {loser}"
        row["match_num"] = str(i + 1)
        row["tourney_date"] = f"2023{(i // 28) + 1:02d}{(i % 28) + 1:02d}"
        row["tourney_id"] = f"2023-{i:03d}"
        rows.append(row)
    return ingest.normalise_raw_matches(pd.DataFrame(rows, dtype=str), source="test")


def _mixed(entries: list[tuple[str, str, str]]) -> pd.DataFrame:
    """Secuencia cronologica de (superficie, ganador, perdedor)."""
    rows = []
    for i, (surface, winner, loser) in enumerate(entries):
        row = _raw_frame(1, surface=surface).iloc[0].to_dict()
        row["winner_id"], row["loser_id"] = winner, loser
        row["winner_name"], row["loser_name"] = f"P {winner}", f"P {loser}"
        row["tourney_id"] = f"2023-{i:03d}"
        row["tourney_date"] = f"2023{(i // 28) + 1:02d}{(i % 28) + 1:02d}"
        row["match_num"] = str(i + 1)
        rows.append(row)
    return ingest.normalise_raw_matches(pd.DataFrame(rows, dtype=str), source="test")


# --- BLOQUEANTE 1: orden cronologico -----------------------------------------


@pytest.mark.critical
def test_elo_is_chronological():
    """El Elo solo puede calcularse sobre la clave de orden total.

    Si el dataset llega desordenado, el rating de un partido podria incorporar el
    resultado de otro posterior. El calculo debe abortar, no continuar en silencio.
    """
    facts = _chain([("A", "B"), ("A", "C"), ("B", "C"), ("C", "A")])

    # En orden: funciona.
    assert len(compute_elo(facts)) == len(facts)

    # Desordenado: debe abortar.
    shuffled = facts.iloc[::-1]
    with pytest.raises(ValueError, match="orden cronologico"):
        compute_elo(shuffled)

    # Intercambiar solo dos filas contiguas: tambien debe abortar.
    positions = [1, 0, *range(2, len(facts))]
    scrambled = facts.iloc[positions].reset_index(drop=True)
    with pytest.raises(ValueError, match="orden cronologico"):
        compute_elo(scrambled)


@pytest.mark.critical
def test_elo_processes_matches_in_order_key_sequence():
    """El rating previo de cada partido debe reflejar solo los partidos anteriores."""
    facts = _chain([("A", "B")] * 6)
    result = compute_elo(facts)

    # A gana siempre: su rating previo crece monotonamente, el de B decrece.
    a_ratings = result["elo_a_before"].where(facts["target"] == 1, result["elo_b_before"])
    assert a_ratings.is_monotonic_increasing
    assert result["elo_a_matches_before"].iloc[0] == 0
    assert list(result["elo_a_matches_before"]) == list(range(6))


# --- BLOQUEANTE 2: ausencia de datos futuros ---------------------------------


@pytest.mark.critical
def test_elo_no_future_data():
    """Alterar el resultado de un partido no puede cambiar los ratings previos.

    Es un test de envenenamiento: se corrompe el desenlace del partido en la
    posicion k y se comprueba que las filas 0..k conservan exactamente los mismos
    ratings previos. Si cambiara alguna, el calculo estaria mirando al futuro.
    """
    facts = _chain([("A", "B"), ("B", "C"), ("C", "A"), ("A", "C"), ("B", "A")])
    baseline = compute_elo(facts)

    for k in range(len(facts)):
        poisoned = facts.copy()
        poisoned.loc[poisoned.index[k], "target"] = 1 - poisoned.loc[poisoned.index[k], "target"]
        result = compute_elo(poisoned)

        # Hasta el partido k inclusive, el estado PREVIO no puede haber cambiado.
        columns = ["elo_a_before", "elo_b_before", "surface_elo_a_before", "surface_elo_b_before"]
        pd.testing.assert_frame_equal(
            baseline.loc[:k, columns],
            result.loc[:k, columns],
            check_exact=False,
            atol=1e-9,
        )

    # Y, como control, envenenar el primer partido SI debe alterar los posteriores.
    poisoned = facts.copy()
    poisoned.loc[poisoned.index[0], "target"] = 1 - poisoned.loc[poisoned.index[0], "target"]
    changed = compute_elo(poisoned)
    assert not np.allclose(
        baseline["elo_a_before"].to_numpy()[1:], changed["elo_a_before"].to_numpy()[1:]
    )


@pytest.mark.critical
def test_first_match_of_every_player_uses_the_initial_rating():
    """Nadie puede llegar a su primer partido con un rating ya formado."""
    facts = _chain([("A", "B"), ("C", "D"), ("A", "C")])
    result = compute_elo(facts)

    assert result["elo_a_before"].iloc[0] == INITIAL_RATING
    assert result["elo_b_before"].iloc[0] == INITIAL_RATING
    assert result["elo_a_before"].iloc[1] == INITIAL_RATING
    assert result["elo_b_before"].iloc[1] == INITIAL_RATING
    # En el tercero, ambos ya tienen historia.
    assert result["elo_a_before"].iloc[2] != INITIAL_RATING
    assert result["elo_b_before"].iloc[2] != INITIAL_RATING


# --- Propiedades del sistema Elo ---------------------------------------------


def test_expected_score_is_symmetric_and_centred():
    assert expected_score(1500, 1500) == pytest.approx(0.5)
    assert expected_score(1900, 1500) == pytest.approx(10 / 11, abs=1e-6)
    assert expected_score(1500, 1900) == pytest.approx(1 / 11, abs=1e-6)
    assert expected_score(1600, 1400) + expected_score(1400, 1600) == pytest.approx(1.0)


def test_rating_moves_towards_the_winner():
    facts = _chain([("A", "B"), ("A", "B")])
    result = compute_elo(facts)
    winner_before_second = (
        result["elo_a_before"].iloc[1]
        if facts["target"].iloc[1] == 1
        else result["elo_b_before"].iloc[1]
    )
    assert winner_before_second > INITIAL_RATING


def test_k_factor_decays_with_experience():
    """Un jugador consolidado debe moverse menos que un novato."""
    config = EloConfig()
    assert config.k_factor(0) > config.k_factor(50) > config.k_factor(500)


def test_beating_a_stronger_player_moves_more():
    """Ganar a alguien mejor debe mover mas el rating que ganar a alguien peor."""
    # Con el mismo K, se compara solo el termino de sorpresa.
    surprise = 1.0 - expected_score(1500, 1900)
    expected = 1.0 - expected_score(1500, 1100)
    assert surprise > expected


@pytest.mark.critical
def test_walkovers_do_not_update_ratings():
    """Un walkover no es informacion sobre quien juega mejor."""
    facts = _chain([("A", "B"), ("A", "B"), ("A", "B")])
    facts.loc[facts.index[0], "completion_status"] = "walkover"
    result = compute_elo(facts)

    # El segundo partido sigue viendo a ambos jugadores sin historial.
    assert result["elo_a_before"].iloc[1] == INITIAL_RATING
    assert result["elo_b_before"].iloc[1] == INITIAL_RATING
    assert result["elo_a_matches_before"].iloc[1] == 0


def test_retirements_do_update_ratings():
    """Un abandono si es informacion: hubo partido y hubo un ganador."""
    facts = _chain([("A", "B"), ("A", "B")])
    facts.loc[facts.index[0], "completion_status"] = "retired"
    result = compute_elo(facts)
    assert result["elo_a_before"].iloc[1] != INITIAL_RATING


# --- Elo por superficie ------------------------------------------------------


@pytest.mark.critical
def test_surface_elo_is_isolated_per_surface():
    """Los resultados en tierra no pueden mover el Elo de hierba."""
    rows = []
    for i, (surface, winner, loser) in enumerate(
        [("Clay", "A", "B"), ("Clay", "A", "B"), ("Grass", "A", "B")]
    ):
        frame = _raw_frame(1, surface=surface)
        row = frame.iloc[0].to_dict()
        row["winner_id"], row["loser_id"] = winner, loser
        row["winner_name"], row["loser_name"] = f"P {winner}", f"P {loser}"
        row["tourney_id"] = f"2023-{i:03d}"
        row["tourney_date"] = f"202301{i + 1:02d}"
        rows.append(row)
    facts = ingest.normalise_raw_matches(pd.DataFrame(rows, dtype=str), source="test")
    result = compute_elo(facts)

    # El partido en hierba es el primero en esa superficie: ratings iniciales.
    assert result["surface_elo_a_before"].iloc[2] == INITIAL_RATING
    assert result["surface_elo_b_before"].iloc[2] == INITIAL_RATING
    assert result["surface_elo_a_matches_before"].iloc[2] == 0
    # Pero el Elo global si acumula los tres.
    assert result["elo_a_matches_before"].iloc[2] == 2


def test_carpet_updates_global_elo_but_no_surface_elo():
    """Carpet queda fuera del Elo por superficie (circuito extinto)."""
    facts = _chain([("A", "B"), ("A", "B")], surface="Carpet")
    result = compute_elo(facts)

    assert result["surface_elo_a_before"].isna().all()
    assert result["surface_elo_diff"].isna().all()
    assert result["elo_a_matches_before"].iloc[1] == 1


def test_missing_surface_yields_null_surface_elo():
    facts = _chain([("A", "B")], surface="Moqueta")
    result = compute_elo(facts)
    assert pd.isna(result["surface_elo_a_before"].iloc[0])


# --- Probabilidades -----------------------------------------------------------


@pytest.mark.critical
def test_elo_probabilities_are_valid():
    facts = _facts(50)
    result = compute_elo(facts)
    for weight in (0.0, 0.5, 1.0):
        probability = elo_probability(result, surface_weight=weight)
        assert probability.between(0.0, 1.0).all()
        assert probability.notna().all()


@pytest.mark.critical
def test_probability_sum_is_one():
    """P(A) + P(B) = 1 por construccion (brief §9)."""
    facts = _facts(30)
    result = compute_elo(facts)
    p_a = elo_probability(result)
    # P(B) es la probabilidad con los lados invertidos.
    mirrored = result.copy()
    mirrored["elo_diff"] = -result["elo_diff"]
    mirrored["surface_elo_diff"] = -result["surface_elo_diff"]
    p_b = elo_probability(mirrored)
    assert np.allclose(p_a + p_b, 1.0)


def test_surface_weight_selects_the_right_ratings():
    """Con historia distinta en global y en superficie, las probabilidades difieren.

    A domina a B en tierra; el primer partido en hierba encuentra el Elo global ya
    inclinado pero el de hierba todavia en su valor inicial.
    """
    facts = _mixed(
        [("Clay", "A", "B"), ("Clay", "A", "B"), ("Clay", "A", "B"), ("Grass", "A", "B")]
    )
    result = compute_elo(facts)

    only_global = elo_probability(result, surface_weight=0.0)
    only_surface = elo_probability(result, surface_weight=1.0)

    # En el partido de hierba, el Elo de superficie no sabe nada todavia.
    assert only_surface.iloc[3] == pytest.approx(0.5)
    assert only_global.iloc[3] != pytest.approx(0.5)
    assert not np.allclose(only_global, only_surface)


def test_invalid_surface_weight_is_rejected():
    facts = _facts(5)
    result = compute_elo(facts)
    for weight in (-0.1, 1.1):
        with pytest.raises(ValueError, match="surface_weight"):
            elo_probability(result, surface_weight=weight)


def test_falls_back_to_global_elo_when_surface_elo_is_missing():
    facts = _chain([("A", "B")] * 3, surface="Carpet")
    result = compute_elo(facts)
    with_surface = elo_probability(result, surface_weight=1.0)
    global_only = elo_probability(result, surface_weight=0.0)
    assert np.allclose(with_surface, global_only)


def test_output_columns_are_stable():
    from edgecourt.features.elo import OUTPUT_COLUMNS

    result = compute_elo(_facts(5))
    assert list(result.columns) == list(OUTPUT_COLUMNS)


def test_missing_order_columns_are_rejected():
    facts = _facts(5).drop(columns=["round_order"])
    with pytest.raises(ValueError, match="clave de orden"):
        compute_elo(facts)


def test_only_the_k_to_scale_ratio_matters():
    """Duplicar K y la escala a la vez no cambia ninguna probabilidad.

    Los ratings crecen proporcionalmente a K, y la probabilidad depende de
    `diff / scale`: por tanto `k_base / scale` es el unico grado de libertad real
    del sistema. Verificado empiricamente sobre VALIDATION 2023, donde
    (100, 400) y (150, 600) producen metricas identicas.

    Tenerlo como test evita que una futura "exploracion de hiperparametros"
    malgaste presupuesto recorriendo configuraciones equivalentes.
    """
    facts = _mixed(
        [("Clay", "A", "B"), ("Grass", "B", "C"), ("Clay", "C", "A"), ("Hard", "A", "C")]
    )
    base = compute_elo(facts, EloConfig(k_base=100.0, scale=400.0))
    scaled = compute_elo(facts, EloConfig(k_base=150.0, scale=600.0))

    p_base = elo_probability(base, config=EloConfig(k_base=100.0, scale=400.0))
    p_scaled = elo_probability(scaled, config=EloConfig(k_base=150.0, scale=600.0))
    assert np.allclose(p_base, p_scaled)


def test_lower_k_produces_less_extreme_probabilities():
    """Un K menor concentra las probabilidades cerca de 0.5.

    Es el mecanismo detras del compromiso observado en PHASE 2: mas K discrimina
    mejor pero se pasa de confiado. La calibracion (PHASE 6) existe para quedarse
    con lo primero sin pagar lo segundo.
    """
    facts = _mixed([("Clay", "A", "B")] * 10)
    high = elo_probability(compute_elo(facts, EloConfig(k_base=250.0)))
    low = elo_probability(compute_elo(facts, EloConfig(k_base=30.0)))
    assert (high - 0.5).abs().max() > (low - 0.5).abs().max()
