"""Les preuves et entrées D2 restent liées à leur contenu jusqu'à l'inscription."""

import json

import pytest
from fixtures_votes import ajouter_critique_synthetique, ajouter_option_vote, creer_atelier_vote, creer_dossier_preuves

from benchab.vote import conseil
from benchab.vote import preuves as validation_preuves
from benchab.vote.permissions import G


@pytest.mark.parametrize("champ", ["entrees", "preuves"])
def test_modifier_d2_apres_qualification_refuse_le_vote(tmp_path, monkeypatch, champ):
    atelier = creer_atelier_vote(tmp_path)
    try:
        base, dossier, donnees = atelier["base"], atelier["dossier"], atelier["donnees"]
        d2 = creer_dossier_preuves(base.prive)
        donnees["dossier_preuves"] = d2
        ajouter_critique_synthetique(base, dossier, donnees)
        dossier.write_text(json.dumps(donnees))
        relatif = next(iter(d2["entrees"])) if champ == "entrees" else d2["preuves"][0]["source"]
        original = conseil.verifier_dossier

        def modifier(*args, **kwargs):
            qualification = original(*args, **kwargs)
            (base.prive / relatif).write_text("Preuve modifiée après qualification")
            return qualification

        monkeypatch.setattr(conseil, "verifier_dossier", modifier)
        with pytest.raises(ValueError, match="modifiés pendant le vote"):
            conseil.produire(base, dossier)
        assert base.connexion.execute("SELECT count(*) FROM votes").fetchone()[0] == 0
    finally:
        atelier["fermer"]()


def test_modifier_preuve_d2_question_refuse_la_comparaison(tmp_path, monkeypatch):
    atelier = creer_atelier_vote(tmp_path)
    try:
        base = atelier["base"]
        seconde = ajouter_option_vote(atelier, "P02")
        d2 = creer_dossier_preuves(base.prive)
        question = base.prive / "question.json"
        question.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "question": "Modifier le jeu de la charnière",
                    "dossier_preuves": d2,
                    "options": {"nouveau": atelier["dossier"].relative_to(base.prive).as_posix(), "autre": seconde},
                }
            )
        )
        original = conseil.verifier_dossier

        def modifier(racine, data, *args, **kwargs):
            qualification = original(racine, data, *args, **kwargs)
            if data.get("question"):
                (base.prive / d2["preuves"][0]["source"]).write_text("Modifiée après qualification de la question")
            return qualification

        monkeypatch.setattr(conseil, "verifier_dossier", modifier)
        with pytest.raises(ValueError, match="modifiée|modifié"):
            conseil.comparer(base, question)
        assert base.connexion.execute("SELECT count(*) FROM decisions").fetchone()[0] == 0
    finally:
        atelier["fermer"]()


def test_mutation_pendant_lecture_du_rapport_d2_est_non_verifiee(tmp_path, monkeypatch):
    from pathlib import Path

    d2 = creer_dossier_preuves(tmp_path)
    preuve = (tmp_path / d2["preuves"][0]["source"]).resolve()
    lire = Path.read_text

    def modifier(chemin, *args, **kwargs):
        contenu = lire(chemin, *args, **kwargs)
        if chemin == preuve:
            chemin.write_text('{"statut":"PASS","contenu":"modifié après son empreinte"}')
        return contenu

    monkeypatch.setattr(Path, "read_text", modifier)
    qualification = validation_preuves.verifier(
        tmp_path, {"dossier_preuves": d2}, "B", G["niveaux_de_preuve"], {"A4": {}}
    )
    assert qualification["statut"] == "Vérification nécessaire"
    assert any("modifiée pendant la qualification" in erreur for erreur in qualification["manquants"])
