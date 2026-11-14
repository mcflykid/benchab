"""Vérifie les verdicts géométriques sur une même interface avec ou sans ouverture."""

import pytest
import trimesh

from benchab.controles.fentes_interface import mesurer


@pytest.mark.parametrize(("mode", "statut"), [("ouverte", "WARN"), ("fermee", "FAIL"), ("pleine", "PASS")])
def test_fente_et_cavite(mode, statut):
    """Retrouve un vide de 0,50 mm sous une limite de 0,84 mm."""
    plein = trimesh.creation.box(extents=[4, 4, 6])
    mesh = plein
    if mode != "pleine":
        vide = trimesh.creation.box(extents=[0.5, 2, 5 if mode == "ouverte" else 4])
        if mode == "ouverte":
            vide.apply_translation([0, 0, 1])
        mesh = trimesh.boolean.difference([plein, vide], engine="manifold")
    rapport, _ = mesurer(mesh, [[-1, -1.2, -2.6], [1, 1.2, 3]], axe="x",
                         largeur_ligne_mm=0.42, pas_mm=0.1, porteuse=True)
    assert rapport["statut"] == statut
    if mode != "pleine":
        assert rapport["vides"][0]["largeur_max_mm"] == pytest.approx(0.5, abs=0.1 + 1e-9)
        assert rapport["vides"][0]["ouverture_echantillonnee"] is (mode == "ouverte")


def test_fente_annulaire_ouverte():
    """Une section radiale traverse un vide annulaire relié au dessus du solide."""
    plein = trimesh.creation.cylinder(radius=4, height=6, sections=96)
    vide = trimesh.creation.annulus(r_min=2, r_max=2.5, height=6, sections=96)
    vide.apply_translation([0, 0, 1])
    mesh = trimesh.boolean.difference([plein, vide], engine="manifold")
    rapport, coupe = mesurer(mesh, [[1.7, -0.3, -2.4], [2.8, 0.3, 3]], axe="x",
                              largeur_ligne_mm=0.42, pas_mm=0.1, porteuse=True)
    assert rapport["statut"] == "WARN"
    assert rapport["vides"][0]["profondeur_z_echantillonnee_mm"] == pytest.approx(5, abs=0.1)
    assert coupe is not None
