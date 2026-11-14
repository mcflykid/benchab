"""Fixtures C05 : transformations, supports modaux et grands modèles ; aucune pièce réelle."""

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

import numpy as np
import trimesh

from benchab.visualisation import visionneuse as V


class Transformations(unittest.TestCase):
    def test_obj_material_fifo_is_never_opened(self):
        import os
        import subprocess

        with tempfile.TemporaryDirectory() as d:
            p = Path(d).resolve()
            source = p / "piece.obj"
            source.write_text("mtllib material.mtl\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
            os.mkfifo(p / "material.mtl")
            code = (
                "import sys;sys.path.insert(0,sys.argv[1]);from visionneuse import"
                " Visionneuse;v=Visionneuse('fixture');v.modele('Piece',sys.argv[2"
                "]);assert len(v.modeles['Piece']['maillage'].faces)==1"
            )
            result = subprocess.run(
                [sys.executable, "-c", code, str(Path(V.__file__).parent), str(source)],
                capture_output=True,
                text=True,
                timeout=5,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_source_fifo_refused_and_fd_closed(self):
        import os
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "source.stl"
            os.mkfifo(source)
            original = V.os.open
            opened = []

            def ouvrir(*args, **kwargs):
                fd = original(*args, **kwargs)
                opened.append(fd)
                return fd

            with patch.object(V.os, "open", side_effect=ouvrir):
                with self.assertRaisesRegex(ValueError, "non régulière"):
                    V.charger_maillage(source)
            self.assertEqual(len(opened), 1)
            with self.assertRaises(OSError):
                os.fstat(opened[0])

    def test_glb_external_buffer_refused(self):
        import struct

        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "piece.glb"
            raw = trimesh.creation.box().export(file_type="glb")
            n = struct.unpack_from("<I", raw, 12)[0]
            data = json.loads(raw[20 : 20 + n])
            data["buffers"][0]["uri"] = "external.bin"
            js = json.dumps(data, separators=(",", ":")).encode()
            js += b" " * (-len(js) % 4)
            body = struct.pack("<II", len(js), 1313821514) + js + raw[20 + n :]
            source.write_bytes(struct.pack("<III", 1179937895, 2, 12 + len(body)) + body)
            with self.assertRaisesRegex(ValueError, "externe"):
                V.charger_maillage(source)

    def test_scene_instances_transformed_and_open(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            scene = trimesh.Scene()
            box = trimesh.creation.box((2, 4, 6))
            scene.add_geometry(box, geom_name="B", node_name="one")
            scene.add_geometry(
                box, geom_name="C", node_name="two", transform=V.translation(100, 0, 0) @ V.rotation("Z", 90)
            )
            scene.export(p / "scene.glb")
            v = V.Visionneuse("multi")
            v.modele("Scene", p / "scene.glb")
            v.piece("Scene", "Scene", V.translation(1, 2, 3))
            self.assertGreater(v.modeles["Scene"]["maillage"].extents[0], 100)
            opened = box.copy()
            opened.update_faces(np.arange(len(opened.faces) - 1))
            v.modele("Open", opened)
            v.piece("Open", "Open", V.translation(1000, 0, 0))
            v.ecrire(p / "page.html")
            self.assertTrue((p / "page.html").exists())

    def test_units_axis_bounds_and_large_mesh(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            v = V.Visionneuse("unités", axe_haut="Z")
            mesh = trimesh.creation.icosphere(subdivisions=5, radius=1000)
            original = mesh.vertices.copy()
            v.modele("Grand", mesh, max_faces=100, allegement=0.25)
            M = V.translation(100, 200, 300) @ V.rotation("X", 90) @ np.diag([2, 1, 0.5, 1])
            v.piece("Grand", "Grand", M)
            v.ecrire(p / "page.html", hors_ligne=True)
            html = (p / "page.html").read_text()
            data = json.loads(html.split('id="donnees"')[1].split(">", 1)[1].split("</script>")[0])
            self.assertEqual(data["axe_haut"], "Z")
            self.assertTrue(np.allclose(data["centre"], [100, 200, 300], atol=0.3))
            self.assertTrue(
                np.allclose(np.array(data["bornes"])[1] - np.array(data["bornes"])[0], [4000, 1000, 2000], atol=1)
            )
            self.assertTrue(np.array_equal(mesh.vertices, original))

    def test_support_absolute_retraction(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            trimesh.creation.box().export(p / "Cube.stl")
            (p / "placement.json").write_text(
                json.dumps({"objects": [{"name": "Cube", "model": "Cube", "plate": 1, "xy_interface": [10, 10]}]})
            )
            with zipfile.ZipFile(p / "s.3mf", "w") as z:
                z.writestr(
                    "Metadata/plate_1.gcode",
                    "G90\nM82\nG92 E0\nG1 Z0.2\n;TYPE:Support\nG1 X10 Y0 E5\nG1 X30 E4\nG1 X50 E5\n",
                )
            v = V.Visionneuse("supports")
            v.modele("Cube", p / "Cube.stl")
            v.piece("Cube", "Cube")
            v.vue_impression(p / "placement.json", p, p / "s.3mf")
            self.assertLess(v.modeles["supports_1"]["maillage"].bounds[1, 0], 20)

    def test_normal_inverse_transpose(self):
        html = (Path(__file__).resolve().parents[1] / "benchab/visualisation/gabarit.html").read_text()
        self.assertIn("getNormalMatrix(p.base)", html)
        self.assertIn("getNormalMatrix(p.mesh.matrixWorld)", html)


if __name__ == "__main__":
    unittest.main()
