"""Adresse de la page générée (erreur du 23/10/2026 : l'utilisateur a vu le coffre au lieu de
l'horloge, page hors du dossier servi) ; aucun réseau."""

import tempfile
import unittest
from pathlib import Path

import trimesh

from benchab.visualisation import visionneuse as V


def ecrire(chemin):
    v = V.Visionneuse("Test", axe_haut="Z")
    v.modele("Cube", trimesh.creation.box((10, 10, 10)))
    v.piece("Cube", "Cube")
    chemin.parent.mkdir(parents=True, exist_ok=True)
    return v.ecrire(chemin)


class AdressePage(unittest.TestCase):
    def setUp(self):
        self.racine = V.RACINE
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.t = Path(t.name).resolve()

    def tearDown(self):
        V.RACINE = self.racine

    def test_page_dans_la_base_donne_l_adresse_du_serveur_commun(self):
        V.RACINE = self.t
        r = ecrire(self.t / "projets/lampe/preuves/visualisation.html")
        self.assertEqual(r["url_locale"], f"http://localhost:{V.PORT_SERVEUR}/projets/lampe/preuves/visualisation.html")
        self.assertNotIn("avertissement", r)

    def test_page_hors_base_signalee_sans_adresse(self):
        V.RACINE = self.t / "base"
        r = ecrire(self.t / "ailleurs/visualisation.html")
        self.assertNotIn("url_locale", r)
        self.assertIn("hors de", r["avertissement"])


if __name__ == "__main__":
    unittest.main()
