"""Chaque test dispose d'un atelier privé jetable, y compris les lectures par défaut."""

import pytest


@pytest.fixture(autouse=True)
def prive_jetable(tmp_path, monkeypatch):
    monkeypatch.setenv("BENCHAB_PRIVE", str(tmp_path / "prive"))
    monkeypatch.setenv("CADGEN_TELEMETRY", "0")
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    monkeypatch.setenv("CADGEN_STATE_DIR", str(tmp_path / "prive/cache/cadgen/etat"))
    monkeypatch.setenv("CADGEN_CACHE_DIR", str(tmp_path / "prive/cache/cadgen/cache"))
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "prive/cache/matplotlib"))


@pytest.fixture
def atelier_initialise(tmp_path):
    """Initialise explicitement la Base jetable des tests métier ; aucune unité ni commande machine.

    Opt-in : les tests d'atelier absent et d'aide restent sans cette fixture
    (docs/reference/chemins-et-atelier.md).
    """
    from benchab.donnees.base import Base

    base = Base(tmp_path / "prive", creer=True)
    try:
        return base.prive
    finally:
        base.fermer()
