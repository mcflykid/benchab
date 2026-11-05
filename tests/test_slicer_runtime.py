"""Régressions de configuration passive du moteur, sans exécution native."""

import hashlib
import os
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from benchab.fabrication import slicer_runtime, trancher


class SlicerRuntimeTests(unittest.TestCase):
    def configuration(self, executable="/configuration/Temoin.app/Contents/MacOS/Temoin"):
        return {
            "machine": {"modeles_slicer": ["Anycubic Kobra S1", "Anycubic Kobra S1 Combo"], "buse_mm": 0.4},
            "slicer": {
                "nom": "TemoinSlicer",
                "version_qualifiee": "2.0.0-beta",
                "executable": executable,
            }
        }

    def test_lit_identite_et_chemin_de_la_configuration_sans_lancement(self):
        with patch.object(slicer_runtime, "charger", return_value=self.configuration()):
            with patch("subprocess.Popen") as lancer, patch("subprocess.run") as executer:
                self.assertEqual(
                    slicer_runtime.executable(),
                    Path("/configuration/Temoin.app/Contents/MacOS/Temoin"),
                )
                resultat = slicer_runtime.informations()
        self.assertEqual(resultat["nom"], "TemoinSlicer")
        self.assertEqual(resultat["version"], "2.0.0-beta")
        self.assertTrue(resultat["prerelease"])
        self.assertFalse(resultat["present"])
        lancer.assert_not_called()
        executer.assert_not_called()

    def test_informations_passives_du_bundle_et_empreinte_fixture(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporaire:
            executable = Path(temporaire) / "Temoin.app/Contents/MacOS/Temoin"
            executable.parent.mkdir(parents=True)
            contenu = b"fixture executable; never launched"
            executable.write_bytes(contenu)
            executable.chmod(0o700)
            info = executable.parents[1] / "Info.plist"
            with info.open("wb") as flux:
                plistlib.dump(
                    {
                        "CFBundleName": "Bundle fixture",
                        "CFBundleShortVersionString": "1.2.3",
                        "CFBundleVersion": "build-4",
                    },
                    flux,
                )
            with patch.object(
                slicer_runtime,
                "charger",
                return_value=self.configuration(str(executable)),
            ):
                resultat = slicer_runtime.informations(empreinte=True)
        self.assertTrue(resultat["present"])
        self.assertEqual(resultat["nom"], "Bundle fixture")
        self.assertEqual(resultat["version"], "1.2.3")
        self.assertEqual(resultat["build"], "build-4")
        self.assertFalse(resultat["prerelease"])
        self.assertEqual(resultat["sha256"], hashlib.sha256(contenu).hexdigest())

    def test_chemin_explicite_est_informatif_et_ne_remplace_pas_le_configure(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporaire:
            autre = Path(temporaire) / "autre"
            autre.write_text("fixture", encoding="utf-8")
            autre.chmod(0o700)
            with patch.object(slicer_runtime, "charger", return_value=self.configuration()):
                resultat = slicer_runtime.informations(autre)
                self.assertEqual(slicer_runtime.executable(), Path(self.configuration()["slicer"]["executable"]))
        self.assertEqual(resultat["executable"], str(autre))
        self.assertIsNone(resultat["version"])

    def test_configuration_incomplete_ou_chemin_relatif_refuse(self):
        for config in ({}, self.configuration("bin/moteur"), {"slicer": {"nom": "S1"}}):
            with self.subTest(config=config), patch.object(slicer_runtime, "charger", return_value=config):
                with self.assertRaises(ValueError):
                    slicer_runtime.configuration()

    def test_execution_unique_cree_home_datadir_et_cache_neufs_sans_lancer(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporaire:
            racine = Path(temporaire)
            source = racine / "source.3mf"
            with ZipFile(source, "w") as archive:
                archive.writestr(
                    "Metadata/model_settings.config",
                    '<config><plate><metadata key="plater_name" value="Essai" /></plate></config>',
                )
                archive.writestr(
                    "Metadata/project_settings.config",
                    '{"filament_type":["PLA"],"filament_colour":["#FFFFFF"],'
                    '"filament_settings_id":["PLA"],"filament_ids":["id-fixture"],'
                    '"printer_model":"Anycubic Kobra S1","nozzle_diameter":["0.4"]}',
                )
            dossiers = []

            class ArretAvantExecution(Exception):
                pass

            def intercepter(commande, **options):
                travail = Path(options["cwd"])
                environnement = options["env"]
                dossiers.append(travail)
                self.assertEqual(commande[0], "/configuration/Temoin.app/Contents/MacOS/Temoin")
                self.assertEqual(Path(commande[commande.index("--datadir") + 1]), travail / "data")
                for cle in ("HOME", "TMPDIR", "XDG_CACHE_HOME", "XDG_CONFIG_HOME"):
                    chemin = Path(environnement[cle])
                    self.assertTrue(chemin.is_relative_to(travail))
                    self.assertTrue(chemin.is_dir())
                    self.assertNotEqual(environnement[cle], os.environ.get(cle))
                raise ArretAvantExecution

            configuration = self.configuration()
            configuration["slicer"]["plaques_max"] = 30

            def creer_dossier_travail(prefix, dir):
                chemin = racine / f"{prefix}{len(dossiers)}"
                chemin.mkdir()
                return str(chemin)

            with patch.object(trancher, "charger", return_value=configuration):
                with patch.object(trancher.tempfile, "mkdtemp", side_effect=creer_dossier_travail):
                    with patch.object(trancher.subprocess, "Popen", side_effect=intercepter) as lancement:
                        for _ in range(2):
                            with self.assertRaises(ArretAvantExecution):
                                trancher._executer(
                                    {
                                        "source": str(source),
                                        "destination": str(racine / "sortie.3mf"),
                                        "timeout_s": 5,
                                    }
                                )
            self.assertEqual(lancement.call_count, 2)
            self.assertEqual(len(set(dossiers)), 2)
            self.assertTrue(all(dossier.is_relative_to(racine) for dossier in dossiers))


if __name__ == "__main__":
    unittest.main()
