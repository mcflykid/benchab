"""Contrat d’installation des outils de développement et de recette."""

import tomllib
from pathlib import Path


def test_uv_sync_installe_les_groupes_de_recette_et_de_developpement():
    """Vérifie les groupes uv par défaut et leurs outils, sans lancer d’installation."""
    racine = Path(__file__).resolve().parents[1]
    with (racine / "pyproject.toml").open("rb") as flux:
        projet = tomllib.load(flux)
    groupes = projet["tool"]["uv"]["default-groups"]
    assert {"dev", "reception"} <= set(groupes)
    assert any(option.startswith("playwright") for option in projet["dependency-groups"]["reception"])
    assert any(option.startswith("pytest") for option in projet["dependency-groups"]["dev"])


def test_verrou_uv_declare_les_groupes_par_defaut():
    """Vérifie que l’installation par défaut ne doit pas réécrire les groupes du verrou."""
    racine = Path(__file__).resolve().parents[1]
    with (racine / "pyproject.toml").open("rb") as flux:
        projet = tomllib.load(flux)
    with (racine / "uv.lock").open("rb") as flux:
        verrou = tomllib.load(flux)
    paquet = next(p for p in verrou["package"] if p["name"] == "benchab")
    assert paquet["default-groups"] == projet["tool"]["uv"]["default-groups"]
