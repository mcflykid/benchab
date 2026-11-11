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


class Base:
    """Base canonique privée ; chemins explicites et données en unités natives, aucune commande machine.

    L'ouverture peut refuser la création ; les fixtures et initialisations explicites utilisent creer=True.
    """

    def __init__(self, prive, *, creer=True):
        """Ouvre le chemin choisi ; FAIL si absent avec creer=False, aucun atelier implicite hors de ce chemin."""
        from benchab.chemins import atelier_prive

        self.prive = atelier_prive(prive)
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
        self.connexion = (sqlite3.connect(chemin, timeout=10) if creer else
                          sqlite3.connect(chemin.as_uri() + "?mode=rw", uri=True, timeout=10))
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
        self.connexion.close()

    def ecrire(self, requete, parametres=()):
        with self.transaction() as connexion:
            curseur = connexion.execute(requete, parametres)
        return curseur

    @contextmanager
    def transaction(self):
        """Valide les données et leurs références durables avant le commit, puis exporte."""
        from benchab.qualite.ecritures import verifier_references_durables

        with self.connexion:
            yield self.connexion
            for table in TABLES:
                for ligne in self.connexion.execute(f'SELECT * FROM "{table}"'):
                    json.dumps(dict(ligne), allow_nan=False)
                    for cle, valeur in dict(ligne).items():
                        if not isinstance(valeur, str):
                            continue
                        if cle.endswith("_json") and "/" in valeur:
                            try:
                                valeur = json.loads(valeur)
                            except ValueError:
                                pass
                        verifier_references_durables(valeur, self.prive)
        self.exporter()

    def exporter(self):
        """Un export cohérent, sérialisé et vérifiable après chaque transaction réussie."""
        from benchab.chemins import sans_lien

        if self.connexion.in_transaction:
            raise ValueError("Terminer la transaction avant l'export")
        chemin_verrou = sans_lien(self.prive / "cache/export.lock", racine=self.prive)
        with chemin_verrou.open("a") as verrou:
            fcntl.flock(verrou, fcntl.LOCK_EX)
            self.connexion.execute("BEGIN")
            try:
                self._exporter_tables()
            finally:
                self.connexion.rollback()

    def _lignes_canoniques(self, table):
        """Rend les lignes JSONL dans les mêmes octets pour comparaison et publication."""
        for ligne in self.connexion.execute(f'SELECT * FROM "{table}" ORDER BY id'):
            texte = json.dumps(dict(ligne), ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
            yield texte.encode("utf-8")

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
        for table in TABLES:
            cible = dossier / f"{table}.jsonl"
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
            temporaire, empreinte_temporaire = self._ecrire_table_temporaire(table, dossier)
            try:
                if empreinte_temporaire != empreintes[table]:
                    raise ValueError("Table SQLite modifiée pendant l’export")
                _verifier_export_inchange(cible, signature)
                temporaire.replace(cible)
            finally:
                temporaire.unlink(missing_ok=True)
        manifeste = {
            "schema": self.connexion.execute("PRAGMA user_version").fetchone()[0],
            "tables": list(TABLES),
            "sha256": empreintes,
        }
        manifeste_octets = (json.dumps(manifeste, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        fichier_manifeste = dossier / "manifest.json"
        manifeste_identique, signature_manifeste = _export_identique(fichier_manifeste, manifeste_octets)
        if manifeste_identique:
            return
        fd, nom = tempfile.mkstemp(prefix=".manifest-", dir=dossier)
        try:
            with os.fdopen(fd, "wb") as flux:
                flux.write(manifeste_octets)
            _verifier_export_inchange(fichier_manifeste, signature_manifeste)
            Path(nom).replace(fichier_manifeste)
        finally:
            Path(nom).unlink(missing_ok=True)
