"""SQLite et export JSONL ; aucun stockage personnel n'existait dans le paquet."""

import fcntl
import hashlib
import json
import os
import sqlite3
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
        identifiant = self.connexion.execute("PRAGMA application_id").fetchone()[0]
        tables = self.connexion.execute("SELECT name FROM sqlite_schema WHERE type = 'table'").fetchall()
        if tables and identifiant != APPLICATION_ID:
            self.connexion.close()
            raise ValueError("Base existante étrangère à BENCHAB : aucune migration implicite.")
        if not tables:
            if not creer:
                self.connexion.close()
                raise ValueError("Atelier sans schéma : initialisation explicite requise.")
            schema = files("benchab").joinpath("donnees/schema.sql").read_text()
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
        if self.connexion.in_transaction:
            raise ValueError("Terminer la transaction avant l'export")
        with (self.prive / "cache/export.lock").open("a") as verrou:
            fcntl.flock(verrou, fcntl.LOCK_EX)
            self.connexion.execute("BEGIN")
            try:
                self._exporter_tables()
            finally:
                self.connexion.rollback()

    def _exporter_tables(self):
        dossier = self.prive / "export"
        empreintes = {}
        for table in TABLES:
            if (dossier / f"{table}.jsonl").is_symlink():
                raise ValueError("Export lié refusé")
            fd, nom = tempfile.mkstemp(prefix=f".{table}-", dir=dossier)
            try:
                with os.fdopen(fd, "w") as flux:
                    lignes = self.connexion.execute(f'SELECT * FROM "{table}" ORDER BY id')
                    empreinte = hashlib.sha256()
                    for ligne in lignes:
                        contenu = json.dumps(dict(ligne), ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
                        flux.write(contenu)
                        empreinte.update(contenu.encode())
                    empreintes[table] = empreinte.hexdigest()
                Path(nom).replace(dossier / f"{table}.jsonl")
            finally:
                Path(nom).unlink(missing_ok=True)
        manifeste = {
            "schema": self.connexion.execute("PRAGMA user_version").fetchone()[0],
            "tables": list(TABLES),
            "sha256": empreintes,
        }
        fd, nom = tempfile.mkstemp(prefix=".manifest-", dir=dossier)
        with os.fdopen(fd, "w") as flux:
            json.dump(manifeste, flux, ensure_ascii=False, sort_keys=True)
            flux.write("\n")
        Path(nom).replace(dossier / "manifest.json")
