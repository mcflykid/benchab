"""Publier des fichiers locaux sans écrasement après préparation privée.

Chaque lien publié est atomique ; l'ensemble de plusieurs fichiers ne l'est pas.
Les lecteurs doivent attendre le retour de publier(). Aucun fichier préexistant
n'est supprimé. Le nettoyage d'échec ne retire que les liens créés ici, encore
identiques (inode et contenu) au fichier privé. Pas de garantie face à un arrêt
brutal du processus ou à un acteur qui renomme les parents pendant l'opération.


"""

import fcntl
import hashlib
import json
import os
import stat
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from benchab.qualite.empreintes import sha256_fichier as _sha


def sortie_privee(chemin, *, prive=None):
    """Vérifier qu'une sortie reste privée ou temporaire hors dépôt.

    Entrées : chemin et racine privée optionnelle, sans unité.
    Sortie : chemin absolu validé.
    Échec : ValueError sur un lien ou une sortie hors périmètre ; aucune écriture.
    """
    from benchab.chemins import atelier_prive, est_temporaire_hors_depot

    chemin = _sans_lien(chemin)
    racine = atelier_prive(prive)
    if not chemin.is_relative_to(racine) and not est_temporaire_hors_depot(chemin):
        raise ValueError("Le rapport doit rester dans l'atelier privé ou temporaire système.")
    return chemin


def sortie_durable(chemin, *, prive=None):
    """Vérifier qu'une preuve reste sous l'atelier privé choisi.

    Entrées : chemin et racine privée optionnelle, sans unité.
    Sortie : chemin absolu validé.
    Échec : ValueError sur un lien ou une sortie hors atelier ; aucune écriture.
    """
    from benchab.chemins import atelier_prive

    racine = atelier_prive(prive)
    cible = _sans_lien(chemin)
    if not cible.is_relative_to(racine):
        raise ValueError("Une sortie durable doit rester dans l’atelier privé.")
    return cible


def destination_neuve(chemin, *, prive=None, temporaire=True):
    """Réserver la validation d'une destination privée encore absente.

    Entrées : chemin, racine privée optionnelle et mode temporaire, sans unité.
    Sortie : chemin validé ; FileExistsError si la destination existe déjà.
    Aucun fichier n'est créé et le nom n'est pas réservé ; publier exclusivement
    ensuite (docs/reference/chemins-et-atelier.md).
    """
    garde = sortie_privee if temporaire else sortie_durable
    cible = garde(chemin, prive=prive)
    if cible.exists():
        raise FileExistsError("Sortie existante ; aucune version précédente écrasée")
    return cible


def verifier_references_durables(valeur, prive, *, champ=None):
    """Refuse les chemins durables hors atelier, sans transformer les citations de provenance."""
    if isinstance(valeur, dict):
        for cle, item in valeur.items():
            verifier_references_durables(cle, prive)
            verifier_references_durables(item, prive, champ=cle)
    elif isinstance(valeur, (tuple, list)):
        for item in valeur:
            verifier_references_durables(item, prive, champ=champ)
    elif isinstance(valeur, str):
        champs = {"fichier", "chemin", "rapport", "manifeste", "envoi_markdown"}
        chemin = Path(valeur)
        temporaire = valeur.startswith(("/tmp/", "/private/tmp/", "/var/folders/", "/private/var/folders/"))
        temporaire |= chemin.is_absolute() and chemin.is_relative_to(tempfile.gettempdir())
        if chemin.is_absolute() and (temporaire or champ in champs):
            # Même sous le temporaire, un atelier de test possède sa racine
            # de confiance (docs/reference/donnees.md).
            sortie_durable(chemin, prive=prive)
        elif champ in champs and ".." in chemin.parts:
            sortie_durable(Path(prive) / chemin, prive=prive)


def ouvrir_verrou(nom, directory):
    """Obtenir le descripteur du verrou stable sans le retirer.

    Entrées : nom de fichier et descripteur du dossier parent, sans unité.
    Sortie : descripteur ouvert sur un fichier ordinaire.
    Erreurs : OSError après huit tentatives de reprise de l'absence transitoire.
    Invariant : ne supprime ni ne force un verrou détenu ailleurs.
    """
    erreur = None
    for tentative in range(8):
        try:
            flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
            try:
                fd = os.open(nom, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory)
            except FileExistsError:
                fd = os.open(nom, flags, dir_fd=directory)
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                os.close(fd)
                raise ValueError("Le verrou doit être un fichier ordinaire")
            return fd
        except FileNotFoundError as exc:
            erreur = exc
            if not os.fstat(directory).st_nlink:
                raise
            if tentative < 7:
                time.sleep(0.001)
    raise erreur


def acquerir_verrou(verrou, delai=5.0):
    """Attendre au plus le délai fourni pour obtenir le verrou exclusif.

    Entrées : descripteur du verrou et délai en secondes (défaut : 5 s).
    Sortie : aucune valeur ; ValueError si le délai est invalide ou dépassé.
    Invariant : ne force jamais la libération d'un verrou détenu ailleurs.
    """
    import math

    if not isinstance(delai, (int, float)) or not math.isfinite(delai) or delai < 0:
        raise ValueError("Délai de verrou invalide")
    limite = time.monotonic() + delai
    while True:
        try:
            fcntl.flock(verrou, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            restant = limite - time.monotonic()
            if restant <= 0:
                raise ValueError("Fichier occupé : délai de verrou dépassé ; réessayer") from None
            time.sleep(min(0.02, restant))


def modifier_json(path, modifier):
    """Vérifier les données puis publier leur remplacement JSON sous verrou.

    Entrées : chemin du JSON et callback recevant le dictionnaire chargé.
    Sortie : valeur renvoyée par le callback ; aucune unité.
    Échec : ValueError si le JSON ou le fichier change hors transaction.
    Invariant : conserve l'original complet en cas d'échec avant remplacement.
    """
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Fiche symbolique refusée")
    p = path.parent.resolve(strict=True) / path.name
    directory = os.open(p.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temp = None
    try:
        lock = ouvrir_verrou(p.name + ".lock", directory)
        with os.fdopen(lock, "a") as verrou:
            acquerir_verrou(verrou)

            def lire():
                fd = os.open(p.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                with os.fdopen(fd, "rb") as f:
                    mode = os.fstat(f.fileno()).st_mode
                    if not stat.S_ISREG(mode):
                        raise ValueError("La fiche doit être un fichier ordinaire")
                    return f.read(), mode

            avant, mode = lire()
            donnees = json.loads(avant)
            resultat = modifier(donnees)
            contenu = json.dumps(donnees, indent=1, ensure_ascii=False, allow_nan=False).encode("utf-8")
            import uuid

            temp = ".json-" + uuid.uuid4().hex + ".tmp"
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode & 0o777, dir_fd=directory)
            with os.fdopen(fd, "wb") as f:
                f.write(contenu)
                f.flush()
                os.fsync(f.fileno())
            if lire()[0] != avant:
                raise ValueError("Fiche modifiée hors transaction : remplacement refusé")
            os.replace(temp, p.name, src_dir_fd=directory, dst_dir_fd=directory)
            temp = None
            os.fsync(directory)
            return resultat
    finally:
        if temp:
            try:
                os.unlink(temp, dir_fd=directory)
            except FileNotFoundError:
                pass
        os.close(directory)


def _sans_lien(path):
    """Délègue la garde commune sous racine de confiance ; aucune création ni résolution de lien interne."""
    from benchab.chemins import sans_lien

    return sans_lien(path)


@contextmanager
def verrou_publication(dossier, nom):
    """Sérialise une publication locale (attente bornée en s), sans effacer le fichier de verrou."""
    dossier = _sans_lien(dossier)
    dossier.mkdir(parents=True, exist_ok=True)
    fd = os.open(dossier, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        verrou = ouvrir_verrou(nom, fd)
        try:
            acquerir_verrou(verrou)
            yield
        finally:
            os.close(verrou)
    finally:
        os.close(fd)


def remplacer_texte(cible, texte):
    """Remplace atomiquement un état texte UTF-8 avec un temporaire unique, sous verrou de l'appelant."""
    cible = _sans_lien(cible)
    fd, nom = tempfile.mkstemp(prefix=".publication-", dir=cible.parent)
    temporaire = Path(nom)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as flux:
            flux.write(texte)
            flux.flush()
            os.fsync(flux.fileno())
        _sans_lien(cible)
        temporaire.replace(cible)
    finally:
        temporaire.unlink(missing_ok=True)


def publier_texte_repris(cible, texte, *, entrees=()):
    """Publie un texte neuf ou confirme sa reprise à l'identique, sans écraser un fichier différent."""
    cible = _sans_lien(cible)
    if cible.exists():
        if cible.read_bytes() != texte.encode("utf-8"):
            raise ValueError("Fichier publié différent de la version canonique")
        return
    with PublicationNeuve(entrees, [cible]) as publication:
        publication.temporaire(cible).write_text(texte, encoding="utf-8")
        publication.publier()


def valider_chemins(entrees, sorties):
    entrees = [_sans_lien(p).resolve() for p in entrees]
    sorties = [_sans_lien(p) for p in sorties]
    cibles = [p.resolve() for p in sorties]
    if len(set(cibles)) != len(cibles) or set(entrees) & set(cibles):
        raise ValueError("Collision entrée, sortie ou rapport : écrasement refusé.")
    for p in sorties:
        if p.exists():
            raise ValueError("Destination existante : écrasement refusé : " + str(p))
    return sorties


class PublicationNeuve:
    """
    Contexte : écrire/relire temporaire(cible), puis publier une seule fois.
    """

    def __init__(self, entrees, sorties):
        self.entrees = list(entrees)
        self.sorties = valider_chemins(self.entrees, sorties)
        self._temps = {}
        self._dossiers = []
        self._publie = False

    def __enter__(self):
        try:
            for cible in self.sorties:
                cible.parent.mkdir(parents=True, exist_ok=True)
                _sans_lien(cible)
                d = tempfile.TemporaryDirectory(prefix=".publication-", dir=cible.parent)
                self._dossiers.append(d)
                self._temps[cible] = Path(d.name).resolve() / ("contenu" + cible.suffix)
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def temporaire(self, cible):
        return self._temps[Path(os.path.abspath(cible))]

    def publier(self):
        if self._publie:
            raise ValueError("Publication déjà effectuée.")
        valider_chemins(self.entrees, self.sorties)
        # Tout fichier privé doit être complet avant la première publication.
        traces = []
        for cible in self.sorties:
            temp = self._temps[cible]
            if not temp.is_file() or temp.is_symlink():
                raise ValueError("Fichier temporaire absent ou invalide.")
            with temp.open("rb") as f:
                os.fsync(f.fileno())
            st = temp.stat()
            traces.append((cible, temp, st.st_dev, st.st_ino, _sha(temp)))
        try:
            for cible, temp, dev, ino, sha in traces:
                _sans_lien(cible)
                # link est exclusif : pas de fenêtre exists()/replace() destructrice.
                os.link(temp, cible, follow_symlinks=False)
            self._publie = True
        except BaseException:
            for cible, temp, dev, ino, sha in reversed(traces):
                try:
                    st = cible.lstat()
                    if (st.st_dev, st.st_ino) == (dev, ino) and _sha(cible) == sha:
                        cible.unlink()
                except OSError:
                    pass
            raise

    def __exit__(self, *exc):
        for d in reversed(self._dossiers):
            d.cleanup()
        self._dossiers.clear()


def ecrire_json_neuf(cible, donnees, *, entrees=()):
    """Publie un rapport complet, sans remplacer une preuve ni suivre ses liens."""
    cible = Path(cible).absolute()
    with PublicationNeuve(entrees, [cible]) as publication:
        publication.temporaire(cible).write_text(
            json.dumps(donnees, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
        publication.publier()
    return cible


def renommer_exclusif(source, destination, *, src_dir_fd=None, dst_dir_fd=None):
    """
    Publication atomique d’un dossier, refusant même une destination vide.

        Les arguments historiques sont des chemins. Avec deux FD de répertoire,
        seuls des noms simples relatifs sont acceptés : les parents restent épinglés.
        Darwin : renamex_np / renameatx_np(RENAME_EXCL), SDK sys/stdio.h.
        Linux : renameat2(RENAME_NOREPLACE), sans repli écrasant.

    """
    import ctypes

    relatifs = src_dir_fd is not None or dst_dir_fd is not None
    if relatifs:
        if any(type(fd) is not int or fd < 0 for fd in (src_dir_fd, dst_dir_fd)):
            raise ValueError("Deux descripteurs de répertoire valides sont requis")
        source, destination = os.fspath(source), os.fspath(destination)
        if any(
            not isinstance(n, str) or not n or n in (".", "..") or "/" in n or "\\" in n or "\0" in n
            for n in (source, destination)
        ):
            raise ValueError("Noms relatifs simples requis")
    else:
        source, destination = _sans_lien(source), _sans_lien(destination)
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        if relatifs:
            fn = libc.renameatx_np
            fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            code = fn(src_dir_fd, os.fsencode(source), dst_dir_fd, os.fsencode(destination), 0x00000004)
        else:
            fn = libc.renamex_np
            fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
            code = fn(os.fsencode(source), os.fsencode(destination), 0x00000004)
    elif sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        fn = libc.renameat2
        fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        code = fn(
            src_dir_fd if relatifs else -100,
            os.fsencode(source),
            dst_dir_fd if relatifs else -100,
            os.fsencode(destination),
            1,
        )
    else:
        raise OSError("Publication exclusive de dossier non disponible sur cette plateforme")
    if code:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), str(destination))


class TransactionVues:
    """
    Journal d'annulation durable pour les petits fichiers de catalogue.

        Appelé sous le verrou stable publication.transaction. Après SIGKILL, les
        lecteurs refusent un catalogue en cours ; la prochaine transaction restaure
        les vues avant toute autre opération. Un changement tiers bloque la reprise.
        Les fichiers de fabrication sont publiés par les primitives exclusives.

    """

    def __init__(self, root):
        self.root = _sans_lien(root).resolve()
        self.journal = self.root / "projets/.transaction-vues.json"
        self.entrees = {}
        self.dossiers = []

    @staticmethod
    def atomique(path, contenu, mode=0o600):
        path = _sans_lien(path)
        fd, nom = tempfile.mkstemp(prefix=".vue-", dir=path.parent)
        try:
            os.fchmod(fd, mode)
            with os.fdopen(fd, "wb") as f:
                f.write(contenu)
                f.flush()
                os.fsync(f.fileno())
            os.replace(nom, path)
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(nom):
                os.unlink(nom)

    def chemin(self, rel):
        p = Path(rel)
        if p.is_absolute() or ".." in p.parts or not p.parts or p.parts[0] != "projets":
            raise ValueError("journal : chemin hors projets")
        return _sans_lien(self.root / p)

    def sauver(self):
        self.atomique(
            self.journal, json.dumps({"schema": 1, "entrees": self.entrees, "dossiers": self.dossiers}).encode()
        )

    def enregistrer_dossier(self, source, destination):
        source = _sans_lien(source)
        destination = _sans_lien(destination)
        a = source.relative_to(self.root).as_posix()
        b = destination.relative_to(self.root).as_posix()
        self.chemin(a)
        self.chemin(b)
        if destination.exists():
            raise FileExistsError(destination)
        st = source.stat()
        self.dossiers.append({"source": a, "destination": b, "dev": st.st_dev, "ino": st.st_ino})
        self.sauver()

    def restaurer(self):

        dossiers = []
        for e in self.dossiers:
            source = self.chemin(e["source"])
            dest = self.chemin(e["destination"])
            if dest.exists():
                st = dest.stat()
                if source.exists() or (st.st_dev, st.st_ino) != (e["dev"], e["ino"]):
                    raise ValueError("reprise bloquée : dossier modifié hors transaction")
                dossiers.append((dest, source))
        self._restaurer_vues()
        for dest, source in reversed(dossiers):
            renommer_exclusif(dest, source)
        self.terminer()

    def enregistrer(self, path, contenu):
        import base64

        p = _sans_lien(path)
        rel = p.relative_to(self.root).as_posix()
        self.chemin(rel)
        if len(contenu) > 16 * 1024 * 1024:
            raise ValueError("vue trop volumineuse")
        if rel not in self.entrees:
            if p.exists() and p.stat().st_size > 16 * 1024 * 1024:
                raise ValueError("vue trop volumineuse")
            self.entrees[rel] = {
                "avant": base64.b64encode(p.read_bytes()).decode() if p.exists() else None,
                "mode": p.stat().st_mode & 0o777 if p.exists() else 0o644,
            }
        e = self.entrees[rel]
        if "apres" in e:
            if not p.exists() or _sha(p) != e["apres"]:
                raise ValueError("vue modifiée hors transaction")
            e.setdefault("versions", []).append(e["apres"])
        e["apres"] = hashlib.sha256(contenu).hexdigest()
        self.sauver()  # durable AVANT le remplacement, même si le processus meurt ensuite

    def _restaurer_vues(self):
        import base64

        # Valider toutes les vues avant de toucher à la première.
        plans = []
        for rel, e in self.entrees.items():
            p = self.chemin(rel)
            avant = base64.b64decode(e["avant"], validate=True) if e["avant"] is not None else None
            courant = _sha(p) if p.exists() else None
            original = hashlib.sha256(avant).hexdigest() if avant is not None else None
            if courant not in (original, e["apres"], *e.get("versions", [])):
                raise ValueError("reprise bloquée : vue modifiée hors transaction : " + rel)
            plans.append((p, avant, e["mode"], courant == original))
        for p, avant, mode, inchange in reversed(plans):
            if inchange:
                continue
            if avant is None:
                p.unlink()
            else:
                self.atomique(p, avant, mode)

    def terminer(self):
        if self.journal.exists():
            self.journal.unlink()
            directory = os.open(self.journal.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)

    def recuperer(self):
        if self.journal.exists():
            d = json.loads(_sans_lien(self.journal).read_text())
            if d.get("schema") != 1 or not isinstance(d.get("entrees"), dict):
                raise ValueError("journal de reprise invalide")
            self.entrees = d["entrees"]
            self.dossiers = d.get("dossiers", [])
            self.restaurer()
            self.entrees = {}
            self.dossiers = []

    def valider(self):
        for e in self.dossiers:
            st = self.chemin(e["destination"]).stat()
            if (st.st_dev, st.st_ino) != (e["dev"], e["ino"]):
                raise ValueError("publication dossier altérée")
        for rel, e in self.entrees.items():
            if _sha(self.chemin(rel)) != e["apres"]:
                raise ValueError("vue modifiée avant validation : " + rel)
        self.terminer()
