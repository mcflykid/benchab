"""Calibration : seulement table et sorties temporaires, jamais le vrai profil."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchab.conception import eprouvette_trous as T


class Calibration(unittest.TestCase):
    def test_mesures_invalides_preservent_table(self):
        for args in [
            [],
            ["--bobine", "test"],
            ["3=nan"],
            ["3=inf"],
            ["3=0"],
            ["3=-1"],
            ["nan=2"],
            ["99=2"],
            ["3=2", "3=2.1"],
            ["3=2", "4=nan"],
        ]:
            with self.subTest(args=args), tempfile.TemporaryDirectory() as td:
                p = Path(td) / "table.json"
                p.write_text(json.dumps({"mesures": [], "vertical_mm": [[3, 0]], "statut": "NON MESURÉ"}))
                before = p.read_bytes()
                with patch.object(T.trous, "TABLE", p), self.assertRaises(ValueError):
                    T.mesure(args, "test")
                self.assertEqual(p.read_bytes(), before)

    def test_mesure_valide_historique_conserve(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "table.json"
            p.write_text(json.dumps({"mesures": [{"ancien": True}], "vertical_mm": [], "statut": "NON MESURÉ"}))
            with patch.object(T.trous, "TABLE", p):
                T.mesure(["3=2.8", "4=3.9"], "test")
            d = json.loads(p.read_text())
            self.assertEqual(len(d["mesures"]), 2)
            self.assertEqual(d["mesures"][0], {"ancien": True})
            self.assertEqual(d["vertical_mm"], [[3, 0.2], [4, 0.1]])

    def test_echec_publication_preserve_table(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "table.json"
            p.write_text(json.dumps({"mesures": [], "vertical_mm": [], "statut": "NON MESURÉ"}))
            before = p.read_bytes()
            with (
                patch.object(T.trous, "TABLE", p),
                patch.object(T.os, "replace", side_effect=OSError("interruption simulée")),
                self.assertRaises(OSError),
            ):
                T.mesure(["3=2.8"], "test")
            self.assertEqual(p.read_bytes(), before)
            self.assertEqual(sorted(q.name for q in Path(td).iterdir()), ["table.json"])

    def test_generer_refuse_sortie_existante(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "eprouvettes").mkdir()
            f = root / "eprouvettes/trous.stl"
            f.write_bytes(b"original")
            profile = root / "table.json"
            profile.write_text(json.dumps({"horizontal": {"angle_pointe_deg": 45, "ajout_diametre_mm": 0}}))
            with (
                patch.object(T, "ICI", root),
                patch.object(T.trous, "TABLE", profile),
                self.assertRaises(FileExistsError),
            ):
                T.generer(sortie=f)
            self.assertEqual(f.read_bytes(), b"original")


if __name__ == "__main__":
    unittest.main()
