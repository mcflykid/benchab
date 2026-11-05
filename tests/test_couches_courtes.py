"""Régressions géométriques synthétiques ; aucune pièce destinée à imprimer."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import trimesh

from benchab.controles.couches_courtes import analyse


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
