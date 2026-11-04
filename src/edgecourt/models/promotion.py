"""Promocion MANUAL de un challenger a production (DEC-013).

Nada aqui se ejecuta solo: lo invoca `edgecourt model promote`, que por defecto solo informa y
exige `--apply` y un responsable (`--by`) para escribir. Este modulo se ocupa unicamente de los
ficheros; el registro en `model_version` (quien y cuando) vive en `db.repositories`.

La promocion verifica la integridad del challenger (SHA-256 del artefacto) antes de copiarlo y
vuelve a verificarla sobre la copia, de modo que un fichero corrupto no llega a production.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from edgecourt.logging_setup import get_logger
from edgecourt.models.base import ModelManifest
from edgecourt.models.registry import ARTIFACT_SUFFIX, MANIFEST_SUFFIX, load_model

log = get_logger("models.promotion")


@dataclass(frozen=True, slots=True)
class PromotionPlan:
    """Lo que se promovera: el manifiesto ya verificado y las rutas de origen y destino."""

    manifest: ModelManifest
    challenger_dir: Path
    production_dir: Path

    @property
    def artifact_target(self) -> Path:
        return self.production_dir / f"{self.manifest.model_id}{ARTIFACT_SUFFIX}"


def plan_promotion(model_id: str, *, challenger_dir: Path, production_dir: Path) -> PromotionPlan:
    """Verifica el challenger y devuelve el plan. No escribe nada.

    Raises:
        FileNotFoundError: falta el manifiesto o el artefacto.
        ModelIntegrityError: el artefacto no coincide con el hash de su manifiesto.
    """
    _, manifest = load_model(challenger_dir, model_id)
    return PromotionPlan(manifest, Path(challenger_dir), Path(production_dir))


def copy_to_production(plan: PromotionPlan) -> Path:
    """Copia artefacto y manifiesto a production y verifica la copia. Devuelve la ruta."""
    plan.production_dir.mkdir(parents=True, exist_ok=True)
    model_id = plan.manifest.model_id
    for suffix in (ARTIFACT_SUFFIX, MANIFEST_SUFFIX):
        shutil.copy2(plan.challenger_dir / f"{model_id}{suffix}", plan.production_dir)

    # Si la copia no supera la comprobacion de integridad, load_model lanza el error.
    load_model(plan.production_dir, model_id)
    log.info("modelo copiado a production", extra={"model_id": model_id})
    return plan.artifact_target
