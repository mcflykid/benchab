"""Maxima nominaux G17 ; les extrema ne sont ni la corde ni la vitesse atteinte."""
import math

import pytest

from benchab.controles.gcode_interpreter import Interpreter
from benchab.gcode.geometrie_arc import vitesses_axes_nominales_g17


def _arc(*commandes):
    """Construit le dernier mouvement dans le repère physique du lecteur."""
    lecteur = Interpreter()
    for ligne in commandes[:-1]:
        lecteur.feed_line(ligne)
    resultat = lecteur.feed_line(commandes[-1])
    assert resultat and not lecteur.errors
    return resultat


@pytest.mark.parametrize("op,y", [("G3", 6), ("G2", -6)])
def test_arc_court_pointe_tangentielle_dans_son_secteur(op, y):
    """La projection X atteint 6 mm/s ; la vitesse Y initiale atteint 10 mm/s."""
    mouvement = _arc("G0 X10 Y0 F600", f"{op} X8 Y{y} I-10 J0")
    assert vitesses_axes_nominales_g17(mouvement) == pytest.approx({"X": 6, "Y": 10, "Z": 0})


def test_arc_majeur_et_tour_entier_atteignent_les_extrema_interieurs():
    """Le balayage majeur et le cercle complet ne se réduisent pas aux extrémités."""
    majeur = _arc("G0 X10 Y0 F600", "G2 X8 Y6 R-10")
    tour = _arc("G0 X10 Y0 F600", "G3 I-10 J0")
    for mouvement in (majeur, tour):
        assert vitesses_axes_nominales_g17(mouvement) == pytest.approx({"X": 10, "Y": 10, "Z": 0})


def test_helice_repere_physique_et_reprise_ne_coupent_pas_la_demande():
    """La projection utilise la longueur XYZ et garde le déplacement avant dépôt."""
    mouvement = _arc("M83", "G0 X30 Y20 Z2 F600", "G92 X10 Y0 Z0", "G1 E-3",
                     "G3 X10 Y0 Z4 I-10 J0 E4")
    duree = math.hypot(math.tau * 10, 4) / 10
    assert mouvement["arc_g17"]["debut_depot_fraction"] == 0.75
    assert vitesses_axes_nominales_g17(mouvement) == pytest.approx({
        "X": math.tau * 10 / duree, "Y": math.tau * 10 / duree, "Z": 4 / duree,
    })


def test_avance_absente_et_longueur_incoherente_refusees():
    """Aucune vitesse ne découle d’une avance invalide ou de la corde."""
    mouvement = _arc("G0 X10 Y0 F600", "G3 X0 Y10 I-10 J0")
    for changement in ({"feed": 0}, {"length": math.sqrt(200)}):
        with pytest.raises(ValueError):
            vitesses_axes_nominales_g17(mouvement | changement)
