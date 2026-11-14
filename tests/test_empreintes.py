"""Régressions des empreintes en flux, reprises sur fixtures privées."""

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchab.qualite import empreintes as E


class Empreintes(unittest.TestCase):
    """Empreintes de fichiers temporaires en octets ; aucune donnée réelle ni réglage."""

    def setUp(self):
        """Crée la fixture temporaire ; aucune donnée utilisateur touchée."""
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root = Path(t.name).resolve()
        (self.root / "projets").mkdir()

    def test_empreintes_vide_petit_gros(self):
        """Vérifie SHA de fichiers vides/petits/gros (octets) et blocs unitaires ; lecture seule."""
        p = self.root / "f"
        for data in (b"", b"abc", b"x" * (8 * 1024 * 1024 + 13)):
            p.write_bytes(data)
            self.assertEqual(E.sha256_fichier(p), hashlib.sha256(data).hexdigest())
        p.write_bytes(b"abc")
        self.assertEqual(E.sha256_fichier(p, 1), hashlib.sha256(b"abc").hexdigest())

    def test_absence_lien_et_bloc_invalide(self):
        """Fichier absent, lien, FIFO et bloc nul refusés ; aucune lecture bloquante ni écriture."""
        p = self.root / "f"
        with self.assertRaises(FileNotFoundError):
            E.sha256_fichier(p)
        p.write_bytes(b"abc")
        lien = self.root / "l"
        lien.symlink_to(p)
        with self.assertRaises(ValueError):
            E.sha256_fichier(lien)
        with self.assertRaises(ValueError):
            E.sha256_fichier(p, 0)
        fifo = self.root / "tube"
        os.mkfifo(fifo)
        with self.assertRaisesRegex(ValueError, "régulier"):
            E.sha256_fichier(fifo)

    def test_mutation_meme_taille_mtime_restaure(self):
        """Un changement de contenu à taille/mtime identiques donne FAIL ; aucune réparation."""
        p = self.root / "f"
        p.write_bytes(b"a" * 4096)
        st = p.stat()
        original = hashlib.sha256
        h = original()

        class Change:
            def update(self, data):
                h.update(data)
                p.write_bytes(b"b" * 4096)
                os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))

            def hexdigest(self):
                return h.hexdigest()

        with patch.object(E.hashlib, "sha256", return_value=Change()):
            with self.assertRaisesRegex(ValueError, "modifié"):
                E.sha256_fichier(p, 1024)

    def test_disparition_pendant_lecture(self):
        """La disparition pendant la lecture donne FAIL ; aucun résultat de cache fabriqué."""
        p = self.root / "f"
        p.write_bytes(b"abc")
        h = hashlib.sha256()

        class Change:
            def update(self, data):
                h.update(data)
                p.unlink()

            def hexdigest(self):
                return h.hexdigest()

        with patch.object(E.hashlib, "sha256", return_value=Change()):
            with self.assertRaises(FileNotFoundError):
                E.sha256_fichier(p)
