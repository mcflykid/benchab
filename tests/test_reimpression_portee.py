import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from benchab.controles.reimpression import verifier


class ReimpressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.r = Path(self.tmp.name)

    def verrou(self):
        (self.r / "src").mkdir()
        p = self.r / "src/model.py"
        p.write_text("référence")
        j = {
            "schema": 1,
            "fichiers": {"src/model.py": hashlib.sha256(p.read_bytes()).hexdigest()},
            "repertoires_figes": ["src"],
        }
        (self.r / "VERROU-REIMPRESSION.json").write_text(json.dumps(j))
        return p

    def test_identite_et_modification(self):
        p = self.verrou()
        self.assertEqual(verifier(self.r)["statut"], "PASS")
        p.write_text("autre")
        self.assertEqual(verifier(self.r)["statut"], "FAIL")

    def test_ajout_et_absence(self):
        p = self.verrou()
        (self.r / "src/autre.py").write_text("nouveau")
        self.assertEqual(verifier(self.r)["statut"], "FAIL")
        p.unlink()
        self.assertTrue(any("absent" in e for e in verifier(self.r)["erreurs"]))

    def test_absence_manifeste(self):
        self.assertEqual(verifier(self.r)["statut"], "NON APPLICABLE")

    def test_sortie_de_perimetre_refusee(self):
        self.verrou()
        p = self.r / "VERROU-REIMPRESSION.json"
        j = json.loads(p.read_text())
        j["fichiers"] = {"../secret": "0" * 64}
        p.write_text(json.dumps(j))
        self.assertEqual(verifier(self.r)["statut"], "FAIL")

    def test_cache_python_ignore(self):
        self.verrou()
        (self.r / "src/__pycache__").mkdir()
        (self.r / "src/__pycache__/x.pyc").write_bytes(b"x")
        self.assertEqual(verifier(self.r)["statut"], "PASS")

    def test_retrait_conserve_les_empreintes_sans_bloquer_une_modification(self):
        p = self.verrou()
        manifeste = self.r / "VERROU-REIMPRESSION.json"
        d = json.loads(manifeste.read_text())
        d.update(etat="retire", date_retrait="2026-10-31", origine_retrait="décision utilisateur")
        manifeste.write_text(json.dumps(d))
        p.write_text("nouvelle source")
        resultat = verifier(self.r)
        self.assertEqual(resultat["statut"], "INACTIF")
        self.assertEqual(resultat["fichiers_historiques"], 1)
        self.assertEqual(d["fichiers"]["src/model.py"], hashlib.sha256("référence".encode()).hexdigest())

    def test_retrait_sans_origine_refuse(self):
        self.verrou()
        manifeste = self.r / "VERROU-REIMPRESSION.json"
        d = json.loads(manifeste.read_text())
        d["etat"] = "retire"
        manifeste.write_text(json.dumps(d))
        with self.assertRaises(ValueError):
            verifier(self.r)


if __name__ == "__main__":
    unittest.main()
