"""Contrôle d'envoi sur un extrudeur dont la case de réglage manque."""

import json
from zipfile import ZipFile

from fixtures_communes import DECL, creer_3mf_test

from benchab.controles import controle_envoi


def test_extrudeur_hors_vecteur_est_non_verifie(tmp_path, capsys):
    """T2 déclaré avec deux cases de température et débit donne NON VÉRIFIÉ, code 2, sans lancement."""
    original = creer_3mf_test(tmp_path / "original.3mf")
    source = tmp_path / "fixture.3mf"
    with ZipFile(original) as entree, ZipFile(source, "w") as sortie:
        projet = json.loads(entree.read("Metadata/project_settings.config"))
        projet.update(nozzle_temperature=["240", "220"], filament_max_volumetric_speed=["32", "16"])
        modele = entree.read("Metadata/model_settings.config").decode("utf-8")
        modele = modele.replace('key="extruder" value="1"', 'key="extruder" value="3"')
        gcode = entree.read("Metadata/plate_1.gcode").decode("utf-8")
        gcode = gcode.replace("T0", "T2").replace("extruderTemp=220", "extruderTemp=210").replace("S240", "S210")
        sortie.writestr("Metadata/project_settings.config", json.dumps(projet))
        sortie.writestr("Metadata/model_settings.config", modele)
        sortie.writestr("Metadata/plate_1.gcode", gcode)
    declaration = tmp_path / "declaration.json"
    declaration.write_text(json.dumps(DECL), encoding="utf-8")
    sortie = tmp_path / "rapport.json"
    code = controle_envoi.main([str(source), "--declaration", str(declaration), "--json", str(sortie)])
    rapport = json.loads(sortie.read_text(encoding="utf-8"))
    assert code == rapport["code"] == 2
    assert rapport["statut"] == "NON VÉRIFIÉ" and rapport["conforme_numerique"] is False
    for nom in ("température après la 1re couche (filament 3)", "débit commandé (filament 3)"):
        note = next(r for r in rapport["resultats"] if r["controle"] == nom)
        assert note["statut"] == "NON VÉRIFIÉ" and "indice ou valeur absent" in note["detail"]
    assert "Traceback" not in capsys.readouterr().err
