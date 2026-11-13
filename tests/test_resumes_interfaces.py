"""Préserve les rapports complets et les verdicts des contrôles d’interface."""

import json

import pytest
import trimesh

from benchab.controles import course_vissage, fentes_interface


@pytest.mark.parametrize("controle", ["vissage", "fentes"])
@pytest.mark.parametrize("mode", ["complet", "resume", "sans_rapport", "raison"])
def test_affichage_interfaces(tmp_path, capsys, controle, mode):
    """Le même diagnostic reste lisible et intégral quel que soit l’affichage."""
    source, femelle = tmp_path / "source.stl", tmp_path / "femelle.stl"
    trimesh.creation.cylinder(radius=1, height=2).export(source)
    anneau = trimesh.creation.annulus(r_min=1.3, r_max=2, height=4)
    fond = trimesh.creation.cylinder(radius=2, height=0.5).apply_translation([0, 0, -1.8])
    trimesh.boolean.union([anneau, fond], engine="manifold").export(femelle)
    destination = tmp_path / "rapport.json"
    if controle == "vissage":
        cmd = course_vissage.main
        arguments = [
            str(source), str(femelle), "--pas-mm", "2", "--sens", "droite", "--butee-mm", "-0.4",
            "--phase-deg", "0", "--course-mm", "2", "--increment-mm",
            "0.0001" if mode == "raison" else "0.25",
            "--zone-male-mm", "-1", "1", "--zone-femelle-mm", "-2.25", "2",
        ]
        cle = "poses"
    else:
        cmd = fentes_interface.main
        zone = ["4", "4", "4", "5", "5", "5"] if mode == "raison" else ["-1", "-1", "-1", "1", "1", "1"]
        arguments = [str(source), "--zone-mm", *zone, "--axe", "x", "--largeur-ligne-mm", "0.42", "--pas-mm", "0.1"]
        cle = "vides"
    if mode == "sans_rapport":
        with pytest.raises(ValueError, match="--resume exige"):
            cmd([*arguments, "--resume"], prive=tmp_path)
        return
    arguments += ["--json", str(destination)]
    if mode != "complet":
        arguments.append("--resume")
    code = cmd(arguments, prive=tmp_path)
    affiche = json.loads(capsys.readouterr().out)
    rapport = json.loads(destination.read_text())
    assert affiche["statut"] == rapport["statut"]
    if mode == "complet":
        assert cle in affiche and affiche == rapport
    else:
        assert affiche["rapport"] == str(destination)
        assert cle not in affiche
    if mode == "raison":
        assert code == 2
        assert affiche["raison"] == rapport["raison"]
    else:
        assert code == 0
