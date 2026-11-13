"""Exerce les intersections d'une course hélicoïdale échantillonnée."""

import pytest
import trimesh

from benchab.conception.filetage_nominal import generer
from benchab.controles.course_vissage import mesurer


@pytest.mark.parametrize(("rayon", "verdict"), [(1.3, "PASS"), (0.85, "FAIL")])
def test_course_et_butee(rayon, verdict):
    """Une couronne coulisse jusqu'à une butée qui bloque le dépassement."""
    male = trimesh.creation.cylinder(radius=1, height=2)
    femelle = trimesh.creation.annulus(r_min=rayon, r_max=2, height=4)
    fond = trimesh.creation.cylinder(radius=2, height=0.5)
    fond.apply_translation([0, 0, -1.8])
    femelle = trimesh.boolean.union([femelle, fond], engine="manifold")
    rapport = mesurer(
        male, femelle, pas_mm=2, sens="droite", butee_mm=-0.4, phase_deg=0,
        course_mm=2, increment_mm=0.25, zone_male_mm=[-1, 1], zone_femelle_mm=[-2.25, 2],
    )
    if verdict == "PASS":
        assert rapport["intersection_max_mm3"] == pytest.approx(0)
        assert rapport["jeu_radial_min_butee_mm"] == pytest.approx(0.3, abs=0.003)
    else:
        assert rapport["intersection_max_mm3"] > 0.01
        assert rapport["jeu_radial_min_butee_mm"] < 0
    assert rapport["recouvrement_axial_butee_mm"] == pytest.approx(2)
    assert rapport["nombre_tours"] == 1
    assert rapport["contre_essais"]["depassement"]["intersection_mm3"] > 0
    assert rapport["statut"] == verdict


def test_course_filetee_distingue_phase_et_sens():
    """Deux pièces filetées nominales révèlent une collision de phase ou de sens."""
    cotes = dict(diametre_mm=8, pas_mm=2, source_cotes="Témoin synthétique", profondeur_radiale_mm=0.6)
    male, _ = generer(**cotes, longueur_mm=4)
    outil, _ = generer(**cotes, longueur_mm=8, role="outil-interieur", jeu_radial_mm=0.12)
    brut = trimesh.creation.cylinder(radius=6, height=7).apply_translation([0, 0, 3.3])
    femelle = trimesh.boolean.difference([brut, outil], engine="manifold")
    rapport = mesurer(
        male, femelle, pas_mm=2, sens="droite", butee_mm=0.05, phase_deg=9,
        course_mm=2, increment_mm=0.25, zone_male_mm=[0, 4], zone_femelle_mm=[-0.2, 6.8],
    )
    assert rapport["statut"] == "PASS"
    assert all(essai["intersection_mm3"] > 0.01 for essai in rapport["contre_essais"].values())
