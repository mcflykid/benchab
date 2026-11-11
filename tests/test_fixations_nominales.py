"""Contrôles géométriques de fixations nominales ; aucune mesure d'impression."""

import math

import pytest
import trimesh

from benchab.conception.fixations import generer_outil

BASE = {
    "bloc_xyz_mm": (30, 24, 12),
    "centre_xy_mm": (15, 12),
    "profondeur_mm": 4,
    "marge_min_mm": 2,
    "source_cotes": "Cotes synthétiques de test",
}


def test_lamage_a_passage_et_epaulement():
    """Le lamage laisse un épaulement et une marge latérale vérifiés en mm."""
    outil, preuve = generer_outil("lamage", largeur_mm=10, passage_mm=4, **BASE)
    assert outil.is_volume and outil.body_count == 1
    assert outil.bounds.tolist() == [[10, 7, 0], [20, 17, 12]]
    assert outil.volume == pytest.approx(math.pi * (25 * 4 + 4 * 8), rel=0.001)
    assert preuve["marges_mm"]["epaulement"] == 3
    assert preuve["marges_mm"]["fond_hors_passage"] == 8
    assert preuve["validation_physique"] == "NON VÉRIFIÉ"


def test_insert_cylindrique_reste_borgne():
    """Le logement d’insert ne traverse pas l’épaisseur et ne déduit pas de jeu."""
    outil, preuve = generer_outil("insert_cylindrique", largeur_mm=6, **BASE)
    assert outil.is_volume
    assert outil.bounds.tolist() == [[12, 9, 0], [18, 15, 4]]
    assert preuve["convention_largeur"] == "diamètre nominal"
    assert preuve["passage_mm"] is None


def test_ecrou_hexagonal_cote_sur_plats_et_rotation():
    """La largeur donnée correspond aux plats et l’angle agit sur les marges XY."""
    options = {**BASE, "largeur_mm": 12, "passage_mm": 4}
    a, zero = generer_outil("ecrou_hexagonal", orientation_deg=0, **options)
    b, trente = generer_outil("ecrou_hexagonal", orientation_deg=30, **options)
    assert a.is_volume and b.is_volume
    assert a.volume == pytest.approx(math.sqrt(3) / 2 * 144 * 4 + 32 * math.pi, rel=0.001)
    assert b.volume == pytest.approx(a.volume)
    assert zero["convention_largeur"] == "sur plats"
    assert zero["marges_mm"]["y_min"] == pytest.approx(6)
    assert trente["marges_mm"]["x_min"] == pytest.approx(9)


@pytest.mark.parametrize("remplacement", [
    {"centre_xy_mm": (3, 12)},
    {"profondeur_mm": 11},
    {"passage_mm": 8},
    {"largeur_mm": True},
    {"source_cotes": " "},
])
def test_marges_ou_donnees_invalides_sont_refusees(remplacement):
    """Aucune géométrie n’est rendue quand une condition locale n’est pas vérifiée."""
    options = {**BASE, "largeur_mm": 10, "passage_mm": 4, **remplacement}
    with pytest.raises(ValueError):
        generer_outil("lamage", **options)


@pytest.mark.parametrize("forme,passage", [
    ("lamage", 4), ("insert_cylindrique", None), ("ecrou_hexagonal", 4),
])
def test_soustraction_dans_le_bloc_conserve_un_volume(forme, passage):
    """Le volume outil se soustrait au bloc déclaré sans fragment ni fuite."""
    outil, _ = generer_outil(forme, largeur_mm=10, passage_mm=passage, **BASE)
    bloc = trimesh.creation.box(extents=BASE["bloc_xyz_mm"])
    bloc.apply_translation((15, 12, 6))
    candidat = trimesh.boolean.difference([bloc, outil], engine="manifold")
    assert candidat.is_volume and candidat.body_count == 1
    assert candidat.volume == pytest.approx(bloc.volume - outil.volume, rel=1e-6)
