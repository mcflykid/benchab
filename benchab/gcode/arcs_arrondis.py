"""Calcule des intervalles XY conditionnels ; ne qualifie ni trajectoire ni débit."""

import math


def _xy(valeurs, nom):
    """Vérifie une paire de coordonnées en mm dans le domaine de la sonde."""
    if (
        not isinstance(valeurs, (tuple, list))
        or len(valeurs) != 2
        or any(type(v) not in (int, float) or not -1e7 <= v <= 1e7 for v in valeurs)
    ):
        raise ValueError(f"{nom} : deux valeurs finies entre -10 000 000 et 10 000 000 mm")
    return tuple(float(v) for v in valeurs)


def intervalle_xy(depart_xy_mm, arrivee_xy_mm, decalage_ij_mm, *, sens, demi_pas_mm):
    """Encadre rayon, balayage et longueur XY d’un éventuel cercle avant arrondi.

    Les coordonnées XY et les décalages I/J relatifs sont en mm ; le sens
    G2/G3 et le demi-pas maximal par champ sont déclarés par l’appelant.
    Le résultat reste NON VÉRIFIÉ, avec intervalles en mm et radians si
    l’hypothèse ne donne pas de contradiction immédiate. Il ne démontre pas
    l’existence d’un cercle commun, ne reconstruit aucune trajectoire et ne
    sert pas au contrôle de débit U25. Aucun fichier ni réglage n’est modifié.
    """
    depart, arrivee, decalage = (
        _xy(valeurs, nom)
        for valeurs, nom in (
            (depart_xy_mm, "Départ XY"), (arrivee_xy_mm, "Arrivée XY"), (decalage_ij_mm, "Décalage I/J")
        )
    )
    if sens not in ("G2", "G3"):
        raise ValueError("Sens d’arc G2 ou G3 requis")
    if type(demi_pas_mm) not in (int, float) or not 0 < demi_pas_mm <= 0.5:
        raise ValueError("Le demi-pas doit être fini, positif et inférieur ou égal à 0,5 mm")
    rapport = {
        "statut": "NON VÉRIFIÉ",
        "hypothese": "Arrondi indépendant de chaque champ X, Y, I et J à demi-pas borné.",
        "demi_pas_mm": float(demi_pas_mm),
        "rayon_conditionnel_mm": None,
        "balayage_conditionnel_rad": None,
        "longueur_xy_conditionnelle_mm": None,
        "cercle_existant_prouve": False,
        "validation_physique": False,
    }
    ux, uy = (-d for d in decalage)
    vx, vy = (fin - debut - d for debut, fin, d in zip(depart, arrivee, decalage, strict=True))
    r0, r1 = math.hypot(ux, uy), math.hypot(vx, vy)
    # Le vecteur final cumule les erreurs de trois champs par axe. La
    # marge binaire agrandit le budget lors des soustractions de grandes cotes.
    echelle = max(1.0, *(abs(v) for v in (*depart, *arrivee, *decalage)), r0, r1)
    marge_mm = 8 * math.ulp(echelle)
    epsilon_xy = math.sqrt(2) * demi_pas_mm
    e0, e1 = epsilon_xy + marge_mm, 3 * epsilon_xy + marge_mm
    rapport["ecart_rayons_observes_mm"] = abs(r0 - r1)
    if r0 <= e0 or r1 <= e1:
        return {**rapport, "raison": "direction_indeterminee"}
    rayon_min = max(0.0, r0 - e0, r1 - e1)
    rayon_max = min(r0 + e0, r1 + e1)
    if rayon_min > rayon_max:
        return {**rapport, "raison": "rayons_incompatibles"}

    debut, fin = math.atan2(uy, ux), math.atan2(vy, vx)
    angle = ((fin - debut) if sens == "G3" else (debut - fin)) % math.tau
    incertitude = math.asin(e0 / r0) + math.asin(e1 / r1) + 8 * math.ulp(math.tau)
    angle_min, angle_max = angle - incertitude, angle + incertitude
    if not 0 < angle_min <= angle_max < math.tau:
        # Une branche voisine du tour complet n’est pas assimilable à un arc court.
        return {**rapport, "raison": "balayage_discontinu"}
    rayon = [max(0.0, math.nextafter(rayon_min, -math.inf)), math.nextafter(rayon_max, math.inf)]
    balayage = [math.nextafter(angle_min, -math.inf), math.nextafter(angle_max, math.inf)]
    longueur = [math.nextafter(rayon[0] * balayage[0], -math.inf), math.nextafter(rayon[1] * balayage[1], math.inf)]
    return {
        **rapport,
        "raison": "intervalle_conditionnel",
        "rayon_conditionnel_mm": rayon,
        "balayage_conditionnel_rad": balayage,
        "longueur_xy_conditionnelle_mm": longueur,
    }
