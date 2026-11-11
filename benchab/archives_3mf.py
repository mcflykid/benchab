"""Lecture unique, sûre et bornée des membres d’une archive 3MF."""

from __future__ import annotations

import contextlib
import io
import os
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree


class _FluxBorne(io.RawIOBase):
    """Relais séquentiel borné ; ferme le membre et vérifie sa taille à EOF."""

    TAILLE_BLOC = 64 * 1024

    def __init__(self, flux, info):
        """Relie un membre ZIP à son entrée d’index vérifiée ; tailles en octets."""
        super().__init__()
        self._flux = flux
        self._info = info
        self._lus = 0
        self._ferme = False

    def read(self, taille=-1):
        """Lit au plus 64 Kio ; contrôle taille et CRC quand la fin est atteinte."""
        if self._ferme:
            raise ValueError("lecture d’un flux fermé")
        demande = self.TAILLE_BLOC if taille is None or taille < 0 else min(taille, self.TAILLE_BLOC)
        if demande == 0:
            return b""
        donnees = self._flux.read(demande)
        self._lus += len(donnees)
        if self._lus > self._info.file_size:
            raise ValueError(f"taille réelle dépassée : {self._info.filename}")
        if not donnees and self._lus != self._info.file_size:
            raise ValueError(f"taille réelle incohérente : {self._info.filename}")
        return donnees

    def readable(self):
        """Indique que le relais accepte la lecture séquentielle."""
        return not self._ferme

    def seekable(self):
        """Déclare le flux non seekable pour interdire la relecture non comptée."""
        return False

    def readinto(self, tampon):
        """Remplit un tampon limité au bloc maximal du lecteur."""
        donnees = self.read(len(tampon))
        tampon[:len(donnees)] = donnees
        return len(donnees)

    def close(self):
        """Ferme le membre ZIP sous-jacent une seule fois."""
        if not self._ferme:
            self._ferme = True
            self._flux.close()
            super().close()

    def __enter__(self):
        """Renvoie ce flux borné pour un bloc with."""
        return self

    def __exit__(self, *_):
        """Ferme le flux même si l’appelant sort avant EOF."""
        self.close()


class Archive3MF:
    """Lecteur contextuel d’un 3MF ; tailles en octets, erreurs en ValueError.

    Le ZIP est ouvert une fois par contexte. Aucun membre n’est extrait sur disque
    et aucune donnée du projet ni aucun réglage ne sont modifiés.
    """

    MAX_MEMBRES = 2048
    MAX_MEMBRE_OCTETS = 512 * 1024 * 1024
    MAX_TOTAL_OCTETS = 2 * 1024 * 1024 * 1024
    MAX_XML_OCTETS = 32 * 1024 * 1024

    def __init__(self, chemin, *, taille_max_membre=MAX_MEMBRE_OCTETS,
                 taille_max_totale=MAX_TOTAL_OCTETS, nombre_max_membres=MAX_MEMBRES,
                 taille_max_xml=MAX_XML_OCTETS):
        """Prépare l’ouverture ; budgets en octets et nombre de membres, sans I/O."""
        self.chemin = Path(chemin)
        self.taille_max_membre = taille_max_membre
        self.taille_max_totale = taille_max_totale
        self.nombre_max_membres = nombre_max_membres
        self.taille_max_xml = taille_max_xml
        self._archive = None
        self._fichier = None
        self._infos = {}
        self._identite = None
        self._octets_lus = 0
        self._flux_ouverts = []

    def __enter__(self):
        """Ouvre l’archive une fois et refuse son index dangereux ou hors budget."""
        from benchab.chemins import sans_lien

        # O_NOFOLLOW ne couvre que le dernier composant ; contrôler aussi les parents (#17).
        self.chemin = sans_lien(self.chemin, strict=True)
        for nom, valeur in (
            ("membre", self.taille_max_membre),
            ("total", self.taille_max_totale),
            ("nombre", self.nombre_max_membres),
            ("XML", self.taille_max_xml),
        ):
            if isinstance(valeur, bool) or not isinstance(valeur, int) or valeur <= 0:
                raise ValueError(f"budget {nom} invalide : entier strictement positif requis")
        drapeaux = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        descripteur = os.open(self.chemin, drapeaux)
        self._fichier = os.fdopen(descripteur, "rb")
        try:
            if not stat.S_ISREG(os.fstat(self._fichier.fileno()).st_mode):
                raise ValueError("la source doit être un fichier régulier")
            self._identite = self._stat_identite()
            self._archive = zipfile.ZipFile(self._fichier, "r")
            infos = self._archive.infolist()
            if len(infos) > self.nombre_max_membres:
                raise ValueError("budget de nombre de membres dépassé")
            total = 0
            fichiers, dossiers = set(), set()
            for info in infos:
                self._verifier_nom(info.filename)
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise ValueError(f"lien symbolique interdit : {info.filename}")
                type_fichier = stat.S_IFMT(mode)
                if type_fichier not in (0, stat.S_IFREG, stat.S_IFDIR):
                    raise ValueError(f"type de membre spécial interdit : {info.filename}")
                if info.filename in self._infos:
                    raise ValueError(f"membre dupliqué : {info.filename}")
                self._verifier_hierarchie(info.filename, fichiers, dossiers)
                if info.file_size > self.taille_max_membre:
                    raise ValueError(f"budget par membre dépassé : {info.filename}")
                total += info.file_size
                if total > self.taille_max_totale:
                    raise ValueError("budget total déclaré dépassé")
                self._infos[info.filename] = info
        except BaseException:
            if self._archive is not None:
                self._archive.close()
            self._archive = None
            self._fichier.close()
            self._fichier = None
            raise
        return self

    def __exit__(self, *_):
        """Ferme le ZIP et refuse une mutation du fichier pendant le contexte."""
        if self._archive is not None:
            self._archive.close()
            self._archive = None
        identite_apres = self._stat_identite()
        if self._fichier is not None:
            self._fichier.close()
            self._fichier = None
        for flux in self._flux_ouverts:
            flux.close()
        self._flux_ouverts.clear()
        if identite_apres != self._identite:
            raise ValueError("archive 3MF modifiée pendant sa lecture")

    def _stat_identite(self):
        """Identité locale (périphérique, inode, taille et dates) de l’archive."""
        info = os.fstat(self._fichier.fileno())
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns

    @staticmethod
    def _verifier_hierarchie(nom, fichiers, dossiers):
        """Refuse les collisions fichier/répertoire, quel que soit l'ordre des membres."""
        chemin = PurePosixPath(nom.rstrip("/"))
        parents = {p.as_posix() for p in chemin.parents if p != PurePosixPath(".")}
        cle = chemin.as_posix()
        # Un lecteur ne doit pas choisir entre un fichier et un dossier (#64).
        if parents & fichiers or cle in fichiers or (not nom.endswith("/") and cle in dossiers):
            raise ValueError(f"hiérarchie ZIP ambiguë : {nom}")
        dossiers.update(parents)
        (dossiers if nom.endswith("/") else fichiers).add(cle)

    @staticmethod
    def _verifier_nom(nom):
        """Refuse noms ZIP absolus, traversants, ambigus ou avec séparateur Windows."""
        if not isinstance(nom, str) or not nom or "\\" in nom or "\x00" in nom:
            raise ValueError(f"membre ZIP invalide : {nom!r}")
        # Les entrées de répertoire terminent par `/` dans les ZIP usuels.
        nom_sans_fin = nom[:-1] if nom.endswith("/") else nom
        chemin = PurePosixPath(nom_sans_fin)
        if chemin.is_absolute() or any(part in ("", ".", "..") for part in nom_sans_fin.split("/")):
            raise ValueError(f"membre ZIP non sûr : {nom!r}")
        if re.match(r"^[A-Za-z]:", nom):
            raise ValueError(f"membre ZIP absolu : {nom!r}")

    def lire(self, membre, *, limite_octets=None):
        """Lit un membre en octets, borné au déclaré et au maximum configuré."""
        if self._archive is None:
            raise RuntimeError("ouvrir Archive3MF avec un bloc with")
        try:
            info = self._infos[membre]
        except KeyError as erreur:
            raise KeyError(membre) from erreur
        maximum = self.taille_max_membre if limite_octets is None else min(self.taille_max_membre, limite_octets)
        plafond = min(info.file_size, maximum)
        if info.file_size > maximum:
            raise ValueError(f"budget par lecture dépassé : {membre}")
        if self._octets_lus + info.file_size > self.taille_max_totale:
            raise ValueError("budget cumulatif de lecture dépassé")
        with self._archive.open(info) as flux:
            donnees = flux.read(plafond + 1)
        if len(donnees) != info.file_size or len(donnees) > plafond:
            raise ValueError(f"taille réelle incohérente ou budget dépassé : {membre}")
        self._octets_lus += len(donnees)
        return donnees

    @contextlib.contextmanager
    def ouvrir(self, membre):
        """Expose un flux mémoire d’un membre après lecture bornée complète."""
        flux = io.BytesIO(self.lire(membre))
        self._flux_ouverts.append(flux)
        try:
            yield flux
        finally:
            flux.close()
            if flux in self._flux_ouverts:
                self._flux_ouverts.remove(flux)

    @contextlib.contextmanager
    def ouvrir_flux(self, membre):
        """Ouvre un flux non seekable limité à 64 Kio par lecture.

        Le membre déclaré est réservé au budget cumulatif avant décompression :
        une lecture partielle ne permet donc pas de relire gratuitement le reste.
        CRC et taille sont contrôlés par zipfile lorsque le consommateur atteint EOF.
        """
        if self._archive is None:
            raise RuntimeError("ouvrir Archive3MF avec un bloc with")
        try:
            info = self._infos[membre]
        except KeyError as erreur:
            raise KeyError(membre) from erreur
        if info.file_size > self.taille_max_membre:
            raise ValueError(f"budget par membre dépassé : {membre}")
        if self._octets_lus + info.file_size > self.taille_max_totale:
            raise ValueError("budget cumulatif de lecture dépassé")
        self._octets_lus += info.file_size
        flux = io.BufferedReader(_FluxBorne(self._archive.open(info), info), buffer_size=_FluxBorne.TAILLE_BLOC)
        self._flux_ouverts.append(flux)
        try:
            yield flux
        finally:
            flux.close()
            if flux in self._flux_ouverts:
                self._flux_ouverts.remove(flux)

    def xml(self, membre):
        """Analyse XML après refus des déclarations DTD/ENTITY, tous encodages usuels."""
        if self._infos[membre].file_size > self.taille_max_xml:
            raise ValueError(f"budget XML dépassé : {membre}")
        donnees = self.lire(membre, limite_octets=self.taille_max_xml)
        # UTF-16/32 sans BOM intercale des NUL ; les retirer seulement pour le scan.
        texte = donnees.decode("latin-1").replace("\x00", "")
        if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", texte, re.IGNORECASE):
            raise ValueError("DOCTYPE et ENTITY sont interdits dans le XML 3MF")
        try:
            return ElementTree.fromstring(donnees)
        except ElementTree.ParseError:
            raise

    def reglages(self, membre="Metadata/project_settings.config"):
        """Décode les réglages via leur dictionnaire typé, sans normalisation nouvelle."""
        from benchab import reglages as schema_reglages

        if self._infos[membre].file_size > self.taille_max_xml:
            raise ValueError(f"budget réglages JSON dépassé : {membre}")
        return schema_reglages.decoder(self.lire(membre, limite_octets=self.taille_max_xml))

    def noms(self):
        """Renvoie les chemins membres vérifiés dans leur ordre ZIP d’origine."""
        if self._archive is None:
            raise RuntimeError("ouvrir Archive3MF avec un bloc with")
        return tuple(self._infos)

    def verifier(self):
        """Parcourt les membres par blocs de 1 Mio pour détecter CRC et flux invalides."""
        if self._archive is None:
            raise RuntimeError("ouvrir Archive3MF avec un bloc with")
        for info in self._infos.values():
            if self._octets_lus + info.file_size > self.taille_max_totale:
                raise ValueError("budget cumulatif de lecture dépassé")
            total = 0
            with self._archive.open(info) as flux:
                while bloc := flux.read(1024 * 1024):
                    total += len(bloc)
                    if total > min(info.file_size, self.taille_max_membre):
                        raise ValueError(f"budget réel dépassé : {info.filename}")
            if total != info.file_size:
                raise ValueError(f"taille réelle incohérente : {info.filename}")
            self._octets_lus += total
