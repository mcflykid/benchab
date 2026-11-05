"""Régressions autonomes : uniquement fixtures temporaires."""

import json
import tempfile
import unittest
from pathlib import Path

from benchab.qualite import qualite as Q
from benchab.qualite import tout as T
from benchab.qualite import validation as V


class Qualite(unittest.TestCase):
    def test_b09(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / "a").mkdir()
            (p / "a/x.stl").write_text("mesh")
            (p / "m.json").write_text(json.dumps({"models": {"x": {}}, "parts": []}))
            items = V.verifier(p, {"manifeste": "m.json", "stl_assemblage": "a", "stl_impression": "a"}, False)
            self.assertFalse(any(i["statut"] == "PASS" for i in items if i["controle"].startswith("integrite")))

    def test_c03(self):
        for s in ["WARN", "NON VÉRIFIÉ", "INCONNU", "NON APPLICABLE"]:
            with self.subTest(s=s):
                self.assertTrue(V.mauvais({"statut": "PASS", "controles": [{"statut": s}]}))

    def test_b11(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / "input").write_text("x")
            (p / "control.py").write_text("old")
            stage = {"entrees": ["input"], "commande": ["{python}", "control.py"]}
            a = T.empreinte(p, stage)
            (p / "control.py").write_text("new")
            self.assertNotEqual(a, T.empreinte(p, stage))

    def test_b27(self):
        with tempfile.TemporaryDirectory() as d:
            r = Q.Rapport("n", Path(d) / "qualite.json")
            r.add("impossible", Q.NV)
            self.assertEqual(r.ecrire(), Q.NV)

    def test_b10(self):
        import trimesh

        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / "a").mkdir()
            (p / "i").mkdir()
            m = trimesh.creation.box()
            m.export(p / "a/x.stl")
            m.export(p / "i/x.stl")
            info = {"stl_sha256": Q.sha(p / "a/x.stl"), "print_sha256": "0" * 64}
            (p / "m.json").write_text(json.dumps({"models": {"x": info}, "parts": []}))
            c = p / "qualite.json"
            c.write_text(json.dumps({"manifeste": "m.json", "stl_assemblage": "a", "stl_impression": "i"}))
            self.assertEqual(Q.niveau1(c), Q.KO)


class Pipeline(unittest.TestCase):
    def test_aggregation(self):
        self.assertEqual(V.agregation_statut({"statut": "PASS", "x": [{"status": "WARN"}]}), "WARNING")
        self.assertFalse(V.mauvais({"statut": "PASS", "x": {"statut": "WARN"}}, ["PASS", "WARNING"]))
        self.assertEqual(V.agregation_statut({"statut": "PASS", "x": {"pass": False}}), "FAIL")
        self.assertEqual(V.agregation_statut({}), "NON VÉRIFIÉ")

    def test_incrementiel_print_hash(self):
        import contextlib
        import io

        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / "old.json").write_text(json.dumps({"models": {"x": {"stl_sha256": "a", "print_sha256": "b"}}}))
            (p / "m.json").write_text(json.dumps({"models": {"x": {"stl_sha256": "a", "print_sha256": "c"}}}))
            (p / "c.json").write_text(json.dumps({"manifeste": "m.json"}))
            with contextlib.redirect_stdout(io.StringIO()):
                changed, _ = Q.incrementiel(p / "c.json", p / "old.json")
            self.assertEqual(changed, ["x"])

    def test_niveau3_absent_measurements(self):
        import zipfile

        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            with zipfile.ZipFile(p / "s.3mf", "w") as z:
                z.writestr(
                    "Metadata/slice_info.config",
                    '<config><plate><metadata key="index" value="1"/>'
                    '<metadata key="prediction" value="60"/></plate></config>',
                )
                z.writestr(
                    "Metadata/model_settings.config",
                    '<config><plate><metadata key="plater_name" value="P"/></plate></config>',
                )
                z.writestr("Metadata/project_settings.config", "{}")
            c = p / "c.json"
            c.write_text(
                json.dumps({"tranche_3mf": "s.3mf", "placement": "missing.json", "plaques_3mf": "missing.3mf"})
            )
            Q.niveau3(c)
            items = json.loads((p / "preuves/qualite/niveau3.json").read_text())["controles"]
            self.assertTrue(
                all(
                    i["statut"] == "NON VÉRIFIÉ"
                    for i in items
                    if "accroche" in i["controle"] or "remplissage par défaut" in i["controle"]
                )
            )

    def test_conformite_false(self):
        for key in ("conforme", "tout_conforme", "tous_conformes", "ok"):
            with self.subTest(key=key):
                self.assertEqual(V.agregation_statut({"statut": "PASS", "detail": {key: False}}), "FAIL")
        self.assertEqual(V.agregation_statut({"conforme": True}), "NON VÉRIFIÉ")

    def test_cache_dependencies(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / "x").write_text("x")
            stage = {"entrees": ["x"], "commande": ["{python}", "-c", "pass"]}
            a = T.empreinte(p, stage, {"v": 1})
            self.assertNotEqual(a, T.empreinte(p, stage, {"v": 2}))
            self.assertNotEqual(a, T.empreinte(p, {**stage, "commande": ["{python}", "-c", "print(1)"]}, {"v": 1}))

    def test_pipeline_warning_and_cache(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / "a").mkdir()
            (p / "a/x.stl").write_text("data")
            h = V.sha(p / "a/x.stl")
            m = {"models": {"x": {"stl_sha256": h, "print_sha256": h}}, "parts": []}
            (p / "m.json").write_text(json.dumps(m))
            cmd = (
                "import json,pathlib; p=pathlib.Path('count'); "
                "p.write_text(str(int(p.read_text())+1) if p.exists() else '1');"
                "pathlib.Path('proof.json').write_text(json.dumps({'statut':'PASS','assembly_manifest_sha256':'"
                + V.sha(p / "m.json")
                + "'}))"
            )
            cfg = {
                "manifeste": "m.json",
                "stl_assemblage": "a",
                "stl_impression": "a",
                "preuves_obligatoires": ["proof.json"],
                "pipeline": [
                    {"id": "check", "entrees": ["a/*"], "preuves": ["proof.json"], "commande": ["{python}", "-c", cmd]}
                ],
            }
            c = p / "qualite.json"
            c.write_text(json.dumps(cfg))
            self.assertEqual(T.run(c), 0)
            self.assertEqual(T.run(c, True), 0)
            self.assertEqual((p / "count").read_text(), "1")
            cfg["pipeline"][0]["commande"][-1] = cmd.replace("'statut':'PASS'", "'statut':'WARNING'")
            cfg["preuves_obligatoires"] = [{"fichier": "proof.json", "statuts": ["PASS", "WARNING"]}]
            c.write_text(json.dumps(cfg))
            self.assertEqual(T.run(c, True), 0)
            self.assertEqual(json.loads((p / "preuves/qualite/dernier-etat.json").read_text())["statut"], "WARNING")
            cfg["pipeline"][0]["commande"][-1] = cmd.replace("'statut':'PASS'", "'statut':'NON VÉRIFIÉ'")
            c.write_text(json.dumps(cfg))
            self.assertEqual(T.run(c), 1)


if __name__ == "__main__":
    unittest.main()
