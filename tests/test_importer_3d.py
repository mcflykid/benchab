import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import trimesh

from benchab.imports3d import importer as outil


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.r = Path(self.tmp.name).resolve()

    def box(self, name="box.stl", extents=(2, 3, 4)):
        p = self.r / name
        trimesh.creation.box(extents=extents).export(p)
        return p

    def test_stl_welded_topology_and_explicit_units(self):
        p = self.box()
        _, report = outil.auditer(p, "cm", "modèle témoin")
        self.assertEqual(report["statut"], "PASS")
        self.assertEqual(report["avant"]["dimensions_mm"], [20, 30, 40])
        self.assertEqual(report["avant"]["corps_connectes"], 1)
        self.assertTrue(report["avant"]["ferme_topologiquement"])

    def test_audit_distingue_topologie_indexee_et_soudure_sur_corps_dupliques(self):
        corps = trimesh.creation.box()
        source = trimesh.util.concatenate([corps, corps.copy()])

        mesure = outil.mesurer(source)
        messages = outil.alertes(mesure)

        self.assertTrue(mesure["topologie_source_indexee"]["ferme_topologiquement"])
        self.assertFalse(mesure["topologie_apres_soudure"]["ferme_topologiquement"])
        self.assertEqual(mesure["corps_exactement_superposes"], [[0, 1]])
        self.assertTrue(any("topologie source indexée" in message.lower() for message in messages))
        self.assertFalse(any("Maillage ouvert ou non manifold." == message for message in messages))

    def test_audit_conserve_alerte_maillage_reellement_ouvert(self):
        ouvert = trimesh.creation.box()
        ouvert.update_faces(np.arange(len(ouvert.faces)) != 0)

        mesure = outil.mesurer(ouvert)
        messages = outil.alertes(mesure)

        self.assertFalse(mesure["topologie_source_indexee"]["ferme_topologiquement"])
        self.assertTrue(any("topologie source indexée" in message.lower() for message in messages))

    def test_audit_attribue_a_la_soudure_le_contact_non_manifold_sans_doublon(self):
        premier = trimesh.creation.box(extents=(2, 2, 2))
        second = premier.copy()
        second.apply_translation((2, 2, 0))
        source = trimesh.util.concatenate([premier, second])

        mesure = outil.mesurer(source)
        messages = outil.alertes(mesure)

        self.assertTrue(mesure["topologie_source_indexee"]["ferme_topologiquement"])
        self.assertFalse(mesure["topologie_apres_soudure"]["ferme_topologiquement"])
        self.assertEqual(mesure["corps_exactement_superposes"], [])
        self.assertTrue(any("après soudure" in message for message in messages))
        self.assertFalse(any("dans la topologie source indexée avant soudure" in message for message in messages))

    def test_scene_instances_transforms_applied(self):
        scene = trimesh.Scene()
        mesh = trimesh.creation.box(extents=(1, 1, 1))
        scene.add_geometry(mesh, node_name="one", geom_name="box")
        t = np.eye(4)
        t[0, 3] = 10
        scene.graph.update(frame_to="two", matrix=t, geometry="box")
        p = self.r / "instances.glb"
        p.write_bytes(scene.export(file_type="glb"))
        _, report = outil.auditer(p, "m")
        self.assertEqual(report["avant"]["dimensions_mm"], [11000, 1000, 1000])
        self.assertEqual(report["avant"]["corps_connectes"], 2)
        self.assertEqual(report["statut"], "WARN")

    def test_glb_refuses_units_other_than_meters(self):
        p = self.r / "unit.glb"
        p.write_bytes(trimesh.Scene(trimesh.creation.box()).export(file_type="glb"))
        for unit in ("mm", "cm"):
            with self.assertRaisesRegex(ValueError, "GLB utilise le mètre"):
                outil.auditer(p, unit)

    def test_obj_material_fifo_is_not_opened(self):
        import os

        p = self.r / "source.obj"
        p.write_text("mtllib annexe.mtl\nv 0 0 0\nv 2 0 0\nv 0 3 0\nf 1 2 3\n")
        os.mkfifo(self.r / "annexe.mtl")
        code = (
            "import sys;from benchab.imports3d.importer import charger;"
            'print(charger(sys.argv[1],"mm").extents.tolist())'
        )
        result = subprocess.run([sys.executable, "-c", code, str(p)], capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[2.0, 3.0, 0.0]", result.stdout)

    def test_source_fifo_refused_before_fingerprint_blocks(self):
        import os

        p = self.r / "source.stl"
        os.mkfifo(p)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "benchab.imports3d.importer",
                "audit",
                str(p),
                "--unites",
                "mm",
                "--rapport",
                str(self.r / "report.json"),
            ],
            capture_output=True,
            text=True,
            timeout=3,
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("fichier régulier requis", result.stderr)
        self.assertFalse((self.r / "report.json").exists())

    def test_glb_external_buffer_refused_without_reading_fifo(self):
        import json
        import os
        import struct

        original = trimesh.Scene(trimesh.creation.box()).export(file_type="glb")
        length = struct.unpack_from("<I", original, 12)[0]
        data = json.loads(original[20 : 20 + length])
        data["buffers"][0]["uri"] = "annexe.bin"
        header = json.dumps(data).encode()
        header += b" " * ((-len(header)) % 4)
        chunks = original[20 + length :]
        p = self.r / "external.glb"
        p.write_bytes(
            struct.pack("<5I", 0x46546C67, 2, 20 + len(header) + len(chunks), len(header), 0x4E4F534A) + header + chunks
        )
        os.mkfifo(self.r / "annexe.bin")
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "benchab.imports3d.importer",
                "audit",
                str(p),
                "--unites",
                "m",
                "--rapport",
                str(self.r / "report.json"),
            ],
            capture_output=True,
            text=True,
            timeout=3,
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("ressource externe requise", result.stderr.lower())
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse((self.r / "report.json").exists())

    def test_open_mesh_warns(self):
        mesh = trimesh.creation.box()
        mesh.update_faces(np.arange(len(mesh.faces)) != 0)
        p = self.r / "open.obj"
        mesh.export(p)
        _, report = outil.auditer(p, "mm")
        self.assertFalse(report["avant"]["ferme_topologiquement"])
        self.assertEqual(report["statut"], "WARN")
        self.assertIsNone(report["avant"]["volume_mm3"])

    def test_prepare_scaled_copy_preserves_source_and_components(self):
        a = trimesh.creation.box(extents=(2, 3, 4))
        b = a.copy()
        b.apply_translation([10, 0, 0])
        p = self.r / "two.obj"
        trimesh.util.concatenate([a, b]).export(p)
        before = p.read_bytes()
        report = outil.preparer(p, self.r / "result.stl", "mm", hauteur_mm=20)
        self.assertEqual(p.read_bytes(), before)
        self.assertTrue(report["source_preservee"])
        self.assertEqual(report["apres"]["corps_connectes"], 2)
        self.assertAlmostEqual(report["apres"]["dimensions_mm"][2], 20)

    def test_cleanup_degenerate_faces_preserves_original(self):
        mesh = trimesh.creation.box()
        mesh.faces = np.vstack([mesh.faces, [0, 0, 0]])
        p = self.r / "degenerate.obj"
        mesh.export(p)
        report = outil.preparer(p, self.r / "clean.stl", "mm", nettoyer=True)
        self.assertEqual(report["avant"]["faces_degenerees"], 1)
        self.assertEqual(report["apres"]["faces_degenerees"], 0)
        self.assertEqual(report["operations"][0]["faces_retirees"], 1)
        self.assertEqual(report["statut_avant"], "WARN")
        self.assertEqual(report["statut_apres"], "PASS")
        self.assertEqual(report["statut"], "PASS")
        self.assertTrue(report["source_preservee"])

    def test_refuses_existing_output_and_original(self):
        p = self.box()
        for out in (p, self.box("existing.stl")):
            before = out.read_bytes()
            with self.assertRaises(ValueError):
                outil.preparer(p, out, "mm")
            self.assertEqual(out.read_bytes(), before)

    def test_refuses_invalid_height_without_writing(self):
        p = self.box()
        for h in (0, -3, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                outil.preparer(p, self.r / "bad.stl", "mm", hauteur_mm=h)
        self.assertFalse((self.r / "bad.stl").exists())

    def test_cli_units_required_and_report_not_overwritten(self):
        p = self.box()
        script = "benchab.imports3d.importer"
        r = subprocess.run(
            [sys.executable, "-m", script, "audit", str(p), "--rapport", str(self.r / "x.json")], capture_output=True
        )
        self.assertEqual(r.returncode, 2)
        report = self.r / "existing.json"
        report.write_text("préserver")
        r = subprocess.run(
            [sys.executable, "-m", script, "audit", str(p), "--unites", "mm", "--rapport", str(report)],
            capture_output=True,
        )
        self.assertEqual(r.returncode, 2)
        self.assertEqual(report.read_text(), "préserver")


class RegressionImporter(unittest.TestCase):
    setUp = ImportTests.setUp
    box = ImportTests.box

    def test_failed_reread_does_not_publish_stl(self):
        src = self.box()
        out = self.r / "out.stl"
        original = outil.charger

        def fail_export(path, unit):
            if Path(path) != src:
                raise ValueError("relecture simulée")
            return original(path, unit)

        with patch.object(outil, "charger", side_effect=fail_export):
            with self.assertRaises(ValueError):
                outil.preparer(src, out, "mm")
        self.assertFalse(out.exists())


class PublicationImporter(unittest.TestCase):
    setUp = ImportTests.setUp
    box = ImportTests.box

    def test_report_failure_no_stl_or_report(self):
        src = self.box()
        out = self.r / "out.stl"
        report = self.r / "report.json"
        with patch.object(outil.json, "dumps", side_effect=ValueError("serialization failed")):
            with self.assertRaises(ValueError):
                outil.preparer(src, out, "mm", rapport_sortie=report)
        self.assertFalse(out.exists())
        self.assertFalse(report.exists())

    def test_source_changed_during_audit_no_output(self):
        src = self.box()
        out = self.r / "out.stl"
        original = outil.charger

        def changed(path, *args):
            mesh = original(path, *args)
            src.write_bytes(src.read_bytes() + b"changed")
            return mesh

        with patch.object(outil, "charger", side_effect=changed):
            with self.assertRaises(ValueError):
                outil.preparer(src, out, "mm")
        self.assertFalse(out.exists())

    def test_glb_rotated_scaled_instances(self):
        scene = trimesh.Scene()
        mesh = trimesh.creation.box(extents=[0.01, 0.02, 0.03])
        scene.add_geometry(mesh, geom_name="g", node_name="one")
        transform = trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0])
        transform[:3, :3] *= 2
        transform[:3, 3] = [0.1, 0, 0]
        scene.graph.update(frame_to="two", matrix=transform, geometry="g")
        src = self.r / "scene.glb"
        src.write_bytes(scene.export(file_type="glb"))
        loaded = outil.charger(src, "m")
        expected = mesh.copy()
        expected.apply_transform(transform)
        combined = trimesh.util.concatenate([mesh, expected])
        combined.apply_scale(1000)
        np.testing.assert_allclose(loaded.bounds, combined.bounds, atol=1e-5)
        self.assertEqual(outil.mesurer(loaded)["corps_connectes"], 2)

    def test_export_geometry_changed_refused(self):
        src = self.box()
        out = self.r / "out.stl"
        original = outil.charger

        def changed(path, *args):
            mesh = original(path, *args)
            if Path(path) != src:
                mesh.apply_scale(2)
            return mesh

        with patch.object(outil, "charger", side_effect=changed):
            with self.assertRaises(ValueError):
                outil.preparer(src, out, "mm")
        self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
