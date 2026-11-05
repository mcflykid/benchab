"""Un identifiant provisoire est interdit partout dans un fichier destiné à l’impression."""

import json
from io import BytesIO
from zipfile import ZipFile

import pytest

from benchab.controles.identifiants_filaments import _contient_marqueur, controler


@pytest.mark.parametrize("membre", ["Metadata/model_settings.config", "Metadata/plate_1.gcode", "3D/3dmodel.model"])
def test_marqueur_hors_reglages_projet_est_aussi_refuse(tmp_path, membre):
    fichier = tmp_path / "fixture.3mf"
    with ZipFile(fichier, "w") as archive:
        archive.writestr("Metadata/project_settings.config", json.dumps({"filament_id": "stable"}))
        archive.writestr(membre, "BENCHAB-temporaire-1")
    resultat = controler(fichier)
    assert resultat["statut"] == "FAIL"
    assert resultat["membres_temporaires"] == [membre]


def test_marqueur_chevauchant_blocs_et_archive_invalide(tmp_path):
    assert _contient_marqueur(BytesIO(b"x" * (1024 * 1024 - 4) + b"BENCHAB-temporaire-1"))
    assert controler(tmp_path / "absent.3mf")["statut"] == "FAIL"
