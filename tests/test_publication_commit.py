"""Pannes injectées entre SQLite, export et publication de vues privées."""

import json
import sqlite3
from contextlib import contextmanager

import pytest
from fixtures_votes import creer_atelier_vote

from benchab import cli
from benchab.connaissances import bobine
from benchab.donnees.base import Base
from benchab.qualite.ecritures import PublicationNeuve
from benchab.vote import conseil


@contextmanager
def _avant_commit(base):
    with base.connexion:
        yield base.connexion
        raise sqlite3.OperationalError("commit injecté")


def _panne(*_args, **_options):
    raise OSError("publication injectée")


def _injecter(monkeypatch, phase, ignorer=0):
    if phase == "fichier":
        monkeypatch.setattr(PublicationNeuve, "publier", _panne)
        return
    nom = "transaction" if phase == "commit" else "exporter"
    original = getattr(Base, nom)
    appels = 0

    def injecter(base):
        nonlocal appels
        appels += 1
        if appels <= ignorer:
            return original(base)
        return _avant_commit(base) if phase == "commit" else _panne()

    monkeypatch.setattr(Base, nom, injecter)

@pytest.mark.parametrize("phase", ["commit", "export", "fichier"])
def test_vote_reprend_la_meme_publication_apres_panne(tmp_path, monkeypatch, phase):
    """Vérifie l'ordre commit/export/Markdown et un seul vote malgré la relance."""
    atelier = creer_atelier_vote(tmp_path)
    base = atelier["base"]
    try:
        with monkeypatch.context() as patch:
            _injecter(patch, phase, ignorer=1)
            with pytest.raises((OSError, sqlite3.OperationalError), match="inject"):
                conseil.produire(base, atelier["dossier"])
        lignes = base.connexion.execute("SELECT id FROM votes").fetchall()
        assert len(lignes) == (0 if phase == "commit" else 1)
        assert not list(base.prive.glob("**/envois/*.md"))
        vote = conseil.produire(base, atelier["dossier"])
        assert not lignes or vote["id"] == lignes[0][0]
        assert base.connexion.execute("SELECT count(*) FROM votes").fetchone()[0] == 1
        assert (base.prive / vote["envoi_markdown"]).is_file()
        export = (base.prive / "export/votes.jsonl").read_text(encoding="utf-8").splitlines()
        assert [json.loads(ligne)["id"] for ligne in export] == [vote["id"]]
    finally:
        atelier["fermer"]()


@pytest.mark.parametrize("phase", ["commit", "export", "fichier"])
def test_bobine_reprend_la_meme_publication_apres_panne(tmp_path, monkeypatch, phase):
    """Vérifie l'ordre commit/export/fiche et une seule bobine malgré la relance."""
    base = Base(tmp_path / "prive")
    try:
        donnees = {
            "id": "fixture", "marque": "Fixture", "nom": "Filament synthétique",
            "possession": "déclarée pour le test",
            "sources": {k: {"statut": "NON VÉRIFIÉ"} for k in ("fabricant", "orca", "3dfilamentprofiles")},
        }
        with monkeypatch.context() as patch:
            _injecter(patch, phase)
            with pytest.raises((OSError, sqlite3.OperationalError), match="inject"):
                bobine.importer(base.prive, donnees)
        assert base.connexion.execute("SELECT count(*) FROM filaments").fetchone()[0] == (
            0 if phase == "commit" else 1
        )
        assert not list(base.prive.glob("docs/reference/filaments/*/*.md"))
        fiche = base.prive / bobine.importer(base.prive, donnees)
        assert fiche.is_file()
        assert base.connexion.execute("SELECT count(*) FROM filaments").fetchone()[0] == 1
        assert base.connexion.execute("SELECT count(*) FROM bobines").fetchone()[0] == 1
        assert len((base.prive / "export/bobines.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    finally:
        base.fermer()


@pytest.mark.parametrize("phase", ["commit", "export"])
def test_mesure_code_cli_reexporte_apres_panne(atelier_initialise, monkeypatch, phase):
    """Une panne unique garde une seule mesure, avec code CLI et export identiques."""
    original = Base.transaction if phase == "commit" else Base.exporter
    appels = 0

    def injecter(base):
        nonlocal appels
        appels += 1
        if appels == 1:
            return _avant_commit(base) if phase == "commit" else _panne()
        return original(base)

    monkeypatch.setattr(Base, "transaction" if phase == "commit" else "exporter", injecter)
    assert cli.main(["--prive", str(atelier_initialise), "etat"]) == 1
    with sqlite3.connect(atelier_initialise / "benchab.sqlite") as connexion:
        lignes = connexion.execute("SELECT id, code FROM mesures").fetchall()
    assert len(lignes) == 1 and lignes[0][1] == 1
    export = (atelier_initialise / "export/mesures.jsonl").read_text(encoding="utf-8").splitlines()
    assert [(json.loads(ligne)["id"], json.loads(ligne)["code"]) for ligne in export] == lignes
