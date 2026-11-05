"""Les métadonnées ambiguës ne qualifient jamais le runtime, même sans exécution de son Python."""

import json
from pathlib import Path

import pytest

from benchab.mesure.runtime import controler


def _fixture(tmp_path, *, git=False):
    python = tmp_path / "venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("non exécuté")
    python.chmod(0o755)
    dossier = tmp_path / "venv/lib/python3.13/site-packages/fixture-1.dist-info"
    dossier.mkdir(parents=True)
    (dossier / "METADATA").write_text("Name: fixture\nVersion: 1\n")
    source = (
        'git = "https://example.invalid/code.git#' + "a" * 40 + '"'
        if git
        else 'registry = "https://example.invalid/simple"'
    )
    verrou = tmp_path / "uv.lock"
    verrou.write_text('version = 1\n[[package]]\nname = "fixture"\nversion = "1"\nsource = {' + source + "}\n")
    return python, verrou, dossier


def test_versions_differentes_et_doublons_ne_passent_pas(tmp_path):
    """Compare les versions sans unité ; écart FAIL, doublon NON VÉRIFIÉ, aucun processus lancé."""
    python, verrou, dossier = _fixture(tmp_path)
    assert controler(python, verrou, ["fixture"])["statut"] == "PASS"
    (dossier / "METADATA").write_text("Name: fixture\nVersion: 2\n")
    assert controler(python, verrou, ["fixture"])["statut"] == "FAIL"
    autre = dossier.with_name("fixture-2.dist-info")
    autre.mkdir()
    (autre / "METADATA").write_text("Name: fixture\nVersion: 2\n")
    assert controler(python, verrou, ["fixture"])["statut"] == "NON VÉRIFIÉ"


@pytest.mark.parametrize("valeur", [[], {"vcs_info": []}])
def test_provenance_malformee_ne_passe_pas(tmp_path, valeur):
    """Une provenance JSON malformée reste NON VÉRIFIÉE, sans import ni installation."""
    python, verrou, dossier = _fixture(tmp_path)
    (dossier / "direct_url.json").write_text(json.dumps(valeur))
    assert controler(python, verrou, ["fixture"])["statut"] == "NON VÉRIFIÉ"


def test_revision_git_exacte_et_runtime_absent(tmp_path):
    """Exige le commit verrouillé ; une version identique ne masque ni provenance absente ni révision différente."""
    python, verrou, dossier = _fixture(tmp_path, git=True)
    assert controler(python, verrou, ["fixture"])["statut"] == "NON VÉRIFIÉ"
    direct = dossier / "direct_url.json"
    direct.write_text(
        json.dumps({"url": "https://example.invalid/code.git", "vcs_info": {"vcs": "git", "commit_id": "a" * 40}})
    )
    assert controler(python, verrou, ["fixture"])["statut"] == "PASS"
    direct.write_text(
        json.dumps({"url": "https://example.invalid/code.git", "vcs_info": {"vcs": "git", "commit_id": "b" * 40}})
    )
    assert controler(python, verrou, ["fixture"])["statut"] == "FAIL"
    assert controler(Path(str(python) + "-absent"), verrou, ["fixture"])["statut"] == "NON VÉRIFIÉ"


def test_git_incomplet_et_version_differente_ne_deviennent_pas_pass(tmp_path):
    """Un SHA seul reste NON VÉRIFIÉ ; la version connue différente impose FAIL malgré cette incertitude."""
    python, verrou, dossier = _fixture(tmp_path, git=True)
    direct = dossier / "direct_url.json"
    direct.write_text(json.dumps({"vcs_info": {"commit_id": "a" * 40}}))
    assert controler(python, verrou, ["fixture"])["statut"] == "NON VÉRIFIÉ"
    (dossier / "METADATA").write_text("Name: fixture\nVersion: 2\n")
    assert controler(python, verrou, ["fixture"])["statut"] == "FAIL"
    direct.unlink()
    assert controler(python, verrou, ["fixture"])["statut"] == "FAIL"
    direct.write_text("JSON invalide")
    assert controler(python, verrou, ["fixture"])["statut"] == "FAIL"


def test_origine_git_differente_et_metadonnees_ambigues(tmp_path):
    """Une autre origine donne FAIL ; des en-têtes ou clés JSON dupliqués restent NON VÉRIFIÉS."""
    python, verrou, dossier = _fixture(tmp_path, git=True)
    direct = dossier / "direct_url.json"
    direct.write_text(
        json.dumps({"url": "https://example.invalid/autre.git", "vcs_info": {"vcs": "git", "commit_id": "a" * 40}})
    )
    assert controler(python, verrou, ["fixture"])["statut"] == "FAIL"
    direct.write_text('{"vcs_info":{},"vcs_info":{}}')
    assert controler(python, verrou, ["fixture"])["statut"] == "NON VÉRIFIÉ"
    (dossier / "METADATA").write_text("Name: fixture\nVersion: 1\nVersion: 2\n")
    assert controler(python, verrou, ["fixture"])["statut"] == "NON VÉRIFIÉ"
