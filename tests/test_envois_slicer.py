"""Archives synthétiques et dossiers temporaires ; aucune imprimante."""

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from benchab.fabrication import envois_slicer as E


def envoi(dossier, nom, temp="210", hs="210", utilise="0.00, 12.50", contenu="G1 X1 E1\n", buse=None, brass=None):
    p = Path(dossier) / "9527" / nom
    p.parent.mkdir(parents=True, exist_ok=True)
    st = {
        "filament_settings_id": ["A", "B"],
        "nozzle_temperature": [temp],
        "nozzle_temperature_HS": [hs],
        "filament_flow_ratio": ["0.98"],
    }
    if buse:
        st["nozzle_type"] = buse
    if brass:
        st["nozzle_temperature_BRASS"] = [brass]
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("Metadata/project_settings.config", json.dumps(st))
        z.writestr("Metadata/plate_1.gcode", f"; filament used [g] = {utilise}\nT1\n{contenu}")
    return p


class Envois(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.t = Path(t.name).resolve()
        self.src = self.t / "ACGcode3mf"
        self.arc = self.t / "archive"

    def test_lecture_des_reglages_et_de_la_date(self):
        e = E.lire(envoi(self.src, "1024-1139-Arrosoir(01)_PLA_0.2_9h15m9s.gcode.3mf"))
        self.assertEqual(e["envoye_le"], "24/10 11:39")
        self.assertEqual(e["nom"], "Arrosoir(01)_PLA_0.2_9h15m9s")
        self.assertEqual(e["reglages"]["nozzle_temperature"], ["210"])
        self.assertEqual(e["filaments_utilises"], [1])
        self.assertEqual(e["filament_utilise_g"], [0, 12.5])
        self.assertFalse(e["temperature_ambigue"])

    def test_empreinte_inventaire_depuis_le_3mf_deja_ouvert(self):
        """L’inventaire hache une seule fois les octets du lecteur déjà ouvert."""
        p = envoi(self.src, "1023-1809-Temoin.gcode.3mf")
        avant = E.sha(p)
        tailles = []
        empreinte = E.Archive3MF.sha256

        def compter(archive, **options):
            """Conserve la vraie lecture et trace le nombre de hachages."""
            resultat = empreinte(archive, **options)
            tailles.append(resultat)
            return resultat

        with patch.object(E.Archive3MF, "sha256", compter), patch.object(
            E, "sha", side_effect=AssertionError("Hachage par réouverture du chemin")
        ):
            rapport = E.lire(p)
        self.assertEqual(tailles, [avant])
        self.assertEqual(rapport["sha256"], avant)
        self.assertEqual(rapport["filament_utilise_g"], [0, 12.5])
        self.assertEqual(rapport["octets"], p.stat().st_size)
        self.assertEqual(E.sha(p), avant)

    def test_variante_hs_differente_ambigue_seulement_sans_type_de_buse(self):
        self.assertTrue(
            E.lire(envoi(self.src, "1023-1809-Pomme.gcode.3mf", temp="210", hs="220"))["temperature_ambigue"]
        )

    def test_temperature_appliquee_selon_la_buse_declaree(self):
        e = E.lire(envoi(self.src, "1023-1809-Pomme.gcode.3mf", temp="210", hs="220", buse="brass", brass="210"))
        self.assertFalse(e["temperature_ambigue"])
        self.assertEqual(e["temperature_appliquee"]["suite"], ["210"])
        e = E.lire(
            envoi(self.src, "1023-1810-Pomme.gcode.3mf", temp="210", hs="220", buse="hardened_steel", brass="210")
        )
        self.assertEqual(e["temperature_appliquee"]["suite"], ["220"])

    def test_archivage_sans_doublon_et_sans_toucher_la_source(self):
        p = envoi(self.src, "1023-1010-Charniere.gcode.3mf")
        avant = E.sha(p)
        c, d, e = E.archiver(self.src, self.arc)
        self.assertEqual((len(c), d, e), (1, 0, []))
        c, d, e = E.archiver(self.src, self.arc)
        self.assertEqual((len(c), d, e), (0, 1, []))
        self.assertEqual(E.sha(p), avant)
        self.assertTrue(p.exists())
        self.assertEqual(len(json.loads((self.arc / "ENVOIS.json").read_text())), 1)

    def test_meme_nom_autre_contenu_jamais_ecrase(self):
        envoi(self.src, "1023-1010-Charniere.gcode.3mf")
        E.archiver(self.src, self.arc)
        envoi(self.src, "1023-1010-Charniere.gcode.3mf", contenu="G1 X2 E2\n")
        c, _, _ = E.archiver(self.src, self.arc)
        self.assertEqual(len(c), 1)
        self.assertNotEqual(c[0]["archive"], "1023-1010-Charniere.gcode.3mf")
        self.assertEqual(len(list(self.arc.glob("*.gcode.3mf"))), 2)

    def test_fichier_illisible_signale_sans_arreter_les_autres(self):
        bad = self.src / "9527" / "casse.gcode.3mf"
        bad.parent.mkdir(parents=True)
        bad.write_text("pas un zip")
        envoi(self.src, "1023-1237-Vis.gcode.3mf")
        c, d, e = E.archiver(self.src, self.arc)
        self.assertEqual(len(c), 1)
        self.assertEqual(len(e), 1)

    def test_source_absente(self):
        self.assertEqual(E.archiver(self.t / "absent", self.arc), ([], 0, []))


class RegressionsArchive(unittest.TestCase):
    setUp = Envois.setUp

    def test_missing_archived_file_is_restored(self):
        envoi(self.src, "test.gcode.3mf")
        copies, _, _ = E.archiver(self.src, self.arc)
        (self.arc / copies[0]["archive"]).unlink()
        copies, deja, errors = E.archiver(self.src, self.arc)
        self.assertEqual(len(copies), 1)
        self.assertEqual(deja, 0)
        self.assertTrue((self.arc / copies[0]["archive"]).exists())

    def test_corrupted_archive_preserved_and_source_copied_again(self):
        envoi(self.src, "test.gcode.3mf")
        copies, _, _ = E.archiver(self.src, self.arc)
        bad = self.arc / copies[0]["archive"]
        bad.write_bytes(b"corrupted user file")
        copies, deja, errors = E.archiver(self.src, self.arc)
        self.assertEqual(len(copies), 1)
        self.assertEqual(deja, 0)
        self.assertEqual(bad.read_bytes(), b"corrupted user file")

    def test_missing_registry_does_not_duplicate_physical_archive(self):
        envoi(self.src, "test.gcode.3mf")
        E.archiver(self.src, self.arc)
        (self.arc / "ENVOIS.json").unlink()
        copies, deja, errors = E.archiver(self.src, self.arc)
        self.assertEqual((len(copies), deja), (0, 1))
        self.assertEqual(len(list(self.arc.glob("*.gcode.3mf"))), 1)

    def test_secondary_collision_never_overwritten(self):
        p = envoi(self.src, "test.gcode.3mf")
        E.archiver(self.src, self.arc)
        envoi(self.src, "test.gcode.3mf", contenu="G1 X999 E999")
        collision = self.arc / f"test-{E.sha(p)[:8]}.gcode.3mf"
        collision.write_bytes(b"user secondary")
        E.archiver(self.src, self.arc)
        self.assertEqual(collision.read_bytes(), b"user secondary")

    def test_copy_failure_preserves_user_destination(self):
        envoi(self.src, "test.gcode.3mf")
        self.arc.mkdir()
        existing = self.arc / "test.gcode.3mf"
        existing.write_bytes(b"user primary")
        with patch.object(E.shutil, "copy2", side_effect=OSError("copy failed")):
            with self.assertRaises(OSError):
                E.archiver(self.src, self.arc)
        self.assertEqual(existing.read_bytes(), b"user primary")


class RobustesseArchive(unittest.TestCase):
    setUp = Envois.setUp

    def test_corrupt_registry_preserved_without_new_copy(self):
        envoi(self.src, "test.gcode.3mf")
        self.arc.mkdir()
        fiche = self.arc / "ENVOIS.json"
        fiche.write_bytes(b"{broken")
        with self.assertRaisesRegex(ValueError, "ENVOIS.json"):
            E.archiver(self.src, self.arc)
        self.assertEqual(fiche.read_bytes(), b"{broken")
        self.assertFalse(list(self.arc.glob("*.gcode.3mf")))
        self.assertFalse((self.arc / ".archivage.lock").exists())

    def test_missing_file_restoration_keeps_history_without_duplicate_row(self):
        envoi(self.src, "test.gcode.3mf")
        copies, _, _ = E.archiver(self.src, self.arc)
        (self.arc / copies[0]["archive"]).unlink()
        E.archiver(self.src, self.arc)
        report = json.loads((self.arc / "ENVOIS.json").read_text())
        self.assertEqual(len(report), 1)
        self.assertEqual(report[0]["etat_archive"], "verifiee")
        self.assertIn("absente", report[0]["historique_etats"])
        self.assertEqual(E.archiver(self.src, self.arc)[1], 1)

    def test_stale_record_without_source_explicit(self):
        p = envoi(self.src, "test.gcode.3mf")
        copies, _, _ = E.archiver(self.src, self.arc)
        (self.arc / copies[0]["archive"]).unlink()
        p.unlink()
        copies, deja, errors = E.archiver(self.src, self.arc)
        self.assertEqual((copies, deja), ([], 0))
        self.assertTrue(any("absente" in e for e in errors))
        report = json.loads((self.arc / "ENVOIS.json").read_text())
        self.assertEqual(report[0]["etat_archive"], "absente")

    def test_two_sources_same_bytes_single_physical_copy(self):
        p = envoi(self.src, "a.gcode.3mf")
        other = self.src / "different/b.gcode.3mf"
        other.parent.mkdir()
        other.write_bytes(p.read_bytes())
        copies, deja, errors = E.archiver(self.src, self.arc)
        self.assertEqual((len(copies), deja, errors), (1, 1, []))
        self.assertEqual(len(list(self.arc.glob("*.gcode.3mf"))), 1)

    def test_register_replace_failure_preserves_previous_and_recovers_complete_copy(self):
        envoi(self.src, "a.gcode.3mf")
        E.archiver(self.src, self.arc)
        before = (self.arc / "ENVOIS.json").read_bytes()
        envoi(self.src, "b.gcode.3mf", contenu="G1 X2 E2")
        with patch.object(E.os, "replace", side_effect=OSError("replace failed")):
            with self.assertRaises(OSError):
                E.archiver(self.src, self.arc)
        self.assertEqual((self.arc / "ENVOIS.json").read_bytes(), before)
        copies, deja, errors = E.archiver(self.src, self.arc)
        self.assertEqual((copies, deja, errors), ([], 2, []))
        self.assertEqual(len(list(self.arc.glob("*.gcode.3mf"))), 2)

    def test_existing_lock_never_forced(self):
        envoi(self.src, "a.gcode.3mf")
        self.arc.mkdir()
        lock = self.arc / ".archivage.lock"
        lock.mkdir()
        (lock / "user").write_text("preserve")
        with self.assertRaises(FileExistsError):
            E.archiver(self.src, self.arc)
        self.assertEqual((lock / "user").read_text(), "preserve")

    def test_symlink_registry_refused(self):
        envoi(self.src, "a.gcode.3mf")
        self.arc.mkdir()
        user = self.t / "user"
        user.write_text("preserve")
        (self.arc / "ENVOIS.json").symlink_to(user)
        with self.assertRaises(ValueError):
            E.archiver(self.src, self.arc)
        self.assertEqual(user.read_text(), "preserve")

    def test_malformed_settings_skipped(self):
        p = envoi(self.src, "a.gcode.3mf")
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("Metadata/project_settings.config", "[]")
            z.writestr("Metadata/plate_1.gcode", "G1 X1 E1")
        copies, deja, errors = E.archiver(self.src, self.arc)
        self.assertEqual((copies, deja), ([], 0))
        self.assertTrue(errors)


class EtatFinalArchive(unittest.TestCase):
    setUp = Envois.setUp

    def test_missing_old_name_now_occupied_by_different_digest_marked_corrupt(self):
        envoi(self.src, "test.gcode.3mf")
        copies, _, _ = E.archiver(self.src, self.arc)
        (self.arc / copies[0]["archive"]).unlink()
        envoi(self.src, "test.gcode.3mf", contenu="G1 X999 E999")
        E.archiver(self.src, self.arc)
        report = json.loads((self.arc / "ENVOIS.json").read_text())
        self.assertEqual(report[0]["etat_archive"], "corrompue")
        self.assertEqual(report[-1]["etat_archive"], "verifiee")
        self.assertIn("absente", report[0]["historique_etats"])


class AuditMultiPlaques(unittest.TestCase):
    setUp = Envois.setUp


    def test_toutes_plaques_masses_et_outils(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = envoi(tmp, "multi.gcode.3mf", utilise="1.00, 2.00")
            with zipfile.ZipFile(p, "a") as z:
                z.writestr("Metadata/plate_2.gcode", "; filament used [g] = 3, 4, 5\nT2\nG1 X1 E1\n")
            d = E.lire(p)
            self.assertEqual(d["filament_utilise_g"], [4.0, 6.0, 5.0])
            self.assertEqual(d["filaments_utilises"], [0, 1, 2])
            self.assertEqual(len(d["details_plaques"]), 2)

    def test_plaque_secondaire_negative_refusee(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = envoi(tmp, "multi.gcode.3mf")
            with zipfile.ZipFile(p, "a") as z:
                z.writestr("Metadata/plate_2.gcode", "; filament used [g] = -1\n")
            with self.assertRaises(ValueError):
                E.lire(p)

    def test_alias_de_numero_de_plaque_refuse(self):
        """Deux noms ZIP représentant la plaque 1 ne donnent pas deux consommations."""
        p = envoi(self.src, "ambigu.gcode.3mf")
        with zipfile.ZipFile(p, "a") as z:
            z.writestr("Metadata/plate_01.gcode", b"; filament used [g] = 1.0\n")
        with self.assertRaisesRegex(ValueError, "numéro de plaque dupliqué"):
            E.lire(p)
        copies, deja, erreurs = E.archiver(self.src, self.arc)
        self.assertEqual((copies, deja), ([], 0))
        self.assertIn("numéro de plaque dupliqué", erreurs[0])
        self.assertEqual(list(self.arc.glob("*.gcode.3mf")), [])

    def test_archive_complete_crc_requis_meme_pour_un_membre_non_analyse(self):
        """Un aperçu endommagé ne peut pas être archivé comme une source contrôlée."""
        for index, membre in enumerate(("Metadata/apercu.txt", "Metadata/annexe/")):
            with self.subTest(membre=membre):
                p = envoi(self.src, f"crc-{index}.gcode.3mf")
                contenu = b"archive_apercu_jetable"
                with zipfile.ZipFile(p, "a") as z:
                    z.writestr(membre, contenu, compress_type=zipfile.ZIP_STORED)
                data = p.read_bytes()
                assert data.count(contenu) == 1
                p.write_bytes(data.replace(contenu, b"x" + contenu[1:]))
                with self.assertRaises(ValueError):
                    E.lire(p)

    def test_masses_et_outils_lus_en_flux_avec_decodage_historique(self):
        """Les octets ignorés et la première mesure restent traités comme avant."""
        p = envoi(self.src, "flux.gcode.3mf", utilise=None)
        copie = p.with_name("flux-octets.gcode.3mf")
        with zipfile.ZipFile(p) as source, zipfile.ZipFile(copie, "w") as dest:
            dest.writestr("Metadata/project_settings.config", source.read("Metadata/project_settings.config"))
            dest.writestr("Metadata/plate_1.gcode", b" \xffT2 ; outil\r\n; filament used [g] = 0, 2\r\n"
                          b"; filament used [g] = 0, 999\n")
        rapport = E.lire(copie)
        self.assertEqual(rapport["filaments_utilises"], [1, 2])
        self.assertEqual(rapport["filament_utilise_g"], [0.0, 2.0])

    def test_masse_manquante_pas_de_total_partiel(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = envoi(tmp, "multi.gcode.3mf")
            with zipfile.ZipFile(p, "a") as z:
                z.writestr("Metadata/plate_2.gcode", "T2\nG1 X2 E1\n")
            self.assertIsNone(E.lire(p)["filament_utilise_g"])


if __name__ == "__main__":
    unittest.main()
