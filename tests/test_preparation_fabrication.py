"""Régressions de fiches de préparation privées sur des archives synthétiques."""

import hashlib
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

from benchab.donnees.base import Base
from benchab.fabrication import preparation
from benchab.projets.envois import enregistrer as associer_envoi


class PreparationFabricationTests(unittest.TestCase):
    def setUp(self):
        temporaire = tempfile.TemporaryDirectory(dir="/private/tmp")
        self.addCleanup(temporaire.cleanup)
        self.base = Base(Path(temporaire.name) / "prive")
        self.addCleanup(self.base.fermer)
        self.projet = self.base.prive / "projets" / "essai"
        self.projet.mkdir(parents=True)
        self.plaques = self.base.prive / "sources/plaques.3mf"
        self.tranche = self.base.prive / "sources/tranche.3mf"
        self.plaques.parent.mkdir()
        with zipfile.ZipFile(self.plaques, "w") as archive:
            archive.writestr("3D/3dmodel.model", "<model/>")
        self.ecrire_tranche("G1 X1 Y1\n")
        self.environnement = {
            "code": 0,
            "mesures": {"python": {"version": "3.13"}},
            "resultats": [{"controle": "Python BENCHAB", "statut": "PASS"}],
        }

    def ecrire_tranche(self, code_gcode):
        with zipfile.ZipFile(self.tranche, "w") as archive:
            archive.writestr("Metadata/plate_1.gcode", code_gcode)

    def creer(self, **options):
        plaques = options.pop("plaques", self.plaques)
        tranche = options.pop("tranche", self.tranche)
        return preparation.creer(
            self.base,
            self.projet,
            "P01",
            plaques,
            tranche,
            environnement=options.pop("environnement", self.environnement),
            **options,
        )

    def test_cree_fiche_figee_et_inscription_idempotente(self):
        resultat = self.creer(note="candidat synthétique")
        fiche_path = self.projet / "preparations/P01/PREPARATION.json"
        fiche = json.loads(fiche_path.read_text(encoding="utf-8"))
        self.assertEqual(resultat["projet_id"], "projets/essai")
        self.assertEqual(fiche["id"], "P01")
        self.assertEqual(fiche["statut"], "préparation candidate — NON imprimée")
        self.assertIsNone(fiche["envoi"])
        self.assertEqual(fiche["note"], "candidat synthétique")
        self.assertEqual(fiche["resistance"]["statut"], "WARN")
        self.assertEqual(fiche["tranche_3mf"]["sha256"], hashlib.sha256(self.tranche.read_bytes()).hexdigest())
        self.assertTrue((fiche_path.parent / "plaques.3mf").is_file())
        self.assertTrue((fiche_path.parent / "environnement.json").is_file())
        with self.assertRaises(FileExistsError):
            self.creer()

    def test_main_argv_creer_utilise_base_et_ne_modifie_pas_sys_argv(self):
        """Vérifie le câblage CLI avec une preuve synthétique, sans dépendre du slicer installé."""
        anciens_arguments = ["pytest", "arguments-appelant"]
        sortie = StringIO()
        # Ce test exerce la CLI. La qualification native reste un contrôle distinct
        # décrit dans docs/reference/controles.md.
        with (
            patch("sys.argv", anciens_arguments),
            patch.object(preparation, "collecter", return_value=self.environnement) as collecte,
            redirect_stdout(sortie),
        ):
            code = preparation.main(
                [
                    "--prive",
                    str(self.base.prive),
                    "creer",
                    "--projet",
                    str(self.projet),
                    "--id",
                    "P01",
                    "--plaques",
                    str(self.plaques),
                    "--tranche",
                    str(self.tranche),
                ]
            )
            self.assertEqual(sys.argv, anciens_arguments)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(sortie.getvalue())["projet_id"], "projets/essai")
        collecte.assert_called_once_with(self.base.prive)

    def test_identifiant_invalide_est_refuse_sans_creation(self):
        with self.assertRaises(SystemExit):
            preparation.main(
                [
                    "--prive",
                    str(self.base.prive),
                    "creer",
                    "--projet",
                    str(self.projet),
                    "--id",
                    "../hors",
                    "--plaques",
                    str(self.plaques),
                    "--tranche",
                    str(self.tranche),
                ]
            )
        self.assertFalse((self.projet / "preparations").exists())

    def test_entrees_absentes_et_rapports_invalides_refuses_sans_dossier(self):
        mauvais_rapport = self.base.prive / "rapports/invalide.json"
        mauvais_rapport.parent.mkdir(parents=True, exist_ok=True)
        cas = (
            {"plaques": self.base.prive / "absente-plaques.3mf"},
            {"tranche": self.base.prive / "absente-tranche.3mf"},
            {"sources": [self.base.prive / "absente-source.stl"]},
            {"modeles": [self.base.prive / "absent-modele.stl"]},
            {"controles": [mauvais_rapport]},
            {"precedente": "P00"},
        )
        for options in cas:
            with self.subTest(options=options):
                mauvais_rapport.write_text("{" if "controles" in options else "null", encoding="utf-8")
                with self.assertRaises((OSError, ValueError, KeyError)):
                    self.creer(**options)
                self.assertFalse((self.projet / "preparations").exists())

    def test_nom_non_pnn_refuse_avant_figer_un_dossier(self):
        with self.assertRaises(ValueError):
            preparation.creer(
                self.base,
                self.projet,
                "essai",
                self.plaques,
                self.tranche,
                environnement=self.environnement,
            )
        self.assertFalse((self.projet / "preparations").exists())
        self.assertEqual(self.base.connexion.execute("SELECT count(*) FROM preparations").fetchone()[0], 0)

    def test_fichier_frere_avec_meme_prefixe_est_hors_base_prive(self):
        voisin = self.base.prive.parent / f"{self.base.prive.name}-copie"
        voisin.mkdir()
        plaques = voisin / "plaques.3mf"
        tranche = voisin / "tranche.3mf"
        shutil.copy2(self.plaques, plaques)
        shutil.copy2(self.tranche, tranche)
        with self.assertRaisesRegex(ValueError, "base.prive"):
            preparation.creer(
                self.base,
                self.projet,
                "P01",
                plaques,
                tranche,
                environnement=self.environnement,
            )
        self.assertFalse((self.projet / "preparations").exists())

    def test_gcode_absent_vide_ou_commentaire_seul_refuse_avant_preparation(self):
        for contenu in (None, "", "; commentaire seul\n"):
            with self.subTest(contenu=contenu):
                self.tranche.unlink(missing_ok=True)
                if contenu is not None:
                    self.ecrire_tranche(contenu)
                else:
                    with zipfile.ZipFile(self.tranche, "w") as archive:
                        archive.writestr("3D/3dmodel.model", "<model/>")
                with self.assertRaisesRegex(ValueError, "G-code de plaque|sans commandes"):
                    self.creer()
                self.assertFalse((self.projet / "preparations").exists())

    def test_commentaire_d_un_gcode_n_est_pas_une_commande(self):
        with zipfile.ZipFile(self.tranche, "w") as archive:
            archive.writestr("Metadata/plate_1.gcode", "; commentaire\nG1 X1 Y1 ; déplacement\n")
            archive.writestr("Metadata/plate_2.gcode", "; commentaire seul\n")
        with self.assertRaisesRegex(ValueError, "Plaque 2 sans commandes"):
            self.creer()
        self.assertFalse((self.projet / "preparations").exists())

    def test_fail_de_controle_est_conserve_sans_etre_promu(self):
        rapport = self.base.prive / "rapports/controle.json"
        rapport.parent.mkdir(exist_ok=True)
        rapport.write_text(json.dumps({"statut": "FAIL", "details": {"statut": None}}), encoding="utf-8")
        self.creer(controles=[rapport])
        fiche = json.loads((self.projet / "preparations/P01/PREPARATION.json").read_text(encoding="utf-8"))
        controle = next(iter(fiche["controles"].values()))
        self.assertEqual(controle["statuts"]["FAIL"], 1)

    def test_collecteur_environnement_en_echec_refuse_la_publication(self):
        environnement = {
            "code": 2,
            "mesures": {"python": {"version": "3.13"}},
            "resultats": [{"controle": "dépendance", "statut": "FAIL"}],
        }
        with self.assertRaisesRegex(ValueError, "Collecte environnement en échec"):
            self.creer(environnement=environnement)
        self.assertTrue((self.projet / "preparations/P01.en-cours").is_dir())
        self.assertFalse((self.projet / "preparations/P01").exists())

    def test_source_modifiee_pendant_copie_ne_publie_pas(self):
        copier = preparation.shutil.copy2

        def modifier(source, destination, *args, **kwargs):
            if Path(source) == self.tranche:
                self.ecrire_tranche("G1 X999 Y1\n")
            return copier(source, destination, *args, **kwargs)

        with patch.object(preparation.shutil, "copy2", side_effect=modifier):
            with self.assertRaisesRegex(ValueError, "modifiée pendant la copie"):
                self.creer()
        self.assertTrue((self.projet / "preparations/P01.en-cours").is_dir())
        self.assertFalse((self.projet / "preparations/P01").exists())
        self.assertIn("X999", self.tranche.read_bytes().decode("latin1"))

    def test_parametre_apparu_pendant_la_copie_refuse(self):
        copier = preparation.shutil.copy2

        def ajouter_parametre(source, destination, *args, **kwargs):
            resultat = copier(source, destination, *args, **kwargs)
            if Path(source) == self.tranche:
                (self.projet / "src").mkdir()
                (self.projet / "src/parametres.py").write_text("EFFORTS = {}", encoding="utf-8")
            return resultat

        with patch.object(preparation.shutil, "copy2", side_effect=ajouter_parametre):
            with self.assertRaisesRegex(ValueError, "Source modifiée pendant la préparation"):
                self.creer()
        self.assertFalse((self.projet / "preparations/P01").exists())

    def test_envoi_est_associe_uniquement_si_commandes_uniques(self):
        def c(empreinte):
            return {"commandes_sha256": empreinte}

        resultat = preparation.comparer_envoi({"1": c("b")}, {"1": c("a"), "2": c("b")})
        self.assertIn("plaque 2", resultat["1"])
        ambigu = preparation.comparer_envoi({"1": c("a")}, {"1": c("a"), "2": c("a")})
        self.assertIn("AMBIGU", ambigu["1"])
        different = preparation.comparer_envoi({"1": c("c")}, {"1": c("a")})
        self.assertIn("DIFFÉRENTES", different["1"])

    def test_statuts_recursifs_conservent_fail(self):
        rapport = self.base.prive / "rapports/imbrique.json"
        rapport.parent.mkdir(exist_ok=True)
        rapport.write_text(json.dumps({"statut": "PASS", "details": {"statut": "FAIL"}}), encoding="utf-8")
        self.assertEqual(preparation.statuts_rapport(rapport), {"FAIL": 1, "PASS": 1})

    def test_preparation_precedente_compare_les_plaques_retires_sans_mutation(self):
        with zipfile.ZipFile(self.tranche, "w") as archive:
            archive.writestr("Metadata/plate_1.gcode", "G1 X1 Y1 E1\n")
            archive.writestr("Metadata/plate_2.gcode", "G1 X2 Y2 E1\n")
        precedente = self.creer()
        precedente_path = Path(precedente["fiche"])
        precedente_avant = precedente_path.read_bytes()
        self.ecrire_tranche("G1 X1 Y1 E1\n")
        suivante = preparation.creer(
            self.base,
            self.projet,
            "P02",
            self.plaques,
            self.tranche,
            precedente="P01",
            environnement=self.environnement,
        )
        fiche_suivante = json.loads(Path(suivante["fiche"]).read_text(encoding="utf-8"))
        self.assertEqual(fiche_suivante["comparaison"]["plaques"]["1"], "commandes identiques")
        self.assertIn("DIFFÉRENTES", fiche_suivante["comparaison"]["plaques"]["2"])
        self.assertNotEqual(fiche_suivante["plaques_3mf"]["sha256"], fiche_suivante["tranche_3mf"]["sha256"])
        self.assertEqual(precedente_path.read_bytes(), precedente_avant)

    def test_preparation_precedente_distingue_modifiee_identique_et_ajoutee(self):
        with zipfile.ZipFile(self.tranche, "w") as archive:
            archive.writestr("Metadata/plate_1.gcode", "G1 X1 E1\n")
            archive.writestr("Metadata/plate_2.gcode", "G1 X2 E1\n")
        precedente = Path(self.creer()["fiche"])
        avant = precedente.read_bytes()
        with zipfile.ZipFile(self.tranche, "w") as archive:
            archive.writestr("Metadata/plate_1.gcode", "G1 X9 E1\n")
            archive.writestr("Metadata/plate_2.gcode", "G1 X2 E1\n")
            archive.writestr("Metadata/plate_3.gcode", "G1 X3 E1\n")
        resultat = preparation.creer(
            self.base,
            self.projet,
            "P02",
            self.plaques,
            self.tranche,
            precedente="P01",
            environnement=self.environnement,
        )
        comparaison = json.loads(Path(resultat["fiche"]).read_text())["comparaison"]["plaques"]
        self.assertIn("DIFFÉRENTES", comparaison["1"])
        self.assertEqual(comparaison["2"], "commandes identiques")
        self.assertIn("DIFFÉRENTES", comparaison["3"])
        self.assertEqual(precedente.read_bytes(), avant)

    def test_envoi_sans_gcode_refuse_et_snapshot_reste_fige(self):
        resultat = self.creer()
        fiche = Path(resultat["fiche"])
        avant = fiche.read_bytes()
        source = self.base.prive / "export-vide.gcode"
        for contenu in (b"", b"; commentaire seul\n"):
            source.write_bytes(contenu)
            with self.subTest(contenu=contenu), self.assertRaisesRegex(ValueError, "sans commandes"):
                associer_envoi(self.base, self.projet, "P01", source)
            self.assertEqual(fiche.read_bytes(), avant)

    def optimisation_fixture(self):
        dossier = self.projet / "candidats/optimisation/essai"
        dossier.mkdir(parents=True)
        profil = dossier / "maitre.json"
        profil.write_text('{"schema":1,"id":"machine-test"}', encoding="utf-8")
        source = dossier / "source.3mf"
        plaques = dossier / "plaques.3mf"
        tranche = dossier / "tranche.3mf"
        for chemin in (source, plaques, tranche):
            with zipfile.ZipFile(chemin, "w") as archive:
                archive.writestr("3D/3dmodel.model", "<model/>")
                if chemin == tranche:
                    archive.writestr("Metadata/plate_1.gcode", "G1 X8 Y2\n")
        rapport = dossier / "rapport.json"

        def entree(chemin):
            return {
                "fichier": chemin.name,
                "sha256": hashlib.sha256(chemin.read_bytes()).hexdigest(),
            }

        donnees = {
            "schema": 1,
            "objectif": "equilibre",
            "source": entree(source),
            "machine": {
                "id": "machine-test",
                "profil": profil.name,
                "profil_sha256": hashlib.sha256(profil.read_bytes()).hexdigest(),
            },
            "candidats": [
                {
                    "id": "c1",
                    "decision": "GO",
                    "comparaison_equitable": {"conforme": True},
                    "plaques_3mf": entree(plaques),
                    "tranche_3mf": entree(tranche),
                }
            ],
        }
        rapport.write_text(json.dumps(donnees), encoding="utf-8")
        return dossier, rapport, donnees

    def test_optimisation_go_est_figee_avec_artefacts_portables(self):
        dossier, rapport, donnees = self.optimisation_fixture()
        resultat = preparation.creer(
            self.base,
            self.projet,
            "P02",
            optimisation=rapport,
            candidat="c1",
            environnement=self.environnement,
        )
        fiche = json.loads(resultat["fiche"].read_text(encoding="utf-8"))
        copie = resultat["fiche"].parent / "optimisation"
        self.assertEqual(fiche["optimisation"]["candidat"], "c1")
        self.assertEqual(fiche["optimisation"]["objectif"], "equilibre")
        self.assertFalse(fiche["optimisation"]["risques_acceptes"])
        self.assertIsNone(fiche["envoi"])
        self.assertEqual(
            hashlib.sha256((copie / "tranche.3mf").read_bytes()).hexdigest(),
            donnees["candidats"][0]["tranche_3mf"]["sha256"],
        )
        self.assertEqual((copie / "profil-maitre.json").read_bytes(), (dossier / "maitre.json").read_bytes())
        self.assertTrue((resultat["fiche"].parent / "tranche.3mf").is_file())

    def test_optimisation_a_revoir_exige_acceptation_expresse(self):
        _, rapport, donnees = self.optimisation_fixture()
        donnees["candidats"][0]["decision"] = "À revoir"
        rapport.write_text(json.dumps(donnees), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "accepter_risques requis"):
            preparation.creer(
                self.base,
                self.projet,
                "P02",
                optimisation=rapport,
                candidat="c1",
                environnement=self.environnement,
            )
        resultat = preparation.creer(
            self.base,
            self.projet,
            "P02",
            optimisation=rapport,
            candidat="c1",
            accepter_risques=True,
            environnement=self.environnement,
        )
        fiche = json.loads(resultat["fiche"].read_text(encoding="utf-8"))
        self.assertTrue(fiche["optimisation"]["risques_acceptes"])

    def test_optimisation_non_go_injuste_alteree_ou_scellage_invalide_refuse(self):
        dossier, rapport, donnees = self.optimisation_fixture()
        for decision in ("NO GO", "inconnue"):
            with self.subTest(decision=decision):
                donnees["candidats"][0]["decision"] = decision
                rapport.write_text(json.dumps(donnees), encoding="utf-8")
                with self.assertRaises(ValueError):
                    preparation.selection_optimisation(self.base, rapport, "c1", True)
        donnees["candidats"][0]["decision"] = "GO"
        donnees["candidats"][0]["comparaison_equitable"]["conforme"] = False
        rapport.write_text(json.dumps(donnees), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "non équitable"):
            preparation.selection_optimisation(self.base, rapport, "c1")
        donnees["candidats"][0]["comparaison_equitable"]["conforme"] = True
        rapport.write_text(json.dumps(donnees), encoding="utf-8")
        (dossier / "SCELLE.json").write_text('{"rapport_sha256":"' + "0" * 64 + '"}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "sceau non conforme"):
            preparation.selection_optimisation(self.base, rapport, "c1")

    def test_optimisation_artefact_altere_refuse_avant_publication(self):
        dossier, rapport, _ = self.optimisation_fixture()
        (dossier / "tranche.3mf").write_bytes(b"alteree")
        with self.assertRaisesRegex(ValueError, "altéré"):
            preparation.creer(
                self.base,
                self.projet,
                "P02",
                optimisation=rapport,
                candidat="c1",
                environnement=self.environnement,
            )
        self.assertFalse((self.projet / "preparations/P02").exists())

    def test_optimisation_copie_les_snapshots_de_preuves_sous_references(self):
        dossier, rapport, donnees = self.optimisation_fixture()
        preuve = dossier / "references/profil.json"
        preuve.parent.mkdir()
        preuve.write_text('{"vitesse":60}', encoding="utf-8")
        profil = dossier / "maitre.json"
        profil.write_text(
            json.dumps({"schema": 1, "id": "machine-test", "sources": [{"snapshot": "references/profil.json"}]}),
            encoding="utf-8",
        )
        machine = donnees["machine"]
        machine.update(
            profil_sha256=hashlib.sha256(profil.read_bytes()).hexdigest(),
            preuves_sources=[
                {
                    "fichier": str(preuve),
                    "sha256": hashlib.sha256(preuve.read_bytes()).hexdigest(),
                    "relatif": "references/profil.json",
                }
            ],
        )
        rapport.write_text(json.dumps(donnees), encoding="utf-8")
        resultat = preparation.creer(
            self.base,
            self.projet,
            "P02",
            optimisation=rapport,
            candidat="c1",
            environnement=self.environnement,
        )
        copie = resultat["fiche"].parent / "optimisation/references/profil.json"
        self.assertEqual(copie.read_bytes(), preuve.read_bytes())
        fiche = json.loads(resultat["fiche"].read_text(encoding="utf-8"))
        self.assertEqual(
            fiche["optimisation"]["preuves_sources"],
            [{"relatif": "references/profil.json", "fichier": "optimisation/references/profil.json"}],
        )

    def test_resistance_fail_refuse_avant_creation_de_preparation(self):
        (self.projet / "src").mkdir()
        cas = {
            "modele": "traction",
            "materiau": "materiau_fixture",
            "direction_couches": "XY",
            "source_effort": "Charge analytique déclarée",
            "justification_section": "Section rectangulaire synthétique",
            "section": {"forme": "rectangle", "largeur_mm": 10, "hauteur_mm": 20, "epaisseur_mm": 5},
            "force_N": 20000,
            "longueur_mm": 200,
            "densite_pct": 100,
            "conditions_representatives": True,
        }
        (self.projet / "src/parametres.py").write_text(
            "EFFORTS = " + repr({"Piece": cas}),
            encoding="utf-8",
        )
        registre = {
            "materiaux": {
                "materiau_fixture": {
                    "traction_MPa": {"XY": 50, "Z": 40},
                    "young_MPa": {"XY": 1000, "Z": 1000},
                    "source": "fixture synthétique",
                }
            },
            "remplissages": {"motifs": ["gyroid"]},
        }
        chemin_registre = self.base.prive / "donnees/materiaux/resistance.json"
        chemin_registre.parent.mkdir(parents=True, exist_ok=True)
        chemin_registre.write_text(json.dumps(registre), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Résistance calculée insuffisante"):
            self.creer()
        self.assertFalse((self.projet / "preparations").exists())

    def test_mesure_environnement_ecrit_uniquement_dans_prive(self):
        cible = self.base.prive / "mesures/environnement.json"
        cible.parent.mkdir(exist_ok=True)
        with patch.object(
            preparation,
            "collecter",
            return_value=self.environnement,
        ):
            resultat = preparation.mesurer_environnement(self.base, cible)
        self.assertEqual(resultat["code"], 0)
        self.assertEqual(json.loads(cible.read_text(encoding="utf-8")), resultat)
        with self.assertRaises(FileExistsError):
            preparation.mesurer_environnement(self.base, cible)


if __name__ == "__main__":
    unittest.main()
