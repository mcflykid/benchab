"""Sondes de longueur XY conditionnelle, sans modification du lecteur exact."""

import math

import pytest


def test_quart_de_tour_et_arc_majeur_bornes_sans_verdict():
    from benchab.gcode.arcs_arrondis import intervalle_xy

    for sens, longueur in (("G3", 5 * math.pi), ("G2", 15 * math.pi)):
        rapport = intervalle_xy([10, 0], [0, 10], [-10, 0], sens=sens, demi_pas_mm=0.0005)
        assert rapport["statut"] == "NON VÉRIFIÉ"
        bas, haut = rapport["longueur_xy_conditionnelle_mm"]
        assert bas < longueur < haut and haut - bas < 0.04


def test_longueur_d_un_arc_exact_avant_arrondi_reste_dans_l_intervalle():
    from benchab.gcode.arcs_arrondis import intervalle_xy

    centre = (0.1234, 0.5678)
    rayon = 3.14159
    a, b = map(math.radians, (10, 100))
    p = (centre[0] + rayon * math.cos(a), centre[1] + rayon * math.sin(a))
    q = (centre[0] + rayon * math.cos(b), centre[1] + rayon * math.sin(b))
    def arrondir(couple):
        return tuple(round(v, 3) for v in couple)

    rapport = intervalle_xy(
        arrondir(p), arrondir(q), arrondir(tuple(c - x for c, x in zip(centre, p, strict=True))),
        sens="G3", demi_pas_mm=0.0005,
    )
    bas, haut = rapport["longueur_xy_conditionnelle_mm"]
    assert bas <= rayon * (b - a) <= haut
    assert rapport["validation_physique"] is False


@pytest.mark.parametrize(
    ("arrivee", "decalage", "raison"),
    [
        ([0, 11], [-10, 0], "rayons_incompatibles"),
        ([10, 0], [-10, 0], "balayage_discontinu"),
        ([10, 0], [0, 0], "direction_indeterminee"),
    ],
)
def test_geometrie_ambigue_ou_incompatible_reste_sans_intervalle(arrivee, decalage, raison):
    from benchab.gcode.arcs_arrondis import intervalle_xy

    rapport = intervalle_xy([10, 0], arrivee, decalage, sens="G3", demi_pas_mm=0.0005)
    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert rapport["raison"] == raison
    assert rapport["longueur_xy_conditionnelle_mm"] is None


@pytest.mark.parametrize("demi_pas", [True, 0, -0.01, math.inf, math.nan])
def test_quantification_doit_etre_explicite_finie_et_positive(demi_pas):
    from benchab.gcode.arcs_arrondis import intervalle_xy

    with pytest.raises(ValueError, match="demi-pas"):
        intervalle_xy([10, 0], [0, 10], [-10, 0], sens="G3", demi_pas_mm=demi_pas)


def test_arcs_deterministes_arrondis_restent_dans_leur_intervalle():
    """Deux sens, petites et grandes cotes ; une fenêtre établie contient la référence."""
    from random import Random

    from benchab.gcode.arcs_arrondis import intervalle_xy

    hasard = Random(134)
    for k in range(300):
        centre = (hasard.uniform(-1e6, 1e6), hasard.uniform(-1e6, 1e6))
        r = hasard.uniform(1, 100)
        a = hasard.uniform(-math.pi, math.pi)
        balayage = hasard.uniform(0.02, math.tau - 0.02)
        signe = 1 if k % 2 == 0 else -1
        b = a + signe * balayage
        p = (centre[0] + r * math.cos(a), centre[1] + r * math.sin(a))
        q = (centre[0] + r * math.cos(b), centre[1] + r * math.sin(b))
        ij = tuple(c - x for c, x in zip(centre, p, strict=True))
        result = intervalle_xy(
            *(tuple(round(v, 3) for v in couple) for couple in (p, q, ij)),
            sens="G3" if signe == 1 else "G2",
            demi_pas_mm=0.0005,
        )
        minimum, maximum = result["longueur_xy_conditionnelle_mm"]
        assert minimum <= r * balayage <= maximum
        assert result["cercle_existant_prouve"] is True


def test_cercle_compatible_avec_les_champs_arrondis_reste_conditionnel():
    """L’existence sous l’hypothèse n’est ni un verdict de débit ni une trajectoire."""
    from benchab.gcode.arcs_arrondis import intervalle_xy

    rapport = intervalle_xy([10, 0], [-10.001, 0], [-10, 0], sens="G2", demi_pas_mm=0.0005)
    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert rapport["cercle_existant_prouve"] is True
    assert rapport["longueur_xy_conditionnelle_mm"] is not None
    assert rapport["validation_physique"] is False


def test_chevauchement_radial_ne_prouve_pas_la_compatibilite_des_champs():
    """Un même centre ne peut réaliser toutes les variations radiales indépendantes."""
    from benchab.gcode.arcs_arrondis import intervalle_xy

    rapport = intervalle_xy([10, 0], [-10.0024, 0], [-10, 0], sens="G3", demi_pas_mm=0.0005)
    assert rapport["raison"] == "arrondis_incompatibles"
    assert rapport["cercle_existant_prouve"] is False
    assert rapport["longueur_xy_conditionnelle_mm"] is None
