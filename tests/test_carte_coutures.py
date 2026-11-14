"""Le rôle modal de paroi extérieure est conservé lors du changement de couche."""

from zipfile import ZipFile

import pytest

from benchab.controles.carte_coutures import analyser


def test_carte_releve_le_debut_a_chaque_couche(tmp_path):
    """Mesure des départs séparés même si le moteur ne répète pas TYPE."""
    archive = tmp_path / "essai.3mf"
    with ZipFile(archive, "w") as z:
        z.writestr("Metadata/plate_1.gcode",
                   "G90\nM83\n;LAYER_CHANGE\n;TYPE:Outer wall\n"
                   "G1 X10 Z0.2 F600\nG1 Y1 E0.1\n"
                   ";LAYER_CHANGE\nG1 X0 Y10 Z0.4\nG1 X1 Z0.5 E0.1\n")
    bilan = analyser(archive, centre_xy_mm=(0, 0))
    ligne, = bilan["series"]
    assert [p["angle_deg"] for p in ligne["points"]] == pytest.approx([0, 90])
    assert ligne["largeur_95_deg"] == pytest.approx(90)
    assert ligne["raccords_z_variable"] == 1
