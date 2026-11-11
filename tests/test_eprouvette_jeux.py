"""Témoin de jeux géométrique ; les tests ne lancent pas le slicer."""

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from benchab.conception import eprouvette_jeux as J


class Jeux(unittest.TestCase):
    def setUp(self):
        temporaire = tempfile.TemporaryDirectory(dir="/private/tmp")
        self.addCleanup(temporaire.cleanup)
        self.dossier = Path(temporaire.name)

    def test_plaque_six_jeux_ronds_et_carres_croissants(self):
        mesh, cotes = J.plaque()
        self.assertTrue(mesh.is_watertight and mesh.is_volume)
        for forme in ("rond", "carre"):
            mesure = [cote for cote in cotes if cote["forme"] == forme]
            self.assertEqual([item["jeu"] for item in mesure], J.JEUX)
            self.assertEqual([item["cote_mm"] for item in mesure], [round(J.NOMINAL + 2 * jeu, 2) for jeu in J.JEUX])
        volume_plein = (8 + J.PAS * len(J.JEUX)) * 36 * J.EP
        self.assertGreater(volume_plein - mesh.volume, 12 * 75 * J.EP)

    def test_pions_nominaux_sur_collerette(self):
        for forme, aire in (("rond", 3.1416 * 25), ("carre", 100)):
            pion = J.pion(forme)
            self.assertTrue(pion.is_volume)
            self.assertAlmostEqual(pion.bounds[1][2], 14, places=3)
            self.assertAlmostEqual(pion.volume, aire * 12 + 3.1416 * 81 * 2, delta=6)
        with self.assertRaises(ValueError):
            J.pion("triangle")

    def test_generer_publie_cinq_pieces_et_refuse_collision(self):
        sortie = self.dossier / "temoin"
        pieces, rapport = J.generer(sortie)
        self.assertEqual(sorted(path.name for path in sortie.glob("*.stl")), sorted(f"{nom}.stl" for nom in pieces))
        self.assertEqual(json.loads((sortie / "eprouvette-jeux.json").read_text())["jeux_par_cote_mm"], J.JEUX)
        self.assertEqual(len(pieces), 5)
        original = sortie / "plaque.stl"
        before = original.read_bytes()
        with self.assertRaises(FileExistsError):
            J.generer(sortie)
        self.assertEqual(original.read_bytes(), before)
        self.assertEqual(rapport["nominal_mm"], J.NOMINAL)

    def test_petits_diametres_et_trous_borgnes(self):
        for nominal, borgne in ((3, False), (5, True)):
            mesh, cotes = J.plaque(nominal, borgne)
            self.assertTrue(mesh.is_watertight and mesh.is_volume)
            self.assertEqual(
                [item["cote_mm"] for item in cotes if item["forme"] == "rond"],
                [round(nominal + 2 * jeu, 2) for jeu in J.JEUX],
            )
            if borgne:
                self.assertAlmostEqual(mesh.bounds[1][2], 6.0)
                self.assertGreater(mesh.volume, 0.9 * mesh.bounding_box.volume - 12 * nominal**2 * 4.5)
            self.assertTrue(J.pion("rond", nominal).is_volume)

    def test_base_liee_refusee_avant_generation(self):
        """Une base 3MF liée n’est pas adoptée comme preuve de réglages de l’éprouvette."""
        base = self.dossier / "base.3mf"
        with zipfile.ZipFile(base, "w") as archive:
            archive.writestr("Metadata/project_settings.config", "{}")
        alias = self.dossier / "alias.3mf"
        alias.symlink_to(base)
        sortie = self.dossier / "preuve-neuve"
        with patch.object(J, "generer") as generer:
            with self.assertRaisesRegex(ValueError, "Lien symbolique"):
                J.trancher(sortie, alias)
            generer.assert_not_called()
        self.assertFalse(sortie.exists())

    def test_trancher_delegue_usage_temoin_au_lanceur_unique(self):
        """Vérifie le 3MF canonique depuis le profil fixture ; aucune unité de mesure ni vrai slicer."""
        sortie = self.dossier / "temoin"
        sortie.mkdir()
        base = self.dossier / "base.3mf"
        with zipfile.ZipFile(base, "w") as archive:
            archive.writestr(
                "Metadata/project_settings.config",
                json.dumps({"filament_settings_id": ["PLA fixe"], "filament_colour": ["#FFFFFF"]}),
            )
        appels = []
        sources_canonique = []

        def faux_lanceur(source, dossier, nom, **options):
            appels.append((source, dossier, nom, options))
            with zipfile.ZipFile(source) as archive:
                config_modele = archive.read("Metadata/model_settings.config").decode("utf-8")
            sources_canonique.append(config_modele)
            tranche = Path(dossier) / nom
            with zipfile.ZipFile(tranche, "w") as archive:
                archive.writestr(
                    "Metadata/slice_info.config",
                    '<config><plate><metadata key="index" value="1"/>'
                    '<metadata key="prediction" value="120"/><metadata key="weight" value="4.2"/>'
                    '<metadata key="support_used" value="false"/></plate></config>',
                )
            return tranche, {"duree_s": 9.5}

        with patch.object(J, "trancher_fichier", side_effect=faux_lanceur):
            rapport = J.trancher(sortie, base, filament=1, nominal=5, borgne=True)
        self.assertEqual(rapport["filament"], "PLA fixe")
        self.assertEqual(rapport["grammes"], 4.2)
        self.assertEqual(rapport["tranchage_s"], 9.5)
        self.assertEqual(appels[0][3]["usage"], "temoin")
        self.assertEqual(appels[0][2], "eprouvette-jeux-tranche.3mf")
        self.assertIn('key="source_file" value="eprouvette-jeux-plaques.3mf"', sources_canonique[0])


if __name__ == "__main__":
    unittest.main()
