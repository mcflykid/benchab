"""Attribution nominale des axes de pont à un îlot horizontal déclaré."""

import pytest

from benchab.controles.ponts_surplombs import rapprocher

TRIANGLES = [
    [[0, 0, 4], [0, 4, 4], [8, 4, 4]],
    [[0, 0, 4], [8, 4, 4], [8, 0, 4]],
]


def test_separe_le_recouvrement_xy_de_la_concordance_en_z():
    """Les deux rôles coupent la même projection ; un seul rejoint le dessous déclaré."""
    lignes = [
        "G90", "M83", "G1 X8 Y12 Z7.2", ";LAYER_CHANGE", ";HEIGHT:0.2",
        ";TYPE:Bridge", "G1 X20 E1 F1200",
        "G1 X8 Y12 Z9.2", ";LAYER_CHANGE", ";HEIGHT:0.2",
        ";TYPE:Internal Bridge", "G1 X20 E1 F1200",
    ]

    r = rapprocher(TRIANGLES, lignes, translation_mm=(10, 10, 3))

    assert r["statut"] == "PASS"
    assert r["altitude_ilot_mm"] == 7
    assert r["projection_mm2"] == 32
    assert r["roles"]["Bridge"]["longueur_projection_xy_mm"] == 8
    assert r["roles"]["Bridge"]["longueur_niveau_concordant_mm"] == 8
    assert r["roles"]["Internal Bridge"]["longueur_projection_xy_mm"] == 8
    assert r["roles"]["Internal Bridge"]["longueur_niveau_concordant_mm"] == 0
    assert r["validation_physique"] is False


def test_hauteur_ne_se_propage_pas_apres_changement_de_couche():
    """Une hauteur annoncée pour une autre couche ne qualifie aucune attribution."""
    lignes = [
        "G90", "M83", ";LAYER_CHANGE", ";HEIGHT:0.2", "G1 X0 Y2 Z4.2",
        ";LAYER_CHANGE", ";TYPE:Bridge", "G1 X8 E1",
    ]

    r = rapprocher(TRIANGLES, lignes, translation_mm=(0, 0, 0))

    assert r["statut"] == "NON VÉRIFIÉ"
    assert r["exclusions"]["hauteur_non_verifiee"] == 1
    assert r["roles"]["Bridge"]["longueur_niveau_concordant_mm"] == 0


def test_refuse_une_projection_non_horizontale():
    """Un îlot incliné n’est pas assimilé à un niveau plan."""
    triangles = [[(0, 0, 4), (0, 4, 5), (8, 4, 4)]]
    with pytest.raises(ValueError, match="horizontal"):
        rapprocher(triangles, [], translation_mm=(0, 0, 0))


def test_niveau_different_sans_nouvelle_hauteur_reste_non_verifie():
    """Même sans marqueur de couche, un ancien HEIGHT ne couvre pas un autre niveau."""
    lignes = [
        "G90", "M83", "G1 X0 Y2 Z3.2", ";HEIGHT:0.2", ";TYPE:Outer wall", "G1 X8 E1",
        "G1 X0 Y2 Z4.2", ";TYPE:Bridge", "G1 X8 E1",
    ]
    r = rapprocher(TRIANGLES, lignes, translation_mm=(0, 0, 0))
    assert r["statut"] == "NON VÉRIFIÉ"
    assert r["exclusions"]["hauteur_non_verifiee"] == 1
    assert r["roles"]["Bridge"]["longueur_niveau_concordant_mm"] == 0
