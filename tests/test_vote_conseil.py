"""Votes calculés sur des archives synthétiques, jamais sur une imprimante."""

import hashlib
import json
from zipfile import ZipFile

import pytest
from fixtures_votes import ajouter_critique_synthetique

from benchab.donnees.base import Base
from benchab.vote import conseil


def sha(chemin):
    return hashlib.sha256(chemin.read_bytes()).hexdigest()


@pytest.fixture
def atelier(tmp_path):
    base = Base(tmp_path / "prive")
    dossier = base.prive / "projets/fixture/preparations/P01"
    dossier.mkdir(parents=True)
    tranche = dossier / "tranche.3mf"
    with ZipFile(tranche, "w") as archive:
        archive.writestr("Metadata/plate_1.gcode", "G90\nM83\nG1 X10 E1\n")
    fiche = dossier / "PREPARATION.json"
    fiche.write_text(
        json.dumps(
            {
                "id": "P01",
                "tranche_3mf": {
                    "fichier": tranche.relative_to(base.prive).as_posix(),
                    "sha256": sha(tranche),
                },
            }
        )
    )
    preuve = base.prive / "rapports/controle.json"
    preuve.write_text(json.dumps({"statut": "PASS", "source_sha256": sha(tranche), "scenario_test": True}))
    vote = base.prive / "vote.json"
    data = {
        "schema": 1,
        "objet": "Vérifier le dossier synthétique",
        "type_action": "verification",
        "preparation": fiche.relative_to(base.prive).as_posix(),
        "risques": [],
        "controles": [{"id": "controle", "fichier": "rapports/controle.json", "sha256": sha(preuve)}],
        "avis_ia": {
            role: {"vote": "pour", "motif": "Avis fictif explicite de test, non indépendant."}
            for role in conseil.AVIS_IA
        },
        "scenario_test": True,
    }
    ajouter_critique_synthetique(base, vote, data)
    vote.write_text(json.dumps(data))
    yield base, vote, data, preuve, fiche, tranche
    base.fermer()


def enregistrer_modifications(atelier, *, statut=None):
    _, vote, data, preuve, _, _ = atelier
    if statut and preuve.exists():
        rapport = json.loads(preuve.read_text())
        rapport["statut"] = statut
        preuve.write_text(json.dumps(rapport))
        data["controles"][0]["sha256"] = sha(preuve)
    ajouter_critique_synthetique(atelier[0], vote, data)
    vote.write_text(json.dumps(data))


def test_sept_voix_questions_et_rejeu_identique_en_base(atelier):
    base, vote, _, _, fiche, _ = atelier
    avant = fiche.read_bytes()
    premier = conseil.produire(base, vote)
    assert premier["statut"] == "APPROUVÉ"
    assert premier["pour"] == 7
    assert set(premier["votes"]) == {f"A{i}" for i in range(1, 8)}
    assert all(v["question"] for v in premier["votes"].values())
    assert premier["action_autorisee_par_vote"] is False
    second = conseil.produire(base, vote)
    assert second["votes"] == premier["votes"]
    assert second["empreinte_vote"] == premier["empreinte_vote"]
    assert second["id"] != premier["id"]
    assert fiche.read_bytes() == avant
    assert base.connexion.execute("SELECT count(*) FROM votes").fetchone()[0] == 2
    export = [json.loads(ligne) for ligne in (base.prive / "export/votes.jsonl").read_text().splitlines()]
    assert {v["id"] for v in export} == {premier["id"], second["id"]}


@pytest.mark.parametrize("statut,voix", [("FAIL", "contre"), ("WARN", "abstention"), ("NON VÉRIFIÉ", "abstention")])
def test_controles_indispensables_bloquent_sans_override_ia(atelier, statut, voix):
    base, vote, *_ = atelier
    enregistrer_modifications(atelier, statut=statut)
    resultat = conseil.produire(base, vote)
    assert resultat["statut"].startswith("BLOQUÉ")
    assert all(resultat["votes"][role]["vote"] == voix for role in conseil.AUTOMATIQUES)


def test_preuve_absente_vote_complet_non_verifie(atelier):
    base, vote, data, preuve, *_ = atelier
    preuve.unlink()
    ajouter_critique_synthetique(base, vote, data)
    vote.write_text(json.dumps(data))
    resultat = conseil.produire(base, vote)
    assert resultat["statut"].startswith("BLOQUÉ")
    assert len(resultat["votes"]) == 7
    assert resultat["controles"][0]["statut"] == "NON VÉRIFIÉ"


def test_rapport_lie_a_une_autre_entree_ne_peut_voter_pour(atelier):
    base, vote, data, preuve, *_ = atelier
    rapport = json.loads(preuve.read_text())
    rapport["source_sha256"] = "0" * 64
    preuve.write_text(json.dumps(rapport))
    data["controles"][0]["sha256"] = sha(preuve)
    enregistrer_modifications(atelier)
    resultat = conseil.produire(base, vote)
    assert resultat["statut"].startswith("BLOQUÉ")
    assert resultat["controles"][0]["statut"] == "NON VÉRIFIÉ"


def test_risque_non_traite_impose_abstention_a4(atelier):
    base, vote, data, *_ = atelier
    data["risques"] = ["Retrait des supports non examiné."]
    enregistrer_modifications(atelier)
    resultat = conseil.produire(base, vote)
    assert resultat["votes"]["A4"]["vote"] == "abstention"
    assert data["risques"][0] in resultat["votes"]["A4"]["points_a_revoir"]
    assert resultat["statut"].startswith("EN ATTENTE D'ARBITRAGE")


def test_voix_automatique_ne_peut_etre_fournie_par_ia(atelier):
    base, vote, data, *_ = atelier
    data["avis_ia"]["A2"] = {"vote": "pour", "motif": "Tentative de surcharge"}
    enregistrer_modifications(atelier)
    with pytest.raises(ValueError, match="quatre autres voix"):
        conseil.produire(base, vote)
    assert base.connexion.execute("SELECT count(*) FROM votes").fetchone()[0] == 0


def test_sept_pour_ne_confere_aucun_droit_machine(atelier):
    base, vote, data, *_ = atelier
    data["type_action"] = "impression"
    data["autorisation_requise"] = False
    enregistrer_modifications(atelier)
    resultat = conseil.produire(base, vote)
    assert resultat["pour"] == 7
    assert resultat["statut"].startswith("EN ATTENTE D'AUTORISATION")
    assert resultat["action_autorisee_par_vote"] is False


def test_avis_ia_absent_abstient_sans_inventer_un_agent(atelier):
    base, vote, data, *_ = atelier
    del data["avis_ia"]["A7"]
    enregistrer_modifications(atelier)
    resultat = conseil.produire(base, vote)
    assert resultat["votes"]["A7"]["vote"] == "abstention"
    assert resultat["statut"].startswith("EN ATTENTE D'ARBITRAGE")


def test_decision_significative_sans_dossier_qualifie_reste_bloquee(atelier):
    base, vote, data, *_ = atelier
    data["objet"] = "Modifier le jeu de la charnière"
    enregistrer_modifications(atelier)
    resultat = conseil.produire(base, vote)
    assert resultat["pour"] == 7
    assert resultat["statut"].startswith("BLOQUÉ")
    assert resultat["qualite_decision"]["manquants"]


def test_modification_concurrente_de_preuve_refuse_l_inscription(atelier, monkeypatch):
    base, vote, _, preuve, *_ = atelier
    original = conseil._voix

    def changer(*args):
        votes = original(*args)
        preuve.write_text('{"statut":"FAIL"}')
        return votes

    monkeypatch.setattr(conseil, "_voix", changer)
    with pytest.raises(ValueError, match="modifiés pendant le vote"):
        conseil.produire(base, vote)
    assert base.connexion.execute("SELECT count(*) FROM votes").fetchone()[0] == 0


def test_source_tranchee_alteree_ne_reecrit_pas_la_fiche(atelier):
    base, vote, _, _, fiche, tranche = atelier
    avant = fiche.read_bytes()
    tranche.write_bytes(b"altere")
    with pytest.raises(ValueError, match="Source de préparation altérée"):
        conseil.produire(base, vote)
    assert fiche.read_bytes() == avant
