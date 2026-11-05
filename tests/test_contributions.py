"""Contrats du collecteur contradictoire avec une Base privée synthétique."""

import copy
import hashlib
import json
from pathlib import Path

import pytest

from benchab.donnees.base import Base
from benchab.vote import contributions


def _version(auteur: str, solution: str, *, exposee: bool = False) -> dict:
    return {
        "auteur": auteur,
        "autres_propositions_vues": exposee,
        "solution": solution,
        "justification": "Scénario de test synthétique.",
        "avantages": "Comparaison explicite.",
        "limites": "Aucune validation physique.",
        "preuves": [],
        "hypotheses": "Conditions fictives.",
        "risques": "Risque à vérifier.",
        "controles": "Contrôle synthétique requis.",
        "conditions": "Contexte du test uniquement.",
    }


def _exemple(*, identifiant: str = "essai", scenario_test: bool = False) -> dict:
    return {
        "schema": 1,
        "id": identifiant,
        "objectif": "Réviser un assemblage fictif.",
        "categorie": "B",
        "execution": {
            "type": "sessions_distinctes",
            "participants": [
                {"id": "A1", "modele": "modele-x", "session": "s1", "source": "fixture"},
                {"id": "A2", "modele": "modele-x", "session": "s2", "source": "fixture"},
            ],
        },
        "propositions": [
            {"id": "maintien", "role": "statu_quo", "versions": [_version("A1", "Conserver")]},
            {"id": "variante", "role": "solution", "versions": [_version("A2", "Modifier")]},
        ],
        "critiques": [],
        "reponses": [],
        "recommandation": "maintien",
        "criteres": ["stabilité"],
        "inconnues": ["effort réel"],
        "scenario_test": scenario_test,
    }


@pytest.fixture
def atelier(tmp_path):
    base = Base(tmp_path / "prive")
    yield base
    base.fermer()


def _ecrire(base: Base, data: dict, name: str = "contribution.json") -> Path:
    chemin = base.prive / name
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return chemin


def _evenements(base: Base, identifiant: str):
    return list(
        base.connexion.execute(
            "SELECT id,date,texte,source,donnees_json FROM decisions WHERE source=? ORDER BY date,id",
            (contributions.PREFIXE_SOURCE + identifiant,),
        )
    )


def test_importe_dans_sqlite_sans_vote_ni_journal_json(atelier):
    fichier = _ecrire(atelier, _exemple())

    resultat = contributions.preparer(atelier, fichier)

    assert resultat["statut"] == "OBSERVATION"
    assert resultat["autorisation"] is False
    assert resultat["vote"] is None
    assert atelier.connexion.execute("SELECT count(*) FROM votes").fetchone()[0] == 0
    assert len(_evenements(atelier, "essai")) == 1
    assert not (atelier.prive / "journal.jsonl").exists()
    export = [json.loads(ligne) for ligne in (atelier.prive / "export/decisions.jsonl").read_text().splitlines()]
    assert any(entree["source"] == contributions.PREFIXE_SOURCE + "essai" for entree in export)


def test_reimport_sans_information_ne_recrit_ni_l_historique_ni_export(atelier):
    fichier = _ecrire(atelier, _exemple())
    contributions.preparer(atelier, fichier)
    avant = (atelier.prive / "export/decisions.jsonl").read_bytes()
    historique = [tuple(row) for row in _evenements(atelier, "essai")]

    resultat = contributions.preparer(atelier, fichier)

    assert resultat["statut"] == "SANS NOUVELLE INFORMATION"
    assert [tuple(row) for row in _evenements(atelier, "essai")] == historique
    assert (atelier.prive / "export/decisions.jsonl").read_bytes() == avant


def test_versions_append_only_refusent_la_recriture_et_acceptent_revision_critiquee(atelier):
    data = _exemple()
    fichier = _ecrire(atelier, data)
    contributions.preparer(atelier, fichier)
    ancienne = copy.deepcopy(data)
    ancienne["propositions"][0]["versions"][0]["solution"] = "Réécriture interdite"
    _ecrire(atelier, ancienne)
    with pytest.raises(ValueError, match="Historique de proposition modifié"):
        contributions.preparer(atelier, fichier)
    assert len(_evenements(atelier, "essai")) == 1

    nouvelle = copy.deepcopy(data)
    nouvelle["propositions"][1]["versions"].append(_version("A2", "Modifier après retour", exposee=True))
    nouvelle["critiques"] = [
        {
            "id": "c1",
            "auteur": "A1",
            "proposition": "variante",
            "version": 1,
            "probleme": "Charge omise.",
            "fondement": "Hypothèse fictive.",
            "verification": "Recalcul synthétique requis.",
        }
    ]
    nouvelle["reponses"] = [
        {"critique": "c1", "auteur": "A2", "decision": "corriger", "justification": "Version corrigée."}
    ]
    _ecrire(atelier, nouvelle)

    resultat = contributions.preparer(atelier, fichier)

    assert resultat["critiques"][0]["statut"] == "À VÉRIFIER"
    assert resultat["critiques"][0]["reponse"]["decision"] == "corriger"
    assert len(_evenements(atelier, "essai")) == 2


def test_historique_interdit_suppression_de_critique_et_changement_de_contexte(atelier):
    data = _exemple()
    data["critiques"] = [
        {
            "id": "c1",
            "auteur": "A1",
            "proposition": "variante",
            "version": 1,
            "probleme": "Problème fictif.",
            "fondement": "Fixture.",
            "verification": "Contrôle à faire.",
        }
    ]
    fichier = _ecrire(atelier, data)
    contributions.preparer(atelier, fichier)
    nouvelle = copy.deepcopy(data)
    nouvelle["critiques"] = []
    _ecrire(atelier, nouvelle)
    with pytest.raises(ValueError, match="Historique modifié : critiques"):
        contributions.preparer(atelier, fichier)
    nouvelle = copy.deepcopy(data)
    nouvelle["execution"]["type"] = "contexte_partage"
    nouvelle["execution"]["participants"][1]["session"] = "s1"
    _ecrire(atelier, nouvelle)
    with pytest.raises(ValueError, match="Historique modifié : execution"):
        contributions.preparer(atelier, fichier)


def test_json_duplique_non_fini_et_budget_fichier_refuses(atelier):
    fichier = atelier.prive / "bad.json"
    fichier.write_text('{"schema":1,"schema":1}', encoding="utf-8")
    with pytest.raises(ValueError, match="Clé JSON répétée"):
        contributions.charger(atelier, fichier)
    fichier.write_text('{"schema":1,"n":NaN}', encoding="utf-8")
    with pytest.raises(ValueError, match="non finie"):
        contributions.charger(atelier, fichier)
    fichier.write_text('{"schema":1,"n":1e9999}', encoding="utf-8")
    with pytest.raises(ValueError, match="non fini"):
        contributions.charger(atelier, fichier)
    fichier.write_bytes(b" " * (contributions.MAX_OCTETS + 1))
    with pytest.raises(ValueError, match="budget"):
        contributions.charger(atelier, fichier)


def test_statu_quo_et_independance_initiale_sont_obligatoires():
    data = _exemple()
    data["propositions"][0]["role"] = "solution"
    with pytest.raises(ValueError, match="statu quo"):
        contributions.verifier(data)
    data = _exemple()
    data["propositions"][1]["versions"][0]["autres_propositions_vues"] = True
    with pytest.raises(ValueError, match="Proposition initiale exposée"):
        contributions.verifier(data)


def test_modeles_et_sessions_declares_distincts_ne_sont_pas_authentifies(atelier):
    data = _exemple()
    data["execution"]["type"] = "modeles_distincts"
    data["execution"]["participants"][1]["modele"] = "modele-y"
    _ecrire(atelier, data)
    resultat = contributions.preparer(atelier, atelier.prive / "contribution.json")
    assert "non authentifiée" in resultat["independance"]
    assert resultat["autorisation"] is False
    assert atelier.connexion.execute("SELECT count(*) FROM votes").fetchone()[0] == 0


def test_budget_participants_et_passes_applique_selon_mode():
    data = _exemple()
    data["execution"]["participants"].extend(
        {"id": f"A{index}", "modele": "modele-x", "session": f"s{index}", "source": "fixture"} for index in (3, 4)
    )
    with pytest.raises(ValueError, match="Budget de participants"):
        contributions.verifier(data, "ECONOMIE")
    data = _exemple()
    version = copy.deepcopy(data["propositions"][1]["versions"][0])
    for index in range(3):
        revision = copy.deepcopy(version)
        revision["solution"] = f"Révision {index}"
        revision["autres_propositions_vues"] = True
        data["propositions"][1]["versions"].append(revision)
    with pytest.raises(ValueError, match="Nombre de passes"):
        contributions.verifier(data, "ECONOMIE")


def test_critique_auto_critique_et_reponse_dupliquee_refusees():
    data = _exemple()
    data["critiques"] = [
        {
            "id": "c1",
            "auteur": "A2",
            "proposition": "variante",
            "version": 1,
            "probleme": "Problème.",
            "fondement": "Fondement.",
            "verification": "Vérification.",
        }
    ]
    with pytest.raises(ValueError, match="auto-critique"):
        contributions.verifier(data)
    data["critiques"][0]["auteur"] = "A1"
    reponse = {"critique": "c1", "auteur": "A2", "decision": "rejeter", "justification": "Motif."}
    data["reponses"] = [reponse, copy.deepcopy(reponse)]
    with pytest.raises(ValueError, match="Réponse invalide"):
        contributions.verifier(data)


def test_preuves_sha_sont_relues_a_la_consultation(atelier):
    rapport = atelier.prive / "rapports" / "preuve.json"
    rapport.parent.mkdir(parents=True, exist_ok=True)
    rapport.write_text('{"statut":"PASS"}', encoding="utf-8")
    data = _exemple()
    data["propositions"][0]["versions"][0]["preuves"] = [
        {"source": "rapports/preuve.json", "sha256": hashlib.sha256(rapport.read_bytes()).hexdigest()}
    ]
    fichier = _ecrire(atelier, data)
    resultat = contributions.preparer(atelier, fichier)
    assert resultat["verifications_manquantes"] == []

    rapport.write_text('{"statut":"FAIL"}', encoding="utf-8")
    derniere = contributions.derniere(atelier, data["objectif"])
    assert derniere["verifications_manquantes"][0]["motif"] == "empreinte périmée"
    assert derniere["verifications_manquantes"][0]["verifie"] is False


def test_preuves_exterieures_traversantes_et_symlinks_ne_sont_pas_lus(atelier, tmp_path):
    rapport = tmp_path / "secret.json"
    rapport.write_text('{"statut":"PASS"}', encoding="utf-8")
    data = _exemple()
    data["propositions"][0]["versions"][0]["preuves"] = [
        {"source": "../secret.json", "sha256": hashlib.sha256(rapport.read_bytes()).hexdigest()}
    ]
    resultat = contributions.preuves_disponibles(atelier, data)
    assert resultat[0]["verifie"] is False
    assert "traversant" in resultat[0]["motif"]

    cible = atelier.prive / "rapports" / "lien.json"
    cible.parent.mkdir(exist_ok=True)
    cible.symlink_to(rapport)
    data["propositions"][0]["versions"][0]["preuves"][0] = {
        "source": "rapports/lien.json",
        "sha256": hashlib.sha256(rapport.read_bytes()).hexdigest(),
    }
    resultat = contributions.preuves_disponibles(atelier, data)
    assert resultat[0]["verifie"] is False


def test_anonymisation_retire_auteurs_sessions_et_alias_propositions():
    vue = contributions.anonymiser(_exemple())
    texte = json.dumps(vue, ensure_ascii=False)
    assert vue["propositions"][0]["id"] == "P1"
    assert "auteur" not in texte
    assert "session" not in texte
    assert "A1" not in texte and "A2" not in texte
    assert "identifiants dans les textes libres" in vue["limite"]


def test_scenario_synthetique_est_journalise_mais_jamais_repris_comme_derniere(atelier):
    data = _exemple(scenario_test=True)
    fichier = _ecrire(atelier, data)
    resultat = contributions.preparer(atelier, fichier)
    assert resultat["scenario_test"] is True
    assert len(_evenements(atelier, "essai")) == 1
    assert contributions.derniere(atelier, data["objectif"]) is None


def test_desactive_et_categorie_a_ne_persistant_aucun_evenement(atelier):
    resultat = contributions.preparer(atelier, atelier.prive / "absent.json", etat="DESACTIVE")
    assert resultat["statut"] == "INACTIF"
    data = _exemple()
    data["categorie"] = "A"
    fichier = _ecrire(atelier, data)
    resultat = contributions.preparer(atelier, fichier)
    assert resultat["statut"] == "NON NÉCESSAIRE (catégorie A)"
    assert not _evenements(atelier, "essai")


def test_source_contribution_exterieure_et_lien_symbolique_refuses(atelier, tmp_path):
    externe = _ecrire(atelier, _exemple())
    hors_base = tmp_path / externe.name
    hors_base.write_bytes(externe.read_bytes())
    with pytest.raises(ValueError, match="base.prive"):
        contributions.charger(atelier, hors_base)
    cible = atelier.prive / "alias.json"
    cible.symlink_to(externe)
    with pytest.raises(ValueError, match="[Ll]ien"):
        contributions.charger(atelier, cible)


def test_derniere_restitue_ordre_global_par_objectif(atelier):
    premier = _exemple(identifiant="premier")
    second = _exemple(identifiant="second")
    second["objectif"] = premier["objectif"]
    contributions.preparer(atelier, _ecrire(atelier, premier, "premier.json"))
    contributions.preparer(atelier, _ecrire(atelier, second, "second.json"))
    resultat = contributions.derniere(atelier, premier["objectif"])
    assert resultat["id"] == "second"
    assert resultat["vote"] is None and resultat["autorisation"] is False


def test_verification_uniformise_objets_mal_formes():
    data = _exemple()
    data["execution"]["participants"][0]["id"] = []
    with pytest.raises(ValueError, match="Participant dupliqué ou incomplet"):
        contributions.verifier(data)


def test_lecture_refuse_historique_global_tronque(atelier):
    premier = _exemple(identifiant="premier")
    second = _exemple(identifiant="second")
    contributions.preparer(atelier, _ecrire(atelier, premier, "premier.json"))
    contributions.preparer(atelier, _ecrire(atelier, second, "second.json"))
    atelier.connexion.execute(
        "DELETE FROM decisions WHERE source=?",
        (contributions.PREFIXE_SOURCE + "premier",),
    )
    with pytest.raises(ValueError, match="[Oo]rdre global de journal incomplet"):
        contributions.derniere(atelier, second["objectif"])


def test_evenement_ne_peut_rendre_une_contribution_autorisante(atelier):
    data = _exemple()
    contributions.preparer(atelier, _ecrire(atelier, data))
    row = _evenements(atelier, "essai")[0]
    event = json.loads(row[4])
    event["synthese"]["autorisation"] = True
    event["synthese_sha256"] = contributions._sha_json(event["synthese"])
    atelier.connexion.execute("UPDATE decisions SET donnees_json=? WHERE id=?", (json.dumps(event), row[0]))
    with pytest.raises(ValueError, match="autorisante"):
        contributions.derniere(atelier, data["objectif"])


def test_cli_import_et_dossier_reprennent_risques_sans_vote(atelier, capsys):
    """Une contribution choisie ajoute des risques NON VÉRIFIÉS ; aucune voix ni relecture GROS inventée."""
    from benchab.cli import main

    data = _exemple()
    source = _ecrire(atelier, data)
    cli = ["--prive", str(atelier.prive), "vote"]
    assert main([*cli, "--contribution", str(source)]) == 0
    assert json.loads(capsys.readouterr().out)["autorisation"] is False
    fiche = _ecrire(atelier, {"id": "P01"}, "PREPARATION.json")
    dossier = atelier.prive / "dossier.json"
    assert main([*cli, "--preparer", str(fiche), "--sortie", str(dossier), "--objet", data["objectif"],
                 "--id-contribution", data["id"]]) == 0
    contenu = json.loads(dossier.read_text())
    assert contenu["contribution_prealable"]["id"] == data["id"]
    assert contenu["contribution_prealable"]["autorisation"] is False
    assert contenu["risques"] == ["NON VÉRIFIÉ : Risque à vérifier."]
    assert contenu["inconnues"] == ["NON VÉRIFIÉ : effort réel"]
    assert any("GROS sans historique" in ligne for ligne in contenu["a_completer"])
    assert atelier.connexion.execute("SELECT count(*) FROM votes").fetchone()[0] == 0
