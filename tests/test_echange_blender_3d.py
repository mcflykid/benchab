import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import trimesh

from benchab.imports3d import echange_blender as E


class EchangeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.r = Path(self.tmp.name).resolve()
        self.src = self.r / "source.stl"
        trimesh.creation.box(extents=(2, 3, 4)).export(self.src)
        self.dest = self.r / "travail.stl"

    def prepare(self):
        return E.preparer_echange(self.src, self.dest, "mm")

    def candidate(self, extents=(2, 3, 4)):
        p = self.r / "retouche.stl"
        trimesh.creation.box(extents=extents).export(p)
        return p

    def test_roundtrip_and_original_unchanged(self):
        sha = E.empreinte(self.src)
        m = self.prepare()
        c = self.candidate((2.2, 3, 4))
        r = E.controler_retour(m["manifeste"], c, "mm")
        self.assertEqual(r["statut"], "A_CONFIRMER")
        self.assertTrue(r["changements"])
        with self.assertRaises(ValueError):
            E.importer_retour(m["manifeste"], c, "mm", self.r / "v2.stl")
        rr = E.importer_retour(m["manifeste"], c, "mm", self.r / "v2.stl", True)
        self.assertEqual(rr["statut"], "VERSION_NEUVE_CONTROLEE")
        self.assertEqual(E.empreinte(self.src), sha)

    def test_explicit_units_and_glb_units(self):
        with self.assertRaises(ValueError):
            E.preparer_echange(self.src, self.dest, None)
        g = self.r / "scene.glb"
        g.write_bytes(trimesh.Scene(trimesh.creation.box()).export(file_type="glb"))
        with self.assertRaises(ValueError):
            E.preparer_echange(g, self.dest, "mm")

    def test_instances_matrices_preserved(self):
        scene = trimesh.Scene()
        scene.add_geometry(trimesh.creation.box(), geom_name="g", node_name="a")
        m = np.eye(4)
        m[0, 3] = 0.01
        scene.graph.update(frame_to="b", matrix=m, geometry="g")
        p = self.r / "instances.glb"
        p.write_bytes(scene.export(file_type="glb"))
        d = E.preparer_echange(p, self.dest, "m")
        self.assertEqual(len(d["instances"]), 2)
        self.assertEqual(d["travail"]["corps_connectes"], 2)
        self.assertAlmostEqual(d["travail"]["dimensions_mm"][0], 1010, places=4)

    def test_obj_material_fifo_ignored_in_both_loads(self):
        import os

        p = self.r / "source.obj"
        p.write_text("mtllib annexe.mtl\nv 0 0 0\nv 2 0 0\nv 0 3 0\nf 1 2 3\n")
        os.mkfifo(self.r / "annexe.mtl")
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "benchab.imports3d.echange_blender",
                "preparer",
                str(p),
                "--destination",
                str(self.dest),
                "--unites",
                "mm",
            ],
            capture_output=True,
            text=True,
            timeout=4,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(E.charger(self.dest, "mm").extents.tolist(), [2, 3, 0])

    def test_rigid_extra_transform(self):
        m = np.eye(4)
        m[0, 3] = 12
        d = E.preparer_echange(self.src, self.dest, "mm", {"source": m.tolist()})
        self.assertEqual(d["travail"]["bornes_mm"][0][0], 11)

    def test_reflections_scale_shear_and_invalid_matrices(self):
        for matrix in (np.diag([-1, 1, 1, 1]), np.diag([2, 2, 2, 1]), np.ones((4, 4)), np.eye(3)):
            with self.assertRaises(ValueError):
                E.preparer_echange(self.src, self.dest, "mm", {"source": matrix.tolist()})
        with self.assertRaises(ValueError):
            E.preparer_echange(self.src, self.dest, "mm", {"missing": np.eye(4).tolist()})

    def test_source_and_manifest_tampering(self):
        d = self.prepare()
        c = self.candidate()
        m = Path(d["manifeste"])
        obj = json.loads(m.read_text())
        obj["travail"]["faces"] = 99
        m.write_text(json.dumps(obj))
        with self.assertRaises(ValueError):
            E.controler_retour(m, c, "mm")

    def test_source_tampering(self):
        d = self.prepare()
        c = self.candidate()
        self.src.write_bytes(b"altere")
        with self.assertRaises(ValueError):
            E.controler_retour(d["manifeste"], c, "mm")

    def test_collision_no_partial_publication(self):
        self.dest.with_suffix(".echange.json").write_text("utilisateur")
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(self.dest.exists())
        self.assertEqual(self.dest.with_suffix(".echange.json").read_text(), "utilisateur")

    def test_paths_symlink_and_traversal(self):
        p = self.r / "lien"
        p.symlink_to(self.src)
        with self.assertRaises(ValueError):
            E.preparer_echange(p, self.dest, "mm")
        with self.assertRaises(ValueError):
            E.preparer_echange(self.src, self.r / "a/../sortie.stl", "mm")

    def test_unit_scale_return_rejected(self):
        d = self.prepare()
        c = self.candidate()
        with self.assertRaises(ValueError):
            E.controler_retour(d["manifeste"], c, "m")

    def test_return_must_be_distinct_and_import_collision(self):
        d = self.prepare()
        with self.assertRaises(ValueError):
            E.controler_retour(d["manifeste"], self.dest, "mm")
        c = self.candidate()
        v = self.r / "v2.stl"
        v.write_text("préserver")
        with self.assertRaises(ValueError):
            E.importer_retour(d["manifeste"], c, "mm", v, True)
        self.assertEqual(v.read_text(), "préserver")

    def test_cli_isolated_help(self):
        r = subprocess.run(
            [sys.executable, "-I", "-m", "benchab.imports3d.echange_blender", "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("preparer", r.stdout)

    def test_cli_transformations_symlink_refused(self):
        matrices = self.r / "matrices.json"
        matrices.write_text("{}")
        link = self.r / "alias.json"
        link.symlink_to(matrices)
        r = subprocess.run(
            [
                sys.executable,
                "-I",
                "-m",
                "benchab.imports3d.echange_blender",
                "preparer",
                str(self.src),
                "--destination",
                str(self.dest),
                "--unites",
                "mm",
                "--transformations",
                str(link),
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertFalse(self.dest.exists())


if __name__ == "__main__":
    unittest.main()
