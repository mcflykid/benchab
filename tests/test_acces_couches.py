"""G-code conservé, génération isolée et absence explicite."""

import json
import tempfile
import unittest
from pathlib import Path

from benchab.visualisation.catalogue_visuel import catalogue, couches, source_couches


class AccesCouches(unittest.TestCase):
    def test_candidat_invalide_nempeche_pas_de_trouver_une_reference(self):
        """L'index ambigu est écarté ; l'aperçu garde la référence explicite comme source."""
        from zipfile import ZipFile

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "projets/temoin"
            (p / "candidats").mkdir(parents=True)
            (p / "reference").mkdir()
            q = {"suivi": {"references_imprimees": [{"fichiers": [{"chemin": "reference/envoi.gcode"}]}]}}
            reference = p / "reference/envoi.gcode"
            reference.write_text("G90\n", encoding="utf-8")
            with ZipFile(p / "candidats/tranche.3mf", "w") as archive:
                archive.writestr("Metadata/plate_1.gcode", "G90\n")
                with self.assertWarns(UserWarning):
                    archive.writestr("Metadata/plate_1.gcode", "G90\n")
            self.assertEqual(source_couches(p, q), reference)

    def test_generation_sans_ecriture_et_absence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = root / "projets/temoin"
            (p / "reference").mkdir(parents=True)
            q = {"suivi": {"references_imprimees": [{"fichiers": [{"chemin": "reference/envoi.gcode"}]}]}}
            (p / "qualite.json").write_text(json.dumps(q))
            with self.assertRaises(ValueError):
                couches(root, "temoin")
            source = p / "reference/envoi.gcode"
            source.write_text(
                (
                    "G90\nM83\n;Z:0.2\nG1 Z0.2\n;TYPE:Outer wall\nG1 X0 Y0 F1200\nG1 X10 Y0 "
                    "E1\n;Z:0.4\nG1 Z0.4\nG1 X10 Y10 E1\n"
                )
            )
            before = {f: f.read_bytes() for f in p.rglob("*") if f.is_file()}
            self.assertEqual(source_couches(p, q), source)
            self.assertTrue(catalogue(root)[0]["couches"])
            html = couches(root, "temoin").decode()
            self.assertIn("window.apercu", html)
            self.assertIn("SHA-256", html)
            self.assertEqual(before, {f: f.read_bytes() for f in p.rglob("*") if f.is_file()})


if __name__ == "__main__":
    unittest.main()
