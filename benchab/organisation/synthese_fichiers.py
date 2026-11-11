"""Lecture des preuves et publication exclusive du paquet de vues d’atelier."""

import contextlib
import hashlib
import json
import os
import stat
import uuid
import zipfile
from pathlib import Path

from benchab.visualisation.stockage_commentaires import ouvrir_dossier, sans_liens

MAX_JSON = 2 * 1024 * 1024
MAX_ARCHIVE = 256 * 1024 * 1024


@contextlib.contextmanager
def _lecture(path, limite=None):
    """Ouvre une source régulière en lecture, budget en octets, identité vérifiée avant/après."""
    path = sans_liens(path)
    directory = ouvrir_dossier(path.parent)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    finally:
        os.close(directory)
    with os.fdopen(fd, "rb") as stream:
        avant = os.fstat(stream.fileno())
        if not stat.S_ISREG(avant.st_mode) or (limite is not None and avant.st_size > limite):
            raise ValueError("Source non régulière ou trop volumineuse")
        yield stream
        apres = os.fstat(stream.fileno())
        if (avant.st_dev, avant.st_ino, avant.st_size, avant.st_mtime_ns, avant.st_ctime_ns) != (
            apres.st_dev,
            apres.st_ino,
            apres.st_size,
            apres.st_mtime_ns,
            apres.st_ctime_ns,
        ):
            raise ValueError("Source modifiée pendant sa lecture")


def _digest(stream):
    """Calcule le SHA-256 d’un flux repositionnable, puis le remet au début."""
    h = hashlib.sha256()
    stream.seek(0)
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        h.update(chunk)
    stream.seek(0)
    return h.hexdigest()


def _json(path, attendu=None, avecempreinte=False):
    """Relit un objet JSON borné avec son SHA facultatif, sans modifier la preuve."""
    with _lecture(path, MAX_JSON) as stream:
        raw = stream.read(MAX_JSON + 1)
    if len(raw) > MAX_JSON:
        raise ValueError("Document JSON trop volumineux")
    digest = hashlib.sha256(raw).hexdigest()
    if attendu is not None and digest != attendu:
        raise ValueError("Document modifié depuis son empreinte")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Objet JSON attendu")
    return (value, digest) if avecempreinte else value


@contextlib.contextmanager
def _archive_liee(path, attendu):
    """Ouvre une archive ZIP dont le SHA attendu est vérifié, sans extraire de membre."""
    with _lecture(path, MAX_ARCHIVE) as stream:
        if not attendu or _digest(stream) != attendu:
            raise ValueError("Archive modifiée ou sans empreinte")
        with zipfile.ZipFile(stream) as archive:
            yield (archive, stream)


def _ouvrir_parent_sortie(path):
    """Crée les parents manquants depuis des FD, sans suivre de lien."""
    path = Path(path).absolute()
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            if part in (".", ".."):
                raise ValueError("Parent ambigu")
            try:
                os.mkdir(part, 448, dir_fd=fd)
            except FileExistsError:
                pass
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        return fd
    except BaseException:
        os.close(fd)
        raise


def _parent_inchange(out, directory):
    probe = ouvrir_dossier(out.parent)
    try:
        a, b = (os.fstat(probe), os.fstat(directory))
        if (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino):
            raise ValueError("Parent de sortie remplacé")
    finally:
        os.close(probe)


def _publier_vues(stage, out, *, renommer):
    """Copie privée au même volume puis renommage exclusif via parents épinglés."""
    directory = _ouvrir_parent_sortie(out.parent)
    temp = ".vues-" + uuid.uuid4().hex
    cible = None
    publie = False
    copies = []
    try:
        _parent_inchange(out, directory)
        os.mkdir(temp, 448, dir_fd=directory)
        cible = os.open(temp, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
        for source in sorted(stage.iterdir()):
            if source.name not in {
                "piece.html",
                "plateau.html",
                "supports.html",
                "risques.html",
                "index.html",
                "VUES.json",
            }:
                raise ValueError("Fichier inattendu dans le paquet privé")
            with _lecture(source) as entree:
                fd = os.open(source.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 384, dir_fd=cible)
                copies.append(source.name)
                with os.fdopen(fd, "wb") as sortie:
                    for chunk in iter(lambda: entree.read(1024 * 1024), b""):
                        sortie.write(chunk)
                    sortie.flush()
                    os.fsync(sortie.fileno())
        os.fsync(cible)
        _parent_inchange(out, directory)
        renommer(temp, out.name, src_dir_fd=directory, dst_dir_fd=directory)
        publie = True
        os.fsync(directory)
        _parent_inchange(out, directory)
    finally:
        if cible is not None:
            if not publie:
                for name in copies:
                    try:
                        os.unlink(name, dir_fd=cible)
                    except FileNotFoundError:
                        pass
                try:
                    a, b = (os.stat(temp, dir_fd=directory, follow_symlinks=False), os.fstat(cible))
                    if (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino):
                        os.rmdir(temp, dir_fd=directory)
                except FileNotFoundError:
                    pass
            os.close(cible)
        os.close(directory)
