"""Commandes mesurées, données temporaires et aucun lancement natif pendant les tests."""

import json
import sqlite3

from benchab.cli import main


def test_continuite_cli_fenetre_et_reference_explicites(tmp_path, capsys, atelier_initialise):
    """Vérifie continuite cli fenetre et reference explicites ; atelier initialisé, aucune machine touchée."""
    prive = tmp_path / "prive"
    fichier = tmp_path / "fixture.gcode"
    fichier.write_text("M83\n;TYPE:Outer wall\nG1 X0 Y0 Z20 E1\nG1 X1 Y0 E1\n")
    arguments = ["--prive", str(prive), "controle", "continuite-paroi", str(fichier)]
    assert main(arguments) == 0
    assert json.loads(capsys.readouterr().out)["statut"] == "PASS"
    assert main([*arguments, "--z-min", "20"]) == 1
    assert "--z-max" in capsys.readouterr().err
    assert main([*arguments, "--reference", str(fichier)]) == 1
    assert "--relation" in capsys.readouterr().err
    with sqlite3.connect(prive / "benchab.sqlite") as connexion:
        assert connexion.execute("SELECT count(*) FROM mesures").fetchone()[0] == 3


def test_fabrication_cli_utilise_uniquement_lanceur_commun(tmp_path, monkeypatch, capsys, atelier_initialise):
    """Vérifie fabrication cli utilise uniquement lanceur commun ; atelier initialisé, aucune machine touchée."""
    from benchab.fabrication import trancher

    appels = []

    def fake(source, dossier, nom, *, usage, prive, identifiants, validation_visuelle):
        appels.append((source, dossier, nom, usage, prive, identifiants, validation_visuelle))
        return dossier / "fixture.3mf", {"code": 0}

    monkeypatch.setattr(trancher, "trancher_fichier", fake)
    prive = tmp_path / "prive"
    entree = tmp_path / "fixture.3mf"
    catalogue = tmp_path / "catalogue.json"
    fiche = prive / "apercu.json"
    catalogue.write_text(json.dumps({"filaments": {"PLA": {"id": "profil-fixe"}}}))
    assert main([
        "--prive", str(prive), "fabrication", "trancher", str(entree), "--usage", "temoin",
        "--identifiants", str(catalogue),
        "--validation-visuelle", str(fiche),
    ]) == 0
    capsys.readouterr()
    assert appels == [(entree, prive / "fabrication", None, "temoin", prive, {"PLA": {"id": "profil-fixe"}}, fiche)]
def test_planning_rejoue_refuse_ecrasement_et_garde_le_code_mesure(tmp_path, atelier_initialise):
    """Vérifie planning rejoue refuse ecrasement et garde le code mesure.

    Atelier initialisé, aucune machine touchée."""
    prive = tmp_path / "prive"
    sortie = prive / "rapports/planning.md"
    commande = ["--prive", str(prive), "fabrication", "planning", "--sortie", str(sortie)]
    assert main(commande) == 0
    avant = sortie.read_bytes()
    assert main(commande) == 2
    assert sortie.read_bytes() == avant
    with sqlite3.connect(prive / "benchab.sqlite") as connexion:
        assert connexion.execute("SELECT code FROM mesures ORDER BY rowid").fetchall() == [(0,), (2,)]


def test_planning_resume_conserve_le_document_complet(tmp_path, capsys, atelier_initialise, monkeypatch):
    """Les durées estimées et le lien au Markdown complet sont identiques sur les deux parcours."""
    from benchab.fabrication import planning

    prive = tmp_path / "prive"
    detail = prive / "rapports/planning.md"
    court = prive / "rapports/planning-resume.md"
    plaques = [
        {"projet": "fixture", "preparation": "P01", "plaque": 1, "nom": "A", "heures": 1.25,
         "grammes": 12.0, "filament": "PLA", "fichier": "fixture.3mf"},
    ]
    monkeypatch.setattr(planning, "plaques_pretes", lambda *_: plaques)
    commande = ["--prive", str(prive), "fabrication", "planning", "--sortie"]
    assert main([*commande, str(detail)]) == 0
    complet = capsys.readouterr().out
    assert main([*commande, str(court), "--resume"]) == 0
    sortie = capsys.readouterr().out
    assert len(sortie.encode()) < len(complet.encode())
    assert json.loads(sortie) == {
        "statut": "PASS",
        "plaques": 1,
        "duree_estimee_s": 4500,
        "masse_estimee_g": 12.0,
        "rapport": str(court),
    }
    assert court.read_bytes() == detail.read_bytes() == complet.encode()
    assert main([*commande, str(court), "--resume"]) == 2
    retour = capsys.readouterr()
    assert retour.out == ""
    assert "sortie existe déjà" in retour.err
    assert court.read_bytes() == complet.encode()
