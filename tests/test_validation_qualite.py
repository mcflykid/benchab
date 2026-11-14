"""Régressions des barrières de validation sur des fixtures temporaires."""

import json
import tempfile
import unittest
from pathlib import Path

from benchab.qualite import validation


class ValidationTests(unittest.TestCase):
    def test_integrite_sans_empreinte_attendue_ne_passe_pas(self):
        with tempfile.TemporaryDirectory() as temporaire:
            base = Path(temporaire)
            (base / "assemblage").mkdir()
            (base / "assemblage" / "piece.stl").write_text("mesh")
            (base / "manifest.json").write_text(json.dumps({"models": {"piece": {}}, "parts": []}))

            resultats = validation.verifier(
                base,
                {"manifeste": "manifest.json", "stl_assemblage": "assemblage", "stl_impression": "assemblage"},
                humain=False,
            )

        self.assertFalse(
            any(resultat["statut"] == "PASS" for resultat in resultats if resultat["controle"].startswith("integrite"))
        )

    def test_non_conformite_recursive_ne_peut_pas_etre_ignoree(self):
        for statut in ("WARN", "NON VÉRIFIÉ", "INCONNU", "NON APPLICABLE"):
            with self.subTest(statut=statut):
                self.assertTrue(validation.mauvais({"statut": "PASS", "controles": [{"statut": statut}]}))


if __name__ == "__main__":
    unittest.main()
