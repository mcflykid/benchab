"""Prépare une copie JSONL prouvée, sans l’appliquer à l’atelier."""

import hashlib
import json
import os
import sqlite3
import stat
from contextlib import closing, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from benchab.chemins import atelier_prive, sans_lien
from benchab.donnees.base import APPLICATION_ID, TABLES, _signature_export, lignes_export
from benchab.qualite.coherence import (
    _contrat_manifeste_export,
    _manifeste_export,
    _ouvrir_chaine_dossiers,
    _verifier_chaine_dossiers,
    _verifier_membre_export,
)
from benchab.qualite.ecritures import destination_neuve, renommer_exclusif
from benchab.qualite.empreintes import sha256_fichier
from benchab.qualite.plan_reparation import _entete_sqlite, _journal_en_mode_rollback

_MAX_OCTETS = 64 * 1024 * 1024


@contextmanager
def _dossier_export(racine):
    """Épingle le chemin et le dossier qui contient le manifeste canonique."""
    descripteurs, noms = _ouvrir_chaine_dossiers(racine)
    fd = None
    try:
        drapeaux = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
        fd = os.open("export", drapeaux, dir_fd=descripteurs[-1])
        signature = _signature_export(os.fstat(fd))

        def verifier():
            _verifier_chaine_dossiers(descripteurs, noms)
            info = os.stat("export", dir_fd=descripteurs[-1], follow_symlinks=False)
            if _signature_export(info) != signature or _signature_export(os.fstat(fd)) != signature:
                raise ValueError("Le dossier d’export a changé pendant la préparation.")

        verifier()
        yield fd, verifier
    finally:
        if fd is not None:
            os.close(fd)
        for descripteur in reversed(descripteurs):
            os.close(descripteur)


def _absence(fd, nom):
    try:
        os.stat(nom, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise ValueError("La cible canonique doit rester absente, sans lien ni fichier existant.")


def _signature_sqlite(chemin):
    info = chemin.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("Base SQLite régulière requise.")
    return _signature_export(info)


def _verifier_journal(chemin):
    entete = _entete_sqlite(chemin)
    if len(entete) != 20 or entete[:16] != b"SQLite format 3\0" or entete[18:20] != b"\x01\x01":
        raise ValueError("Format ou journal SQLite incompatible avec la lecture seule.")
    for suffixe in ("-wal", "-shm", "-journal"):
        try:
            chemin.with_name(chemin.name + suffixe).lstat()
        except FileNotFoundError:
            continue
        raise ValueError("Fichier auxiliaire de journal SQLite présent : reprendre plus tard.")


def _contrat(fd, version):
    erreurs, signatures, empreintes_brutes = [], {}, {}
    manifeste = _manifeste_export(fd, erreurs, signatures, empreintes_brutes=empreintes_brutes)
    empreintes, diagnostics = _contrat_manifeste_export(manifeste, version)
    if empreintes is None or erreurs or diagnostics:
        raise ValueError("Manifeste d’export inutilisable : " + "; ".join(erreurs + diagnostics))
    return empreintes, empreintes_brutes["manifest.json"], signatures["manifest.json"]


def _produire(connexion, table, fichier):
    empreinte = hashlib.sha256()
    octets = lignes = 0
    with fichier.open("xb") as flux:
        for ligne in lignes_export(connexion, table):
            lignes += 1
            octets += len(ligne)
            if octets > _MAX_OCTETS:
                raise ValueError("La table dépasse le budget de 64 Mio.")
            empreinte.update(ligne)
            flux.write(ligne)
        flux.flush()
        os.fsync(flux.fileno())
    return lignes, octets, empreinte.hexdigest()


def preparer(prive, table, sortie):
    """Publie une copie neuve d’une table absente, avec preuve JSON, hors export/.

    Reçoit la racine privée, une table BENCHAB et un dossier de sortie absent.
    PASS signifie que le JSONL (octets, lignes) relu égale l’empreinte du
    manifeste et le contenu d’un instantané SQLite en lecture seule.
    ValueError ou OSError signifie refus ; aucune copie n’est appliquée,
    aucun original ni table SQLite n’est modifié.
    """
    if table not in TABLES:
        raise ValueError("Table d’export BENCHAB inconnue.")
    racine = atelier_prive(prive)
    destination = destination_neuve(sortie, prive=racine)
    if destination.is_relative_to(racine / "export"):
        raise ValueError("La copie de preuve doit rester hors du dossier export.")
    base = sans_lien(racine / "benchab.sqlite", racine=racine, strict=True)
    signature_base = _signature_sqlite(base)
    _verifier_journal(base)
    nom = table + ".jsonl"
    with _dossier_export(racine) as (fd, verifier_dossier), closing(
        sqlite3.connect(base.as_uri() + "?mode=ro", uri=True, timeout=1)
    ) as connexion:
        _absence(fd, nom)
        connexion.row_factory = sqlite3.Row
        connexion.execute("PRAGMA query_only = ON")
        connexion.execute("BEGIN")
        identite = connexion.execute("PRAGMA application_id").fetchone()[0]
        version = connexion.execute("PRAGMA user_version").fetchone()[0]
        if identite != APPLICATION_ID or version != 1 or not _journal_en_mode_rollback(connexion):
            raise ValueError("Identité, schéma ou journal SQLite non qualifié.")
        empreintes, sha_manifeste, signature_manifeste = _contrat(fd, version)
        # Publier la preuve et sa copie ensemble, sans ouvrir la cible canonique.
        with TemporaryDirectory(prefix=".reprise-export-", dir=destination.parent) as temporaire:
            dossier = Path(temporaire)
            lignes, octets, sha = _produire(connexion, table, dossier / nom)
            if sha != empreintes[table] or sha256_fichier(dossier / nom) != sha:
                raise ValueError("L’empreinte de la copie diffère du manifeste ou de sa relecture.")
            rapport = {
                "schema": 1, "statut": "PASS", "applique": False,
                "table": table, "cible_canonique": "export/" + nom,
                "copie": nom, "lignes": lignes, "octets": octets, "sha256": sha,
                "manifeste_sha256": sha_manifeste, "version_sqlite": version,
                "limite": "Copie vérifiée d’une seule table, non appliquée ; aucune reconstruction de base.",
            }
            (dossier / "preuve.json").write_text(json.dumps(rapport, ensure_ascii=False, indent=2) + "\n",
                                                 encoding="utf-8")
            _verifier_journal(base)
            if _signature_sqlite(base) != signature_base:
                raise ValueError("La base SQLite a changé pendant la préparation.")
            _verifier_membre_export(fd, "manifest.json", signature_manifeste)
            _absence(fd, nom)
            verifier_dossier()
            renommer_exclusif(dossier, destination)
    return rapport
