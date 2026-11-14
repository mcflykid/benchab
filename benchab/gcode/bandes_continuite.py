"""Synthèses par bandes Z des mesures existantes ; aucun second lecteur G-code ni verdict physique."""

import copy
import math
from decimal import ROUND_CEILING, Decimal

MAX_BANDES = 10_000


def ajouter_bandes(rapport, largeur_mm, intervalle_z_mm=None):
    """Copie un rapport et ajoute ses bandes Z en mm, sans modifier couches ni statut.

    Intervalles [min,max), dernière borne haute incluse ; au plus 10 000 bandes.
    Renvoie un dict ; ValueError si largeur/bornes invalides. Une bande vide reste NON VÉRIFIÉE.
    """
    try:
        largeur_valide = not isinstance(largeur_mm, bool) and math.isfinite(largeur_mm) and largeur_mm > 0
    except (TypeError, ValueError, OverflowError):
        largeur_valide = False
    if not largeur_valide:
        raise ValueError("Largeur de bande Z positive et finie requise, en mm")
    couches = rapport.get("couches", [])
    resultat = copy.deepcopy(rapport)
    resultat["bandes_z"] = []
    resultat["convention_bandes_z"] = "[z_min_mm, z_max_mm), dernière borne supérieure incluse ; mm"
    if intervalle_z_mm is None:
        if not couches:
            return resultat
        intervalle_z_mm = (min(c["z_mm"] for c in couches), max(c["z_mm"] for c in couches))
    try:
        bornes_valides = len(intervalle_z_mm) == 2 and all(
            not isinstance(z, bool) and math.isfinite(z) for z in intervalle_z_mm
        )
    except (TypeError, ValueError, OverflowError):
        bornes_valides = False
    if not bornes_valides:
        raise ValueError("Deux bornes Z finies requises, en mm")
    debut, fin = (Decimal(str(z)) for z in intervalle_z_mm)
    largeur = Decimal(str(largeur_mm))
    if fin < debut:
        raise ValueError("Bornes Z inversées")
    nombre = max(1, int(((fin - debut) / largeur).to_integral_value(rounding=ROUND_CEILING)))
    if nombre > MAX_BANDES:
        raise ValueError("Nombre de bandes Z supérieur au budget de 10 000")
    groupes = [[] for _ in range(nombre)]
    # Decimal conserve l'appartenance des valeurs décimales aux bornes (ex. 0,3 mm).
    # Voir docs/reference/continuite-paroi.md ; aucune réinterprétation des trajectoires.
    for couche in couches:
        z = Decimal(str(couche["z_mm"]))
        if debut <= z <= fin:
            index = min(nombre - 1, int((z - debut) // largeur))
            groupes[index].append(couche)
    for index, groupe in enumerate(groupes):
        bas, haut = debut + largeur * index, min(fin, debut + largeur * (index + 1))
        segments = sum(c["segments"] for c in groupe)
        micro = sum(c["microsegments"] for c in groupe)
        resultat["bandes_z"].append({
            "z_min_mm": float(bas), "z_max_mm": float(haut),
            "borne_superieure_incluse": index == nombre - 1,
            "partielle": haut - bas < largeur, "couches_analysees": len(groupe),
            "segments": segments, "microsegments": micro,
            "fraction_microsegments": micro / segments if segments else None,
            "max_morceaux_par_couche": max((c["morceaux"] for c in groupe), default=None),
            "couverture": "MESURÉE" if groupe else "NON VÉRIFIÉE",
        })
    return resultat
