"""Les contrôles spécialisés restent accessibles depuis l'entrée mesurée."""

import importlib
import sqlite3

import pytest

from benchab.cli import CONTROLES_DIRECTS, main


@pytest.mark.parametrize("nom,module", CONTROLES_DIRECTS.items())
def test_controle_mesure_transmet_les_arguments_et_le_code(tmp_path, monkeypatch, nom, module, atelier_initialise):
    """Vérifie controle mesure transmet les arguments et le code ; atelier initialisé, aucune machine touchée."""
    appels = []

    def controle(arguments, **options):
        assert options == ({"prive": tmp_path / "prive"} if nom in {"appuis-gcode", "ecarts-profils-3mf"} else {})
        appels.append(arguments)
        return 3

    monkeypatch.setattr(importlib.import_module("benchab.controles." + module), "main", controle)
    prive = tmp_path / "prive"
    arguments = ["entree avec espaces.3mf", "--json", "sortie avec espaces.json"]
    assert main(["--prive", str(prive), "controle", nom, *arguments]) == 3
    assert appels == [arguments]
    with sqlite3.connect(prive / "benchab.sqlite") as connexion:
        mesures = connexion.execute("SELECT commande, code FROM mesures").fetchall()
    assert len(mesures) == 1
    assert f"controle {nom}" in mesures[0][0]
    assert mesures[0][1] == 3


def test_exception_controle_reste_mesuree(tmp_path, monkeypatch, atelier_initialise):
    """Vérifie exception controle reste mesuree ; atelier initialisé, aucune machine touchée."""
    def refuser(arguments):
        raise ValueError("entrée refusée")

    monkeypatch.setattr(importlib.import_module("benchab.controles.debit_gcode"), "main", refuser)
    prive = tmp_path / "prive"
    assert main(["--prive", str(prive), "controle", "debit-gcode", "absent.3mf"]) == 1
    with sqlite3.connect(prive / "benchab.sqlite") as connexion:
        assert connexion.execute("SELECT code FROM mesures").fetchall() == [(1,)]


def test_sortie_argparse_controle_garde_son_code(tmp_path, monkeypatch, atelier_initialise):
    """Vérifie sortie argparse controle garde son code ; atelier initialisé, aucune machine touchée."""
    def arreter(arguments):
        raise SystemExit(2)

    monkeypatch.setattr(importlib.import_module("benchab.controles.debit_gcode"), "main", arreter)
    prive = tmp_path / "prive"
    assert main(["--prive", str(prive), "controle", "debit-gcode", "entree.3mf"]) == 2
    with sqlite3.connect(prive / "benchab.sqlite") as connexion:
        assert connexion.execute("SELECT code FROM mesures").fetchall() == [(2,)]
