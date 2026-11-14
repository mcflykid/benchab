"""Prépare ou applique une seule reprise d’export depuis un candidat privé."""

import fcntl
import hashlib
import json
import os
import re
import sqlite3
import stat
from contextlib import closing, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from benchab.chemins import atelier_prive, sans_lien
from benchab.donnees.base import APPLICATION_ID, TABLES, _signature_export, lignes_export
from benchab.qualite.coherence import (
    _cles_uniques,
    _contrat_manifeste_export,
    _empreinte_membre_export,
    _manifeste_export,
    _ouvrir_chaine_dossiers,
    _ouvrir_membre_export,
    _verifier_chaine_dossiers,
    _verifier_membre_export,
)
from benchab.qualite.ecritures import (
    acquerir_verrou,
    destination_neuve,
    ouvrir_verrou,
    renommer_exclusif,
    sortie_privee,
)
from benchab.qualite.empreintes import sha256_fichier
from benchab.qualite.plan_reparation import _entete_sqlite, _journal_en_mode_rollback

_MAX_OCTETS = 64 * 1024 * 1024


@contextmanager
def _dossier_export(racine, *, tolere_modifications=False):
    """Épingle le dossier canonique ; optionnellement tolère l’ajout du seul lien contrôlé."""
    descripteurs, noms = _ouvrir_chaine_dossiers(racine)
    fd = None
    try:
        drapeaux = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
        fd = os.open("export", drapeaux, dir_fd=descripteurs[-1])
        signature = _signature_export(os.fstat(fd))

        def verifier():
            _verifier_chaine_dossiers(descripteurs, noms)
            info = os.stat("export", dir_fd=descripteurs[-1], follow_symlinks=False)
            courant = _signature_export(info)
            epingle = _signature_export(os.fstat(fd))
            if tolere_modifications:
                # Le lien exclusif de reprise change ctime/mtime du dossier (#119).
                courant = courant[:3]
                epingle = epingle[:3]
                attendu = signature[:3]
            else:
                attendu = signature
            if courant != attendu or epingle != attendu:
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


def _identite_fichier(info):
    """Identité stable d’un fichier régulier malgré taille et ctime modifiés au stage."""
    return info.st_dev, info.st_ino, info.st_mode


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


@contextmanager
def _verrou_export(racine):
    """Épingle cache/export.lock comme Base.exporter, sans créer l’atelier."""
    descripteurs, noms = _ouvrir_chaine_dossiers(racine / "cache")
    verrou = None
    try:
        cache_fd = descripteurs[-1]
        signature_cache = _signature_export(os.fstat(cache_fd))[:3]
        verrou = ouvrir_verrou("export.lock", cache_fd)
        acquerir_verrou(verrou, delai=2.0)

        def verifier():
            _verifier_chaine_dossiers(descripteurs, noms)
            entree_cache = os.stat("cache", dir_fd=descripteurs[-2], follow_symlinks=False)
            entree_verrou = os.stat("export.lock", dir_fd=cache_fd, follow_symlinks=False)
            if (
                _signature_export(entree_cache)[:3] != signature_cache
                or _signature_export(os.fstat(cache_fd))[:3] != signature_cache
                or _signature_export(entree_verrou) != _signature_export(os.fstat(verrou))
            ):
                raise ValueError("Cache ou verrou d’export remplacé pendant l’application.")

        verifier()
        yield verifier
    finally:
        if verrou is not None:
            fcntl.flock(verrou, fcntl.LOCK_UN)
            os.close(verrou)
        for descripteur in reversed(descripteurs):
            os.close(descripteur)


def _ouvrir_fichier_candidat(dossier_fd, nom, budget):
    """Ouvre un fichier ordinaire lié au dossier candidat, sans suivre de lien."""
    entree = os.stat(nom, dir_fd=dossier_fd, follow_symlinks=False)
    if not stat.S_ISREG(entree.st_mode) or entree.st_size > budget:
        raise ValueError("Fichier candidat lié, spécial ou au-delà du budget.")
    signature = _signature_export(entree)
    fd = os.open(nom, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dossier_fd)
    if _signature_export(os.fstat(fd)) != signature:
        os.close(fd)
        raise ValueError("Fichier candidat remplacé pendant son ouverture.")
    return fd, signature


def _lire_fichier_candidat(dossier_fd, nom, budget):
    """Lit un JSON borné et épinglé ; renvoie octets, SHA et signature."""
    fd, signature = _ouvrir_fichier_candidat(dossier_fd, nom, budget)
    try:
        with os.fdopen(fd, "rb", closefd=False) as flux:
            octets = flux.read(budget + 1)
        if len(octets) > budget or _signature_export(os.fstat(fd)) != signature:
            raise ValueError("Fichier candidat modifié ou hors budget.")
        entree = os.stat(nom, dir_fd=dossier_fd, follow_symlinks=False)
        if _signature_export(entree) != signature:
            raise ValueError("Fichier candidat remplacé après lecture.")
        return octets, hashlib.sha256(octets).hexdigest(), signature
    finally:
        os.close(fd)


def _creer_stage(fd_export):
    """Crée un membre temporaire exclusif dans le dossier d’export épinglé."""
    import secrets

    for _ in range(16):
        nom = ".reprise-" + secrets.token_hex(12)
        try:
            fd = os.open(
                nom,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK,
                0o600,
                dir_fd=fd_export,
            )
            return nom, fd, _identite_fichier(os.fstat(fd))
        except FileExistsError:
            continue
    raise FileExistsError("Impossible de réserver un stage neuf dans export/.")


def _hachage_fd_borne(fd, budget):
    """Hache au plus budget + 1 octets depuis le début d’un descripteur."""
    if type(budget) is not int or budget < 0:
        raise ValueError("Budget de lecture invalide.")
    hachage = hashlib.sha256()
    position = 0
    while position <= budget:
        taille = min(1024 * 1024, budget + 1 - position)
        bloc = os.pread(fd, taille, position)
        if not bloc:
            return hachage.hexdigest(), position
        position += len(bloc)
        if position > budget:
            raise ValueError("Fichier candidat au-delà du budget de lecture.")
        hachage.update(bloc)
    raise ValueError("Fichier candidat au-delà du budget de lecture.")


def _ecrire_tout(fd, octets):
    """Écrit un bloc complet ; une écriture nulle ou négative est un refus."""
    reste = memoryview(octets)
    while reste:
        ecrit = os.write(fd, reste)
        if ecrit <= 0:
            raise OSError("Écriture nulle du stage d’export.")
        reste = reste[ecrit:]


def _publier_stage_exclusif(source, destination, export_fd):
    """Renomme un stage par dirfd, sans alias ni remplacement."""
    renommer_exclusif(source, destination, src_dir_fd=export_fd, dst_dir_fd=export_fd)


def _verifier_cible_finale(fd_export, nom, fd_stage, sha_attendu, taille_attendue):
    """Contrôle en dernière opération le membre publié depuis son dossier épinglé."""
    entree = os.stat(nom, dir_fd=fd_export, follow_symlinks=False)
    fd = os.open(nom, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd_export)
    try:
        signature = _signature_export(os.fstat(fd))
        if (
            not stat.S_ISREG(entree.st_mode)
            or not os.path.samestat(os.fstat(fd_stage), os.fstat(fd))
            or _signature_export(entree) != signature
            or signature[3] != taille_attendue
        ):
            raise ValueError("La cible canonique ne désigne pas le stage créé.")
        sha, taille = _hachage_fd_borne(fd, _MAX_OCTETS)
        if sha != sha_attendu or taille != taille_attendue:
            raise ValueError("Le SHA final de la cible diffère du candidat.")
        if _signature_export(os.fstat(fd)) != signature:
            raise ValueError("La cible canonique a changé pendant sa relecture.")
        entree_fin = os.stat(nom, dir_fd=fd_export, follow_symlinks=False)
        if _signature_export(entree_fin) != signature:
            raise ValueError("La cible canonique a été remplacée après sa relecture.")
    finally:
        os.close(fd)


def verifier_copie(prive, preuve, sha256_preuve):
    """Relit une preuve d’export et sa copie en octets, puis observe la cible.

    Renvoie PASS pour l’identité de la copie. La cible peut être ABSENTE,
    IDENTIQUE ou DIVERGENTE. Aucun verrou n’est créé, aucune reprise appliquée.
    La décision d’appliquer exige ensuite la confrontation à SQLite et au manifeste.
    """
    return _reprendre(prive, preuve, sha256_preuve, lecture_seule=True)


def appliquer(prive, preuve, sha256_preuve):
    """Applique une seule table absente depuis une preuve CANDIDAT épinglée.

    Entrées : racine privée, chemin de preuve JSON et SHA-256 déclaré par l’appelant.
    Sortie : rapport PASS uniquement après publication exclusive et relecture.
    Refus : OSError/ValueError/SQLite ; aucune écriture SQLite, modification de la
    preuve ou suppression de la cible canonique. Le verrou cache/export.lock reste
    détenu pendant le snapshot, la sérialisation et la publication.
    """
    return _reprendre(prive, preuve, sha256_preuve, lecture_seule=False)


def _reprendre(prive, preuve, sha256_preuve, *, lecture_seule):
    racine = atelier_prive(prive)
    chemin_preuve = sortie_privee(Path(preuve).absolute(), prive=racine)
    if chemin_preuve.is_relative_to(racine / "export"):
        raise ValueError("La preuve CANDIDAT doit rester hors du dossier export.")
    if not re.fullmatch(r"[0-9a-f]{64}", str(sha256_preuve)):
        raise ValueError("SHA-256 de preuve attendu en hexadécimal.")
    parent_preuve = chemin_preuve.parent
    descripteurs_preuve, noms_preuve = _ouvrir_chaine_dossiers(parent_preuve)
    fd_candidat = descripteurs_preuve[-1]
    nom_preuve = chemin_preuve.name
    try:
        preuve_octets, sha_reel_preuve, signature_preuve = _lire_fichier_candidat(
            fd_candidat, nom_preuve, 100_000
        )
        if sha_reel_preuve != sha256_preuve:
            raise ValueError("SHA de la preuve différent du SHA déclaré.")
        try:
            donnees = json.loads(preuve_octets, object_pairs_hook=_cles_uniques)
        except (UnicodeError, ValueError, RecursionError) as erreur:
            raise ValueError("Preuve JSON invalide ou ambiguë.") from erreur
        if not isinstance(donnees, dict):
            raise ValueError("Objet JSON de preuve requis.")
        table = donnees.get("table")
        sha_table = donnees.get("sha256")
        sha_attendu = donnees.get("sha256_attendu")
        sha_manifeste_attendu = donnees.get("manifeste_sha256")
        nom_table = donnees.get("copie")
        if (
            type(donnees.get("schema")) is not int
            or donnees["schema"] != 1
            or donnees.get("statut") != "CANDIDAT"
            or donnees.get("applique") is not False
            or not isinstance(table, str)
            or table not in TABLES
            or donnees.get("cible_canonique") != "export/" + str(table) + ".jsonl"
            or nom_table != str(table) + ".jsonl"
            or not isinstance(sha_table, str)
            or not re.fullmatch(r"[0-9a-f]{64}", sha_table)
            or sha_attendu != sha_table
            or not isinstance(sha_manifeste_attendu, str)
            or not re.fullmatch(r"[0-9a-f]{64}", sha_manifeste_attendu)
            or type(donnees.get("version_sqlite")) is not int
            or donnees["version_sqlite"] != 1
            or type(donnees.get("lignes")) is not int
            or donnees["lignes"] < 0
            or type(donnees.get("octets")) is not int
            or not 0 <= donnees["octets"] <= _MAX_OCTETS
        ):
            raise ValueError("Preuve CANDIDAT incomplète ou non applicable.")

        fd_table, signature_table = _ouvrir_fichier_candidat(fd_candidat, nom_table, _MAX_OCTETS)
        try:
            if lecture_seule:
                digest, taille = _hachage_fd_borne(fd_table, _MAX_OCTETS)
                if (digest, taille) != (sha_table, donnees["octets"]):
                    raise ValueError("Les octets du JSONL diffèrent de la preuve CANDIDAT.")
                nom_canonique = table + ".jsonl"
                with _dossier_export(racine) as (fd_export, verifier_export):
                    try:
                        fd_canonique, signature_canonique = _ouvrir_membre_export(fd_export, nom_canonique)
                    except FileNotFoundError:
                        courant = None
                        _absence(fd_export, nom_canonique)
                    else:
                        try:
                            courant, _ = _hachage_fd_borne(fd_canonique, _MAX_OCTETS)
                            if _signature_export(os.fstat(fd_canonique)) != signature_canonique:
                                raise ValueError("L’export canonique a changé pendant sa lecture.")
                            _verifier_membre_export(fd_export, nom_canonique, signature_canonique)
                        finally:
                            os.close(fd_canonique)
                    verifier_export()
                # Relire les deux membres et leur chemin après l’observation de la cible (#119).
                if (_signature_export(os.fstat(fd_table)) != signature_table
                        or _signature_export(os.stat(nom_table, dir_fd=fd_candidat, follow_symlinks=False))
                        != signature_table
                        or _lire_fichier_candidat(fd_candidat, nom_preuve, 100_000)
                        != (preuve_octets, sha_reel_preuve, signature_preuve)):
                    raise ValueError("La preuve ou le candidat a changé pendant la vérification.")
                _verifier_chaine_dossiers(descripteurs_preuve, noms_preuve)
                etat = "ABSENTE" if courant is None else "IDENTIQUE" if courant == sha_table else "DIVERGENTE"
                return {"statut": "PASS", "applique": False, "table": table, "preuve_sha256": sha_reel_preuve,
                        "copie_sha256": digest, "octets": taille, "cible": {"statut": etat, "sha256": courant},
                        "compatibilite_sqlite_et_manifeste": "NON VÉRIFIÉ"}
            racine = atelier_prive(prive)
            base = sans_lien(racine / "benchab.sqlite", racine=racine, strict=True)
            signature_base = _signature_sqlite(base)
            _verifier_journal(base)
            nom_canonique = table + ".jsonl"
            with (
                _dossier_export(racine, tolere_modifications=True) as (fd_export, verifier_export),
                _verrou_export(racine) as verifier_verrou,
                closing(sqlite3.connect(base.as_uri() + "?mode=ro", uri=True, timeout=1)) as connexion,
            ):
                _absence(fd_export, nom_canonique)
                connexion.row_factory = sqlite3.Row
                connexion.execute("PRAGMA query_only = ON")
                connexion.execute("BEGIN")
                connexion.execute("SELECT name FROM sqlite_schema LIMIT 1").fetchall()
                identite = connexion.execute("PRAGMA application_id").fetchone()[0]
                version = connexion.execute("PRAGMA user_version").fetchone()[0]
                if identite != APPLICATION_ID or version != 1 or not _journal_en_mode_rollback(connexion):
                    raise ValueError("Identité, schéma ou journal SQLite non qualifié.")
                empreintes, sha_manifeste, signature_manifeste = _contrat(fd_export, version)
                if sha_manifeste != sha_manifeste_attendu or empreintes.get(table) != sha_table:
                    raise ValueError("Le manifeste courant diffère de la preuve ou du SHA de la table.")

                nom_stage, fd_stage, identite_stage = _creer_stage(fd_export)
                try:
                    hachage = hashlib.sha256()
                    octets = lignes = 0
                    while True:
                        bloc = os.read(fd_table, 1024 * 1024)
                        if not bloc:
                            break
                        octets += len(bloc)
                        if octets > _MAX_OCTETS:
                            raise ValueError("Le JSONL dépasse le budget de 64 Mio.")
                        hachage.update(bloc)
                        _ecrire_tout(fd_stage, bloc)
                    sha_table_lu = hachage.hexdigest()
                    if octets != donnees["octets"] or sha_table_lu != sha_table:
                        raise ValueError("Les octets du JSONL diffèrent de la preuve CANDIDAT.")
                    if _signature_export(os.fstat(fd_table)) != signature_table:
                        raise ValueError("Le JSONL candidat a changé pendant sa lecture.")
                    entree_table = os.stat(nom_table, dir_fd=fd_candidat, follow_symlinks=False)
                    if _signature_export(entree_table) != signature_table:
                        raise ValueError("Le JSONL candidat a été remplacé après sa lecture.")

                    os.lseek(fd_stage, 0, os.SEEK_SET)
                    with os.fdopen(os.dup(fd_stage), "rb") as candidat_flux:
                        for ligne in lignes_export(connexion, table):
                            lue = candidat_flux.readline(_MAX_OCTETS + 1)
                            lignes += 1
                            if lue != ligne:
                                raise ValueError("Le JSONL candidat diffère de la sérialisation SQLite.")
                        if candidat_flux.read(1):
                            raise ValueError("Le JSONL candidat contient des octets en trop.")
                    if lignes != donnees["lignes"]:
                        raise ValueError("Le nombre de lignes du candidat diffère de sa preuve.")

                    os.fsync(fd_stage)
                    preuve_avant_publication, sha_preuve_avant, signature_preuve_avant = _lire_fichier_candidat(
                        fd_candidat, nom_preuve, 100_000
                    )
                    if (
                        preuve_avant_publication != preuve_octets
                        or sha_preuve_avant != sha256_preuve
                        or signature_preuve_avant != signature_preuve
                    ):
                        raise ValueError("La preuve candidate a changé avant publication.")
                    _verifier_chaine_dossiers(descripteurs_preuve, noms_preuve)
                    verifier_export()
                    verifier_verrou()
                    _absence(fd_export, nom_canonique)
                    if _signature_sqlite(base) != signature_base:
                        raise ValueError("La base SQLite a changé pendant l’application.")
                    _verifier_membre_export(fd_export, "manifest.json", signature_manifeste)
                    if _sha_membre(fd_export, "manifest.json") != sha_manifeste:
                        raise ValueError("Le manifeste d’export a changé avant publication.")
                    entree_stage = os.stat(nom_stage, dir_fd=fd_export, follow_symlinks=False)
                    if (
                        not stat.S_ISREG(entree_stage.st_mode)
                        or _identite_fichier(entree_stage) != identite_stage
                        or entree_stage.st_size != octets
                        or _sha_membre(fd_export, nom_stage) != sha_table
                    ):
                        raise ValueError("Le stage neuf diffère des octets du candidat avant publication.")
                    _publier_stage_exclusif(nom_stage, nom_canonique, fd_export)
                    try:
                        os.fsync(fd_export)
                        _verifier_chaine_dossiers(descripteurs_preuve, noms_preuve)
                        octets_preuve_fin, sha_preuve_fin, signature_preuve_fin = _lire_fichier_candidat(
                            fd_candidat, nom_preuve, 100_000
                        )
                        if (
                            octets_preuve_fin != preuve_octets
                            or sha_preuve_fin != sha256_preuve
                            or signature_preuve_fin != signature_preuve
                        ):
                            raise ValueError("La preuve candidate a changé après publication.")
                        hachage_candidat_fin, taille_candidat_fin = _hachage_fd_borne(fd_table, _MAX_OCTETS)
                        if (
                            hachage_candidat_fin != sha_table
                            or taille_candidat_fin != donnees["octets"]
                            or _signature_export(os.fstat(fd_table)) != signature_table
                            or _signature_export(os.stat(nom_table, dir_fd=fd_candidat, follow_symlinks=False))
                            != signature_table
                        ):
                            raise ValueError("Le JSONL candidat a changé après publication.")
                        _verifier_membre_export(fd_export, "manifest.json", signature_manifeste)
                        if _sha_membre(fd_export, "manifest.json") != sha_manifeste:
                            raise ValueError("Manifeste modifié après publication.")
                        verifier_export()
                        verifier_verrou()
                        if _signature_sqlite(base) != signature_base:
                            raise ValueError("Base modifiée après publication.")
                        _verifier_journal(base)
                        _verifier_chaine_dossiers(descripteurs_preuve, noms_preuve)
                        verifier_export()
                        verifier_verrou()
                        _verifier_cible_finale(fd_export, nom_canonique, fd_stage, sha_table, octets)
                        # Le dirfd peut encore lire l'ancien dossier après un
                        # remplacement ; l'entrée canonique doit lui correspondre.
                        verifier_export()
                    except (OSError, ValueError, sqlite3.Error) as erreur:
                        raise ValueError(
                            f"La cible export/{nom_canonique} a été créée et est laissée en place ; "
                            "le contrôle a échoué ; "
                            f"aucune suppression n’a été tentée : {erreur}"
                        ) from erreur
                finally:
                    os.close(fd_stage)
        finally:
            os.close(fd_table)
    finally:
        for descripteur in reversed(descripteurs_preuve):
            os.close(descripteur)
    return {
        "schema": 1,
        "statut": "PASS",
        "applique": True,
        "table": table,
        "cible_canonique": "export/" + nom_canonique,
        "sha256": sha_table,
        "preuve_sha256": sha_reel_preuve,
        "manifeste_sha256": sha_manifeste,
        "version_sqlite": version,
        "verification": "PASS après publication exclusive et relecture du JSONL, du manifeste et du snapshot SQLite.",
        "limite": "Une table absente seulement ; candidat et manifeste conservés, aucune autre table modifiée.",
    }


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
