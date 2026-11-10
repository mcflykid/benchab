"""Capacités locales et création de projets, sans moteur natif ni compte distant."""

import importlib.metadata
import json

import pytest

from benchab.conception.capacites_locales import A_PORTER, CAPACITES
from benchab.fabrication.anycubic import ecrire_3mf
from benchab.projets.nouveau import creer


def test_trois_capacites_et_cadgen_ne_sont_plus_annonces_absents():
    assert A_PORTER == {}
    for nom in ("dfam", "maillage_avance", "rendu_photo", "cadgen"):
        assert nom in CAPACITES
    assert importlib.metadata.version("cadgen") == "0.8.4"
    assert importlib.metadata.version("pymeshlab") == "2025.7.post1"
    assert importlib.metadata.version("meshoptimizer") == "0.2.30a0"


def test_fichier_31_plaques_refuse_avant_ecriture(tmp_path):
    cible = tmp_path / "refuse.3mf"
    with pytest.raises(ValueError, match="30 plaques"):
        ecrire_3mf([{"name": str(n), "items": []} for n in range(31)], {}, cible)
    assert not cible.exists()


@pytest.mark.parametrize("niveau", ["S", "M", "L"])
def test_nouveau_projet_n_est_jamais_public_ni_imprime(tmp_path, niveau):
    from benchab.donnees.base import Base

    base = Base(tmp_path / "prive")
    try:
        path = creer(base.prive, "fixture", niveau=niveau, base=base)
        row = json.loads(base.connexion.execute("SELECT donnees_json FROM projets").fetchone()[0])
        assert row["publication_autorisee"] is False
        assert row["suivi"]["physique"]["imprime"] is False
        assert path.is_relative_to(base.prive)
        assert (path / "src/construire.py").is_file()
    finally:
        base.fermer()
