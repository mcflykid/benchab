"""Défaut synthétique connu, provenance, absence, limites et entrée manquante."""

import tempfile
import unittest
from pathlib import Path

import numpy as np
import trimesh

from benchab.visualisation.anomalies_geometriques import charger_diagnostic, diagnostiquer


class Anomalies(unittest.TestCase):
    def setUp(self):
        self.mesh = trimesh.creation.box((4, 4, 2))
        self.mesh.apply_translation((0, 0, 3))

    def test_centre_reel_et_sources(self):
        d = diagnostiquer(self.mesh, "Piece", "source.stl", "a" * 64)
        self.assertEqual(d["faces_detectees"], 2)
        for a in d["anomalies"]:
            self.assertEqual(a["point_modele"][2], 2)
            self.assertEqual(a["repere"], "modele")
            self.assertGreater(a["valeur"], a["seuil"])
            self.assertEqual(a["sha256"], "a" * 64)
            self.assertEqual(a["resultat"], "WARNING")
            np.testing.assert_allclose(a["point_modele"], self.mesh.triangles[a["triangle_source"]].mean(axis=0))

    def test_contre_exemple_et_selection_bornee(self):
        m = self.mesh.copy().apply_translation((0, 0, -2))
        self.assertEqual(diagnostiquer(m, "P", "s", "b" * 64)["faces_detectees"], 0)
        d = diagnostiquer(self.mesh, "P", "s", "b" * 64, maximum=1)
        self.assertTrue(d["selection_bornee"])
        self.assertEqual(len(d["anomalies"]), 1)

    def test_identite_rechargee_et_source_absente(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "source.stl"
            self.mesh.export(p)
            m, d = charger_diagnostic(p, "P")
            import hashlib

            self.assertEqual(d["sha256"], hashlib.sha256(p.read_bytes()).hexdigest())
            self.assertEqual(d["faces_detectees"], 2)
            with self.assertRaises(FileNotFoundError):
                charger_diagnostic(p.with_name("absent.stl"), "P")

    def test_provenance_et_non_fini_refuses(self):
        with self.assertRaises(ValueError):
            diagnostiquer(self.mesh, "", "s", "b" * 64)
        with self.assertRaises(ValueError):
            diagnostiquer(trimesh.Trimesh(vertices=[], faces=[]), "P", "s", "b" * 64)
        m = self.mesh.copy()
        m.vertices[0, 0] = float("nan")
        with self.assertRaises(ValueError):
            diagnostiquer(m, "P", "s", "b" * 64)


if __name__ == "__main__":
    unittest.main()
