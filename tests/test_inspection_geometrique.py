"""Défauts connus et contre-exemples, uniquement des maillages temporaires."""

import hashlib
import tempfile
import unittest
from pathlib import Path

import numpy as np
import trimesh

from benchab.visualisation.anomalies_geometriques import charger_diagnostic, diagnostiquer
from benchab.visualisation.inspection_geometrique import comparer, enrichir


class Inspection(unittest.TestCase):
    def diagnostic(self, mesh, **kw):
        return enrichir(mesh, diagnostiquer(mesh, "Piece", "source.stl", "a" * 64), **kw)

    def test_stl_ferme_dedouble_et_mesures_source(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "pièce été.stl"
            trimesh.creation.box((10, 20, 30)).export(p)
            avant = p.read_bytes()
            m, d = charger_diagnostic(p, "Piece")
            self.assertEqual(p.read_bytes(), avant)
            self.assertEqual(d["sha256"], hashlib.sha256(avant).hexdigest())
            self.assertEqual(d["mesures"]["bords_ouverts"], 0)
            self.assertAlmostEqual(d["mesures"]["volume_maillage_mm3"], 6000)
            np.testing.assert_allclose(d["mesures"]["dimensions_source_mm"], (10, 20, 30))
            self.assertIn("parois fines", d["non_verifie"])

    def test_face_manquante_localisee_et_volume_non_verifie(self):
        m = trimesh.creation.box((10, 10, 10))
        m.update_faces(np.arange(len(m.faces)) != 0)
        d = self.diagnostic(m)
        self.assertEqual(d["mesures"]["bords_ouverts"], 3)
        a = [a for a in d["anomalies"] if a["type"] == "bord_ouvert"]
        self.assertEqual(len(a), 3)
        self.assertIsNone(d["mesures"]["volume_maillage_mm3"])
        for x in a:
            self.assertEqual(x["repere"], "modele")
            self.assertTrue(np.isfinite(x["point_modele"]).all())

    def test_arete_trois_triangles_et_triangle_sans_aire(self):
        m = trimesh.Trimesh(
            vertices=[[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1]],
            faces=[[0, 1, 2], [1, 0, 3], [0, 1, 4], [0, 0, 1]],
            process=False,
        )
        d = self.diagnostic(m)
        self.assertTrue(any((a["type"] == "arete_multiple" for a in d["anomalies"])))
        self.assertEqual(d["mesures"]["triangles_degeneres"], 1)
        self.assertTrue(any((a["type"] == "triangle_degenere" for a in d["anomalies"])))

    def test_petite_base_et_contre_exemple(self):
        d = self.diagnostic(trimesh.creation.box((1, 1, 10)))
        a = next((a for a in d["anomalies"] if a["type"] == "petite_base_modele"))
        self.assertAlmostEqual(a["valeur"], 1)
        self.assertIn("pas une pose plateau", a["limite"])
        d = self.diagnostic(trimesh.creation.box((10, 10, 10)))
        self.assertFalse(any((a["type"] == "petite_base_modele" for a in d["anomalies"])))

    def test_volume_negatif_non_corrige(self):
        m = trimesh.creation.box((10, 10, 10))
        m.invert()
        faces = m.faces.copy()
        d = self.diagnostic(m)
        self.assertIsNone(d["mesures"]["volume_maillage_mm3"])
        self.assertTrue(any((a["type"] == "orientation_inversee" for a in d["anomalies"])))
        np.testing.assert_array_equal(m.faces, faces)

    def test_limite_taille_et_comparaison_hors_pose(self):
        m = trimesh.creation.box((10, 20, 30))
        a = self.diagnostic(m)
        b = self.diagnostic(trimesh.creation.box((10, 20, 31)))
        b["sha256"] = "b" * 64
        c = comparer(a, b)
        np.testing.assert_allclose(c["delta_dimensions_mm"], (0, 0, 1))
        self.assertAlmostEqual(c["delta_volume_maillage_mm3"], 200)
        d = self.diagnostic(m, faces_max=1)
        self.assertIsNone(d["mesures"]["volume_maillage_mm3"])
        self.assertEqual(d["mesures"]["topologie"], "NON_VERIFIE_LIMITE_TAILLE")
        with self.assertRaises(ValueError):
            comparer({}, d)


if __name__ == "__main__":
    unittest.main()
