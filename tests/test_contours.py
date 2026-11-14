import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import trimesh
from PIL import Image

from benchab.references_visuelles import contours as outil


class ContoursTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.r = Path(self.temp.name)

    def image(self, mask, mode="L"):
        data = np.asarray(mask, dtype=np.uint8) * 255
        if mode == "RGBA":
            rgba = np.zeros((*data.shape, 4), dtype=np.uint8)
            rgba[:, :, :3] = 255
            rgba[:, :, 3] = data
            data = rgba
        p = self.r / "source.png"
        Image.fromarray(data).save(p)
        return p

    def creer(self, mask, largeur=10, epaisseur=2):
        p = self.image(mask)
        before = p.read_bytes()
        report = outil.creer(p, self.r / "result.stl", self.r / "proof.json", "clair", 127, largeur, epaisseur)
        self.assertEqual(before, p.read_bytes())
        self.assertTrue(report["source_preservee"])
        self.assertEqual(outil.sha256(self.r / "result.stl"), report["sortie_sha256"])
        return report, trimesh.load(self.r / "result.stl")

    def test_rectangle_dimensions_volume_cropping_and_boundaries(self):
        mask = np.zeros((7, 9), dtype=bool)
        mask[2:5, 2:7] = True
        report, mesh = self.creer(mask, largeur=20, epaisseur=3)
        np.testing.assert_allclose(mesh.extents, [20, 12, 3])
        self.assertAlmostEqual(mesh.volume, 720)
        self.assertEqual(report["analyse"]["bbox_source_pixels"], [2, 2, 7, 5])
        self.assertEqual(len(report["frontieres_grille_xy"]), 16)
        self.assertTrue(mesh.is_volume)
        self.assertEqual(json.loads((self.r / "proof.json").read_text())["statut"], "PASS")

    def test_hole_is_through_not_filled(self):
        mask = np.ones((5, 5), dtype=bool)
        mask[1:4, 1:4] = False
        report, mesh = self.creer(mask, largeur=10, epaisseur=2)
        self.assertAlmostEqual(mesh.volume, 128)
        self.assertEqual(mesh.euler_number, 0)
        self.assertTrue(mesh.is_volume)
        self.assertFalse(mesh.contains([[5, 5, 1]])[0])
        self.assertTrue(mesh.contains([[1, 1, 1]])[0])
        self.assertEqual(report["apres_export"]["corps_connectes"], 1)

    def test_concave_l_shape_keeps_notch(self):
        report, mesh = self.creer([[1, 0, 0], [1, 0, 0], [1, 1, 1]], largeur=12, epaisseur=2)
        self.assertAlmostEqual(mesh.volume, 160)
        self.assertTrue(mesh.is_volume)
        self.assertFalse(mesh.contains([[10, 10, 1]])[0])
        self.assertTrue(mesh.contains([[2, 10, 1]])[0])

    def test_multiple_components_are_preserved(self):
        report, mesh = self.creer([[1, 1, 0, 1], [1, 1, 0, 1]], largeur=8, epaisseur=1)
        self.assertEqual(report["apres_export"]["corps_connectes"], 2)
        self.assertEqual(len(report["analyse"]["composants_4_connexes"]), 2)
        self.assertAlmostEqual(mesh.volume, 24)

    def test_alpha_and_dark_modes_are_explicit(self):
        p = self.image([[0, 1], [0, 1]], mode="RGBA")
        mask, _ = outil.extraire(p, "alpha", 127)
        np.testing.assert_array_equal(mask, [[True], [True]])
        p = self.image([[1, 0], [1, 0]])
        mask, _ = outil.extraire(p, "sombre", 127)
        np.testing.assert_array_equal(mask, [[True], [True]])
        with self.assertRaises(ValueError):
            outil.extraire(p, "alpha", 127)

    def test_diagonal_contact_is_rejected(self):
        for mask in ([[1, 0], [0, 1]], [[0, 1], [1, 0]]):
            p = self.image(mask)
            with self.assertRaisesRegex(ValueError, "diagonaux"):
                outil.creer(p, self.r / "bad.stl", self.r / "bad.json", "clair", 127, 10, 2)
        self.assertFalse((self.r / "bad.stl").exists())

    def test_empty_mask_and_limits_rejected(self):
        p = self.image([[0, 0], [0, 0]])
        with self.assertRaisesRegex(ValueError, "vide"):
            outil.extraire(p, "clair", 127)
        p = self.image(np.ones((3, 3)))
        with self.assertRaisesRegex(ValueError, "max_pixels"):
            outil.extraire(p, "clair", 127, 8)
        with self.assertRaisesRegex(ValueError, "max_faces"):
            outil.creer(p, self.r / "bad.stl", self.r / "bad.json", "clair", 127, 10, 2, max_faces=1)
        self.assertFalse((self.r / "bad.stl").exists())

    def test_invalid_dimensions_rejected_without_outputs(self):
        p = self.image([[1]])
        for w, e in [(0, 2), (-1, 2), (float("nan"), 2), (10, float("inf")), (True, 1)]:
            with self.assertRaises(ValueError):
                outil.creer(p, self.r / "bad.stl", self.r / "bad.json", "clair", 127, w, e)
        self.assertFalse((self.r / "bad.stl").exists())

    def test_refuses_all_existing_or_alias_paths(self):
        p = self.image([[1]])
        out, report = self.r / "existing.stl", self.r / "existing.json"
        out.write_bytes(b"preserver")
        report.write_text("preserver")
        cases = [
            (out, self.r / "new.json"),
            (self.r / "new.stl", report),
            (p, self.r / "new.json"),
            (self.r / "same.stl", self.r / "same.stl"),
        ]
        for o, r in cases:
            with self.assertRaises(ValueError):
                outil.creer(p, o, r, "clair", 127, 10, 2)
        self.assertEqual(out.read_bytes(), b"preserver")
        self.assertEqual(report.read_text(), "preserver")

    def test_deterministic_geometry(self):
        mask = np.array([[1, 1, 1], [1, 0, 0]], dtype=bool)
        a, _, _ = outil.extruder(mask, 12, 2)
        b, _, _ = outil.extruder(mask, 12, 2)
        self.assertEqual(a.export(file_type="stl"), b.export(file_type="stl"))


if __name__ == "__main__":
    unittest.main()
