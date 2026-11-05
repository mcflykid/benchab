import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import trimesh

from benchab.conception import variantes as variants


class VariantTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name, dims in [("base", [20, 10, 5]), ("changed", [30, 10, 5]), ("fixed", [4, 5, 6])]:
            trimesh.creation.box(extents=dims).export(self.root / (name + ".stl"))
        self.data = {
            "base": "original",
            "variantes": [
                {"nom": "original", "modeles": [self.part("corps", "base.stl"), self.part("cle", "fixed.stl")]},
                {"nom": "essai", "modeles": [self.part("corps", "changed.stl"), self.part("cle", "fixed.stl")]},
            ],
        }
        self.path = self.root / "variantes.json"

    def part(self, name, path, units="mm"):
        return {"piece": name, "chemin": path, "unites": units}

    def run_report(self):
        self.path.write_text(json.dumps(self.data))
        return variants.comparer(self.path)

    def make_3mf(self, filename, seconds=120, grams=15):
        path = self.root / filename
        with ZipFile(path, "w") as archive:
            archive.writestr(
                "Metadata/slice_info.config",
                '<config><plate><metadata key="index" value="1"/>'
                f'<metadata key="prediction" value="{seconds}"/>'
                f'<metadata key="weight" value="{grams}"/></plate></config>',
            )
        return {"source": filename, "sha256": variants.importer().empreinte(path)}

    def test_change_one_named_part_preserves_other(self):
        before = {p.name: p.read_bytes() for p in self.root.glob("*.stl")}
        report = self.run_report()
        changes = report["variantes"]["essai"]["changements"]
        self.assertEqual(changes["corps"]["etat"], "modifié")
        self.assertEqual(changes["cle"]["etat"], "inchangé")
        self.assertEqual(changes["corps"]["ecart_dimensions_mm"], [10, 0, 0])
        self.assertEqual(report["classement"], "NON EFFECTUÉ")
        self.assertEqual(report["validation_physique"], variants.NON_VERIFIE)
        self.assertEqual(report["statut"], "WARN")
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.glob("*.stl")})

    def test_protected_part_modified_refuses(self):
        self.data["protegees"] = [{"piece": "corps", "sha256": variants.importer().empreinte(self.root / "base.stl")}]
        report = self.run_report()
        self.assertEqual(report["variantes"]["original"]["protections"]["statut"], "PASS")
        self.assertEqual(report["variantes"]["essai"]["protections"]["statut"], "FAIL")
        self.assertEqual(report["statut"], "FAIL")

    def test_protected_part_removed_refuses(self):
        self.data["protegees"] = [{"piece": "cle", "sha256": variants.importer().empreinte(self.root / "fixed.stl")}]
        self.data["variantes"][1]["modeles"].pop()
        report = self.run_report()
        self.assertEqual(report["variantes"]["essai"]["changements"]["cle"]["etat"], "retiré")
        self.assertEqual(report["statut"], "FAIL")

    def test_same_hash_wrong_units_is_modification(self):
        self.data["variantes"][1]["modeles"][1]["unites"] = "cm"
        self.data["protegees"] = [{"piece": "cle", "sha256": variants.importer().empreinte(self.root / "fixed.stl")}]
        report = self.run_report()
        self.assertEqual(report["variantes"]["essai"]["changements"]["cle"]["etat"], "modifié")
        self.assertEqual(report["statut"], "FAIL")

    def test_missing_proof_never_validates_declared_metrics(self):
        self.data["variantes"][1]["mesures_fabrication"] = {"temps_s": 0, "masse_g": 0}
        report = self.run_report()
        metric = report["variantes"]["essai"]["fabrication"]
        self.assertEqual(metric["statut"], variants.NON_VERIFIE)
        self.assertNotIn("temps_s", metric)
        self.assertNotIn("ecarts_fabrication", report["variantes"]["essai"])
        self.assertEqual(report["statut"], "WARN")

    def test_real_slice_summary_and_stale_hash(self):
        self.data["variantes"][0]["mesures_fabrication"] = self.make_3mf("a.3mf")
        self.data["variantes"][1]["mesures_fabrication"] = self.make_3mf("b.3mf", seconds=90, grams=12)
        report = self.run_report()
        self.assertEqual(report["variantes"]["essai"]["fabrication"]["temps_s"], 90)
        self.assertEqual(report["variantes"]["essai"]["ecarts_fabrication"], {"temps_s": -30, "masse_g": -3})
        self.assertEqual(report["classement"], "NON EFFECTUÉ")
        with (self.root / "b.3mf").open("ab") as f:
            f.write(b"changed")
        self.assertEqual(self.run_report()["statut"], "FAIL")

    def test_summary_claim_mismatch_fails(self):
        self.data["variantes"][1]["mesures_fabrication"] = self.make_3mf("a.3mf") | {"temps_s": 5}
        self.assertEqual(self.run_report()["statut"], "FAIL")

    def test_glb_instances_are_transformed(self):
        scene = trimesh.Scene()
        box = trimesh.creation.box(extents=[0.01, 0.01, 0.01])
        scene.add_geometry(box, node_name="a")
        transform = np.eye(4)
        transform[0, 3] = 0.02
        scene.add_geometry(box.copy(), node_name="b", transform=transform)
        scene.export(self.root / "scene.glb")
        self.data["variantes"][1]["modeles"] = [self.part("scene", "scene.glb", "m")]
        report = self.run_report()
        measure = report["variantes"]["essai"]["modeles"]["scene"]["mesures"]
        self.assertAlmostEqual(measure["dimensions_mm"][0], 30, places=4)
        self.assertEqual(measure["corps_connectes"], 2)
        self.assertEqual(report["variantes"]["essai"]["changements"]["scene"]["etat"], "ajouté")

    def test_units_and_duplicate_names_are_rejected(self):
        self.data["variantes"][1]["modeles"][0].pop("unites")
        with self.assertRaises(ValueError):
            self.run_report()
        self.data["variantes"][1]["modeles"][0]["unites"] = "mm"
        self.data["variantes"][1]["modeles"][1]["piece"] = "corps"
        with self.assertRaises(ValueError):
            self.run_report()

    def test_open_geometry_remains_warning(self):
        mesh = trimesh.creation.box()
        mesh.update_faces(np.arange(len(mesh.faces) - 1))
        mesh.export(self.root / "changed.stl")
        report = self.run_report()
        self.assertFalse(report["variantes"]["essai"]["modeles"]["corps"]["mesures"]["ferme_topologiquement"])
        self.assertEqual(report["variantes"]["essai"]["modeles"]["corps"]["statut"], "WARN")

    def test_cli_outputs_and_no_overwrite(self):
        self.path.write_text(json.dumps(self.data))
        command = [
            sys.executable,
            str(Path(variants.__file__).resolve()),
            str(self.path),
            "--sortie",
            str(self.root / "report"),
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        path = self.root / "report/rapport.json"
        original = path.read_bytes()
        self.assertIn("corps", (self.root / "report/comparaison.md").read_text())
        self.assertEqual(subprocess.run(command, capture_output=True).returncode, 2)
        self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
