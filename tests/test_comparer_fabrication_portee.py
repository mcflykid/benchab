"""Cas de comparaison portés ; uniquement des dossiers de fabrication synthétiques."""

import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from benchab.qualite.comparer_fabrication import comparer, main


class ComparaisonTests(unittest.TestCase):
    """Vérifie les écarts de fichiers synthétiques et refus FAIL, sans aucune préparation réelle."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.r = Path(self.tmp.name)
        self.a = self.r / "attendu"
        self.b = self.r / "obtenu"
        self.a.mkdir()
        self.b.mkdir()
        self.out = self.r / "rapport.json"

    def donnees(self, a=b"same", b=b"same"):
        (self.a / "piece.stl").write_bytes(a)
        (self.b / "piece.stl").write_bytes(b)

    def test_missing_or_empty_directories_never_pass(self):
        """Refuse dossiers vides puis absents, sans les créer ni annoncer PASS."""
        with self.assertRaises(ValueError):
            comparer(self.a, self.b, self.out)
        self.assertFalse(self.out.exists())
        self.a.rmdir()
        self.b.rmdir()
        with self.assertRaises(ValueError):
            comparer(self.a, self.b, self.out)
        self.assertFalse(self.out.exists())

    def test_cli_erreur_entree_code_2_sans_rapport_ni_recreation(self):
        """Un dossier absent donne FAIL/code2 avec erreur exacte ; aucune entrée ni rapport créé."""
        import contextlib
        import io

        self.a.rmdir()
        erreur = io.StringIO()
        with contextlib.redirect_stderr(erreur):
            code = main(["--attendu", str(self.a), "--obtenu", str(self.b), "--sortie", str(self.out)])
        self.assertEqual(code, 2)
        self.assertIn("FAIL comparaison de fabrication : ValueError:", erreur.getvalue())
        self.assertFalse(self.a.exists())
        self.assertFalse(self.out.exists())

    def test_geometric_difference_nonzero_and_report(self):
        self.donnees(b"a", b"b")
        self.assertEqual(comparer(self.a, self.b, self.out)["verdicts"]["geometrie"], "FAIL")
        self.assertTrue(self.out.exists())

    def test_report_collision_preserves_input(self):
        self.donnees()
        p = self.a / "piece.stl"
        before = p.read_bytes()
        with self.assertRaises(ValueError):
            comparer(self.a, self.b, p)
        self.assertEqual(p.read_bytes(), before)

    def test_existing_report_preserved(self):
        self.donnees()
        self.out.write_bytes(b"user report")
        with self.assertRaises(ValueError):
            comparer(self.a, self.b, self.out)
        self.assertEqual(self.out.read_bytes(), b"user report")

    def test_identical_invalid_zip_never_pass(self):
        for directory in (self.a, self.b):
            (directory / "piece.3mf").write_bytes(b"not a ZIP")
        with self.assertRaises(ValueError):
            comparer(self.a, self.b, self.out)
        self.assertFalse(self.out.exists())

    def test_identical_valid_data_pass(self):
        self.donnees()
        self.assertEqual(comparer(self.a, self.b, self.out)["verdicts"]["geometrie"], "PASS")
        self.assertTrue(self.out.exists())

    def test_commands_inside_3mf_are_compared_strictly(self):
        for directory, command in ((self.a, b"G1 X1 E1"), (self.b, b"G1 X2 E2")):
            with ZipFile(directory / "piece.3mf", "w") as archive:
                archive.writestr("Metadata/plate_1.gcode", command)
        self.assertEqual(comparer(self.a, self.b, self.out)["verdicts"]["fabrication"], "FAIL")

    def test_3mf_configuration_difference_is_not_normalized(self):
        for directory, speed in ((self.a, 100), (self.b, 101)):
            with ZipFile(directory / "piece.3mf", "w") as archive:
                archive.writestr("Metadata/project_settings.config", f'{{"speed": {speed}}}')
        rapport = comparer(self.a, self.b, self.out)
        self.assertEqual(rapport["verdicts"]["fabrication"], "FAIL")

    def test_duplicate_zip_refused(self):
        for directory in (self.a, self.b):
            path = directory / "piece.3mf"
            with ZipFile(path, "w") as archive:
                archive.writestr("Metadata/plate_1.gcode", b"G1 X1")
            with ZipFile(path, "a") as archive:
                archive.writestr("Metadata/plate_1.gcode", b"G1 X1")
        with self.assertRaises(ValueError):
            comparer(self.a, self.b, self.out)
        self.assertFalse(self.out.exists())

    def test_symlink_report_target_preserved(self):
        self.donnees()
        target = self.r / "user"
        target.write_bytes(b"user")
        self.out.symlink_to(target)
        with self.assertRaises(ValueError):
            comparer(self.a, self.b, self.out)
        self.assertEqual(target.read_bytes(), b"user")


if __name__ == "__main__":
    unittest.main()
