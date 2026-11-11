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
from benchab.imports3d.maillage_indexe import lire as lire_indexe

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
    options = _options_vis() if connecteur == "vis-ecrou" else {"type": connecteur}
    corps, extras, rapport = C.appliquer(original, parts, COUPE, options)
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


@pytest.mark.parametrize(("forme", "langues"), [("clip-languette", 1), ("clip-double", 2)])
def test_clips_ont_un_logement_et_une_epaule_sans_validation_physique(forme, langues):
    """Le réassemblage mesure l’encombrement nominal, pas la souplesse des langues."""
    original = trimesh.creation.box((40, 40, 40))
    parties, _ = G.couper(original, COUPE)
    corps, extras, rapport = C.appliquer(original, parties, COUPE, {"type": forme})
    assert not extras
    assert rapport["clip"]["nombre_languettes"] == langues
    assert rapport["clip"]["epaulement_nominal_mm"] > 0
    assert rapport["clip"]["insertion_reelle"] == "NON VÉRIFIÉ"
    assert rapport["jeu_lateral_demande_mm"] == max(C.JEUX)
    assert rapport["reassemblage"]["statut"] == "PASS"
    assert corps["dessous"].bounds[1, 2] > 5
    assert corps["dessus"].volume < parties["dessus"].volume


def test_gorge_clip_annonce_les_deux_degagements_mesures_dans_sa_section():
    """Sous l'épaulement, la coupe du volume révèle le dégagement intérieur."""
    male, femelle, _, _, rapport = C._clip_nominal("clip-languette", 3.0, 12.0, 0.4, 1.2)
    b_male, b_femelle = (
        C._vers_trimesh(volume).section(plane_origin=[0, 0, 1], plane_normal=[0, 0, 1]).bounds
        for volume in (male, femelle)
    )
    cotes = rapport["gorge_nominale_avant_union_mm"]
    assert cotes == pytest.approx(
        {
            "jeu_exterieur": b_femelle[1, 0] - b_male[1, 0],
            "degagement_interieur": b_male[0, 0] - b_femelle[0, 0],
            "jeu_y_par_face": b_femelle[1, 1] - b_male[1, 1],
        }
    )
    assert cotes == pytest.approx({"jeu_exterieur": 0.4, "degagement_interieur": 1.6, "jeu_y_par_face": 0.4})


def test_clip_ne_reutilise_pas_un_jeu_mesure_sur_une_autre_famille():
    """Un jeu déclaré n'est prioritaire que pour le connecteur et la bobine annoncés."""
    options = {
        "type": "clip-languette",
        "bobine": "FIXTURE",
        "mesure_jeu": {
            "valeur_mm": 0.3,
            "nature": "lateral_par_face",
            "reference": "témoin synthétique",
            "bobine": "FIXTURE",
            "connecteur": "clip-double",
        },
    }
    with pytest.raises(ValueError, match="connecteur"):
        C.politique_jeu(options)
    options["mesure_jeu"]["connecteur"] = "clip-languette"
    jeu, provenance = C.politique_jeu(options)
    assert jeu == 0.3
    assert provenance["reference"] == "témoin synthétique"
    original = trimesh.creation.box((40, 40, 40))
    parties, _ = G.couper(original, COUPE)
    _, _, rapport = C.appliquer(original, parties, COUPE, options)
    controles = {c["controle"]: c for c in rapport["controles"]}
    assert controles["Qualification du jeu"]["statut"] == "NON VÉRIFIÉ"
    assert controles["Dimensions de gorge nominales"]["statut"] == "PASS"
    assert rapport["jeu_lateral_demande_mm"] == 0.3
    assert rapport["clip"]["insertion_reelle"] == "NON VÉRIFIÉ"


def test_clip_double_refuse_si_son_epaule_ne_depasse_pas_le_jeu():
    """Une dimension trop petite ne crée pas une retenue apparente dans le rapport."""
    original = trimesh.creation.box((40, 40, 40))
    parties, _ = G.couper(original, COUPE)
    with pytest.raises(ValueError, match="épaulement"):
        C.appliquer(original, parties, COUPE, {"type": "clip-double", "diametre": 2.0})


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


@pytest.mark.parametrize("format_", ["stl", "3mf", "npz"])
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
        if format_ == "3mf":
            recharge = lire_3mf(sortie / (nom + ".3mf")).to_mesh()
        elif format_ == "npz":
            recharge = lire_indexe(sortie / (nom + ".npz"))
            bilan = rapport["controles_export"][nom]["topologie_indexee"]
            assert bilan["statut"] == "PASS"
            assert bilan["avant_export"] == bilan["relecture_brute"]
            assert bilan["fusion"] == "NON APPLIQUÉE"
        else:
            recharge = trimesh.load(sortie / (nom + ".stl"), force="mesh", process=True)
        assert recharge.is_volume and recharge.bounds[0, 2] == pytest.approx(0, abs=1e-5)
        if format_ == "stl":
            assert b"units=mm" in (sortie / (nom + ".stl")).read_bytes()[:80]
        elif format_ == "3mf":
            assert verifier_nommage(sortie / (nom + ".3mf"))["statut"] == "PASS"
    with pytest.raises(FileExistsError):
        A.exporter(resultat, sortie, format_)
    plan_altere = copy.deepcopy(plan)
    source.write_bytes("source changée".encode())
    with pytest.raises(ValueError, match="Source|format"):
        A.calculer(plan_altere)


@pytest.mark.parametrize("forme", ["clip-languette", "clip-double"])
def test_clip_reouvert_depuis_un_export_3mf_ne_touche_pas_a_l_original(tmp_path, forme):
    """Deux pièces fermées se réouvrent avec des poses inverses, sans profil ni validation réelle."""
    source = tmp_path / "original.stl"
    trimesh.creation.box((40, 40, 40)).export(source)
    avant = source.read_bytes()
    plan = A.nouveau_plan(source, "mm", 2)
    plan["coupes"][0]["connecteur"] = {"type": forme}
    resultat = A.calculer(plan)
    sortie = A.exporter(resultat, tmp_path / forme, "3mf")
    rapport = json.loads((sortie / "decoupe.json").read_text())
    assert source.read_bytes() == avant
    assert rapport["reassemblage"]["statut"] == "PASS"
    assert rapport["reglages_impression"]["export"].startswith("NON PRÊT")
    assert len(rapport["fichiers"]) == 2
    for nom, pose in rapport["poses_export"].items():
        assert lire_3mf(sortie / (nom + ".3mf")).to_mesh().is_volume
        assert np.allclose(np.array(pose["vers_reassemblage"]) @ pose["vers_plateau"], np.eye(4))


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


def _options_vis():
    """Déclare la visserie fictive ; aucune cote ISO ou mesure réelle n’est présumée."""
    return {
        "type": "vis-ecrou",
        "diametre": 4,
        "source_cotes": "fixture de test",
        "lamage_diametre_mm": 8,
        "lamage_profondeur_mm": 4,
        "ecrou_sur_plats_mm": 8,
        "ecrou_profondeur_mm": 4,
        "ecrou_orientation_deg": 30,
    }


def test_vis_ecrou_traverse_et_creuse_les_deux_faces_exterieures():
    """Les logements restent sur les faces extérieures, avec un passage plus étroit au joint."""
    original = trimesh.creation.box((40, 40, 40))
    coupe = {"type": "plan", "origine": [0, 0, 0], "normale": [0, 0, 1]}
    parties, _ = G.couper(original, coupe)
    corps, extras, rapport = C.appliquer(original, parties, coupe, _options_vis())
    assert all(piece.is_volume for piece in corps.values()) and not extras
    assert rapport["reassemblage"]["statut"] == "PASS"
    vis = rapport["visserie"]
    assert vis["distance_faces_mm"] == pytest.approx(40)
    assert vis["passage_diametre_mm"] == pytest.approx(4 + 2 * max(C.JEUX))
    assert vis["lamage"]["centre_entree_mm"] == pytest.approx([0, 0, 20])
    assert vis["ecrou"]["centre_entree_mm"] == pytest.approx([0, 0, -20])
    assert vis["ajustement_reel"] == "NON VÉRIFIÉ"
    # Un point décalé du passage est absent près des deux faces, présent près du joint.
    for piece, signe in ((corps["dessous"], -1), (corps["dessus"], 1)):
        assert piece.contains([[3.2, 0, signe * 19], [3.2, 0, signe * 1]]).tolist() == [False, True]


def test_vis_ecrou_refuse_logement_trop_profond_et_coupe_courbe():
    """Un logement ne peut pas rejoindre le joint ni être calculé sur une surface courbe."""
    original = trimesh.creation.box((40, 40, 40))
    coupe = {"type": "plan", "origine": [0, 0, 0], "normale": [0, 0, 1]}
    parties, _ = G.couper(original, coupe)
    with pytest.raises(ValueError, match="joint"):
        C.appliquer(original, parties, coupe, {**_options_vis(), "lamage_profondeur_mm": 19.5})
    sans_diametre = _options_vis()
    sans_diametre.pop("diametre")
    with pytest.raises(ValueError, match="diamètre nominal"):
        C.appliquer(original, parties, coupe, sans_diametre)
    with pytest.raises(ValueError, match="coupe plane"):
        C.appliquer(original, parties, {**coupe, "type": "courbe"}, _options_vis())


def test_vis_ecrou_refuse_une_paroi_de_logement_percee():
    """Le centre peut traverser les faces alors que la garde du logement sort du côté."""
    original = trimesh.creation.box((40, 40, 40))
    coupe = {"type": "plan", "origine": [0, 0, 0], "normale": [0, 0, 1]}
    parties, _ = G.couper(original, coupe)
    _, _, rapport = C.appliquer(original, parties, coupe, {**_options_vis(), "position": [15, 0, 0]})
    assert rapport["statut"] == "FAIL"
    garde = next(c for c in rapport["controles"] if c["controle"] == "Paroi et logement dans la matière")
    assert garde["statut"] == "FAIL"
    assert garde["volume_garde_hors_matiere_mm3"] > 0


def test_vis_ecrou_exporte_deux_volumes_et_conserve_le_plan(tmp_path):
    """Le 3MF relu conserve les deux corps et le rapport garde la source des cotes."""
    source = tmp_path / "boite.stl"
    trimesh.creation.box((40, 40, 40)).export(source)
    avant = source.read_bytes()
    plan = A.nouveau_plan(source, "mm", 2)
    plan["coupes"][0].update(origine=[0, 0, 0], normale=[0, 0, 1], connecteur=_options_vis())
    sortie = A.exporter(A.calculer(plan), tmp_path / "export-vis", "3mf")
    rapport = json.loads((sortie / "decoupe.json").read_bytes())
    assert rapport["reassemblage"]["statut"] == "PASS"
    assert rapport["coupes"][0]["connecteur"]["visserie"]["source_cotes"] == "fixture de test"
    assert rapport["validation_physique"] is False
    assert len(list(sortie.glob("*.3mf"))) == 2
    assert source.read_bytes() == avant
