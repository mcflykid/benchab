"""Régressions d'arrondi, codes CLI et robustesse des écritures SQLite."""

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from benchab import cli
from benchab.controles import decollement
from benchab.donnees.base import APPLICATION_ID, Base
from benchab.fabrication.planning import _heure


def test_duree_arrondie_sans_soixante_minutes():
    """Une durée de 1,999 h arrondie à la minute s'affiche 2 h 00."""
    assert _heure(1.999) == "2 h 00"
    assert _heure(1.25) == "1 h 15"


def test_system_exit_texte_garde_le_motif_original(atelier_initialise, monkeypatch, capsys):
    """Un refus délégué conserve son message sur stderr et sort en code 1."""
    def refuser(_arguments):
        raise SystemExit("Diagnostic synthétique exact")

    monkeypatch.setattr(decollement, "main", refuser)
    code = cli.main(["--prive", str(atelier_initialise), "controle", "decollement", "fixture.3mf"])
    assert code == 1
    assert capsys.readouterr().err.strip() == "Diagnostic synthétique exact"


def test_export_accentue_est_utf8_en_locale_c(tmp_path):
    """Avec le mode UTF-8 implicite désactivé, l'export conserve les octets UTF-8."""
    code = (
        "import sys; from benchab.donnees.base import Base; "
        "b=Base(sys.argv[1]); b.ecrire('INSERT INTO projets(id,nom) VALUES(?,?)',"
        "('fixture','Pi'+chr(232)+'ce')); b.fermer()"
    )
    env = {**os.environ, "LC_ALL": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0"}
    prive = tmp_path / "prive"
    r = subprocess.run([sys.executable, "-B", "-c", code, str(prive)],
                       env=env, capture_output=True, timeout=20, check=False)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    assert "Pièce".encode("utf-8") in (prive / "export/projets.jsonl").read_bytes()


def test_initialisations_concurrentes_partagent_un_schema(tmp_path):
    """Quatre ouvertures concurrentes voient la même base SQLite valide."""
    depart = Barrier(4)
    prive = tmp_path / "prive"

    def initialiser():
        depart.wait(timeout=5)
        base = Base(prive)
        try:
            return base.connexion.execute("PRAGMA application_id").fetchone()[0]
        finally:
            base.fermer()

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(lambda _: initialiser(), range(4))) == [APPLICATION_ID] * 4
    assert (prive / "export/manifest.json").is_file()


def test_echec_du_manifeste_nettoie_le_temporaire(atelier_initialise, monkeypatch):
    """Une erreur de renommage conserve le manifeste existant sans fichier temporaire résiduel."""
    base = Base(atelier_initialise, creer=False)
    export = base.prive / "export"
    avant = (export / "manifest.json").read_bytes()
    remplacer = Path.replace

    def echec(source, destination):
        if source.name.startswith(".manifest-"):
            raise OSError("renommage du manifeste injecté")
        return remplacer(source, destination)

    monkeypatch.setattr(Path, "replace", echec)
    try:
        with pytest.raises(OSError, match="manifeste injecté"):
            base.exporter()
        assert (export / "manifest.json").read_bytes() == avant
        assert not list(export.glob(".manifest-*"))
    finally:
        base.fermer()
