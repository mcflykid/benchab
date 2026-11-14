"""Port des cas D3 sur le classificateur et les votes BENCHAB synthétiques."""

import json
import tempfile
import unittest
from pathlib import Path

from fixtures_votes import ajouter_critique_synthetique, creer_atelier_vote, creer_dossier_preuves

from benchab.vote import conseil, permissions, preuves


class TestVotePermissions(unittest.TestCase):
    def voter(self, **donnees):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        atelier = creer_atelier_vote(Path(temporary.name))
        self.addCleanup(atelier["fermer"])
        base, path, data = atelier["base"], atelier["dossier"], atelier["donnees"]
        if "type_action" not in donnees:
            data.pop("type_action", None)
        data.update(donnees)
        data["dossier_preuves"] = creer_dossier_preuves(base.prive)
        ajouter_critique_synthetique(base, path, data)
        path.write_text(json.dumps(data), encoding="utf-8")
        return conseil.produire(base, path)

    def attente(self, **donnees):
        permission = permissions.permissions(donnees)
        self.assertTrue(permission["autorisation_requise"], permission)
        self.assertTrue(permission["motifs"], permission)
        resultat = self.voter(**donnees)
        self.assertTrue(resultat["statut"].startswith("EN ATTENTE D'AUTORISATION"), resultat)
        self.assertTrue(resultat["permissions"]["motifs"], resultat)
        return permission

    def test_chaque_type_protege_sans_indicateur_ou_indicateur_faux(self):
        types = (
            "suppression_definitive",
            "impression",
            "chauffe",
            "mouvement",
            "calibration",
            "modification_piece_imprimee",
            "modification_piece_protegee",
            "modification_profil_installe",
            "depense",
            "publication",
            "exposition_donnees",
            "git_init",
            "git_commit",
            "git_push",
        )
        for type_action in types:
            for indicateur in ({}, {"autorisation_requise": False}):
                with self.subTest(type_action=type_action, indicateur=indicateur):
                    resultat = permissions.permissions({"type_action": type_action, **indicateur})
                    self.assertTrue(resultat["autorisation_requise"], resultat)
                    self.assertTrue(resultat["motifs"], resultat)

    def test_description_protegee_ne_peut_etre_minoree_par_le_type(self):
        objets = (
            "supprimer définitivement une archive",
            "lancer une impression",
            "chauffer la buse",
            "déplacer la tête",
            "calibrer le plateau",
            "modifier une pièce déjà imprimée",
            "remplacer une pièce protégée",
            "modifier un profil installé",
            "acheter du filament",
            "publier le rapport",
            "exposer les données sur le réseau",
            "git init",
            "git commit",
            "git push",
        )
        for objet in objets:
            with self.subTest(objet=objet):
                self.attente(
                    objet=objet,
                    type_action="documentation",
                    categorie_risque="A",
                    autorisation_requise=False,
                )

    def test_recommandation_et_cible_comptent(self):
        self.attente(type_action="verification", recommandation="chauffer à 210 °C")
        self.attente(type_action="correction_reversible", cible="pièce protégée")

    def test_categorie_c_declaree_ou_deduite(self):
        for donnees in (
            {"categorie_risque": "C"},
            {"categorie": "C"},
            {"categorie": "C", "categorie_risque": "A"},
            {"objet": "refonte du socle"},
        ):
            with self.subTest(donnees=donnees):
                resultat = self.attente(type_action="documentation", **donnees)
                self.assertEqual(resultat["categorie_risque"], "C")

    def test_informations_insuffisantes_ou_invalides(self):
        cas = (
            {},
            {"type_action": "faire_au_mieux"},
            {"type_action": None},
            {"type_action": ["documentation"]},
            {"type_action": "documentation", "categorie_risque": "?"},
            {"type_action": "documentation", "categorie": None},
            {"type_action": "documentation", "actions": "impression"},
            {"type_action": "documentation", "actions": []},
            {"type_action": "documentation", "actions": [None]},
            {"type_action": "documentation", "commande": "outil inconnu"},
        )
        for donnees in cas:
            with self.subTest(donnees=donnees):
                self.attente(**donnees)

    def test_autorisation_declaree_ne_peut_promouvoir_un_vote(self):
        self.attente(type_action="impression", autorisation_requise=False, autorisation_utilisateur=True)
        self.attente(type_action="documentation", autorisation_requise=True)

    def test_action_courante_approvable(self):
        for type_action in ("documentation", "classement", "verification", "lecture", "correction_reversible"):
            with self.subTest(type_action=type_action):
                resultat = self.voter(objet="corriger une faute dans le guide", type_action=type_action)
                self.assertEqual(resultat["statut"], "APPROUVÉ")
                self.assertFalse(resultat["permissions"]["autorisation_requise"])
                self.assertEqual(resultat["permissions"]["categorie_risque"], "A")

    def test_actions_composees(self):
        self.attente(actions=[{"type_action": "documentation"}, {"type_action": "git_push"}])
        self.attente(actions=[{"type_action": "verification"}, {"objet": "inconnu"}])
        self.attente(actions=[{"type_action": "lecture", "categorie_risque": "C"}])
        resultat = self.voter(actions=[{"type_action": "documentation"}, {"type_action": "lecture"}])
        self.assertEqual(resultat["statut"], "APPROUVÉ")
        self.assertFalse(resultat["permissions"]["autorisation_requise"])

    def test_desaccord_et_verification_manquante_restent_bloquants(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        atelier = creer_atelier_vote(Path(temporary.name))
        self.addCleanup(atelier["fermer"])
        base, path, data = atelier["base"], atelier["dossier"], atelier["donnees"]
        data["votes"] = {"A1": {"vote": "contre", "motif": "avis manuel"}}
        path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Les voix sont calculées"):
            conseil.produire(base, path)
        self.assertEqual(base.connexion.execute("SELECT count(*) FROM votes").fetchone()[0], 0)

        del data["votes"]
        data["verifications_manquantes"] = ["Mesure synthétique absente."]
        path.write_text(json.dumps(data), encoding="utf-8")
        resultat = conseil.produire(base, path)
        self.assertTrue(resultat["statut"].startswith("BLOQUÉ"))

        del data["verifications_manquantes"]
        data["dossier_preuves"] = creer_dossier_preuves(
            base.prive,
            votes={"A7": {"vote": "contre", "motif": "Objection synthétique structurée."}},
        )
        data["avis_ia"]["A7"] = {"vote": "contre", "motif": "Objection synthétique explicite."}
        ajouter_critique_synthetique(base, path, data)
        path.write_text(json.dumps(data), encoding="utf-8")
        resultat = conseil.produire(base, path)
        self.assertEqual(resultat["pour"], 6)
        self.assertEqual(resultat["votes"]["A7"]["vote"], "contre")
        self.assertTrue(resultat["statut"].startswith("EN ATTENTE D'ARBITRAGE"))

    def test_motifs_persistes_et_affiches(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        atelier = creer_atelier_vote(Path(temporary.name))
        self.addCleanup(atelier["fermer"])
        base, path, data = atelier["base"], atelier["dossier"], atelier["donnees"]
        data["type_action"] = "impression"
        data["dossier_preuves"] = creer_dossier_preuves(base.prive)
        ajouter_critique_synthetique(base, path, data)
        path.write_text(json.dumps(data), encoding="utf-8")
        resultat = conseil.produire(base, path)
        self.assertTrue(resultat["statut"].startswith("EN ATTENTE D'AUTORISATION"))
        record = conseil.historique(base)[-1]
        self.assertEqual(record["permissions"], resultat["permissions"])
        self.assertIn("Permission :", conseil.rendre(resultat))
        self.assertIn(resultat["objet"], conseil.etat(base)["decisions_en_attente"])

    def test_regles_verrouillees(self):
        membres = conseil.charger_conseil()["administrateurs"]
        self.assertEqual(len(membres), 7)
        self.assertEqual(set(conseil.AUTOMATIQUES), {"A2", "A3", "A4", "A6"})
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        atelier = creer_atelier_vote(Path(temporary.name))
        self.addCleanup(atelier["fermer"])
        base, path, data = atelier["base"], atelier["dossier"], atelier["donnees"]
        data["dossier_preuves"] = creer_dossier_preuves(base.prive)
        ajouter_critique_synthetique(base, path, data)
        path.write_text(json.dumps(data), encoding="utf-8")
        resultat = conseil.produire(base, path)
        self.assertEqual(resultat["pourcentage"], 100.0)
        self.assertEqual(resultat["statut"], "APPROUVÉ")


def test_dossier_d2_synthetique_est_documente_sans_permission_derivee(tmp_path):
    data = {
        "objet": "Modifier une pièce protégée",
        "type_action": "verification",
        "dossier_preuves": creer_dossier_preuves(tmp_path),
    }
    decision = permissions.permissions(data)
    qualification = preuves.verifier(
        tmp_path,
        data,
        decision["categorie_risque"],
        permissions.G["niveaux_de_preuve"],
        {membre["id"] for membre in conseil.charger_conseil()["administrateurs"]},
    )
    assert decision["autorisation_requise"] is True
    assert decision["categorie_risque"] == "C"
    assert qualification["statut"] == "documenté"
