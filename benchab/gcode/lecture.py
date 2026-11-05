"""
Lecture bornée des membres G-code d'un paquet 3MF tranché.
"""

from __future__ import annotations

import hashlib
import inspect
import io
import json
import re
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path
from zipfile import ZipFile

_CACHE_MAX_OCTETS = 96 * 1024 * 1024
_CACHE_MAX_ENTREES = 128
_CACHE: OrderedDict[tuple[str, str], bytes] = OrderedDict()
_CACHE_OCTETS = 0
_HASH_CACHE: dict[tuple[int, int, int, int, int], str] = {}
_ANALYSE_CACHE: OrderedDict[tuple[str, str, str, str, str], bytes] = OrderedDict()
_ANALYSE_OCTETS = 0


def _cle_stat(chemin: Path) -> tuple[int, int, int, int, int]:
    stat = chemin.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def empreinte_3mf(chemin: str | Path) -> str:
    """
    Retourne le SHA-256 du 3MF complet, avec invalidation par stat du fichier.
    """
    chemin = Path(chemin)
    stat = _cle_stat(chemin)
    empreinte = _HASH_CACHE.get(stat)
    if empreinte is not None:
        if _cle_stat(chemin) != stat:
            raise ValueError("3MF modifié pendant la vérification de son empreinte")
        return empreinte
    with chemin.open("rb") as fichier:
        empreinte = hashlib.file_digest(fichier, "sha256").hexdigest()
        apres = _cle_stat(chemin)
    if apres != stat:
        raise ValueError("3MF modifié pendant le calcul de son empreinte")
    _HASH_CACHE[stat] = empreinte
    if len(_HASH_CACHE) > 256:
        _HASH_CACHE.pop(next(iter(_HASH_CACHE)))
    return empreinte


def lire_membre_3mf(chemin: str | Path, membre: str) -> bytes:
    """
    Lit un membre d'archive, avec cache LRU indexé par empreinte 3MF.
    """
    global _CACHE_OCTETS

    chemin = Path(chemin)
    cle = (empreinte_3mf(chemin), membre)
    valeur = _CACHE.get(cle)
    if valeur is not None:
        if empreinte_3mf(chemin) != cle[0]:
            raise ValueError("3MF modifié pendant la lecture du membre")
        _CACHE.move_to_end(cle)
        return valeur

    with ZipFile(chemin) as archive:
        valeur = archive.read(membre)
    if empreinte_3mf(chemin) != cle[0]:
        raise ValueError("3MF modifié pendant la lecture du membre")

    if len(valeur) > _CACHE_MAX_OCTETS:
        return valeur
    while _CACHE and (len(_CACHE) >= _CACHE_MAX_ENTREES or _CACHE_OCTETS + len(valeur) > _CACHE_MAX_OCTETS):
        _, supprime = _CACHE.popitem(last=False)
        _CACHE_OCTETS -= len(supprime)
    _CACHE[cle] = valeur
    _CACHE_OCTETS += len(valeur)
    return valeur


def membres_gcode_3mf(chemin: str | Path) -> tuple[str, ...]:
    """Liste les membres G-code de plaque dans un ordre stable."""
    with ZipFile(chemin) as archive:
        membres = archive.namelist()
    return tuple(sorted(nom for nom in membres if re.fullmatch(r"Metadata/plate_\d+\.gcode", nom)))


def lire_texte_gcode_3mf(
    chemin: str | Path,
    membre: str,
    encodage: str = "utf-8",
    erreurs: str = "replace",
) -> str:
    """
    Décode un membre G-code sans altérer ses fins de ligne.
    """
    return lire_membre_3mf(chemin, membre).decode(encodage, errors=erreurs)


@contextmanager
def ouvrir_membre_3mf(chemin: str | Path, membre: str):
    """
    Ouvre un membre en flux, sans charger les gros G-code en mémoire.
    """
    empreinte_initiale = empreinte_3mf(chemin)
    donnees = _CACHE.get((empreinte_initiale, membre))
    if donnees is not None:
        flux = io.BytesIO(donnees)
        try:
            yield flux
        finally:
            flux.close()
            if empreinte_3mf(chemin) != empreinte_initiale:
                raise ValueError("3MF modifié pendant la lecture du membre")
        return
    try:
        with ZipFile(chemin) as archive:
            with archive.open(membre) as flux:
                yield flux
    finally:
        if empreinte_3mf(chemin) != empreinte_initiale:
            raise ValueError("3MF modifié pendant la lecture du membre")


def iter_lignes_gcode_3mf(chemin: str | Path, membre: str, encodage: str = "utf-8"):
    """
    Itère sur les lignes G-code, en conservant le mode flux pour les gros membres.
    """
    with ouvrir_membre_3mf(chemin, membre) as flux:
        with io.TextIOWrapper(flux, encoding=encodage, errors="replace") as texte:
            yield from texte


def analyser_membre_gcode_3mf(chemin, membre, options, analyseur, *, version="1"):
    """
    Analyse en flux et met en cache un résultat JSON borné par SHA du 3MF.

        Les résultats et les sources des analyseurs sont identifiés séparément ; les
        seuils/options font partie de la clé. Le résultat retourné est décodé à neuf,
        donc un appelant ne peut pas modifier l'objet conservé dans le cache.

    """
    global _ANALYSE_OCTETS

    options_json = json.dumps(options, sort_keys=True, separators=(",", ":"), allow_nan=False)
    try:
        source = inspect.getsource(analyseur)
    except (TypeError, OSError):
        return analyseur(iter_lignes_gcode_3mf(chemin, membre))
    version_parseur = hashlib.sha256(source.encode("utf-8")).hexdigest()
    empreinte_initiale = empreinte_3mf(chemin)
    cle = (empreinte_initiale, membre, options_json, version, version_parseur)
    valeur = _ANALYSE_CACHE.get(cle)
    if valeur is not None:
        if empreinte_3mf(chemin) != empreinte_initiale:
            raise ValueError("3MF modifié pendant la lecture du résultat d'analyse")
        _ANALYSE_CACHE.move_to_end(cle)
        return json.loads(valeur)

    resultat = analyseur(iter_lignes_gcode_3mf(chemin, membre))
    if empreinte_3mf(chemin) != empreinte_initiale:
        raise ValueError("3MF modifié pendant l'analyse du G-code")
    serialise = json.dumps(resultat, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(serialise) <= _CACHE_MAX_OCTETS:
        while _ANALYSE_CACHE and (
            len(_ANALYSE_CACHE) >= _CACHE_MAX_ENTREES or _ANALYSE_OCTETS + len(serialise) > _CACHE_MAX_OCTETS
        ):
            _, supprime = _ANALYSE_CACHE.popitem(last=False)
            _ANALYSE_OCTETS -= len(supprime)
        _ANALYSE_CACHE[cle] = serialise
        _ANALYSE_OCTETS += len(serialise)
    return json.loads(serialise)


def vider_cache_lecture() -> None:
    """
    Vide le cache pour les tests et les opérations explicitement isolées.
    """
    global _CACHE_OCTETS
    global _ANALYSE_OCTETS
    _CACHE.clear()
    _CACHE_OCTETS = 0
    _ANALYSE_CACHE.clear()
    _ANALYSE_OCTETS = 0
    _HASH_CACHE.clear()
