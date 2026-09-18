"""Descarga de datasets historicos.

Politica (docs/DATA.md):

* **Fuente primaria: TennisMyLife.** Es la unica fuente viva y gratuita con
  estadisticas por partido, y la unica que aporta la columna `indoor`.
* **Fuente de contraste: mirror archivistico de los datos de Jeff Sackmann.**
  No se entrena con ella: sirve para auditar el solapamiento y cuantificar
  discrepancias, que es la unica verificacion de integridad disponible desde que
  el repositorio original desaparecio.

Uso no comercial, con atribucion a Jeff Sackmann y a TennisMyLife.

Nada se descarga de forma implicita: la descarga es un comando explicito
(`edgecourt data fetch`). Cada fichero se registra en un manifiesto con su
SHA-256, de modo que la ingesta sea trazable y reproducible.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from edgecourt.logging_setup import get_logger

log = get_logger("data.sources")

TML_BASE = "https://stats.tennismylife.org/data"
MIRROR_BASE = "https://raw.githubusercontent.com/Aneeshers/tennis-sackmann-archive/main/atp"

MANIFEST_NAME = "_manifest.json"

# Reintentos con backoff exponencial (brief §20). Conservador a proposito: la
# fuente es un sitio pequeno y no queremos castigarlo.
MAX_ATTEMPTS = 4
BACKOFF_BASE_SECONDS = 2.0
REQUEST_TIMEOUT_SECONDS = 60.0
# Pausa entre ficheros para no saturar el origen.
POLITE_DELAY_SECONDS = 0.5


@dataclass(frozen=True, slots=True)
class DownloadResult:
    """Resultado de la descarga de un fichero."""

    year: int
    path: Path
    sha256: str
    size_bytes: int
    url: str
    downloaded_at: str
    from_cache: bool


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fetch_with_retry(client: httpx.Client, url: str) -> bytes:
    """GET con reintentos y backoff exponencial.

    Un 404 no se reintenta: significa que el ano pedido no existe en el origen,
    y reintentarlo solo gasta tiempo y molesta al servidor.
    """
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = client.get(url)
            if response.status_code == 404:
                raise FileNotFoundError(f"No disponible en el origen: {url}")
            response.raise_for_status()
            return response.content
        except FileNotFoundError:
            raise
        except (httpx.HTTPError, OSError) as exc:
            last_error = exc
            if attempt == MAX_ATTEMPTS:
                break
            delay = BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
            log.warning(
                "descarga fallida, reintentando",
                extra={"url": url, "attempt": attempt, "delay_s": delay, "error": str(exc)},
            )
            time.sleep(delay)
    raise RuntimeError(f"No se pudo descargar {url} tras {MAX_ATTEMPTS} intentos") from last_error


def _load_manifest(directory: Path) -> dict[str, dict]:
    path = directory / MANIFEST_NAME
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_manifest(directory: Path, manifest: dict[str, dict]) -> None:
    path = directory / MANIFEST_NAME
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _download_years(
    *,
    years: range | list[int],
    destination: Path,
    url_for: callable,
    filename_for: callable,
    force: bool,
    source_name: str,
) -> list[DownloadResult]:
    destination.mkdir(parents=True, exist_ok=True)
    manifest = _load_manifest(destination)
    results: list[DownloadResult] = []

    with httpx.Client(
        timeout=REQUEST_TIMEOUT_SECONDS,
        follow_redirects=True,
        headers={"User-Agent": "EdgeCourt/0.1 (investigacion no comercial)"},
    ) as client:
        for year in years:
            target = destination / filename_for(year)
            key = str(year)

            if target.exists() and not force and key in manifest:
                results.append(
                    DownloadResult(
                        year=year,
                        path=target,
                        sha256=manifest[key]["sha256"],
                        size_bytes=manifest[key]["size_bytes"],
                        url=manifest[key]["url"],
                        downloaded_at=manifest[key]["downloaded_at"],
                        from_cache=True,
                    )
                )
                continue

            url = url_for(year)
            try:
                content = _fetch_with_retry(client, url)
            except FileNotFoundError:
                log.info("ano no disponible en el origen", extra={"year": year, "url": url})
                continue

            digest = _sha256(content)
            previous = manifest.get(key, {}).get("sha256")
            target.write_bytes(content)

            entry = {
                "sha256": digest,
                "size_bytes": len(content),
                "url": url,
                "downloaded_at": datetime.now(UTC).isoformat(),
                "source": source_name,
            }
            manifest[key] = entry
            results.append(
                DownloadResult(
                    year=year,
                    path=target,
                    sha256=digest,
                    size_bytes=len(content),
                    url=url,
                    downloaded_at=entry["downloaded_at"],
                    from_cache=False,
                )
            )

            log.info(
                "descargado",
                extra={
                    "source": source_name,
                    "year": year,
                    "bytes": len(content),
                    "changed": previous is not None and previous != digest,
                },
            )
            time.sleep(POLITE_DELAY_SECONDS)

    _save_manifest(destination, manifest)
    return results


def fetch_tennismylife(
    raw_dir: Path, years: range | list[int], *, force: bool = False
) -> list[DownloadResult]:
    """Descarga los CSV anuales de TennisMyLife (fuente primaria, ATP)."""
    return _download_years(
        years=years,
        destination=raw_dir / "tml",
        url_for=lambda y: f"{TML_BASE}/{y}.csv",
        filename_for=lambda y: f"{y}.csv",
        force=force,
        source_name="tennismylife",
    )


def fetch_sackmann_mirror(
    raw_dir: Path, years: range | list[int], *, force: bool = False
) -> list[DownloadResult]:
    """Descarga el mirror de Sackmann (solo para contraste, nunca para entrenar)."""
    return _download_years(
        years=years,
        destination=raw_dir / "sackmann_mirror",
        url_for=lambda y: f"{MIRROR_BASE}/atp_matches_{y}.csv",
        filename_for=lambda y: f"atp_matches_{y}.csv",
        force=force,
        source_name="sackmann_mirror",
    )
