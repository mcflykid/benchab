"""Contrats de consultation, sans dépendance géométrique ni écriture de projet."""

import json
import tempfile
import unittest
from pathlib import Path

from benchab.visualisation.catalogue_visuel import accueil, catalogue, interne, projet_courant


class CatalogueVisuel(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.p = self.root / "projets/pot-ete"
        self.p.mkdir(parents=True)
        (self.p / "qualite.json").write_text(json.dumps({"suivi": {"titre": "<Pot été>", "statut": "EN_PREPARATION"}}))

    def test_absence_de_geometrie_explicite_sans_validation_physique(self):
        self.assertFalse(catalogue(self.root)[0]["disponible"])
        page = accueil(self.root).decode()
        self.assertIn("&lt;Pot été&gt;", page)
        self.assertNotIn("<Pot été>", page)
        self.assertIn("Scène indisponible", page)
        self.assertIn("EN_PREPARATION", page)

    def test_navigation_conserve_les_exports_et_n_ecrit_pas(self):
        f = self.p / "candidats/assemblage.json"
        f.parent.mkdir()
        f.write_text('{"pieces":{}}')
        page = self.p / "preuves/visualisation/projet-3d.html"
        page.parent.mkdir(parents=True)
        page.write_text("export historique")
        avant = {str(f.relative_to(self.root)): f.read_bytes() for f in self.root.rglob("*") if f.is_file()}
        output = accueil(self.root).decode()
        self.assertIn("/inspecter?projet=pot-ete", output)
        self.assertIn("/projets/pot-ete/preuves/visualisation/projet-3d.html", output)
        self.assertEqual(
            avant, {str(f.relative_to(self.root)): f.read_bytes() for f in self.root.rglob("*") if f.is_file()}
        )

    def test_projets_et_sources_hors_racine_refuses(self):
        for nom in ("../pot-ete", "/etc", "pot ete", "Pot"):
            with self.subTest(nom=nom), self.assertRaises(ValueError):
                projet_courant(self.root, nom)
        for rel in ("../qualite.json", "/etc/hosts"):
            with self.subTest(rel=rel), self.assertRaises(ValueError):
                interne(self.p, rel)
        (self.p / "lien").symlink_to(self.root)
        with self.assertRaises(ValueError):
            interne(self.p, "lien/quelconque")

    def test_projet_lien_non_consulte(self):
        (self.root / "projets/alias").symlink_to(self.p)
        self.assertEqual(len(catalogue(self.root)), 1)
        with self.assertRaises(ValueError):
            projet_courant(self.root, "alias")

    def test_metadonnees_mal_typees_signalees_sans_crash(self):
        path = self.p / "qualite.json"
        for value in (
            [],
            {"suivi": []},
            {"suivi": {"titre": None}},
            {"suivi": {"references_imprimees": [0]}},
            {"suivi": {"references_imprimees": [{"fichiers": [0]}]}},
        ):
            with self.subTest(value=value):
                path.write_text(json.dumps(value))
                before = path.read_bytes()
                result = catalogue(self.root)
                self.assertEqual(result[0]["etat"], "Métadonnées illisibles")
                self.assertFalse(result[0]["disponible"])
                self.assertIn("Métadonnées illisibles", accueil(self.root).decode())
                self.assertEqual(path.read_bytes(), before)
        path.write_text(json.dumps({"suivi": {"titre": "Projet récupéré"}}))
        self.assertEqual(catalogue(self.root)[0]["titre"], "Projet récupéré")

    def test_formes_manifeste_refusees_avant_lecture_maillages(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        from benchab.visualisation.catalogue_visuel import scene

        path = self.p / "candidats/assemblage.json"
        path.parent.mkdir()
        with patch.dict(
            "sys.modules", {"visionneuse": SimpleNamespace(Visionneuse=object), "numpy": SimpleNamespace()}
        ):
            for value in ([], {"models": 7}, {"models": {"Cube": {}}, "parts": [0]}):
                with self.subTest(value=value):
                    path.write_text(json.dumps(value))
                    with self.assertRaises(ValueError):
                        scene(self.root, "pot-ete")


if __name__ == "__main__":
    unittest.main()
