"""Entrée mesurée : calcul, dossier neuf et lecture de l'historique privé."""

import json

from fixtures_votes import creer_atelier_vote

from benchab.cli import main


def test_vote_cli_inscrit_mesure_et_avis_sans_changer_preparation(tmp_path, capsys, monkeypatch):
    atelier = creer_atelier_vote(tmp_path)
    try:
        base, fiche, dossier = atelier["base"], atelier["fiche"], atelier["dossier"]
        avant = fiche.read_bytes()
        monkeypatch.chdir(tmp_path)
        assert main(["--prive", str(base.prive.relative_to(tmp_path)), "vote", str(dossier.relative_to(tmp_path))]) == 0
        resultat = json.loads(capsys.readouterr().out)
        assert len(resultat["votes"]) == 7
        assert resultat["action_autorisee_par_vote"] is False
        assert fiche.read_bytes() == avant
        assert base.connexion.execute("SELECT count(*) FROM votes").fetchone()[0] == 1
        mesure = base.connexion.execute("SELECT duree_s,code FROM mesures").fetchone()
        assert mesure[0] > 0 and mesure[1] == 0
        assert main(["--prive", str(base.prive), "vote", "--etat"]) == 0
        assert json.loads(capsys.readouterr().out)["derniers_votes"][0]["id"] == resultat["id"]
    finally:
        atelier["fermer"]()


def test_preparer_cli_ecrit_un_dossier_explicitement_a_completer(tmp_path, capsys):
    atelier = creer_atelier_vote(tmp_path)
    try:
        base, fiche = atelier["base"], atelier["fiche"]
        sortie = base.prive / "dossier-neuf.json"
        arguments = [
            "--prive",
            str(base.prive),
            "vote",
            "--preparer",
            str(fiche),
            "--sortie",
            str(sortie),
            "--objet",
            "Vérifier le dossier synthétique",
        ]
        assert main(arguments) == 0
        capsys.readouterr()
        donnees = json.loads(sortie.read_text())
        assert donnees["a_completer"]
        assert set(donnees["avis_ia"]) == {"A1", "A5", "A7"}
        assert all(a["vote"] == "abstention" for a in donnees["avis_ia"].values())
        avant = sortie.read_bytes()
        assert main(arguments) != 0
        assert sortie.read_bytes() == avant
    finally:
        atelier["fermer"]()


def test_arbitrage_explicite_enregistre_sans_voix_ni_autorisation(atelier_initialise, capsys):
    """Choix explicite DÉCLARÉ dans SQLite, aucune voix ni autorisation ; objet absent FAIL."""
    import sqlite3

    argv = ["--prive", str(atelier_initialise), "vote", "--arbitrer", "Garder le statu quo"]
    assert main(argv) != 0
    assert main([*argv, "--objet", "Choix synthétique"]) == 0
    verdict = json.loads(capsys.readouterr().out)
    assert verdict["statut"] == "DÉCLARÉ" and verdict["autorisation"] is False and verdict["vote"] is None
    with sqlite3.connect(atelier_initialise / "benchab.sqlite") as db:
        assert db.execute("SELECT count(*) FROM votes").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM decisions").fetchone()[0] == 1
