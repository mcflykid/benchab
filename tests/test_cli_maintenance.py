"""Les quatre usages passent par l'entrée mesurée, sans données réelles."""

import hashlib
import json
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile

from benchab.cli import main
from benchab.donnees import sauvegarde
from benchab.qualite import archives


def test_sauvegarde_restauration_et_verrou_depuis_cli(tmp_path, atelier_initialise, monkeypatch):
    """Vérifie les trois commandes sur un atelier jetable et un volume simulé en octets."""
    # La politique du seuil réel est testée séparément avec un espace insuffisant (#163).
    monkeypatch.setattr(sauvegarde.shutil, "disk_usage", lambda _: SimpleNamespace(free=15 * 1024**3))
    prive = tmp_path / "prive"
    source = prive / "projets/fixture"
    source.mkdir(parents=True)
    note = source / "note.txt"
    note.write_text("fixture privée")
    verrou = {"schema": 1, "fichiers": {"note.txt": hashlib.sha256(note.read_bytes()).hexdigest()}}
    (source / "VERROU-REIMPRESSION.json").write_text(json.dumps(verrou))
    cli = ["--prive", str(prive)]
    assert main([*cli, "controle", "reimpression", "--", "--projet", str(source)]) == 0
    archive = tmp_path / "archive.tar"
    options = ["--source", "projets/fixture", "--sortie", str(archive), "--jetable"]
    assert main([*cli, "donnees", "sauvegarder", *options]) == 0
    destination = prive / "restauree"
    assert main([*cli, "donnees", "restaurer", str(archive), "--destination", str(destination)]) == 0
    assert (destination / "note.txt").read_bytes() == note.read_bytes()
    note.write_text("modifiée")
    assert main([*cli, "controle", "reimpression", "--", "--projet", str(source)]) == 1


def test_comparaison_cli_conserve_le_code_echec_et_le_rapport(tmp_path, atelier_initialise):
    """Écart géométrique code1 puis écrasement refusé code2 ; le rapport FAIL initial reste intact."""
    attendu, obtenu = tmp_path / "attendu", tmp_path / "obtenu"
    attendu.mkdir()
    obtenu.mkdir()
    (attendu / "piece.stl").write_bytes(b"geometrie-attendue")
    (obtenu / "piece.stl").write_bytes(b"geometrie-differente")
    rapport = tmp_path / "rapport.json"
    args = [
        "--prive",
        str(tmp_path / "prive"),
        "qualite",
        "comparer-fabrication",
        "--",
        "--attendu",
        str(attendu),
        "--obtenu",
        str(obtenu),
        "--sortie",
        str(rapport),
    ]
    assert main(args) == 1
    assert json.loads(rapport.read_text())["verdicts"]["geometrie"] == "FAIL"
    original = rapport.read_bytes()
    assert main(args) == 2
    assert rapport.read_bytes() == original


def test_inventaire_cli_necrase_pas_et_ne_valide_pas_un_budget_depasse(tmp_path, atelier_initialise):
    """Vérifie inventaire cli necrase pas et ne valide pas un budget depasse.

    Atelier initialisé, aucune machine touchée."""
    source = tmp_path / "source"
    source.mkdir()
    with ZipFile(source / "fixture.zip", "w") as z:
        z.writestr("volumineux.txt", "fixture")
    rapport = tmp_path / "inventaire.json"
    args = ["--prive", str(tmp_path / "prive"), "qualite", "archives", str(source), str(rapport)]
    with patch.object(archives, "MAX_MEMBER", 2):
        assert main(args) != 0
    resultat = json.loads(rapport.read_text())
    assert resultat["statut"] != "PASS"
    assert any(e.get("inspection") == "incomplete" for e in resultat["entries"])
    avant = rapport.read_bytes()
    assert main(args) == 1
    assert rapport.read_bytes() == avant


def test_comparaison_arbres_cli_signale_ecart_sans_reparer(tmp_path, atelier_initialise):
    """La route compare deux arbres privés ; PASS puis FAIL, octets inchangés et aucun accès machine."""
    prive = tmp_path / "prive"
    a, b = prive / "a", prive / "b"
    a.mkdir(parents=True)
    b.mkdir()
    (a / "note").write_text("abc")
    (b / "note").write_text("abc")
    args = ["--prive", str(prive), "donnees", "comparer-arbres", str(a), str(b)]
    assert main(args) == 0
    (b / "note").write_text("def")
    assert main(args) == 2
    assert (a / "note").read_text() == "abc" and (b / "note").read_text() == "def"


def test_resume_reimpression_conserve_les_ecarts_et_le_projet(tmp_path, capsys, atelier_initialise):
    """Le résumé retrouve les écarts exacts dans un rapport créé hors du projet contrôlé."""
    prive = tmp_path / "prive"
    source = prive / "projets/fixture"
    source.mkdir()
    contenu = {"schema": 1, "fichiers": {f"note-{i}.txt": "0" * 64 for i in range(12)}, "repertoires_figes": ["."]}
    verrou = source / "VERROU-REIMPRESSION.json"
    verrou.write_text(json.dumps(contenu))
    commande = ["--prive", str(prive), "controle", "reimpression", "--", "--projet", str(source)]
    assert main(commande) == 1
    detail = capsys.readouterr().out
    resultat = json.loads(detail)
    assert len(resultat["erreurs"]) == 13
    rapport = prive / "rapports/reimpression.json"
    assert main([*commande, "--sortie", str(rapport), "--resume"]) == 1
    court = capsys.readouterr().out
    assert len(court.encode()) < len(detail.encode())
    assert json.loads(court) == {
        "statut": "FAIL", "projet": "fixture", "references_attendues": 12, "erreurs": 13,
        "premier_ecart": "Fichier absent : note-0.txt", "rapport": str(rapport),
    }
    assert json.loads(rapport.read_bytes()) == resultat
    avant = rapport.read_bytes()
    assert main([*commande, "--sortie", str(rapport), "--resume"]) == 2
    assert capsys.readouterr().out == ""
    assert rapport.read_bytes() == avant
    dans_projet = source / "rapport.json"
    assert main([*commande, "--sortie", str(dans_projet), "--resume"]) == 2
    retour = capsys.readouterr()
    assert "hors du projet contrôlé" in retour.err
    assert retour.out == ""
    assert not dans_projet.exists()
    assert set(source.iterdir()) == {verrou}
    assert main([*commande, "--resume"]) == 2
    retour = capsys.readouterr()
    assert "--resume exige --sortie" in retour.err
    assert retour.out == ""


def test_resume_reimpression_sans_verrou_garde_le_code_2(tmp_path, capsys, atelier_initialise):
    """Un contrôle non applicable ne rapporte aucun nombre de fichiers ou d’écarts."""
    prive = tmp_path / "prive"
    source = prive / "projets/fixture"
    source.mkdir()
    rapport = prive / "rapports/reimpression.json"
    assert main([
        "--prive", str(prive), "controle", "reimpression", "--", "--projet", str(source),
        "--sortie", str(rapport), "--resume",
    ]) == 2
    resume = json.loads(capsys.readouterr().out)
    assert resume == {
        "statut": "NON APPLICABLE", "projet": "fixture", "rapport": str(rapport),
        "detail": "Aucun verrou de réimpression déclaré.",
    }
    assert json.loads(rapport.read_bytes())["detail"] == "Aucun verrou de réimpression déclaré."


def test_resume_reimpression_conserve_la_raison_sans_rapport_ouvert(tmp_path, capsys, atelier_initialise):
    """Un verrou historique ou absent donne sa raison dans le résumé, sans seconde lecture."""
    prive = tmp_path / "prive"
    source = prive / "projets/fixture"
    source.mkdir()
    rapport = prive / "rapports/reimpression-raison.json"
    assert main([
        "--prive", str(prive), "controle", "reimpression", "--", "--projet", str(source),
        "--sortie", str(rapport), "--resume",
    ]) == 2
    affiche = json.loads(capsys.readouterr().out)
    assert affiche["detail"] == json.loads(rapport.read_bytes())["detail"]
