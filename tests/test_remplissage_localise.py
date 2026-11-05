"""Tests géométriques et G-code avec des pièces créées pour BENCHAB."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import trimesh

from benchab.fabrication import remplissage_localise as remplissage
from benchab.fabrication.anycubic import ecrire_3mf, ranger

PIECE = trimesh.creation.box(extents=(40, 20, 10))
PIECE.apply_translation((20, 10, 5))
VIS = {"nom": "vis", "role": "fixation", "forme": "cylindre", "centre": [8, 10, 5], "rayon": 4, "hauteur": 10}
BLOC = {
    "nom": "corps",
    "role": "structurel",
    "forme": "boite",
    "min": [20, 0, 0],
    "max": [40, 20, 10],
    "reglages": {"sparse_infill_density": "30%"},
}


class Zones(unittest.TestCase):
    def test_zones_produisent_reglages_et_bilan(self):
        modificateurs, bilan = remplissage.volumes(PIECE, [VIS, BLOC])
        self.assertEqual([v["settings"]["sparse_infill_density"] for v in modificateurs], ["50%", "30%"])
        self.assertTrue(all(v["subtype"] == "modifier_part" for v in modificateurs))
        self.assertEqual(bilan[0]["role"], "fixation")

    def test_reglage_connu_wall_loops_est_conserve_dans_volume(self):
        modificateurs, _ = remplissage.volumes(PIECE, [{**VIS, "reglages": {"wall_loops": "5"}}])
        self.assertEqual(modificateurs[0]["settings"]["wall_loops"], "5")

    def test_zone_inchangee_ne_genere_pas_de_modificateur(self):
        zone = {"nom": "reference", "role": "inchange", "forme": "boite", "min": [0, 0, 0], "max": [40, 20, 10]}
        self.assertEqual(remplissage.volumes(PIECE, [zone])[0], [])
        self.assertEqual(remplissage.volumes(PIECE, [])[0:2], ([], []))

    def test_zones_invalides_et_doublons_refuses(self):
        invalides = [
            {**VIS, "role": "magique"},
            {**VIS, "forme": "sphere"},
            {**VIS, "rayon": -1},
            {**VIS, "centre": [200, 200, 5]},
            {**VIS, "reglages": {"future_fixture": "valeur"}},
            {**VIS, "reglages": {"sparse_infill_density": "150%"}},
            {"role": "fixation", "forme": "boite", "min": [0, 0, 0], "max": [1, 1, 1]},
            {**BLOC, "min": [30, 0, 0], "max": [20, 20, 10]},
        ]
        for zone in invalides:
            with self.subTest(zone=zone), self.assertRaises(ValueError):
                remplissage.volumes(PIECE, [zone])
        with self.assertRaisesRegex(ValueError, "nom en double"):
            remplissage.volumes(PIECE, [VIS, VIS])

    def test_boite_englobante_ne_suffit_pas_pour_piece_en_l(self):
        horizontale = trimesh.creation.box(extents=(40, 10, 10))
        horizontale.apply_translation((20, 5, 5))
        verticale = trimesh.creation.box(extents=(10, 30, 10))
        verticale.apply_translation((5, 25, 5))
        piece = trimesh.boolean.union([horizontale, verticale], engine="manifold")
        hors_matiere = {**VIS, "centre": [30, 30, 5], "rayon": 3}
        avec_matiere = {**VIS, "centre": [30, 5, 5], "rayon": 3}
        with self.assertRaisesRegex(ValueError, "ne recoupe pas"):
            remplissage.volumes(piece, [hors_matiere])
        self.assertEqual(len(remplissage.volumes(piece, [avec_matiere])[0]), 1)

    def test_ecriture_3mf_preserve_le_volume_modificateur(self):
        modificateurs, _ = remplissage.volumes(PIECE, [VIS])
        with tempfile.TemporaryDirectory(dir="/private/tmp") as repertoire:
            item = {
                "name": "P",
                "mesh": PIECE,
                "w": 40,
                "h": 20,
                "extruder": 1,
                "settings": {"sparse_infill_density": "10%"},
                "volumes": modificateurs,
            }
            ranger([item], 0, 1, 10.0, cli=True)
            archive = ecrire_3mf([{"name": "plaque", "items": [item]}], {}, Path(repertoire) / "zones.3mf", cli=True)
            with ZipFile(archive) as fichier:
                configuration = fichier.read("Metadata/model_settings.config").decode()
            self.assertIn('subtype="modifier_part"', configuration)
            self.assertIn('key="sparse_infill_density" value="50%"', configuration)

    def test_analyse_gcode_compte_la_portion_dans_chaque_region(self):
        gcode = (
            "M83\n;TYPE:Sparse infill\nG1 X0 Y0\nG1 X10 Y0 E1\n"
            "G1 X30 Y0\nG1 X40 Y0 E1\n;TYPE:Outer wall\nG1 X50 Y0 E1\n"
        )
        regions = {"gauche": [0, -1, 20, 1, -1, 1], "droite": [20, -1, 44, 1, -1, 1]}
        mesure = remplissage.analyser_gcode(gcode, regions)
        self.assertEqual(mesure["gauche"]["Sparse infill"], 10)
        self.assertEqual(mesure["droite"]["Sparse infill"], 10)
        self.assertEqual(mesure["droite"]["Outer wall"], 4)

    def test_etude_delegue_au_lanceur_unique_en_mode_temoin(self):
        """Vérifie l’export canonique depuis un profil privé et la délégation témoin mockée."""
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporaire:
            racine = Path(temporaire)
            base = racine / "base.3mf"
            with ZipFile(base, "w") as archive:
                archive.writestr(
                    "Metadata/project_settings.config",
                    '{"filament_settings_id":["PLA fixe"],"filament_colour":["#FFFFFF"]}',
                )

            def faux_tranchage(source, dossier, nom, *, usage):
                self.assertEqual(usage, "temoin")
                tranche = Path(dossier) / nom
                contenu = ["<config>"]
                for index in range(1, 4):
                    contenu.append(
                        f'<plate><metadata key="index" value="{index}"/>'
                        '<metadata key="prediction" value="60"/>'
                        '<metadata key="weight" value="1"/></plate>'
                    )
                contenu.append("</config>")
                with ZipFile(tranche, "w") as archive:
                    archive.writestr("Metadata/slice_info.config", "".join(contenu))
                    for index in range(1, 4):
                        archive.writestr(
                            f"Metadata/plate_{index}.gcode",
                            "M83\n;TYPE:Sparse infill\nG1 X0 Y0\nG1 X1 Y0 E1\n",
                        )
                return tranche, {"duree_s": 0.25}

            ecritures = []

            def faux_export(plaques, settings, cible, **options):
                ecritures.append((Path(cible).name, options))
                return Path(cible)

            with (
                patch("benchab.fabrication.trancher.trancher_fichier", side_effect=faux_tranchage) as lanceur,
                patch("benchab.fabrication.anycubic.ecrire_3mf", side_effect=faux_export),
            ):
                rapport = remplissage.eprouvette(racine / "sortie", base)
            lanceur.assert_called_once()
            self.assertEqual(rapport["tranchage_s"], 0.25)
            self.assertTrue(ecritures[0][1]["canonique"])
            self.assertEqual(ecritures[0][1]["nom_source"], "eprouvette-plaques.3mf")
            noms = [ligne["nom"] for ligne in rapport["variantes"]]
            self.assertEqual(noms, ["A-homogene", "B-localise", "C-localise-parois"])

    def test_etude_refuse_sortie_hors_benchab_et_tmp(self):
        with self.assertRaisesRegex(ValueError, "sortie refusé|BENCHAB/prive"):
            remplissage.eprouvette("/var/tmp/sortie-interdite", "/missing.3mf")


if __name__ == "__main__":
    unittest.main()
