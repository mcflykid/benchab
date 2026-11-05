"""Dossier STL privé et ancien assembly.json partagent la visionneuse native, sans copier le viewer tiers."""

import json

import numpy as np
import pytest
import trimesh

from benchab.visualisation import assemblage_dossier as A


def _fixture(tmp_path):
    prive = tmp_path / "prive"
    dossier = prive / "projets/fixture/stl"
    dossier.mkdir(parents=True)
    trimesh.creation.box((10, 20, 30)).export(dossier / "Cube.stl")
    matrice = np.eye(4)
    matrice[:3, 3] = [40, 50, 60]
    assembly = {
        "primary": {"Cube": matrice.ravel(order="F").tolist()},
        "extras": [{"name": "Double", "src": "Cube", "asm": np.eye(4).ravel(order="F").tolist()}],
    }
    (dossier.parent / "assembly.json").write_text(json.dumps(assembly))
    return prive, dossier, matrice


def test_collecte_et_conversion_col_major_sans_mutation(tmp_path):
    """Collecte un STL 10×20×30 mm et sa pose 40/50/60 mm ; les sources restent intactes."""
    prive, dossier, matrice = _fixture(tmp_path)
    modeles, poses, extras, empreintes = A.collecter(dossier, prive=prive, unites="mm")
    assert set(modeles) == {"Cube"} and np.allclose(modeles["Cube"].extents, [10, 20, 30])
    assert np.array_equal(poses["Cube"], matrice)
    assert extras[0]["nom"] == "Double" and extras[0]["modele"] == "Cube"
    assert all(A.sha256_fichier(p) == sha for p, sha in empreintes.items())


def test_vue_autonome_posede_grille_assemblage_et_instances(tmp_path):
    """Produit un HTML neuf avec deux poses, une instance masquée en grille ; aucun navigateur ou moteur lancé."""
    prive, dossier, _ = _fixture(tmp_path)
    sortie = prive / "vue.html"
    chemin, rapport = A.creer(dossier, sortie, prive=prive, unites="mm")
    assert chemin == sortie and rapport["statut"] == "PASS" and rapport["validation_physique"] is False
    html = sortie.read_text()
    assert "assemblage_dossier" in html and "Grille" in html and "Assemblage" in html and "Double" in html
    with pytest.raises(FileExistsError):
        A.creer(dossier, sortie, prive=prive, unites="mm")


def test_configuration_incoherente_et_unites_non_declarees_refusees(tmp_path):
    """Une instance source absente ou une pose non affine est refusée ; jamais de modèle inventé."""
    prive, dossier, _ = _fixture(tmp_path)
    configuration = dossier.parent / "assembly.json"
    for mauvais in [{"extras": [{"name": "Extra", "src": "Absent", "asm": [1] * 16}]}, {"primary": {"Cube": [1] * 16}}]:
        configuration.write_text(json.dumps(mauvais))
        with pytest.raises(ValueError):
            A.collecter(dossier, prive=prive, unites="mm")
    with pytest.raises(ValueError, match="unités"):
        A.collecter(dossier, prive=prive, unites="cm")
    with pytest.raises(ValueError, match="privé"):
        A.collecter(tmp_path, prive=prive, unites="mm")
