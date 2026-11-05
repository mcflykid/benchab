"""Contrats de l'adaptateur, sans installer ni démarrer de service."""

import io
import json
import math
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from benchab.conception import cad_engine as cad


class CadEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "source.step"
        self.source.write_bytes(b"original")
        self.output = self.root / "sortie"
        self.api = types.ModuleType("cadgen.engine.api")
        package = types.ModuleType("cadgen")
        engine = types.ModuleType("cadgen.engine")
        engine.api = self.api
        self.modules = patch.dict(
            sys.modules, {"cadgen": package, "cadgen.engine": engine, "cadgen.engine.api": self.api}
        )
        self.modules.start()
        self.addCleanup(self.modules.stop)
        versions = patch(
            "importlib.metadata.version", side_effect=lambda name: {"cadgen": "0.8.4", "build123d": "0.13.0"}[name]
        )
        versions.start()
        self.addCleanup(versions.stop)

    def args(self, *extra):
        return cad.parser().parse_args(
            ["export", str(self.source), "--output-dir", str(self.output), "--formats", *extra]
        )

    def test_export_contract_and_source_preserved(self):
        def export(source, *paths, **kwargs):
            self.assertEqual(kwargs, {"daemon": False, "progress": False})
            for path in paths:
                path.write_bytes(b"exported")

        self.api.export = export
        result = cad.run(self.args("stl", "glb"))
        self.assertEqual(self.source.read_bytes(), b"original")
        self.assertEqual(len(result["files"]), 2)
        self.assertEqual(
            json.loads((self.output / "manifest.json").read_text())["source_sha256"], cad.empreinte(self.source)
        )

    def test_missing_export_is_failure_without_success_manifest(self):
        self.api.export = lambda *args, **kwargs: None
        with self.assertRaisesRegex(ValueError, "absent ou vide"):
            cad.run(self.args("stl"))
        self.assertTrue((self.output / "ECHEC.txt").exists())
        self.assertFalse((self.output / "manifest.json").exists())

    def test_refused_dxf_export_has_no_success_manifest(self):
        self.api.export = lambda source, *paths, **kwargs: paths[0].write_bytes(b"dxf")
        with patch.object(cad, "inspect_dxf", side_effect=ValueError("DXF refusé : profil ouvert")):
            with self.assertRaisesRegex(ValueError, "DXF refusé"):
                cad.run(self.args("dxf"))
        self.assertFalse((self.output / "manifest.json").exists())
        self.assertTrue((self.output / "ECHEC.txt").is_file())

    def test_source_changed_during_export_refuses_success_manifest(self):
        def export(source, *paths, **kwargs):
            # Une écriture concurrente de l'utilisateur ou d'un autre traitement.
            source.write_bytes(b"new source revision")
            paths[0].write_bytes(b"new source exported")

        self.api.export = export
        with self.assertRaisesRegex(ValueError, "Source modifiée"):
            cad.run(self.args("stl"))
        self.assertFalse((self.output / "manifest.json").exists())
        self.assertTrue((self.output / "ECHEC.txt").is_file())
        self.assertEqual(self.source.read_bytes(), b"new source revision")

    def test_existing_destination_is_never_overwritten(self):
        self.output.mkdir()
        marker = self.output / "personnel"
        marker.write_text("preserver")
        with self.assertRaises(FileExistsError):
            cad.run(self.args("stl"))
        self.assertEqual(marker.read_text(), "preserver")

    def test_symlink_destination_refused(self):
        self.output.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(FileExistsError):
            cad.run(self.args("stl"))

    def test_duplicate_formats_refused_before_output(self):
        with self.assertRaisesRegex(ValueError, "dupliqués"):
            cad.run(self.args("stl", "stl"))
        self.assertFalse(self.output.exists())

    def test_invalid_step_reimport_refused(self):
        self.api.export = lambda source, *paths, **kwargs: paths[0].write_bytes(b"bad step")
        with patch.object(cad, "inspect_step", return_value={"valid": False, "solids": 0}):
            with self.assertRaisesRegex(ValueError, "invalide"):
                cad.run(self.args("step"))
        self.assertFalse((self.output / "manifest.json").exists())

    def test_snapshot_missing_is_failure(self):
        self.api.snapshot = lambda *args, **kwargs: None
        args = cad.parser().parse_args(["snapshot", str(self.source), "--output-dir", str(self.output)])
        with self.assertRaisesRegex(ValueError, "Rendu absent"):
            cad.run(args)

    def test_build_requires_real_export(self):
        self.source = self.root / "modele.py"
        self.source.write_text("original")

        class Report:
            ok = True

            def __len__(self):
                return 1

        self.api.build_script = lambda *args, **kwargs: Report()
        args = cad.parser().parse_args(["build", str(self.source), "--output-dir", str(self.output)])
        with patch.object(cad, "asdict", return_value={}):
            with self.assertRaisesRegex(ValueError, "sans export"):
                cad.run(args)
        self.assertFalse((self.output / "manifest.json").exists())
        self.assertEqual(self.source.read_text(), "original")

    def test_build_verifies_and_reimports_step(self):
        self.source = self.root / "modele.py"
        self.source.write_text("original")

        class Report:
            ok = True

            def __len__(self):
                return 1

        def build(script, functions, **kwargs):
            self.assertEqual(kwargs, {"verify": True, "daemon": False, "progress": False})
            (script.parent / "piece.step").write_bytes(b"step")
            return Report()

        self.api.build_script = build
        args = cad.parser().parse_args(["build", str(self.source), "--output-dir", str(self.output)])
        with (
            patch.object(cad, "asdict", return_value={}),
            patch.object(cad, "inspect_step", return_value={"valid": True, "solids": 1}) as inspect,
        ):
            result = cad.run(args)
        inspect.assert_called_once_with(self.output / "piece.step")
        self.assertEqual(result["controls"]["piece.step"]["solids"], 1)
        self.assertEqual(self.source.read_text(), "original")

    def test_build_checks_declared_dxf(self):
        self.source = self.root / "modele.py"
        self.source.write_text("original")

        class Report:
            ok = True

            def __len__(self):
                return 1

        def build(script, *args, **kwargs):
            (script.parent / "piece.dxf").write_bytes(b"dxf")
            return Report()

        self.api.build_script = build
        args = cad.parser().parse_args(["build", str(self.source), "--output-dir", str(self.output)])
        with (
            patch.object(cad, "asdict", return_value={}),
            patch.object(cad, "inspect_dxf", side_effect=ValueError("DXF refusé")) as check,
        ):
            with self.assertRaisesRegex(ValueError, "DXF refusé"):
                cad.run(args)
        check.assert_called_once_with(self.output / "piece.dxf")
        self.assertFalse((self.output / "manifest.json").exists())

    def test_runtime_mismatch_refused(self):
        with patch("importlib.metadata.version", return_value="0.0"):
            with self.assertRaisesRegex(ValueError, "Runtime incompatible"):
                cad.run(self.args("stl"))
        self.assertFalse(self.output.exists())

    def test_cli_stdout_is_json_and_engine_logs_remain_on_stderr(self):
        def export(source, *paths, **kwargs):
            print("wrote modele.stl")
            paths[0].write_bytes(b"exported")

        self.api.export = export
        stdout, stderr = io.StringIO(), io.StringIO()
        argv = ["cad_engine.py", "export", str(self.source), "--output-dir", str(self.output), "--formats", "stl"]
        with patch.object(sys, "argv", argv), patch.object(sys, "stdout", stdout), patch.object(sys, "stderr", stderr):
            self.assertEqual(cad.main(), 0)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["operation"], "export")
        self.assertIn("wrote modele.stl", stderr.getvalue())


class DxfEllipseTests(unittest.TestCase):
    """Régression réelle ezdxf, exécutée dans le runtime CAO existant."""

    def setUp(self):
        try:
            import ezdxf
            from cadgen.engine.export import dxf
        except ImportError:
            self.skipTest("Runtime cadgen/ezdxf requis pour les fixtures DXF réelles.")
        self.ezdxf, self.upstream = ezdxf, dxf
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "ellipse.dxf"
        self.doc = ezdxf.new()
        self.doc.units = 4

    def ellipse(self, layer="CUT", end=math.pi / 2):
        return self.doc.modelspace().add_ellipse(
            (0, 0), (10, 0), 0.5, start_param=0, end_param=end, dxfattribs={"layer": layer}
        )

    def inspect(self):
        self.doc.saveas(self.path)
        return cad.inspect_dxf(self.path)

    def test_open_cut_ellipse_refused_despite_upstream_acceptance(self):
        self.ellipse()
        self.assertFalse([f for f in self.upstream.validate_drawing_document(self.doc) if f.severity == "error"])
        with self.assertRaisesRegex(ValueError, "open_cut_profile"):
            self.inspect()

    def test_whole_ellipse_accepted(self):
        self.ellipse(end=math.tau)
        self.assertEqual(self.inspect()["partial_cut_ellipses"], 0)

    def test_ellipse_arc_closed_by_lines_accepted(self):
        arc = self.ellipse()
        self.doc.modelspace().add_line(arc.end_point, (0, 0), dxfattribs={"layer": "CUT"})
        self.doc.modelspace().add_line((0, 0), arc.start_point, dxfattribs={"layer": "CUT"})
        self.assertEqual(self.inspect()["partial_cut_ellipses"], 1)

    def test_open_engrave_ellipse_accepted(self):
        self.ellipse(layer="ENGRAVE")
        self.assertEqual(self.inspect()["partial_cut_ellipses"], 0)

    def test_open_polyline_stays_blocking_beside_closed_ellipse_chain(self):
        arc = self.ellipse()
        self.doc.modelspace().add_line(arc.end_point, arc.start_point, dxfattribs={"layer": "CUT"})
        self.doc.modelspace().add_lwpolyline([(20, 0), (21, 1), (22, 0)], dxfattribs={"layer": "CUT"})
        with self.assertRaisesRegex(ValueError, "open LWPOLYLINE"):
            self.inspect()

    def test_unrelated_open_chain_stays_blocking(self):
        arc = self.ellipse()
        self.doc.modelspace().add_line(arc.end_point, arc.start_point, dxfattribs={"layer": "CUT"})
        self.doc.modelspace().add_line((20, 0), (21, 1), dxfattribs={"layer": "CUT"})
        with self.assertRaisesRegex(ValueError, "open_cut_profile"):
            self.inspect()


if __name__ == "__main__":
    unittest.main()
