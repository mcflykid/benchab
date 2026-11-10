"""Diagnostic local, en lecture seule, des prérequis BENCHAB et de l'atelier."""

from __future__ import annotations

import ast
import hashlib
import os
import plistlib
import shutil
import sqlite3
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

from benchab.donnees.base import APPLICATION_ID, TABLES
from benchab.qualite.statuts import code_statut


def _statut_global(controles: list[dict[str, Any]]) -> str:
    """Agrège les verdicts ; FAIL prévaut, puis NON VÉRIFIÉ, puis WARN."""
    statuts = {controle["statut"] for controle in controles}
    if "FAIL" in statuts:
        return "FAIL"
    if "NON VÉRIFIÉ" in statuts:
        return "NON VÉRIFIÉ"
    if "WARN" in statuts:
        return "WARN"
    return "PASS"


def _controle(nom: str, statut: str, resume: str, detail: Any = None) -> dict[str, Any]:
    """Construit une ligne de rapport courte et son détail optionnel."""
    resultat = {"nom": nom, "statut": statut, "resume": resume}
    if detail is not None:
        resultat["detail"] = detail
    return resultat


def _version_locale() -> str | None:
    """Réutilise la sonde passive du moteur, puis lit deux bundles connus sans les lancer."""
    try:
        from benchab.fabrication.slicer_runtime import informations

        sonde = informations(empreinte=False)
        if sonde.get("present") and sonde.get("version"):
            return str(sonde["version"])
    except (OSError, ValueError, KeyError):
        pass
    candidats = (
        Path("/Applications/AnycubicSlicerNext.app/Contents/Info.plist"),
        Path("/Applications/Anycubic Slicer Next.app/Contents/Info.plist"),
        Path.home() / "Applications/AnycubicSlicerNext.app/Contents/Info.plist",
        Path.home() / "Applications/Anycubic Slicer Next.app/Contents/Info.plist",
    )
    for chemin in candidats:
        try:
            with chemin.open("rb") as flux:
                infos = plistlib.load(flux)
            version = infos.get("CFBundleShortVersionString") or infos.get("CFBundleVersion")
            if version:
                return str(version)
        except (OSError, ValueError, plistlib.InvalidFileException):
            continue
    return None


def _controle_sqlite(chemin: Path) -> dict[str, Any]:
    """Vérifie l'identité de la base BENCHAB et les PRAGMA en mode lecture seule."""
    if not chemin.is_file() or chemin.is_symlink():
        return _controle("sqlite", "NON VÉRIFIÉ", "Base absente ou liée.")
    try:
        uri = chemin.resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=1) as connexion:
            connexion.execute("PRAGMA query_only = ON")
            integrite = [ligne[0] for ligne in connexion.execute("PRAGMA integrity_check")]
            cles = [tuple(ligne) for ligne in connexion.execute("PRAGMA foreign_key_check")]
            application_id = connexion.execute("PRAGMA application_id").fetchone()[0]
            user_version = connexion.execute("PRAGMA user_version").fetchone()[0]
            tables = {ligne[0] for ligne in connexion.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table'"
            )}
    except (sqlite3.Error, OSError, ValueError) as erreur:
        return _controle("sqlite", "FAIL", "Base illisible en lecture seule.", str(erreur))
    identite_ok = application_id == APPLICATION_ID and user_version == 1 and set(TABLES) <= tables
    if integrite != ["ok"] or cles or not identite_ok:
        return _controle("sqlite", "FAIL", "Intégrité ou identité de base BENCHAB invalide.", {
            "integrity_check": integrite, "foreign_key_check": cles[:20],
            "application_id": application_id, "user_version": user_version,
            "tables_benchab_manquantes": sorted(set(TABLES) - tables),
        })
    return _controle("sqlite", "PASS", "Identité, schéma, intégrité et clés étrangères vérifiés.")


def _controle_seance(chemin: Path) -> dict[str, Any]:
    """Distingue séance verrouillée et séance orpheline via les requêtes passives du module séance."""
    if not chemin.is_file() or chemin.is_symlink():
        return _controle("seance", "NON VÉRIFIÉ", "Base d'atelier absente.")
    try:
        from benchab.donnees.seance import active_valide

        with sqlite3.connect(chemin.resolve().as_uri() + "?mode=ro", uri=True, timeout=1) as connexion:
            connexion.row_factory = sqlite3.Row
            lignes = connexion.execute("SELECT id FROM seances WHERE fin IS NULL").fetchall()
            active = active_valide(connexion)
        if not lignes:
            return _controle("seance", "PASS", "Aucune séance ouverte.")
        if active is not None:
            return _controle("seance", "PASS", "Séance ouverte avec verrou valide.")
        return _controle("seance", "WARN", "Séance ouverte orpheline.", {"orphelines": len(lignes)})
    except (sqlite3.Error, OSError, ValueError, IndexError) as erreur:
        return _controle("seance", "NON VÉRIFIÉ", "État de séance indisponible.", str(erreur))


def _controle_git_prive(racine: Path, fichier_prive: Path) -> dict[str, Any]:
    """Vérifie la règle Git effective sans indexer ni modifier l'index."""
    environnement = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
    try:
        resultat = subprocess.run(
            ["git", "-C", str(racine), "check-ignore", "--no-index", "--quiet", "--", str(fichier_prive)],
            check=False, capture_output=True, timeout=2, env=environnement,
        )
    except (OSError, subprocess.TimeoutExpired):
        return _controle("git_prive", "NON VÉRIFIÉ", "Checkout Git ou vérification effective indisponible.")
    if resultat.returncode == 0:
        return _controle("git_prive", "PASS", "Le fichier privé est effectivement ignoré par Git.")
    if resultat.returncode == 1:
        return _controle("git_prive", "FAIL", "Git n'ignore pas le fichier privé.")
    return _controle("git_prive", "NON VÉRIFIÉ", "Git n'a pas pu confirmer la règle effective.")


def _version_hatch(racine: Path, configuration: dict) -> str | None:
    """Lit une unique version littérale depuis le chemin Hatch ; aucun code Python exécuté."""
    from benchab.chemins import sans_lien

    try:
        relatif = configuration["tool"]["hatch"]["version"]["path"]
        if not isinstance(relatif, str) or Path(relatif).is_absolute() or ".." in Path(relatif).parts:
            return None
        source = sans_lien(racine / relatif, racine=racine, strict=True)
        if source.stat().st_size > 1024**2:
            return None
        arbre = ast.parse(source.read_text(encoding="utf-8"))
        valeurs = [n.value.value for n in arbre.body if isinstance(n, ast.Assign)
                   and any(isinstance(c, ast.Name) and c.id == "__version__" for c in n.targets)
                   and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)]
        return valeurs[0] if len(valeurs) == 1 else None
    except (OSError, ValueError, KeyError, TypeError, SyntaxError):
        return None


def _controle_uv(racine: Path) -> tuple[dict[str, Any], str | None]:
    """Lit pyproject.toml et uv.lock ; vérifie leur version et la contrainte Python sans installation."""
    pyproject, verrou = racine / "pyproject.toml", racine / "uv.lock"
    try:
        configuration = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        projet = configuration["project"]
        lock = tomllib.loads(verrou.read_text(encoding="utf-8"))
        requis = projet["requires-python"]
        version_verrou = lock.get("requires-python")
        paquets = [p for p in lock.get("package", []) if p.get("name") == projet.get("name")]
        dynamiques = "version" in projet.get("dynamic", [])
        version_projet = _version_hatch(racine, configuration) if dynamiques else projet.get("version")
        version_lock = paquets[0].get("version") if len(paquets) == 1 else None
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return _controle("uv", "FAIL", "pyproject.toml ou uv.lock absent, invalide ou incohérent."), None
    if not isinstance(requis, str) or requis != version_verrou or len(paquets) != 1:
        return _controle("uv", "FAIL", "Contrainte Python ou projet différente entre pyproject.toml et uv.lock."), None
    if dynamiques and version_projet is None:
        statut = "NON VÉRIFIÉ"
        resume = "Source de version dynamique non reconnue ; comparaison au lock NON VÉRIFIÉE."
    elif dynamiques and version_lock is None and paquets[0].get("source", {}).get("editable") == ".":
        statut, resume = "PASS", "Verrou éditable cohérent ; version dynamique lue, non figée dans uv.lock."
    elif version_projet != version_lock:
        statut = "FAIL"
        resume = "Version du projet différente entre pyproject.toml et uv.lock."
    else:
        statut, resume = "PASS", "Projet, version et contrainte Python cohérents avec uv.lock."
    return _controle("uv", statut, resume), requis


def _python_conforme(version: tuple[int, int, int], contrainte: str | None) -> bool | None:
    """Compare la version courante à la contrainte PEP 440 déclarée par le projet."""
    if contrainte is None:
        return None
    try:
        from packaging.specifiers import SpecifierSet
        from packaging.version import Version

        return Version(".".join(map(str, version))) in SpecifierSet(contrainte)
    except (ImportError, ValueError):
        return None


def _empreinter_profil(chemin: Path, attendu: str | None) -> dict[str, Any]:
    """Hache un fichier régulier non lié avec une limite de 64 Mio et compare le SHA attendu."""
    try:
        if chemin.is_symlink() or not chemin.is_file():
            return {"chemin": str(chemin), "erreur": "fichier absent, non régulier ou lien symbolique"}
        taille = chemin.stat().st_size
        if taille > 64 * 1024**2:
            return {"chemin": str(chemin), "erreur": "fichier au-delà de 64 Mio"}
        hachage = hashlib.sha256()
        with chemin.open("rb") as flux:
            for bloc in iter(lambda: flux.read(1024 * 1024), b""):
                hachage.update(bloc)
        obtenu = hachage.hexdigest()
        return {"chemin": str(chemin), "sha256": obtenu, "mtime_ns": chemin.stat().st_mtime_ns,
                "sha_concorde": obtenu == attendu.lower() if attendu else None}
    except OSError as erreur:
        return {"chemin": str(chemin), "erreur": str(erreur)}


def diagnostiquer(
    racine: Path | str,
    *,
    profils: tuple[Path | str, ...] = (),
    profils_attendus: dict[Path | str, str] | None = None,
    base: Path | str | None = None,
    python: Path | str | None = None,
    versions_testees: tuple[str, ...] | None = None,
    version_slicer: str | None = None,
    espace_libre_octets: int | None = None,
    espace_minimum_octets: int = 0,
    detail: bool = False,
) -> dict[str, Any]:
    """Diagnostique l'installation sans écriture, exécution slicer, migration, réseau ou mesure archivée.

    Entrées : racine et chemins locaux (sans unité), capacité disque en octets, versions locales.
    Sortie : rapport court PASS/WARN/FAIL/NON VÉRIFIÉ et code compatible qualite.statuts.
    Invariant : aucune création, chmod, réparation ou modification de profil/base ; aucun slicer lancé.
    """
    racine = Path(racine).expanduser().absolute()
    python = Path(python or sys.executable).expanduser().absolute()
    controles: list[dict[str, Any]] = []
    attendu_python = (racine / ".venv/bin/python").absolute()
    controle_uv, contrainte_python = _controle_uv(racine)
    controles.append(controle_uv)
    version_valide = _python_conforme(tuple(sys.version_info[:3]), contrainte_python)
    python_ok = python == attendu_python and python.is_file()
    if not python_ok or version_valide is False:
        statut_python = "FAIL"
        resume_python = "Python hors .venv ou version non conforme au projet."
    elif version_valide is None:
        statut_python = "NON VÉRIFIÉ"
        resume_python = "Contrainte Python non interprétable."
    else:
        statut_python = "PASS"
        resume_python = f"Python {sys.version_info.major}.{sys.version_info.minor} dans .venv et conforme."
    controles.append(_controle("python", statut_python, resume_python))
    verrou = racine / "uv.lock"

    version_slicer = version_slicer if version_slicer is not None else _version_locale()
    teste = tuple(versions_testees or ())
    if version_slicer is None or not teste:
        controles.append(_controle("slicer", "NON VÉRIFIÉ",
                                   f"Version rapportée : {version_slicer or 'inconnue'} ; "
                                   "version ou liste de recettes vérifiées indisponible."))
    elif version_slicer in teste:
        controles.append(_controle("slicer", "PASS", f"Version {version_slicer} présente dans la liste testée."))
    else:
        controles.append(_controle("slicer", "WARN", f"Version {version_slicer} absente de la liste testée."))
    controles.append(_controle(
        "slicer_binaire", "NON VÉRIFIÉ",
        "La correspondance de version ne certifie pas le binaire ni son SHA qualifié.",
    ))

    inventaire = []
    if not profils:
        controles.append(_controle("profils", "NON VÉRIFIÉ", "Aucun profil candidat déclaré."))
    else:
        attendus = {Path(p).expanduser().absolute(): sha for p, sha in (profils_attendus or {}).items()}
        for brut in profils:
            chemin = Path(brut).expanduser().absolute()
            inventaire.append(_empreinter_profil(chemin, attendus.get(chemin)))
        statuts_profil = [
            "FAIL" if item.get("sha_concorde") is False else
            "PASS" if item.get("sha_concorde") is True else
            "NON VÉRIFIÉ" if "sha256" in item else "WARN"
            for item in inventaire
        ]
        statut_profils = (
            "FAIL" if "FAIL" in statuts_profil else
            "NON VÉRIFIÉ" if "NON VÉRIFIÉ" in statuts_profil else
            "WARN" if "WARN" in statuts_profil else "PASS"
        )
        controles.append(_controle("profils", statut_profils,
                                   "Empreinte capturée et comparée à la référence fournie, si connue.",
                                   inventaire if detail else None))

    libre = espace_libre_octets
    if libre is None:
        try:
            libre = shutil.disk_usage(racine).free
        except OSError:
            libre = None
    if libre is None:
        controles.append(_controle("espace", "NON VÉRIFIÉ", "Espace disque indisponible."))
    elif libre < espace_minimum_octets:
        detail_espace = ({"libre_octets": libre, "minimum_octets": espace_minimum_octets}
                         if detail else None)
        controles.append(_controle("espace", "WARN", "Espace libre sous le seuil fourni.", detail_espace))
    else:
        controles.append(_controle("espace", "PASS", "Espace libre suffisant au seuil fourni."))

    base_path = Path(base).expanduser().absolute() if base is not None else racine / "prive/benchab.sqlite"
    # L'option --prive peut déplacer l'atelier : le diagnostic vérifie le fichier choisi,
    # sans confondre sa protection Git avec celle du dossier privé par défaut.
    controles.append(_controle_git_prive(racine, base_path))
    controles.append(_controle_sqlite(base_path))
    controles.append(_controle_seance(base_path))

    rapport: dict[str, Any] = {
        "statut": _statut_global(controles),
        "code": code_statut(_statut_global(controles)),
        "controles": controles,
        "lecture_seule": True,
    }
    if detail:
        rapport["detail"] = {"python": str(python), "uv_lock": str(verrou),
                             "versions_slicer_testees": list(teste), "profils": inventaire}
    return rapport


def main() -> int:
    """Point d'entrée CLI recommandé ; utilise la racine du checkout et rend le code commun."""
    from benchab.chemins import atelier_prive, racine_depot

    racine = racine_depot()
    return diagnostiquer(racine, base=atelier_prive() / "benchab.sqlite")["code"]
