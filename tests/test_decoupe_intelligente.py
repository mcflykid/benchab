"""Coupes et connecteurs réels sur géométries générées, sans slicer ou machine."""

import copy
import json

import numpy as np
import pytest
import trimesh
from fixtures_decoupe import figurine, technique, trois_branches

from benchab.conception.decoupe import atelier as A
from benchab.conception.decoupe import connecteurs as C
from benchab.conception.decoupe import geometrie as G
from benchab.controles.regles import verifier_nommage
from benchab.imports3d.lecture_3mf import lire as lire_3mf

COUPE = {"type": "plan", "origine": [0, 0, 0], "normale": [0, 0, 1]}


def test_cou_detecte_sans_indication_de_position():
    r = G.proposer(figurine())
    assert r["coupes"][0]["normale"] == [0.0, 0.0, 1.0]
    assert 29 < r["coupes"][0]["origine"][2] < 44
    assert r["coupes"][0]["etranglement"] > 0.6
    assert "rétrécissement de section" in r["declencheurs"]
    assert r["validation_physique"] is False


def test_coupe_courbe_non_plane_fermee_et_complementaire():
    mesh = trimesh.creation.box((40, 40, 40))
    angles = np.linspace(0, 2 * np.pi, 12, endpoint=False)
    xy = np.c_[np.cos(angles), np.sin(angles)]
    xy *= 20 / np.max(np.abs(xy), axis=1)[:, None]
    points = np.c_[xy, 4 * np.sin(2 * angles)]
    assert np.linalg.matrix_rank(points - points.mean(axis=0)) == 3
    parts, r = G.couper(mesh, {"type": "courbe", "points": points.tolist(), "fermee": True})
    assert r["reassemblage"]["statut"] == "PASS"
    assert all(p.is_volume for p in parts.values())
    # La couture ne peut pas être remplacée discrètement par le plan moyen.
    z_a = parts["dessous"].vertices[:, 2]
    assert np.count_nonzero((z_a > 0.1) & (z_a < 8)) > 5
    with pytest.raises(ValueError, match="fermée"):
        G.couper(mesh, {"type": "courbe", "points": points.tolist(), "fermee": False})


@pytest.mark.parametrize("connecteur", C.CATALOGUE)
def test_chaque_connecteur_ferme_se_reassemble_sans_interpenetration(connecteur):
    original = trimesh.creation.box((40, 40, 40))
    parts, _ = G.couper(original, COUPE)
    corps, extras, rapport = C.appliquer(original, parts, COUPE, {"type": connecteur})
    assert rapport["statut"] != "FAIL"
    assert rapport["reassemblage"]["statut"] == "PASS"
    assert not rapport["reassemblage"]["interpenetrations"]
    assert all(p.is_volume for p in (corps | extras).values())
    assert rapport["validation_physique"] is False
    if connecteur == "douille":
        assert set(extras) == {"goupille"}
        assert corps["dessous"].volume < parts["dessous"].volume
    elif connecteur == "rotule-double":
        assert set(extras) == {"axe-double"}
    elif connecteur == "canal-tige":
        assert rapport["tige"]["longueur_a_couper_mm"] == pytest.approx(40)
    elif connecteur.startswith("cheville-"):
        assert corps["dessous"].bounds[1, 2] > 5
        assert corps["dessus"].volume < parts["dessus"].volume
    if connecteur != "aucun":
        assert any(c["statut"] == "NON VÉRIFIÉ" for c in rapport["controles"])


def test_jeu_mesure_prioritaire_mais_bobine_differente_refusee():
    options = {
        "bobine": "FIXTURE_A",
        "mesure_jeu": {
            "valeur_mm": 0.3,
            "nature": "radial",
            "reference": "FIXTURE: déclaration synthétique, pas mesure réelle",
            "bobine": "FIXTURE_A",
        },
    }
    assert C.politique_jeu(options)[0] == 0.3
    options["bobine"] = "FIXTURE_B"
    with pytest.raises(ValueError, match="bobine"):
        C.politique_jeu(options)
    valeur, origine = C.politique_jeu({})
    assert valeur == max(C.JEUX)
    assert origine["statut"] == "NON MESURÉ"


def test_paroi_insuffisante_et_pieces_deplacees_ne_passent_pas():
    original = trimesh.creation.box((40, 40, 40))
    parts, _ = G.couper(original, COUPE)
    _, _, rapport = C.appliquer(original, parts, COUPE, {"type": "douille", "longueur": 70})
    assert rapport["statut"] == "FAIL"
    parts["dessus"].apply_translation([1, 0, 0])
    with pytest.raises(ValueError, match="réassemblent"):
        C.appliquer(original, parts, COUPE)


def test_volumes_source_chevauchants_exigent_attribution_explicite():
    a = trimesh.creation.box((20, 20, 20))
    b = a.copy().apply_translation([10, 0, 0])
    parts, r = A.separer_identifiables({"A": a, "B": b})
    assert parts == {} and r["statut"] == "NON VÉRIFIÉ"
    parts, r = A.separer_identifiables({"A": a, "B": b}, ["A", "B"])
    original = trimesh.boolean.union([a, b], engine="manifold")
    assert G.reassemblage(original, parts)["statut"] == "PASS"
    assert r["priorite_explicite"] == ["A", "B"]


@pytest.mark.parametrize("format_", ["stl", "3mf"])
def test_chaine_export_reouvert_mm_pose_reversible_et_original_intact(tmp_path, format_):
    """Vérifie l’export géométrique et son état NON PRÊT sans profil configuré, en mm."""
    source = tmp_path / "original.stl"
    trimesh.creation.box((40, 40, 40)).export(source)
    avant = source.read_bytes()
    plan = A.nouveau_plan(source, "mm", 2)
    resultat = A.calculer(plan)
    sortie = A.exporter(resultat, tmp_path / format_, format_)
    rapport = json.loads((sortie / "decoupe.json").read_text())
    assert source.read_bytes() == avant
    assert rapport["reassemblage"]["statut"] == "PASS"
    assert rapport["reglages_impression"]["export"].startswith("NON PRÊT")
    assert len(rapport["fichiers"]) == 3  # deux pièces et une goupille
    for nom, pose in rapport["poses_export"].items():
        assert np.allclose(np.array(pose["vers_reassemblage"]) @ pose["vers_plateau"], np.eye(4))
        recharge = (
            lire_3mf(sortie / (nom + ".3mf")).to_mesh()
            if format_ == "3mf"
            else trimesh.load(sortie / (nom + ".stl"), force="mesh", process=True)
        )
        assert recharge.is_volume and recharge.bounds[0, 2] == pytest.approx(0, abs=1e-5)
        if format_ == "stl":
            assert b"units=mm" in (sortie / (nom + ".stl")).read_bytes()[:80]
        else:
            assert verifier_nommage(sortie / (nom + ".3mf"))["statut"] == "PASS"
    with pytest.raises(FileExistsError):
        A.exporter(resultat, sortie, format_)
    plan_altere = copy.deepcopy(plan)
    source.write_bytes("source changée".encode())
    with pytest.raises(ValueError, match="Source|format"):
        A.calculer(plan_altere)


def test_modele_technique_propose_reduction_des_surplombs():
    rapport = G.proposer(technique(), 3)
    assert "surplombs dans la pose source" in rapport["declencheurs"]
    assert rapport["coupes"]
    assert any(
        c["comparaison"]["decoupe"]["surplomb_mm2"] < c["comparaison"]["monobloc"]["surplomb_mm2"]
        for c in rapport["alternatives"]
    )
    coupe = rapport["coupes"][0]
    parties, _ = G.couper(technique(), coupe)
    assert all(G.orienter(p, coupe["normale"])[2]["surplomb_mm2"] < 1e-5 for p in parties.values())


def test_trois_branches_un_plan_quatre_pieces_et_trois_joints(tmp_path):
    source = tmp_path / "branches.stl"
    trois_branches().export(source)
    plan = A.nouveau_plan(source, "mm", 4)
    assert len(plan["coupes"]) == 1
    assert plan["coupes"][0]["nombre_volumes"] == 4
    resultat = A.calculer(plan)
    assert len(resultat["pieces"]) == 4
    assert len(resultat["accessoires"]) == 3
    assert resultat["rapport"]["reassemblage"]["statut"] == "PASS"
    assert resultat["rapport"]["coupes"][0]["connecteur"]["nombre_joints"] == 3


def test_stl_volumes_chevauchants_separes_sur_choix_explicite(tmp_path):
    source = tmp_path / "volumes.stl"
    a = trimesh.creation.box((20, 20, 20))
    b = a.copy().apply_translation([10, 0, 0])
    trimesh.util.concatenate([a, b]).export(source)
    original, volumes, _ = A.charger(source, "mm")
    assert len(volumes) == 2
    assert original.volume == pytest.approx(12000)
    with pytest.raises(ValueError, match="priorité"):
        A.nouveau_plan(source, "mm", 2, separer_volumes_source=True)
    plan = A.nouveau_plan(source, "mm", 2, separer_volumes_source=True, priorite_volumes=["Volume1", "Volume2"])
    assert not plan["coupes"]
    resultat = A.calculer(plan)
    assert len(resultat["pieces"]) == 2
    assert resultat["rapport"]["reassemblage"]["statut"] == "PASS"


@pytest.mark.parametrize("nombre", [1, 2, 3])
def test_nombre_de_pieces_ajuste_la_geometrie(tmp_path, nombre):
    source = tmp_path / "nombre.stl"
    trimesh.creation.box((40, 40, 40)).export(source)
    plan = A.nouveau_plan(source, "mm", nombre)
    for coupe in plan["coupes"]:
        coupe["connecteur"] = {"type": "aucun"}
    resultat = A.calculer(plan)
    assert len(resultat["pieces"]) == nombre
    assert resultat["rapport"]["reassemblage"]["statut"] == "PASS"


def test_longueur_tige_prise_sur_son_axe_pas_sur_la_boite_globale():
    cube = trimesh.creation.box((40, 40, 40))
    tour = trimesh.creation.box((4, 4, 30)).apply_translation([15, 0, 30])
    original = trimesh.boolean.union([cube, tour], engine="manifold")
    parties, _ = G.couper(original, COUPE)
    _, _, rapport = C.appliquer(original, parties, COUPE, {"type": "canal-tige"})
    assert original.extents[2] == 65
    assert rapport["tige"]["longueur_a_couper_mm"] == pytest.approx(40)


def test_export_interdit_dans_preparation_figee(tmp_path):
    with pytest.raises(ValueError, match="figée"):
        A.sauver_plan({"source": {"fichier": "non lu"}}, tmp_path / "preparations/P01/choix.json")
    assert not (tmp_path / "preparations").exists()


def test_un_maillage_ouvert_n_est_pas_repare_en_silence(tmp_path):
    mesh = trimesh.creation.box((20, 20, 20))
    mesh.update_faces(np.arange(len(mesh.faces) - 1))
    source = tmp_path / "ouvert.stl"
    mesh.export(source)
    avant = source.read_bytes()
    with pytest.raises(ValueError, match="fermé"):
        A.nouveau_plan(source, "mm", 2)
    assert source.read_bytes() == avant


def test_orientation_relue_avec_effort_declare_et_pose_exportee(tmp_path):
    source = tmp_path / "effort.stl"
    trimesh.creation.box((40, 40, 40)).export(source)
    plan = A.nouveau_plan(source, "mm", 2)
    plan["coupes"][0]["connecteur"]["efforts"] = [[1, 0, 0]]
    resultat = A.calculer(plan)
    sortie = A.exporter(resultat, tmp_path / "effort-export")
    rapport = json.loads((sortie / "decoupe.json").read_text())
    orientation = next(
        c for c in rapport["coupes"][0]["connecteur"]["controles"] if c["controle"] == "Orientation du connecteur"
    )
    assert orientation["poses"]
    assert orientation["risque_couches_max"] > 0.5
    assert orientation["statut"] == "WARN"


def test_choix_automatique_tient_compte_du_logement_sans_modifier_un_choix_manuel(tmp_path):
    source = tmp_path / "section-etroite.stl"
    trimesh.creation.box((30, 20, 20)).export(source)
    plan = A.nouveau_plan(source, "mm", 3)
    assert plan["ajustements_automatiques"]
    assert any(c["connecteur"]["type"] == "aucun" for c in plan["coupes"])
    resultat = A.calculer(plan)
    assert len(resultat["pieces"]) == 3 and resultat["rapport"]["statut"] != "FAIL"
    manuel = copy.deepcopy(plan)
    manuel["coupes"][1]["connecteur"] = {"type": "douille", "diametre": 15}
    avant = copy.deepcopy(manuel)
    with pytest.raises(ValueError, match="Section|section"):
        A.calculer(manuel)
    assert manuel == avant


def test_paroi_du_canal_verifiee_jusque_dans_le_col_eloigne_du_joint():
    import manifold3d as mf

    from benchab.conception.decoupe import _vers_trimesh

    corps = mf.Manifold.cylinder(30, 10, 10, 64).translate([0, 0, -20])
    col = mf.Manifold.cylinder(10, 3.8, 3.8, 64).translate([0, 0, 10])
    original = _vers_trimesh(corps + col)
    parties, _ = G.couper(original, COUPE)
    _, _, rapport = C.appliquer(original, parties, COUPE, {"type": "canal-tige"})
    assert rapport["tige"]["longueur_a_couper_mm"] == pytest.approx(40)
    assert 3.8 - (6 / 2 + 0.6) == pytest.approx(0.2)
    controle = next(c for c in rapport["controles"] if c["controle"] == "Paroi et logement dans la matière")
    assert controle["statut"] == "FAIL"
    assert rapport["statut"] == "FAIL"
