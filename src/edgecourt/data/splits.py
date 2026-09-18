"""Division temporal de los datos. Fijada el 2026-09-18 y, a partir de ahi, inmutable.

Vive en el codigo y no solo en la documentacion para que ningun experimento
pueda "elegir" su propio corte a conveniencia.

Reglas (docs/DATA.md):

* El desarrollo y el ajuste usan **solo** VALIDATION.
* TEST se consulta un numero limitado de veces, y cada consulta se registra.
  Mirar el test repetidamente y ajustar es leakage humano: produce optimismo
  sistematico sin dejar rastro en ninguna metrica.
* LIVE es out-of-sample continuo: nunca se entrena con el.
* La division es **cronologica**. El split aleatorio esta prohibido como
  validacion principal (brief §10).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pandas as pd

SplitName = Literal["train", "validation", "test", "live"]

# Anos incluidos en cada conjunto (ambos extremos incluidos).
SPLITS: dict[SplitName, tuple[int, int]] = {
    "train": (2000, 2022),
    "validation": (2023, 2023),
    "test": (2024, 2025),
    "live": (2026, 9999),
}

EVALUATION_LOG = "test_evaluations.jsonl"


@dataclass(frozen=True, slots=True)
class Split:
    name: SplitName
    first_year: int
    last_year: int

    def contains(self, year: int) -> bool:
        return self.first_year <= year <= self.last_year


def get_split(name: SplitName) -> Split:
    if name not in SPLITS:
        raise ValueError(f"Split desconocido: {name}. Validos: {sorted(SPLITS)}")
    first, last = SPLITS[name]
    return Split(name=name, first_year=first, last_year=last)


def assign_split(years: pd.Series) -> pd.Series:
    """Etiqueta cada fila con el conjunto al que pertenece."""
    result = pd.Series(pd.NA, index=years.index, dtype="string")
    for name, (first, last) in SPLITS.items():
        result[years.between(first, last)] = name
    return result


def filter_split(df: pd.DataFrame, name: SplitName, *, year_column: str = "year") -> pd.DataFrame:
    split = get_split(name)
    return df[df[year_column].between(split.first_year, split.last_year)]


def record_test_evaluation(results_dir: Path, *, model: str, reason: str, metrics: dict) -> Path:
    """Deja constancia de cada evaluacion sobre TEST.

    No impide consultarlo: hace visible cuantas veces se ha consultado, que es lo
    que permite juzgar despues si el resultado final esta contaminado.
    """
    results_dir.mkdir(parents=True, exist_ok=True)
    path = results_dir / EVALUATION_LOG
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "model": model,
        "reason": reason,
        "metrics": metrics,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, default=str) + "\n")
    return path


def count_test_evaluations(results_dir: Path) -> int:
    path = results_dir / EVALUATION_LOG
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
