"""
Lecture bornée des membres G-code d'un paquet 3MF tranché.
"""

from __future__ import annotations

import hashlib
import inspect
import io
import json
import re
import stat
from collections import OrderedDict
from contextlib import contextmanager, nullcontext
from pathlib import Path
from threading import RLock

from benchab.archives_3mf import Archive3MF
from benchab.chemins import sans_lien
from benchab.qualite.empreintes import sha256_fichier

_CACHE_MAX_OCTETS = 96 * 1024 * 1024
_CACHE_MAX_ENTREES = 128
_CACHE: OrderedDict[tuple[str, str], bytes] = OrderedDict()
_CACHE_OCTETS = 0
_HASH_CACHE: dict[tuple[int, int, int, int, int], str] = {}
_ANALYSE_CACHE: OrderedDict[tuple[str, str, str, str, str], bytes] = OrderedDict()
_ANALYSE_OCTETS = 0
_VERROU_CACHE = RLock()


def _recuperer(cache, cle):
    """Lit et touche une entrée LRU atomiquement ; octets ou None, aucune lecture de fichier."""
    with _VERROU_CACHE:
        valeur = cache.get(cle)
        if valeur is not None:
            cache.move_to_end(cle)
        return valeur


def _memoriser(cle, valeur, *, analyse=False):
    """Réserve les octets d’une entrée une seule fois ; budget LRU conservé, aucune E/S sous verrou."""
    global _CACHE_OCTETS, _ANALYSE_OCTETS

    if len(valeur) > _CACHE_MAX_OCTETS:
        return
    with _VERROU_CACHE:
        cache = _ANALYSE_CACHE if analyse else _CACHE
        compte = _ANALYSE_OCTETS if analyse else _CACHE_OCTETS
        # Deux calculs de la même clé peuvent finir ensemble : remplacer n’ajoute
        # jamais une deuxième réservation au budget commun (#13).
        precedent = cache.pop(cle, None)
        if precedent is not None:
            compte -= len(precedent)
        while cache and (len(cache) >= _CACHE_MAX_ENTREES or compte + len(valeur) > _CACHE_MAX_OCTETS):
            _, supprime = cache.popitem(last=False)
            compte -= len(supprime)
        cache[cle] = valeur
        compte += len(valeur)
        if analyse:
            _ANALYSE_OCTETS = compte
        else:
            _CACHE_OCTETS = compte


def _cle_stat(chemin: Path) -> tuple[int, int, int, int, int]:
    info = chemin.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("Empreinte 3MF : fichier régulier requis")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def empreinte_3mf(chemin: str | Path) -> str:
    """
    Retourne le SHA-256 du 3MF complet, avec invalidation par stat et cache concurrent protégé.
    Chemin sans unité ; lecture seule, FAIL sur lien, fichier spécial ou mutation.
    """
    # Un résultat déjà mémorisé ne qualifie jamais un autre chemin lié (#122).
    chemin = sans_lien(chemin, strict=True)
    signature = _cle_stat(chemin)
    with _VERROU_CACHE:
        empreinte = _HASH_CACHE.get(signature)
    if empreinte is not None:
        if _cle_stat(chemin) != signature:
            raise ValueError("3MF modifié pendant la vérification de son empreinte")
        return empreinte
    empreinte = sha256_fichier(chemin)
    if _cle_stat(chemin) != signature:
        raise ValueError("3MF modifié pendant le calcul de son empreinte")
    with _VERROU_CACHE:
        _HASH_CACHE[signature] = empreinte
        if len(_HASH_CACHE) > 256:
            _HASH_CACHE.pop(next(iter(_HASH_CACHE)))
    return empreinte


def lire_membre_3mf(chemin: str | Path, membre: str, *, archive: Archive3MF | None = None) -> bytes:
    """Lit un membre en octets, avec cache par SHA et contexte 3MF facultatif.

    Le contexte doit désigner la source exacte, même si le membre est en cache.
    La lecture complète vérifie le CRC et ne modifie aucune source.
    """
    chemin = Path(chemin)
    if archive is not None:
        archive.verifier_source(chemin)
    cle = (empreinte_3mf(chemin), membre)
    def verifier():
        # Le descripteur et le SHA du chemin doivent encore désigner la même archive,
        # même sur cache chaud : le fichier peut être remplacé pendant le calcul (#162).
        if empreinte_3mf(chemin) != cle[0]:
            raise ValueError("3MF modifié pendant la lecture du membre")
        if archive is not None:
            archive.verifier_source(chemin)

    valeur = _recuperer(_CACHE, cle)
    if valeur is not None:
        verifier()
        return valeur

    with (Archive3MF(chemin) if archive is None else nullcontext(archive)) as contexte:
        valeur = contexte.lire(membre)
        verifier()

    _memoriser(cle, valeur)
    return valeur


def membres_gcode_3mf(chemin: str | Path) -> tuple[str, ...]:
    """Liste les membres G-code de plaque dans un ordre stable."""
    with Archive3MF(chemin) as archive:
        membres = archive.noms()
    return tuple(sorted(nom for nom in membres if re.fullmatch(r"Metadata/plate_\d+\.gcode", nom)))


def plaques_gcode_archive(archive):
    """Renvoie (numéro de plaque, membre) en ordre numérique depuis un contexte 3MF ouvert.

    Les numéros sont sans unité ; lève sur ambiguïté entre deux graphies sans lire ni modifier le G-code.
    """
    plaques = sorted(
        (int(match[1]), nom)
        for nom in archive.noms()
        if (match := re.fullmatch(r"Metadata/plate_(\d+)\.gcode", nom))
    )
    if len({numero for numero, _ in plaques}) != len(plaques):
        raise ValueError("numéro de plaque dupliqué dans l'archive")
    return plaques


def lire_texte_gcode_3mf(
    chemin: str | Path,
    membre: str,
    encodage: str = "utf-8",
    erreurs: str = "replace",
    *,
    archive: Archive3MF | None = None,
) -> str:
    """Décode le G-code d’un membre exact sans modifier les fins de ligne ni la source."""
    return lire_membre_3mf(chemin, membre, archive=archive).decode(encodage, errors=erreurs)


@contextmanager
def ouvrir_membre_3mf(chemin: str | Path, membre: str, *, archive: Archive3MF | None = None):
    """
    Ouvre un membre en flux, sans charger les gros G-code en mémoire ; cache concurrent protégé.
    Flux d’octets en sortie ; source inchangée, FAIL sur mutation pendant la lecture.
    Un contexte fourni reste ouvert par l'appelant et doit correspondre à la source exacte.
    """
    if archive is not None:
        archive.verifier_source(chemin)
    empreinte_initiale = empreinte_3mf(chemin)

    def verifier():
        """Revalide la source avant de rendre les résultats, y compris sur un accès au cache."""
        if archive is not None:
            archive.verifier_source(chemin)
        if empreinte_3mf(chemin) != empreinte_initiale:
            raise ValueError("3MF modifié pendant la lecture du membre")

    donnees = _recuperer(_CACHE, (empreinte_initiale, membre))
    if donnees is not None:
        flux = io.BytesIO(donnees)
        try:
            yield flux
        finally:
            flux.close()
            verifier()
        return
    try:
        with (Archive3MF(chemin) if archive is None else nullcontext(archive)) as contexte:
            with contexte.ouvrir_flux(membre) as flux:
                yield flux
    finally:
        verifier()


def iter_lignes_gcode_3mf(
    chemin: str | Path, membre: str, encodage: str = "utf-8", *, archive: Archive3MF | None = None,
):
    """
    Itère sur les lignes G-code, en flux, avec le contexte existant si fourni.
    Le cache par empreinte est conservé et aucun fichier n'est modifié.
    """
    with ouvrir_membre_3mf(chemin, membre, archive=archive) as flux:
        with io.TextIOWrapper(flux, encoding=encodage, errors="replace") as texte:
            yield from texte


def analyser_membre_gcode_3mf(chemin, membre, options, analyseur, *, version="1"):
    """
    Analyse en flux et met en cache un résultat JSON borné par SHA du 3MF, avec compteur atomique en octets.

        Les résultats et les sources des analyseurs sont identifiés séparément ; les
        seuils/options font partie de la clé. Le résultat retourné est décodé à neuf,
        donc un appelant ne peut pas modifier l'objet conservé dans le cache.

    """
    options_json = json.dumps(options, sort_keys=True, separators=(",", ":"), allow_nan=False)
    try:
        source = inspect.getsource(analyseur)
    except (TypeError, OSError):
        return analyseur(iter_lignes_gcode_3mf(chemin, membre))
    version_parseur = hashlib.sha256(source.encode("utf-8")).hexdigest()
    empreinte_initiale = empreinte_3mf(chemin)
    cle = (empreinte_initiale, membre, options_json, version, version_parseur)
    valeur = _recuperer(_ANALYSE_CACHE, cle)
    if valeur is not None:
        if empreinte_3mf(chemin) != empreinte_initiale:
            raise ValueError("3MF modifié pendant la lecture du résultat d'analyse")
        return json.loads(valeur)

    resultat = analyseur(iter_lignes_gcode_3mf(chemin, membre))
    if empreinte_3mf(chemin) != empreinte_initiale:
        raise ValueError("3MF modifié pendant l'analyse du G-code")
    serialise = json.dumps(resultat, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    _memoriser(cle, serialise, analyse=True)
    return json.loads(serialise)


def vider_cache_lecture() -> None:
    """
    Vide les caches et compteurs en octets atomiquement ; ne modifie aucune archive.
    """
    global _CACHE_OCTETS
    global _ANALYSE_OCTETS
    with _VERROU_CACHE:
        _CACHE.clear()
        _CACHE_OCTETS = 0
        _ANALYSE_CACHE.clear()
        _ANALYSE_OCTETS = 0
        _HASH_CACHE.clear()
