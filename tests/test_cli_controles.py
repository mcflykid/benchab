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
        assert options == (
            {"prive": tmp_path / "prive"}
            if nom in {"appuis-gcode", "ecarts-profils-3mf", "plages-z", "surplombs-connexes"}
            else {}
        )
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


def test_sortie_argparse_controle_preserve_message_et_delimiteur(tmp_path, monkeypatch, atelier_initialise, capsys):
    """Une route déléguée conserve le texte SystemExit et retire seulement le séparateur initial."""
    def arreter(arguments):
        assert arguments == ["entree.3mf"]
        raise SystemExit("format attendu: fichier.3mf")

    monkeypatch.setattr(importlib.import_module("benchab.controles.debit_gcode"), "main", arreter)
    prive = tmp_path / "prive"
    assert main(["--prive", str(prive), "controle", "debit-gcode", "--", "entree.3mf"]) == 1
    assert capsys.readouterr().err.strip() == "format attendu: fichier.3mf"
    with sqlite3.connect(prive / "benchab.sqlite") as connexion:
        assert connexion.execute("SELECT code FROM mesures").fetchall() == [(1,)]


@pytest.mark.parametrize(
    "commande,action,module,options",
    [
        ("connaissances", "memoire", "benchab.connaissances.memoire", {"racine": "prive"}),
        ("conception", "cad-engine", "benchab.conception.cad_engine", {"prive": "prive"}),
        ("imports", "maillage-avance", "benchab.imports3d.maillage_avance", {"prive": "prive"}),
        ("visualisation", "photo", "benchab.visualisation.rendu_photo", {"prive": "prive"}),
        ("references", "verifier", "benchab.references_visuelles.references", {}),
        ("machine", "profils-cibles", "benchab.machine.profils_cibles", {}),
    ],
)
def test_familles_declarees_resolvent_module_options_et_arguments(
    tmp_path, monkeypatch, atelier_initialise, commande, action, module, options
):
    """Les familles déclarées conservent les modules, options privées et arguments délégués."""
    appels = []

    def executer(arguments, **kwargs):
        appels.append((arguments, kwargs))
        return 4

    monkeypatch.setattr(importlib.import_module(module), "main", executer)
    prive = tmp_path / "prive"
    assert main(["--prive", str(prive), commande, action, "--", "entree avec espaces", "--json"]) == 4
    attendu_options = {
        nom: (prive if valeur == "prive" else valeur)
        for nom, valeur in options.items()
    }
    assert appels == [(["entree avec espaces", "--json"], attendu_options)]


def test_qualite_route_speciale_ne_mange_pas_le_deuxieme_separateur(monkeypatch, atelier_initialise):
    """Préserve le séparateur métier après le séparateur de délégation."""
    module = importlib.import_module("benchab.qualite.tout")
    appels = []

    def executer(arguments):
        appels.append(arguments)
        return 4

    monkeypatch.setattr(module, "main", executer)
    assert main(["--prive", str(atelier_initialise), "qualite", "tout", "--", "--", "--", "preuve"]) == 4
    assert appels == [["--", "preuve"]]
