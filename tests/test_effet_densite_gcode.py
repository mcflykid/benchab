"""Verdicts fondés sur deux régions homologues et une précision explicite."""

import pytest

from benchab.fabrication.remplissage_localise import evaluer_effet_densite


@pytest.mark.parametrize(
    "temoin,variante,statut",
    [
        ((628.2, 469.8), (1833.8, 469.8), "PASS"),
        ((628.2, 469.8), (628.2, 469.8), "FAIL"),
        ((628.2, 469.8), (628.3, 469.8), "FAIL"),
        ((628.2, 469.8), (600.0, 469.8), "FAIL"),
        ((628.2, 469.8), (1833.8, 470.0), "NON VÉRIFIÉ"),
        ((0.0, 469.8), (1833.8, 469.8), "NON VÉRIFIÉ"),
        ((628.2, None), (1833.8, 469.8), "NON VÉRIFIÉ"),
    ],
)
def test_effet_avec_controle_stable_et_seuil(temoin, variante, statut):
    """Détecte l'effet, son absence, l'inversion ou un témoin non comparable."""
    a, b = (dict(zip(("zone_mm", "hors_zone_mm"), x, strict=True)) for x in (temoin, variante))
    verdict = evaluer_effet_densite(a, b)
    assert verdict["statut"] == statut
    assert verdict["seuil_mm"] == 0.1
