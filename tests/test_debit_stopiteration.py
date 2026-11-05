"""Régressions U25 : couverture de plaque et arguments incomplets."""

import json
from zipfile import ZipFile

import pytest

from benchab.controles import debit_gcode, regles


def test_plaque_sans_index_est_non_verifiee(tmp_path, capsys):
    """Un index absent laisse la plaque non vérifiée, code 2 dans le rapport et la commande."""
    source = tmp_path / "fixture.3mf"
    with ZipFile(source, "w") as archive:
        archive.writestr("Metadata/project_settings.config", "{}")
        archive.writestr("Metadata/slice_info.config", "<config><plate/></config>")
    sortie = tmp_path / "debit.json"
    assert debit_gcode.main([str(source), "--sortie", str(sortie)]) == 2
    rapport = json.loads(sortie.read_text(encoding="utf-8"))
    assert rapport["statut"] == "NON VÉRIFIÉ" and rapport["code"] == 2
    assert rapport["conforme"] is False and "index absent" in rapport["motif"]
    u25 = next(x for x in regles.controler(source)["resultats"] if x["controle"] == "U25")
    assert u25["statut"] == "NON VÉRIFIÉ"
    assert u25["rapport"] == rapport
    assert "Traceback" not in capsys.readouterr().err


@pytest.mark.parametrize("option", ["--plafonds", "--sortie"])
def test_argument_u25_incomplet_affiche_usage(option, capsys):
    """Refuse l'option sans valeur par un diagnostic d'usage, code 2, sans ouvrir d'archive."""
    assert debit_gcode.main(["fixture.3mf", option]) == 2
    erreur = capsys.readouterr().err
    assert "usage:" in erreur and option in erreur
    assert "Traceback" not in erreur
