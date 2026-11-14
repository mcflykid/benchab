"""Le conseil exige une relecture GROS liée aux entrées avant tout vote formel."""

import hashlib
import json

import pytest
from fixtures_votes import ajouter_option_vote, creer_atelier_vote

from benchab.vote import conseil, relecture


def _charger(atelier):
    data = json.loads(atelier["dossier"].read_text(encoding="utf-8"))
    critique = atelier["base"].prive / data["relecture_critique"]["fichier"]
    contenu = json.loads(critique.read_text(encoding="utf-8"))
    return data, critique, contenu


def _enregistrer(atelier, data, critique, contenu):
    critique.write_text(json.dumps(contenu, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    data["relecture_critique"]["sha256"] = hashlib.sha256(critique.read_bytes()).hexdigest()
    atelier["dossier"].write_text(json.dumps(data), encoding="utf-8")


def test_critique_absente_enregistre_une_attente_sans_quorum(tmp_path):
    atelier = creer_atelier_vote(tmp_path)
    try:
        base, dossier, data = atelier["base"], atelier["dossier"], atelier["donnees"]
        data.pop("relecture_critique")
        dossier.write_text(json.dumps(data), encoding="utf-8")

        vote = conseil.produire(base, dossier)

        assert vote["statut"] == "EN ATTENTE DE RELECTURE CRITIQUE"
        assert vote["preavis_pour"] == 7
        assert vote["pour"] == 0
        assert all(item["vote"] == "abstention" for item in vote["votes"].values())
        assert vote["action_autorisee_par_vote"] is False
        assert conseil.historique(base)[-1]["relecture_critique"]["statut"] == "EN ATTENTE"
        assert data["objet"] in conseil.etat(base)["decisions_en_attente"]
        affichage = conseil.rendre(vote)
        assert "Préavis non formels : 7/7" in affichage
        assert "aucun quorum actif" in affichage
    finally:
        atelier["fermer"]()


@pytest.mark.parametrize(
    "mutation",
    ["stale", "wrong_preparation", "historique", "modele_leger", "classe_invalide", "sans_mode"],
)
def test_critique_perimee_ou_provenance_invalide_reste_en_attente(tmp_path, mutation):
    atelier = creer_atelier_vote(tmp_path)
    try:
        data, critique, contenu = _charger(atelier)
        if mutation == "stale":
            data["relecture_critique"]["sha256"] = "0" * 64
            atelier["dossier"].write_text(json.dumps(data), encoding="utf-8")
        else:
            if mutation == "wrong_preparation":
                contenu["preparation_sha256"] = "f" * 64
            elif mutation == "historique":
                contenu["provenance"]["mode"] = "avec_historique"
                contenu["provenance"]["sans_historique"] = False
            elif mutation == "modele_leger":
                contenu["provenance"]["modele"] = "gpt-6-luna"
            elif mutation == "classe_invalide":
                contenu["provenance"]["classe"] = "LEGER"
            elif mutation == "sans_mode":
                del contenu["dixieme_homme"]["mode"]
            _enregistrer(atelier, data, critique, contenu)

        vote = conseil.produire(atelier["base"], atelier["dossier"])

        assert vote["statut"] == "EN ATTENTE DE RELECTURE CRITIQUE"
        assert vote["pour"] == 0
        assert vote["relecture_critique"]["manquants"]
    finally:
        atelier["fermer"]()


def test_risque_critique_sourcé_est_normalise_et_remis_a_a4(tmp_path):
    atelier = creer_atelier_vote(tmp_path)
    try:
        data, critique, contenu = _charger(atelier)
        fichier, sha = next(iter(contenu["entrees"].items()))
        contenu["risques_nouveaux"] = [
            {
                "id": "risque-premortem-1",
                "description": "Risque observé à vérifier.",
                "constat": "Le rapport synthétique contient un point à vérifier.",
                "source": {"fichier": fichier, "sha256": sha},
            }
        ]
        contenu["dixieme_homme"]["scenarios"][0]["couverture"] = {
            "type": "risque_nouveau",
            "reference": "risque-premortem-1",
        }
        _enregistrer(atelier, data, critique, contenu)

        vote = conseil.produire(atelier["base"], atelier["dossier"])

        assert vote["risques_nouveaux_non_verifies"][0]["statut"] == "NON VÉRIFIÉ"
        assert vote["votes"]["A4"]["vote"] == "abstention"
        assert any(
            "NON VÉRIFIÉ" in point and "Risque observé" in point for point in vote["votes"]["A4"]["points_a_revoir"]
        )
        assert vote["statut"].startswith("EN ATTENTE D'ARBITRAGE")
    finally:
        atelier["fermer"]()


def test_unanimite_sans_trois_scenarios_interdit_le_vote_formel(tmp_path):
    atelier = creer_atelier_vote(tmp_path)
    try:
        data, critique, contenu = _charger(atelier)
        del contenu["dixieme_homme"]
        _enregistrer(atelier, data, critique, contenu)

        vote = conseil.produire(atelier["base"], atelier["dossier"])

        assert vote["preavis_pour"] == 7
        assert vote["statut"] == "EN ATTENTE DE RELECTURE CRITIQUE"
        assert vote["pour"] == 0
        assert vote["dixieme_homme"] is None
    finally:
        atelier["fermer"]()


def test_scenario_sans_couverture_ni_risque_source_reste_en_attente(tmp_path):
    atelier = creer_atelier_vote(tmp_path)
    try:
        data, critique, contenu = _charger(atelier)
        del contenu["dixieme_homme"]["scenarios"][0]["couverture"]
        _enregistrer(atelier, data, critique, contenu)

        vote = conseil.produire(atelier["base"], atelier["dossier"])

        assert vote["statut"] == "EN ATTENTE DE RELECTURE CRITIQUE"
        assert vote["preavis_pour"] == 7
        assert any("couverture explicite" in manque for manque in vote["relecture_critique"]["manquants"])
    finally:
        atelier["fermer"]()


def test_tous_controles_pass_exigent_dix_meme_avec_un_avis_ia_contre(tmp_path):
    atelier = creer_atelier_vote(tmp_path)
    try:
        data, critique, contenu = _charger(atelier)
        data["avis_ia"]["A7"] = {"vote": "contre", "motif": "Désaccord synthétique de test."}
        del contenu["dixieme_homme"]
        _enregistrer(atelier, data, critique, contenu)

        vote = conseil.produire(atelier["base"], atelier["dossier"])

        assert vote["relecture_critique"]["statut"] == "EN ATTENTE"
        assert vote["preavis_pour"] == 6
        assert vote["votes"]["A7"]["vote"] == "abstention"
        assert all(vote["votes"][role]["vote"] == "abstention" for role in conseil.AUTOMATIQUES)
        assert vote["statut"] == "EN ATTENTE DE RELECTURE CRITIQUE"
    finally:
        atelier["fermer"]()


def test_trois_scenarios_complets_persistes_sans_echec_automatique(tmp_path):
    atelier = creer_atelier_vote(tmp_path)
    try:
        fiche_avant = atelier["fiche"].read_bytes()

        vote = conseil.produire(atelier["base"], atelier["dossier"])

        cible = atelier["base"].prive / vote["envoi_markdown"]
        texte = cible.read_text(encoding="utf-8")
        assert vote["statut"] == "APPROUVÉ"
        assert len(vote["points_surveillance"]) == 3
        assert "Signe fictif 1" in texte
        assert "Cause fictive de test 3" in texte
        assert "aucune observation ni impression n'est confirmée" in texte
        assert "NON PRÊT" not in texte
        assert atelier["fiche"].read_bytes() == fiche_avant
        assert cible.is_relative_to(atelier["base"].prive / "projets/synthetique/envois")
    finally:
        atelier["fermer"]()


def test_mutation_critique_apres_validation_empeche_toute_inscription(tmp_path, monkeypatch):
    atelier = creer_atelier_vote(tmp_path)
    try:
        base, dossier, data = atelier["base"], atelier["dossier"], atelier["donnees"]
        chemin_critique = base.prive / data["relecture_critique"]["fichier"]
        original = relecture.verifier

        def valider_puis_muter(*args, **kwargs):
            resultat = original(*args, **kwargs)
            chemin_critique.write_text("critique modifiée après validation", encoding="utf-8")
            return resultat

        monkeypatch.setattr(relecture, "verifier", valider_puis_muter)

        with pytest.raises(ValueError, match="critique ou preuve modifiés pendant le vote"):
            conseil.produire(base, dossier)

        assert base.connexion.execute("SELECT count(*) FROM votes").fetchone()[0] == 0
        assert not list((base.prive / "projets/fixture/envois").glob("*.md"))
    finally:
        atelier["fermer"]()


def test_comparaison_affiche_l_attente_de_critique_sans_annoncer_quorum(tmp_path):
    atelier = creer_atelier_vote(tmp_path)
    try:
        base = atelier["base"]
        seconde = ajouter_option_vote(atelier, "P02")
        donnees = json.loads(atelier["dossier"].read_text(encoding="utf-8"))
        donnees.pop("relecture_critique")
        atelier["dossier"].write_text(json.dumps(donnees), encoding="utf-8")
        question = base.prive / "question-attente.json"
        question.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "question": "Comparer deux dossiers synthétiques",
                    "options": {
                        "première": atelier["dossier"].relative_to(base.prive).as_posix(),
                        "seconde": seconde,
                    },
                }
            ),
            encoding="utf-8",
        )

        resultat = conseil.comparer(base, question)

        assert resultat["statut"] == "EN ATTENTE DE RELECTURE CRITIQUE"
        assert resultat["options"]["première"]["vote_formalise"] is False
        assert resultat["options"]["première"]["voix"] == 0
        assert resultat["recommandation"] == ""
        assert "préavis 7/7 ; attente relecture critique" in resultat["resume"]
    finally:
        atelier["fermer"]()
