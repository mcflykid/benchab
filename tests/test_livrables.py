"""Gel exclusif : un nouveau gel ne peut masquer l'altération d'une version figée."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from benchab.controles import livrables as L


class Livrables(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.piece = self.root / "piece.stl"
        self.piece.write_text("original")
        self.capture = contextlib.redirect_stdout(io.StringIO())
        self.capture.__enter__()
        self.addCleanup(self.capture.__exit__, None, None, None)

    def test_nouveau_gel_ne_masque_pas_alteration(self):
        L.figer(self.root, ["*.stl"])
        self.assertEqual(L.verifier(self.root), 0)
        manifest = self.root / "LIVRABLES.json"
        before = manifest.read_bytes()
        self.piece.write_text("altérée")
        self.assertEqual(L.verifier(self.root), 1)
        with self.assertRaises(ValueError):
            L.figer(self.root, ["*.stl"])
        self.assertEqual(manifest.read_bytes(), before)
        self.assertEqual(L.verifier(self.root), 1)

    def test_gel_vide_et_source_symbolique_refuses(self):
        with self.assertRaises(ValueError):
            L.figer(self.root, ["absent*.stl"])
        alias = self.root / "alias.stl"
        alias.symlink_to(self.piece)
        with self.assertRaises(ValueError):
            L.figer(self.root, ["alias.stl"])
        self.assertFalse((self.root / "LIVRABLES.json").exists())

    def test_chemin_manifest_exterieur_refuse(self):
        manifest = self.root / "LIVRABLES.json"
        manifest.write_text(json.dumps({"fichiers": [{"chemin": "../piece.stl", "sha256": "a" * 64}]}))
        with self.assertRaises(ValueError):
            L.verifier(self.root)
        manifest.unlink()
        with self.assertRaises(ValueError):
            L.figer(self.root, ["../*.stl"])
        self.assertFalse(manifest.exists())


if __name__ == "__main__":
    unittest.main()
