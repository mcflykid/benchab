"""Vérifie la projection du rapport, sans déduire ni déplacer une couture."""

import json
from xml.etree import ElementTree as ET

import pytest


def _rapport():
    return {
        "schema": 1,
        "source_sha256": "a" * 64,
        "objet_id": "Pièce <fixture>",
        "plaque": 1,
        "statut": "WARN",
        "regions": [
            {"id": "visible", "visibilite": "visible", "polygone_xy_mm": [[0, 0], [4, 0], [4, 1]],
             "z_min_mm": 0, "z_max_mm": 2},
        ],
        "boucles": [
            {"id": "b1", "couche": 1, "ligne_debut": 10, "point_depart_xyz_mm": [1, 0.1, 0.2],
             "region_id": "visible", "visibilite": "visible", "statut": "WARN"},
            {"id": "b2", "couche": 2, "ligne_debut": 20, "point_depart_xyz_mm": [1, 0.1, 0.4],
             "region_id": "visible", "visibilite": "visible", "statut": "WARN"},
            {"id": "b3", "couche": 3, "ligne_debut": 30, "point_depart_xyz_mm": None,
             "region_id": None, "visibilite": None, "statut": "NON VÉRIFIÉ"},
        ],
    }


def test_apercu_lie_au_sha_et_aux_points_du_rapport():
    """Les marqueurs gardent leurs lignes/couches et les inconnues sans point sont signalées."""
    from benchab.visualisation.apercu_coutures import rendre

    rapport = _rapport()
    xml = rendre(rapport)
    svg = ET.fromstring(xml)
    ns = {"s": "http://www.w3.org/2000/svg"}
    assert svg.get("data-source-sha256") == rapport["source_sha256"]
    points = svg.findall(".//s:circle", ns)
    assert len(points) == 4
    assert [(p.get("data-plan"), p.get("data-couche"), p.get("data-ligne")) for p in points] == [
        ("XY", "1", "10"), ("XY", "2", "20"), ("XZ", "1", "10"), ("XZ", "2", "20"),
    ]
    assert points[0].get("cx") == points[1].get("cx")
    assert points[2].get("cy") != points[3].get("cy")
    assert "&lt;fixture&gt;" in xml
    assert "1 boucle sans coordonnées" in xml
    assert svg.find(".//s:script", ns) is None


def test_rapport_et_apercu_publies_dans_un_dossier_neuf(tmp_path):
    """Une nouvelle publication n’écrase ni le rapport précédent ni son aperçu."""
    from benchab.visualisation.apercu_coutures import publier

    rapport = _rapport()
    cible = tmp_path / "resultat"
    resume = publier(rapport, cible)
    assert json.loads((cible / "rapport.json").read_bytes()) == rapport
    assert resume["statut"] == rapport["statut"]
    svg = (cible / "apercu.svg").read_bytes()
    with pytest.raises(FileExistsError):
        publier(rapport, cible)
    assert (cible / "apercu.svg").read_bytes() == svg
