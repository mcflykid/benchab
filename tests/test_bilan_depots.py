"""Bilan des dépôts par rôle, outil et zone Z, sans accès machine."""

import json
from zipfile import ZipFile

import pytest

from benchab.controles.bilan_depots import analyser


def _archive(tmp_path, *, densite=True):
    """Construit un témoin en mm avec un dépôt et un déplacement sans extrusion."""
    p = tmp_path / "fixture.3mf"
    with ZipFile(p, "w") as z:
        profil = {"filament_diameter": ["2"]}
        if densite:
            profil["filament_density"] = ["1.25"]
        z.writestr("Metadata/project_settings.config", json.dumps(profil))
        z.writestr("Metadata/slice_info.config",
                   '<config><plate><metadata key="index" value="1"/><filament id="1"/></plate></config>')
        z.writestr("Metadata/plate_1.gcode",
                   "G90\nM83\nG1 Z0.2 F600\n;TYPE:Outer wall\n"
                   "EXCLUDE_OBJECT_START NAME=Corps\nG1 X10 E2 F600\nG1 X20 F600\n"
                   "EXCLUDE_OBJECT_END NAME=Corps\n")
    return p


def test_bilan_depots_repartit_le_filament_et_exclut_le_trajet_a_vide(tmp_path):
    """Vérifie la conversion du diamètre et de la densité en grammes."""
    resultat = analyser(_archive(tmp_path), bornes_z=[0.2, 1.0])
    ligne, = resultat["groupes"]
    assert resultat["statut"] == "PASS"
    assert (ligne["plaque"], ligne["objet"], ligne["type"], ligne["filament"]) == (1, "Corps", "Outer wall", 1)
    assert ligne["zone_z"] == 1
    assert ligne["filament_mm"] == 2
    assert ligne["matiere_g"] == pytest.approx(0.0078539816)
    assert ligne["temps_depot_nominal_s"] == 1


def test_bilan_depots_densite_inconnue_conserve_millimetres_et_temps(tmp_path):
    """Une conversion indisponible ne remplace jamais la masse par zéro."""
    resultat = analyser(_archive(tmp_path, densite=False))
    assert resultat["statut"] == "NON VÉRIFIÉ"
    assert resultat["groupes"][0]["matiere_g"] is None
    assert resultat["groupes"][0]["filament_mm"] == 2


def test_cli_detail_restitue_le_rapport_complet(tmp_path, capsys):
    """Le détail conserve tous les groupes du JSON écrit et le même verdict."""
    from benchab.controles.bilan_depots import main

    source = _archive(tmp_path, densite=False)
    sortie = tmp_path / "bilan.json"
    assert main([str(source), "--sortie", str(sortie), "--detail"], prive=tmp_path) == 2
    assert json.loads(capsys.readouterr().out) == json.loads(sortie.read_text())
