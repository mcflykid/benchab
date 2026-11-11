"""Transactions de commentaires communes au serveur et à la CLI (verrou interprocessus)."""

import contextlib
import fcntl
import json
import math
import os
import stat
import threading
import time
import uuid

from benchab.chemins import sans_lien


def sans_liens(path):
    """Normalise un chemin reconnu et refuse tout lien interne, même avant `..`."""
    return sans_lien(path)


_active = threading.local()
DELAI_VERROU = 5.0


def ouvrir_dossier(path):
    """Ouvre chaque ancêtre sans suivre de lien ; normalise les alias système de la racine."""
    path = sans_liens(path)
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        return fd
    except BaseException:
        os.close(fd)
        raise


@contextlib.contextmanager
def transaction(path):
    """Fournit le chemin normalisé sous verrou exclusif (attente maximale en s), sans remplacer les données."""
    p = sans_liens(path)
    directory = ouvrir_dossier(p.parent)
    try:
        try:
            lock = os.open(
                p.name + ".lock",
                os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                384,
                dir_fd=directory,
            )
        except FileExistsError:
            lock = os.open(p.name + ".lock", os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(lock, "a") as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                raise ValueError("verrou de commentaires : fichier régulier requis")
            limite = time.monotonic() + DELAI_VERROU
            while True:
                try:
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= limite:
                        raise ValueError("commentaires occupés ; réessayer après la transaction en cours")
                    time.sleep(0.01)
            _active.target = (str(p), directory)
            try:
                sans_liens(p)
                yield p
            finally:
                _active.target = None
                fcntl.flock(f, fcntl.LOCK_UN)
    finally:
        os.close(directory)


def lire(path):
    """Lit un fichier JSON régulier et stable, borné en octets ; renvoie un objet vide si la cible manque."""
    p = sans_liens(path)
    active = getattr(_active, "target", None)
    own = not active or active[0] != str(p)
    directory = ouvrir_dossier(p.parent) if own else active[1]
    try:
        try:
            fd = os.open(p.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except FileNotFoundError:
            return {}
        with os.fdopen(fd, "rb") as f:
            avant = os.fstat(f.fileno())
            if not stat.S_ISREG(avant.st_mode):
                raise ValueError("commentaires : fichier régulier requis")
            if avant.st_size > MAX_OCTETS:
                raise ValueError("commentaires trop volumineux")
            body = f.read(MAX_OCTETS + 1)
            apres = os.fstat(f.fileno())
            if len(body) > MAX_OCTETS:
                raise ValueError("commentaires trop volumineux")
            if (avant.st_size, avant.st_mtime_ns, avant.st_ctime_ns) != (
                apres.st_size,
                apres.st_mtime_ns,
                apres.st_ctime_ns,
            ):
                raise ValueError("commentaires modifiés pendant lecture")
        return json.loads(body)
    finally:
        if own:
            os.close(directory)


MAX_OCTETS = 1000000


def serialiser(data):
    """Valide le document puis renvoie le JSON UTF-8 dans la limite d’octets de stockage."""
    valider(data)
    body = json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8")
    if len(body) > MAX_OCTETS:
        raise ValueError("commentaires trop volumineux")
    return body


def atomique(path, data):
    """Publie le JSON validé sous verrou, par remplacement atomique dans le même dossier ; ne renvoie rien."""
    p = sans_liens(path)
    active = getattr(_active, "target", None)
    if not active or active[0] != str(p):
        with transaction(p):
            return atomique(p, data)
    body = serialiser(data)
    directory = active[1]
    name = ".commentaires-" + uuid.uuid4().hex + ".tmp"
    fd = os.open(name, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 384, dir_fd=directory)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        sans_liens(p)
        os.replace(name, p.name, src_dir_fd=directory, dst_dir_fd=directory)
    finally:
        try:
            os.unlink(name, dir_fd=directory)
        except FileNotFoundError:
            pass


def valider(d):
    """Renvoie le document intact ou lève ValueError si le schéma, les identifiants ou les vecteurs sont invalides."""
    if not isinstance(d, dict) or not isinstance(d.get("page"), str) or (not isinstance(d.get("commentaires"), list)):
        raise ValueError("objet page/commentaires attendu")
    cs = d["commentaires"]
    ids = set()
    if len(cs) > 2000:
        raise ValueError("trop de commentaires")
    for c in cs:
        if not isinstance(c, dict) or not isinstance(c.get("id"), str) or (not c["id"]) or (c["id"] in ids):
            raise ValueError("identifiant absent ou dupliqué")
        ids.add(c["id"])
        for k in ("texte", "piece"):
            if not isinstance(c.get(k), str) or len(c[k]) > 20000:
                raise ValueError("texte/pièce invalide")
        for k in ("local", "point_mm", "normale", "normale_projet"):
            v = c.get(k)
            if (
                k in ("local", "point_mm")
                and v is None
                or (
                    v is not None
                    and (
                        not isinstance(v, list)
                        or len(v) != 3
                        or any(
                            (
                                isinstance(x, bool) or not isinstance(x, (int, float)) or (not math.isfinite(x))
                                for x in v
                            )
                        )
                    )
                )
            ):
                raise ValueError("vecteur invalide")
        if not isinstance(c.get("reponses", []), list):
            raise ValueError("réponses invalides")
        for r in c.get("reponses", []):
            if not isinstance(r, dict) or any((not isinstance(r.get(k), str) for k in ("auteur", "date", "texte"))):
                raise ValueError("réponse invalide")
        if c.get("statut", "ouvert") not in ("ouvert", "résolu"):
            raise ValueError("statut invalide")
    for k in ("supprimes",):
        if not isinstance(d.get(k, []), list) or any((not isinstance(x, str) for x in d.get(k, []))):
            raise ValueError("suppressions invalides")
    return d
