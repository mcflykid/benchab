"""Verdicts fondés sur deux régions homologues et une précision explicite."""

import pytest

from benchab import reglages as schema_reglages
from benchab.fabrication.remplissage_localise import (
    evaluer_effet_densite,
    evaluer_effet_reglage_gcode,
)


@pytest.mark.parametrize(
    "temoin,variante,statut",
    [
        ((628.2, 469.8), (1833.8, 469.8), "PASS"),
        ((628.2, 469.8), (628.2, 469.8), "FAIL"),
        ((628.2, 469.8), (628.3, 469.8), "NON VÉRIFIÉ"),
        ((628.2, 469.8), (628.4, 469.8), "NON VÉRIFIÉ"),
        ((628.2, 469.8), (628.5, 469.8), "PASS"),
        ((628.2, 469.8), (1833.8, 469.9), "NON VÉRIFIÉ"),
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


def _variante(gcode, *, densite, sha_geometrie="a" * 64, application="ASN fixture"):
    """Constitue un témoin synthétique dont réglages et géométrie sont explicites."""
    return {
        "gcode": gcode,
        "reglages": schema_reglages.Reglages(
            {"sparse_infill_density": densite, "sparse_infill_pattern": "gyroid"}
        ),
        "sha_geometrie": sha_geometrie,
        "application": application,
        "version_slicer": "2.0.0.3",
        "sha_binaire": "618b3fc1b291f30a564c4df2e87df5e4e6598927ae061e3d9a90dd119754a58d",
        "role_modificateur": "modifier_part",
        "identifiant_modificateur": "objet-1/part-zone-1",
        "sha_modificateur": "c" * 64,
    }


def _gcode(longueur_zone, longueur_controle, type_trajet="Sparse infill"):
    """Produit deux dépôts du même rôle dans les régions synthétiques."""
    return (
        f"M83\n;TYPE:{type_trajet}\nG1 X1 Y1 Z1\nG1 X{1 + longueur_zone} Y1 Z1 E1\n"
        f"G1 X41 Y1 Z1\nG1 X{41 + longueur_controle} Y1 Z1 E1\n"
    )


REGIONS = {
    "zone": [0, 0, 30, 10, 0, 10],
    "hors_zone": [40, 0, 70, 10, 0, 10],
}


def test_effet_gcode_lie_une_hausse_de_reglage_a_deux_regions_equivalentes():
    temoin = _variante(_gcode(10, 10), densite="20%")
    variante = _variante(_gcode(20, 10), densite="60%")

    rapport = evaluer_effet_reglage_gcode(temoin, variante, REGIONS, "Sparse infill", "sparse_infill_density")

    assert rapport["statut"] == "PASS"
    assert rapport["provenance_contexte"] == "DÉCLARÉ, NON AUTHENTIFIÉ"
    assert "ne sont pas authentifiées" in rapport["limite_provenance"]
    assert rapport["delta_zone_mm"] == 10
    assert rapport["delta_hors_zone_mm"] == 0
    assert rapport["valeurs_reglage"] == ["20%", "60%"]
    assert rapport["type_trajet"] == "Sparse infill"
    assert rapport["regions_mm"] == REGIONS
    assert len(rapport["sha256_gcode"]["temoin"]) == 64


def test_effet_nul_mesure_dans_les_deux_regions_est_fail():
    temoin = _variante(_gcode(10, 10), densite="20%")
    variante = _variante(_gcode(10, 10), densite="60%")

    rapport = evaluer_effet_reglage_gcode(temoin, variante, REGIONS, "Sparse infill", "sparse_infill_density")

    assert rapport["statut"] == "FAIL"
    assert rapport["delta_zone_mm"] == 0


@pytest.mark.parametrize(
    "modification",
    [
        {"sha_geometrie": "b" * 64},
        {"application": "autre moteur"},
        {"sha_binaire": "b" * 64},
        {"identifiant_modificateur": "objet-1/part-zone-2"},
        {"sha_modificateur": "d" * 64},
        {"reglages": {"sparse_infill_density": "60%", "sparse_infill_pattern": "grid"}},
    ],
)
def test_effet_reglage_refuse_les_temmoins_non_equivalents(modification):
    temoin = _variante(_gcode(10, 10), densite="20%")
    variante = _variante(_gcode(20, 10), densite="60%")
    variante.update(modification)

    rapport = evaluer_effet_reglage_gcode(temoin, variante, REGIONS, "Sparse infill", "sparse_infill_density")

    assert rapport["statut"] == "NON VÉRIFIÉ"


def test_effet_reglage_refuse_des_regions_zone_et_controle_qui_se_recouvrent():
    temoin = _variante(_gcode(10, 10), densite="20%")
    variante = _variante(_gcode(20, 10), densite="60%")
    regions = {"zone": [0, 0, 40, 10, 0, 10], "hors_zone": [20, 0, 60, 10, 0, 10]}

    rapport = evaluer_effet_reglage_gcode(temoin, variante, regions, "Sparse infill", "sparse_infill_density")

    assert rapport["statut"] == "NON VÉRIFIÉ"


def test_effet_reglage_refuse_les_regions_qui_se_touchent():
    """Refuse des boîtes qui partagent une face, pour éviter le double compte."""
    temoin = _variante(_gcode(10, 10), densite="20%")
    variante = _variante(_gcode(20, 10), densite="60%")
    regions = {"zone": [0, 0, 30, 10, 0, 10], "hors_zone": [30, 0, 70, 10, 0, 10]}

    rapport = evaluer_effet_reglage_gcode(temoin, variante, regions, "Sparse infill", "sparse_infill_density")

    assert rapport["statut"] == "NON VÉRIFIÉ"


def test_effet_reglage_refuse_les_dictionnaires_non_types():
    """Refuse un dictionnaire brut qui contourne le schéma des réglages."""
    temoin = _variante(_gcode(10, 10), densite="20%")
    variante = _variante(_gcode(20, 10), densite="60%")
    variante["reglages"] = dict(variante["reglages"])

    rapport = evaluer_effet_reglage_gcode(temoin, variante, REGIONS, "Sparse infill", "sparse_infill_density")

    assert rapport["statut"] == "NON VÉRIFIÉ"


def test_cle_presente_avec_none_ne_se_confond_pas_avec_cle_absente():
    """Traite une valeur présente à None comme invalide, jamais comme absente."""
    temoin = _variante(_gcode(10, 10), densite="20%")
    variante = _variante(_gcode(20, 10), densite="60%")
    variante["reglages"]["top_shell_layers"] = None

    rapport = evaluer_effet_reglage_gcode(temoin, variante, REGIONS, "Sparse infill", "sparse_infill_density")

    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert rapport["provenance_contexte"] == "DÉCLARÉ, NON AUTHENTIFIÉ"


def test_effet_reglage_insuffisant_ne_devient_pas_fail():
    temoin = _variante(_gcode(0, 0), densite="20%")
    variante = _variante(_gcode(0, 0), densite="60%")

    rapport = evaluer_effet_reglage_gcode(temoin, variante, REGIONS, "Sparse infill", "sparse_infill_density")

    assert rapport["statut"] == "NON VÉRIFIÉ"


@pytest.mark.parametrize(
    "type_trajet,cle_reglage",
    [
        ("Internal solid infill", "sparse_infill_density"),
        ("Sparse infill", "locked_skin_infill_pattern"),
    ],
)
def test_peau_squelette_et_roles_non_qualifies_restent_non_verifies(type_trajet, cle_reglage):
    temoin = _variante(_gcode(10, 10), densite="20%")
    variante = _variante(_gcode(20, 10), densite="60%")

    rapport = evaluer_effet_reglage_gcode(temoin, variante, REGIONS, type_trajet, cle_reglage)

    assert rapport["statut"] == "NON VÉRIFIÉ"
