"""Un rapport de contrôle ne remplace ni ses entrées ni un résultat existant."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from fixtures_communes import DECL, creer_3mf_test


class PublicationEnvoi(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = creer_3mf_test(self.root / "source.3mf")
        self.declaration = self.root / "declaration.json"
        self.declaration.write_text(json.dumps(DECL))

    def runcli(self, *args):
        return subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "benchab.controles.controle_envoi",
                str(self.source),
                "--declaration",
                str(self.declaration),
                *map(str, args),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_entrees_et_rapport_existant_preserves(self):
        report = self.root / "rapport.json"
        report.write_text("résultat précédent")
        for target in (self.source, self.declaration, report):
            with self.subTest(target=target.name):
                before = target.read_bytes()
                result = self.runcli("--json", target)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(target.read_bytes(), before)
                self.assertIn("Rapport refusé", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_rapport_neuf_relu_et_entrees_intactes(self):
        before = [p.read_bytes() for p in (self.source, self.declaration)]
        report = self.root / "rapport.json"
        result = self.runcli("--json", report)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(report.read_text())["statut"], "WARN")
        self.assertEqual(before, [p.read_bytes() for p in (self.source, self.declaration)])
        second = self.runcli("--json", report)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(json.loads(report.read_text())["_rapport"]["outil"], "controle_envoi")

    def test_rapport_protege_reste_intact(self):
        import hashlib

        report = self.root / "rapport.json"
        self.assertEqual(self.runcli("--json", report).returncode, 0)
        before = report.read_bytes()
        (self.root / "LIVRABLES.json").write_text(
            json.dumps({"fichiers": [{"chemin": report.name, "sha256": hashlib.sha256(before).hexdigest()}]})
        )
        result = self.runcli("--json", report)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(report.read_bytes(), before)
        self.assertIn("protégé", result.stderr)
        # Une nouvelle preuve ne s'ajoute pas non plus dans un Pnn figé.
        preparation = self.root / "preparations/P01"
        preparation.mkdir(parents=True)
        result = self.runcli("--json", preparation / "nouveau.json")
        self.assertEqual(result.returncode, 2)
        self.assertFalse((preparation / "nouveau.json").exists())

    def test_rapport_ou_source_modifie_entre_controle_et_publication_refuse(self):
        from benchab.controles.rapports import RapportCourant

        report = self.root / "rapport.json"
        self.assertEqual(self.runcli("--json", report).returncode, 0)
        preparation = RapportCourant(report, [self.source, self.declaration], "controle_envoi")
        report.write_text(json.dumps({"modification": "autre exécutant"}))
        before = report.read_bytes()
        with self.assertRaises(ValueError):
            preparation.publier({"statut": "PASS"})
        self.assertEqual(report.read_bytes(), before)
        fresh = self.root / "neuf.json"
        preparation = RapportCourant(fresh, [self.source, self.declaration], "controle_envoi")
        self.declaration.write_text("{}")
        with self.assertRaises(ValueError):
            preparation.publier({"statut": "PASS"})
        self.assertFalse(fresh.exists())

    def test_rapport_symbolique_et_option_sans_valeur_refuses(self):
        link = self.root / "alias.json"
        link.symlink_to(self.source)
        before = self.source.read_bytes()
        self.assertEqual(self.runcli("--json", link).returncode, 2)
        self.assertEqual(self.source.read_bytes(), before)
        result = self.runcli("--json")
        self.assertEqual(result.returncode, 2)
        self.assertIn("Option sans valeur", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_controle_envoi_rejoue_deux_fois_avec_geometrie(self):
        candidats = self.root / "candidats"
        candidats.mkdir()
        (candidats / "tranche.3mf").write_bytes(self.source.read_bytes())
        (self.root / "DECLARATION-ENVOI.json").write_bytes(self.declaration.read_bytes())
        preuves = self.root / "preuves"
        preuves.mkdir()
        (preuves / "controles-pieces.json").write_text(
            json.dumps({"fichiers": {"Piece.stl": [{"controle": "maillage fermé", "statut": "PASS"}]}})
        )
        # Seule la recette envoi est en revue ici ; son prérequis références
        # est explicitement déjà satisfait par la fixture, sans CAO ni slicer.
        command = [
            sys.executable,
            "-B",
            "-m",
            "benchab.controles.controle_envoi",
            str(candidats / "tranche.3mf"),
            "--declaration",
            str(self.root / "DECLARATION-ENVOI.json"),
            "--geometrie",
            str(preuves / "controles-pieces.json"),
            "--json",
            str(preuves / "controle-envoi.json"),
        ]
        before = (candidats / "tranche.3mf").read_bytes()
        for _ in range(2):
            result = subprocess.run(command, cwd=self.root, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((candidats / "tranche.3mf").read_bytes(), before)
        self.assertEqual(
            json.loads((preuves / "controle-envoi.json").read_text())["_rapport"]["outil"], "controle_envoi"
        )


if __name__ == "__main__":
    unittest.main()
