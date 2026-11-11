"""Le routage déclaré ACE ne permet aucune déduction par famille de filament."""

import json
from zipfile import ZipFile

import pytest

from benchab.controles.controle_envoi import controler
from benchab.machine.compatibilite_ace import evaluer_alimentation


@pytest.mark.parametrize(
    "chemin, generation, produit, slot, type_3mf, attendu",
    [
        ("ace", "ACE 2 Pro", "Anycubic TPU 95A", 1, "TPU", "FAIL"),
        ("ace", "ACE 2 Pro", "Anycubic TPU 68D", 4, "TPU", "PASS"),
        ("ace", "ACE Pro", "Anycubic TPU 95A", 2, "TPU", "FAIL"),
        ("ace", "ACE Pro", "Anycubic TPU 68D", 2, "TPU", "NON VÉRIFIÉ"),
        ("ace", "ACE 2 Pro", "TPU 95A générique", 1, "TPU", "NON VÉRIFIÉ"),
        ("ace", "ACE 2 Pro", "PETG-CF sans référence", 1, "PETG-CF", "NON VÉRIFIÉ"),
        ("ace", "ACE 2 Pro", "Anycubic TPU 68D", 4, "PLA", "NON VÉRIFIÉ"),
        ("ace", "ACE 2 Pro", "Anycubic TPU 68D", True, "TPU", "NON VÉRIFIÉ"),
        ("ace", None, "Anycubic TPU 95A", 1, "TPU", "NON VÉRIFIÉ"),
        ("arriere", None, "Anycubic TPU 95A", None, "TPU", "NON VÉRIFIÉ"),
        ("arriere", None, "Anycubic TPU 95A", 1, "TPU", "FAIL"),
        ("sechage", "ACE 2 Pro", "Anycubic TPU 95A", 1, "TPU", "NON VÉRIFIÉ"),
    ],
)
def test_acheminement_exact(chemin, generation, produit, slot, type_3mf, attendu):
    """Le produit exact et l'usage déclaré fixent la portée du verdict d'alimentation."""
    declaration = {"chemin": chemin, "generation_ace": generation, "produit": produit}
    assert evaluer_alimentation(declaration, slot, type_3mf)["acheminement"][0] == attendu


def test_bobine_carton_est_un_avertissement_independant():
    """Le carton est signalé pour l'ACE 2 Pro, même si le produit reste non vérifié."""
    declaration = {"chemin": "ace", "generation_ace": "ACE 2 Pro", "bobine": "carton"}
    resultat = evaluer_alimentation(declaration, 1, None)
    assert resultat["acheminement"][0] == "NON VÉRIFIÉ"
    assert resultat["bobine"][0] == "WARN"
    declaration["generation_ace"] = "ACE Pro"
    assert evaluer_alimentation(declaration, 1, None)["bobine"][0] == "WARN"
    declaration["generation_ace"] = "ACE générique"
    assert evaluer_alimentation(declaration, 1, None)["bobine"][0] == "NON VÉRIFIÉ"


def test_rapport_envoi_distingue_produit_et_affectation_physique(tmp_path):
    """Un produit exact incompatible donne FAIL sans inférer l'emplacement réellement chargé."""
    tranche = tmp_path / "tranche.3mf"
    declaration = tmp_path / "declaration.json"
    reglages = {
        "curr_bed_type": "Textured PEI Plate", "filament_type": ["TPU"],
        "nozzle_temperature": [220], "nozzle_temperature_initial_layer": [220],
        "filament_max_volumetric_speed": [8],
    }
    modele = (
        '<config><object id="1"><metadata key="extruder" value="1"/></object>'
        '<plate><metadata key="plater_id" value="1"/><model_instance>'
        '<metadata key="object_id" value="1"/></model_instance></plate></config>'
    )
    with ZipFile(tranche, "w") as archive:
        archive.writestr("Metadata/project_settings.config", json.dumps(reglages))
        archive.writestr("Metadata/model_settings.config", modele)
        archive.writestr("Metadata/plate_1.gcode", "T0\nG90\nM83\n;LAYER_CHANGE\nG1 X10 E1 F600\n")
    avant = tranche.read_bytes()
    declaration.write_text(json.dumps({"plateau": "Textured PEI Plate", "filaments": {"1": {
        "matiere": "TPU", "temp_1re": 220, "temp": 220, "plateau_c": 55, "debit_max": 8,
        "emplacement_ace": 1, "alimentation": {
            "chemin": "ace", "generation_ace": "ACE 2 Pro", "produit": "Anycubic TPU 95A", "bobine": "carton",
        },
    }}}))
    notes = {r["controle"]: r["statut"] for r in controler(tranche, declaration)["resultats"]}
    assert notes["acheminement déclaré (filament 1)"] == "FAIL"
    assert notes["bobine déclarée (filament 1)"] == "WARN"
    assert notes["correspondance ACE (filament 1)"] == "NON VÉRIFIÉ"
    assert tranche.read_bytes() == avant
