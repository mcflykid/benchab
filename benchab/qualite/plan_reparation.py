"""Plan en lecture seule de régénération des vues privées déterministes."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from contextlib import closing
from pathlib import Path
from typing import Any

from benchab.chemins import atelier_prive, sans_lien
from benchab.donnees import vues
from benchab.donnees.base import APPLICATION_ID
from benchab.qualite.statuts import code_statut

MANIFESTE = "vues/manifest.json"
README = "README.md"
MAX_README_OCTETS = 2 * 1024 * 1024


def _empreinte_fichier(racine: Path, relatif: str, *, lecture=False):
    """Empreinte un fichier régulier sans suivre de lien ; lecture bornée si son texte est requis."""
    candidat = racine / relatif
    try:
        chemin = sans_lien(candidat, racine=racine)
    except ValueError:
        # Repérer un lien par lstat sans résoudre ni ouvrir sa cible.
        courant = racine
        for composant in Path(relatif).parts:
            courant = courant / composant
            if courant.is_symlink():
                return {"statut": "LIEN_A_VERIFIER", "chemin": candidat}
        return {"statut": "A_VERIFIER", "chemin": candidat}
    if chemin.is_symlink():
        return {"statut": "LIEN_A_VERIFIER", "chemin": chemin}
    try:
        fd = os.open(chemin, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        return {"statut": "ABSENTE", "chemin": chemin}
    except OSError:
        return {"statut": "A_VERIFIER", "chemin": chemin}
    try:
        with os.fdopen(fd, "rb") as flux:
            info = os.fstat(flux.fileno())
            if not stat.S_ISREG(info.st_mode):
                return {"statut": "A_VERIFIER", "chemin": chemin}
            if lecture and info.st_size > MAX_README_OCTETS:
                return {"statut": "A_VERIFIER", "chemin": chemin}
            identite_avant = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            empreinte = hashlib.file_digest(flux, "sha256").hexdigest()
            contenu = None
            if lecture:
                flux.seek(0)
                contenu = flux.read(MAX_README_OCTETS + 1)
                if len(contenu) > MAX_README_OCTETS:
                    return {"statut": "A_VERIFIER", "chemin": chemin}
            apres = os.fstat(flux.fileno())
            identite_apres = (apres.st_dev, apres.st_ino, apres.st_size, apres.st_mtime_ns, apres.st_ctime_ns)
            courant = chemin.lstat()
            identite_courante = (
                courant.st_dev, courant.st_ino, courant.st_size, courant.st_mtime_ns, courant.st_ctime_ns,
            )
            if not identite_avant == identite_apres == identite_courante:
                return {"statut": "A_VERIFIER", "chemin": chemin}
    except OSError:
        return {"statut": "A_VERIFIER", "chemin": chemin}
    return {"statut": "PRESENT", "chemin": chemin, "sha256": empreinte, "contenu": contenu}


def _section_readme(contenu: bytes, attendu: str):
    """Compare le bloc géré sans toucher au texte éditorial autour des marqueurs."""
    try:
        texte = contenu.decode("utf-8")
    except UnicodeDecodeError:
        return "A_VERIFIER", None
    if texte.count(vues.DEBUT) != 1 or texte.count(vues.FIN) != 1:
        return ("ABSENTE", None) if vues.DEBUT not in texte and vues.FIN not in texte else ("A_VERIFIER", None)
    debut = texte.index(vues.DEBUT) + len(vues.DEBUT)
    fin = texte.index(vues.FIN)
    if fin < debut or not texte[debut:fin].startswith("\n") or not texte[debut:fin].endswith("\n"):
        return "A_VERIFIER", None
    corps = texte[debut + 1:fin - 1]
    statut = "PRESENTE_IDENTIQUE" if corps == attendu.rstrip() else "DIVERGENTE"
    return statut, hashlib.sha256(corps.encode("utf-8")).hexdigest()


def _action(cible, sha_attendu, source_sha, genre):
    """Décrit une future action avec sauvegarde et contrôles requis ; n'exécute rien."""
    return {
        "action": genre,
        "cible": cible,
        "sha256_attendu": sha_attendu,
        "source": {"type": "rendu des vues depuis SQLite en lecture seule", "sha256": source_sha},
        "preconditions_requises": {
            "sauvegarde_verifiee_avant": True,
            "rendu_source_recalcule_et_identique": True,
            "cible_reverifiee_sans_lien": True,
            "controle_apres_application": True,
        },
    }


def _rapport_indisponible(erreur):
    """Retourne un plan vide NON VÉRIFIÉ sans créer ni réparer l'atelier."""
    return {
        "schema": 1,
        "statut": "NON VÉRIFIÉ",
        "code": code_statut("NON VÉRIFIÉ"),
        "application_effectuee": False,
        "detail": erreur,
        "vues": [],
        "actions": [],
    }


def planifier(prive: Path | str | None = None) -> dict[str, Any]:
    """Compare les vues privées à SQLite et prépare un plan JSON sans écriture ni mesure.

    Entrée : répertoire privé local. Sortie : statuts PASS/WARN/NON VÉRIFIÉ,
    chemins relatifs et SHA-256. Une divergence ne prouve aucune falsification.
    Invariant : SQLite est ouvert en mode ro/query_only ; aucune source n'est réparée.
    """
    try:
        racine = atelier_prive(prive).absolute()
        base = sans_lien(racine / "benchab.sqlite", racine=racine, strict=True)
        if not base.is_file():
            return _rapport_indisponible("Base privée absente ou non régulière.")
        with closing(sqlite3.connect(base.as_uri() + "?mode=ro", uri=True, timeout=1)) as connexion:
            connexion.row_factory = sqlite3.Row
            connexion.execute("PRAGMA query_only = ON")
            # Toutes les vues doivent venir du même instantané et d'une base BENCHAB identifiée (#119).
            connexion.execute("BEGIN")
            identite = connexion.execute("PRAGMA application_id").fetchone()[0]
            version = connexion.execute("PRAGMA user_version").fetchone()[0]
            if identite != APPLICATION_ID or version != 1:
                return _rapport_indisponible("Identité ou schéma de la base BENCHAB non établi.")
            rendu = vues._rendre(connexion)
            fiches = vues._fiches_filaments(connexion)
    except (OSError, ValueError, sqlite3.Error, KeyError, TypeError, json.JSONDecodeError) as erreur:
        return _rapport_indisponible(f"Sources indisponibles en lecture seule ({type(erreur).__name__}).")

    manifeste = vues._manifeste(rendu)
    texte_manifeste = json.dumps(manifeste, ensure_ascii=False, sort_keys=True) + "\n"
    attendus = {**rendu["fichiers"], **fiches}
    empreintes_attendues = {
        chemin: hashlib.sha256(texte.encode("utf-8")).hexdigest()
        for chemin, texte in sorted(attendus.items())
    }
    # L'empreinte logique porte sur toutes les vues calculées, y compris les fiches modernes non listées au manifeste.
    sources_rendues = {"fichiers": empreintes_attendues, "sections": manifeste["sections"]}
    sha_source = hashlib.sha256(
        json.dumps(sources_rendues, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    lignes = []
    actions = []
    incertitudes = False

    # SQLite fait foi pour les octets attendus ; le manifeste existant ne les autorise pas (#119).
    for relatif in sorted(attendus):
        attendu = empreintes_attendues[relatif]
        actuel = _empreinte_fichier(racine, relatif)
        statut = actuel["statut"]
        if statut == "PRESENT":
            statut = "PRESENTE_IDENTIQUE" if actuel["sha256"] == attendu else "DIVERGENTE"
        entree = {"cible": relatif, "statut": statut, "sha256_attendu": attendu}
        if "sha256" in actuel:
            entree["sha256_actuel"] = actuel["sha256"]
        lignes.append(entree)
        if statut in {"ABSENTE", "DIVERGENTE"}:
            actions.append(_action(relatif, attendu, sha_source, "REGENERER_VUE"))
        elif statut not in {"PRESENTE_IDENTIQUE"}:
            incertitudes = True

    attendu_section = rendu["sections"][README]
    sha_section = manifeste["sections"][README]
    lu = _empreinte_fichier(racine, README, lecture=True)
    if lu["statut"] == "ABSENTE":
        statut, sha_actuel = "ABSENTE", None
    elif lu["statut"] == "LIEN_A_VERIFIER" or lu["statut"] == "A_VERIFIER":
        statut, sha_actuel = lu["statut"], None
    else:
        try:
            statut, sha_actuel = _section_readme(lu["contenu"], attendu_section)
        except (KeyError, TypeError):
            statut, sha_actuel = "A_VERIFIER", None
    entree = {"cible": README + "#BENCHAB:BASE", "statut": statut, "sha256_attendu": sha_section}
    if sha_actuel is not None:
        entree["sha256_actuel"] = sha_actuel
    lignes.append(entree)
    if statut in {"ABSENTE", "DIVERGENTE"}:
        actions.append(_action(entree["cible"], sha_section, sha_source, "REGENERER_SECTION"))
    elif statut != "PRESENTE_IDENTIQUE":
        incertitudes = True

    manifeste_actuel = _empreinte_fichier(racine, MANIFESTE)
    statut = manifeste_actuel["statut"]
    if statut == "PRESENT":
        statut = "PRESENTE_IDENTIQUE" if manifeste_actuel["sha256"] == hashlib.sha256(
            texte_manifeste.encode("utf-8")
        ).hexdigest() else "DIVERGENTE"
    entree = {
        "cible": MANIFESTE,
        "statut": statut,
        "sha256_attendu": hashlib.sha256(texte_manifeste.encode("utf-8")).hexdigest(),
    }
    if "sha256" in manifeste_actuel:
        entree["sha256_actuel"] = manifeste_actuel["sha256"]
    lignes.append(entree)
    if statut in {"ABSENTE", "DIVERGENTE"}:
        actions.append(_action(MANIFESTE, entree["sha256_attendu"], sha_source, "REGENERER_MANIFESTE"))
    elif statut != "PRESENTE_IDENTIQUE":
        incertitudes = True

    statut_global = "NON VÉRIFIÉ" if incertitudes else ("WARN" if actions else "PASS")
    return {
        "schema": 1,
        "statut": statut_global,
        "code": code_statut(statut_global),
        "application_effectuee": False,
        "source": {"cible": "benchab.sqlite#vues", "sha256_rendu_attendu": sha_source},
        "vues": lignes,
        "actions": actions,
        "preconditions_futures_requises": {
            "sauvegarde_verifiee_avant": True,
            "source_recalculee_avant_application": True,
            "verification_apres_application": True,
        },
    }
