"""Mesures analytiques de dépôts G17 dans des boîtes cartésiennes."""

import pytest

from benchab.fabrication.remplissage_localise import analyser_gcode

ROLE = "Sparse infill"


def _longueurs(debut, mouvements, zones):
    gcode = "\n".join(("G90", "M83", debut, ";TYPE:Sparse infill", mouvements))
    return {nom: r[ROLE] for nom, r in analyser_gcode(gcode, zones, types=(ROLE,)).items()}


def test_cercle_entier_pas_reduit_a_son_point_final():
    """Un tour complet compte dans ses deux demi-plans malgré la corde nulle."""
    resultats = _longueurs(
        "G0 X10 Y0 F600", "G3 I-10 J0 E4",
        {"droite": (0, -10, 11, 10, -1, 1), "gauche": (-11, -10, 0, 10, -1, 1)},
    )
    assert resultats == {"droite": 31.4, "gauche": 31.4}


@pytest.mark.parametrize("op,fin_y,quart_haut,quart_bas", [
    ("G3", 10, 15.7, 0.0),
    ("G2", -10, 0.0, 15.7),
])
def test_quart_dans_le_sens_declare(op, fin_y, quart_haut, quart_bas):
    """Les deux orientations CW/CCW traversent des quadrants différents."""
    resultats = _longueurs(
        "G0 X10 Y0 F600", f"{op} X0 Y{fin_y} I-10 J0 E1",
        {"haut": (0, 0, 11, 11, -1, 1), "bas": (0, -11, 11, 0, -1, 1)},
    )
    assert resultats == {"haut": quart_haut, "bas": quart_bas}


def test_r_signe_reste_majeur():
    """Un arc R négatif contribue aux trois quarts réellement parcourus."""
    resultats = _longueurs(
        "G0 X10 Y0 F600", "G2 X0 Y10 R-10 E1",
        {"total": (-11, -11, 11, 11, -1, 1), "quart_exclu": (0, 0, 11, 11, -1, 1)},
    )
    assert resultats == {"total": 47.1, "quart_exclu": 0.0}


def test_helice_reprise_extrusion_et_z():
    """Le quart hélicoïdal dans la boîte commence après la dette d'extrusion."""
    resultats = _longueurs(
        "G0 X10 Y0 F600", "G1 E-1\nG3 X10 Y0 Z4 I-10 J0 E2",
        {"sans_depot": (-11, -11, 11, 11, 0, 1.9), "quart_depose": (-11, -11, 11, 11, 2, 3)},
    )
    assert resultats == {"sans_depot": 0.0, "quart_depose": 15.7}


def test_g92_ne_deplace_pas_la_boite_physique():
    """Le centre et le dépôt restent dans le repère physique après G92."""
    resultats = _longueurs(
        "G0 X30 Y20 F600\nG92 X10 Y0", "G3 X0 Y10 I-10 J0 E1",
        {"physique": (20, 20, 31, 31, -1, 1), "commande": (0, 0, 11, 11, -1, 1)},
    )
    assert resultats == {"physique": 15.7, "commande": 0.0}
