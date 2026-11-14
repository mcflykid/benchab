"""Régressions d'arrondi, codes CLI et robustesse des écritures SQLite."""

import hashlib
import json
import os
import stat
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from benchab import cli
from benchab.controles import decollement
from benchab.donnees.base import APPLICATION_ID, TABLES, Base
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
    (export / "manifest.json").write_bytes(b'{"corrompu":true}\n')
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


def test_export_identique_conserve_inode_et_octets(atelier_initialise, monkeypatch):
    """Un export canonique déjà présent ne remplace ni les JSONL ni le manifeste."""
    base = Base(atelier_initialise, creer=False)
    export = base.prive / "export"
    fichiers = [export / f"{table}.jsonl" for table in TABLES]
    fichiers.append(export / "manifest.json")
    avant = {f: (f.stat().st_ino, f.read_bytes()) for f in fichiers}
    monkeypatch.setattr("benchab.donnees.base.tempfile.mkstemp", lambda **_: pytest.fail("temporaire inattendu"))
    try:
        base.exporter()
        assert {f: (f.stat().st_ino, f.read_bytes()) for f in fichiers} == avant
    finally:
        base.fermer()


def test_export_corrompu_est_reconstruit_depuis_sqlite(atelier_initialise):
    """Un manifeste inchangé ne masque pas un JSONL altéré, que le canon répare."""
    base = Base(atelier_initialise, creer=False)
    export = base.prive / "export"
    fichier = export / "projets.jsonl"
    canon = fichier.read_bytes()
    fichier.write_bytes(b'{"id":"corrompu"}\n')
    try:
        base.exporter()
        repare = fichier.read_bytes()
        manifeste = json.loads((export / "manifest.json").read_text())
        assert repare == canon
        assert manifeste["sha256"]["projets"] == hashlib.sha256(repare).hexdigest()
        assert all(
            manifeste["sha256"][table] == hashlib.sha256((export / f"{table}.jsonl").read_bytes()).hexdigest()
            for table in TABLES
        )
    finally:
        base.fermer()


def test_manifeste_altere_est_reconstruit_apres_verification_jsonl(atelier_initialise):
    """Le manifeste se répare depuis les SHA calculés, sans toucher aux JSONL concordants."""
    base = Base(atelier_initialise, creer=False)
    export = base.prive / "export"
    avant = {table: (export / f"{table}.jsonl").stat().st_ino for table in TABLES}
    (export / "manifest.json").write_bytes(b'{"manifeste":"invalide"}\n')
    try:
        base.exporter()
        manifeste = json.loads((export / "manifest.json").read_text())
        assert all(
            manifeste["sha256"][table] == hashlib.sha256((export / f"{table}.jsonl").read_bytes()).hexdigest()
            for table in TABLES
        )
        assert {table: (export / f"{table}.jsonl").stat().st_ino for table in TABLES} == avant
    finally:
        base.fermer()


def test_export_refuse_une_table_speciale(atelier_initialise):
    """Un FIFO dans le dossier d’export reste en place et interrompt l’écriture."""
    base = Base(atelier_initialise, creer=False)
    fifo = base.prive / "export/projets.jsonl"
    fifo.unlink()
    os.mkfifo(fifo)
    try:
        with pytest.raises(ValueError, match="Export spécial refusé"):
            base.exporter()
        assert stat.S_ISFIFO(fifo.stat().st_mode)
    finally:
        fifo.unlink()
        base.fermer()


def test_export_refuse_une_table_liee(atelier_initialise, tmp_path):
    """Un JSONL lié est refusé sans modifier sa cible."""
    base = Base(atelier_initialise, creer=False)
    cible = tmp_path / "cible.jsonl"
    cible.write_bytes(b"preuve intacte")
    lien = base.prive / "export/projets.jsonl"
    lien.unlink()
    lien.symlink_to(cible)
    try:
        with pytest.raises(ValueError, match="Export lié refusé"):
            base.exporter()
        assert cible.read_bytes() == b"preuve intacte"
        assert lien.is_symlink()
    finally:
        lien.unlink()
        base.fermer()


def test_export_refuse_un_manifeste_lie(atelier_initialise, tmp_path):
    """Un manifeste lié est refusé après contrôle des JSONL, sans écraser sa cible."""
    base = Base(atelier_initialise, creer=False)
    cible = tmp_path / "manifeste-cible.json"
    cible.write_bytes(b"preuve intacte")
    lien = base.prive / "export/manifest.json"
    lien.unlink()
    lien.symlink_to(cible)
    try:
        with pytest.raises(ValueError, match="Export lié refusé"):
            base.exporter()
        assert cible.read_bytes() == b"preuve intacte"
        assert lien.is_symlink()
    finally:
        lien.unlink()
        base.fermer()


def test_export_refuse_dossier_remplace_par_un_lien(atelier_initialise, tmp_path):
    """Un atelier déjà ouvert ne publie pas dans un autre dossier lié."""
    base = Base(atelier_initialise, creer=False)
    dossier = base.prive / "export"
    avant = base.prive / "export-original"
    autre = tmp_path / "destination-temoin"
    autre.mkdir()
    dossier.rename(avant)
    dossier.symlink_to(autre, target_is_directory=True)
    try:
        with pytest.raises(ValueError, match="Lien symbolique refusé"):
            base.exporter()
        assert not list(autre.iterdir())
        assert (avant / "manifest.json").is_file()
    finally:
        dossier.unlink()
        avant.rename(dossier)
        base.fermer()


def test_export_ne_suit_pas_un_verrou_remplace_apres_validation(atelier_initialise, tmp_path, monkeypatch):
    """Un verrou devenu lien après la validation ne crée pas sa cible et ne lance aucun export."""
    from benchab import chemins

    base = Base(atelier_initialise, creer=False)
    verrou = base.prive / "cache/export.lock"
    cible = tmp_path / "cible-a-preserver"
    original = chemins.sans_lien

    def remplacer(chemin, **options):
        """Simule un changement local entre la garde et l'ouverture, sans modifier la cible."""
        resultat = original(chemin, **options)
        if resultat in (verrou, verrou.parent):
            verrou.unlink(missing_ok=True)
            verrou.symlink_to(cible)
        return resultat

    monkeypatch.setattr(chemins, "sans_lien", remplacer)
    try:
        with pytest.raises((OSError, ValueError)):
            base.exporter()
        assert verrou.is_symlink()
        assert not cible.exists()
    finally:
        base.fermer()
