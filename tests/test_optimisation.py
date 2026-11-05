"""Régressions synthétiques des propositions de réglages de fabrication."""

import copy
import hashlib
import json
import os
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

import trimesh

from benchab.donnees.base import Base
from benchab.fabrication import anycubic, optimisation


def fait(value, unit, kind="configuration", source_ids=("atelier",)):
    return {
        "value": value,
        "unit": unit,
        "type": kind,
        "source_ids": list(source_ids),
        "date_reference": "2026-11-04",
        "version_reference": "fixture-v1",
        "confidence": "donnée synthétique",
        "conditions": "générée pour le test",
        "verification_status": "NON_VERIFIE",
    }


def profil():
    return {
        "schema": 1,
        "version": 1,
        "profil_id": "machine-fixture",
        "configuration_materielle_id": "kobra-fixture",
        "sources": {
            "atelier": {
                "sha256": "0" * 64,
                "date_reference": "2026-11-04",
                "origine": "fixture générée dans BENCHAB",
            }
        },
        "machine": {
            "modele": fait("Anycubic Kobra S1 Combo", "texte"),
            "cinematique": fait("corexy", "texte"),
            "volume_mm": fait([250, 250, 250], "mm"),
            "buse_mm": fait(0.4, "mm"),
            "buse_matiere": fait("brass", "texte"),
            "chargeur": fait("ACE fixture", "texte"),
        },
        "configuration_par_defaut": "configuration-fixture",
        "configurations": {
            "configuration-fixture": {
                "disponible": True,
                "snapshot_source_id": "atelier",
                "parametres": {
                    "machine_max_speed_x": fait([300, 280], "mm/s"),
                    "machine_max_speed_y": fait([300, 280], "mm/s"),
                    "machine_max_speed_z": fait([20], "mm/s"),
                    "machine_max_speed_e": fait([40], "mm/s"),
                    "machine_max_acceleration_x": fait([8000], "mm/s2"),
                    "machine_max_acceleration_y": fait([8000], "mm/s2"),
                    "machine_max_acceleration_z": fait([500], "mm/s2"),
                    "machine_max_acceleration_e": fait([5000], "mm/s2"),
                    "machine_max_acceleration_extruding": fait([5000], "mm/s2"),
                    "machine_max_acceleration_travel": fait([5000], "mm/s2"),
                    "machine_max_acceleration_retracting": fait([2000], "mm/s2"),
                    "nozzle_diameter": fait([0.4], "mm"),
                    "printable_height": fait(250, "mm"),
                },
            }
        },
        "constructeur": {
            "acceleration_recommandee_mm_s2": fait(6000, "mm/s2", "constructeur"),
            "acceleration_max_mm_s2": fait(10000, "mm/s2", "constructeur"),
            "vitesse_max_mm_s": fait(600, "mm/s", "constructeur"),
        },
    }


def profil_avec_snapshots(dossier):
    dossier.mkdir(parents=True, exist_ok=True)
    donnees = profil()
    valeurs = {
        cle: fait["value"] for cle, fait in donnees["configurations"]["configuration-fixture"]["parametres"].items()
    }
    configuration = dossier / "configuration.json"
    configuration.write_text(json.dumps({"valeurs": valeurs}), encoding="utf-8")
    donnees["sources"]["atelier"].update(
        snapshot="configuration.json",
        sha256_snapshot=hashlib.sha256(configuration.read_bytes()).hexdigest(),
    )
    preuve = dossier / "references/profil-exporte.json"
    preuve.parent.mkdir(parents=True)
    preuve.write_text('{"vitesse":60}', encoding="utf-8")
    donnees["sources"]["reference"] = {
        "sha256": "1" * 64,
        "date_reference": "2026-11-04",
        "origine": "profil exporté synthétique",
        "snapshot": "references/profil-exporte.json",
        "sha256_snapshot": hashlib.sha256(preuve.read_bytes()).hexdigest(),
    }
    donnees["_dossier_sources"] = str(dossier)
    return donnees


class OptimisationTests(unittest.TestCase):
    def setUp(self):
        temporaire = tempfile.TemporaryDirectory(dir="/private/tmp")
        self.addCleanup(temporaire.cleanup)
        self.base = Base(Path(temporaire.name) / "prive")
        self.addCleanup(self.base.fermer)
        self.projet = self.base.prive / "projets/fixture"
        self.projet.mkdir(parents=True)
        self.source = self.projet / "source.3mf"
        self.reglages = {
            "printer_model": "Anycubic Kobra S1 Combo",
            "printer_settings_id": "Anycubic Kobra S1 0.4 nozzle",
            "machine_max_speed_x": ["600", "300", "780"],
            "nozzle_type": "brass",
            "nozzle_diameter": ["0.4"],
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
            "line_width": "0.42",
            "outer_wall_line_width": "0.42",
            "inner_wall_line_width": "0.42",
            "sparse_infill_line_width": "0.42",
            "internal_solid_infill_line_width": "0.42",
            "top_surface_line_width": "0.42",
            "gap_infill_line_width": "0.42",
            "outer_wall_speed": "60",
            "inner_wall_speed": "80",
            "sparse_infill_speed": "80",
            "internal_solid_infill_speed": "80",
            "top_surface_speed": "50",
            "gap_infill_speed": "40",
            "travel_speed": "300",
            "default_acceleration": "6000",
            "inner_wall_acceleration": "6000",
            "travel_acceleration": "5000",
        }
        self.fabrication = {
            "schema": 1,
            "objectif": "equilibre",
            "filaments": {"1": {"id": "filament-fixture", "ace_declare": 1}},
            "contraintes": {"objets": {}},
        }
        self.creer()
        self.base.ecrire(
            "INSERT INTO filaments (id, nom, matiere, fabricant, donnees_json) VALUES (?, ?, ?, ?, ?)",
            ("filament-fixture", "Filament fixture", "PLA", "Atelier", json.dumps({"stock": {"etat": "disponible"}})),
        )

    def creer(self):
        maillage = trimesh.creation.box(extents=(40, 30, 12))
        maillage.apply_translation((20, 15, 6))
        anycubic.ecrire_3mf(
            [
                {
                    "name": "plaque",
                    "items": [{"name": "Cube", "mesh": maillage, "x": 10, "y": 10, "extruder": 1, "settings": {}}],
                }
            ],
            self.reglages,
            self.source,
        )
        (self.projet / "FABRICATION.json").write_text(json.dumps(self.fabrication), encoding="utf-8")

    def proposer(self, objectif=None):
        return optimisation.proposer(self.base, self.projet, self.source, objectif, profil())

    def test_objectifs_et_geometrie(self):
        rapports = {mode: self.proposer(mode) for mode in optimisation.MODES}
        self.assertNotIn("outer_wall_speed", rapports["qualite"]["reglages"]["objets"][0]["reglages"])
        self.assertGreater(float(rapports["equilibre"]["reglages"]["objets"][0]["reglages"]["outer_wall_speed"]), 60)
        self.assertTrue(rapports["rapide"]["geometrie"]["mesures"][0]["ferme"])
        self.assertTrue(any("résistance" in alerte for alerte in rapports["resistance"]["alertes"]))

    def test_main_argv_proposer_ne_modifie_pas_sys_argv(self):
        maitre_path = self.base.prive / "machine/profil.json"
        maitre_path.parent.mkdir(parents=True)
        maitre = profil_avec_snapshots(maitre_path.parent)
        maitre_path.write_text(
            json.dumps({cle: valeur for cle, valeur in maitre.items() if not cle.startswith("_")}), encoding="utf-8"
        )
        machine_config = self.base.prive / "machine.toml"
        machine_config.write_text(f'[machine]\nprofil_maitre = "{maitre_path}"\n', encoding="utf-8")
        anciens_arguments = ["pytest", "arguments-appelant"]
        sortie = StringIO()
        with patch.dict(os.environ, {"BENCHAB_MACHINE": str(machine_config)}):
            with patch("sys.argv", anciens_arguments), redirect_stdout(sortie):
                code = optimisation.main(
                    [
                        "--prive",
                        str(self.base.prive),
                        "proposer",
                        "--projet",
                        str(self.projet),
                        "--source",
                        str(self.source),
                    ]
                )
                self.assertEqual(sys.argv, anciens_arguments)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(sortie.getvalue())["objectif"], "equilibre")

    def test_verrou_objet_et_protection_preservent_reglages_fins(self):
        self.fabrication["contraintes"]["objets"]["Cube"] = {
            "fonctionnelle": True,
            "details_fins": True,
            "reglages_verrouilles": ["sparse_infill_speed", "travel_speed"],
            "acceleration_max_mm_s2": 4000,
        }
        self.creer()
        proposition = self.proposer("rapide")
        reglages = proposition["reglages"]["objets"][0]["reglages"]
        self.assertNotIn("outer_wall_speed", reglages)
        self.assertNotIn("sparse_infill_speed", reglages)
        self.assertNotIn("travel_speed", proposition["reglages"]["global"])
        self.assertLessEqual(float(reglages["default_acceleration"]), 4000)

    def test_filaments_multiples_conservent_plafonds_et_identifiants_physiques(self):
        self.reglages.update(
            filament_settings_id=["Filament fixture", "Second fixture"],
            filament_ids=["filament-fixture", "second-fixture"],
            filament_diameter=["1.75", "1.75"],
            filament_max_volumetric_speed=["12", "32"],
            filament_max_volumetric_speed_BRASS=["12", "32"],
            nozzle_temperature_BRASS=["220", "240"],
            nozzle_temperature_initial_layer_BRASS=["220", "220"],
        )
        self.fabrication["filaments"]["2"] = {"id": "second-fixture", "ace_declare": 2}
        self.creer()
        self.base.ecrire(
            "INSERT INTO filaments (id, nom, matiere, fabricant, donnees_json) VALUES (?, ?, ?, ?, ?)",
            ("second-fixture", "Second fixture", "PLA", "Atelier", json.dumps({"stock": {"etat": "épuisé"}})),
        )
        rapport = self.proposer()
        self.assertEqual(rapport["filaments"]["2"]["plafond_mm3_s"], 32)
        self.assertEqual(rapport["filaments"]["2"]["temperature_c"], 240)
        self.assertEqual(rapport["filaments"]["2"]["ace_declare"], 2)
        self.assertFalse(rapport["filaments"]["2"]["ace_physique_verifie"])
        self.assertTrue(any("épuisé" in alerte for alerte in rapport["alertes"]))

    def test_filament_synthetique_abrasif_est_signale_sans_modifier_buse(self):
        self.base.ecrire(
            "INSERT INTO filaments (id, nom, matiere, fabricant, donnees_json) VALUES (?, ?, ?, ?, ?)",
            (
                "glow-fixture",
                "Filament Luminous fixture",
                "PLA",
                "Fabricant fixture",
                json.dumps(
                    {
                        "nom": "Filament Luminous fixture",
                        "fabricant": "Fabricant fixture",
                        "stock": {"etat": "disponible"},
                    }
                ),
            ),
        )
        self.fabrication["filaments"]["1"]["id"] = "glow-fixture"
        self.creer()
        rapport = self.proposer()
        self.assertTrue(any("abrasive" in alerte.lower() for alerte in rapport["alertes"]))
        self.assertEqual(self.reglages["nozzle_type"], "brass")

    def test_source_non_privee_machine_incompatible_et_reglage_invalide_refuses(self):
        with self.assertRaisesRegex(ValueError, "base.prive"):
            optimisation.proposer(self.base, self.projet, Path("/private/tmp/absent.3mf"), maitre=profil())
        original = self.reglages["printer_model"]
        self.reglages["printer_model"] = "Autre machine"
        self.creer()
        with self.assertRaisesRegex(ValueError, "Kobra S1"):
            self.proposer()
        self.reglages["printer_model"] = original
        self.reglages["filament_max_volumetric_speed_BRASS"] = ["nan"]
        self.creer()
        with self.assertRaisesRegex(ValueError, "nombre fini"):
            self.proposer()

    def test_contour_machine_reglage_buse_et_contrainte_incompatibles_refuses(self):
        for cle, valeur, message in (
            ("printer_model", "Autre machine", "Kobra S1"),
            ("printable_height", "300", "incompatible"),
            ("printable_area", ["0x0", "300x0", "300x250", "0x250"], "incompatible"),
        ):
            with self.subTest(cle=cle):
                self.reglages["printer_model"] = "Anycubic Kobra S1 Combo"
                self.reglages["printable_height"] = "250"
                self.reglages["printable_area"] = ["0x0", "250x0", "250x250", "0x250"]
                self.reglages[cle] = valeur
                self.creer()
                with self.assertRaisesRegex(ValueError, message):
                    self.proposer()
        self.reglages["printable_area"] = ["0x0", "250x0", "250x250", "0x250"]
        self.reglages["nozzle_diameter"] = ["0.8"]
        self.creer()
        with self.assertRaisesRegex(ValueError, "buse"):
            self.proposer()
        self.reglages["nozzle_diameter"] = ["0.4"]
        self.fabrication["contraintes"]["objets"]["Absent"] = {}
        self.creer()
        with self.assertRaisesRegex(ValueError, "objet absent"):
            self.proposer()

    def test_mode_maitre_indexe_est_respecte(self):
        self.fabrication["machine"] = {"mode_index": 1}
        self.creer()
        machine = profil()
        for cle, parametre in machine["configurations"]["configuration-fixture"]["parametres"].items():
            if cle.startswith("machine_max_") and isinstance(parametre["value"], list) and len(parametre["value"]) == 1:
                parametre["value"].append(parametre["value"][0])
        rapport = optimisation.proposer(self.base, self.projet, self.source, maitre=machine)
        self.assertEqual(rapport["machine"]["limites"]["vitesse_xy_mm_s"], 280)
        self.assertEqual(rapport["machine"]["limites"]["sources"]["vitesse_xy_mm_s"]["mode_index"], 1)

    def test_clone_preserve_geometrie_et_refuse_parametre_hors_allowlist(self):
        source_initiale = self.source.read_bytes()
        proposition = self.proposer()
        copie = self.projet / "copie.3mf"
        _, rapport = optimisation.clone(self.source, copie, proposition)
        self.assertTrue(rapport["conforme"])
        self.assertEqual(self.source.read_bytes(), source_initiale)
        with self.assertRaises(ValueError):
            optimisation.clone(self.source, copie, proposition)
        proposition["reglages"]["global"]["layer_height"] = "0.3"
        with self.assertRaisesRegex(ValueError, "allowlist"):
            optimisation.clone(self.source, self.projet / "interdite.3mf", proposition)
        self.assertFalse((self.projet / "interdite.3mf").exists())

    def test_clone_refuse_une_mutation_3mf_non_declaree(self):
        proposition = self.proposer()
        copie = self.projet / "mutation.3mf"
        tostring_original = ET.tostring

        def ajouter_reglage_non_declare(element, *args, **kwargs):
            racine = ET.fromstring(tostring_original(element, *args, **kwargs))
            objet = racine.find("object")
            if objet is not None:
                presents = {meta.get("key") for meta in objet.findall("metadata")}
                cle = next(
                    cle for cle in ("bottom_shell_layers", "retraction_length", "seam_position") if cle not in presents
                )
                ET.SubElement(objet, "metadata", key=cle, value="999")
            return tostring_original(racine, *args, **kwargs)

        with patch.object(optimisation.ET, "tostring", side_effect=ajouter_reglage_non_declare):
            with self.assertRaisesRegex(ValueError, "Intégrité"):
                optimisation.clone(self.source, copie, proposition)
        self.assertFalse(copie.exists())

    def test_clone_preserve_vecteur_machine_trois_modes(self):
        proposition = self.proposer()
        copie = self.projet / "vecteur-machine.3mf"
        optimisation.clone(self.source, copie, proposition)
        with zipfile.ZipFile(copie) as archive:
            resultat = json.loads(archive.read(optimisation.CONFIGURATION))
        self.assertEqual(resultat["machine_max_speed_x"], ["600", "300", "780"])

    def test_valeur_indexee_refuse_un_axe_machine_comme_filament(self):
        reglages = optimisation.reglages_types.Reglages(
            {"machine_max_speed_x": ["600", "300", "780"], "filament_settings_id": ["PLA"]}
        )
        with self.assertRaisesRegex(ValueError, "pas un vecteur filament typé"):
            optimisation._valeur_indexee(reglages, "machine_max_speed_x", 2)

    def test_decision_exige_les_controles_indispensables(self):
        capacites = {"statut": "PASS", "decision": "GO"}
        controles = {
            "routine": {"statut": "PASS"},
            "premiere_couche": {"1": {"statut": "PASS"}},
            "zones_locales": {"1": {"statut": "PASS"}},
            "resistance": {"obligatoire": False, "rapport": {"statut": "WARN"}},
            "geometrie": {"statut": "PASS"},
        }
        self.assertEqual(optimisation.decision_candidat(capacites, {"routine": {"statut": "PASS"}}, [])[0], "À revoir")
        self.assertEqual(optimisation.decision_candidat(capacites, controles, []), ("GO", "PASS"))
        controles["premiere_couche"]["1"]["statut"] = "NON VÉRIFIÉ"
        self.assertNotEqual(optimisation.decision_candidat(capacites, controles, [])[0], "GO")
        controles["routine"]["statut"] = "FAIL"
        self.assertEqual(optimisation.decision_candidat(capacites, controles, [])[0], "NO GO")

    def test_comparaison_sans_tranchage_cree_une_etude_scellee_non_go(self):
        rapport = optimisation.comparer(self.base, self.projet, self.source, maitre=profil(), trancher=False)
        self.assertEqual(rapport["decision"], "À revoir")
        self.assertEqual(rapport["statut"], "WARN")
        self.assertIsNone(rapport["gain_pct"])
        self.assertEqual(len(rapport["candidats"]), 2)
        self.assertTrue(Path(rapport["rapport"]).is_file())
        self.assertEqual(
            rapport["source"]["sha256"],
            optimisation.sha256_fichier(Path(rapport["source"]["fichier"])),
        )

    def test_cache_reutilise_uniquement_rapport_et_preuves_intacts(self):
        premier = optimisation.comparer(self.base, self.projet, self.source, maitre=profil(), trancher=False)
        second = optimisation.comparer(self.base, self.projet, self.source, maitre=profil(), trancher=False)
        self.assertTrue(second["cache_reutilise"])
        self.assertEqual(premier["empreinte_cache"], second["empreinte_cache"])
        rapport = Path(premier["rapport"])
        donnees = json.loads(rapport.read_text(encoding="utf-8"))
        donnees["decision"] = "GO"
        rapport.write_text(json.dumps(donnees), encoding="utf-8")
        nouveau = optimisation.comparer(self.base, self.projet, self.source, maitre=profil(), trancher=False)
        self.assertFalse(nouveau["cache_reutilise"])
        self.assertEqual(nouveau["decision"], "À revoir")

    def test_budget_et_sortie_dans_preparation_figee_refuses(self):
        with self.assertRaisesRegex(ValueError, "budget"):
            optimisation.comparer(
                self.base, self.projet, self.source, maitre=profil(), trancher=False, max_candidats=99
            )
        with self.assertRaisesRegex(ValueError, "Pnn"):
            optimisation.comparer(
                self.base,
                self.projet,
                self.source,
                sortie=self.projet / "preparations/P01/recherche",
                maitre=profil(),
                trancher=False,
            )

    def test_changement_declaration_projet_invalide_le_cache(self):
        premier = optimisation.comparer(self.base, self.projet, self.source, maitre=profil(), trancher=False)
        self.fabrication["objectif"] = "rapide"
        (self.projet / "FABRICATION.json").write_text(json.dumps(self.fabrication), encoding="utf-8")
        suivant = optimisation.comparer(self.base, self.projet, self.source, maitre=profil(), trancher=False)
        self.assertFalse(suivant["cache_reutilise"])
        self.assertNotEqual(premier["empreinte_cache"], suivant["empreinte_cache"])

    def test_profil_maitre_et_snapshots_sont_portables_dans_une_etude(self):
        dossier_maitre = self.base.prive / "config/profil-synthetique"
        maitre = profil_avec_snapshots(dossier_maitre)
        rapport = optimisation.comparer(
            self.base,
            self.projet,
            self.source,
            sortie=self.projet / "candidats/optimisation/portable",
            maitre=maitre,
            trancher=False,
        )
        profil_copie = Path(rapport["machine"]["profil"])
        from benchab.machine.profil_maitre import charger_machine

        charger_machine(profil_copie)
        preuves = rapport["machine"]["preuves_sources"]
        self.assertEqual(
            {preuve["relatif"] for preuve in preuves}, {"configuration.json", "references/profil-exporte.json"}
        )
        for preuve in preuves:
            chemin = Path(preuve["fichier"])
            self.assertEqual(hashlib.sha256(chemin.read_bytes()).hexdigest(), preuve["sha256"])

    def historique_synthetique(self, maitre=None):
        chemin = self.base.prive / "references/historique-synthetique.json"
        chemin.parent.mkdir(parents=True, exist_ok=True)
        document = {
            "references": [
                {
                    "name": "Essai synthétique",
                    "settings": {
                        "layer_height": self.reglages["layer_height"],
                        "nozzle_temperature": self.reglages["nozzle_temperature"],
                        "nozzle_temperature_initial_layer": self.reglages["nozzle_temperature_initial_layer"],
                    },
                    "reported_result": "WARN",
                    "file": "references/modele-historique-absent.3mf",
                    "evidence_limit": "Référence synthétique de test seulement.",
                }
            ]
        }
        chemin.write_text(json.dumps(document), encoding="utf-8")
        profil_maitre = copy.deepcopy(maitre or profil())
        profil_maitre["observations_contextuelles"] = [
            {"reference": "references/historique-synthetique.json", "filament_id": "autre-filament"}
        ]
        return chemin, profil_maitre, document

    def test_references_historiques_synthetiques_sans_transfert(self):
        _chemin, maitre, _document = self.historique_synthetique()
        rapport = optimisation.proposer(self.base, self.projet, self.source, maitre=maitre)
        reference = rapport["indices_historiques"][0]
        self.assertFalse(reference["transfert_automatique"])
        self.assertIn("matière différente ou mélange non établi", reference["conditions_non_etablies"])
        self.assertEqual(len(reference["sha256_reference"]), 64)
        self.assertFalse(reference["source_disponible"])

    def test_references_historiques_hors_prive_ou_symboliques_refusees(self):
        historique = self.base.prive / "references/historique-synthetique.json"
        historique.parent.mkdir(parents=True, exist_ok=True)
        historique.write_text('{"references": []}', encoding="utf-8")
        maitre = profil()
        maitre["observations_contextuelles"] = [{"reference": "../hors.json", "filament_id": "filament-fixture"}]
        with self.assertRaisesRegex(ValueError, "relatives à base.prive"):
            optimisation.proposer(self.base, self.projet, self.source, maitre=maitre)
        alias = self.base.prive / "references/alias.json"
        alias.symlink_to(historique)
        maitre["observations_contextuelles"] = [{"reference": "references/alias.json", "filament_id": "x"}]
        with self.assertRaisesRegex(ValueError, "Lien symbolique"):
            optimisation.proposer(self.base, self.projet, self.source, maitre=maitre)

    def test_changement_reference_historique_invalide_cache(self):
        chemin, maitre, document = self.historique_synthetique()
        premier = optimisation.comparer(
            self.base,
            self.projet,
            self.source,
            maitre=maitre,
            trancher=False,
        )
        second = optimisation.comparer(
            self.base,
            self.projet,
            self.source,
            maitre=maitre,
            trancher=False,
        )
        self.assertTrue(second["cache_reutilise"])
        document["references"][0]["reported_result"] = "FAIL"
        chemin.write_text(json.dumps(document), encoding="utf-8")
        suivant = optimisation.comparer(
            self.base,
            self.projet,
            self.source,
            maitre=maitre,
            trancher=False,
        )
        self.assertFalse(suivant["cache_reutilise"])
        self.assertNotEqual(premier["empreinte_cache"], suivant["empreinte_cache"])

    def test_comparaison_tranche_par_api_unique_et_cache_controle_protege(self):
        appels = []

        def trancher(source, dossier, nom, **options):
            with zipfile.ZipFile(source) as archive:
                settings = json.loads(archive.read(optimisation.CONFIGURATION))
            self.assertEqual(settings["filament_ids"], ["filament-fixture"])
            self.assertEqual(options["usage"], "impression")
            cible = Path(dossier) / nom
            cible.write_bytes("tranchage simulé; aucun slicer externe".encode("utf-8"))
            appels.append(cible)
            return cible, 1.0

        def resultats(cible):
            secondes = 100 if Path(cible).parent.name == "reference" else 80
            return [{"index": 1, "secondes": secondes, "grammes": 10}]

        controles = {
            "routine": {"statut": "PASS"},
            "premiere_couche": {"1": {"statut": "PASS"}},
            "zones_locales": {"1": {"statut": "PASS"}},
            "resistance": {"obligatoire": False, "rapport": {"statut": "WARN"}},
            "geometrie": {"statut": "PASS"},
        }
        capacites = {"statut": "PASS", "decision": "GO"}
        with (
            patch.object(optimisation.anycubic, "trancher", side_effect=trancher),
            patch.object(optimisation.anycubic, "resultats", side_effect=resultats),
            patch("benchab.controles.capacites_machine.analyser", return_value=capacites),
            patch.object(optimisation, "controles_candidat", return_value=controles),
        ):
            premier = optimisation.comparer(self.base, self.projet, self.source, maitre=profil())
            self.assertEqual(premier["gain_pct"], 20)
            self.assertEqual(premier["recommande"], "proposition")
            cache = optimisation.comparer(self.base, self.projet, self.source, maitre=profil())
            self.assertTrue(cache["cache_reutilise"])
            self.assertEqual(len(appels), 2)
            Path(premier["candidats"][0]["controle_fichier"]["fichier"]).write_text("{}", encoding="utf-8")
            nouveau = optimisation.comparer(self.base, self.projet, self.source, maitre=profil())
            self.assertFalse(nouveau["cache_reutilise"])
            self.assertEqual(len(appels), 4)


if __name__ == "__main__":
    unittest.main()
