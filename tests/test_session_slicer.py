"""Contrats d'extraction opt-in, uniquement données synthétiques."""

import contextlib
import hashlib
import io
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from benchab.machine import cloud_lecture as C
from benchab.machine import session_slicer as S


class SessionTests(unittest.TestCase):
    def test_adresse_macho_et_fichiers_tronques(self):
        blob = bytearray(144)
        blob[:4] = b"\xcf\xfa\xed\xfe"
        struct.pack_into("<I", blob, 16, 1)
        struct.pack_into("<II", blob, 32, 25, 72)
        struct.pack_into("<QQQQ", blob, 56, 4096, 40, 104, 40)
        blob[120:124] = b"test"
        self.assertEqual(S._adresse(blob, 4112, 4), b"test")
        for data, address in ((b"", 0), (blob[:80], 4112), (blob, 36864)):
            with self.assertRaises(ValueError):
                S._adresse(data, address, 4)
        for address, size in ((-1, 4), (0, -1), (0, 0)):
            with self.assertRaises(ValueError):
                S._adresse(blob, address, size)

    def test_dechiffrement_refuse_sans_opt_in(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d).resolve() / "config.json"
            f.write_text(json.dumps({"anycubic_cloud": {"chiffre": "valeur"}}))
            with patch.object(S, "extraire") as extract:
                with self.assertRaises(C.CloudErreur):
                    C.token_slicer(f)
                extract.assert_not_called()
                extract.return_value = "eyJ.fixture.sig"
                self.assertEqual(C.token_slicer(f, dechiffrer=True), "eyJ.fixture.sig")
                extract.assert_called_once_with({"chiffre": "valeur"})

    def test_cli_ne_dechiffre_pas_implicitement(self):
        for argv in (["--dechiffrer-session"], ["--cloud", "--dechiffrer-session"]):
            with patch.object(S, "extraire") as extract, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as result:
                    C.main(argv)
                self.assertEqual(result.exception.code, 2)
                extract.assert_not_called()

    def test_erreur_crypto_ne_revele_pas_valeur(self):
        fake = Mock()
        fake.EVP_CIPHER_CTX_new = Mock()
        fake.EVP_aes_256_cbc = Mock()
        with tempfile.TemporaryDirectory() as d:
            lib = Path(d).resolve() / "fake.dylib"
            lib.write_bytes(b"fixture")
            with (
                patch.object(S, "_materiel", return_value=(b"k" * 32, b"i" * 16, lib)),
                patch.object(S, "BIBLIOTHEQUE_SHA", hashlib.sha256(b"fixture").hexdigest()),
                patch.object(S.ctypes, "CDLL", return_value=fake),
                patch.object(S, "_dechiffrer", side_effect=RuntimeError("secret-fixture")),
            ):
                with self.assertRaisesRegex(ValueError, "^SESSION_EXTRACTION_REFUSEE$") as caught:
                    S.extraire({"chiffre": "valeur"})
                self.assertNotIn("secret-fixture", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
