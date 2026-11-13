"""Témoins de contraintes 3D en unités N, mm et MPa."""

import pytest

from benchab.conception.contraintes_volumiques import calculer


def test_traction_retrouve_la_solution_analytique():
    """Avec Poisson nul, la traction uniforme est exacte sur les deux maillages."""
    rapport = calculer((30, 8, 6), (48, 0, 0), 1500, 0, (4, 2, 2), source_matiere="Témoin analytique")
    for essai in rapport["maillages"]:
        assert essai["deplacement_face_mm"] == pytest.approx([0.02, 0, 0], abs=1e-10)
        assert essai["reaction_appui_n"] == pytest.approx([-48, 0, 0], abs=1e-8)
        assert essai["von_mises_moyenne_par_zone_mpa"] == pytest.approx([1] * 4)
    assert rapport["validation_physique"] == "NON VÉRIFIÉ"


def test_console_compare_les_deux_maillages_et_localise_la_flexion():
    """Le raffinement rapproche la flèche d’une console élancée de la référence 1D."""
    rapport = calculer((60, 8, 6), (0, 0, -4), 1800, 0.3, (12, 3, 3), source_matiere="Témoin analytique")
    grossier, fin = rapport["maillages"]
    reference = rapport["reference_analytique"]["deplacement_face_mm"][2]
    assert fin["deplacement_face_mm"][2] < grossier["deplacement_face_mm"][2] < 0
    assert abs(fin["deplacement_face_mm"][2] - reference) < abs(grossier["deplacement_face_mm"][2] - reference)
    assert fin["reaction_appui_n"] == pytest.approx([0, 0, 4], abs=1e-6)
    zones = fin["von_mises_moyenne_par_zone_mpa"]
    assert zones[0] > zones[-1]


def test_refuse_maillage_hors_budget_et_force_nulle():
    """Les entrées sans résultat interprétable sont refusées avant l’assemblage."""
    with pytest.raises(ValueError, match="Budget"):
        calculer((60, 8, 6), (0, 0, -4), 1800, 0.3, (40, 40, 40), source_matiere="Témoin")
    with pytest.raises(ValueError, match="non nulle"):
        calculer((60, 8, 6), (0, 0, 0), 1800, 0.3, (12, 3, 3), source_matiere="Témoin")
