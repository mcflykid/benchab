"""Chaîne intégrée sur géométrie et G-code synthétiques, sans moteur natif ni projet personnel."""

import json
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import trimesh

from benchab.controles.controles_piece import controler as controler_piece
from benchab.controles.envoi import controler as controler_envoi
from benchab.fabrication.anycubic import ecrire_3mf
from benchab.fabrication.trancher import normaliser, restaurer_noms


def prototype(fichier, identifiant="filament-fixture-permanent"):
    mesh = trimesh.creation.box((10, 10, 10))
    mesh.apply_translation((5, 5, 5))
    ecrire_3mf(
        [
            {
                "name": "Plaque de contrôle",
                "items": [
                    {
                        "name": "Cube",
                        "mesh": mesh,
                        "w": 10,
                        "h": 10,
                        "x": 10,
                        "y": 10,
                        "extruder": 1,
                        "settings": {"top_shell_layers": "8"},
                    }
                ],
            }
        ],
        {
            "filament_type": ["PLA"],
            "filament_colour": ["#FFFFFF"],
            "filament_settings_id": ["Profil synthétique"],
            "filament_ids": [identifiant],
            "filament_id": identifiant,
            "top_shell_layers": "5",
            "outer_wall_acceleration": "2000",
        },
        fichier,
        cli=True,
    )
    # Simule un projet sauvegardé et nommé par son auteur, avant le lanceur.
    with ZipFile(fichier) as archive:
        membres = {nom: archive.read(nom) for nom in archive.namelist()}
    modele = ET.fromstring(membres["Metadata/model_settings.config"])
    modele.find("plate/metadata[@key='plater_name']").set("value", "Plaque de contrôle")
    membres["Metadata/model_settings.config"] = ET.tostring(modele)
    with ZipFile(fichier, "w") as archive:
        for nom, contenu in membres.items():
            archive.writestr(nom, contenu)
    return mesh


def test_geometrie_preparation_et_reouverture_sans_modifier_reglages(tmp_path):
    source = tmp_path / "source.3mf"
    mesh = prototype(source)
    stl = tmp_path / "cube.stl"
    mesh.export(stl)
    controles = {r["controle"]: r["statut"] for r in controler_piece(stl)}
    assert controles["maillage fermé"] == "PASS"
    assert controles["volume"] == "PASS"
    avant = source.read_bytes()
    copie = tmp_path / "copie.3mf"
    rapport = normaliser(source, copie)
    with ZipFile(copie, "a") as archive:
        # Conteneur synthétique pour vérifier la restauration ; aucun tranchage natif revendiqué.
        archive.writestr("Metadata/plate_1.gcode", "G90\nM83\n;Z:0.2\n;TYPE:Outer wall\nG1 X1 Y1 F600\nG1 X5 E1\n")
    restauration = restaurer_noms(copie, rapport["noms_origine"])
    assert restauration["reouverture"] == "PASS"
    with ZipFile(copie) as archive:
        assert archive.testzip() is None
        projet = json.loads(archive.read("Metadata/project_settings.config"))
        modele = ET.fromstring(archive.read("Metadata/model_settings.config"))
        assert projet["top_shell_layers"] == "5"
        assert modele.find("object/metadata[@key='top_shell_layers']").get("value") == "8"
        assert modele.find("plate/metadata[@key='plater_name']").get("value") == "Plaque de contrôle"
        assert b"G1 X5 E1" in archive.read("Metadata/plate_1.gcode")
    assert not any(m["reglage_impression_modifie"] for m in rapport["modifications"])
    assert source.read_bytes() == avant


def test_envoi_refuse_identifiant_temporaire_et_ne_promet_pas_validation_physique(tmp_path):
    fichier = tmp_path / "prototype.3mf"
    prototype(fichier, "BENCHAB-temporaire-1")
    with ZipFile(fichier, "a") as archive:
        archive.writestr("Metadata/plate_1.gcode", "G90\nM83\n;Z:0.2\n;TYPE:Outer wall\nG1 X1 Y1 F600\nG1 X5 E1\n")
    avant = fichier.read_bytes()
    rapport = controler_envoi(fichier)
    assert rapport["statut"] == "FAIL"
    assert rapport["validation_physique"] is False
    assert fichier.read_bytes() == avant


def test_archive_non_tranchee_ne_devient_pas_envoi_conforme(tmp_path):
    fichier = tmp_path / "prototype.3mf"
    prototype(fichier)
    rapport = controler_envoi(fichier)
    assert rapport["statut"] in {"FAIL", "NON VÉRIFIÉ"}
    assert rapport["validation_physique"] is False
