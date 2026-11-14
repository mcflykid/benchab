"""Port des cas D2 ; les dossiers et rapports sont entièrement synthétiques."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from fixtures_votes import (
    ajouter_critique_synthetique,
    ajouter_option_vote,
    creer_atelier_vote,
    creer_dossier_preuves,
    empreinte,
)

from benchab.vote import conseil, permissions, preuves


class TestVotePreuves(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.racine = Path(self.temporary.name)
        self.dossier = creer_dossier_preuves(self.racine)

    def tearDown(self):
        self.temporary.cleanup()

    def verifier(self, dossier=True, **modifications):
        data = {
            "objet": "modifier le jeu de la charnière",
            "type_action": "correction_reversible",
            "scenario_test": True,
        }
        if dossier:
            data["dossier_preuves"] = self.dossier
        data.update(modifications)
        categorie = permissions.permissions(data)["categorie_risque"]
        administrateurs = {membre["id"] for membre in conseil.charger_conseil()["administrateurs"]}
        return preuves.verifier(
            self.racine,
            data,
            categorie,
            permissions.G["niveaux_de_preuve"],
            administrateurs,
        )

    def invalide(self, dossier=True, **modifications):
        resultat = self.verifier(dossier=dossier, **modifications)
        self.assertEqual(resultat["statut"], "Vérification nécessaire", resultat)
        self.assertTrue(resultat["manquants"], resultat)
        return resultat

    def test_preuve_indispensable_absente_bloque_meme_avec_sept_pour(self):
        self.dossier["verifications_requises"][0]["preuve"] = "absente"
        votes = {f"A{i}": {"vote": "pour"} for i in range(1, 8)}
        self.invalide(votes=votes)

    def test_dossier_obligatoire_pour_b_et_c(self):
        self.invalide(dossier=False, objet="refonte du socle", autorisation_requise=False)
        self.invalide(dossier=False, objet="modifier le jeu de la charnière", autorisation_requise=False)

    def test_dossier_complet_b_approuvable(self):
        resultat = self.verifier()
        self.assertEqual(resultat["statut"], "documenté")
        self.assertEqual(resultat["manquants"], [])

    def test_dossier_complet_ne_donne_pas_les_permissions(self):
        data = {
            "objet": "modifier une pièce protégée",
            "type_action": "correction_reversible",
            "dossier_preuves": self.dossier,
        }
        resultat = preuves.verifier(
            self.racine,
            data,
            permissions.permissions(data)["categorie_risque"],
            permissions.G["niveaux_de_preuve"],
            {membre["id"] for membre in conseil.charger_conseil()["administrateurs"]},
        )
        assert resultat["statut"] == "documenté"
        assert permissions.permissions(data)["autorisation_requise"] is True

    def test_operation_courante_sans_dossier(self):
        resultat = self.verifier(
            dossier=False,
            objet="corriger une faute",
            type_action="documentation",
        )
        self.assertEqual(resultat["statut"], "non requis")

    def test_dossier_fourni_est_verifie_meme_en_a(self):
        self.dossier["preuves"][0]["source"] = "absente.json"
        resultat = self.verifier(objet="corriger une faute", type_action="documentation")
        self.assertEqual(resultat["statut"], "Vérification nécessaire")
        self.assertTrue(resultat["manquants"])

    def test_empreinte_de_la_preuve_et_des_entrees(self):
        for nom in ("preuve-synthetique.json", "entree-synthetique.txt"):
            with self.subTest(fichier=nom):
                self.dossier = creer_dossier_preuves(self.racine)
                (self.racine / nom).write_text("modifié après contrôle", encoding="utf-8")
                self.invalide()

    def test_source_absente_ou_hors_projet(self):
        for source in ("inexistant", "../preuve-synthetique.json", str(self.racine / "preuve-synthetique.json")):
            with self.subTest(source=source):
                self.dossier = creer_dossier_preuves(self.racine)
                self.dossier["preuves"][0]["source"] = source
                self.invalide()

    def test_origine_date_et_niveau_requis(self):
        for champ, valeur in (
            ("origine", ""),
            ("date", ""),
            ("date", "pas une date"),
            ("date", "2999-01-01"),
            ("niveau", "preuve magique"),
            ("sha256", "incorrect"),
        ):
            with self.subTest(champ=champ, valeur=valeur):
                self.dossier = creer_dossier_preuves(self.racine)
                self.dossier["preuves"][0][champ] = valeur
                self.invalide()

    def test_nature_de_preuve_ne_devient_pas_physique_par_consensus(self):
        self.dossier["verifications_requises"][0]["niveaux_acceptes"] = ["testé physiquement"]
        self.invalide()

    def test_echec_avertissement_et_inconnu_ne_satisfont_pas_indispensable(self):
        for statut in ("WARN", "FAIL", "INCONNU"):
            with self.subTest(statut=statut):
                self.dossier = creer_dossier_preuves(self.racine)
                self.dossier["preuves"][0]["statut"] = statut
                self.invalide()

    def test_rapport_negatif_ne_peut_etre_redeclare_pass(self):
        for rapport in (
            {"statut": "FAIL"},
            {"resultats": [{"statut": "WARN"}]},
            {"ok": False},
            {"errors": ["contrôle raté"]},
            {"code_retour": 2},
            {"statut": "incompréhensible"},
            {},
        ):
            with self.subTest(rapport=rapport):
                self.dossier = creer_dossier_preuves(self.racine)
                path = self.racine / "preuve-synthetique.json"
                path.write_text(json.dumps(rapport), encoding="utf-8")
                self.dossier["preuves"][0]["sha256"] = empreinte(path)
                self.invalide()

    def test_rapport_json_illisible_bloque(self):
        path = self.racine / "preuve-synthetique.json"
        path.write_text("rapport cassé", encoding="utf-8")
        self.dossier["preuves"][0]["sha256"] = empreinte(path)
        self.invalide()

    def test_preuve_secondaire_en_echec_peut_decrire_une_alternative_ecartee(self):
        path = self.racine / "alternative-synthetique.json"
        path.write_text('{"statut":"FAIL"}', encoding="utf-8")
        seconde = copy.deepcopy(self.dossier["preuves"][0])
        seconde.update(
            id="alternative-en-echec",
            source=path.name,
            sha256=empreinte(path),
            statut="FAIL",
            conclusion="Alternative fictive écartée.",
        )
        self.dossier["preuves"].append(seconde)
        self.dossier["solutions"][1]["preuves"] = [seconde["id"]]
        resultat = self.verifier()
        self.assertEqual(resultat["statut"], "documenté")

    def test_hypothese_ne_satisfait_pas_indispensable(self):
        for niveau in ("probable", "inconnu"):
            with self.subTest(niveau=niveau):
                self.dossier = creer_dossier_preuves(self.racine)
                self.dossier["preuves"][0]["niveau"] = niveau
                self.dossier["verifications_requises"][0]["niveaux_acceptes"] = [niveau]
                self.invalide()

    def test_exigences_vides_ou_inconnues_refusees(self):
        cas = (
            [],
            None,
            [{"id": "v1", "description": "test", "preuve": "preuve-synthetique", "niveaux_acceptes": []}],
            [{"id": "v1", "description": "test", "preuve": "preuve-synthetique", "niveaux_acceptes": ["magique"]}],
        )
        for valeur in cas:
            with self.subTest(valeur=valeur):
                self.dossier = creer_dossier_preuves(self.racine)
                self.dossier["verifications_requises"] = valeur
                self.invalide()

    def test_entrees_vides_exigent_une_justification(self):
        self.dossier["entrees"] = {}
        self.invalide()
        self.dossier["sans_entrees"] = "Décision abstraite sans fichier cible, scénario synthétique."
        self.assertEqual(self.verifier()["statut"], "documenté")

    def test_statu_quo_est_obligatoire_et_peut_etre_choisi(self):
        self.dossier["choix"] = "existant"
        self.assertEqual(self.verifier()["statut"], "documenté")
        self.dossier["solutions"] = self.dossier["solutions"][:2]
        self.invalide()

    def test_alternative_ou_absence_justifiee(self):
        self.dossier["solutions"] = [s for s in self.dossier["solutions"] if s["role"] != "alternative"]
        self.invalide()
        self.dossier["absence_alternative"] = "Aucune autre option crédible dans le scénario synthétique."
        self.assertEqual(self.verifier()["statut"], "documenté")

    def test_comparaison_incomplete_et_choix_inconnu(self):
        self.dossier["solutions"][0]["evaluation"].pop("comparaison")
        self.invalide()
        self.dossier = creer_dossier_preuves(self.racine)
        self.dossier["choix"] = "inconnue"
        self.invalide()
        self.dossier = creer_dossier_preuves(self.racine)
        self.dossier["solutions"][0]["preuves"] = ["inconnue"]
        self.invalide()

    def test_objection_structuree_ou_absence_justifiee(self):
        for champ in ("probleme", "consequence", "fondement", "test", "correction", "reexamen"):
            with self.subTest(champ=champ):
                self.dossier = creer_dossier_preuves(self.racine)
                self.dossier["objections"][0].pop(champ)
                self.invalide()
        self.dossier = creer_dossier_preuves(self.racine)
        self.dossier["objections"] = []
        self.invalide()
        self.dossier["absence_objection"] = "Aucune objection précise dans ce scénario de test synthétique."
        self.assertEqual(self.verifier()["statut"], "documenté")

    def test_vote_contre_exige_objection_structuree_et_reste_en_arbitrage(self):
        votes = {f"A{i}": {"vote": "pour"} for i in range(1, 8)}
        votes["A7"] = {"vote": "contre", "motif": "Risque fictif."}
        self.invalide(votes=votes)
        self.dossier = creer_dossier_preuves(self.racine, votes=votes)
        resultat = self.verifier(votes=votes)
        self.assertEqual(resultat["statut"], "documenté")
        # Le vote contre reste un objet de validation D2 ; le conseil ne reçoit aucun vote manuel.

    def test_schemas_malformes_refuses_sans_approbation(self):
        for champ in (
            "contraintes",
            "risques",
            "inconnues",
            "preuves",
            "solutions",
            "criteres",
            "couts",
            "objections",
            "entrees",
        ):
            with self.subTest(champ=champ):
                self.dossier = creer_dossier_preuves(self.racine)
                self.dossier[champ] = None
                self.invalide()
        for valeur in (None, [], False, "invalide"):
            with self.subTest(dossier=valeur):
                resultat = self.verifier(dossier=False, dossier_preuves=valeur)
                self.assertEqual(resultat["statut"], "Vérification nécessaire")

    def test_ids_dupliques_et_reference_vide_refuse(self):
        self.dossier["preuves"].append(copy.deepcopy(self.dossier["preuves"][0]))
        self.invalide()
        self.dossier = creer_dossier_preuves(self.racine)
        self.dossier["solutions"][1]["id"] = "nouveau"
        self.invalide()

    def test_verifications_manquantes_existantes_restent_bloquantes(self):
        self.invalide(verifications_manquantes=["mesure encore absente"])

    def test_historique_et_reprise_suivent_le_dernier_vote(self):
        atelier = creer_atelier_vote(self.racine)
        self.addCleanup(atelier["fermer"])
        base, chemin, data = atelier["base"], atelier["dossier"], atelier["donnees"]
        data["verifications_manquantes"] = ["Mesure synthétique à compléter."]
        chemin.write_text(json.dumps(data), encoding="utf-8")
        premier = conseil.produire(base, chemin)
        self.assertTrue(premier["statut"].startswith("BLOQUÉ"))
        historique = conseil.historique(base)
        self.assertEqual(historique[0]["objet"], premier["objet"])
        self.assertIn(premier["objet"], conseil.etat(base)["decisions_en_attente"])
        del data["verifications_manquantes"]
        chemin.write_text(json.dumps(data), encoding="utf-8")
        second = conseil.produire(base, chemin)
        historique = conseil.historique(base)
        self.assertEqual(historique[0]["id"], premier["id"])
        self.assertEqual(len(historique), 2)
        self.assertNotIn(second["objet"], conseil.etat(base)["decisions_en_attente"])
        data["verifications_manquantes"] = ["Nouvelle mesure à compléter."]
        chemin.write_text(json.dumps(data), encoding="utf-8")
        conseil.produire(base, chemin)
        self.assertIn(second["objet"], conseil.etat(base)["decisions_en_attente"])

    def test_dossier_et_qualification_persistes_et_affiches(self):
        atelier = creer_atelier_vote(self.racine)
        self.addCleanup(atelier["fermer"])
        base, chemin, data = atelier["base"], atelier["dossier"], atelier["donnees"]
        dossier = creer_dossier_preuves(base.prive)
        data["dossier_preuves"] = dossier
        ajouter_critique_synthetique(base, chemin, data)
        chemin.write_text(json.dumps(data), encoding="utf-8")
        resultat = conseil.produire(base, chemin)
        stocke = conseil.historique(base)[0]
        self.assertEqual(stocke["dossier_preuves"], dossier)
        self.assertEqual(stocke["qualite_decision"]["statut"], "documenté")
        affichage = conseil.rendre(resultat)
        for texte in (
            "preuve-synthetique.json",
            "calculé",
            "Conserver l'état de référence synthétique",
            "Preuve fictive",
        ):
            self.assertIn(texte, affichage)

    def test_dossier_d2_invalide_bloque_conseil_automatique(self):
        atelier = creer_atelier_vote(self.racine)
        self.addCleanup(atelier["fermer"])
        base, chemin, data = atelier["base"], atelier["dossier"], atelier["donnees"]
        data["dossier_preuves"] = creer_dossier_preuves(base.prive)
        data["dossier_preuves"]["verifications_requises"][0]["preuve"] = "absente"
        ajouter_critique_synthetique(base, chemin, data)
        chemin.write_text(json.dumps(data), encoding="utf-8")
        resultat = conseil.produire(base, chemin)
        self.assertTrue(resultat["statut"].startswith("BLOQUÉ"))
        self.assertEqual(resultat["pour"], 7)
        self.assertTrue(resultat["qualite_decision"]["manquants"])

    def test_question_importante_sans_preuve_ne_contourne_pas_la_barriere(self):
        atelier = creer_atelier_vote(self.racine)
        self.addCleanup(atelier["fermer"])
        base = atelier["base"]
        option_autre = ajouter_option_vote(atelier, "P02")
        donnees = {
            "schema": 1,
            "question": "modifier le jeu de la charnière",
            "type_action": "verification",
            "options": {
                "nouveau": atelier["dossier"].relative_to(base.prive).as_posix(),
                "autre": option_autre,
            },
        }
        question = base.prive / "vote-comparatif.json"
        question.write_text(json.dumps(donnees), encoding="utf-8")
        resultat = conseil.comparer(base, question)
        self.assertTrue(resultat["statut"].startswith("BLOQUÉ"))
        self.assertIn("100 % (7/7)", resultat["resume"])
        donnees["dossier_preuves"] = creer_dossier_preuves(base.prive)
        question.write_text(json.dumps(donnees), encoding="utf-8")
        resultat = conseil.comparer(base, question)
        self.assertTrue(resultat["statut"].startswith("EN ATTENTE"))
        donnees["options"]["non comparée"] = ajouter_option_vote(atelier, "P03")
        question.write_text(json.dumps(donnees), encoding="utf-8")
        resultat = conseil.comparer(base, question)
        self.assertTrue(resultat["statut"].startswith("BLOQUÉ"))

    def test_mission_importante_signale_le_dossier_a_completer(self):
        atelier = creer_atelier_vote(self.racine)
        self.addCleanup(atelier["fermer"])
        sortie = atelier["base"].prive / "rapports/dossier-a-completer.json"
        conseil.preparer(atelier["base"], atelier["fiche"], sortie, "Modifier une charnière fictive")
        self.assertIn("Dossier de preuves à compléter", sortie.read_text(encoding="utf-8"))
