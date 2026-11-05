"""Tests de reparer.py (chaîne C, 27/10/2026) sur des maillages abîmés synthétiques."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import trimesh

from benchab.imports3d import reparer as R


def abime():
    b = trimesh.creation.box([20, 20, 20])
    b = trimesh.Trimesh(
        b.triangles.reshape(-1, 3), np.arange(36).reshape(-1, 3), process=False
    )  # sommets non soudés, comme un STL
    f = b.faces.copy()
    f[0] = f[0][::-1]  # une face retournée
    return trimesh.Trimesh(b.vertices, np.vstack([f, f[3:4], [[0, 0, 1]]]), process=False)  # + doublon + dégénérée


class Reparer(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.d = Path(self.t.name).resolve()

    def tearDown(self):
        self.t.cleanup()

    def lancer(self, m, *a):
        src = self.d / "source.stl"
        m.export(src)
        return R.main(
            [str(src), str(self.d / "sortie.stl"), "--unites", "mm", "--rapport", str(self.d / "r.json"), *a]
        ), json.loads((self.d / "r.json").read_text())

    def test_defauts_corriges_et_valide_par_manifold(self):
        c, r = self.lancer(abime())
        self.assertEqual(c, 0, r["decision"])
        self.assertTrue(r["apres"]["ferme_topologiquement"])
        self.assertTrue(r["apres"]["orientation_coherente"])
        self.assertEqual(r["apres"]["faces_degenerees"], 0)
        self.assertTrue(r["manifold3d"]["solide"])
        self.assertAlmostEqual(r["apres"]["volume_mm3"], 8000, delta=1)

    def test_petit_trou_bouche_grand_trou_laisse(self):
        b = trimesh.creation.icosphere(3, 10)
        m = trimesh.Trimesh(b.vertices, b.faces[1:])  # un triangle manquant
        c, r = self.lancer(m)
        self.assertEqual(c, 0, r["decision"])
        b = trimesh.creation.icosphere(3, 10)
        m = trimesh.Trimesh(b.vertices, b.faces[b.triangles_center[:, 2] < 6])  # calotte coupée : grand trou
        (self.d / "sortie.stl").unlink()
        (self.d / "r.json").unlink()
        c, r = self.lancer(m)
        self.assertEqual(c, 1)
        self.assertTrue(any("NON bouché" in e["etape"] for e in r["corrections"]))

    def test_jamais_d_ecrasement(self):
        (self.d / "sortie.stl").write_text("existant")
        src = self.d / "source.stl"
        abime().export(src)
        self.assertEqual(R.main([str(src), str(self.d / "sortie.stl"), "--unites", "mm"]), 2)
        self.assertEqual((self.d / "sortie.stl").read_text(), "existant")


class RegressionReparer(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.d = Path(self.t.name).resolve()
        self.src = self.d / "source.stl"
        trimesh.creation.box().export(self.src)
        self.out = self.d / "out.stl"
        self.report = self.d / "report.json"

    def run_repair(self, rapport=None, *extra):
        return R.main(
            [str(self.src), str(self.out), "--unites", "mm", "--rapport", str(rapport or self.report), *extra]
        )

    def test_report_collisions_preserve_input_and_no_output(self):
        before = self.src.read_bytes()
        self.assertEqual(self.run_repair(self.src), 2)
        self.assertEqual(self.src.read_bytes(), before)
        self.assertFalse(self.out.exists())

    def test_report_collision_with_output(self):
        self.assertEqual(self.run_repair(self.out), 2)
        self.assertFalse(self.out.exists())

    def test_existing_report_preserved(self):
        self.report.write_text("preserve")
        self.assertEqual(self.run_repair(), 2)
        self.assertEqual(self.report.read_text(), "preserve")
        self.assertFalse(self.out.exists())

    def test_absent_manifold_never_validated(self):
        with patch.object(R, "valide_manifold", return_value={"statut": "manifold3d absent", "solide": None}):
            self.assertEqual(self.run_repair(), 1)
        self.assertNotEqual(json.loads(self.report.read_text())["decision"], "RÉPARÉ ET VALIDE")

    def test_fill_large_supported_threshold_truthful(self):
        mesh = trimesh.creation.icosphere(subdivisions=1)
        mesh.update_faces(mesh.triangles_center[:, 2] < 0.6)
        repaired, journal = R.reparer(mesh, 100)
        self.assertGreater(R.bords_ouverts(repaired)[0], 0)
        self.assertFalse(any("trou(s) bouché(s)" in e["etape"] for e in journal))

    def test_negative_and_nonfinite_parameters_refused(self):
        self.assertEqual(self.run_repair(None, "--trous-max", "-1"), 2)
        self.assertFalse(self.out.exists())
        self.assertEqual(self.run_repair(None, "--tolerance-volume", "nan"), 2)
        self.assertFalse(self.out.exists())


class GeometriesSynthetiques(unittest.TestCase):
    setUp = RegressionReparer.setUp
    run_repair = RegressionReparer.run_repair

    # Battery checks numeric contracts after STL export and reread only.
    def test_synthetic_repair_battery_export_reread(self):
        box = trimesh.creation.box(extents=[2, 3, 4])
        inverse = box.copy()
        inverse.invert()
        duplicate = box.copy()
        duplicate.faces = np.vstack([duplicate.faces, duplicate.faces[:1]])
        degenerate = box.copy()
        degenerate.faces = np.vstack([degenerate.faces, [0, 0, 0]])
        small = box.copy()
        small.update_faces(np.arange(len(small.faces)) != 0)
        several = box.copy()
        several.update_faces(~np.isin(np.arange(len(several.faces)), [0, 11]))
        large = trimesh.creation.icosphere(subdivisions=1)
        large.update_faces(large.triangles_center[:, 2] < 0.6)
        other = box.copy()
        other.apply_translation([5, 0, 0])
        intersect = box.copy()
        intersect.apply_translation([1, 0, 0])
        nonmanifold = box.copy()
        nonmanifold.vertices = np.vstack([nonmanifold.vertices, [20, 20, 20]])
        nonmanifold.faces = np.vstack([nonmanifold.faces, [0, 1, len(nonmanifold.vertices) - 1]])
        cases = {
            "duplicate": duplicate,
            "degenerate": degenerate,
            "inverse": inverse,
            "small": small,
            "several": several,
            "large": large,
            "separate": trimesh.util.concatenate([box, other]),
            "intersecting": trimesh.util.concatenate([box, intersect]),
            "nonmanifold": nonmanifold,
        }
        for name, mesh in cases.items():
            with self.subTest(name=name):
                src = self.d / (name + ".obj")
                mesh.export(src)
                before = src.read_bytes()
                out = self.d / (name + ".stl")
                report = self.d / (name + ".json")
                code = R.main([str(src), str(out), "--unites", "mm", "--rapport", str(report), "--trous-max", "100"])
                self.assertIn(code, (0, 1))
                self.assertEqual(src.read_bytes(), before)
                result = json.loads(report.read_text())
                relu = R.mesurer(R.charger(out, "mm"))
                self.assertEqual(result["apres"], relu)
                self.assertEqual(result["sortie_sha256"], R.empreinte(out))
                self.assertEqual(result["apres"]["faces_degenerees"], 0)
                if name in ("duplicate", "degenerate", "inverse", "small", "several", "separate"):
                    self.assertTrue(relu["ferme_topologiquement"])
                    self.assertTrue(relu["orientation_coherente"])
                if name == "separate":
                    self.assertEqual(relu["corps_connectes"], 2)
                if name in ("large", "nonmanifold"):
                    self.assertEqual(code, 1)
                if name == "intersecting":
                    self.assertEqual(relu["corps_connectes"], 2)  # no intersection proof

    def test_mixed_holes_threshold_and_remaining_counts(self):
        small = trimesh.creation.box()
        small.update_faces(np.arange(len(small.faces)) != 0)
        large = trimesh.creation.icosphere(subdivisions=1)
        large.update_faces(large.triangles_center[:, 2] < 0.6)
        large.apply_translation([5, 0, 0])
        mixed = trimesh.util.concatenate([small, large])
        self.assertEqual(R.bords_ouverts(mixed)[0], 2)
        untouched, j = R.reparer(mixed, 2)
        self.assertEqual(R.bords_ouverts(untouched)[0], 2)
        repaired, j = R.reparer(mixed, 3)
        self.assertEqual(R.bords_ouverts(repaired)[0], 1)
        operation = next(e for e in j if "ouvertures_fermees" in e and e["ouvertures_fermees"])
        self.assertEqual(operation["ouvertures_fermees"], 1)
        self.assertEqual(operation["ouvertures_restantes"], 1)
        disabled, j = R.reparer(mixed, 0)
        self.assertEqual(R.bords_ouverts(disabled)[0], 2)
        self.assertTrue(any("désactivé" in e["etape"] for e in j))

    def test_volume_change_refused_without_stl(self):
        original = R.reparer

        def changed(mesh, *args):
            m, j = original(mesh, *args)
            m.apply_scale(2)
            return m, j

        with patch.object(R, "reparer", side_effect=changed):
            self.assertEqual(self.run_repair(), 2)
        self.assertFalse(self.out.exists())
        self.assertTrue(json.loads(self.report.read_text())["decision"].startswith("REFUSÉ"))

    def test_source_change_during_load_refused(self):
        original = R.charger

        def changed(path, *args):
            result = original(path, *args)
            if Path(path) == self.src:
                self.src.write_bytes(self.src.read_bytes() + b"changed")
            return result

        with patch.object(R, "charger", side_effect=changed):
            self.assertEqual(self.run_repair(), 2)
        self.assertFalse(self.out.exists())
        self.assertFalse(self.report.exists())

    def test_manifold_engine_error_distinct_from_invalid(self):
        import types

        fake = types.SimpleNamespace(
            Mesh=lambda **kwargs: None, Manifold=lambda mesh: (_ for _ in ()).throw(RuntimeError("engine failed"))
        )
        with patch.dict(sys.modules, {"manifold3d": fake}):
            result = R.valide_manifold(trimesh.creation.box())
        self.assertEqual(result["statut"], "erreur moteur")
        self.assertIsNone(result["solide"])
        with patch.dict(sys.modules, {"manifold3d": None}):
            result = R.valide_manifold(trimesh.creation.box())
        self.assertEqual(result["statut"], "manifold3d absent")
        self.assertIsNone(result["solide"])
        self.assertFalse(
            R.valide_manifold(trimesh.Trimesh(vertices=[[0, 0, 0], [1, 0, 0], [0, 1, 0]], faces=[[0, 1, 2]]))["solide"]
        )

    def test_report_write_failure_leaves_no_output(self):
        original = Path.write_text

        def fail(path, *args, **kw):
            if path.name.endswith(".json"):
                raise OSError("report failed")
            return original(path, *args, **kw)

        with patch.object(Path, "write_text", fail):
            self.assertEqual(self.run_repair(), 2)
        self.assertFalse(self.out.exists())
        self.assertFalse(self.report.exists())

    def test_symlink_collision_preserves_source(self):
        before = self.src.read_bytes()
        link = self.d / "link.json"
        link.symlink_to(self.src)
        self.assertEqual(self.run_repair(link), 2)
        self.assertEqual(self.src.read_bytes(), before)
        self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()
