"""Enveloppes analytiques de dépôts G17, sans approximation par corde."""

import math

import pytest

from benchab.controles.gcode_interpreter import Interpreter
from benchab.gcode.geometrie_arc import enveloppe_deposee_xyz


def mouvement_arc(*, sens=1, debut=0.0, z_fin=0.2, balayage=math.pi):
    """Construit un arc synthétique avec centre et repère déjà qualifiés en mm."""
    return {
        "start": (0.0, 0.0, 0.2),
        "end": (10.0, 0.0, z_fin),
        "length": math.hypot(math.pi * 5.0, z_fin - 0.2),
        "arc_g17": {
            "centre_xy_mm": (5.0, 0.0),
            "rayon_mm": 5.0,
            "angle_debut_rad": math.pi,
            "balayage_signe_rad": sens * balayage,
            "debut_depot_fraction": debut,
        },
    }


def test_enveloppe_ccw_inclut_extremum_courbe_et_helice():
    """Le minimum Y d'un demi-cercle CCW est hors de sa corde horizontale."""
    enveloppe = enveloppe_deposee_xyz(mouvement_arc(z_fin=4.2))

    assert enveloppe[0] == pytest.approx((0.0, 10.0))
    assert enveloppe[1] == pytest.approx((-5.0, 0.0))
    assert enveloppe[2] == pytest.approx((0.2, 4.2))


def test_enveloppe_cw_et_retraction_partielle_excluent_le_debut_non_depose():
    """Le CW couvre Y positif ; le début de dépôt réduit analytiquement son maximum."""
    complet = enveloppe_deposee_xyz(mouvement_arc(sens=-1))
    partiel = enveloppe_deposee_xyz(mouvement_arc(sens=-1, debut=0.75))

    assert complet[1] == pytest.approx((0.0, 5.0))
    assert partiel[1] == pytest.approx((0.0, 5.0 * math.sin(math.pi / 4)))


def test_enveloppe_cercle_complet_couvre_tous_les_extrema_xy():
    """Un cercle complet déposé a les quatre extrema, même si ses bouts coïncident."""
    mouvement = mouvement_arc(balayage=2 * math.pi)
    mouvement["start"] = mouvement["end"] = (5.0, 0.0, 0.2)
    mouvement["arc_g17"]["angle_debut_rad"] = 0.0
    mouvement["arc_g17"]["centre_xy_mm"] = (0.0, 0.0)

    enveloppe = enveloppe_deposee_xyz(mouvement)

    assert enveloppe[0] == pytest.approx((-5.0, 5.0))
    assert enveloppe[1] == pytest.approx((-5.0, 5.0))
    assert enveloppe[2] == pytest.approx((0.2, 0.2))


def test_enveloppe_arc_majeur_cw_ou_ccw_couvre_les_extrema_intermediaires():
    """Un balayage majeur inclut ses extrema même s'ils ne sont pas ses extrémités."""
    mouvement = mouvement_arc(sens=1, balayage=1.5 * math.pi)
    mouvement["start"] = (5.0, 0.0, 0.2)
    mouvement["end"] = (0.0, -5.0, 0.2)
    mouvement["arc_g17"].update({"centre_xy_mm": (0.0, 0.0), "angle_debut_rad": 0.0})

    enveloppe = enveloppe_deposee_xyz(mouvement)

    assert enveloppe[0] == pytest.approx((-5.0, 5.0))
    assert enveloppe[1] == pytest.approx((-5.0, 5.0))


def test_enveloppe_arc_majeur_reel_du_lecteur_conserve_le_sens_et_le_rayon_signe():
    """Le G2 R négatif qualifié garde ses trois quarts de tour hors de la corde."""
    lecteur = Interpreter()
    for ligne in ("G90", "M83", "G1 X1 Y0 Z0.2 F600"):
        lecteur.feed_line(ligne)
    mouvement = lecteur.feed_line("G2 X0 Y1 R-1 E1")

    enveloppe = enveloppe_deposee_xyz(mouvement)

    assert enveloppe[0] == pytest.approx((-1.0, 1.0))
    assert enveloppe[1] == pytest.approx((-1.0, 1.0))


def test_enveloppe_refuse_valeurs_hors_domaine():
    """Une enveloppe non bornée reste non mesurée au lieu d'autoriser un verdict."""
    mouvement = mouvement_arc()
    mouvement["arc_g17"]["rayon_mm"] = 1_000_001.0

    with pytest.raises(ValueError, match="domaine"):
        enveloppe_deposee_xyz(mouvement)
