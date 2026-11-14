import copy
import hashlib
import json
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from test_profil_maitre import _fait, profil_synthetique

from benchab.archives_3mf import Archive3MF
from benchab.machine import affectations_ace as A


def _corrompre_crc(path, cible):
    """Altère le CRC central d’un membre sans modifier ses octets stockés."""
    contenu = bytearray(path.read_bytes())
    position = 0
    while (position := contenu.find(b"PK\x01\x02", position)) >= 0:
        taille_nom, taille_extra, taille_commentaire = struct.unpack_from("<HHH", contenu, position + 28)
        debut_nom = position + 46
        nom = bytes(contenu[debut_nom:debut_nom + taille_nom]).decode("utf-8")
        if nom == cible:
            crc = struct.unpack_from("<I", contenu, position + 16)[0]
            struct.pack_into("<I", contenu, position + 16, crc ^ 1)
            path.write_bytes(contenu)
            return
        position = debut_nom + taille_nom + taille_extra + taille_commentaire
    raise AssertionError("membre ZIP absent : " + cible)


class AceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.r = Path(self.tmp.name).resolve()
        self.src = self.r / "projet.3mf"
        self.settings = {
            "printer_model": "Anycubic Kobra S1",
            "nozzle_diameter": ["0.4"],
            "nozzle_type": "brass",
            "filament_settings_id": ["eSUN projet"],
            "filament_type": ["PLA+"],
            "filament_colour": ["#FFFFFF"],
            "nozzle_temperature": ["220"],
            "nozzle_temperature_initial_layer": ["220"],
            "filament_max_volumetric_speed": ["16"],
        }
        self.reg = {
            "schema": 1,
            "filaments": [
                {
                    "id": "test",
                    "profil": {
                        "filament_type": ["PLA+"],
                        "nozzle_temperature": ["220"],
                        "nozzle_temperature_initial_layer": ["220"],
                        "filament_max_volumetric_speed": ["16"],
                    },
                    "stock": {"etat": "disponible", "bobines_disponibles": 1},
                    "abrasif": False,
                }
            ],
        }
        self.dec = {
            "schema": 1,
            "outils": [
                {
                    "outil": "T0",
                    "filament_id": "test",
                    "slot_declare": 4,
                    "source_declaration": "utilisateur synthétique",
                    "date_declaration": "2026-10-29",
                }
            ],
        }
        self.maitre = profil_synthetique()
        self.maitre["constructeur"] = {
            "temperature_buse_max_C": _fait(320, "degC", "constructeur"),
            "temperature_plateau_max_C": _fait(110, "degC", "constructeur"),
        }
        valeurs = {
            cle: fait["value"] for cle, fait in self.maitre["configurations"]["atelier-test-v1"]["parametres"].items()
        }
        snapshot = self.r / "snapshot.json"
        snapshot.write_text(json.dumps({"valeurs": valeurs}))
        self.maitre["sources"]["configuration"].update(
            {
                "snapshot": snapshot.name,
                "sha256_snapshot": A._sha(snapshot),
            }
        )
        self.write()

    def write(self):
        with zipfile.ZipFile(self.src, "w") as z:
            z.writestr("Metadata/project_settings.config", json.dumps(self.settings))

    def audit(self):
        return A.auditer_affectations(self.src, self.dec, maitre=self.maitre, registre=self.reg)

    def test_refuse_une_cle_de_reglage_dupliquee(self):
        """Un profil ambigu ne doit pas être audité sous la dernière valeur lue."""
        with zipfile.ZipFile(self.src, "w") as archive:
            archive.writestr("Metadata/project_settings.config", '{"nozzle_type":"brass","nozzle_type":"steel"}')
        with self.assertRaisesRegex(ValueError, "Clé de réglage JSON dupliquée"):
            self.audit()

    def test_limites_constructeur_absentes_restent_non_verifiees(self):
        self.maitre["constructeur"] = {}
        resultat = self.audit()
        self.assertEqual(resultat["statut"], "NON VERIFIE")
        self.assertTrue(any("Limite constructeur non vérifiée" in r for r in resultat["non_verifie"]))

    def test_no_default_or_physical_confirmation(self):
        r = self.audit()
        self.assertEqual(r["physique"], "NON VERIFIE")
        self.assertEqual(r["outils"][0]["declaration"]["slot_declare"], 4)
        self.dec["outils"] = []
        r = self.audit()
        self.assertIsNone(r["outils"][0]["declaration"])
        self.assertEqual(r["statut"], "NON VERIFIE")

    def test_normal_context_keeps_report_values_and_physical_status(self):
        """La lecture commune conserve le schéma et ne promeut aucun statut physique."""
        result = self.audit()
        brut = json.dumps(self.settings).encode()
        self.assertEqual(result["source_sha256"], hashlib.sha256(self.src.read_bytes()).hexdigest())
        self.assertEqual(result["configuration_sha256"], hashlib.sha256(brut).hexdigest())
        self.assertEqual(result["physique"], "NON VERIFIE")
        self.assertEqual(result["outils"][0]["outil"], "T0")
        self.assertEqual(result["outils"][0]["nozzle_temperature"], 220)
        self.assertEqual(result["outils"][0]["filament_max_volumetric_speed"], 16)
        self.assertEqual(set(result), {
            "schema", "type", "source", "source_sha256", "configuration_sha256", "contexte_sha256",
            "maitre_sha256", "registre_sha256", "outils", "erreurs", "non_verifie", "statut",
            "physique", "compatibilite_lan_s1", "portee", "limites",
        })

    def test_explicit_attestation_exact_context(self):
        r = self.audit()
        self.dec["outils"][0]["confirmation_physique"] = {
            "confirme": True,
            "date": "2026-10-29T10:00:00+00:00",
            "auteur": "témoin fictif",
            "preuve": "contrôle synthétique sans machine",
            "contexte_sha256": r["contexte_sha256"],
        }
        rr = self.audit()
        self.assertEqual(rr["physique"], "ATTESTATION_HUMAINE_DATEE")
        self.settings["filament_colour"] = ["#000000"]
        self.write()
        with self.assertRaises(ValueError):
            self.audit()

    def test_future_or_undated_attestation_refused(self):
        for date in ["2099-01-01T00:00:00+00:00", "2026-10-29"]:
            self.dec["outils"][0]["confirmation_physique"] = None
            r = self.audit()
            self.dec["outils"][0]["confirmation_physique"] = {
                "confirme": True,
                "date": date,
                "auteur": "a",
                "preuve": "b",
                "contexte_sha256": r["contexte_sha256"],
            }
            with self.assertRaises(ValueError):
                self.audit()

    def test_invalid_slot_and_tools(self):
        for value in [True, 0, 5, "1"]:
            self.dec["outils"][0]["slot_declare"] = value
            with self.assertRaises(ValueError):
                self.audit()
        self.dec["outils"][0]["slot_declare"] = 4
        self.dec["outils"][0]["outil"] = "T3"
        with self.assertRaises(ValueError):
            self.audit()

    def test_duplicate_and_unknown_filament(self):
        self.dec["outils"].append(copy.deepcopy(self.dec["outils"][0]))
        with self.assertRaises(ValueError):
            self.audit()
        self.dec["outils"].pop()
        self.dec["outils"][0]["filament_id"] = "inconnu"
        with self.assertRaises(ValueError):
            self.audit()

    def test_material_stock_and_registry_contradictions(self):
        self.settings["filament_type"] = ["PETG"]
        self.write()
        self.assertEqual(self.audit()["statut"], "FAIL")
        self.settings["filament_type"] = ["PLA+"]
        self.write()
        self.reg["filaments"][0]["stock"]["bobines_disponibles"] = 0
        self.assertEqual(self.audit()["statut"], "FAIL")

    def test_temperature_flow_invalid_and_over_limit(self):
        for key, value in [("nozzle_temperature", "400"), ("filament_max_volumetric_speed", "32")]:
            old = self.settings[key]
            self.settings[key] = [value]
            self.write()
            self.assertEqual(self.audit()["statut"], "FAIL")
            self.settings[key] = old
        for value in [True, "NaN", "-1", "0"]:
            self.settings["filament_max_volumetric_speed"] = [value]
            self.write()
            with self.assertRaises(ValueError):
                self.audit()

    def test_missing_values_remain_unknown(self):
        del self.settings["nozzle_temperature"]
        del self.settings["filament_type"]
        self.write()
        r = self.audit()
        self.assertEqual(r["statut"], "NON VERIFIE")
        self.assertIsNone(r["outils"][0]["matiere_projet"])

    def test_wrong_machine_and_nozzle(self):
        self.settings["printer_model"] = "Anycubic Kobra X"
        self.write()
        self.assertEqual(self.audit()["statut"], "FAIL")
        self.settings["printer_model"] = "Anycubic Kobra S1"
        self.settings["nozzle_type"] = "hardened_steel"
        self.write()
        self.assertEqual(self.audit()["statut"], "FAIL")

    def test_ambiguous_archive_and_lengths(self):
        with zipfile.ZipFile(self.src, "a") as z:
            z.writestr("../escape", "x")
        with self.assertRaises(ValueError):
            self.audit()
        self.settings["filament_type"] = ["PLA+", "PETG"]
        self.write()
        with self.assertRaises(ValueError):
            self.audit()

    def test_ignored_member_crc_is_refused_before_audit_result(self):
        with zipfile.ZipFile(self.src, "w") as archive:
            archive.writestr("Metadata/project_settings.config", json.dumps(self.settings))
            archive.writestr("Metadata/notes.bin", b"annexe synthetique")
        _corrompre_crc(self.src, "Metadata/notes.bin")

        with self.assertRaises((ValueError, zipfile.BadZipFile)):
            self.audit()

    def test_zip_alias_of_project_settings_is_refused(self):
        with zipfile.ZipFile(self.src, "w") as archive:
            archive.writestr("Metadata/project_settings.config", json.dumps(self.settings))
            archive.writestr("Metadata//project_settings.config", json.dumps(self.settings))

        with self.assertRaises(ValueError):
            self.audit()

    def test_archive3mf_sha_uses_open_context_and_rejects_replacement(self):
        attendu = hashlib.sha256(self.src.read_bytes()).hexdigest()
        with Archive3MF(self.src) as archive:
            self.assertEqual(archive.sha256(), attendu)
            self.assertEqual(archive.lire("Metadata/project_settings.config"), json.dumps(self.settings).encode())

        with self.assertRaisesRegex(ValueError, "modifi|différent"):
            with Archive3MF(self.src) as archive:
                self.src.write_bytes(self.src.read_bytes() + b"mutation fixture")
                archive.sha256()

    def test_source_change_after_settings_read_refuses_context(self):
        lire = Archive3MF.lire

        def lire_puis_mutation(archive, membre, **options):
            brut = lire(archive, membre, **options)
            self.src.write_bytes(self.src.read_bytes() + b"mutation fixture")
            return brut

        with patch.object(Archive3MF, "lire", lire_puis_mutation):
            with self.assertRaisesRegex(ValueError, "Source différente|modifiée"):
                self.audit()

    def test_cli_json_and_collision_isolated(self):
        dec = self.r / "dec.json"
        dec.write_text(json.dumps(self.dec))
        out = self.r / "audit.json"
        maitre = self.r / "maitre.json"
        maitre.write_text(json.dumps(self.maitre))
        registre = self.r / "registre.json"
        registre.write_text(json.dumps(self.reg))
        cmd = [
            sys.executable,
            "-B",
            "-m",
            "benchab.machine.affectations_ace",
            str(self.src),
            "--declarations",
            str(dec),
            "--json",
            str(out),
            "--maitre",
            str(maitre),
            "--registre",
            str(registre),
        ]
        registre.write_text(json.dumps({"filaments": []}))
        r = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)  # identifiant absent du registre synthétique explicite
        self.assertFalse(out.exists())

    def test_cli_valid_registry_and_output_collision(self):
        self.dec["outils"][0]["filament_id"] = "test"
        self.dec["outils"][0]["slot_declare"] = 2
        dec = self.r / "dec.json"
        dec.write_text(json.dumps(self.dec))
        out = self.r / "audit.json"
        maitre = self.r / "maitre.json"
        maitre.write_text(json.dumps(self.maitre))
        registre = self.r / "registre.json"
        registre.write_text(json.dumps(self.reg))
        cmd = [
            sys.executable,
            "-B",
            "-m",
            "benchab.machine.affectations_ace",
            str(self.src),
            "--declarations",
            str(dec),
            "--json",
            str(out),
            "--maitre",
            str(maitre),
            "--registre",
            str(registre),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        before = out.read_bytes()
        r = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(out.read_bytes(), before)

    def test_nozzle_specific_flow_and_abrasivity(self):
        self.settings["filament_max_volumetric_speed_BRASS"] = ["40"]
        self.write()
        self.assertEqual(self.audit()["statut"], "FAIL")
        self.settings.pop("filament_max_volumetric_speed_BRASS")
        self.write()
        self.reg["filaments"][0]["abrasif"] = True
        self.assertTrue(any("Abrasivité" in s for s in self.audit()["non_verifie"]))

    def test_invalid_project_settings_without_declaration(self):
        self.dec["outils"] = []
        self.settings["nozzle_temperature"] = ["400"]
        self.write()
        self.assertEqual(self.audit()["statut"], "FAIL")
        self.settings["nozzle_temperature"] = ["NaN"]
        self.write()
        with self.assertRaises(ValueError):
            self.audit()


if __name__ == "__main__":
    unittest.main()
