"""Contrat de nommage et de racines publiques du dépôt BENCHAB."""

import os
import re
import tomllib
import unicodedata
from pathlib import Path, PurePosixPath

from benchab.qualite.empreintes import sha256_fichier
from benchab.qualite.frontiere import EXCLUS

_KEBAB = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_VERSION = re.compile(r"[0-9]+(?:\.[0-9]+){1,3}")
_MODULE = re.compile(r"(?:[a-z][a-z0-9_]*|_[a-z0-9][a-z0-9_]*)\.py")
_FICHIER = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*(?:\.[a-z0-9]+)*")
_SHA = re.compile(r"[a-f0-9]{64}")
_EXCLUS_CASEFOLD = {unicodedata.normalize("NFC", nom).casefold() for nom in EXCLUS}


def _est_exclus(nom: str) -> bool:
    """Compare exclusions macOS en NFC/casefold avant toute résolution ou lecture de chemin."""
    return unicodedata.normalize("NFC", nom).casefold() in _EXCLUS_CASEFOLD


def _entrees_systeme(racine: Path, erreurs: list[str]) -> list[tuple[str, bool]]:
    """Énumère les chemins publics sans suivre les liens ni entrer dans les exclusions communes."""
    resultat = []

    def echec_parcours(_erreur):
        erreurs.append("Parcours de structure incomplet : répertoire public illisible")

    for dossier, repertoires, fichiers in os.walk(racine, followlinks=False, onerror=echec_parcours):
        parent = Path(dossier)
        gardes = []
        for nom in repertoires:
            chemin = parent / nom
            if _est_exclus(nom) or nom.endswith(".egg-info") or nom == ".DS_Store" or nom.startswith("._"):
                continue
            resultat.append((chemin.relative_to(racine).as_posix(), True))
            if not chemin.is_symlink():
                gardes.append(nom)
        repertoires[:] = gardes
        for nom in fichiers:
            if nom == ".DS_Store" or nom.startswith("._"):
                continue
            resultat.append(((parent / nom).relative_to(racine).as_posix(), False))
    return resultat


def _chemin_contrat(racine: Path, valeur: object) -> Path | None:
    """Résout une preuve relative au dépôt ; refuse liens, absolus et traversal."""
    if not isinstance(valeur, str) or not valeur:
        return None
    relatif = Path(valeur)
    if (
        relatif.is_absolute()
        or ".." in relatif.parts
        or "\\" in valeur
        or any(_est_exclus(part) for part in relatif.parts)
    ):
        return None
    cible = racine / relatif
    try:
        for element in (cible, *cible.parents):
            if element == racine:
                break
            if element.is_symlink():
                return None
        resolu = cible.resolve(strict=True)
        return resolu if resolu.is_relative_to(racine) else None
    except OSError:
        return None


def _valider_preuve(racine: Path, declaration: dict, *, prefixe: str) -> list[str]:
    """Vérifie preuve SHA et consommateur d'une exception/ressource de nommage."""
    erreurs = []
    fichier = _chemin_contrat(racine, declaration.get("preuve_fichier"))
    sha = declaration.get("preuve_sha256")
    if fichier is None or not fichier.is_file() or not isinstance(sha, str) or not _SHA.fullmatch(sha):
        return [f"{prefixe} : preuve de nommage absente ou invalide"]
    try:
        if sha256_fichier(fichier) != sha:
            erreurs.append(f"{prefixe} : empreinte de preuve de nommage différente")
    except (OSError, ValueError):
        erreurs.append(f"{prefixe} : preuve de nommage NON VÉRIFIÉE")
    if declaration.get("consommateur") is not None:
        consommateur = _chemin_contrat(racine, declaration.get("consommateur"))
        if consommateur is None or not consommateur.is_file():
            erreurs.append(f"{prefixe} : consommateur de ressource absent")
    return erreurs


def _charger_contrat(racine: Path, contrat: str | Path | None) -> tuple[dict, list[str]]:
    """Charge le contrat TOML explicite ; une absence ou un format inconnu est FAIL."""
    chemin_brut = Path(contrat) if contrat is not None else Path("config/architecture.toml")
    if chemin_brut.is_absolute():
        try:
            chemin_brut = chemin_brut.relative_to(racine)
        except ValueError:
            return {}, ["Contrat de structure hors dépôt"]
    chemin = _chemin_contrat(racine, chemin_brut.as_posix())
    try:
        if chemin is None or chemin.is_symlink() or not chemin.is_file():
            raise ValueError("contrat hors dépôt, privé ou lié")
        donnees = tomllib.loads(chemin.read_text(encoding="utf-8"))
        if donnees.get("schema") != 1:
            raise ValueError("schéma de contrat inconnu")
        return donnees, []
    except (OSError, UnicodeError, ValueError, tomllib.TOMLDecodeError) as erreur:
        return {}, [f"Contrat de structure absent ou illisible : {type(erreur).__name__}"]


def _nom_conforme(
    relatif: str,
    dossier: bool,
    standards: set[str],
    dossiers_standards: set[str],
    packages: set[str],
) -> bool:
    """Applique kebab-case aux chemins ordinaires et snake_case aux modules Python."""
    nom = PurePosixPath(relatif).name
    if nom in standards or dossier and nom in dossiers_standards:
        return True
    if dossier and relatif in packages:
        return bool(re.fullmatch(r"(?:[a-z][a-z0-9_]*|_[a-z0-9][a-z0-9_]*)", nom))
    if dossier:
        return bool(_KEBAB.fullmatch(nom) or _VERSION.fullmatch(nom))
    if nom.endswith(".py"):
        return bool(_MODULE.fullmatch(nom) or nom in {"__init__.py", "__main__.py"})
    return bool(_FICHIER.fullmatch(nom))


def verifier(racine, *, contrat=None, entrees=None):
    """Vérifie le nommage public, les collisions NFC/casefold et racines interdites ; renvoie []/erreurs FAIL.

    Entrées : racine et contrat TOML, unités sans objet ; sortie : erreurs publiques, sans données privées.
    Les exceptions exigent motif/raison/contrainte/durée et SHA de preuve ; aucun fichier n'est modifié.
    """
    racine_brute = Path(racine).expanduser().absolute()
    try:
        racine = racine_brute.resolve(strict=True)
        if racine_brute.is_symlink() or not racine.is_dir():
            raise ValueError("racine non ordinaire")
    except (OSError, ValueError):
        return ["Racine de structure absente ou liée"]
    config, erreurs = _charger_contrat(racine, contrat)
    if erreurs:
        return erreurs
    cles_noms = ("noms_standards", "repertoires_standards", "racines_interdites")
    if any(
        not isinstance(config.get(cle, []), list)
        or any(not isinstance(valeur, str) for valeur in config.get(cle, []))
        for cle in cles_noms
    ):
        return ["Contrat de structure mal formé : listes de noms invalides"]
    if not all(isinstance(config.get(cle, []), list) for cle in ("exceptions", "ressources")):
        return ["Contrat de structure mal formé : exceptions ou ressources invalides"]
    standards = set(config.get("noms_standards", []))
    dossiers_standards = set(config.get("repertoires_standards", []))
    interdits = set(config.get("racines_interdites", []))
    exceptions, ressources = {}, []
    for declaration in config.get("exceptions", []):
        champs = ("chemin", "regle", "raison", "contrainte", "duree", "preuve_fichier", "preuve_sha256")
        if (
            not isinstance(declaration, dict)
            or any(not isinstance(declaration.get(cle), str) or not declaration[cle].strip() for cle in champs)
        ):
            erreurs.append("Exception de nommage incomplète")
            continue
        chemin_exception = declaration["chemin"]
        cible_exception = _chemin_contrat(racine, chemin_exception)
        if cible_exception is None or not cible_exception.is_file():
            erreurs.append("Exception de nommage hors racine publique ou sans cible")
            continue
        if chemin_exception in exceptions:
            erreurs.append("Exception de nommage dupliquée")
            continue
        exceptions[chemin_exception] = declaration
        erreurs.extend(_valider_preuve(racine, declaration, prefixe=chemin_exception))
    for declaration in config.get("ressources", []):
        champs = (
            "racine", "regle", "raison", "contrainte", "duree", "preuve_fichier", "preuve_sha256",
            "consommateur", "source_fichier", "source_sha256",
        )
        if not isinstance(declaration, dict) or any(not declaration.get(cle) for cle in champs):
            erreurs.append("Contrat de ressource incomplet")
            continue
        if any(not isinstance(declaration.get(cle), str) for cle in champs):
            erreurs.append("Contrat de ressource incomplet")
            continue
        racine_ressource = declaration["racine"].rstrip("/")
        chemin_ressource = _chemin_contrat(racine, racine_ressource)
        if chemin_ressource is None or not chemin_ressource.is_dir():
            erreurs.append(f"Ressource déclarée absente : {racine_ressource}")
        erreurs.extend(_valider_preuve(racine, declaration, prefixe=racine_ressource))
        source = _chemin_contrat(racine, declaration.get("source_fichier"))
        source_sha = declaration.get("source_sha256")
        if source is None or not source.is_file() or not isinstance(source_sha, str) or not _SHA.fullmatch(source_sha):
            erreurs.append(f"{racine_ressource} : provenance tierce absente ou invalide")
        else:
            try:
                if sha256_fichier(source) != source_sha:
                    erreurs.append(f"{racine_ressource} : empreinte de provenance différente")
            except (OSError, ValueError):
                erreurs.append(f"{racine_ressource} : provenance NON VÉRIFIÉE")
        ressources.append((racine_ressource, declaration))

    trouvees = list(_entrees_systeme(racine, erreurs) if entrees is None else entrees)
    # Les tests injectent deux chemins impossibles sur certains systèmes ; les exclusions ne sont
    # tout de même jamais inventoriées (docs/guides/developpement.md, frontière dépôt/atelier).
    propres = []
    for entree in trouvees:
        try:
            relatif, est_dossier = entree
            chemin_rel = PurePosixPath(relatif)
        except (TypeError, ValueError):
            erreurs.append("Entrée de structure invalide")
            continue
        if (
            not isinstance(relatif, str) or not relatif or "\\" in relatif
            or chemin_rel.is_absolute() or ".." in chemin_rel.parts
        ):
            erreurs.append("Chemin de structure hors racine")
            continue
        if any(_est_exclus(part) or part.endswith(".egg-info") for part in chemin_rel.parts):
            continue
        propres.append((chemin_rel.as_posix(), bool(est_dossier)))
    chemins = [chemin for chemin, _ in propres]
    if len(chemins) != len(set(chemins)):
        erreurs.append("Entrées de structure dupliquées")
    present = set(chemins)
    packages = {chemin.rsplit("/", 1)[0] for chemin in present if chemin.endswith("/__init__.py")}
    vus = {}
    for relatif, est_dossier in propres:
        canonique = unicodedata.normalize("NFC", relatif).casefold()
        precedent = vus.get(canonique)
        if precedent is not None and precedent != relatif:
            erreurs.append(f"Collision macOS : {relatif} / {precedent}")
        vus[canonique] = relatif
        premier = PurePosixPath(relatif).parts[0]
        if premier.casefold() in {nom.casefold() for nom in interdits}:
            erreurs.append(f"Racine publique interdite : {premier}")
            continue
        # Les collisions restent contrôlées avant la dérogation de nom d'une ressource tierce.
        zone_ressource = any(relatif == base or relatif.startswith(base + "/") for base, _ in ressources)
        if relatif in exceptions or zone_ressource:
            continue
        if not _nom_conforme(relatif, est_dossier, standards, dossiers_standards, packages):
            genre = "Dossier" if est_dossier else "Fichier"
            erreurs.append(f"{genre} mal nommé : {relatif}")
    for chemin in exceptions:
        if chemin not in present:
            erreurs.append(f"Exception sans cible : {chemin}")
    return erreurs
