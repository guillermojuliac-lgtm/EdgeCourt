"""Tests de la descarga de datasets.

No se toca la red: se inyecta un transporte simulado de httpx. Lo que se
comprueba es la politica de reintentos, el trazado por manifiesto y que la
cache evite descargas repetidas (brief §20 y §27).
"""

from __future__ import annotations

import json

import httpx
import pytest

from edgecourt.data import sources

CSV = b"tourney_id,match_num,winner_id,loser_id,tourney_date\n2023-1,1,A,B,20230101\n"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Los tests no deben esperar los backoff reales."""
    monkeypatch.setattr(sources.time, "sleep", lambda _s: None)


def _install_transport(monkeypatch, handler):
    real_client = httpx.Client

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(sources.httpx, "Client", factory)


def test_downloads_and_writes_manifest(tmp_path, monkeypatch):
    _install_transport(monkeypatch, lambda request: httpx.Response(200, content=CSV))

    results = sources.fetch_tennismylife(tmp_path, [2023, 2024])

    assert len(results) == 2
    assert (tmp_path / "tml" / "2023.csv").read_bytes() == CSV

    manifest = json.loads((tmp_path / "tml" / sources.MANIFEST_NAME).read_text())
    assert set(manifest) == {"2023", "2024"}
    assert manifest["2023"]["sha256"] == results[0].sha256
    assert manifest["2023"]["size_bytes"] == len(CSV)
    assert manifest["2023"]["source"] == "tennismylife"


def test_second_run_uses_cache(tmp_path, monkeypatch):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=CSV)

    _install_transport(monkeypatch, handler)
    sources.fetch_tennismylife(tmp_path, [2023])
    results = sources.fetch_tennismylife(tmp_path, [2023])

    assert len(calls) == 1, "la segunda ejecucion no debe volver a descargar"
    assert results[0].from_cache is True


def test_force_redownloads(tmp_path, monkeypatch):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=CSV)

    _install_transport(monkeypatch, handler)
    sources.fetch_tennismylife(tmp_path, [2023])
    results = sources.fetch_tennismylife(tmp_path, [2023], force=True)

    assert len(calls) == 2
    assert results[0].from_cache is False


@pytest.mark.critical
def test_retries_with_backoff_then_succeeds(tmp_path, monkeypatch):
    delays: list[float] = []
    monkeypatch.setattr(sources.time, "sleep", lambda s: delays.append(s))
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(500)
        return httpx.Response(200, content=CSV)

    _install_transport(monkeypatch, handler)
    results = sources.fetch_tennismylife(tmp_path, [2023])

    assert len(results) == 1
    assert attempts["n"] == 3
    # Backoff exponencial: cada espera dobla la anterior.
    backoffs = [d for d in delays if d >= sources.BACKOFF_BASE_SECONDS]
    assert backoffs == [2.0, 4.0]


@pytest.mark.critical
def test_gives_up_after_max_attempts(tmp_path, monkeypatch):
    _install_transport(monkeypatch, lambda request: httpx.Response(503))

    with pytest.raises(RuntimeError, match="intentos"):
        sources.fetch_tennismylife(tmp_path, [2023])


@pytest.mark.critical
def test_404_is_not_retried(tmp_path, monkeypatch):
    """Un ano inexistente no debe generar reintentos contra el origen."""
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(404)

    _install_transport(monkeypatch, handler)
    results = sources.fetch_tennismylife(tmp_path, [1800])

    assert results == []
    assert len(calls) == 1


def test_mirror_uses_its_own_layout(tmp_path, monkeypatch):
    urls = []

    def handler(request):
        urls.append(str(request.url))
        return httpx.Response(200, content=CSV)

    _install_transport(monkeypatch, handler)
    sources.fetch_sackmann_mirror(tmp_path, [2023])

    assert (tmp_path / "sackmann_mirror" / "atp_matches_2023.csv").exists()
    assert urls[0].endswith("/atp/atp_matches_2023.csv")


def test_changed_content_updates_the_digest(tmp_path, monkeypatch):
    content = {"body": CSV}
    _install_transport(monkeypatch, lambda request: httpx.Response(200, content=content["body"]))

    first = sources.fetch_tennismylife(tmp_path, [2023])
    content["body"] = CSV + b"2023-1,2,C,D,20230101\n"
    second = sources.fetch_tennismylife(tmp_path, [2023], force=True)

    assert first[0].sha256 != second[0].sha256
