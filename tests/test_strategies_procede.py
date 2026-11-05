"""Régressions synthétiques pour les profils et études de procédé."""

import copy
import json
import shutil
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import trimesh
from test_optimisation import profil_avec_snapshots

from benchab.donnees.base import Base
from benchab.fabrication import anycubic
from benchab.fabrication import strategies_procede as strategies


class StrategiesProcedeTests(unittest.TestCase):
    def setUp(self):
        temporaire = tempfile.TemporaryDirectory(dir="/private/tmp")
        self.addCleanup(temporaire.cleanup)
        self.base = Base(Path(temporaire.name) / "prive")
        self.addCleanup(self.base.fermer)
        self.dossier = self.base.prive / "etudes/fixture"
        self.dossier.mkdir(parents=True)
        self.source = self.dossier / "base.3mf"
        self.modele = self.dossier / "piece.stl"
        maillage = trimesh.creation.box(extents=(20, 20, 10))
        maillage.apply_translation((10, 10, 5))
        anycubic.ecrire_3mf(
            [
                {
                    "name": "plaque",
                    "items": [{"name": "Piece", "mesh": maillage, "x": 10, "y": 10, "extruder": 1, "settings": {}}],
                }
            ],
            self.reglages(),
            self.source,
        )
        trimesh.creation.box(extents=(20, 20, 10)).export(self.modele)
        self.parent = {
            "name": "Standard",
            "type": "process",
            "layer_height": "0.2",
            "top_shell_layers": "4",
            "top_shell_thickness": "1",
            "initial_layer_speed": "20",
        }
        self.raw = {
            "name": "My 0.12",
            "type": "print",
            "inherits": "Standard",
            "layer_height": "0.12",
            "top_shell_layers": "3",
            "bridge_flow": "1.5",
            "initial_layer_speed": "100",
            "sparse_infill_pattern": "adaptivecubic",
            "infill_combination": "1",
            "support_object_xy_distance": "1.75",
            "xy_contour_compensation": "-0.1",
        }
        self.profil = self.dossier / "profil.json"
        self.profil.write_text(json.dumps(self.raw), encoding="utf-8")
        self.maitre = profil_avec_snapshots(self.base.prive / "machine")

    @staticmethod
    def reglages():
        return {
            "printer_model": "Anycubic Kobra S1 Combo",
            "nozzle_type": "brass",
            "nozzle_diameter": "0.4",
            "printable_area": ["0x0", "250x0", "250x250", "0x250"],
            "printable_height": "250",
            "filament_settings_id": ["Filament fixture"],
            "filament_ids": ["filament-fixture"],
            "filament_diameter": ["1.75"],
            "filament_max_volumetric_speed": ["12"],
            "filament_max_volumetric_speed_BRASS": ["12"],
            "nozzle_temperature": ["220"],
            "nozzle_temperature_BRASS": ["220"],
            "nozzle_temperature_initial_layer": ["220"],
            "nozzle_temperature_initial_layer_BRASS": ["220"],
            "layer_height": "0.2",
            "top_shell_layers": "5",
            "top_shell_thickness": "1",
            "enable_support": "1",
        }

    def audit(self):
        return strategies.auditer_profil(self.base, self.profil, {"Standard": self.parent}, self.parent)

    def test_heritage_epaisseur_et_differences_sans_modification(self):
        avant = strategies.sha(self.profil)
        parent_avant = copy.deepcopy(self.parent)
        rapport = self.audit()
        self.assertEqual(rapport["resolution"]["manquants"], [])
        self.assertAlmostEqual(rapport["dessus"]["couches_seules_mm"], 0.36)
        self.assertEqual(rapport["dessus"]["minimum_nominal_mm"], 1)
        self.assertIn("bridge_flow", rapport["differences"])
        self.assertGreaterEqual(len(rapport["risques"]), 6)
        self.assertEqual(strategies.sha(self.profil), avant)
        self.assertEqual(self.parent, parent_avant)
        self.assertFalse(rapport["adoption_automatique"])

    def test_heritage_incomplet_reste_non_verifie(self):
        rapport = strategies.auditer_profil(self.base, self.profil, {})
        self.assertEqual(rapport["statut"], "NON VÉRIFIÉ")
        self.assertIsNone(rapport["dessus"]["minimum_nominal_mm"])

    def test_valeurs_non_finies_et_couches_superieures_invalides_refusees(self):
        cas = (
            ("layer_height", "nan"),
            ("bridge_flow", False),
            ("top_shell_layers", "-1"),
            ("top_shell_layers", "2.5"),
            ("top_shell_thickness", "-0.2"),
        )
        for cle, valeur in cas:
            self.profil.write_text(json.dumps({**self.raw, cle: valeur}), encoding="utf-8")
            with self.subTest(cle=cle, valeur=valeur), self.assertRaises(ValueError):
                self.audit()

    def test_profil_machine_acier_et_commande_C_refuses_sans_conversion(self):
        brut = {
            "name": "Optimized",
            "type": "printer",
            "printer_model": "Anycubic Kobra S1",
            "nozzle_type": "hardened_steel",
            "nozzle_diameter": "0.4",
            "machine_max_speed_x": "600,300",
            "machine_max_jerk_x": "0,0",
            "machine_start_gcode": "C X20000\\nM203 X600",
            "change_filament_gcode": "",
        }
        self.profil.write_text(json.dumps(brut), encoding="utf-8")
        rapport = strategies.auditer_machine(self.base, self.profil, self.maitre)
        self.assertEqual(rapport["statut"], "FAIL")
        self.assertEqual(rapport["valeurs_modes"]["machine_max_speed_x"], [600, 300])
        self.assertIsNone(rapport["mode_choisi"])
        self.assertEqual(rapport["commandes_non_couvertes"][0]["opcode"], "C")
        self.assertFalse(rapport["adoption_automatique"])

    def test_limites_machine_invalides_refusees(self):
        brut = {
            "name": "S1",
            "type": "printer",
            "printer_model": "Anycubic Kobra S1",
            "nozzle_type": "brass",
            "nozzle_diameter": "0.4",
            "machine_max_speed_x": "-5,600",
        }
        self.profil.write_text(json.dumps(brut), encoding="utf-8")
        with self.assertRaises(ValueError):
            strategies.auditer_machine(self.base, self.profil, self.maitre)

    def test_strategies_bornees_sans_baisse_des_dessus(self):
        reglages = self.reglages()
        reglages["top_shell_thickness"] = "1.5"
        variantes = strategies.proposer_strategies(reglages, ["adaptivecubic", "combinaison", "dessus"])
        self.assertTrue(all(variante["acceptable"] is False for variante in variantes))
        self.assertGreaterEqual(float(variantes[-1]["reglages"]["top_shell_thickness"]), 1.5)
        cas = ([], ["inconnue"], ["dessus", "dessus"], list(strategies.STRATEGIES))
        for choix in cas:
            with self.subTest(choix=choix), self.assertRaises(ValueError):
                strategies.proposer_strategies(reglages, choix)
        reglages["enable_support"] = "0"
        with self.assertRaises(ValueError):
            strategies.proposer_strategies(reglages, ["supports"])

    def preparer(self, sortie=None):
        return strategies.preparer_etude(
            self.base,
            self.source,
            self.modele,
            sortie or (self.dossier / "recherche"),
            maitre=self.maitre,
        )

    def test_etude_neuve_copie_sources_et_conserve_la_configuration(self):
        avant = [strategies.sha(chemin) for chemin in (self.source, self.modele)]
        dossier = self.dossier / "recherche"
        resultat = self.preparer(dossier)
        configuration = json.loads(Path(resultat["etude"]).read_text(encoding="utf-8"))
        self.assertEqual(configuration["pieces"][0]["reference"], {})
        self.assertEqual(len(configuration["pieces"][0]["variantes"]), 2)
        self.assertEqual(avant, [strategies.sha(chemin) for chemin in (self.source, self.modele)])
        self.assertEqual(strategies.sha(dossier / "reference.3mf"), avant[0])
        self.assertEqual(strategies.sha(dossier / "modele.stl"), avant[1])
        with self.assertRaises((OSError, ValueError)):
            self.preparer(dossier)

    def test_sortie_dans_preparation_figee_ou_lien_refusee(self):
        (self.base.prive / "preparations/P01").mkdir(parents=True)
        (self.base.prive / "alias").symlink_to(self.base.prive)
        for chemin in (self.base.prive / "preparations/P01/recherche", self.base.prive / "alias/recherche"):
            with self.subTest(chemin=chemin), self.assertRaises((OSError, ValueError)):
                self.preparer(chemin)

    def test_source_deja_tranchee_refusee_avant_creation(self):
        source_tranchee = self.dossier / "tranchee.3mf"
        shutil.copy2(self.source, source_tranchee)
        with zipfile.ZipFile(source_tranchee, "a") as archive:
            archive.writestr("Metadata/plate_1.gcode", "G1 X1")
        sortie = self.dossier / "refusee"
        with self.assertRaises(ValueError):
            strategies.preparer_etude(self.base, source_tranchee, self.modele, sortie, maitre=self.maitre)
        self.assertFalse(sortie.exists())

    def test_maillage_hors_volume_refuse_avant_creation(self):
        trimesh.creation.box(extents=(300, 20, 10)).export(self.modele)
        sortie = self.dossier / "refusee"
        with self.assertRaises(ValueError):
            self.preparer(sortie)
        self.assertFalse(sortie.exists())

    def test_etude_alteree_refusee_avant_comparateur(self):
        resultat = self.preparer()
        Path(resultat["etude"]).write_text("{}", encoding="utf-8")
        with patch("benchab.fabrication.comparer_remplissages.main") as comparer, self.assertRaises(ValueError):
            strategies.executer_etude(self.base, resultat["etude"], self.maitre)
        comparer.assert_not_called()

    def test_chemin_sortie_forge_refuse_meme_si_empreinte_actualisee(self):
        resultat = self.preparer()
        etude = Path(resultat["etude"])
        configuration = json.loads(etude.read_text(encoding="utf-8"))
        configuration["sortie"] = "../hors"
        etude.write_text(json.dumps(configuration), encoding="utf-8")
        manifeste_path = etude.with_name("MANIFESTE.json")
        manifeste = json.loads(manifeste_path.read_text(encoding="utf-8"))
        manifeste["etude_sha256"] = strategies.sha(etude)
        manifeste_path.write_text(json.dumps(manifeste), encoding="utf-8")
        with patch("benchab.fabrication.comparer_remplissages.main") as comparer, self.assertRaises(ValueError):
            strategies.executer_etude(self.base, etude, self.maitre)
        comparer.assert_not_called()

    def test_journal_symbolique_refuse_sans_ecriture_exterieure(self):
        resultat = self.preparer()
        etude = Path(resultat["etude"])
        cible = self.dossier / "a-preserver"
        etude.with_name("comparateur.log").symlink_to(cible)
        with patch("benchab.fabrication.comparer_remplissages.main") as comparer, self.assertRaises(ValueError):
            strategies.executer_etude(self.base, etude, self.maitre)
        comparer.assert_not_called()
        self.assertFalse(cible.exists())

    def test_contexte_perime_refuse_avant_execution(self):
        resultat = self.preparer()
        with patch.object(strategies, "contexte_etude", return_value={"nouveau_maitre": True}):
            with patch("benchab.fabrication.comparer_remplissages.main") as comparer, self.assertRaises(ValueError):
                strategies.executer_etude(self.base, resultat["etude"], self.maitre)
        comparer.assert_not_called()

    def test_comparateur_est_appele_et_aucune_adoption_automatique(self):
        resultat = self.preparer()
        etude = Path(resultat["etude"])
        contenu = {"resultats": [], "meilleure_acceptable": {}}

        def comparer(fichier, *, prive):
            self.assertEqual(fichier, etude)
            self.assertEqual(prive, self.base.prive)
            dossier = etude.parent / "comparaison"
            dossier.mkdir()
            (dossier / "comparaison.json").write_text(json.dumps(contenu), encoding="utf-8")
            return contenu

        with patch("benchab.fabrication.comparer_remplissages.main", side_effect=comparer) as comparer:
            rapport = strategies.executer_etude(self.base, etude, self.maitre)
        comparer.assert_called_once_with(etude, prive=self.base.prive)
        self.assertFalse(rapport["adoption_automatique"])
        self.assertEqual(rapport["statut"], "WARN")
        preuve = Path(rapport["rapport"]) / "comparaison.json"
        self.assertEqual(json.loads(preuve.read_text(encoding="utf-8")), contenu)
        with self.assertRaises(ValueError):
            strategies.executer_etude(self.base, etude, self.maitre)

    def test_main_utilise_argv_fourni_sans_remplacer_sys_argv(self):
        parents = self.dossier / "parents.json"
        parents.write_text(json.dumps({"Standard": self.parent}), encoding="utf-8")
        ancien_argv = ["appelant", "arguments-invalides"]
        sortie = StringIO()
        with patch("sys.argv", ancien_argv), redirect_stdout(sortie):
            code = strategies.main(
                [
                    "--prive",
                    str(self.base.prive),
                    "auditer-profil",
                    str(self.profil),
                    "--parents",
                    str(parents),
                ]
            )
            self.assertEqual(sys.argv, ancien_argv)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(sortie.getvalue())["statut"], "WARN")


if __name__ == "__main__":
    unittest.main()
