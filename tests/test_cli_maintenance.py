"""Les quatre usages passent par l'entrée mesurée, sans données réelles."""

import hashlib
import json
from unittest.mock import patch
from zipfile import ZipFile

from benchab.cli import main
from benchab.qualite import archives


def test_sauvegarde_restauration_et_verrou_depuis_cli(tmp_path, atelier_initialise):
    """Vérifie sauvegarde restauration et verrou depuis cli ; atelier initialisé, aucune machine touchée."""
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
