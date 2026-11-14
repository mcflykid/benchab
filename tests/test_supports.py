"""Masse de support lue dans le G-code ; données synthétiques sans tranchage."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import trimesh

from benchab.controles.supports_requis import masse_support

ICI = Path(__file__).resolve().parent


class MasseSupport(unittest.TestCase):
    def test_reamorcage_sans_deplacement_exclu(self):
        g = ";Z:1\n;TYPE:Support\nG1 X1 Y1 E1\nG1 E-.8\nG1 E.8\nG1 X2 E.5"
        bandes, interface = masse_support(g, 1, 1000)
        self.assertAlmostEqual(sum(bandes.values()), 1.5)
        self.assertEqual(interface, 0)

    def test_autres_types_et_retractions_exclus(self):
        g = ";TYPE:Outer wall\nG1 X1 E3\n;TYPE:Support\nG1 X2 E-.4 ; retrait\nG1 X3 F900 ; deplacement"
        self.assertFalse(masse_support(g, 1, 1000)[0])

    def test_interface_et_tranches_de_5_mm(self):
        g = ";Z:2\n;TYPE:Support\nG1 X1 E1\n;Z:7.5\n;TYPE:Support interface\nG1 Y4 E.25"
        bandes, interface = masse_support(g, 1, 1000)
        self.assertEqual(bandes, {0: 1, 5: 0.25})
        self.assertAlmostEqual(interface, 0.25)

    def test_orientations_reutilise_la_regle_commune(self):
        from benchab.controles import orientations_supports

        src = Path(orientations_supports.__file__).read_text()
        self.assertIn("S.masse_support(", src)
        self.assertNotIn("startswith('Support')", src)

    def test_orientation_ecrit_source_canonique_depuis_profil_explicitement_fourni(self):
        """Vérifie le nom canonique depuis un profil fixture ; arrêt mocké avant tout slicer."""
        from benchab.controles import orientations_supports

        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporaire:
            profil = Path(temporaire) / "profil.3mf"
            with ZipFile(profil, "w") as archive:
                archive.writestr(
                    "Metadata/project_settings.config",
                    '{"filament_colour":["#FFFFFF"],"filament_density":["1.24"],"filament_diameter":["1.75"]}',
                )
            mesh = trimesh.creation.box((10, 10, 10))
            appels = []

            def faux_export(plaques, settings, cible, **options):
                appels.append((Path(cible).name, options))
                return Path(cible)

            with (
                patch.object(orientations_supports.A, "ecrire_3mf", side_effect=faux_export),
                patch.object(orientations_supports.A, "trancher", side_effect=RuntimeError("arrêt mocké")),
                self.assertRaisesRegex(RuntimeError, "arrêt mocké"),
            ):
                orientations_supports.trancher([("cube #1", mesh)], profil, {})
        self.assertTrue(appels[0][1]["canonique"])
        self.assertEqual(appels[0][1]["nom_source"], "orientations.3mf")

    def test_reglages_de_supports_conservent_defauts_et_options(self):
        from benchab.controles import supports_requis

        self.assertEqual(supports_requis.reglages(), supports_requis.PAR_DEFAUT)
        resultat = supports_requis.reglages(
            "normal",
            "grille",
            manuel=True,
            partout=True,
            angle=45,
            z_haut=0.3,
            interfaces=0,
            autres=["bridge_no_support=1"],
        )
        self.assertEqual(
            (resultat["support_type"], resultat["support_style"], resultat["support_on_build_plate_only"]),
            ("normal(manual)", "grid", "0"),
        )
        self.assertEqual(
            (
                resultat["support_threshold_angle"],
                resultat["support_top_z_distance"],
                resultat["support_interface_top_layers"],
                resultat["bridge_no_support"],
            ),
            ("45", "0.3", "0", "1"),
        )
        self.assertEqual(supports_requis.reglages(style="hybride")["support_style"], "tree_hybrid")
        for type_, style in (("normal", "organique"), ("arbre", "grille"), ("pilier", "defaut")):
            with self.subTest(type_=type_, style=style), self.assertRaises(ValueError):
                supports_requis.reglages(type_, style)

    def test_ecriture_3mf_conserve_volume_bloqueur(self):
        from benchab.fabrication import anycubic

        maillage = trimesh.creation.box((10, 10, 10))
        maillage.apply_translation(-maillage.bounds[0])
        item = {
            "name": "Cube",
            "mesh": maillage,
            "w": 10,
            "h": 10,
            "x": 20,
            "y": 20,
            "extruder": 1,
            "volumes": [{"name": "bloqueur", "mesh": maillage.copy(), "subtype": "support_blocker"}],
        }
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporaire:
            chemin = Path(temporaire) / "fixture.3mf"
            anycubic.ecrire_3mf(
                [{"name": "", "items": [item]}, {"name": "", "items": [{**item, "name": "Cube2", "volumes": []}]}],
                {},
                chemin,
                cli=True,
            )
            with ZipFile(chemin) as archive:
                configuration = archive.read("Metadata/model_settings.config").decode("utf-8")
                modele = archive.read("3D/3dmodel.model").decode("utf-8")
        self.assertIn('subtype="support_blocker"', configuration)
        self.assertEqual(modele.count("<item "), 2)
        self.assertIn('objectid="1"', modele)
        self.assertIn('objectid="3"', modele)


if __name__ == "__main__":
    unittest.main()
