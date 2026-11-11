"""Contrats de la cartographie numérique avant tranchage, sans slicer ni machine."""

import json

import numpy as np
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


def test_cli_ecrit_json_neuf_sans_toucher_au_maillage(tmp_path):
    """Le parcours CLI écrit un rapport privé neuf, sans modifier l'entrée STL."""
    maillage = trimesh.creation.box(extents=[2, 2, 2])
    source = tmp_path / "fixture.stl"
    maillage.export(source)
    empreinte = source.read_bytes()
    sortie = tmp_path / "rapport.json"

    assert sc.main([str(source), "--unite-mm", "--sortie", str(sortie)]) == 0
    rapport = json.loads(sortie.read_text())

    assert rapport["statut"] == "RENSEIGNÉE"
    assert rapport["unite"] == "mm"
    assert source.read_bytes() == empreinte
