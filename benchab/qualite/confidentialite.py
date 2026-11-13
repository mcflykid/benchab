"""Noms privés dynamiques : les expressions de contrôle ne sont jamais publiées."""

import json
import re
import sqlite3
from pathlib import Path

from benchab.donnees.base import APPLICATION_ID
from benchab.qualite.ecritures import _sans_lien


def noms_prives(prive, *, inclure_alias=True):
    """Renvoie les noms privés et, sur demande, les alias déclarés, sans modifier la base."""
    base = _sans_lien(Path(prive).absolute() / "benchab.sqlite")
    if not base.exists():
        return set()
    valeurs = set()
    cles_noms = {"noms_modeles_tiers", "noms_confidentiels", "nom_modele_tiers"}
    if inclure_alias:
        cles_noms.update(("alias", "aliases"))

    def aliases(objet):
        if isinstance(objet, dict):
            for cle, valeur in objet.items():
                if cle in cles_noms:
                    valeurs.update(
                        [valeur]
                        if isinstance(valeur, str)
                        else [s for s in valeur if isinstance(s, str)]
                        if isinstance(valeur, list)
                        else []
                    )
                aliases(valeur)
        elif isinstance(objet, list):
            for valeur in objet:
                aliases(valeur)

    with sqlite3.connect(base.as_uri() + "?mode=ro", uri=True) as connexion:
        if connexion.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
            raise ValueError("Base privée étrangère : confidentialité non vérifiée")
        for ident, nom, origine, brut in connexion.execute("SELECT id,nom,origine,donnees_json FROM projets"):
            # Une origine peut être un libellé générique. Les chemins d’origine restent protégés.
            origine_explicite = isinstance(origine, str) and ("/" in origine or "\\" in origine)
            for v in (ident, nom, origine if inclure_alias or origine_explicite else None):
                if isinstance(v, str) and v.strip():
                    valeurs.add(v.strip())
                    valeurs.add(v.rstrip("/").rsplit("/", 1)[-1])
            donnees = json.loads(brut)
            aliases(donnees)
        for (brut,) in connexion.execute("SELECT donnees_json FROM decisions"):
            bloc = json.loads(brut).get("confidentialite", {})
            cles = ("modeles_tiers", "identifiants_github", "emails_locaux")
            for cle in (*cles, "alias") if inclure_alias else cles:
                valeurs.update(s for s in bloc.get(cle, []) if isinstance(s, str))
    return {v.strip() for v in valeurs if v.strip()}


def motifs(prive, *, inclure_alias=True):
    """Construit les motifs en mémoire ; renvoie des regex, sans journaliser de donnée personnelle ni statut."""
    expressions = []
    for nom in noms_prives(prive, inclure_alias=inclure_alias):
        # Un alias peut être écrit avec tirets, espaces ou underscores. Les noms
        # sont des données privées, pas une liste livrée (frontière dans docs/decisions).
        mots = re.split(r"[\s_-]+", nom.strip())
        expressions.append(re.compile(r"(?<![^\W_])" + r"[\s_-]+".join(map(re.escape, mots)) + r"(?![^\W_])", re.I))
    return expressions


def contient(texte, expressions):
    """Renvoie un booléen de fuite, sans unité ; ne révèle aucun nom privé et n’autorise jamais la publication."""
    return any(m.search(texte) for m in expressions)
