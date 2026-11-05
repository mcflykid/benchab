"""Vérifie l'aller-retour STL sans tranchage."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import trimesh

from benchab.fabrication.exportable import exportable


class ExportTests(unittest.TestCase):
    def test_maillage_lecture_seule_accepte_par_mesh64(self):
        maillage = trimesh.creation.box([10, 12, 14])
        maillage.vertices.flags.writeable = False
        maillage.faces.flags.writeable = False

        resultat = exportable(maillage)

        self.assertFalse(maillage.vertices.flags.writeable)
        self.assertFalse(maillage.faces.flags.writeable)
        self.assertTrue(resultat.is_volume)
        np.testing.assert_allclose(resultat.extents, [10, 12, 14], atol=1e-5)
        self.assertAlmostEqual(resultat.volume, 1680.0, delta=1.68)

    def test_maillage_non_contigu_transmis_en_buffers_c(self):
        import manifold3d

        constructeur = manifold3d.Mesh64

        def verifier_buffers(*, vert_properties, tri_verts):
            self.assertTrue(vert_properties.flags.c_contiguous)
            self.assertTrue(tri_verts.flags.c_contiguous)
            self.assertTrue(vert_properties.flags.writeable)
            self.assertTrue(tri_verts.flags.writeable)
            self.assertEqual(vert_properties.dtype, np.dtype(np.float64))
            self.assertEqual(tri_verts.dtype, np.dtype(np.uint64))
            return constructeur(vert_properties=vert_properties, tri_verts=tri_verts)

        maillage = trimesh.creation.box([10, 12, 14])
        for cle in ("vertices", "faces"):
            original = maillage._data[cle]
            maillage._data[cle] = np.asfortranarray(original).view(type(original))
            self.assertFalse(maillage._data[cle].flags.c_contiguous)
        sommets_avant = maillage.vertices.copy()
        faces_avant = maillage.faces.copy()
        with patch.object(manifold3d, "Mesh64", side_effect=verifier_buffers):
            resultat = exportable(maillage)

        np.testing.assert_array_equal(maillage.vertices, sommets_avant)
        np.testing.assert_array_equal(maillage.faces, faces_avant)
        with tempfile.TemporaryDirectory() as temporaire:
            chemin = Path(temporaire) / "non-contigu.stl"
            resultat.export(chemin)
            relu = trimesh.load_mesh(chemin)
        self.assertTrue(relu.is_watertight)
        self.assertTrue(relu.is_volume)
        np.testing.assert_allclose(relu.extents, [10, 12, 14], atol=1e-5)
        self.assertAlmostEqual(relu.volume, maillage.volume, delta=abs(maillage.volume) * 1e-3)

    def test_booleen_relu_ferme(self):
        boite = trimesh.creation.box([20, 20, 20])
        sphere = trimesh.creation.icosphere(subdivisions=4, radius=11.0)
        sphere.apply_translation([3e-6, 0, 10])
        maillage = trimesh.boolean.difference([boite, sphere], engine="manifold")
        resultat = exportable(maillage)

        with tempfile.TemporaryDirectory() as temporaire:
            chemin = Path(temporaire) / "resultat.stl"
            resultat.export(chemin)
            relu = trimesh.load_mesh(chemin)
        self.assertTrue(relu.is_watertight)
        self.assertAlmostEqual(relu.volume, maillage.volume, delta=abs(maillage.volume) * 1e-3)

    def test_refuse_un_maillage_ouvert(self):
        maillage = trimesh.creation.box([10, 10, 10])
        maillage.update_faces(list(range(len(maillage.faces) - 1)))

        with self.assertRaises(ValueError):
            exportable(maillage)


if __name__ == "__main__":
    unittest.main()
