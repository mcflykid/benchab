"""Régressions géométriques synthétiques ; aucune pièce destinée à imprimer."""

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import trimesh
from shapely.geometry import box as polygon_box

from benchab.controles.appuis_gcode import analyser_lignes
from benchab.controles.couches_courtes import (
    _mesurer_recouvrement_xy,
    _position_enveloppes_xy,
    _relations_projection_xy,
    analyse,
    main,
)


def box(size, centre):
    mesh = trimesh.creation.box(size)
    mesh.apply_translation(centre)
    return mesh


class CouchesCourtes(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "synthetique.stl"

    def tearDown(self):
        self.temp.cleanup()

    def check_mesh(self, meshes, **kwargs):
        trimesh.util.concatenate(meshes).export(self.path)
        return analyse(self.path, **kwargs)

    def test_petit_ilot_detecte_malgre_grande_aire_totale(self):
        result = self.check_mesh([box((30, 30, 10), (0, 0, 5)), box((2, 2, 4), (25, 0, 8))])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["centre_xy_mm"], (25.0, 0.0))
        self.assertGreater(result[0]["aire_totale_max_mm2"], 240.0)
        self.assertAlmostEqual(result[0]["hauteur_mm"], 4.0)

    def test_qualification_geometrique_releve_un_candidat_pose_sans_affirmer_contact_plateau(self):
        result = self.check_mesh([box((2, 2, 3), (0, 0, 1.5))])
        qualification = result[0]["qualification_sections"]
        self.assertEqual(
            qualification["premiere_relation_appui_xy"], "premier_echantillon_du_maillage"
        )
        self.assertTrue(qualification["candidat_pose_au_z_min_du_maillage"])
        self.assertEqual(qualification["statut"], "NON VÉRIFIÉ")
        self.assertFalse(qualification["contact_plateau_prouve"])
        self.assertEqual(qualification["fraction_recouvrement_xy_min"], 1.0)

    def test_qualification_releve_une_absence_de_recouvrement_sous_un_ilot_flottant(self):
        result = self.check_mesh(
            [box((10, 10, 2), (0, 0, 1)), box((2, 2, 2), (15, 0, 5))]
        )
        flottant = next(r for r in result if r["centre_xy_mm"] == (15.0, 0.0))
        self.assertEqual(
            flottant["qualification_sections"]["premiere_relation_appui_xy"],
            "sans_recouvrement_xy_echantillonne",
        )
        self.assertEqual(
            flottant["qualification_sections"]["fraction_recouvrement_xy_min"], 0.0
        )

    def test_comparaison_synthetique_sections_et_appui_gcode_supporte(self):
        """Un support G-code peut appuyer un îlot sans recouvrement STL précédent."""
        result = self.check_mesh(
            [box((10, 10, 2), (0, 0, 1)), box((2, 2, 2), (15, 0, 5))]
        )
        ilot = next(r for r in result if r["centre_xy_mm"] == (15.0, 0.0))
        qualification = ilot["qualification_sections"]
        self.assertEqual(
            qualification["premiere_relation_appui_xy"],
            "sans_recouvrement_xy_echantillonne",
        )
        self.assertEqual(qualification["fraction_recouvrement_xy_min"], 0.0)
        self.assertEqual(qualification["statut"], "NON VÉRIFIÉ")

        def gcode(support):
            """Construit un témoin au même repère XY/Z, sans dépendre d'un trancheur."""
            x0 = 15 if support else 100
            x1 = x0 + 1
            type_premiere_couche = "Support" if support else "Outer wall"
            return [
                "G90", "M83", "EXCLUDE_OBJECT_START NAME=ilot",
                ";LAYER_CHANGE", ";Z:4.0", ";WIDTH:0.4", f";TYPE:{type_premiere_couche}",
                f"G0 X{x0} Y0 Z4.0 F600", f"G1 X{x1} Y0 E1 F600",
                ";LAYER_CHANGE", ";Z:4.2", ";WIDTH:0.4", ";TYPE:Outer wall",
                "G0 X15 Y0 Z4.2 F600", "G1 X16 Y0 E1 F600",
                "EXCLUDE_OBJECT_END NAME=ilot",
            ]

        sans_support = analyser_lignes(gcode(False), mode="local", pas_mm=0.15)
        avec_support = analyser_lignes(gcode(True), mode="local", pas_mm=0.15)
        sans_support_cible = next(r for r in sans_support["par_couche"] if r["z_mm"] == 4.2)
        avec_support_cible = next(r for r in avec_support["par_couche"] if r["z_mm"] == 4.2)
        comparaison = {
            "sections_stl": {
                "relation": qualification["premiere_relation_appui_xy"],
                "fraction_min": qualification["fraction_recouvrement_xy_min"],
                "statut": qualification["statut"],
            },
            "trajectoires_sans_support": {
                "points_avec_appui_pct": sans_support_cible["points_avec_appui_pct"],
                "longueur_max_sans_appui_mm": sans_support_cible["portee_libre_max_mm"],
            },
            "trajectoires_avec_support": {
                "points_avec_appui_pct": avec_support_cible["points_avec_appui_pct"],
                "recouvrement_min_mm": avec_support_cible["recouvrement_min_echantillonne_mm"],
                "longueur_max_sans_appui_mm": avec_support_cible["portee_libre_max_mm"],
            },
        }
        self.assertEqual(comparaison["sections_stl"], {
            "relation": "sans_recouvrement_xy_echantillonne",
            "fraction_min": 0.0,
            "statut": "NON VÉRIFIÉ",
        })
        self.assertEqual(comparaison["trajectoires_sans_support"]["points_avec_appui_pct"], 0.0)
        self.assertEqual(comparaison["trajectoires_avec_support"]["points_avec_appui_pct"], 100.0)
        self.assertAlmostEqual(comparaison["trajectoires_avec_support"]["recouvrement_min_mm"], 0.4)
        self.assertEqual(comparaison["trajectoires_avec_support"]["longueur_max_sans_appui_mm"], 0.0)

    def test_position_d_un_ilot_dans_un_trou_exige_inclusion_stricte(self):
        """Une section entièrement dans un anneau reçoit une position locale, non physique."""
        anneau = polygon_box(0, 0, 10, 10).difference(polygon_box(3, 3, 7, 7))
        ilot = polygon_box(4, 4, 6, 6)

        resultat = _position_enveloppes_xy(ilot, [anneau])

        self.assertEqual(resultat["classe"], "interieur_d_un_trou")
        self.assertEqual(resultat["statut"], "RENSEIGNÉE")

    def test_position_exterieure_ne_confond_pas_contact_frontiere_et_exterieur(self):
        """Une frontière touchée reste ambiguë ; une enveloppe disjointe est extérieure."""
        enveloppe = polygon_box(0, 0, 10, 10)

        exterieur = _position_enveloppes_xy(polygon_box(12, 0, 14, 2), [enveloppe])
        frontiere = _position_enveloppes_xy(polygon_box(10, 2, 12, 4), [enveloppe])

        self.assertEqual(exterieur["classe"], "exterieur_aux_enveloppes")
        self.assertEqual(exterieur["statut"], "RENSEIGNÉE")
        self.assertEqual(frontiere["statut"], "NON VÉRIFIÉ")
        self.assertEqual(frontiere["cause"], "frontiere_touchee")

    def test_position_ambigue_si_ilot_chevauche_une_enveloppe_ou_si_invalidite(self):
        """Un recouvrement matériel ou une géométrie invalide ne devient pas une classe."""
        enveloppe = polygon_box(0, 0, 10, 10)
        recouvrement = _position_enveloppes_xy(polygon_box(1, 1, 2, 2), [enveloppe])
        invalide = _position_enveloppes_xy(polygon_box(11, 1, 12, 2), [None])

        self.assertEqual(recouvrement["statut"], "NON VÉRIFIÉ")
        self.assertEqual(recouvrement["cause"], "chevauchement_enveloppe")
        self.assertEqual(invalide["statut"], "NON VÉRIFIÉ")
        self.assertEqual(invalide["cause"], "geometrie_invalide")

    def test_position_enveloppe_optionnelle_et_changement_sur_hauteur_reste_ambigu(self):
        """La position XY est opt-in et son changement au fil des coupes n'est pas aplati."""
        anneau = trimesh.creation.annulus(r_min=4, r_max=5, height=2)
        anneau.apply_translation((0, 0, 1))
        ilot = box((1, 1, 4), (0, 0, 2))
        historique = self.check_mesh([anneau, ilot], hmin=1)
        ilot_historique = next(r for r in historique if r["hauteur_mm"] == 4.0)
        assert "position_enveloppe_xy" not in ilot_historique["qualification_sections"]

        qualifie = analyse(self.path, hmin=1, qualifier_positions=True)
        ilot_qualifie = next(r for r in qualifie if r["hauteur_mm"] == 4.0)
        position = ilot_qualifie["qualification_sections"]["position_enveloppe_xy"]

        self.assertEqual(position["statut"], "NON VÉRIFIÉ")
        self.assertEqual(position["classe"], "ambigue")
        self.assertEqual(position["cause"], "position_change_au_fil_des_couches")
        self.assertEqual(
            {item["classe"] for item in position["par_couche"]},
            {"interieur_d_un_trou", "exterieur_aux_enveloppes"},
        )
        sortie = io.StringIO()
        with contextlib.redirect_stdout(sortie):
            code = main([str(self.path), "--qualifier-position-enveloppes"])
        rapport_cli = json.loads(sortie.getvalue())
        self.assertEqual(code, 2)
        self.assertIn(
            "position_enveloppe_xy",
            rapport_cli["ilots_isoles"][-1]["qualification_sections"],
        )

    def test_qualification_quantifie_un_recouvrement_partiel_de_coupes(self):
        result = self.check_mesh(
            [box((4, 4, 2), (0, 0, 1)), box((4, 4, 2), (2, 0, 3))]
        )
        self.assertEqual(len(result), 1)
        qualification = result[0]["qualification_sections"]
        self.assertIn("recouvrement_partiel_xy_echantillonne", qualification["relations_appui_xy_observees"])
        self.assertEqual(qualification["fraction_recouvrement_xy_min"], 0.5)

    def test_qualification_detecte_une_branche_dans_la_graphe_des_coupes(self):
        result = self.check_mesh(
            [
                box((6, 4, 3), (0, 0, 1.5)),
                box((2, 2, 3), (-2, 0, 4.5)),
                box((2, 2, 3), (2, 0, 4.5)),
            ]
        )
        branches = [r for r in result if r["qualification_sections"]["branche_projection_xy"]]
        self.assertTrue(branches)
        self.assertTrue(branches[0]["qualification_sections"]["association_pistes_ambigue"])
        self.assertIn(
            "association_un_a_un_non_prouvee_branche_ou_fusion",
            branches[0]["qualification_sections"]["ambiguites"],
        )

    def test_relations_projection_indexees_gardent_branche_et_fusion(self):
        precedente = [polygon_box(0, 0, 6, 4)]
        courantes = [polygon_box(0, 0, 2, 2), polygon_box(4, 0, 6, 2)]
        parents, branches = _relations_projection_xy(precedente, courantes)
        self.assertEqual(parents, [[0], [0]])
        self.assertEqual(branches, [True, True])

        precedentes = [polygon_box(0, 0, 2, 2), polygon_box(4, 0, 6, 2)]
        fusion = [polygon_box(0, 0, 6, 2)]
        parents, branches = _relations_projection_xy(precedentes, fusion)
        self.assertEqual(parents, [[0, 1]])
        self.assertEqual(branches, [False])

    def test_relations_projection_indexees_excluent_le_contact_sans_aire(self):
        parents, branches = _relations_projection_xy(
            [polygon_box(0, 0, 1, 1)], [polygon_box(1, 0, 2, 1)]
        )
        self.assertEqual(parents, [[]])
        self.assertEqual(branches, [False])

    def test_qualification_signale_un_vide_interieur_de_section(self):
        mesh = trimesh.creation.annulus(r_min=1, r_max=3, height=3)
        mesh.apply_translation((0, 0, 1.5))
        result = self.check_mesh([mesh])
        self.assertTrue(
            any(r["qualification_sections"]["cavite_section_detectee"] for r in result)
        )

    def test_qualification_laisse_les_intersections_sous_precision_indeterminees(self):
        petite = polygon_box(0, 0, 2e-5, 2e-5)
        relation, aire, fraction = _mesurer_recouvrement_xy(
            petite, [petite], couche=1
        )
        self.assertEqual(relation, "recouvrement_non_qualifie_precision_numerique")
        self.assertLessEqual(aire, 1e-9)
        self.assertIsNone(fraction)

    def test_qualification_garde_la_terminaison_superieure_echantillonnee(self):
        result = self.check_mesh([box((2, 2, 2), (0, 0, 1))])
        self.assertTrue(result[0]["qualification_sections"]["sommet_terminal_echantillonne"])

    def test_petit_sommet_final_sous_hauteur_minimale(self):
        result = self.check_mesh([box((30, 30, 8), (0, 0, 4)), box((2, 2, 1), (0, 0, 8.5))])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["motifs"], ["petit_sommet_terminal"])
        self.assertAlmostEqual(result[0]["hauteur_mm"], 1.0)

    def test_sommet_local_avant_fin_du_modele(self):
        result = self.check_mesh([box((30, 30, 10), (0, 0, 5)), box((2, 2, 1), (25, 0, 8.5))])
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["sommet_terminal"])
        self.assertLess(result[0]["a_z"], 9.1)
        self.assertAlmostEqual(result[0]["hauteur_mm"], 1.0)

    def test_intervalles_verticaux_disjoints_pas_fusionnes(self):
        result = self.check_mesh([box((2, 2, 1), (0, 0, 0.5)), box((2, 2, 1), (0, 0, 3.5))])
        self.assertEqual(len(result), 2)
        self.assertEqual([r["hauteur_mm"] for r in result], [1.0, 1.0])
        self.assertLess(result[0]["a_z"], result[1]["de_z"])

    def test_centres_stl_restent_stables_par_translation(self):
        mesh = box((2, 2, 3), (20, 30, 1.5))
        before = self.check_mesh([mesh])
        mesh.apply_translation((123, -45, 7))
        after = self.check_mesh([mesh])
        self.assertEqual(before[0]["centre_xy_mm"], (20.0, 30.0))
        self.assertEqual(after[0]["centre_xy_mm"], (143.0, -15.0))
        self.assertAlmostEqual(after[0]["de_z"] - before[0]["de_z"], 7.0)
        self.assertEqual(after[0]["hauteur_mm"], before[0]["hauteur_mm"])

    def test_boite_massive_sans_alerte(self):
        self.assertEqual(self.check_mesh([box((30, 30, 10), (0, 0, 5))]), [])

    def test_derniere_couche_partielle_est_analysee(self):
        result = self.check_mesh([box((2, 2, 0.05), (0, 0, 0.025))])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["couches"], 1)
        self.assertAlmostEqual(result[0]["hauteur_mm"], 0.05)

    def test_petits_ilots_proches_ne_sont_pas_fusionnes(self):
        result = self.check_mesh([box((0.4, 0.4, 3), (-0.7, 0, 1.5)), box((0.4, 0.4, 3), (0.7, 0, 1.5))])
        self.assertEqual(len(result), 2)
        self.assertEqual([r["centre_xy_mm"] for r in result], [(-0.7, 0.0), (0.7, 0.0)])

    def test_elargissement_non_terminal_sous_hmin_non_signale(self):
        result = self.check_mesh([box((2, 2, 1), (0, 0, 0.5)), box((30, 30, 2), (0, 0, 2))])
        self.assertEqual(result, [])

    def test_seuils_invalides_refuses(self):
        self.check_mesh([box((2, 2, 3), (0, 0, 1.5))])
        for kwargs in ({"lh": 0}, {"amax": -1}, {"hmin": float("nan")}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                analyse(self.path, **kwargs)

    def test_cli_codes_et_schema_conserves(self):
        for size, expected in [((30, 30, 3), 0), ((2, 2, 3), 2)]:
            self.check_mesh([box(size, (0, 0, 1.5))])
            result = subprocess.run(
                [sys.executable, "-m", "benchab.controles.couches_courtes", str(self.path), ".2", "40", "2"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, expected, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["fichier"], str(self.path))
            self.assertIsInstance(payload["ilots_isoles"], list)


if __name__ == "__main__":
    unittest.main()
