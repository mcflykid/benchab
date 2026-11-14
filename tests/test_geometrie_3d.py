"""Témoins synthétiques indépendants, jamais le modèle privé modele-prive-fixture."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import trimesh

from benchab.imports3d import geometrie as G
from benchab.imports3d.geometrie import comparer, empreinte, main, preparation, recaler


class GeometrieTests(unittest.TestCase):
    def setUp(self):
        self.mesh = trimesh.creation.box(extents=[10, 20, 30])
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name).resolve()

    def compare(self, a, b, **kw):
        return comparer(a, b, nombre=200, **kw)

    def test_axes_y_hauteur_apres_rotation(self):
        mesh, mat = preparation(self.mesh, "y", 40, True)
        np.testing.assert_allclose(mesh.extents, [20, 60, 40])
        np.testing.assert_allclose(mesh.bounds, [[-10, -30, 0], [10, 30, 40]])
        np.testing.assert_allclose(trimesh.transform_points(self.mesh.vertices, mat), mesh.vertices)
        np.testing.assert_allclose(self.mesh.extents, [10, 20, 30])
        self.assertGreater(np.linalg.det(mat[:3, :3]), 0)
        self.assertAlmostEqual(mesh.volume / self.mesh.volume, 8)

    def test_z_et_pas_de_transformation_implicite(self):
        self.mesh.apply_translation([1, 8, -5])
        m, mat = preparation(self.mesh, "z")
        np.testing.assert_array_equal(m.vertices, self.mesh.vertices)
        np.testing.assert_array_equal(mat, np.eye(4))

    def test_parametres_invalides(self):
        for h in [float("nan"), float("inf"), 0, -3]:
            with self.assertRaises(ValueError):
                preparation(self.mesh, "y", h)
        with self.assertRaises(ValueError):
            preparation(self.mesh, "x")
        for n in [0, 50001, 1.5, True]:
            with self.assertRaises(ValueError):
                comparer(self.mesh, self.mesh, nombre=n)
        with self.assertRaises(ValueError):
            self.compare(self.mesh, self.mesh, tolerance_mm=float("nan"))
        with self.assertRaises(ValueError):
            self.compare(self.mesh, self.mesh, graine=-1)

    def test_identite_reproductible(self):
        r = self.compare(self.mesh, self.mesh)
        self.assertEqual(r, self.compare(self.mesh, self.mesh))
        self.assertEqual(r["statut"], "CONFORME_ECHANTILLONS")
        self.assertLess(r["reference_vers_candidat"]["maximum_observe_mm"], 1e-10)
        self.assertAlmostEqual(r["f_score_tolerance"], 1)

    def test_translation_n_est_pas_cachee(self):
        m = self.mesh.copy()
        m.apply_translation([0, 0, 2])
        r = self.compare(self.mesh, m)
        self.assertEqual(r["statut"], "ECART")
        self.assertAlmostEqual(r["candidat_vers_reference"]["maximum_observe_mm"], 2)
        self.assertAlmostEqual(r["delta_bornes_mm"][0][2], 2)

    def test_echelle_n_est_pas_normalisee(self):
        m = self.mesh.copy()
        m.apply_scale(2)
        r = self.compare(self.mesh, m)
        self.assertEqual(r["statut"], "ECART")
        np.testing.assert_allclose(r["delta_dimensions_mm"], [10, 20, 30])
        self.assertAlmostEqual(r["delta_volume_mm3"], 7 * self.mesh.volume)

    def test_bosse_locale_meme_enveloppe(self):
        m = trimesh.creation.icosphere(subdivisions=2, radius=10)
        c = m.copy()
        i = np.argmax(c.vertices.sum(axis=1))
        c.vertices[i] *= 0.6
        r = self.compare(m, c, tolerance_mm=0.05)
        self.assertEqual(r["statut"], "ECART")
        np.testing.assert_allclose(r["delta_dimensions_mm"], 0)
        self.assertGreater(r["reference_vers_candidat"]["maximum_observe_mm"], 0.05)

    def test_composant_supprime(self):
        petit = trimesh.creation.box(extents=[1, 1, 1])
        petit.apply_translation([7, 0, 0])
        r = self.compare(trimesh.util.concatenate([self.mesh, petit]), self.mesh)
        self.assertEqual(r["reference"]["corps_connectes"], 2)
        self.assertEqual(r["candidat"]["corps_connectes"], 1)
        self.assertTrue(any("Topologie" in s for s in r["raisons"]))

    def test_ouverture_normales_degenerees(self):
        ouvert = self.mesh.copy()
        ouvert.update_faces(np.arange(len(ouvert.faces) - 1))
        r = self.compare(self.mesh, ouvert)
        self.assertFalse(r["candidat"]["ferme_topologiquement"])
        self.assertEqual(r["statut"], "ECART")
        m = self.mesh.copy()
        m.faces[0] = m.faces[0][::-1]
        self.assertFalse(self.compare(self.mesh, m)["candidat"]["orientation_coherente"])
        m = self.mesh.copy()
        m.faces = np.vstack([m.faces, [0, 0, 0]])
        self.assertEqual(self.compare(self.mesh, m)["statut"], "ECART")

    def test_orientation_globale_inversee(self):
        m = self.mesh.copy()
        m.invert()
        r = self.compare(self.mesh, m)
        self.assertEqual(r["statut"], "ECART")
        self.assertFalse(r["candidat"]["solide_oriente_exterieur"])

    def test_surface_nulle_refusee(self):
        m = trimesh.Trimesh(vertices=[[0, 0, 0], [0, 0, 0], [0, 0, 0]], faces=[[0, 1, 2]], process=False)
        with self.assertRaises(ValueError):
            self.compare(m, self.mesh)

    def test_recalage_rigide_translation(self):
        m = self.mesh.copy()
        m.apply_translation([30, -20, 5])
        h = m.vertices.copy()
        c, mat, cost = recaler(self.mesh, m, nombre=200)
        np.testing.assert_allclose(c.vertices, self.mesh.vertices, atol=1e-7)
        np.testing.assert_array_equal(m.vertices, h)
        self.assertAlmostEqual(np.linalg.det(mat[:3, :3]), 1)
        self.assertLess(cost, 1e-12)

    def test_recalage_ne_corrige_pas_echelle(self):
        m = self.mesh.copy()
        m.apply_scale(1.2)
        c, mat, _ = recaler(self.mesh, m, nombre=200)
        self.assertAlmostEqual(abs(c.volume / m.volume), 1, places=7)
        self.assertAlmostEqual(np.linalg.det(mat[:3, :3]), 1, places=7)
        self.assertEqual(self.compare(self.mesh, c)["statut"], "ECART")

    def test_cli_glb_instances_unites_export_relu(self):
        scene = trimesh.Scene()
        small = trimesh.creation.box(extents=[0.01, 0.02, 0.03])
        scene.add_geometry(small, geom_name="partage", node_name="a")
        scene.graph.update(
            frame_to="b", matrix=trimesh.transformations.translation_matrix([0.02, 0, 0]), geometry="partage"
        )
        src = self.d / "scene.glb"
        src.write_bytes(scene.export(file_type="glb"))
        h = empreinte(src)
        out = self.d / "p.stl"
        report = self.d / "p.json"
        self.assertEqual(
            main(
                [
                    "preparer",
                    str(src),
                    "--unites",
                    "m",
                    "--axe-haut",
                    "y",
                    "--hauteur-mm",
                    "40",
                    "--poser",
                    "--sortie",
                    str(out),
                    "--rapport",
                    str(report),
                ]
            ),
            0,
        )
        r = json.loads(report.read_text())
        np.testing.assert_allclose(r["export_relu"]["dimensions_mm"], [60, 60, 40], atol=1e-4)
        self.assertEqual(r["export_relu"]["corps_connectes"], 2)
        self.assertTrue(r["sources_preservees"])
        self.assertEqual(empreinte(src), h)
        with self.assertRaises(SystemExit):
            main(
                [
                    "preparer",
                    str(src),
                    "--unites",
                    "mm",
                    "--axe-haut",
                    "y",
                    "--sortie",
                    str(self.d / "refuse.stl"),
                    "--rapport",
                    str(self.d / "refuse.json"),
                ]
            )

    def test_cli_sans_ecrasement_ni_fichier_partiel_options(self):
        src = self.d / "a.stl"
        self.mesh.export(src)
        rapport = self.d / "r.json"
        rapport.write_text("intact")
        args = [
            "preparer",
            str(src),
            "--unites",
            "mm",
            "--axe-haut",
            "z",
            "--sortie",
            str(self.d / "b.stl"),
            "--rapport",
            str(rapport),
        ]
        with self.assertRaises(SystemExit):
            main(args)
        self.assertEqual(rapport.read_text(), "intact")
        self.assertFalse((self.d / "b.stl").exists())
        with self.assertRaises(SystemExit):
            main(["comparer", str(src), "--unites", "mm", "--rapport", str(self.d / "x.json")])
        self.assertFalse((self.d / "x.json").exists())

    def test_cli_comparaison_code_et_reference_preservee(self):
        a = self.d / "a.stl"
        b = self.d / "b.stl"
        self.mesh.export(a)
        m = self.mesh.copy()
        m.apply_translation([0, 0, 3])
        m.export(b)
        hashes = [empreinte(a), empreinte(b)]
        r = self.d / "r.json"
        code = main(
            [
                "comparer",
                str(b),
                "--unites",
                "mm",
                "--reference",
                str(a),
                "--unites-reference",
                "mm",
                "--echantillons",
                "200",
                "--rapport",
                str(r),
            ]
        )
        self.assertEqual(code, 1)
        self.assertEqual([empreinte(a), empreinte(b)], hashes)
        self.assertEqual(json.loads(r.read_text())["statut"], "ECART")


class RegressionGeometrie(unittest.TestCase):
    setUp = GeometrieTests.setUp

    def test_reread_failure_does_not_publish_stl(self):
        src = self.d / "src.stl"
        self.mesh.export(src)
        out = self.d / "out.stl"
        report = self.d / "report.json"
        original = G.charger

        def fail_export(path, unit):
            if Path(path) != src:
                raise ValueError("relecture simulée")
            return original(path, unit)

        with patch.object(G, "charger", side_effect=fail_export):
            with self.assertRaises(SystemExit):
                main(
                    [
                        "preparer",
                        str(src),
                        "--unites",
                        "mm",
                        "--axe-haut",
                        "z",
                        "--sortie",
                        str(out),
                        "--rapport",
                        str(report),
                    ]
                )
        self.assertFalse(out.exists())
        self.assertFalse(report.exists())


if __name__ == "__main__":
    unittest.main()
