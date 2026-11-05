"""Sauvegarde et restauration vérifiées des données explicitement privées."""

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tarfile
import tempfile
import uuid
from pathlib import Path, PurePosixPath, PureWindowsPath

from benchab.donnees.base import APPLICATION_ID, Base
from benchab.qualite.ecritures import renommer_exclusif

MAX_MEMBER = 4 * 1024**3
MAX_TOTAL = 64 * 1024**3
MAX_MANIFEST = 32 * 1024**2
BLOCK_SIZE = 1024**2
MANIFEST_NAME = "BENCHAB-MANIFEST.json"
SEUIL_DEPART = 31 * 1024**3
PLANCHER_LIBRE = 10 * 1024**3
SQLITE_SIDECARS = {"benchab.sqlite-wal", "benchab.sqlite-shm", "benchab.sqlite-journal"}
PRIVATE_TMP = Path("/tmp").resolve()


def _exiger_espace(chemin, seuil):
    """Refuse une copie sous le seuil libre en octets ; aucun fichier ni réglage modifié."""
    libre = shutil.disk_usage(chemin).free
    if libre < seuil:
        raise ValueError(f"Espace insuffisant : {chemin} ; libres {libre} octets, seuil {seuil} octets")


class _LectureSurveillee:
    """Surveille la destination à chaque bloc TAR ; n'altère jamais la source lue."""

    def __init__(self, flux, destination):
        self.flux, self.destination = flux, destination

    def read(self, taille=-1):
        """Lit au plus taille octets ou lève FAIL sous le plancher ; aucune écriture source."""
        _exiger_espace(self.destination, PLANCHER_LIBRE)
        return self.flux.read(taille)


def _signature(path: Path) -> tuple[int, int, int, int, int]:
    info = path.lstat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _digest_flux(source, destination=None, *, limit=None, surveiller=None) -> tuple[str, int]:
    empreinte = hashlib.sha256()
    total = 0
    while bloc := source.read(BLOCK_SIZE):
        total += len(bloc)
        if limit is not None and total > limit:
            raise ValueError("Budget de taille dépassé.")
        if destination is not None:
            if surveiller is not None:
                surveiller()
            destination.write(bloc)
        empreinte.update(bloc)
    return empreinte.hexdigest(), total


def _chemin_prive(base: Base, path: str | Path) -> Path:
    """Valide une source dans la Base explicite ; aucun lien interne ni écriture, FAIL hors privé."""
    from benchab.chemins import sans_lien

    if not isinstance(base, Base):
        raise TypeError("Une Base BENCHAB explicite est requise.")
    path = Path(path).expanduser()
    if not path.is_absolute():
        path = base.prive / path
    return sans_lien(path, racine=base.prive, strict=True)


def _chemin_destination(base: Base, path: str | Path, *, autoriser_prive: bool) -> Path:
    """Valide une destination privée/temporaire ; Path sans unité, aucun lien interne ni dossier créé."""
    from benchab.chemins import est_temporaire, sans_lien

    path = Path(path).expanduser()
    if not path.is_absolute():
        if not autoriser_prive:
            raise ValueError("Destination explicite absolue requise.")
        path = base.prive / path
    path = sans_lien(path)
    if not path.is_relative_to(base.prive) and not est_temporaire(path):
        raise ValueError("Destination limitée à base.prive ou au dossier temporaire canonique.")
    if not path.parent.is_dir():
        raise ValueError("Le parent de destination doit déjà exister.")
    return path


def _inventaire(source: Path, *, snapshot_canonique=False) -> dict[str, dict]:
    resultats = {}

    def erreur_parcours(erreur):
        raise erreur

    for dossier, sous_dossiers, fichiers in os.walk(source, topdown=True, followlinks=False, onerror=erreur_parcours):
        sous_dossiers.sort()
        fichiers.sort()
        here = Path(dossier)
        for nom in list(sous_dossiers):
            chemin = here / nom
            rel = chemin.relative_to(source).as_posix()
            _nom_archive(rel)
            info = chemin.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise ValueError("Lien symbolique source refusé : " + rel)
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError("Type de dossier source refusé : " + rel)
            resultats[rel] = {"type": "directory", "signature": _signature(chemin), "mode": stat.S_IMODE(info.st_mode)}
        for nom in fichiers:
            chemin = here / nom
            rel = chemin.relative_to(source).as_posix()
            _nom_archive(rel)
            info = chemin.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise ValueError("Lien symbolique source refusé : " + rel)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("Fichier spécial source refusé : " + rel)
            sidecar = snapshot_canonique and rel in SQLITE_SIDECARS and here == source
            resultats[rel] = {
                "type": "omitted_sqlite_sidecar" if sidecar else "file",
                "signature": _signature(chemin),
                "mode": stat.S_IMODE(info.st_mode),
            }
    return resultats


def _verifier_inventaire(source: Path, initial: dict[str, dict], *, snapshot_canonique=False) -> None:
    courant = _inventaire(source, snapshot_canonique=snapshot_canonique)
    if courant != initial:
        raise ValueError("Inventaire ou métadonnées source modifiés pendant la sauvegarde.")


def _verifier_sqlite(path: Path, *, exiger_benchab: bool) -> None:
    connexion = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        resultat = connexion.execute("PRAGMA integrity_check").fetchone()
        if resultat != ("ok",):
            raise ValueError("Intégrité SQLite invalide : " + str(resultat))
        if exiger_benchab and connexion.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
            raise ValueError("La base restaurée n'est pas une base BENCHAB canonique.")
    finally:
        connexion.close()


def _copier_flux(chemin: Path, destination: Path, signature: tuple, limite: int) -> dict:
    fd = os.open(chemin, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        with os.fdopen(fd, "rb") as source, destination.open("xb") as sortie:
            ouvert = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(ouvert.st_mode)
                or (
                    ouvert.st_dev,
                    ouvert.st_ino,
                    ouvert.st_size,
                    ouvert.st_mtime_ns,
                    ouvert.st_ctime_ns,
                )
                != signature
            ):
                raise ValueError("Source remplacée avant copie : " + str(chemin))
            digest, taille = _digest_flux(source, sortie, limit=limite,
                                          surveiller=lambda: _exiger_espace(destination.parent, PLANCHER_LIBRE))
            fin = os.fstat(source.fileno())
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    if (fin.st_dev, fin.st_ino, fin.st_size, fin.st_mtime_ns, fin.st_ctime_ns) != signature:
        raise ValueError("Source modifiée pendant la copie : " + str(chemin))
    if _signature(chemin) != signature:
        raise ValueError("Source remplacée après copie : " + str(chemin))
    os.chmod(destination, stat.S_IMODE(fin.st_mode))
    return {"type": "file", "size": taille, "sha256": digest, "mode": stat.S_IMODE(fin.st_mode)}


def _snapshot_sqlite(source: Path, destination: Path, signature: tuple) -> dict:
    if _signature(source) != signature:
        raise ValueError("Base SQLite modifiée avant le snapshot.")
    source_db = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=10)
    cible_db = sqlite3.connect(destination, timeout=10)
    try:
        source_db.backup(cible_db, pages=256, sleep=0.01,
                         progress=lambda *_: _exiger_espace(destination.parent, PLANCHER_LIBRE))
    finally:
        cible_db.close()
        source_db.close()
    _verifier_sqlite(destination, exiger_benchab=True)
    for suffixe in ("-wal", "-shm", "-journal"):
        Path(str(destination) + suffixe).unlink(missing_ok=True)
    if _signature(source) != signature:
        raise ValueError("Base SQLite modifiée pendant le snapshot.")
    with destination.open("rb") as flux:
        digest, taille = _digest_flux(flux, limit=MAX_MEMBER)
    return {
        "type": "file",
        "size": taille,
        "sha256": digest,
        "mode": stat.S_IMODE(source.stat().st_mode),
        "sqlite_snapshot": True,
    }


def _collecter(source: Path, travail: Path, base: Base) -> tuple[list[dict], dict[str, dict]]:
    snapshot_canonique = source == base.prive.resolve()
    initial = _inventaire(source, snapshot_canonique=snapshot_canonique)
    fichiers = []
    total = 0
    for rel, entree in sorted(initial.items()):
        cible = travail / rel
        if entree["type"] == "directory":
            cible.mkdir(parents=True, exist_ok=True)
            os.chmod(cible, entree["mode"])
            fichiers.append({"path": rel, "type": "directory", "mode": entree["mode"]})
            continue
        if entree["type"] == "omitted_sqlite_sidecar":
            continue
        cible.parent.mkdir(parents=True, exist_ok=True)
        if source == base.prive.resolve() and rel == "benchab.sqlite":
            record = _snapshot_sqlite(source / rel, cible, entree["signature"])
        else:
            if entree["signature"][2] > MAX_MEMBER:
                raise ValueError("Fichier trop volumineux pour le budget de sauvegarde : " + rel)
            record = _copier_flux(source / rel, cible, entree["signature"], MAX_MEMBER)
        total += record["size"]
        if total > MAX_TOTAL:
            raise ValueError("Budget total de sauvegarde dépassé.")
        fichiers.append({"path": rel, **record})
    _verifier_inventaire(source, initial, snapshot_canonique=snapshot_canonique)
    return fichiers, initial


def _manifest_bytes(manifeste: dict) -> bytes:
    return (json.dumps(manifeste, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def creer(base: Base, source: str | Path, destination: str | Path) -> dict:
    """Archive une source privée explicite, tailles en octets ; rapport ou FAIL, aucun profil ni source modifié.

    Départ 31 Gio libres, plancher 10 Gio pendant copie ; refuse les liens source, ne collecte rien implicitement.
    """
    source = _chemin_prive(base, source)
    if not source.is_dir():
        raise ValueError("La source explicite doit être un dossier privé.")
    cible = _chemin_destination(base, destination, autoriser_prive=True)
    if os.path.lexists(cible):
        raise FileExistsError(cible)
    if cible.is_relative_to(source):
        raise ValueError("La destination archive ne peut pas être incluse dans la source.")
    # Le plafond protège le volume réel de copie, pas le HOME de l'appelant
    # (docs/reference/donnees.md, sauvegarde explicite).
    _exiger_espace(cible.parent, SEUIL_DEPART)
    _exiger_espace(PRIVATE_TMP, SEUIL_DEPART)
    with tempfile.TemporaryDirectory(prefix="benchab-backup-", dir=PRIVATE_TMP) as tmp:
        travail = Path(tmp)
        fichiers, initial = _collecter(source, travail, base)
        manifeste = {
            "schema": 1,
            "source": source.relative_to(base.prive.resolve()).as_posix(),
            "files": fichiers,
        }
        (travail / MANIFEST_NAME).write_bytes(_manifest_bytes(manifeste))
        temporaire = cible.parent / (".benchab-backup-" + uuid.uuid4().hex + ".tmp")
        try:
            with tarfile.open(temporaire, "w") as archive:
                for chemin in sorted(travail.rglob("*")):
                    rel = chemin.relative_to(travail).as_posix()
                    _exiger_espace(cible.parent, PLANCHER_LIBRE)
                    info = archive.gettarinfo(chemin, arcname=rel)
                    if info.isfile():
                        with chemin.open("rb") as flux:
                            archive.addfile(info, _LectureSurveillee(flux, cible.parent))
                    else:
                        archive.addfile(info)
            _verifier_inventaire(source, initial, snapshot_canonique=source == base.prive.resolve())
            _verifier_archive_creee(temporaire, fichiers, _manifest_bytes(manifeste))
            renommer_exclusif(temporaire, cible)
        finally:
            temporaire.unlink(missing_ok=True)
    return {
        "archive": str(cible),
        "files": len(fichiers),
        "manifest_sha256": hashlib.sha256(_manifest_bytes(manifeste)).hexdigest(),
    }


def _sha_membre(tar: tarfile.TarFile, membre: tarfile.TarInfo, limite: int) -> tuple[str, int]:
    flux = tar.extractfile(membre)
    if flux is None:
        raise ValueError("Contenu de fichier absent : " + membre.name)
    with flux:
        return _digest_flux(flux, limit=limite)


def _verifier_archive_creee(path: Path, fichiers: list[dict], manifest_data: bytes) -> None:
    attendus = {item["path"]: item for item in fichiers}
    with tarfile.open(path, "r:") as tar:
        index = {item.name: item for item in tar.getmembers()}
        attendus_noms = set(attendus) | {MANIFEST_NAME}
        if len(index) != len(tar.getmembers()) or set(index) != attendus_noms:
            raise ValueError(
                "Inventaire de l'archive créée incohérent : "
                f"absents={sorted(attendus_noms - set(index))}, en_trop={sorted(set(index) - attendus_noms)}"
            )
        if tar.extractfile(index[MANIFEST_NAME]).read(MAX_MANIFEST + 1) != manifest_data:
            raise ValueError("Manifeste de l'archive créée incohérent.")
        for rel, attendu in attendus.items():
            item = index[rel]
            if attendu["type"] == "directory":
                if not item.isdir():
                    raise ValueError("Type de dossier créé incohérent : " + rel)
            else:
                if not item.isfile():
                    raise ValueError("Type de fichier créé incohérent : " + rel)
                digest, taille = _sha_membre(tar, item, MAX_MEMBER)
                if digest != attendu["sha256"] or taille != attendu["size"]:
                    raise ValueError("Contenu archive créé incohérent : " + rel)


def _nom_archive(nom: str) -> PurePosixPath:
    chemin = PurePosixPath(nom)
    if (
        not nom
        or chemin.is_absolute()
        or not chemin.parts
        or any(part in ("", ".", "..") for part in chemin.parts)
        or "\\" in nom
        or PureWindowsPath(nom).drive
        or any(ord(caractere) < 32 or ord(caractere) == 127 for caractere in nom)
    ):
        raise ValueError("Chemin archive dangereux : " + repr(nom))
    return chemin


def _cible_lien(nom: str, item: tarfile.TarInfo) -> str:
    cible = _nom_archive(item.linkname)
    if item.issym():
        cible = PurePosixPath(nom).parent / cible
    return _nom_archive(cible.as_posix()).as_posix()


def _valider_graphe(index: dict[str, tarfile.TarInfo]) -> dict[str, str]:
    for nom in index:
        chemin = PurePosixPath(nom)
        for parent in chemin.parents:
            item = index.get(parent.as_posix())
            if item is not None and not item.isdir():
                raise ValueError("Conflit fichier-parent dans archive : " + parent.as_posix())

    def regulier(nom: str, vus: tuple[str, ...] = ()) -> str:
        if nom in vus:
            raise ValueError("Cycle de liens dans archive : " + nom)
        item = index.get(nom)
        if item is None:
            raise ValueError("Cible de lien absente : " + nom)
        if item.isfile():
            return nom
        if item.issym() or item.islnk():
            return regulier(_cible_lien(nom, item), (*vus, nom))
        raise ValueError("Lien vers un non-fichier : " + nom)

    return {nom: regulier(nom) for nom, item in index.items() if item.issym() or item.islnk()}


def _valider_manifeste(tar: tarfile.TarFile, index: dict[str, tarfile.TarInfo]) -> tuple[dict, dict[str, dict]]:
    item = index.get(MANIFEST_NAME)
    if item is None or not item.isfile() or item.size > MAX_MANIFEST:
        raise ValueError("Manifeste absent, spécial ou trop volumineux.")
    with tar.extractfile(item) as flux:
        contenu = flux.read(MAX_MANIFEST + 1)
    if len(contenu) > MAX_MANIFEST:
        raise ValueError("Manifeste trop volumineux.")
    manifeste = json.loads(contenu)
    if manifeste.get("schema") != 1 or not isinstance(manifeste.get("files"), list):
        raise ValueError("Manifeste invalide.")
    attendus = {}
    for entree in manifeste["files"]:
        if not isinstance(entree, dict) or not isinstance(entree.get("path"), str):
            raise ValueError("Entrée de manifeste invalide.")
        nom = _nom_archive(entree["path"]).as_posix()
        if nom == MANIFEST_NAME or nom in attendus:
            raise ValueError("Entrée manifeste dupliquée ou réservée : " + nom)
        attendus[nom] = entree
    if set(index) != set(attendus) | {MANIFEST_NAME}:
        raise ValueError("Inventaire et archive différents.")
    for nom, entree in attendus.items():
        item_archive = index[nom]
        kind = entree.get("type")
        correspond = {
            "file": item_archive.isfile(),
            "directory": item_archive.isdir(),
            "symlink": item_archive.issym(),
            "hardlink": item_archive.islnk(),
        }.get(kind, False)
        if not correspond:
            raise ValueError("Type du manifeste et de l'archive différents : " + nom)
        if kind in ("file",):
            if type(entree.get("size")) is not int or entree["size"] != item_archive.size:
                raise ValueError("Taille du manifeste incohérente : " + nom)
            sha = entree.get("sha256")
            if not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
                raise ValueError("SHA-256 invalide : " + nom)
        elif kind in ("symlink", "hardlink") and entree.get("target") != item_archive.linkname:
            raise ValueError("Cible du lien incohérente : " + nom)
    return manifeste, attendus


def _copier_membre(tar: tarfile.TarFile, item: tarfile.TarInfo, sortie: Path, attendu: dict) -> None:
    flux = tar.extractfile(item)
    if flux is None:
        raise ValueError("Contenu de fichier absent : " + item.name)
    with flux, sortie.open("xb") as destination:
        digest, taille = _digest_flux(flux, destination, limit=MAX_MEMBER,
                                      surveiller=lambda: _exiger_espace(sortie.parent, PLANCHER_LIBRE))
    if taille != attendu["size"] or digest != attendu["sha256"]:
        raise ValueError("Taille ou empreinte incorrecte : " + item.name)
    os.chmod(sortie, int(attendu.get("mode", item.mode)) & 0o777)


def restaurer(archive: str | Path, destination: str | Path, *, base: Base) -> dict:
    """Restaure une archive privée après contrôle SHA/espace en octets ; rapport ou FAIL, aucune cible écrasée.

    Seuls les liens internes vérifiés vers des fichiers sont recréés ; aucun profil installé n'est collecté.
    """
    cible = _chemin_destination(base, destination, autoriser_prive=True)
    if not cible.is_relative_to(base.prive.resolve()):
        raise ValueError("La restauration doit rester dans base.prive.")
    if os.path.lexists(cible):
        raise FileExistsError(cible)
    archive_path = _chemin_destination(base, archive, autoriser_prive=False)
    if not archive_path.is_file():
        raise ValueError("Archive régulière requise.")

    with tarfile.open(archive_path, "r:*") as tar:
        membres = tar.getmembers()
        index = {}
        total = 0
        for item in membres:
            nom = _nom_archive(item.name).as_posix()
            if nom in index:
                raise ValueError("Entrée d'archive dupliquée : " + nom)
            if not (item.isfile() or item.isdir() or item.issym() or item.islnk()):
                raise ValueError("Entrée spéciale refusée : " + nom)
            if item.isfile():
                if item.size > MAX_MEMBER:
                    raise ValueError("Membre trop volumineux : " + nom)
                total += item.size
                if total > MAX_TOTAL:
                    raise ValueError("Budget total d'archive dépassé.")
            index[nom] = item
        manifeste, attendus = _valider_manifeste(tar, index)
        liens = _valider_graphe(index)

        # Prélecture et SHA de chaque fichier avant de créer le staging.
        for nom, entree in attendus.items():
            item = index[nom]
            if entree["type"] == "file":
                digest, taille = _sha_membre(tar, item, MAX_MEMBER)
                if taille != entree["size"] or digest != entree["sha256"]:
                    raise ValueError("Taille ou empreinte incorrecte : " + nom)

        _exiger_espace(cible.parent, SEUIL_DEPART)
        staging = Path(tempfile.mkdtemp(prefix=".benchab-restauration-", dir=cible.parent))
        try:
            for nom, entree in attendus.items():
                item = index[nom]
                sortie = staging / nom
                if entree["type"] == "directory":
                    sortie.mkdir(parents=True, exist_ok=True)
                    os.chmod(sortie, 0o700)
                elif entree["type"] == "file":
                    sortie.parent.mkdir(parents=True, exist_ok=True)
                    _copier_membre(tar, item, sortie, entree)
            for nom, entree in attendus.items():
                item = index[nom]
                sortie = staging / nom
                if entree["type"] == "hardlink":
                    sortie.parent.mkdir(parents=True, exist_ok=True)
                    os.link(staging / liens[nom], sortie, follow_symlinks=False)
                elif entree["type"] == "symlink":
                    sortie.parent.mkdir(parents=True, exist_ok=True)
                    os.symlink(item.linkname, sortie)
            for nom, entree in attendus.items():
                if entree.get("sqlite_snapshot"):
                    _verifier_sqlite(staging / nom, exiger_benchab=True)
            for nom, entree in sorted(
                attendus.items(), key=lambda couple: len(PurePosixPath(couple[0]).parts), reverse=True
            ):
                if entree["type"] == "directory":
                    os.chmod(staging / nom, int(entree.get("mode", 0o700)) & 0o777)
            renommer_exclusif(staging, cible)
            staging = None
        finally:
            if staging is not None:
                shutil.rmtree(staging)
    return {
        "destination": str(cible),
        "files": sum(entree["type"] == "file" for entree in attendus.values()),
        "verified": True,
        "source": manifeste.get("source"),
    }


def comparer_arbres(base: Base, attendu: str | Path, obtenu: str | Path) -> dict:
    """Compare deux arbres privés (octets/SHA/modes), PASS/FAIL ; lecture seule, pas de comparaison SQLite active.

    Refuse les liens source et les chemins hors privé ; ne répare rien et n'adopte aucun réglage.
    """
    a, b = _chemin_prive(base, attendu), _chemin_prive(base, obtenu)
    if not a.is_dir() or not b.is_dir():
        raise ValueError("Deux dossiers privés existants sont requis.")

    def manifeste(dossier):
        initial = _inventaire(dossier)
        resultat = {}
        for nom, entree in initial.items():
            item = {"type": entree["type"], "mode": entree["mode"]}
            if entree["type"] == "file":
                from benchab.qualite.empreintes import sha256_fichier

                if entree["signature"][2] > MAX_MEMBER:
                    raise ValueError("Fichier au-delà du budget de comparaison : " + nom)
                item.update(sha256=sha256_fichier(dossier / nom), octets=entree["signature"][2])
            resultat[nom] = item
        _verifier_inventaire(dossier, initial)
        return resultat

    ma, mb = manifeste(a), manifeste(b)
    ecarts = [{"chemin": nom, "attendu": ma.get(nom), "obtenu": mb.get(nom)}
              for nom in sorted(ma.keys() | mb.keys()) if ma.get(nom) != mb.get(nom)]
    return {"statut": "FAIL" if ecarts else "PASS", "ecarts": ecarts,
            "fichiers_attendus": len(ma), "fichiers_obtenus": len(mb), "lecture_seule": True}
