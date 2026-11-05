"""Vraie géométrie CAO/maillage et calibration privée, sans slicer ni impression."""

import json

import numpy as np
import pytest
import trimesh
from build123d import Box, Pos

from benchab.cli import main
from benchab.conception import chanfreins, eprouvette_trous, trous


def test_calibration_inconnue_n_est_pas_appliquee(tmp_path):
    table = tmp_path / "absente.json"
    assert trous.diametre_vertical(3, table_path=table) == 3
    assert trous.diametre_vertical(3, theorique=True, table_path=table) == 3.25
    assert not table.exists()


def test_mesure_cree_une_table_privee_et_conserve_theorie(tmp_path):
    table = tmp_path / "config/calibration.json"
    avant = json.dumps(trous.THEORIE, sort_keys=True)
    eprouvette_trous.mesure(["3=2.8"], "bobine de test", table_path=table)
    assert trous.diametre_vertical(3, table_path=table) == 3.2
    assert json.dumps(trous.THEORIE, sort_keys=True) == avant
    assert json.loads(table.read_text())["mesures"][0]["bobine"] == "bobine de test"


@pytest.mark.parametrize("axe", ["X", "Y"])
def test_goutte_maillage_fermee_et_bien_orientee(tmp_path, axe):
    objet = trous.goutte_maillage(5, 20, axe=axe, table_path=tmp_path / "absente.json")
    assert objet.is_watertight and objet.is_winding_consistent and objet.volume > 0
    assert objet.extents[0 if axe == "X" else 1] == pytest.approx(20)
    assert objet.extents[2] > 5


def test_eprouvette_generee_mesurable_fermee_sans_ecrasement(tmp_path):
    sortie = tmp_path / "trous.stl"
    eprouvette_trous.generer(sortie, table_path=tmp_path / "absente.json")
    mesh = trimesh.load_mesh(sortie)
    assert mesh.is_watertight and mesh.is_winding_consistent and mesh.volume > 0
    assert np.allclose(mesh.extents, [90, 32, 16])
    avant = sortie.read_bytes()
    with pytest.raises(FileExistsError):
        eprouvette_trous.generer(sortie)
    assert sortie.read_bytes() == avant


def test_chanfrein_sur_vrai_solide_et_repli_sans_resultat_partiel():
    solide = Box(10, 10, 10)
    nouveau, rapport = chanfreins.chanfrein_base(solide, 0.5)
    assert rapport["statut"] == "PASS", rapport
    assert nouveau.volume < solide.volume
    assert solide.volume == pytest.approx(1000)
    impossible, rapport = chanfreins.chanfrein_base(solide, 100)
    assert rapport["statut"] == "WARN"
    assert impossible is solide


def test_conge_rentrant_sur_vrai_solide():
    solide = Box(10, 4, 10) + Pos(3, 3, 0) * Box(4, 10, 10)
    nouveau, rapport = chanfreins.conges_interieurs(solide, 0.5, "Z")
    assert rapport["statut"] == "PASS", rapport
    assert rapport["aretes"] == 1
    assert solide.volume == pytest.approx(640)
    assert nouveau.volume > solide.volume


def test_calibration_cli_dans_atelier_demande(tmp_path, atelier_initialise):
    """Vérifie calibration cli dans atelier demande ; atelier initialisé, aucune machine touchée."""
    prive = tmp_path / "autre atelier"
    from benchab.donnees.base import Base

    Base(prive, creer=True).fermer()
    assert main([
        "--prive", str(prive), "conception", "eprouvette-trous", "mesure", "3=2.8",
        "--bobine", "lot=fixture",
    ]) == 0
    table = prive / "config/compensation-trous.json"
    assert json.loads(table.read_text())["mesures"][0]["bobine"] == "lot=fixture"
    assert not (tmp_path / "prive/config/compensation-trous.json").exists()
