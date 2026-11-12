"""Actualiser uniquement un rapport courant reconnu, jamais une entrée ni une preuve figée."""

import json
import os
import re
import stat
from pathlib import Path

from benchab.projets.protections import protections
from benchab.qualite.ecritures import PublicationNeuve, _sans_lien, modifier_json, valider_chemins
from benchab.qualite.empreintes import sha256_fichier as sha


def _lire(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > 16 * 1024 * 1024:
            raise ValueError("Rapport non régulier ou volumineux")
        raw = stream.read(16 * 1024 * 1024 + 1)
        after = os.fstat(stream.fileno())
    if len(raw) > 16 * 1024 * 1024 or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise ValueError("Rapport modifié pendant lecture")
    return json.loads(raw)


def _protege(path):
    for root in path.parents:
        if any((root / n).is_file() for n in ("qualite.json", "LIVRABLES.json", "VERROU-REIMPRESSION.json")):
            rel = path.relative_to(root)
            if len(rel.parts) > 2 and rel.parts[0] == "preparations" and re.fullmatch(r"P[0-9]+", rel.parts[1]):
                raise ValueError("Préparation figée : nouvelle version requise")
            verrou = root / "VERROU-REIMPRESSION.json"
            if verrou.is_file():
                info = _lire(verrou)
                if info.get("etat", "actif") == "actif" and any(
                    rel.is_relative_to(p) for p in info.get("repertoires_figes", [])
                ):
                    raise ValueError("Répertoire figé : nouvelle version requise")
            if rel.as_posix() in protections(root):
                raise ValueError("Rapport protégé : nouvelle version requise")


def _reconnu(data, outil, source, path):
    if not isinstance(data, dict):
        return False
    meta = data.get("_rapport")
    if isinstance(meta, dict):
        return meta.get("schema") == 1 and meta.get("outil") == outil and meta.get("source") == str(source)
    contrats = {
        "controle_envoi": ("fichier", {"resultats", "zones_locales", "conforme_numerique", "statut"}),
        "plaques_interface": ("fichier", {"plaques", "conforme"}),
        "disposition_reelle": ("source", {"plaques", "conforme"}),
    }
    contrat = contrats.get(outil)
    if contrat is None:
        # Seuls ces formats historiques sont reconnus sans provenance explicite.
        return False
    champ, requis = contrat
    nom = data.get(champ)
    if not requis <= set(data) or not isinstance(nom, str):
        return False
    if Path(nom).resolve() == source:
        return True
    # Anciens contrôles de placement : seul le nom était conservé. Le contexte
    # doit aussi être le même projet, ou son sous-dossier de rapports.
    if nom != source.name or outil == "controle_envoi":
        return False
    if path.parent == source.parent or path.parent.parent == source.parent:
        return True
    return any((root / "qualite.json").is_file() and path.is_relative_to(root) for root in source.parents)


class RapportCourant:
    """Publie le rapport courant d’un outil et d’une source identifiés ; protège les preuves figées."""

    def __init__(self, path, entrees, outil, *, neuf=False):
        """Épingle les entrées et refuse les collisions ou une destination existante si neuf est demandé."""
        self.path = _sans_lien(path)
        self.entrees = [_sans_lien(p) for p in entrees]
        self.outil = outil
        self.source = self.entrees[0]
        valider_chemins(self.entrees, [])
        if self.path.resolve() in {p.resolve() for p in self.entrees}:
            raise ValueError("Collision entrée/rapport : écrasement refusé")
        _protege(self.path)
        existant = self.path.exists()
        if neuf and existant:
            raise FileExistsError("Sortie existante ; aucune version précédente écrasée")
        self.ancien = _lire(self.path) if existant else None
        self.sha_ancien = sha(self.path) if existant else None
        if existant and not _reconnu(self.ancien, outil, self.source, self.path):
            raise ValueError("Destination existante non reconnue comme rapport de cet outil et de cette source")
        self.empreintes = {p: sha(p) if p.is_file() else None for p in self.entrees}

    def publier(self, data):
        """Publie le rapport et sa provenance sans changer les entrées ; lève si une empreinte diverge."""
        if any((sha(p) if p.is_file() else None) != h for p, h in self.empreintes.items()):
            raise ValueError("Entrée modifiée pendant le contrôle")
        _protege(self.path)
        data = {
            **data,
            "_rapport": {
                "schema": 1,
                "outil": self.outil,
                "source": str(self.source),
                "source_sha256": self.empreintes[self.source],
            },
        }
        if self.ancien is None:
            with PublicationNeuve(self.entrees, [self.path]) as publication:
                publication.temporaire(self.path).write_text(
                    json.dumps(data, ensure_ascii=False, indent=1, allow_nan=False), encoding="utf-8"
                )
                publication.publier()
        else:

            def remplacer(old):
                _protege(self.path)
                if old != self.ancien or sha(self.path) != self.sha_ancien:
                    raise ValueError("Rapport modifié par une autre exécution")
                old.clear()
                old.update(data)

            modifier_json(self.path, remplacer)
