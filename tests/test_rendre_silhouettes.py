import copy
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from benchab.references_visuelles import rendre_silhouettes as module


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "cube.stl").write_text("test")
        self.spec = {
            "meshes": [{"path": "cube.stl", "units": "mm"}],
            "output_dir": "rendus",
            "resolution": [64, 64],
            "views": {"face": {"position": [0, -100, 0], "target": [0, 0, 0], "ortho_scale": 50}},
        }

    def test_fixed_camera_contract(self):
        before = copy.deepcopy(self.spec)
        self.assertEqual(module.validate_spec(self.spec, self.root), self.root / "rendus")
        self.assertEqual(self.spec, before)
        self.assertFalse((self.root / "rendus").exists())

    def test_existing_output_refused(self):
        (self.root / "rendus").mkdir()
        with self.assertRaises(ValueError):
            module.validate_spec(self.spec, self.root)

    def test_existing_output_can_be_reaudited_without_writing(self):
        out = self.root / "rendus"
        out.mkdir()
        sentinel = out / "face.png"
        sentinel.write_bytes(b"original")
        self.assertEqual(module.validate_spec(self.spec, self.root, check_output=False), out)
        self.assertEqual(sentinel.read_bytes(), b"original")
        bad = copy.deepcopy(self.spec)
        bad["views"]["face"]["ortho_scale"] = 0
        with self.assertRaises(ValueError):
            module.validate_spec(bad, self.root, check_output=False)

    def test_bad_camera(self):
        for key, value in [("position", [0, 0, 0]), ("ortho_scale", -1), ("target", [0, float("nan"), 0])]:
            bad = copy.deepcopy(self.spec)
            bad["views"]["face"][key] = value
            with self.assertRaises(ValueError):
                module.validate_spec(bad, self.root)

    def test_units_and_safe_views(self):
        for change in ["units", "name", "missing", "resolution"]:
            bad = copy.deepcopy(self.spec)
            if change == "units":
                del bad["meshes"][0]["units"]
            if change == "name":
                bad["views"]["../face"] = bad["views"].pop("face")
            if change == "missing":
                bad["meshes"][0]["path"] = "absent.stl"
            if change == "resolution":
                bad["resolution"] = [True, 64]
            with self.assertRaises((ValueError, KeyError)):
                module.validate_spec(bad, self.root)

    def test_glb_uses_meters(self):
        (self.root / "cube.glb").write_bytes(b"test")
        self.spec["meshes"][0]["path"] = "cube.glb"
        with self.assertRaises(ValueError):
            module.validate_spec(self.spec, self.root)
        self.spec["meshes"][0]["units"] = "m"
        module.validate_spec(self.spec, self.root)


@unittest.skipUnless(os.environ.get("TEST_BLENDER") == "1", "essai Blender explicite : TEST_BLENDER=1")
class BlenderIntegrationTests(unittest.TestCase):
    def test_real_box_four_views_and_no_overwrite(self):
        import trimesh
        from PIL import Image

        blender = shutil.which("blender")
        if not blender:
            self.skipTest("Blender absent")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mesh = root / "cube.stl"
            trimesh.creation.box(extents=[20, 10, 30]).export(mesh)
            original = module.sha256(mesh)
            spec = {
                "meshes": [{"path": "cube.stl", "units": "mm"}],
                "output_dir": "rendus",
                "resolution": [96, 96],
                "views": {
                    name: {"position": pos, "target": [0, 0, 0], "ortho_scale": 50}
                    for name, pos in {
                        "face": [0, -100, 0],
                        "profil": [100, 0, 0],
                        "dos": [0, 100, 0],
                        "dessus": [0, 0, 100],
                    }.items()
                },
            }
            source = root / "spec.json"
            source.write_text(json.dumps(spec))
            command = [
                blender,
                "--background",
                "--factory-startup",
                "--python",
                str(Path(module.__file__).resolve()),
                "--",
                str(source),
            ]
            run = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=90,
                env=dict(
                    os.environ,
                    HOME=str(root),
                    TMPDIR=str(root),
                    XDG_CACHE_HOME=str(root / "cache"),
                    BLENDER_USER_CONFIG=str(root / "blender-config"),
                ),
            )
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            manifest = json.loads((root / "rendus/manifest.json").read_text())
            self.assertEqual(manifest["spec_sha256"], module.sha256(source))
            self.assertEqual(original, module.sha256(mesh))
            widths = {}
            for name in spec["views"]:
                image = root / "rendus" / (name + ".png")
                self.assertEqual(manifest["views"][name]["sha256"], module.sha256(image))
                with Image.open(image) as im:
                    bounds = im.convert("L").point(lambda pixel: 255 if pixel > 127 else 0).getbbox()
                    self.assertIsNotNone(bounds)
                    widths[name] = bounds[2] - bounds[0]
            self.assertGreater(widths["face"], widths["profil"] * 1.7)
            self.assertEqual(widths["face"], widths["dos"])
            # Re-running fails before touching any existing PNG.
            before = module.sha256(root / "rendus/face.png")
            run = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=90,
                env=dict(
                    os.environ,
                    HOME=str(root),
                    TMPDIR=str(root),
                    XDG_CACHE_HOME=str(root / "cache"),
                    BLENDER_USER_CONFIG=str(root / "blender-config"),
                ),
            )
            self.assertIn("sortie existante refusée", run.stdout + run.stderr)
            self.assertEqual(before, module.sha256(root / "rendus/face.png"))


if __name__ == "__main__":
    unittest.main()
