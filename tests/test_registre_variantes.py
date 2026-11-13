"""Les variantes d’exploration restent distinctes des candidates promues."""

from pathlib import Path

import pytest

from benchab.conception import registre_variantes as registre
from benchab.qualite.empreintes import sha256_fichier


def _variante(preuve: Path, identifiant="forme-a", **extra):
    return {
        "id": identifiant, "parent": None, "changement": "Modifier le rayon",
        "source": {"fichier": preuve.name, "sha256": sha256_fichier(preuve)},
        "apercu": {"fichier": preuve.name, "sha256": sha256_fichier(preuve)},
        "decision": "EN ATTENTE", "raison": "", **extra,
    }


def test_variante_ne_devient_candidate_qu_apres_validation(tmp_path):
    preuve = tmp_path / "apercu.html"
    preuve.write_text("source de test")
    variante = _variante(preuve, candidate="P04")
    with pytest.raises(ValueError, match="VALIDÉE"):
        registre.verifier(tmp_path, [variante])
    variante.update(decision="VALIDÉE", raison="Forme retenue")
    rapport = registre.verifier(tmp_path, [variante])
    assert rapport["candidates"] == ["P04"]
    assert rapport["explorations"] == []


def test_variante_refuse_parent_non_declare_et_apercu_perime(tmp_path):
    preuve = tmp_path / "apercu.html"
    preuve.write_text("source de test")
    variante = _variante(preuve, parent="absent")
    with pytest.raises(ValueError, match="parent"):
        registre.verifier(tmp_path, [variante])
    variante["parent"] = None
    preuve.write_text("vue modifiée")
    with pytest.raises(ValueError, match="empreinte"):
        registre.verifier(tmp_path, [variante])
