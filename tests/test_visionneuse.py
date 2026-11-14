"""Régressions de la visionneuse autonome sur une géométrie synthétique."""

import tempfile
import unittest
from pathlib import Path

import trimesh

from benchab.visualisation.visionneuse import Visionneuse


class VisionneuseTests(unittest.TestCase):
    def test_page_hors_ligne_et_chemins_personnels_assainis(self):
        mesh = trimesh.creation.box(extents=(10, 10, 10))
        source = {"modele": str(Path.home() / "projet" / "piece.stl")}
        visionneuse = Visionneuse("Essai", source=source)
        visionneuse.modele("piece", mesh)
        visionneuse.piece("pièce", "piece")
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporaire:
            chemin = Path(temporaire) / "vue.html"
            rapport = visionneuse.ecrire(chemin)
            page = chemin.read_text()
        self.assertTrue(rapport["hors_ligne"])
        self.assertEqual(rapport["fichier"], "vue.html")
        self.assertNotIn(str(Path.home()), page)
        self.assertNotIn("cdn.jsdelivr.net", page)
        self.assertNotIn("fonts.googleapis.com", page)

    def test_sortie_exterieure_et_mode_cdn_refuses(self):
        mesh = trimesh.creation.box(extents=(2, 2, 2))
        visionneuse = Visionneuse("Essai")
        visionneuse.modele("piece", mesh)
        visionneuse.piece("pièce", "piece")
        with self.assertRaisesRegex(ValueError, "prive/ ou /private/tmp"):
            visionneuse.ecrire("/var/tmp/vue-interdite.html")
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporaire:
            with self.assertRaisesRegex(ValueError, "autonome hors ligne"):
                visionneuse.ecrire(Path(temporaire) / "vue.html", hors_ligne=False)


if __name__ == "__main__":
    unittest.main()
