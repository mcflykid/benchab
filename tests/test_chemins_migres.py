"""Ancien nom de projet : aucune confiance dans le seul nom du fichier."""

import hashlib
import json

import pytest

from benchab.donnees.base import Base
from benchab.qualite.publication import chemin_projet


def test_ancien_prefixe_exige_empreinte_de_preparation_enregistree(tmp_path):
    base = Base(tmp_path / "prive")
    try:
        projet = base.prive / "projets/demo"
        dossier = projet / "preparations/P01"
        dossier.mkdir(parents=True)
        fichier = dossier / "fixture.json"
        fichier.write_text('{"fixture":true}')
        attendu = hashlib.sha256(fichier.read_bytes()).hexdigest()
        rel = "projets/ancien/preparations/P01/fixture.json"
        base.ecrire("INSERT INTO projets(id,nom) VALUES (?,?)", ("demo", "demo"))
        with pytest.raises(ValueError, match="sans copie importée"):
            chemin_projet(base.prive, projet, rel)
        base.ecrire(
            "INSERT INTO preparations(id,projet_id,nom,empreinte,donnees_json) VALUES (?,?,?,?,?)",
            ("p1", "demo", "P01", "a" * 64, json.dumps({"tranche_3mf": {"fichier": rel, "sha256": attendu}})),
        )
        assert chemin_projet(base.prive, projet, rel) == fichier
        fichier.write_text('{"fixture":false}')
        with pytest.raises(ValueError, match="sans copie importée"):
            chemin_projet(base.prive, projet, rel)
        with pytest.raises(ValueError):
            chemin_projet(base.prive, projet, "../exterieur")
    finally:
        base.fermer()
