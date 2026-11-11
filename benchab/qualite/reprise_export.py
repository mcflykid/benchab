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
    _empreinte_membre_export,
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


def _identite_dossier(chemin):
    """Retourne dev/inode d’un dossier réel, sans suivre de lien symbolique."""
    info = chemin.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError("Le dossier publié n’est pas un dossier régulier.")
    return info.st_dev, info.st_ino


@contextmanager
def _ouvrir_dossier_epingle(chemin):
    """Ouvre et ferme un dossier régulier sans suivre de lien symbolique."""
    fd = os.open(chemin, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        yield fd
    finally:
        os.close(fd)


def _sha_membre(dossier_fd, nom):
    """Réutilise l’empreinte bornée d’un membre depuis le dossier épinglé."""
    return _empreinte_membre_export(dossier_fd, nom)[0]


def preparer(prive, table, sortie):
    """Publie une copie neuve d’une table absente, avec preuve JSON, hors export/.

    Reçoit la racine privée, une table BENCHAB et un dossier de sortie absent.
    PASS signifie que le JSONL (octets, lignes) relu égale l’empreinte du
    manifeste et le contenu d’un instantané SQLite en lecture seule ; le JSONL
    et la preuve sont rehachés après renommage. Le fichier de preuve reste
    CANDIDAT ; seul le résultat de retour devient PASS après revérification.
    Une divergence laisse le dossier publié en place pour diagnostic. ValueError
    ou OSError signifie refus ;
    aucune copie n’est appliquée, aucun original ni table SQLite n’est modifié.
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
    with (
        _dossier_export(racine) as (fd, verifier_dossier),
        closing(sqlite3.connect(base.as_uri() + "?mode=ro", uri=True, timeout=1)) as connexion,
    ):
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
                "schema": 1,
                "statut": "CANDIDAT",
                "applique": False,
                "table": table,
                "cible_canonique": "export/" + nom,
                "copie": nom,
                "lignes": lignes,
                "octets": octets,
                "sha256": sha,
                "manifeste_sha256": sha_manifeste,
                "version_sqlite": version,
                "sha256_attendu": sha,
                "verification": (
                    "Revérification requise après publication ; le PASS est renvoyé séparément "
                    "après lecture des deux membres."
                ),
                "limite": "Copie vérifiée d’une seule table, non appliquée ; aucune reconstruction de base.",
            }
            octets_preuve = (json.dumps(rapport, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            (dossier / "preuve.json").write_bytes(octets_preuve)
            # L’empreinte attendue vient des octets destinés à la preuve,
            # jamais de la première relecture du fichier en cours de publication.
            attendus = {nom: sha, "preuve.json": hashlib.sha256(octets_preuve).hexdigest()}
            with _ouvrir_dossier_epingle(dossier) as stage_fd:
                info_stage = os.fstat(stage_fd)
                identite_stage = (info_stage.st_dev, info_stage.st_ino)
            _verifier_journal(base)
            if _signature_sqlite(base) != signature_base:
                raise ValueError("La base SQLite a changé pendant la préparation.")
            _verifier_membre_export(fd, "manifest.json", signature_manifeste)
            _absence(fd, nom)
            verifier_dossier()
            if _identite_dossier(dossier) != identite_stage:
                raise ValueError("Le dossier temporaire a été remplacé avant publication.")
            with _ouvrir_dossier_epingle(dossier) as stage_fd:
                info_stage = os.fstat(stage_fd)
                if (info_stage.st_dev, info_stage.st_ino) != identite_stage:
                    raise ValueError("Le dossier temporaire a été remplacé avant publication.")
                if {membre: _sha_membre(stage_fd, membre) for membre in attendus} != attendus:
                    raise ValueError("Les SHA du dossier temporaire diffèrent des octets attendus.")
                renommer_exclusif(dossier, destination)
                if _identite_dossier(destination) != identite_stage:
                    raise ValueError("La cible ne désigne plus le dossier préparé ; aucun tiers déplacé.")
                try:
                    dest_fd = os.open(destination, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                    try:
                        identite_publiee = (os.fstat(dest_fd).st_dev, os.fstat(dest_fd).st_ino)
                        if identite_publiee != identite_stage:
                            raise ValueError("La cible ne désigne plus le dossier préparé ; aucun tiers déplacé.")
                        reels = {membre: _sha_membre(dest_fd, membre) for membre in attendus}
                    finally:
                        os.close(dest_fd)
                    if _identite_dossier(destination) != identite_stage:
                        raise ValueError("La cible ne désigne plus le dossier préparé ; aucun tiers déplacé.")
                    if reels != attendus:
                        raise ValueError("Les SHA des fichiers publiés diffèrent du stage épinglé.")
                except (OSError, ValueError) as erreur:
                    if _identite_dossier(destination) != identite_stage:
                        raise ValueError(
                            "La cible publiée a changé ; elle est laissée en place sans déplacement."
                        ) from erreur
                    raise ValueError(
                        "La vérification après publication a échoué ; le dossier est laissé en place pour diagnostic."
                    ) from erreur
    resultat = dict(rapport)
    resultat.update(
        statut="PASS",
        statut_preuve_fichier="CANDIDAT",
        preuve_sha256=attendus["preuve.json"],
        sha256_attendus=attendus,
        verification_post_publication="PASS",
        verification="PASS après relecture exacte du JSONL et de la preuve publiés.",
    )
    return resultat
