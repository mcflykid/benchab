"""Régressions géométriques synthétiques ; aucune pièce destinée à imprimer."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import trimesh
from shapely.geometry import box as polygon_box

from benchab.controles.appuis_gcode import mesurer_appui
from benchab.controles.couches_courtes import (
    _mesurer_recouvrement_xy,
    _relations_projection_xy,
    analyse,
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

        points = np.array([[15.0, y, 0.2] for y in (-0.3, 0.0, 0.3)])
        resultat_gcode = mesurer_appui(
            points, points, recouvrement_min_mm=0.05, pas_mm=0.15
        )
        self.assertEqual(resultat_gcode["points"], 3)
        self.assertEqual(resultat_gcode["points_avec_appui_pct"], 100.0)
        self.assertAlmostEqual(resultat_gcode["recouvrement_min_echantillonne_mm"], 0.4)
        self.assertEqual(resultat_gcode["longueur_max_parcours_sans_appui_mm"], 0.0)

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
