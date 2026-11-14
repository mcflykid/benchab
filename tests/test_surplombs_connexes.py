"""Contrats de la cartographie numérique avant tranchage, sans slicer ni machine."""

import hashlib
import json
import os
import re
import struct

import numpy as np
import pytest
import trimesh

from benchab.cli import CONTROLES_DIRECTS
from benchab.controles import surplombs_connexes as sc


def test_controle_est_accessible_par_la_route_commune():
    """La cartographie rejoint la route directe mesurée de la CLI."""
    assert CONTROLES_DIRECTS["surplombs-connexes"] == "surplombs_connexes"


def test_cartographie_groupe_faces_connexes_et_separe_composants():
    """Deux triangles partageant une arête forment un groupe ; une coque disjointe garde son composant."""
    sommets = np.array([
        [0, 0, 2], [1, 0, 2], [1, 1, 2], [0, 1, 2],
        [4, 0, 2], [5, 0, 2], [4.5, 1, 2],
    ], dtype=float)
    maillage = trimesh.Trimesh(vertices=sommets, faces=[[0, 2, 1], [0, 3, 2], [4, 6, 5]], process=False)

    rapport = sc.cartographier(maillage, source="fixture.stl", unite="mm")

    assert rapport["statut"] == "RENSEIGNÉE"
    assert len(rapport["groupes"]) == 2
    assert [g["nb_faces"] for g in rapport["groupes"]] == [2, 1]
    assert len({g["composante_maillage"] for g in rapport["groupes"]}) == 2
    assert rapport["groupes"][0]["aire_mm2"] == 1.0
    assert rapport["groupes"][0]["hauteur_mm"]["min"] == 2.0
    assert rapport["groupes"][0]["angle_surface_horizontale_deg"]["max"] == 0.0
    assert all(g["acces_plateau"]["statut"] == "NON VÉRIFIÉ" for g in rapport["groupes"])
    assert all(g["pont_possible"]["statut"] == "NON VÉRIFIÉ" for g in rapport["groupes"])


def test_cartographie_garde_distincts_deux_plaques_de_surplomb():
    """Des faces non adjacentes ne sont pas fusionnées par proximité spatiale seule."""
    sommets = np.array([
        [0, 0, 3], [1, 0, 3], [0, 1, 3],
        [1.1, 0, 3], [2.1, 0, 3], [1.1, 1, 3],
    ], dtype=float)
    maillage = trimesh.Trimesh(vertices=sommets, faces=[[0, 2, 1], [3, 5, 4]], process=False)

    rapport = sc.cartographier(maillage, source="fixture.stl", unite="mm")

    assert len(rapport["groupes"]) == 2


def test_angle_incline_et_hauteur_sont_exprimees_avec_unites():
    """La pente du plan est donnée en degrés et l'intervalle vertical en mm."""
    maillage = trimesh.Trimesh(
        vertices=[[0, 0, 2], [2, 0, 2], [0, 2, 3]],
        faces=[[0, 2, 1]],
        process=False,
    )

    groupe = sc.cartographier(maillage, source="pente.stl", unite="mm")["groupes"][0]

    assert groupe["angle_surface_horizontale_deg"]["moyenne_ponderee"] == 26.565051
    assert groupe["hauteur_mm"]["min"] == 2.0
    assert groupe["hauteur_mm"]["max"] == 3.0
    assert groupe["hauteur_mm"]["etendue"] == 1.0


def test_acces_obstrue_et_pont_geometrique_restant_non_verifies():
    """Une face sous un obstacle et un plafond géométrique ne deviennent pas des verdicts physiques."""
    sous_face = trimesh.Trimesh(
        vertices=[[0, 0, 4], [2, 0, 4], [2, 2, 4], [0, 2, 4]],
        faces=[[0, 2, 1], [0, 3, 2]],
        process=False,
    )
    obstacle = trimesh.Trimesh(
        vertices=[[1, 0, 0], [1, 2, 0], [1, 2, 3], [1, 0, 3]],
        faces=[[0, 1, 2], [0, 2, 3]],
        process=False,
    )
    scene = trimesh.util.concatenate([sous_face, obstacle])

    rapport = sc.cartographier(scene, source="obstacle-fixture.stl", unite="mm")

    assert len(rapport["groupes"]) == 1
    groupe = rapport["groupes"][0]
    assert groupe["acces_plateau"]["statut"] == "NON VÉRIFIÉ"
    assert groupe["pont_possible"]["statut"] == "NON VÉRIFIÉ"
    assert "aucun test" in groupe["acces_plateau"]["raison"]


def test_cartographie_refuse_unite_non_etablie():
    """Aucune hauteur ou aire métrique n'est publiée si l'unité du maillage manque."""
    with np.testing.assert_raises_regex(ValueError, "unité"):
        sc.cartographier(trimesh.creation.box(), source="piece.stl", unite=None)


def test_cli_ecrit_json_neuf_sans_toucher_au_maillage(tmp_path, monkeypatch):
    """Le parcours CLI écrit un rapport privé neuf, sans modifier l'entrée STL."""
    maillage = trimesh.creation.box(extents=[2, 2, 2])
    source = tmp_path / "fixture.stl"
    maillage.export(source)
    empreinte = source.read_bytes()
    sortie = tmp_path / "rapport.json"

    flux_charge = {}
    charge_originale = sc.trimesh.load_mesh

    def charger(flux, **options):
        flux_charge["octets"] = flux.getvalue()
        flux_charge["options"] = options
        return charge_originale(flux, **options)

    monkeypatch.setattr(sc.trimesh, "load_mesh", charger)
    assert sc.main([str(source), "--unite-mm", "--sortie", str(sortie)]) == 0
    rapport = json.loads(sortie.read_text())

    assert rapport["statut"] == "RENSEIGNÉE"
    assert rapport["unite"] == "mm"
    assert flux_charge["octets"] == empreinte
    assert flux_charge["options"] == {"file_type": "stl", "process": False}
    assert rapport["source_sha256"] == hashlib.sha256(flux_charge["octets"]).hexdigest()
    assert source.read_bytes() == empreinte


def test_stl_exporte_et_relu_reconstruit_connexite_par_coordonnees_exactes(tmp_path):
    """Le STL répète les sommets des triangles, mais garde la face plane dans un seul groupe connexe."""
    source = tmp_path / "cube.stl"
    trimesh.creation.box(extents=[2, 2, 2]).export(source)
    relu = trimesh.load_mesh(source, process=False)
    assert len(relu.vertices) == len(relu.faces) * 3

    rapport = sc.cartographier(relu, source=source, unite="mm")

    assert len(rapport["groupes"]) == 1
    assert rapport["groupes"][0]["nb_faces"] == 2
    assert rapport["topologie_maillage"]["statut"] == "RENSEIGNÉE"


def test_topologie_signale_arete_non_manifold_et_orientation_incoherente():
    """Les diagnostics géométriques signalent l'ambiguïté sans réorienter les triangles."""
    adjacency, topologie = sc._adjacence_exacte(np.asarray([
        [[0, 0, 0], [1, 0, 0], [0, 1, 1]],
        [[0, 0, 0], [1, 0, 0], [0, -1, 1]],
        [[0, 0, 0], [1, 0, 0], [0, 0, -1]],
    ], dtype=float))

    assert len(adjacency) == 2
    assert topologie["aretes_non_manifold"] == 1
    assert topologie["aretes_orientation_incoherente"] == 1


def test_adjacence_ne_fusionne_pas_les_coordonnees_proches():
    """Un écart même minime entre deux sommets reste une frontière, sans tolérance implicite."""
    triangles = np.asarray([
        [[0, 0, 1], [1, 0, 1], [0, 1, 1]],
        [[1.0000001, 0, 1], [0, 0, 1], [1, -1, 1]],
    ], dtype=float)

    adjacency, topologie = sc._adjacence_exacte(triangles)

    assert len(adjacency) == 0
    assert topologie["aretes_ouvertes"] == 6


def test_visibilite_verticale_echantillonnee_sur_temoin_ferme():
    """Le mode opt-in mesure une visée échantillonnée et n'élargit pas le résultat aux surfaces."""
    maillage = trimesh.creation.box(extents=[2, 2, 2])
    maillage.apply_translation([0, 0, 3])

    rapport = sc.cartographier(maillage, source="cube.stl", unite="mm", source_sha256="a" * 64, plateau_z_mm=0.0)

    groupe = rapport["groupes"][0]
    visibilite = groupe["visibilite_verticale_plateau"]["visées_échantillonnées"]
    assert visibilite["statut"] == "DÉGAGÉE"
    assert visibilite["methode"] == "barycentres de faces surplombantes, visées verticales vers −Z"
    assert visibilite["rayons_total"] == 2
    assert visibilite["echantillons"][0]["point_xyz_mm"][2] == 2.0
    assert all(item["statut"] == "DÉGAGÉE" for item in visibilite["echantillons"])
    assert "face entière" in visibilite["portee"]
    assert groupe["acces_plateau"]["statut"] == "NON VÉRIFIÉ"


def test_visibilite_verticale_signale_un_obstacle_incline():
    """Un triangle obstacle sous le point central interrompt seulement la visée échantillonnée."""
    cible = trimesh.Trimesh(
        vertices=[[0, 0, 2], [2, 0, 2], [0, 2, 2]], faces=[[0, 2, 1]], process=False
    )
    obstacle = trimesh.Trimesh(
        vertices=[[0, 0, 0.5], [2, 0, 1.5], [0, 2, 1.5]], faces=[[0, 1, 2]], process=False
    )
    maillage = trimesh.util.concatenate([cible, obstacle])

    rapport = sc.cartographier(maillage, source="obstacle.stl", unite="mm", source_sha256="a" * 64, plateau_z_mm=0.0)

    visibilite = rapport["groupes"][0]["visibilite_verticale_plateau"]["visées_échantillonnées"]
    assert visibilite["statut"] == "OBSTRUÉE"
    assert visibilite["echantillons"][0]["statut"] == "OBSTRUÉE"
    assert visibilite["echantillons"][0]["obstacle_z_mm"] == pytest.approx(1.166667, abs=1e-6)


def test_visibilite_verticale_surface_inclinee_hors_point_reste_locale():
    """La méthode ponctuelle ne prétend rien sur une partie inclinée non échantillonnée."""
    cible = trimesh.Trimesh(
        vertices=[[0, 0, 2], [2, 0, 2], [0, 2, 2]], faces=[[0, 2, 1]], process=False
    )
    # L'obstacle projeté ne couvre qu'une portion de la face cible, hors de son barycentre.
    obstacle = trimesh.Trimesh(
        vertices=[[1.2, 0.1, 0.5], [1.8, 0.1, 1.2], [1.2, 0.7, 1.0]],
        faces=[[0, 1, 2]], process=False,
    )
    maillage = trimesh.util.concatenate([cible, obstacle])

    rapport = sc.cartographier(maillage, source="pente.stl", unite="mm", source_sha256="a" * 64, plateau_z_mm=0.0)

    visibilite = rapport["groupes"][0]["visibilite_verticale_plateau"]["visées_échantillonnées"]
    assert visibilite["statut"] == "DÉGAGÉE"
    assert visibilite["rayons_total"] == 1
    assert "barycentre" in visibilite["portee"]


@pytest.mark.parametrize("plateau", [float("nan"), float("inf"), 2.0, 3.0])
def test_visibilite_verticale_plateau_invalide_est_ambigu(plateau):
    """Un plan non fini ou qui touche/traverse le maillage ne produit jamais une visée dégagée."""
    maillage = trimesh.creation.box(extents=[2, 2, 2])
    maillage.apply_translation([0, 0, 3])

    rapport = sc.cartographier(
        maillage, source="cube.stl", unite="mm", source_sha256="a" * 64, plateau_z_mm=plateau
    )

    visibilite = rapport["groupes"][0]["visibilite_verticale_plateau"]["visées_échantillonnées"]
    assert visibilite["statut"] == "AMBIGUË"
    assert visibilite["echantillons"] == []
    assert visibilite["raison"] == "plan de plateau invalide ou en contact avec le maillage"


def test_visibilite_verticale_contact_au_plan_est_ambigu():
    """Un obstacle qui rejoint le plan explicite reste ambigu, car le trajet vers le plateau touche sa borne."""
    cible = trimesh.Trimesh(
        vertices=[[0, 0, 2], [2, 0, 2], [0, 2, 2]], faces=[[0, 2, 1]], process=False
    )
    contact = trimesh.Trimesh(
        vertices=[[0, 0, -1], [2, 0, -1], [0, 2, -1]], faces=[[0, 1, 2]], process=False
    )
    borne_basse = trimesh.Trimesh(
        vertices=[[-12, 0, -2], [-11, 0, -2], [-12, 1, -2]], faces=[[0, 1, 2]], process=False
    )

    rapport = sc.cartographier(
        trimesh.util.concatenate([cible, contact, borne_basse]),
        source="contact.stl", unite="mm", source_sha256="a" * 64, plateau_z_mm=-1.0,
    )

    visibilite = rapport["groupes"][0]["visibilite_verticale_plateau"]["visées_échantillonnées"]
    assert visibilite["echantillons"][0]["statut"] == "AMBIGUË"
    assert visibilite["echantillons"][0]["raison"] == "contact avec le plan de plateau"


def test_visibilite_verticale_projection_verticale_alignee_est_ambigue():
    """Une face sans aire en projection XY alignée à la visée ne devient pas un obstacle affirmé."""
    cible = trimesh.Trimesh(
        vertices=[[0, 0, 2], [2, 0, 2], [0, 2, 2]], faces=[[0, 2, 1]], process=False
    )
    verticale = trimesh.Trimesh(
        vertices=[[2 / 3, 0, -0.5], [2 / 3, 1, -0.5], [2 / 3, 0, 1.5]],
        faces=[[0, 1, 2]], process=False,
    )

    rapport = sc.cartographier(
        trimesh.util.concatenate([cible, verticale]), source="verticale.stl", unite="mm",
        source_sha256="a" * 64, plateau_z_mm=-1.0,
    )

    echantillon = rapport["groupes"][0]["visibilite_verticale_plateau"]["visées_échantillonnées"]["echantillons"][0]
    assert echantillon["statut"] == "AMBIGUË"
    assert echantillon["raison"] == "projection XY dégénérée sur la visée"


def test_face_sur_le_plateau_n_invalide_pas_un_autre_groupe():
    """Une face au niveau du plateau ne doit pas rendre tous les autres îlots indéterminés."""
    maillage = trimesh.Trimesh(
        vertices=[[0, 0, 0], [2, 0, 0], [0, 2, 0], [5, 0, 2], [7, 0, 2], [5, 2, 2]],
        faces=[[0, 2, 1], [3, 5, 4]], process=False,
    )
    rapport = sc.cartographier(maillage, source="deux.stl", unite="mm", source_sha256="a" * 64, plateau_z_mm=0)
    mesures = [g["visibilite_verticale_plateau"]["visées_échantillonnées"] for g in rapport["groupes"]]
    assert [m["statut"] for m in mesures] == ["AMBIGUË", "DÉGAGÉE"]
    assert [m["rayons_total"] for m in mesures] == [0, 1]


def test_frontiere_bien_sous_le_plan_est_hors_visée():
    """Une arête projetée sur XY ne compte que si son altitude touche le segment déclaré."""
    triangles = np.array([[[0, 0, 2], [2, 0, 2], [0, 2, 2]], [[0, 0, -2], [2, 0, -2], [0, 2, -2]]], dtype=float)
    assert sc._evaluer_rayon_vertical(triangles, 0, (1, 1, 2), 0)["statut"] == "DÉGAGÉE"


def test_projection_verticale_bien_hors_intervalle_z_ne_bloque_pas():
    """La boîte Z d'un triangle dégénéré ne suffit pas si la fibre au rayon reste au-dessus du segment."""
    p = 2 / 3
    triangles = np.asarray([
        [[0, 0, 2], [2, 0, 2], [0, 2, 2]],
        [[p, p, 100], [p, p, 200], [p + 1, p, 1]],
    ], dtype=float)

    resultat = sc._evaluer_rayon_vertical(triangles, 0, (p, p, 2), 0)

    assert resultat["statut"] == "DÉGAGÉE"


def test_projection_verticale_dont_fibre_z_traverse_le_segment_reste_ambigue():
    """Les bornes Z de la fibre dégénérée peuvent être de part et d'autre du segment mesuré."""
    p = 2 / 3
    triangles = np.asarray([
        [[0, 0, 2], [2, 0, 2], [0, 2, 2]],
        [[p - 1, p, -2], [p + 1, p, -2], [p, p, 4]],
    ], dtype=float)

    resultat = sc._evaluer_rayon_vertical(triangles, 0, (p, p, 2), 0)

    assert resultat["statut"] == "AMBIGUË"
    assert resultat["raison"] == "projection XY dégénérée sur la visée"


def test_face_superposee_au_depart_reste_ambigue():
    """Deux triangles coïncidents au départ ne forment pas une visée qualifiée."""
    triangle = [[0, 0, 2], [2, 0, 2], [0, 2, 2]]
    triangles = np.array([triangle, triangle], dtype=float)
    resultat = sc._evaluer_rayon_vertical(triangles, 0, (0.5, 0.5, 2), 0)
    assert resultat["statut"] == "AMBIGUË"
    assert "départ" in resultat["raison"]


def test_visibilite_verticale_superposition_de_triangles_est_ambigue():
    """Deux obstacles superposés à même Z ne sont pas arbitrés par leur ordre de triangles."""
    cible = trimesh.Trimesh(
        vertices=[[0, 0, 2], [2, 0, 2], [0, 2, 2]], faces=[[0, 2, 1]], process=False
    )
    obstacles = trimesh.Trimesh(
        vertices=[
            [0, 0, 1], [2, 0, 1], [0, 2, 1],
            [0, 0, 1], [2, 0, 1], [0, 2, 1],
        ], faces=[[0, 1, 2], [3, 4, 5]], process=False,
    )

    rapport = sc.cartographier(
        trimesh.util.concatenate([cible, obstacles]), source="doublon.stl", unite="mm",
        source_sha256="a" * 64, plateau_z_mm=0.0,
    )

    echantillon = rapport["groupes"][0]["visibilite_verticale_plateau"]["visées_échantillonnées"]["echantillons"][0]
    assert echantillon["statut"] == "AMBIGUË"
    assert echantillon["raison"] == "superposition de triangles sur la visée"


def test_visibilite_verticale_opt_in_seul_change_le_rapport():
    """Sans plan déclaré, le JSON historique conserve exactement ses clés et ses valeurs."""
    maillage = trimesh.creation.box(extents=[2, 2, 2])
    maillage.apply_translation([0, 0, 3])
    historique = sc.cartographier(maillage, source="cube.stl", unite="mm")

    assert "visibilite_verticale_plateau" not in historique["groupes"][0]
    assert historique["groupes"][0]["acces_plateau"]["statut"] == "NON VÉRIFIÉ"


def test_visibilite_verticale_refuse_un_sha_absent_ou_mal_forme():
    """Le mode opt-in refuse une provenance qui ne peut pas identifier le STL mesuré."""
    maillage = trimesh.creation.box()

    for sha in (None, "not-a-sha"):
        with pytest.raises(ValueError, match="SHA-256 du STL exact"):
            sc.cartographier(maillage, source="cube.stl", unite="mm", source_sha256=sha, plateau_z_mm=-2)


def test_cli_active_visibilite_uniquement_sur_demande_et_lie_le_sha(tmp_path):
    """Le paramètre plateau est opt-in et sa mesure reste liée au STL décodé."""
    source = tmp_path / "cube.stl"
    maillage = trimesh.creation.box(extents=[2, 2, 2])
    maillage.apply_translation([0, 0, 3])
    maillage.export(source)
    attendu = hashlib.sha256(source.read_bytes()).hexdigest()
    sortie = tmp_path / "rapport.json"

    assert sc.main([str(source), "--unite-mm", "--plateau-z-mm", "0", "--sortie", str(sortie)]) == 0

    rapport = json.loads(sortie.read_bytes())
    assert rapport["source_sha256"] == attendu
    assert rapport["groupes"][0]["visibilite_verticale_plateau"]["visées_échantillonnées"]["statut"] == "DÉGAGÉE"
    assert rapport["groupes"][0]["acces_plateau"]["statut"] == "NON VÉRIFIÉ"


def test_cli_apercu_des_visees_garde_le_maillage_source_et_des_marqueurs_hors_export(tmp_path, capsys):
    """Les marqueurs sont liés au JSON et au STL exact, sans ajouter de géométrie au GLB."""
    source = tmp_path / "coupon.stl"
    maillage = trimesh.creation.box(extents=[2, 2, 2])
    maillage.apply_translation([0, 0, 3])
    maillage.export(source)
    source_avant = source.read_bytes()
    destination = tmp_path / "apercu"
    arguments = [str(source), "--unite-mm", "--plateau-z-mm", "0", "--dossier-apercu", str(destination)]

    assert sc.main(arguments, prive=tmp_path) == 0
    resume = json.loads(capsys.readouterr().out)
    rapport = json.loads((destination / "rapport.json").read_bytes())
    page = (destination / "apercu.html").read_text(encoding="utf-8")
    scene = json.loads(re.search(r'<script type="application/json" id="donnees">(.*?)</script>', page).group(1))
    assert scene["source"]["sha256"] == rapport["source_sha256"] == hashlib.sha256(source_avant).hexdigest()
    assert len(scene["pieces"]) == len(scene["modeles"]) == 1
    marqueurs = scene["diagnostics"][0]["anomalies"]
    assert len(marqueurs) == 2
    echantillons = rapport["groupes"][0]["visibilite_verticale_plateau"]["visées_échantillonnées"]["echantillons"]
    assert [m["point_modele"] for m in marqueurs] == [e["point_xyz_mm"] for e in echantillons]
    assert {m["type"] for m in marqueurs} == {"visee_degagee"}
    assert resume["apercu"] == str(destination / "apercu.html")
    assert source.read_bytes() == source_avant
    assert sc.main(arguments, prive=tmp_path) == 2
    assert (destination / "apercu.html").read_text(encoding="utf-8") == page


def test_cli_apercu_exige_un_plan_explicite(tmp_path, capsys):
    """La CLI ne crée ni dossier ni approximation lorsque le plan déclaré manque."""
    source = tmp_path / "coupon.stl"
    trimesh.creation.box().export(source)
    destination = tmp_path / "apercu"
    assert sc.main([str(source), "--unite-mm", "--dossier-apercu", str(destination)], prive=tmp_path) == 2
    assert "--plateau-z-mm" in capsys.readouterr().err
    assert not destination.exists()


def test_visibilite_verticale_borne_et_annonce_les_rayons_omis(monkeypatch):
    """Le budget global est strict et une omission empêche de qualifier la visée agrégée."""
    monkeypatch.setattr(sc, "MAX_VISIBILITE_RAYONS", 1, raising=False)
    sommets = [[0, 0, 2], [1, 0, 2], [1, 1, 2], [0, 1, 2]]
    maillage = trimesh.Trimesh(vertices=sommets, faces=[[0, 2, 1], [0, 3, 2]], process=False)

    visibilite = sc.cartographier(
        maillage, source="deux-faces.stl", unite="mm", source_sha256="a" * 64, plateau_z_mm=0.0
    )["groupes"][0]["visibilite_verticale_plateau"]["visées_échantillonnées"]

    assert visibilite["rayons_total"] == 1
    assert visibilite["rayons_omis"] == 1
    assert visibilite["statut"] == "AMBIGUË"


def test_cli_refuse_une_source_remplacee_pendant_la_lecture(tmp_path, monkeypatch, capsys):
    """La lecture refuse un changement d'identité du chemin observé depuis le descripteur."""
    source = tmp_path / "fixture.stl"
    trimesh.creation.box().export(source)
    remplacement = tmp_path / "remplacement.stl"
    remplacement.write_bytes(source.read_bytes())
    lecture_originale = os.read
    remplace = False

    def lire_et_remplacer(descripteur, taille):
        nonlocal remplace
        octets = lecture_originale(descripteur, taille)
        if octets and not remplace:
            os.replace(remplacement, source)
            remplace = True
        return octets

    monkeypatch.setattr(sc.os, "read", lire_et_remplacer)

    assert sc.main([str(source), "--unite-mm"]) == 2
    assert "a changé pendant sa lecture" in capsys.readouterr().err


def test_cli_refuse_budget_triangles_avant_decodage(tmp_path, monkeypatch, capsys):
    """Le budget de triangles est vérifié depuis l'en-tête binaire avant le décodage du maillage."""
    source = tmp_path / "trop-grand.stl"
    nombre = sc.MAX_FACES + 1
    source.write_bytes(bytes(80) + struct.pack("<I", nombre) + bytes(50 * nombre))
    monkeypatch.setattr(sc.trimesh, "load_mesh", lambda *_args, **_kwargs: pytest.fail("décodage prématuré"))

    assert sc.main([str(source), "--unite-mm"]) == 2
    assert "budget de 50000 triangles" in capsys.readouterr().err


def test_cli_refuse_budget_octets_avant_lecture(tmp_path, monkeypatch, capsys):
    """Le budget de taille est vérifié sur le descripteur avant toute lecture du contenu."""
    source = tmp_path / "trop-lourd.stl"
    with source.open("wb") as fichier:
        fichier.truncate(sc.MAX_SOURCE_OCTETS + 1)
    monkeypatch.setattr(sc.os, "read", lambda *_args: pytest.fail("lecture prématurée"))

    assert sc.main([str(source), "--unite-mm"]) == 2
    assert "budget de 67108864 octets" in capsys.readouterr().err


def test_cli_refuse_source_lien_symbolique(tmp_path, monkeypatch, capsys):
    """Le lecteur refuse un lien avant d'ouvrir les octets ciblés."""
    cible = tmp_path / "cible.stl"
    cible.write_bytes(trimesh.creation.box().export(file_type="stl"))
    source = tmp_path / "lien.stl"
    source.symlink_to(cible)
    monkeypatch.setattr(sc.os, "read", lambda *_args: pytest.fail("lecture de cible liée"))

    assert sc.main([str(source), "--unite-mm"]) == 2
    assert "ne peut pas être un lien symbolique" in capsys.readouterr().err


def test_resume_opt_in_surplombs_garde_le_rapport_et_borne_les_groupes(tmp_path, capsys):
    """Le résumé ne tronque pas le rapport neuf et sélectionne les plus grandes aires."""
    prive = tmp_path / "prive"
    prive.mkdir()
    source = prive / "temoin.stl"
    formes = []
    for taille in range(1, 7):
        boite = trimesh.creation.box(extents=[taille, taille, 1])
        boite.apply_translation([20 * taille, 0, 1])
        formes.append(boite)
    trimesh.util.concatenate(formes).export(source)
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    destination = prive / "resume.json"
    assert sc.main([str(source), "--unite-mm", "--sortie", str(destination), "--resume"], prive=prive) == 0
    resume = json.loads(capsys.readouterr().out)
    rapport = json.loads(destination.read_bytes())
    assert resume["rapport"] == str(destination)
    assert resume["statut"] == rapport["statut"]
    assert resume["total_groupes"] == len(rapport["groupes"]) == 6
    assert resume["groupes_omis"] == 1
    assert [g["aire_mm2"] for g in resume["points_importants"]] == [36, 25, 16, 9, 4]
    assert resume["appuis_et_ponts"] == "NON VÉRIFIÉS"
    assert rapport["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest() == sha


def test_detail_opt_in_surplombs_exige_une_preuve_neuve(tmp_path, capsys):
    """Le mode détaillé fournit le rapport complet ; aucun mode de preuve sans destination."""
    prive = tmp_path / "prive"
    prive.mkdir()
    source = prive / "temoin.stl"
    trimesh.creation.box().export(source)
    assert sc.main([str(source), "--unite-mm", "--resume"], prive=prive) == 2
    assert "exige" in capsys.readouterr().err
    destination = prive / "detail.json"
    assert sc.main([str(source), "--unite-mm", "--sortie", str(destination), "--detail"], prive=prive) == 0
    imprime = json.loads(capsys.readouterr().out)
    assert imprime.pop("rapport") == str(destination)
    assert imprime == json.loads(destination.read_bytes())
