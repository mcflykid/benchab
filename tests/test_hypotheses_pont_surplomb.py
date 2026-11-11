"""Pont géométrique avant tranchage : hypothèse limitée, jamais un verdict physique."""
import math

import pytest
import trimesh
from shapely.geometry import Polygon

from benchab.controles.surplombs_connexes import cartographier


def _coupon(ouvert=False):
    """Extrude un profil de pont ou de surplomb simple en coordonnées mm."""
    trace = [(-5, 0), (-2, 0), (-2, 3), (2, 3)]
    trace += ([(2, 0), (5, 0), (5, 5), (-5, 5)] if not ouvert else [(2, 5), (-5, 5)])
    m = trimesh.creation.extrude_polygon(Polygon(trace), 6, engine="earcut")
    m.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, (1, 0, 0)))
    # Le coupon utilise des coordonnées exactes pour l’adjacence déclarée.
    m.vertices = m.vertices.round(9)
    assert m.is_watertight
    return m


def _plafond(m, **options):
    """Sélectionne le plafond synthétique à Z = 3 mm."""
    r = cartographier(m, source="coupon.stl", unite="mm", source_sha256="a" * 64, **options)
    [g] = [g for g in r["groupes"] if g["altitude_mm"] == {"min": 3.0, "max": 3.0}]
    return g["pont_possible"]


def test_ouverture_de_quatre_mm_entre_deux_bords_reconnue():
    """Deux côtés opposés complets donnent une hypothèse, sans accord de tranchage."""
    r = _plafond(_coupon(), portee_pont_mm=4.0)
    assert r["statut"] == "HYPOTHÈSE"
    assert r["axe"] == "X" and r["portee_mm"] == 4.0
    assert r["franchissabilite"] == "NON VÉRIFIÉE"


def test_un_surplomb_ou_une_portee_trop_grande_ne_sont_pas_qualifies():
    """Un seul bord ou une portée supérieure à la déclaration reste non vérifié."""
    a = _plafond(_coupon(ouvert=True), portee_pont_mm=4.0)
    b = _plafond(_coupon(), portee_pont_mm=3.9)
    assert a["statut"] == b["statut"] == "NON VÉRIFIÉ"
    assert "opposés" in a["raison"]
    assert "portée" in b["raison"]


def test_topologie_ouverte_ou_sha_absent_ne_qualifient_pas():
    """Une face supprimée ou une provenance absente bloquent l’hypothèse."""
    m = _coupon()
    m.update_faces(range(len(m.faces) - 1))
    assert _plafond(m, portee_pont_mm=4.0)["statut"] == "NON VÉRIFIÉ"
    with pytest.raises(ValueError, match="SHA"):
        cartographier(_coupon(), source="coupon.stl", unite="mm", portee_pont_mm=4.0)


def test_resume_et_visionneuse_separent_hypothese_et_franchissabilite():
    """La page signale l’axe et la portée sans appeler l’îlot un pont validé."""
    from benchab.controles.surplombs_connexes import _resume
    from benchab.visualisation.apercu_surplombs import _diagnostic

    r = cartographier(
        _coupon(), source="coupon.stl", unite="mm", source_sha256="a" * 64,
        plateau_z_mm=-0.2, portee_pont_mm=4,
    )
    s = _resume(r, "rapport.json")
    assert s["hypotheses_pont"] == 1 and s["appuis_et_ponts"] == "NON VÉRIFIÉS"
    d, _ = _diagnostic(r)
    marques = [m for m in d["anomalies"] if "Hypothèse géométrique" in m["precision"]]
    assert marques and all("axe X, portée 4 mm" in m["precision"] for m in marques)
    assert all("aucun accès global, pont ou appui réel" in m["limite"] for m in marques)
