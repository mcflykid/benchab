"""Un délai déclaré sur une étape produit un échec lisible et laisse sa trace."""

import hashlib
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from benchab.qualite import tout


class ProcessusQualite(unittest.TestCase):
    """Vérifie délais en secondes, sorties et reprise du pipeline sur des fixtures privées."""
    def test_delai_propre_a_l_etape(self):
        with tempfile.TemporaryDirectory() as dossier:
            base = Path(dossier)
            log = base / "etape.log"
            code = tout.executer_commande(
                [sys.executable, "-c", "import time;time.sleep(2)"], base, log, {"id": "etape", "delai_secondes": 0.1}
            )
            self.assertEqual(code, 124)
            self.assertIn("Délai de l’étape dépassé", log.read_text())

    def test_delai_invalide_refuse(self):
        with tempfile.TemporaryDirectory() as dossier:
            base = Path(dossier)
            with self.assertRaisesRegex(ValueError, "delai_secondes"):
                tout.executer_commande(
                    [sys.executable, "-c", "pass"], base, base / "etape.log", {"id": "etape", "delai_secondes": True}
                )

    def test_pipeline_refuse_configuration_publique_et_identifiant_traversant(self):
        """La configuration reste privée/temporaire et les identifiants ne peuvent fabriquer un chemin."""
        publique = Path(__file__).parents[1] / "pyproject.toml"
        with self.assertRaisesRegex(ValueError, "atelier privé ou un temporaire"):
            tout.run(publique)

        with tempfile.TemporaryDirectory() as dossier:
            base = Path(dossier)
            config = base / "qualite.json"
            config.write_text(
                json.dumps(
                    {
                        "manifeste": "assembly.json",
                        "pipeline": [{"id": "../hors", "commande": [sys.executable, "-c", "pass"]}],
                    }
                )
            )
            (base / "assembly.json").write_text(json.dumps({"models": {}, "parts": [], "options": {}}))
            with self.assertRaisesRegex(ValueError, "Identifiant d’étape invalide"):
                tout.run(config)
            self.assertFalse((base / "preuves/qualite").exists())

    def test_pipeline_refuse_dossier_preuves_lie(self):
        """Un dossier preuves relié n'est pas suivi pour écrire l'état du pipeline."""
        with tempfile.TemporaryDirectory() as dossier:
            base = Path(dossier)
            (base / "preuves").mkdir()
            extérieur = base / "extérieur"
            extérieur.mkdir()
            try:
                os.symlink(extérieur, base / "preuves/qualite")
            except (OSError, NotImplementedError):
                self.skipTest("Création de lien symbolique indisponible")
            (base / "qualite.json").write_text(
                json.dumps(
                    {
                        "manifeste": "assembly.json",
                        "pipeline": [{"id": "etape", "commande": [sys.executable, "-c", "pass"]}],
                    }
                )
            )
            (base / "assembly.json").write_text(json.dumps({"models": {}, "parts": [], "options": {}}))
            with self.assertRaisesRegex(ValueError, "Lien symbolique refusé"):
                tout.run(base / "qualite.json")
            self.assertEqual(list(extérieur.iterdir()), [])

    def test_pipeline_incremental_rejoue_echec_partiel_et_invalide_entrees(self):
        """Le code 7 avec preuve partielle n'est pas caché ; seules les preuves valides le sont."""
        with tempfile.TemporaryDirectory() as dossier:
            base = Path(dossier)
            (base / "preuves").mkdir()
            (base / "candidats").mkdir()
            (base / "stl").mkdir()
            manifest = base / "assembly.json"
            manifest.write_text(json.dumps({"models": {}, "parts": [], "options": {}}))
            manifest_sha = hashlib.sha256(manifest.read_bytes()).hexdigest()
            script = base / "writer.py"
            script.write_text(
                """import hashlib, json, pathlib, sys
root = pathlib.Path.cwd()
(root / 'runs.txt').open('a').write('run\\n')
(root / 'preuves' / 'partiel.json').write_text(json.dumps({
    'assembly_manifest_sha256': hashlib.sha256((root / 'assembly.json').read_bytes()).hexdigest(),
    'status': 'PASS',
}))
print('sortie complète de l’étape')
sys.exit(7 if (root / 'mode.txt').read_text().strip() == 'fail' else 0)
"""
            )
            (base / "mode.txt").write_text("fail")
            (base / "entree.txt").write_text("version 1")
            stage = {
                "id": "preuve",
                "commande": [sys.executable, "writer.py"],
                "entrees": ["mode.txt", "entree.txt"],
                "preuves": ["preuves/partiel.json"],
                "cache": {"dependances": [], "paquets": [], "environnement": [], "configuration": []},
            }
            cfg = {
                "manifeste": "assembly.json",
                "stl_assemblage": "stl",
                "stl_impression": "stl",
                "preuves_obligatoires": [{"fichier": "preuves/partiel.json", "statuts": ["PASS"]}],
                "pipeline": [stage],
            }
            config = base / "qualite.json"
            config.write_text(json.dumps(cfg))

            self.assertEqual(tout.run(config), 1)
            failed = json.loads((base / "preuves/qualite/dernier-etat.json").read_text())
            self.assertEqual(failed["steps"][0]["code"], 7)
            self.assertEqual(failed["steps"][0]["statut"], "FAIL")
            self.assertNotIn("cache", failed["steps"][0])
            self.assertIn("sortie complète de l’étape", (base / "preuves/qualite/preuve.log").read_text())
            self.assertEqual((base / "runs.txt").read_text().count("run"), 1)

            (base / "mode.txt").write_text("ok")
            self.assertEqual(tout.run(config, incremental=True), 0)
            passed = json.loads((base / "preuves/qualite/dernier-etat.json").read_text())
            self.assertEqual(passed["steps"][0]["statut"], "PASS")
            self.assertEqual(len(passed["steps"][0]["preuves_sha256"]), 1)
            preuve_sha = hashlib.sha256((base / "preuves/partiel.json").read_bytes()).hexdigest()
            self.assertEqual(passed["steps"][0]["preuves_sha256"]["preuves/partiel.json"], preuve_sha)
            self.assertEqual((base / "runs.txt").read_text().count("run"), 2)

            self.assertEqual(tout.run(config, incremental=True), 0)
            cached = json.loads((base / "preuves/qualite/dernier-etat.json").read_text())
            self.assertTrue(cached["steps"][0]["cache"])
            self.assertEqual((base / "runs.txt").read_text().count("run"), 2)

            (base / "entree.txt").write_text("version 2")
            self.assertEqual(tout.run(config, incremental=True), 0)
            invalidated = json.loads((base / "preuves/qualite/dernier-etat.json").read_text())
            self.assertNotIn("cache", invalidated["steps"][0])
            self.assertEqual((base / "runs.txt").read_text().count("run"), 3)
            self.assertEqual(manifest_sha, hashlib.sha256(manifest.read_bytes()).hexdigest())

    def test_pipeline_timeout_arrete_aussi_le_processus_descendant(self):
        """Un délai de pipeline tue le groupe entier avant qu'un descendant écrive sa sentinelle."""
        with tempfile.TemporaryDirectory() as dossier:
            base = Path(dossier)
            (base / "preuves").mkdir()
            (base / "candidats").mkdir()
            (base / "stl").mkdir()
            (base / "assembly.json").write_text(json.dumps({"models": {}, "parts": [], "options": {}}))
            sentinelle = base / "descendant-a-survecu.txt"
            script = (
                "import pathlib, subprocess, sys, time; "
                "cible=sys.argv[1]; "
                "subprocess.Popen([sys.executable, '-c', "
                "'import pathlib,time;time.sleep(0.8); "
                "pathlib.Path(\\\"' + cible + '\\\").write_text(\\\"vivant\\\")']); "
                "print('parent prêt', flush=True); time.sleep(5)"
            )
            cfg = {
                "manifeste": "assembly.json",
                "stl_assemblage": "stl",
                "stl_impression": "stl",
                "pipeline": [
                    {
                        "id": "timeout",
                        "commande": [sys.executable, "-c", script, str(sentinelle)],
                        "delai_secondes": 0.35,
                    }
                ],
            }
            config = base / "qualite.json"
            config.write_text(json.dumps(cfg))
            self.assertEqual(tout.run(config), 1)
            record = json.loads((base / "preuves/qualite/dernier-etat.json").read_text())
            self.assertEqual(record["steps"][0]["code"], 124)
            journal = (base / "preuves/qualite/timeout.log").read_text()
            self.assertIn("Délai de l’étape dépassé", journal)
            time.sleep(0.9)
            self.assertFalse(sentinelle.exists())


if __name__ == "__main__":
    unittest.main()
