"""Interfaces réelles synthétiques : conflit, proximité, paroi et accessibilité."""

import json

import pytest
import trimesh

from benchab.controles.assemblage import analyser, balayer, enveloppe
from benchab.tiers import modeling as M


def scene(tmp_path, decalage):
    a = trimesh.creation.box((4, 4, 4))
    b = a.copy()
    b.apply_translation((decalage, 0, 0))
    s = trimesh.Scene()
    s.add_geometry(a, node_name="a")
    s.add_geometry(b, node_name="b")
    p = tmp_path / "fixture.glb"
    s.export(p)
    return p


@pytest.mark.parametrize("decalage,statut", [(3, "FAIL"), (4.1, "WARN"), (10, "PASS")])
def test_collisions_et_jeu(tmp_path, decalage, statut):
    rapport = analyser(scene(tmp_path, decalage))
    assert rapport["statut"] == statut
    assert rapport["validation_physique"] is False


def test_contact_voulu_reste_explicitement_signale(tmp_path):
    rapport = analyser(scene(tmp_path, 3), intentionnels=[frozenset(("a", "b"))])
    assert rapport["statut"] == "WARN"
    assert rapport["alertes"]


def test_contacts_deterministes_et_serialisables(tmp_path):
    """Un contact non autorisé donne FAIL et reste déterministe ; aucune validation physique."""
    p = scene(tmp_path, 3)
    a = analyser(p, mode="contacts", echantillons=60)
    b = analyser(p, mode="contacts", echantillons=60)
    assert a == b
    assert a["statut"] == "FAIL"
    json.dumps(a, allow_nan=False)


def test_balayer_detecte_interference_intermediaire_sans_modifier_source(tmp_path):
    """Le contrôle retrouve une collision à 45° et conserve le fichier d'entrée."""
    mobile = trimesh.creation.box((4, 0.5, 0.5))
    mobile.apply_translation((2, 0, 0))
    fixe = trimesh.creation.box((0.5, 0.5, 0.5))
    fixe.apply_translation((1.4142, 1.4142, 0))
    scene_test = trimesh.Scene()
    scene_test.add_geometry(mobile, node_name="mobile")
    scene_test.add_geometry(fixe, node_name="fixe")
    chemin = tmp_path / "mouvement.glb"
    scene_test.export(chemin)
    octets_avant = chemin.read_bytes()

    rapport = balayer(chemin, "mobile", "Z", 0, 90, 3, pivot=(0, 0, 0), jeu=0.3)

    assert rapport["statut"] == "FAIL"
    assert rapport["positions"][1]["angle_deg"] == 45
    assert rapport["positions"][1]["echecs"]
    assert chemin.read_bytes() == octets_avant


def test_balayer_valide_axe_et_nombre_positions(tmp_path):
    """Les paramètres hors contrat sont refusés avant le calcul géométrique."""
    chemin = scene(tmp_path, 10)
    with pytest.raises(ValueError, match="Axe attendu"):
        balayer(chemin, "a", "Q", 0, 90, 3)
    with pytest.raises(ValueError, match="2 à 181"):
        balayer(chemin, "a", "Z", 0, 90, 1)


def test_paroi_reelle_et_volume_ouvert_inconnu(tmp_path):
    cube = trimesh.creation.box((10, 10, 10))
    p = tmp_path / "fixture.stl"
    cube.export(p)
    assert analyser(p, mode="parois", echantillons=30, paroi=1)["statut"] == "PASS"
    cube.update_faces(list(range(len(cube.faces) - 1)))
    cube.export(p)
    assert analyser(p, mode="parois", echantillons=30)["statut"] == "NON VÉRIFIÉ"


def test_enveloppe_et_invariant_percage():
    coque = trimesh.creation.box((10, 10, 10))
    interne = trimesh.creation.box((2, 2, 2))
    assert enveloppe(coque, {"interne": interne}, paroi=1, pas=0.5)["statut"] == "PASS"
    interne.apply_translation((5, 0, 0))
    assert enveloppe(coque, {"interne": interne}, paroi=1, pas=0.5)["statut"] == "FAIL"
    assert not M.bore_pierces(coque, (0, 0, -4), (0, 0, 1), 8)
    trou = trimesh.creation.cylinder(1, 12)
    percee = trimesh.boolean.difference([coque, trou], engine="manifold")
    assert M.bore_pierces(percee, (0, 0, -4), (0, 0, 1), 8)
    with pytest.raises(ValueError, match="Budget voxel"):
        enveloppe(coque, {"interne": interne}, pas=0.001)


@pytest.mark.parametrize("autorise,statut", [(False, "FAIL"), (True, "PASS")])
def test_contact_sans_intersection_mais_jeu_nul_est_signale(tmp_path, monkeypatch, autorise, statut):
    """Un écart de 0,004 mm non autorisé donne FAIL sans exiger d'intersection ; autorisé explicitement sinon."""
    monkeypatch.setattr(M, "fit_map", lambda *a, **k: [{
        "a": "a", "b": "b", "mm": .004, "press": False, "designed": autorise,
    }])
    rapport = analyser(scene(tmp_path, 4.004), mode="contacts", echantillons=60)
    assert rapport["statut"] == statut
    assert bool(rapport["contacts_non_autorises"]) is not autorise


def test_balayer_verifie_pose_initiale_hors_intervalle(tmp_path):
    """Une collision à0° reste FAIL même quand seules les poses90°–100° sont demandées ; source intacte."""
    mobile = trimesh.creation.box((4, .5, .5))
    mobile.apply_translation((2, 0, 0))
    fixe = trimesh.creation.box((.5, .5, .5))
    fixe.apply_translation((2, 0, 0))
    modele = trimesh.Scene()
    modele.add_geometry(mobile, node_name="mobile")
    modele.add_geometry(fixe, node_name="fixe")
    fichier = tmp_path / "collision-initiale.glb"
    modele.export(fichier)
    avant = fichier.read_bytes()
    rapport = balayer(fichier, "mobile", "Z", 90, 100, 2, pivot=(0, 0, 0))
    assert all(not p["echecs"] for p in rapport["positions"])
    assert rapport["pose_initiale"]["echecs"]
    assert rapport["statut"] == "FAIL"
    assert fichier.read_bytes() == avant


def test_enveloppe_profondeur_egale_pas_reste_fail(tmp_path, monkeypatch):
    """Une profondeur égale au pas voxel signale la peau extérieure, même si pas>=paroi ; aucun PASS abusif."""
    import numpy as np

    monkeypatch.setattr(M, "breach_check", lambda *a: [("interne", 2.0, np.zeros(3))])
    coque = trimesh.creation.box((10, 10, 10))
    interne = trimesh.creation.box((2, 2, 2))
    assert enveloppe(coque, {"interne": interne}, paroi=1.2, pas=2)["statut"] == "FAIL"
