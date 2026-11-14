"""Contrat U25 du débit nominal commandé ; aucun plafond physique universel ni réglage modifié."""

import math


def section_nominale(largeur_mm, hauteur_mm):
    """Estime la section du cordon en mm² à partir de ses dimensions en mm.

    Modèle à bords arrondis utilisé avant tranchage, distinct d’un dépôt
    commandé par le G-code. Retourne None si les dimensions sont incompatibles.
    """
    largeur, hauteur = positif(largeur_mm), positif(hauteur_mm)
    if largeur is None or hauteur is None or largeur < hauteur:
        return None
    aire = hauteur * (largeur - hauteur) + math.pi * hauteur**2 / 4
    return positif(aire)


def positif(valeur):
    """Lit un nombre fini strictement positif ; renvoie float ou None, unités fournies par l'appelant."""
    if isinstance(valeur, bool):
        return None
    try:
        nombre = float(valeur)
    except (TypeError, ValueError, OverflowError):
        return None
    return nombre if math.isfinite(nombre) and nombre > 0 else None


def mesurer(mouvement, diametre_mm):
    """Mesure un dépôt nominal : E et longueur de trajectoire en mm, F en mm/min, diamètre en mm.

    Renvoie volume_mm3, duree_depot_s et debit_mm3_s ; None/NON VÉRIFIÉ si incomplet.
    La durée couvre la partie déposée du mouvement, après remboursement de rétraction.
    La longueur fournie peut être une longueur analytique d’arc XY G17, jamais une corde.
    Ne simule ni dynamique, ni firmware, ni débit physique ; voir docs/reference/debit.md.
    """
    valeurs = [positif(v) for v in (
        mouvement.get("deposited"), mouvement.get("deposited_length"), mouvement.get("feed"), diametre_mm
    )]
    inconnu = {"statut": "NON VÉRIFIÉ", "volume_mm3": None, "duree_depot_s": None, "debit_mm3_s": None}
    if any(v is None for v in valeurs):
        return inconnu
    extrusion, longueur, avance, diametre = valeurs
    try:
        volume = extrusion * math.pi * (diametre / 2) ** 2
        duree = longueur / (avance / 60)
        debit = volume / duree
    except (OverflowError, ZeroDivisionError):
        return inconnu
    if not all(math.isfinite(v) and v > 0 for v in (volume, duree, debit)):
        return inconnu
    return {"statut": "PASS", "volume_mm3": volume, "duree_depot_s": duree, "debit_mm3_s": debit}


def verdict(debit_mm3_s, plafond_mm3_s):
    """Compare un débit et un plafond déclarés en mm³/s ; PASS/FAIL/NON VÉRIFIÉ, sans écriture.

    Seule la tolérance d'arrondi max(1e-9, plafond × 1e-10) est admise ; aucun +5 % physique.
    """
    debit, plafond = positif(debit_mm3_s), positif(plafond_mm3_s)
    if debit is None or plafond is None:
        return "NON VÉRIFIÉ"
    return "FAIL" if debit > plafond + max(1e-9, plafond * 1e-10) else "PASS"


def bilan(echantillons, plafond_mm3_s):
    """Résume des couples (débit mm³/s, longueur déposée mm) ; p99 informatif, maximum strict.

    Un dépassement établi donne FAIL même si un autre échantillon est incomplet ; sinon
    toute donnée manquante donne NON VÉRIFIÉ. Ne déduit aucun plafond ni validation physique.
    """
    valides, incomplets = [], False
    for debit, longueur in echantillons:
        debit, longueur = positif(debit), positif(longueur)
        if debit is None or longueur is None:
            incomplets = True
        else:
            valides.append((debit, longueur))
    total = sum(longueur for _, longueur in valides)
    maximum = max((debit for debit, _ in valides), default=None)
    depassements = sum(verdict(debit, plafond_mm3_s) == "FAIL" for debit, _ in valides)
    percentile, cumul = None, 0.0
    if math.isfinite(total) and total > 0:
        for debit, longueur in sorted(valides):
            cumul += longueur
            if cumul >= total * 0.99:
                percentile = debit
                break
    statut = "FAIL" if depassements else "NON VÉRIFIÉ" if (
        incomplets or percentile is None or positif(plafond_mm3_s) is None
    ) else "PASS"
    return {
        "statut": statut, "p99_mm3_s": percentile, "maximum_mm3_s": maximum,
        "depassements": depassements, "echantillons_mesures": len(valides), "mesure_incomplete": incomplets,
    }
