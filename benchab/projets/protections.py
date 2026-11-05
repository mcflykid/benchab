"""Repère les fichiers de projet dont les rapports ne peuvent pas être remplacés."""

import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from benchab.donnees.base import APPLICATION_ID


def suivi_base(projet):
    """Lit les références canoniques sans créer de base ni modifier ses exports."""
    projet = Path(projet).resolve()
    for parent in projet.parents:
        chemin = parent / "benchab.sqlite"
        if not chemin.is_file():
            continue
        if chemin.is_symlink():
            raise ValueError("Base de protections liée refusée.")
        with closing(sqlite3.connect(chemin.as_uri() + "?mode=ro", uri=True)) as connexion:
            if connexion.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
                raise ValueError("Base de protections étrangère.")
            ident = projet.relative_to(parent).as_posix()
            ligne = connexion.execute("SELECT donnees_json FROM projets WHERE id=?", (ident,)).fetchone()
            return json.loads(ligne[0]).get("suivi", {}) if ligne else {}
    return {}


def protections(projet):
    """Retourne les chemins relatifs gelés d'un projet et leurs empreintes déclarées."""
    projet = Path(projet)
    proteges = {}
    livrables = projet / "LIVRABLES.json"
    if livrables.is_file():
        for item in json.loads(livrables.read_text()).get("fichiers", []):
            proteges[item["chemin"]] = item.get("sha256")

    verrou = projet / "VERROU-REIMPRESSION.json"
    if verrou.is_file():
        donnees_verrou = json.loads(verrou.read_text())
        if donnees_verrou.get("etat", "actif") == "actif":
            proteges.update(donnees_verrou.get("fichiers", {}))

    preparations = projet / "preparations"
    if preparations.is_dir():
        for dossier in preparations.glob("P[0-9]*"):
            if dossier.is_dir() and not dossier.name.endswith(".en-cours"):
                for chemin in dossier.rglob("*"):
                    if (
                        chemin.is_file()
                        and "__pycache__" not in chemin.parts
                        and not chemin.name.startswith(".")
                        and not chemin.name.endswith((".lock", ".tmp", ".pyc"))
                    ):
                        proteges.setdefault(chemin.relative_to(projet).as_posix(), None)

    qualite = projet / "qualite.json"
    donnees_qualite = json.loads(qualite.read_text()) if qualite.is_file() else {}
    for nom, info in donnees_qualite.get("protegees", {}).items():
        # Une empreinte seule protège aussi une copie renommée (U36,
        # docs/reference/regles.md) dans les parcours projet et ajout manuel.
        if isinstance(info, str) and re.fullmatch(r"[0-9a-fA-F]{64}", info):
            proteges[nom] = info.lower()
            continue
        if not isinstance(info, dict):
            raise ValueError("Une protection doit fournir une empreinte SHA-256 ou un objet de référence.")
        if info.get("fichier"):
            proteges[info["fichier"]] = info.get("sha256")
        for cle, empreinte in (("stl_assemblage", "sha256_assemblage"), ("stl_impression", "sha256_impression")):
            if donnees_qualite.get(cle) and info.get(empreinte):
                proteges[str(Path(donnees_qualite[cle]) / f"{nom}.stl")] = info[empreinte]

    suivi = donnees_qualite.get("suivi", {})
    canonique = suivi_base(projet)
    references = (
        suivi.get("references_imprimees", [])
        + suivi.get("references_historiques", [])
        + canonique.get("references_imprimees", [])
        + canonique.get("references_historiques", [])
    )
    for reference in references:
        for item in reference.get("fichiers", []):
            chemin = item["chemin"]
            empreinte = item.get("sha256")
            if chemin in proteges and proteges[chemin] and empreinte and proteges[chemin] != empreinte:
                raise ValueError("empreintes protégées contradictoires : " + chemin)
            if not proteges.get(chemin):
                proteges[chemin] = empreinte
    return proteges
