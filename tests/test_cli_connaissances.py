"""Entrée mesurée des outils de connaissances dans un atelier jetable."""

import json
import sqlite3

from benchab.cli import main


def test_recherche_conserve_conditions_exclusions_et_preuve(tmp_path, capsys, atelier_initialise):
    """Vérifie recherche conserve conditions exclusions et preuve ; atelier initialisé, aucune machine touchée."""
    prive = tmp_path / "prive"
    registre = prive / "donnees/connaissances/registre.json"
    registre.parent.mkdir(parents=True)
    connaissance = {
        "id": "K1", "titre": "Jeu traversant", "conditions": "axe vertical",
        "exclusions": "pas un trou borgne", "niveau_preuve": "NUMERIQUE",
        "validite": "contextuelle", "sources": [{"chemin": "fixture.md"}],
    }
    registre.write_text(json.dumps({"connaissances": [connaissance], "journaux": {}}))
    assert main(["--prive", str(prive), "connaissances", "memoire", "chercher", "traversant"]) == 0
    assert json.loads(capsys.readouterr().out) == [connaissance]
    with sqlite3.connect(prive / "benchab.sqlite") as connexion:
        assert connexion.execute("SELECT code FROM mesures").fetchall() == [(0,)]


def test_etudes_instantane_explicite_sans_reseau(tmp_path, capsys, atelier_initialise):
    """Vérifie etudes instantane explicite sans reseau ; atelier initialisé, aucune machine touchée."""
    prive = tmp_path / "prive"
    registre = prive / "donnees/connaissances/registre.json"
    registre.parent.mkdir(parents=True)
    registre.write_text('{"connaissances": []}')
    instantane = tmp_path / "instantane.json"
    instantane.write_text('{}')
    assert main([
        "--prive", str(prive), "connaissances", "etudes", "--", "--instantane", str(instantane),
    ]) == 0
    rapport = json.loads(capsys.readouterr().out)
    assert rapport == {"execution": "PASS", "resultat": "PASS", "erreurs": [], "etudes": 0, "propositions": []}
    assert registre.read_text() == '{"connaissances": []}'
