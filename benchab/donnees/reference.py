"""Import idempotent du savoir général livré avec le paquet."""

import hashlib
import json
from functools import lru_cache
from importlib.resources import files

CHAMPS = ("id", "texte", "categorie", "source", "date_source", "public", "donnees_json")


@lru_cache(maxsize=1)
def _index(texte):
    lignes = tuple(json.loads(ligne) for ligne in texte.splitlines())
    empreintes = {
        json.dumps(ligne, sort_keys=True): {
            "reference_paquet": "reference/connaissances.jsonl", "id": ligne["id"],
            "sha256_ligne": hashlib.sha256(json.dumps(ligne, sort_keys=True).encode()).hexdigest(),
        } for ligne in lignes
    }
    return lignes, empreintes


def _reference():
    # La clé est le contenu exact, pas seulement une date de fichier : toute
    # modification du paquet invalide l'index, même à taille et date identiques.
    texte = files("benchab").joinpath("reference/connaissances.jsonl").read_text(encoding="utf-8")
    return _index(texte)


def _lignes():
    return [dict(ligne) for ligne in _reference()[0]]


def source_livree(ligne):
    """Provenance du contenu livré, sans la confondre avec une validation physique."""
    source = _reference()[1].get(json.dumps(dict(ligne), sort_keys=True))
    return dict(source) if source is not None else None


def verifier_source(source):
    if source.get("reference_paquet") != "reference/connaissances.jsonl":
        return False
    return source in _reference()[1].values()


def importer(base):
    lignes = _lignes()
    for ligne in lignes:
        if set(ligne) != set(CHAMPS) or ligne["public"] != 1:
            raise ValueError("Connaissance générale livrée invalide.")
        donnees = json.loads(ligne["donnees_json"])
        if not isinstance(donnees, dict) or any(k in donnees for k in ("projet", "projet_id", "preparation_id")):
            raise ValueError("Une connaissance de projet ne peut être un savoir général livré.")
    ajouts = 0
    with base.transaction() as connexion:
        connexion.execute("BEGIN IMMEDIATE")
        for ligne in lignes:
            valeurs = tuple(ligne[k] for k in CHAMPS)
            precedente = connexion.execute("SELECT * FROM connaissances WHERE id=?", (ligne["id"],)).fetchone()
            if precedente is not None:
                if tuple(precedente) != valeurs:
                    raise ValueError("Connaissance existante différente : " + ligne["id"])
            else:
                connexion.execute("INSERT INTO connaissances VALUES (?,?,?,?,?,?,?)", valeurs)
                ajouts += 1
    return ajouts
