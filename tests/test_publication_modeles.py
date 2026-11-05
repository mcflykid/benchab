"""Tous les projets privés ; exceptions vérifiables réservées aux géométries de test."""

import hashlib
import json

import pytest

from benchab.cli import main
from benchab.qualite.frontiere import verifier


@pytest.mark.parametrize("extension", ["stl", "3mf", "step", "glb", "gcode", "obj"])
def test_fichier_3d_public_interdit_meme_personnel(tmp_path, extension):
    (tmp_path / f"modele-personnel.{extension}").write_bytes(b"fixture")
    assert any("fichier 3D public interdit" in e for e in verifier(tmp_path))


def test_fixture_exige_provenance_generateur_et_empreintes(tmp_path):
    fixture = tmp_path / "tests/fixtures/cube.stl"
    fixture.parent.mkdir(parents=True)
    contenu = b"solid cube_atelier\nendsolid cube_atelier\n"
    fixture.write_bytes(contenu)
    assert verifier(tmp_path)
    generateur = tmp_path / "tests/generer_fixture.py"
    generateur.write_text("def generer():\n    return b'solid cube_atelier\\nendsolid cube_atelier\\n'\n")
    manifeste = tmp_path / "benchab/reference/fixtures-atelier.json"
    manifeste.parent.mkdir(parents=True)
    entree = {
        "origine": "atelier",
        "famille": "fixture-test",
        "generateur": "tests/generer_fixture.py",
        "sha256": hashlib.sha256(contenu).hexdigest(),
        "generateur_sha256": hashlib.sha256(generateur.read_bytes()).hexdigest(),
    }
    manifeste.write_text(json.dumps({"fichiers": {"tests/fixtures/cube.stl": entree}}))
    assert verifier(tmp_path) == []
    fixture.write_bytes(contenu + b"modifie")
    assert any("empreinte" in e for e in verifier(tmp_path))


def test_projet_dans_prive_admis_et_fausse_fixture_projet_refusee(tmp_path):
    prive = tmp_path / "prive/modele-prive-fixture"
    prive.mkdir(parents=True)
    (prive / "lune.stl").write_bytes(b"modele personnel prive")
    assert verifier(tmp_path) == []
    dossier = tmp_path / "tests/fixtures"
    dossier.mkdir(parents=True)
    (dossier / "lune.stl").write_bytes(b"copie projet")
    assert verifier(tmp_path)


def test_commande_publication_bloque_modele(tmp_path):
    (tmp_path / "modele.stl").write_bytes(b"modele non autorise")
    assert main(["--prive", str(tmp_path / "prive"), "controle", "publication", "--racine", str(tmp_path)]) == 1
