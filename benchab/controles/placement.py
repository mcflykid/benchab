"""Contrat de placement avant contrôle ; voir docs/reference/placement.md."""

import json
import math
import re
from pathlib import Path


def _refuser(chemin, raison):
    raise ValueError(f"Placement invalide : {chemin} : {raison}")


def _vecteur(valeur, chemin, longueurs, *, positif=False):
    if not isinstance(valeur, list) or len(valeur) not in longueurs:
        _refuser(chemin, "vecteur de dimensions attendues requis")
    if any(type(v) not in (int, float) or not math.isfinite(v) or positif and v < 0 for v in valeur):
        _refuser(chemin, "coordonnées finies en mm, dimensions positives ou nulles requises")


def valider(document):
    """Valide un placement historique ou schéma 1 ; rend le dict intact ou lève un FAIL localisé.

    Coordonnées et dimensions en mm ; aucune mesure G-code, normalisation de réglage ni écriture.
    Les champs supplémentaires et les intentions indéterminées restent inchangés.
    """
    if not isinstance(document, dict):
        _refuser("racine", "objet JSON requis")
    if "schema" in document and (type(document["schema"]) is not int or document["schema"] != 1):
        _refuser("schema", "seul le schéma 1 est reconnu")
    plaques = document.get("plates")
    if not isinstance(plaques, list) or not plaques:
        _refuser("plates", "liste non vide requise")
    noms_plaques = set()
    for numero, plaque in enumerate(plaques, 1):
        chemin = f"plates[{numero - 1}]"
        if not isinstance(plaque, dict):
            _refuser(chemin, "objet JSON requis")
        nom = plaque.get("name")
        if not isinstance(nom, str) or not nom.strip() or nom in noms_plaques:
            _refuser(chemin + ".name", "nom non vide et unique requis")
        noms_plaques.add(nom)
        if "index" in plaque and (type(plaque["index"]) is not int or plaque["index"] != numero):
            _refuser(chemin + ".index", "index contigu base 1 requis")
    objets = document.get("objects")
    if not isinstance(objets, list) or not objets:
        _refuser("objects", "liste non vide requise")
    identites = set()
    comptes = [0] * len(plaques)
    for index, objet in enumerate(objets):
        chemin = f"objects[{index}]"
        if not isinstance(objet, dict):
            _refuser(chemin, "objet JSON requis")
        nom, plaque = objet.get("name"), objet.get("plate")
        if not isinstance(nom, str) or not nom.strip():
            _refuser(chemin + ".name", "nom non vide requis")
        if type(plaque) is not int or not 1 <= plaque <= len(plaques):
            _refuser(chemin + ".plate", "plaque existante, index base 1 requis")
        if (plaque, nom) in identites:
            _refuser(chemin + ".name", "identité dupliquée sur la plaque")
        identites.add((plaque, nom))
        comptes[plaque - 1] += 1
        _vecteur(objet.get("xy"), chemin + ".xy", (2,))
        _vecteur(objet.get("size"), chemin + ".size", (2, 3), positif=True)
        if "reglages" in objet and not isinstance(objet["reglages"], dict):
            _refuser(chemin + ".reglages", "dictionnaire typé attendu par le consommateur")
    for index, plaque in enumerate(plaques):
        if "objects" in plaque and (type(plaque["objects"]) is not int or plaque["objects"] != comptes[index]):
            _refuser(f"plates[{index}].objects", "compte incohérent avec les objets déclarés")
    empreinte = document.get("assembly_manifest_sha256")
    if "assembly_manifest_sha256" in document and (
        not isinstance(empreinte, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", empreinte)
    ):
        _refuser("assembly_manifest_sha256", "empreinte SHA-256 requise")
    return document


def lire(chemin):
    """Lit et valide un placement JSON ; renvoie dict ou FAIL, unités mm, aucun fichier modifié."""
    def paires(champs):
        resultat = {}
        for cle, valeur in champs:
            if cle in resultat:
                _refuser(cle, "clé JSON dupliquée")
            resultat[cle] = valeur
        return resultat

    # Une clé répétée rend l’intention ambiguë même si json.loads garderait la dernière.
    return valider(json.loads(Path(chemin).read_text(encoding="utf-8"), object_pairs_hook=paires))
