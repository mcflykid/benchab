"""Hooks locaux inertes avant appel explicite et contrôles lancés par l'entrée mesurée."""

import io
import json
import subprocess
import sys

import pytest

from benchab.cli import main
from benchab.donnees.base import Base
from benchab.qualite import hooks, qualite


def test_post_refuse_atelier_non_identifie_sans_ecriture(tmp_path):
    (tmp_path / "qualite.json").write_text('{}')
    source = tmp_path / "src/piece.py"
    source.parent.mkdir()
    source.write_text("fixture")
    resultat = hooks.post({"tool_input": {"file_path": str(source)}})
    assert resultat["code"] == 2
    assert not (tmp_path / "ETAT.md").exists()
    assert source.read_text() == "fixture"


def test_post_lance_la_commande_mesuree_dans_le_prive(tmp_path, monkeypatch):
    prive = tmp_path / "prive"
    base = Base(prive)
    base.fermer()
    projet = prive / "projets/fixture"
    (projet / "src").mkdir(parents=True)
    (projet / "qualite.json").write_text('{}')
    source = projet / "src/piece.py"
    source.write_text("fixture")
    appels = []

    def executer(commande):
        appels.append(commande)
        return subprocess.CompletedProcess(commande, 1, "contrôle FAIL", "")

    monkeypatch.setattr(qualite, "executer_borne", executer)
    resultat = hooks.post({"tool_input": {"file_path": str(source)}})
    assert resultat["code"] == 1
    assert appels == [[
        sys.executable, "-B", "-m", "benchab.cli", "--prive", str(prive),
        "qualite", "niveau1", str(projet / "qualite.json"),
    ]]
    assert "NO GO" in (projet / "ETAT.md").read_text()


def test_pre_refuse_declaration_pret_sans_preuves(tmp_path):
    with pytest.raises(RuntimeError, match="qualite.json absent"):
        hooks.pre({"cwd": str(tmp_path), "tool_input": {"file_path": "ETAT.md", "content": "PRÊT À IMPRIMER"}})


def test_hook_explicitement_appele_est_mesure(tmp_path, monkeypatch, capsys, atelier_initialise):
    """Vérifie hook explicitement appele est mesure ; atelier initialisé, aucune machine touchée."""
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"tool_input": {}})))
    assert main(["--prive", str(tmp_path / "prive"), "qualite", "hooks", "pre"]) == 0
    assert json.loads(capsys.readouterr().out) == {"decision": "allow"}
    base = Base(tmp_path / "prive")
    try:
        assert base.connexion.execute("SELECT code FROM mesures").fetchall()[0][0] == 0
    finally:
        base.fermer()
