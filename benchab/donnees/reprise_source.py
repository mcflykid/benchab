"""Prépare une copie isolée d’un original identifié dans une sauvegarde BENCHAB."""

import hashlib
import json
import os
import re
import stat
import tarfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory

from benchab.chemins import atelier_prive, sans_lien
from benchab.donnees.sauvegarde import (
    MANIFEST_NAME,
    MAX_MANIFEST,
    MAX_MEMBER,
    MAX_TOTAL,
    _nom_archive,
    _valider_manifeste,
)
from benchab.qualite.ecritures import destination_neuve, renommer_exclusif

_MAX_ENTREES = 100_000
_MAX_TAILLE_EXTENSION = 8 * 1024**2
_MAX_ENTETES_EXTENSION = _MAX_ENTREES
_MAX_PROFONDEUR_EXTENSION = 8
_MAX_ARCHIVE_OCTETS = 65 * 1024**3
_TAILLE_BLOC = 1024**2
_MAX_CARACTERES_CHEMIN = 4096
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,47}\Z")


class _TarInfoBorne(tarfile.TarInfo):
    """Borne les métadonnées d’extension avant le parseur standard du tar."""

    @staticmethod
    def _compter_extension(archive):
        nombre = getattr(archive, "_benchab_entetes_extension", 0) + 1
        if nombre > _MAX_ENTETES_EXTENSION:
            raise ValueError("L’archive dépasse le budget d’entêtes d’extension TAR.")
        archive._benchab_entetes_extension = nombre
        profondeur = getattr(archive, "_benchab_profondeur_extension", 0) + 1
        if profondeur > _MAX_PROFONDEUR_EXTENSION:
            raise ValueError("L’archive dépasse le budget d’imbrication des extensions TAR.")
        archive._benchab_profondeur_extension = profondeur

    def _proc_pax(self, archive):
        """Borne un corps PAX et refuse les métadonnées sparse avant leur interprétation."""
        self._compter_extension(archive)
        if not 0 <= self.size <= _MAX_TAILLE_EXTENSION:
            raise ValueError("Un entête TAR dépasse le budget de 8 Mio.")
        position = archive.fileobj.tell()
        corps = archive.fileobj.read(self.size)
        if len(corps) != self.size:
            raise tarfile.ReadError("Corps d’entête PAX tronqué")
        if b"GNU.sparse." in corps:
            raise ValueError("Les métadonnées TAR sparse sont refusées.")
        archive.fileobj.seek(position)
        try:
            return super()._proc_pax(archive)
        finally:
            archive._benchab_profondeur_extension -= 1

    def _proc_gnulong(self, archive):
        """Borne le nom GNU long avant que tarfile ne lise son corps."""
        self._compter_extension(archive)
        if not 0 <= self.size <= _MAX_CARACTERES_CHEMIN + 1:
            raise ValueError("Un nom long TAR dépasse le budget de 4 096 caractères.")
        try:
            return super()._proc_gnulong(archive)
        finally:
            archive._benchab_profondeur_extension -= 1

    def _proc_sparse(self, archive):
        """Refuse tout membre sparse avant collecte de ses blocs complémentaires."""
        raise ValueError("Les membres TAR sparse sont refusés.")


def _objet_sans_doublons(paires):
    """Décode un objet JSON de manifeste sans perdre une clé dupliquée."""
    resultat = {}
    for cle, valeur in paires:
        if cle in resultat:
            raise ValueError("Manifeste ambigu : clé JSON dupliquée.")
        resultat[cle] = valeur
    return resultat


def _constante_json_interdite(nom):
    """Refuse les constantes non JSON (NaN et infinis) dans le manifeste."""
    raise ValueError("Manifeste JSON invalide : constante numérique non finie " + nom + ".")


def _verifier_archive(source, flux, signature):
    """Revalide le fichier TAR épinglé et son nom jusqu’à la publication."""
    epingle = _signature(os.fstat(flux.fileno()))
    chemin = _signature(source.lstat())
    if epingle != _signature(signature) or chemin != epingle:
        raise ValueError("L’archive a changé pendant la préparation de la copie.")


def _signature(info):
    """Construit une identité de fichier ordinaire ; tailles et horodatages en octets/ns."""
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _lire_sha_archive(flux):
    """Calcule le SHA-256 d’un tar régulier sans dépasser le budget en octets."""
    empreinte = hashlib.sha256()
    total = 0
    while bloc := flux.read(_TAILLE_BLOC):
        total += len(bloc)
        if total > _MAX_ARCHIVE_OCTETS:
            raise ValueError("L’archive dépasse le budget de 65 Gio.")
        empreinte.update(bloc)
    return empreinte.hexdigest(), total


@contextmanager
def _ouvrir_archive(chemin, sha_attendu):
    """Épingle un tar non lié et vérifie son SHA et son identité avant de céder le flux."""
    source = sans_lien(Path(chemin).expanduser().absolute(), strict=True)
    avant_chemin = source.lstat()
    if stat.S_ISLNK(avant_chemin.st_mode) or not stat.S_ISREG(avant_chemin.st_mode):
        raise ValueError("L’archive doit être un fichier tar régulier, sans lien.")
    if avant_chemin.st_size > _MAX_ARCHIVE_OCTETS:
        raise ValueError("L’archive dépasse le budget de 65 Gio.")
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        with os.fdopen(fd, "rb") as flux:
            fd = -1
            avant_fd = os.fstat(flux.fileno())
            if not stat.S_ISREG(avant_fd.st_mode) or _signature(avant_fd) != _signature(avant_chemin):
                raise ValueError("L’archive a changé entre son identification et son ouverture.")
            sha_reel, taille = _lire_sha_archive(flux)
            if taille != avant_fd.st_size or sha_reel != sha_attendu:
                raise ValueError("Le SHA-256 de l’archive diffère de la déclaration.")
            apres_sha = os.fstat(flux.fileno())
            courant_chemin = source.lstat()
            if _signature(apres_sha) != _signature(avant_fd) or _signature(courant_chemin) != _signature(avant_fd):
                raise ValueError("L’archive a changé pendant son empreinte.")
            flux.seek(0)
            yield source, flux, avant_fd, taille
            apres = os.fstat(flux.fileno())
            courant = source.lstat()
            if _signature(apres) != _signature(avant_fd) or _signature(courant) != _signature(avant_fd):
                raise ValueError("L’archive a changé pendant la préparation de la copie.")
    finally:
        if fd >= 0:
            os.close(fd)


def _nom_source(nom):
    """Valide le chemin relatif `projets/SLUG/sources/FICHIER`, sans lien ni unité."""
    if not isinstance(nom, str):
        raise ValueError("Un membre source explicite est requis.")
    if len(nom) > _MAX_CARACTERES_CHEMIN:
        raise ValueError("Le membre source dépasse 4 096 caractères.")
    chemin = _nom_archive(nom)
    if nom != chemin.as_posix() or len(chemin.parts) != 4:
        raise ValueError("Le membre doit être `projets/SLUG/sources/FICHIER`.")
    if chemin.parts[0] != "projets" or chemin.parts[2] != "sources" or not _SLUG.fullmatch(chemin.parts[1]):
        raise ValueError("Le membre doit être une source d’un projet BENCHAB identifié.")
    return chemin.as_posix()


def _indexer(tar):
    """Indexe un tar BENCHAB sans extraction ; les membres et octets restent bornés."""
    index = {}
    total = 0
    for nombre, item in enumerate(tar, 1):
        if nombre > _MAX_ENTREES:
            raise ValueError("L’archive dépasse le budget de 100 000 entrées.")
        if not isinstance(item.name, str) or len(item.name) > _MAX_CARACTERES_CHEMIN:
            raise ValueError("Chemin d’archive au-delà du budget de 4 096 caractères.")
        nom = _nom_archive(item.name).as_posix()
        if nom in index:
            raise ValueError("Entrée d’archive dupliquée : " + nom)
        if not (item.isfile() or item.isdir()):
            raise ValueError("Entrée TAR spéciale ou lien refusé : " + nom)
        if type(item.size) is not int or item.size < 0:
            raise ValueError("Taille TAR négative ou invalide : " + nom)
        if item.isfile():
            if item.size > MAX_MEMBER:
                raise ValueError("Membre d’archive au-delà du budget de 4 Gio : " + nom)
            total += item.size
            if total > MAX_TOTAL:
                raise ValueError("L’archive dépasse le budget total de 64 Gio.")
        index[nom] = item
    return index


def _copier_membre(tar, item, destination, sha_attendu, taille_attendue):
    """Copie et hache un membre régulier en flux ; aucune arborescence n’est extraite."""
    flux = tar.extractfile(item)
    if flux is None:
        raise ValueError("Contenu de la source absent de l’archive.")
    empreinte = hashlib.sha256()
    total = 0
    with flux, destination.open("xb") as sortie:
        while bloc := flux.read(_TAILLE_BLOC):
            total += len(bloc)
            if total > MAX_MEMBER:
                raise ValueError("La source dépasse le budget de 4 Gio.")
            empreinte.update(bloc)
            sortie.write(bloc)
        sortie.flush()
        os.fsync(sortie.fileno())
    if total != taille_attendue or empreinte.hexdigest() != sha_attendu:
        raise ValueError("Les octets lus ne concordent pas avec le manifeste et le SHA déclaré.")
    return total


def _sha_fichier(path, limite):
    """Hache un fichier régulier en flux, sans charger son contenu en mémoire."""
    empreinte = hashlib.sha256()
    total = 0
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        with os.fdopen(fd, "rb") as flux:
            fd = -1
            info = os.fstat(flux.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > limite:
                raise ValueError("Fichier temporaire absent, non régulier ou trop volumineux.")
            while bloc := flux.read(_TAILLE_BLOC):
                total += len(bloc)
                if total > limite:
                    raise ValueError("Fichier temporaire au-delà de son budget.")
                empreinte.update(bloc)
            apres = os.fstat(flux.fileno())
            courant = path.lstat()
            if _signature(apres) != _signature(info) or _signature(courant) != _signature(info):
                raise ValueError("Le fichier temporaire a changé pendant sa lecture.")
    finally:
        if fd >= 0:
            os.close(fd)
    if total != info.st_size:
        raise ValueError("Le fichier temporaire a changé pendant sa lecture.")
    return empreinte.hexdigest()


def preparer(prive, archive, sha256_archive, membre, sha256_source, sortie):
    """Prépare une copie passive vérifiée d’un original depuis un tar BENCHAB.

    Entrées : tar régulier, SHA-256 de l’archive, membre `projets/SLUG/sources/FICHIER`,
    SHA-256 source et dossier de sortie neuf, sans unité. Sortie : dictionnaire PASS
    après relecture ou ValueError/OSError sur refus. Copie et preuve restent sous
    `prive/rapports/` ; la preuve disque garde le statut CANDIDAT. Aucun original,
    Base SQLite, profil, machine ou archive n’est modifié et rien n’est appliqué.
    """
    if not isinstance(sha256_archive, str) or not _SHA256.fullmatch(sha256_archive):
        raise ValueError("Un SHA-256 exact de l’archive est requis.")
    if not isinstance(sha256_source, str) or not _SHA256.fullmatch(sha256_source):
        raise ValueError("Un SHA-256 exact de la source est requis.")
    nom = _nom_source(membre)
    racine = atelier_prive(prive)
    rapports = sans_lien(racine / "rapports", racine=racine)
    cible = Path(sortie).expanduser()
    if not cible.is_absolute():
        cible = rapports / cible
    cible = sans_lien(cible, racine=racine)
    if cible == rapports or not cible.is_relative_to(rapports):
        raise ValueError("La sortie doit être un dossier neuf sous l’atelier `rapports/`.")
    destination = destination_neuve(cible, prive=racine, temporaire=False)
    if not destination.parent.is_dir():
        raise ValueError("Le parent de sortie sous `rapports/` doit déjà exister.")
    if destination.parent.is_symlink():
        raise ValueError("Le parent de sortie ne peut pas être un lien.")

    with _ouvrir_archive(archive, sha256_archive) as (source_archive, flux_archive, signature_archive, octets_archive):
        try:
            with tarfile.open(fileobj=flux_archive, mode="r:", tarinfo=_TarInfoBorne) as tar:
                index = _indexer(tar)
                item_manifeste = index.get(MANIFEST_NAME)
                if item_manifeste is None or not item_manifeste.isfile() or item_manifeste.size > MAX_MANIFEST:
                    raise ValueError("Manifeste absent, spécial ou trop volumineux.")
                with tar.extractfile(item_manifeste) as flux_manifeste:
                    manifeste_brut = flux_manifeste.read(MAX_MANIFEST + 1)
                if len(manifeste_brut) > MAX_MANIFEST:
                    raise ValueError("Manifeste de sauvegarde trop volumineux.")
                try:
                    donnees_manifeste = json.loads(
                        manifeste_brut.decode("utf-8"),
                        object_pairs_hook=_objet_sans_doublons,
                        parse_constant=_constante_json_interdite,
                    )
                except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as erreur:
                    raise ValueError("Manifeste de sauvegarde invalide en UTF-8.") from erreur
                if (
                    not isinstance(donnees_manifeste, dict)
                    or type(donnees_manifeste.get("schema")) is not int
                    or donnees_manifeste["schema"] != 1
                    or not isinstance(donnees_manifeste.get("files"), list)
                ):
                    raise ValueError("Manifeste de sauvegarde invalide.")
                if len(donnees_manifeste["files"]) > _MAX_ENTREES:
                    raise ValueError("Le manifeste dépasse le budget de 100 000 entrées.")
                if any(
                    not isinstance(entree, dict)
                    or not isinstance(entree.get("path"), str)
                    or len(entree["path"]) > _MAX_CARACTERES_CHEMIN
                    for entree in donnees_manifeste["files"]
                ):
                    raise ValueError("Chemin de manifeste absent ou au-delà de 4 096 caractères.")
                try:
                    _manifeste, attendus = _valider_manifeste(tar, index)
                except (AttributeError, TypeError, RecursionError) as erreur:
                    raise ValueError("Manifeste de sauvegarde invalide.") from erreur
                item = index.get(nom)
                declaration = attendus.get(nom)
                if item is None or declaration is None or not item.isfile() or declaration.get("type") != "file":
                    raise ValueError("Le membre demandé doit être un fichier régulier déclaré au manifeste.")
                sha_manifeste = declaration.get("sha256")
                taille_manifeste = declaration.get("size")
                if sha_manifeste != sha256_source or type(taille_manifeste) is not int:
                    raise ValueError("Le SHA source ne concorde pas avec le manifeste.")
                for parent in PurePosixPath(nom).parents:
                    parent_item = index.get(parent.as_posix())
                    if parent_item is not None and not parent_item.isdir():
                        raise ValueError("Un parent du membre source n’est pas un dossier régulier.")

                with TemporaryDirectory(prefix=".reprise-source-", dir=destination.parent) as temporaire:
                    stage = Path(temporaire)
                    copie_rel = Path("source") / PurePosixPath(nom).name
                    copie = stage / copie_rel
                    copie.parent.mkdir()
                    octets = _copier_membre(tar, item, copie, sha256_source, taille_manifeste)
                    preuve = {
                        "schema": 1,
                        "statut": "CANDIDAT",
                        "applique": False,
                        "archive_sha256": sha256_archive,
                        "archive_octets": octets_archive,
                        "membre": nom,
                        "sha256_source": sha256_source,
                        "octets_source": octets,
                        "copie": copie_rel.as_posix(),
                        "manifeste_sha256": hashlib.sha256(manifeste_brut).hexdigest(),
                        "verification": "Revérification requise après publication ; cette copie n’est pas appliquée.",
                        "limite": (
                            "Un seul original déclaré, copie exacte non appliquée ; "
                            "aucune reconstruction de projet."
                        ),
                    }
                    preuve_brute = (
                        json.dumps(preuve, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
                    ).encode("utf-8")
                    fichier_preuve = stage / "preuve.json"
                    with fichier_preuve.open("xb") as flux:
                        flux.write(preuve_brute)
                        flux.flush()
                        os.fsync(flux.fileno())
                    sha_preuve = hashlib.sha256(preuve_brute).hexdigest()
                    if _sha_fichier(copie, MAX_MEMBER) != sha256_source:
                        raise ValueError("La copie temporaire diffère du SHA source déclaré.")
                    if _sha_fichier(fichier_preuve, MAX_MANIFEST) != sha_preuve:
                        raise ValueError("La preuve temporaire a changé avant publication.")
                    _verifier_archive(source_archive, flux_archive, signature_archive)
                    info_parent = destination.parent.lstat()
                    fd_parent = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                    try:
                        if (info_parent.st_dev, info_parent.st_ino) != (
                            os.fstat(fd_parent).st_dev, os.fstat(fd_parent).st_ino,
                        ):
                            raise ValueError("Le parent de sortie a changé avant publication.")
                        identite_stage = (stage.stat().st_dev, stage.stat().st_ino)
                        renommer_exclusif(stage.name, destination.name, src_dir_fd=fd_parent, dst_dir_fd=fd_parent)
                        info_publie = os.stat(destination.name, dir_fd=fd_parent, follow_symlinks=False)
                        if (
                            not stat.S_ISDIR(info_publie.st_mode)
                            or (info_publie.st_dev, info_publie.st_ino) != identite_stage
                        ):
                            raise ValueError(
                                "La sortie publiée diffère du dossier préparé ; "
                                "laissée en place pour diagnostic."
                            )
                        fd_sortie = os.open(
                            destination.name,
                            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=fd_parent,
                        )
                        try:
                            fd_dossier_copie = os.open(
                                "source", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd_sortie,
                            )
                            try:
                                for nom_fichier, dir_fd, attendu, limite in (
                                    (copie_rel.name, fd_dossier_copie, sha256_source, MAX_MEMBER),
                                    ("preuve.json", fd_sortie, sha_preuve, MAX_MANIFEST),
                                ):
                                    fd = os.open(
                                        nom_fichier, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd,
                                    )
                                    try:
                                        info_avant = os.fstat(fd)
                                        if not stat.S_ISREG(info_avant.st_mode) or info_avant.st_size > limite:
                                            raise ValueError("Un membre publié n’est pas un fichier ordinaire borné.")
                                        calcule = hashlib.sha256()
                                        total = 0
                                        while bloc := os.read(fd, _TAILLE_BLOC):
                                            total += len(bloc)
                                            if total > limite:
                                                raise ValueError("Un membre publié dépasse son budget.")
                                            calcule.update(bloc)
                                        info_apres = os.fstat(fd)
                                        info_chemin = os.stat(
                                            nom_fichier, dir_fd=dir_fd, follow_symlinks=False,
                                        )
                                        if (
                                            _signature(info_apres) != _signature(info_avant)
                                            or _signature(info_chemin) != _signature(info_apres)
                                        ):
                                            raise ValueError("Un membre publié a changé pendant sa relecture.")
                                        if total != info_avant.st_size or calcule.hexdigest() != attendu:
                                            raise ValueError("Un membre publié diffère de son empreinte attendue.")
                                    finally:
                                        os.close(fd)
                            finally:
                                os.close(fd_dossier_copie)
                        finally:
                            os.close(fd_sortie)
                        _verifier_archive(source_archive, flux_archive, signature_archive)
                        sortie_apres = os.stat(destination.name, dir_fd=fd_parent, follow_symlinks=False)
                        if (sortie_apres.st_dev, sortie_apres.st_ino) != identite_stage:
                            raise ValueError("La sortie a changé après publication ; laissée en place.")
                        parent_apres = destination.parent.lstat()
                        if (parent_apres.st_dev, parent_apres.st_ino) != (
                            os.fstat(fd_parent).st_dev, os.fstat(fd_parent).st_ino,
                        ):
                            raise ValueError("Le parent a changé après publication ; sortie laissée en place.")
                    finally:
                        os.close(fd_parent)
        except (tarfile.TarError, EOFError) as erreur:
            raise ValueError("Archive tar illisible ou incomplète.") from erreur

    resultat = dict(preuve)
    resultat.update(
        statut="PASS",
        statut_preuve_fichier="CANDIDAT",
        preuve_sha256=sha_preuve,
        sha256_copie=sha256_source,
        verification_post_publication="PASS",
        verification="PASS après relecture exacte de la copie et de la preuve ; copie non appliquée.",
    )
    return resultat
