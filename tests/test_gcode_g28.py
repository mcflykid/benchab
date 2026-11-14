"""Régressions nominales de référencement et de changement d'outil ACE."""

import pytest

from benchab.controles.gcode_interpreter import Interpreter
from benchab.gcode.continuite import controler


def test_m82_partage_position_et_retraction_entre_outils(tmp_path):
    """Sans G92 à T1, ne dépose rien tant que la dette de 0,2 mm n'est pas soldée."""
    lignes = [
        "G90", "M82", ";TYPE:Outer wall", "T0", "G92 E100", "G1 Z2 F600",
        "G1 X10 E101", "G1 E100.8", "T1", "G1 X11 E100.9", "G1 X12 E101.1",
    ]
    it = Interpreter()
    mouvements = [res for ligne in lignes if (res := it.feed_line(ligne))]
    a, b = mouvements[-2:]
    assert a["tool"] == b["tool"] == 1
    assert a["extrusion"] == pytest.approx(0.1) and a["deposited"] == 0
    assert b["extrusion"] == pytest.approx(0.2) and b["deposited"] == pytest.approx(0.1)
    fichier = tmp_path / "axe-e.gcode"
    fichier.write_text("\n".join(lignes), encoding="utf-8")
    rapport = controler(fichier)
    assert rapport["statut"] == "PASS"
    assert rapport["couches"][0]["segments"] == 2


def test_g28_change_le_repere_et_exclut_le_segment_incertain(tmp_path):
    """Après G28 X Y, conserve Z, repart en XY à 0 et ne compte pas le dépôt immédiat."""
    lignes = [
        "G90", "M83", ";TYPE:Outer wall", "T0", "G1 X49 Y50 Z2 F600",
        "G1 X50 E0.1", "G28 X Y", "G1 X60 E1",
    ]
    it = Interpreter()
    mouvements = [res for ligne in lignes if (res := it.feed_line(ligne))]
    assert mouvements[-1]["start"] == (0, 0, 2)
    assert mouvements[-1]["deposited_length"] == 0
    assert any("après G28" in erreur for erreur in it.errors)
    fichier = tmp_path / "origine.gcode"
    fichier.write_text("\n".join(lignes), encoding="utf-8")
    rapport = controler(fichier)
    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert rapport["couches"][0]["segments"] == 1
    assert any("G28" in erreur for erreur in rapport["avertissements"])
