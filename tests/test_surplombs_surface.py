"""Le surplomb se mesure depuis la verticale et se compare au procédé déclaré."""

import numpy as np
import pytest
import trimesh

from benchab.controles.surplombs_surface import analyser


def test_surface_paroi_et_zone_pleine_appliquent_leurs_criteres():
    """Le même angle de 50° dépasse 45° et passe le critère de demi-ligne."""
    angle = np.radians(50)
    progression = np.tan(angle)
    angles = [[0, 0, 0], [0, 1, 0], [progression, 0, 1],
              [3, 0, 0], [3, 1, 0], [3 + progression, 0, 1]]
    mesh = trimesh.Trimesh(vertices=angles, faces=[[0, 1, 2], [3, 4, 5]], process=False)
    zones = [
        {"nom": "paroi", "type": "paroi_fine", "boite_mm": [[-1, -1, -1], [2, 2, 2]],
         "angle_max_deg": 45, "source": "témoin"},
        {"nom": "masse", "type": "zone_pleine", "boite_mm": [[2, -1, -1], [6, 2, 2]],
         "hauteur_couche_mm": 0.15, "largeur_ligne_mm": 0.42, "source": "profil témoin"},
    ]
    rapport = analyser(mesh, zones)
    a, b = rapport["zones"]
    assert a["angle_max_deg"] == pytest.approx(50)
    assert a["statut"] == "WARN"
    assert b["statut"] == "PASS"
    assert b["decalage_max_mm"] == pytest.approx(0.15 * progression)


def test_surface_zone_vide_reste_non_verifiee():
    """Une zone sans face descendante ne certifie pas une couverture."""
    mesh = trimesh.creation.box()
    zone = {"nom": "vide", "type": "paroi_fine", "boite_mm": [[10, 10, 10], [12, 12, 12]],
            "angle_max_deg": 45, "source": "témoin"}
    assert analyser(mesh, [zone])["statut"] == "NON VÉRIFIÉ"
