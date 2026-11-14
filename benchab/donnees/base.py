"""SQLite et export JSONL ; aucun stockage personnel n'existait dans le paquet."""

import fcntl
import hashlib
import json
import os
import sqlite3
import stat
import tempfile
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path

TABLES = (
    "projets", "preparations", "impressions", "retours", "filaments", "bobines", "profils",
    "connaissances", "decisions", "votes", "seances", "mesures", "versions",
)
DOSSIERS = ("export", "projets", "config", "mesures", "rapports", "instantanes", "cache", "chantier")
APPLICATION_ID = 0x42454E43


def _signature_export(info):
    """Capture l'identité d'un export régulier pour détecter un remplacement concurrent."""
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _ouvrir_export(path):
    """Ouvre un export existant sans suivre de lien ; absent → None, type spécial → FAIL."""
    try:
        avant = path.lstat()
    except FileNotFoundError:
        return None, None
    if stat.S_ISLNK(avant.st_mode):
        raise ValueError("Export lié refusé")
    if not stat.S_ISREG(avant.st_mode):
        raise ValueError("Export spécial refusé")
    signature = _signature_export(avant)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    flux = os.fdopen(fd, "rb")
    if _signature_export(os.fstat(fd)) != signature:
        flux.close()
        raise ValueError("Export remplacé pendant l'ouverture")
    return flux, signature


def _verifier_export_inchange(path, signature):
    """Refuse publication si la destination a changé depuis sa comparaison canonique."""
    try:
        courant = _signature_export(path.lstat())
    except FileNotFoundError:
        courant = None
    if courant != signature:
        raise ValueError("Export remplacé pendant sa vérification")


def _export_identique(path, attendu):
    """Compare les octets et SHA réels d'un petit manifeste, sans suivre de lien."""
    flux, signature = _ouvrir_export(path)
    if flux is None:
        return False, None
    try:
        sha = hashlib.sha256()
        identique = True
        position = 0
        while position < len(attendu):
            bloc = flux.read(min(1024 * 1024, len(attendu) - position))
            if not bloc:
                identique = False
                break
            sha.update(bloc)
            fin = position + len(bloc)
            identique &= bloc == attendu[position:fin]
            position = fin
        reste = flux.read(1)
        sha.update(reste)
        identique &= not reste and position == len(attendu)
        if _signature_export(os.fstat(flux.fileno())) != signature:
            raise ValueError("Export modifié pendant sa vérification")
        _verifier_export_inchange(path, signature)
        return identique and sha.digest() == hashlib.sha256(attendu).digest(), signature
    finally:
        flux.close()


def _export_sha_identique(path, attendu, octets):
    """Compare un export au SHA attendu issu d'un instantané SQLite identifié."""
    flux, signature = _ouvrir_export(path)
    if flux is None:
        return False, None
    try:
        identique = False
        if signature[3] == octets:
            empreinte = hashlib.sha256()
            restant = octets
            while restant:
                bloc = flux.read(min(1024 * 1024, restant))
                if not bloc:
                    break
                empreinte.update(bloc)
                restant -= len(bloc)
            identique = not restant and not flux.read(1) and empreinte.hexdigest() == attendu
        if _signature_export(os.fstat(flux.fileno())) != signature:
            raise ValueError("Export modifié pendant sa vérification")
        _verifier_export_inchange(path, signature)
        return identique, signature
    finally:
        flux.close()


def _ligne_jsonl(donnees):
    """Sérialise une ligne une fois, pour validation et empreinte du même JSONL."""
    texte = json.dumps(donnees, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
    return texte.encode("utf-8")


def lignes_export(connexion, table):
    """Produit les lignes JSONL canoniques d’une table BENCHAB, en octets UTF-8.

    La connexion fournie conserve son instantané et doit renvoyer des lignes
    SQLite nommées. La table doit être connue ; aucune écriture n’a lieu.
    """
    if table not in TABLES:
        raise ValueError("Table d’export BENCHAB inconnue.")
    for ligne in connexion.execute(f'SELECT * FROM main."{table}" ORDER BY id'):
        yield _ligne_jsonl(dict(ligne))


class _ConnexionAtelier(sqlite3.Connection):
    """Connexion dont les accès hors SQL ordinaire désactivent le cache JSONL."""

    suivi_sql = True
    autoriseur_externe = False

    def blobopen(self, table, column, row, /, *, readonly=False, name="main"):
        """Ouvre le BLOB SQLite ; un accès en écriture rend le suivi SQL incomplet."""
        if not readonly:
            self.suivi_sql = False
        return super().blobopen(table, column, row, readonly=readonly, name=name)

    def deserialize(self, data, /, *, name="main"):
        """Charge des octets SQLite et invalide tout suivi de l'ancienne base."""
        self.suivi_sql = False
        return super().deserialize(data, name=name)

    def set_authorizer(self, authorizer_callback):
        """Préserve l'autoriseur demandé par l'appelant, avec audit complet ensuite."""
        self.suivi_sql = False
        self.autoriseur_externe = True
        return super().set_authorizer(authorizer_callback)


class Base:
    """Base canonique privée ; chemins explicites et données en unités natives, aucune commande machine.

    L'ouverture peut refuser la création ; les fixtures et initialisations explicites utilisent creer=True.
    """

    def __init__(self, prive, *, creer=True):
        """Ouvre le chemin choisi ; FAIL si absent avec creer=False, aucun atelier implicite hors de ce chemin."""
        from benchab.chemins import atelier_prive

        self.prive = atelier_prive(prive)
        self._export_attendu = None
        self._export_verifie = None
        self._bloc_transaction = 0
        chemin = self.prive / "benchab.sqlite"
        if chemin.is_symlink():
            raise ValueError("Une base liée est refusée.")
        if not creer and not chemin.is_file():
            raise FileNotFoundError("Atelier absent : lancer benchab init avec ce même --prive.")
        if not creer:
            # Vérifier l'identité avant chmod/mkdir : une base étrangère n'est
            # pas un atelier à réparer (docs/reference/chemins-et-atelier.md).
            fd = os.open(chemin, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as flux:
                entete = flux.read(100)
            if len(entete) < 72 or int.from_bytes(entete[68:72], "big") != APPLICATION_ID:
                raise ValueError("Base existante étrangère à BENCHAB : aucune migration implicite.")
        if self.prive.is_symlink():
            raise ValueError("Le répertoire privé ne peut pas être un lien symbolique.")
        self.prive.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.prive.chmod(0o700)
        for dossier in DOSSIERS:
            cible = self.prive / dossier
            if cible.is_symlink():
                raise ValueError(f"Sous-dossier privé lié refusé : {dossier}")
            cible.mkdir(mode=0o700, exist_ok=True)
        chemin = self.prive / "benchab.sqlite"
        if chemin.is_symlink():
            raise ValueError("Une base liée est refusée.")
        self.connexion = sqlite3.connect(
            chemin if creer else chemin.as_uri() + "?mode=rw",
            uri=not creer, timeout=10, factory=_ConnexionAtelier,
        )
        self.connexion.row_factory = sqlite3.Row
        self.connexion.execute("PRAGMA foreign_keys = ON")
        try:
            if creer:
                from benchab.qualite.ecritures import verrou_publication

                # Deux initialisations ne peuvent plus décider ensemble que
                # le schéma est absent (docs/reference/donnees.md).
                with verrou_publication(self.prive / "cache", "initialisation.lock"):
                    self._initialiser(creer)
            else:
                self._initialiser(creer)
        except BaseException:
            self.connexion.close()
            raise

    def _initialiser(self, creer):
        identifiant = self.connexion.execute("PRAGMA application_id").fetchone()[0]
        tables = self.connexion.execute("SELECT name FROM sqlite_schema WHERE type = 'table'").fetchall()
        if tables and identifiant != APPLICATION_ID:
            raise ValueError("Base existante étrangère à BENCHAB : aucune migration implicite.")
        if not tables:
            if not creer:
                    raise ValueError("Atelier sans schéma : initialisation explicite requise.")
            schema = files("benchab").joinpath("donnees/schema.sql").read_text(encoding="utf-8")
            self.connexion.executescript(schema)
            self.connexion.execute(f"PRAGMA application_id = {APPLICATION_ID}")
            self.connexion.commit()
            self.exporter()

    def fermer(self):
        """Ferme la connexion SQLite ; ne crée aucun export supplémentaire."""
        self.connexion.close()

    def ecrire(self, requete, parametres=()):
        """Exécute le SQL paramétré dans une transaction vérifiée et renvoie son curseur."""
        with self.transaction() as connexion:
            curseur = connexion.execute(requete, parametres)
        return curseur

    @contextmanager
    def transaction(self):
        """Fournit la connexion SQLite et publie ses exports JSONL en octets UTF-8.

        Les références durables sont contrôlées avant le commit. Les tables
        inchangées réemploient un SHA issu de la même connexion ; les fichiers
        et les chemins restent vérifiés. Une exception dans le bloc conserve
        le rollback SQLite habituel.
        """
        from benchab.qualite.ecritures import sortie_durable

        precedent = self._export_verifie
        self._export_verifie = None
        changements_avant = self.connexion.total_changes
        tables_touchees: set[str] = set()
        self._bloc_transaction += 1
        bloc = self._bloc_transaction
        tracer = self.connexion.suivi_sql and precedent is not None
        suivi_complet = True
        transaction_continue = True

        def suivre(action, nom, detail, schema, origine):
            nonlocal suivi_complet, transaction_continue
            if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE):
                if schema == "main" and nom in TABLES:
                    tables_touchees.add(nom)
            elif action in (sqlite3.SQLITE_PRAGMA, sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH):
                suivi_complet = False
            elif action == sqlite3.SQLITE_SAVEPOINT or (action == sqlite3.SQLITE_TRANSACTION and nom != "BEGIN"):
                transaction_continue = False
            return sqlite3.SQLITE_OK

        with self.connexion:
            # Poser l'autoriseur à chaque bloc force SQLite à recompiler les
            # requêtes en cache. Les écritures des triggers sont suivies aussi (#19).
            observer = not self.connexion.autoriseur_externe
            if observer:
                sqlite3.Connection.set_authorizer(self.connexion, suivre)
            try:
                yield self.connexion
            finally:
                if observer and not self.connexion.autoriseur_externe:
                    sqlite3.Connection.set_authorizer(self.connexion, None)
            # Sans écriture dans un bloc SQLite actif, un snapshot de lecture
            # WAL peut précéder un commit externe. L'export recalcule alors.
            version = self._version_export() if (
                self.connexion.in_transaction and self.connexion.total_changes != changements_avant
                and transaction_continue and self._bloc_transaction == bloc
            ) else None
            reutiliser = (
                tracer and suivi_complet and self.connexion.suivi_sql and self._bloc_transaction == bloc
                and version is not None and precedent is not None
                and precedent[0] == (changements_avant, *version[1:])
            )
            digests, chemins = {}, {}
            for table in TABLES:
                if reutiliser and precedent is not None and table not in tables_touchees:
                    digests[table] = precedent[1][table]
                    chemins[table] = precedent[2][table]
                else:
                    digests[table], chemins[table] = self._verifier_table(table)
            # L'identité SQLite ne garantit pas que le système de fichiers
            # n'a pas changé. Les chemins distincts sont donc toujours relus.
            for chemin in dict.fromkeys(chemin for table in TABLES for chemin in chemins[table]):
                sortie_durable(chemin, prive=self.prive)

        self._export_attendu = (version, digests, chemins) if version is not None else None
        try:
            self.exporter()
        finally:
            self._export_attendu = None

    def _verifier_table(self, table):
        """Calcule SHA et taille JSONL, et collecte les chemins à revalider avant commit."""
        from benchab.qualite.ecritures import chemins_references_durables

        empreinte, octets, chemins = hashlib.sha256(), 0, set()
        for ligne in self.connexion.execute(f'SELECT * FROM main."{table}" ORDER BY id'):
            donnees = dict(ligne)
            canonique = _ligne_jsonl(donnees)
            empreinte.update(canonique)
            octets += len(canonique)
            for cle, valeur in donnees.items():
                if not isinstance(valeur, str):
                    continue
                # Le décodage est requis quand un JSON masque un séparateur.
                if cle.endswith("_json"):
                    if valeur == "{}":
                        continue
                    try:
                        valeur = json.loads(valeur)
                    except ValueError:
                        pass
                chemins.update(chemins_references_durables(valeur, self.prive))
        return (empreinte.hexdigest(), octets), chemins

    def _version_export(self):
        """Épingle l'instantané puis relève les compteurs utiles sur la même connexion."""
        self.connexion.execute("SELECT count(*) FROM main.sqlite_schema").fetchone()
        # data_version ne signale que les commits d'autres connexions. Le
        # total_changes protège le réemploi en cas d'écriture locale intercalée (#19).
        return (
            self.connexion.total_changes,
            self.connexion.execute("PRAGMA main.data_version").fetchone()[0],
            self.connexion.execute("PRAGMA main.schema_version").fetchone()[0],
            self.connexion.execute("PRAGMA main.user_version").fetchone()[0],
        )

    def exporter(self):
        """Publie les JSONL cohérents et le manifeste, sans écrire dans SQLite.

        Les exports conformes gardent leurs octets et leur inode. En
        l’absence d’empreintes de transaction encore qualifiées, compare
        toute la sérialisation. Réserve brièvement le verrou SQLite d’écriture
        pendant la publication, y compris en WAL. Refuse une connexion en
        lecture seule ou un contexte SQLite déjà ouvert.
        """
        from benchab.chemins import sans_lien
        from benchab.qualite.ecritures import ouvrir_verrou

        if self.connexion.in_transaction:
            raise ValueError("Terminer la transaction avant l'export")
        self._export_verifie = None
        cache = sans_lien(self.prive / "cache", racine=self.prive)
        dossier = os.open(cache, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            # Le chemin peut changer après la garde : ouvrir le verrou sans suivre de lien (#138).
            verrou = ouvrir_verrou("export.lock", dossier)
            try:
                fcntl.flock(verrou, fcntl.LOCK_EX)
                if not os.path.samestat(os.fstat(dossier), cache.lstat()) or not os.path.samestat(
                    os.fstat(verrou), os.stat("export.lock", dir_fd=dossier, follow_symlinks=False)
                ):
                    raise ValueError("Verrou d'export remplacé pendant son ouverture")
                # En WAL, un snapshot de lecture seul n'empêche pas un autre
                # auteur de committer avant le manifeste. Réserver l'écriture
                # pendant la publication, sans changer de ligne (docs/reference/donnees.md, #226).
                self.connexion.execute("BEGIN IMMEDIATE")
                try:
                    self._exporter_tables()
                finally:
                    self.connexion.rollback()
            finally:
                os.close(verrou)
        finally:
            os.close(dossier)

    def _lignes_canoniques(self, table):
        """Rend les lignes JSONL dans les mêmes octets pour comparaison et publication."""
        yield from lignes_export(self.connexion, table)

    def _ecrire_table_temporaire(self, table, dossier):
        """Sérialise une table SQLite en UTF-8 JSONL temporaire ; renvoie son SHA-256 hexadécimal."""
        fd, nom = tempfile.mkstemp(prefix=f".{table}-", dir=dossier)
        try:
            empreinte = hashlib.sha256()
            with os.fdopen(fd, "wb") as flux:
                for octets in self._lignes_canoniques(table):
                    flux.write(octets)
                    empreinte.update(octets)
            return Path(nom), empreinte.hexdigest()
        except BaseException:
            Path(nom).unlink(missing_ok=True)
            raise

    def _exporter_tables(self):
        from benchab.chemins import sans_lien

        # Base peut être ouverte depuis longtemps : ne pas reprendre un dossier lié (#138).
        dossier = sans_lien(self.prive / "export", racine=self.prive, strict=True)
        empreintes = {}
        attendu = None
        if self._export_attendu is not None and self.connexion.suivi_sql:
            version, vues, _ = self._export_attendu
            if version == self._version_export():
                attendu = vues
        for table in TABLES:
            cible = dossier / f"{table}.jsonl"
            if attendu is not None:
                sha, octets = attendu[table]
                identique, signature = _export_sha_identique(cible, sha, octets)
                empreintes[table] = sha
                if not identique:
                    self._publier_table(table, dossier, cible, signature, sha)
                continue
            ancien, signature = _ouvrir_export(cible)
            if ancien is None:
                temporaire, empreintes[table] = self._ecrire_table_temporaire(table, dossier)
                try:
                    _verifier_export_inchange(cible, None)
                    temporaire.replace(cible)
                finally:
                    temporaire.unlink(missing_ok=True)
                continue

            try:
                canonique = hashlib.sha256()
                empreinte_existante = hashlib.sha256()
                identique = True
                for octets in self._lignes_canoniques(table):
                    canonique.update(octets)
                    existants = ancien.read(len(octets))
                    empreinte_existante.update(existants)
                    identique &= existants == octets
                reste = ancien.read(1)
                empreinte_existante.update(reste)
                identique &= not reste
                if _signature_export(os.fstat(ancien.fileno())) != signature:
                    raise ValueError("Export modifié pendant sa vérification")
                _verifier_export_inchange(cible, signature)
            finally:
                ancien.close()

            empreintes[table] = canonique.hexdigest()
            if identique and empreinte_existante.hexdigest() == empreintes[table]:
                continue
            # Une table divergente seule est rescannée pour produire son temporaire canonique.
            self._publier_table(table, dossier, cible, signature, empreintes[table])
        manifeste = {
            "schema": self.connexion.execute("PRAGMA user_version").fetchone()[0],
            "tables": list(TABLES),
            "sha256": empreintes,
        }
        manifeste_octets = (json.dumps(manifeste, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        fichier_manifeste = dossier / "manifest.json"
        manifeste_identique, signature_manifeste = _export_identique(fichier_manifeste, manifeste_octets)
        if not manifeste_identique:
            fd, nom = tempfile.mkstemp(prefix=".manifest-", dir=dossier)
            try:
                with os.fdopen(fd, "wb") as flux:
                    flux.write(manifeste_octets)
                _verifier_export_inchange(fichier_manifeste, signature_manifeste)
                Path(nom).replace(fichier_manifeste)
            finally:
                Path(nom).unlink(missing_ok=True)
        if attendu is not None and self.connexion.suivi_sql:
            self._export_verifie = self._export_attendu


    def _publier_table(self, table, dossier, cible, signature, attendu):
        """Relit une table divergente dans l'instantané avant de remplacer son export."""
        temporaire, empreinte = self._ecrire_table_temporaire(table, dossier)
        try:
            if empreinte != attendu:
                raise ValueError("Table SQLite modifiée pendant l’export")
            _verifier_export_inchange(cible, signature)
            temporaire.replace(cible)
        finally:
            temporaire.unlink(missing_ok=True)
