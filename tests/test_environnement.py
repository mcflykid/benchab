"""Contrats du collecteur local sans appel externe."""

import hashlib
import json
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchab.mesure import environnement


class CollecteurEnvironnementTests(unittest.TestCase):
    def test_validation_code_pass_warn_et_imprimante_non_verifiee(self):
        rapport = {
            "code": 1,
            "mesures": {"python": {"version": "3.13"}},
            "resultats": [
                {"controle": "Python", "statut": "PASS"},
                {"controle": "Slicer", "statut": "WARN"},
                {"controle": "Imprimante", "statut": "NON VÉRIFIÉ"},
            ],
        }
        self.assertIs(environnement.verifier_rapport(rapport, code_processus=1), rapport)

    def test_validation_refuse_code_faux_et_rapport_incomplet(self):
        rapport = {"code": 0, "mesures": {"python": "3.13"}, "resultats": [{"controle": "Python", "statut": "PASS"}]}
        with self.assertRaisesRegex(ValueError, "incohérents"):
            environnement.verifier_rapport(rapport, code_processus=1)
        for invalide in (None, {}, {"code": 0, "mesures": {}, "resultats": []}):
            with self.subTest(invalide=invalide), self.assertRaises(ValueError):
                environnement.verifier_rapport(invalide)

    def test_validation_compte_warning_et_fail_et_refuse_statut_inconnu(self):
        for statut, code in (("WARN", 1), ("NON VÉRIFIÉ", 1), ("FAIL", 2)):
            rapport = {
                "code": code,
                "mesures": {"python": True},
                "resultats": [{"controle": "local", "statut": statut}],
            }
            self.assertIs(environnement.verifier_rapport(rapport), rapport)
        with self.assertRaisesRegex(ValueError, "inconnu"):
            environnement.verifier_rapport(
                {"code": 0, "mesures": {"python": True}, "resultats": [{"controle": "local", "statut": "MYSTERE"}]}
            )

    def test_collecteur_ne_lance_aucune_commande_et_ne_lit_pas_les_profils(self):
        configuration = {
            "machine": {"nom": "Machine synthétique"},
            "slicer": {"nom": "Moteur synthétique", "version_qualifiee": "1.0", "executable": "/absent/slicer"},
        }
        with (
            patch.object(environnement, "charger", return_value=configuration),
            patch.object(environnement.importlib.metadata, "version", return_value="1.2"),
            patch.object(environnement.shutil, "disk_usage", return_value=type("Usage", (), {"free": 20 * 1024**3})()),
            patch.object(
                environnement.slicer_runtime,
                "informations",
                return_value={
                    "nom": "Moteur synthétique",
                    "version": "1.0",
                    "build": "test",
                    "present": True,
                    "sha256": "a" * 64,
                },
            ),
            patch.object(environnement, "_information_blender", return_value={"present": False, "raison": "fixture"}),
            patch.object(
                environnement,
                "_profil_systeme",
                return_value={"statut": "NON VÉRIFIÉ", "raison": "profil absent dans le test"},
            ),
        ):
            rapport = environnement.collecter("/private/tmp")
        self.assertEqual(rapport["mesures"]["machine_benchab"], "Machine synthétique")
        self.assertEqual(rapport["mesures"]["paquets"]["build123d"], "1.2")
        self.assertIn("Profils utilisateur slicer", {r["controle"] for r in rapport["resultats"]})
        self.assertEqual(rapport["code"], 1)
        self.assertEqual(rapport["mesures"]["slicer"]["sha256"], "a" * 64)
        self.assertFalse(rapport["mesures"]["blender"]["present"])

    def test_profil_systeme_est_limite_au_bundle_et_sha_attendue(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp", suffix=".app") as temporaire:
            app = Path(temporaire)
            executable = app / "Contents/MacOS/Slicer"
            executable.parent.mkdir(parents=True)
            executable.write_text("binaire fixture")
            profil = app / "Contents/Resources/profiles/machine.json"
            profil.parent.mkdir(parents=True)
            profil.write_text('{"fixture": true}')
            chemin_relatif = f"{app.name}/Contents/Resources/profiles/machine.json"
            digest = hashlib.sha256(profil.read_bytes()).hexdigest()
            config = {
                "slicer": {"executable": str(executable)},
                "mouvement": {"source": chemin_relatif, "source_sha256": digest},
            }
            resultat = environnement._profil_systeme(config)
            self.assertEqual(resultat["statut"], "PASS")
            self.assertEqual(resultat["sha256"], digest)
            config["mouvement"]["source_sha256"] = "0" * 64
            self.assertEqual(environnement._profil_systeme(config)["statut"], "WARN")
            config["mouvement"]["source"] = "../profil.json"
            self.assertEqual(environnement._profil_systeme(config)["statut"], "FAIL")

    def test_information_blender_lit_bundle_sans_execution(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp", suffix=".app") as temporaire:
            app = Path(temporaire)
            executable = app / "Contents/MacOS/Blender"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"fixture executable")
            executable.chmod(0o755)
            info = app / "Contents/Info.plist"
            info.write_bytes(
                plistlib.dumps({"CFBundleShortVersionString": "4.2", "CFBundleVersion": "42"})
            )
            resultat = environnement._information_blender(executable)
        self.assertEqual(resultat["version"], "4.2")
        self.assertEqual(resultat["build"], "42")
        self.assertEqual(resultat["sha256"], hashlib.sha256(b"fixture executable").hexdigest())

    def test_cli_ecrit_sans_ecraser_et_refuse_chemin_exterieur(self):
        temporaire = tempfile.TemporaryDirectory(dir="/private/tmp")
        self.addCleanup(temporaire.cleanup)
        root = Path(temporaire.name)
        destination = root / "environnement.json"
        rapport = {
            "date": "test",
            "resultats": [{"controle": "Python", "statut": "PASS", "detail": "OK"}],
            "mesures": {"python": True},
            "code": 0,
        }
        with patch.object(environnement, "collecter", return_value=rapport):
            self.assertEqual(environnement.main(["--json", str(destination)]), 0)
        self.assertEqual(json.loads(destination.read_text(encoding="utf-8")), rapport)
        with patch.object(environnement, "collecter", return_value=rapport), self.assertRaises(SystemExit) as erreur:
            environnement.main(["--json", str(destination)])
        self.assertEqual(erreur.exception.code, 2)
        forbidden = Path.cwd() / ".environnement-test-interdit.json"
        self.addCleanup(forbidden.unlink, missing_ok=True)
        with patch.object(environnement, "collecter", return_value=rapport), self.assertRaises(SystemExit):
            environnement.main(["--json", str(forbidden)])
        self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()


def test_slicer_absent_fail_et_sha_sans_reference_jamais_pass():
    """Contrôle présence/version/SHA passifs ; absent FAIL, sans référence ou SHA différent WARN, aucune exécution."""
    import copy

    configuration = {
        "machine": {"nom": "Machine fixture"},
        "slicer": {"nom": "Moteur fixture", "version_qualifiee": "1.0"},
    }
    scenarios = (
        (False, "a" * 64, "a" * 64, "FAIL"),
        (True, "a" * 64, None, "WARN"),
        (True, "a" * 64, "b" * 64, "WARN"),
        (True, "a" * 64, "a" * 64, "PASS"),
    )
    for present, sha, reference, attendu in scenarios:
        config = copy.deepcopy(configuration)
        if reference:
            config["slicer"]["sha256_binaire_matrices_origine"] = reference
        with (
            patch.object(environnement, "charger", return_value=config),
            patch.object(environnement.importlib.metadata, "version", return_value="fixture"),
            patch.object(environnement, "controler_runtime", return_value={"statut": "PASS"}),
            patch.object(environnement.slicer_runtime, "informations", return_value={
                "nom": "Moteur fixture", "present": present, "sha256": sha, "version": "1.0",
            }),
            patch.object(environnement, "_information_blender", return_value={"present": False}),
            patch.object(environnement, "_profil_systeme", return_value={"statut": "NON VÉRIFIÉ"}),
        ):
            rapport = environnement.collecter()
        ligne = next(r for r in rapport["resultats"] if r["controle"] == "Moteur fixture")
        assert ligne["statut"] == attendu


def test_module_commun_absent_fail_sans_lancer_outil(tmp_path):
    """Une copie de paquet incomplète est FAIL ; la présence du module ne prouve aucune exécution ni impression."""
    for relatif in environnement.OUTILS_COMMUNS:
        cible = tmp_path / relatif
        cible.parent.mkdir(parents=True, exist_ok=True)
        cible.write_text("raise AssertionError('ne jamais importer la fixture')\n")
    assert environnement._outils_communs(tmp_path)["statut"] == "PASS"
    absent = environnement.OUTILS_COMMUNS[0]
    (tmp_path / absent).unlink()
    rapport = environnement._outils_communs(tmp_path)
    assert rapport["statut"] == "FAIL"
    assert rapport["modules_absents"] == [absent]
