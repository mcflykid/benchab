"""Le contrôle d'envoi garde un état local entre appels."""

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from benchab.controles import controle_envoi as CE


class ControleEnvoi(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dossier = Path(self.temp.name)
        self.tranche = self.dossier / "tranche.3mf"
        settings = {
            "curr_bed_type": "Textured PEI Plate",
            "filament_type": ["PLA"],
            "nozzle_temperature": [220],
            "nozzle_temperature_initial_layer": [220],
            "filament_max_volumetric_speed": [16],
        }
        modele = (
            '<config><object id="1"><metadata key="extruder" value="1"/></object>'
            '<plate><metadata key="plater_id" value="1"/><model_instance>'
            '<metadata key="object_id" value="1"/></model_instance></plate></config>'
        )
        with zipfile.ZipFile(self.tranche, "w") as archive:
            archive.writestr("Metadata/project_settings.config", json.dumps(settings))
            archive.writestr("Metadata/model_settings.config", modele)
            archive.writestr("Metadata/plate_1.gcode", "G90\nM83\nT0\n;LAYER_CHANGE\nG1 X0 Y0 Z.2 F600\nG1 X10 E1\n")
        self.declaration = self.dossier / "DECLARATION.json"
        self.declaration.write_text(
            json.dumps(
                {
                    "plateau": "Textured PEI Plate",
                    "filaments": {
                        "1": {
                            "matiere": "PLA témoin",
                            "fiche": "fixture",
                            "emplacement_ace": 1,
                            "temp_1re": 220,
                            "temp": 220,
                            "plateau_c": 55,
                            "debit_max": 16,
                        }
                    },
                }
            )
        )
        self.revue = self.dossier / "REVUE-ZONES-LOCALES.json"
        from benchab.controles import revue_locale as RL

        self.revue.write_text(json.dumps({"tranche_sha256": RL.sha(self.tranche), "zones": {}}))

    def tearDown(self):
        self.temp.cleanup()

    def test_main_utilise_argv_explicite(self):
        precedent = sys.argv
        try:
            sys.argv = ["controle_envoi.py", "--help"]
            self.assertEqual(CE.main(["fichier-invalide"]), 2)
        finally:
            sys.argv = precedent

    def test_deux_controles_consecutifs_ne_partagent_pas_leur_etat(self):
        premier = CE.controler(self.tranche, self.declaration, revue_locale=self.revue)
        second = CE.controler(self.tranche, self.declaration, revue_locale=self.revue)
        self.assertEqual(premier, second)
        self.assertEqual(len(premier["resultats"]), len(second["resultats"]))
        self.assertNotIn("/Users/", json.dumps(premier))


if __name__ == "__main__":
    unittest.main()
