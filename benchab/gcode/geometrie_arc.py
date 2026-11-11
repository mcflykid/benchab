"""Intersection analytique des dépôts G17 avec des boîtes en repère physique."""

import math

_MAX_COORDONNEE_MM = 1e6


def _ajouter_angles(coupures, angles, a0, balayage, debut):
    # Un seul tour est qualifié ; ces périodes couvrent les angles [-3π, 3π].
    for racine in angles:
        for tour in range(-2, 3):
            fraction = (racine + tour * math.tau - a0) / balayage
            if debut < fraction < 1:
                coupures.add(fraction)


def enveloppe_deposee_xyz(mouvement):
    """Vérifier la géométrie et calculer les bornes XYZ du dépôt G17, en mm.

    Entrée : mouvement qualifié par l'interpréteur, centre physique en mm,
    angles en rad et début de dépôt en fraction de balayage.
    Sortie : triplet de paires (min, max) pour X, Y et Z, en mm.
    Échec : ValueError si la courbe est incohérente ou hors de ±1 000 000 mm.
    Ne remplace jamais l'arc par sa corde et ne valide aucune propriété physique.
    """
    try:
        arc = mouvement["arc_g17"]
        start, end = tuple(map(float, mouvement["start"])), tuple(map(float, mouvement["end"]))
        cx, cy = map(float, arc["centre_xy_mm"])
        rayon = float(arc["rayon_mm"])
        a0 = float(arc["angle_debut_rad"])
        balayage = float(arc["balayage_signe_rad"])
        debut = float(arc["debut_depot_fraction"])
    except (KeyError, TypeError, ValueError, OverflowError) as erreur:
        raise ValueError("Géométrie G17 du dépôt absente ou invalide.") from erreur
    if len(start) != 3 or len(end) != 3:
        raise ValueError("Coordonnées XYZ du dépôt invalides.")
    valeurs = (*start, *end, cx, cy, rayon, a0, balayage, debut)
    if (
        not all(math.isfinite(valeur) for valeur in valeurs)
        or any(abs(valeur) > _MAX_COORDONNEE_MM for valeur in (*start, *end, cx, cy, rayon))
        or rayon <= 0
        or abs(balayage) <= 0
        or abs(balayage) > math.tau
        or abs(a0) > math.pi
        or not 0 <= debut < 1
        or abs(cx) + rayon > _MAX_COORDONNEE_MM
        or abs(cy) + rayon > _MAX_COORDONNEE_MM
    ):
        raise ValueError("Arc G17 hors du domaine d'enveloppe XYZ.")
    rayon_debut = math.hypot(start[0] - cx, start[1] - cy)
    x_debut = cx + rayon * math.cos(a0)
    y_debut = cy + rayon * math.sin(a0)
    angle_fin = a0 + balayage
    x_fin = cx + rayon * math.cos(angle_fin)
    y_fin = cy + rayon * math.sin(angle_fin)
    if (
        not math.isclose(rayon_debut, rayon, rel_tol=1e-7, abs_tol=1e-6)
        or not math.isclose(x_debut, start[0], rel_tol=0.0, abs_tol=1e-6)
        or not math.isclose(y_debut, start[1], rel_tol=0.0, abs_tol=1e-6)
        or not math.isclose(x_fin, end[0], rel_tol=0.0, abs_tol=1e-6)
        or not math.isclose(y_fin, end[1], rel_tol=0.0, abs_tol=1e-6)
    ):
        raise ValueError("Rayon ou extrémité incohérents avec l'arc G17.")

    def point(fraction):
        angle = a0 + balayage * fraction
        return (
            cx + rayon * math.cos(angle),
            cy + rayon * math.sin(angle),
            start[2] + (end[2] - start[2]) * fraction,
        )

    points = [point(debut), point(1.0)]
    for angles in ((0.0, math.pi), (math.pi / 2, 3 * math.pi / 2)):
        for angle in angles:
            for tour in range(-2, 3):
                fraction = (angle + tour * math.tau - a0) / balayage
                if debut < fraction < 1:
                    points.append(point(fraction))
    if any(not math.isfinite(coord) or abs(coord) > _MAX_COORDONNEE_MM for p in points for coord in p):
        raise ValueError("Enveloppe XYZ hors du domaine d'un million de mm.")
    return tuple((min(p[axe] for p in points), max(p[axe] for p in points)) for axe in range(3))


def point_debut_deposee_xyz(mouvement):
    """Renvoie le premier point réellement déposé par un arc G17, en mm.

    Entrée : mouvement analytique de l'interpréteur, avec centre en mm,
    angles en rad et fraction de début de dépôt entre 0 et 1.
    Sortie : coordonnées spatiales (X, Y, Z) du début de dépôt, en mm.
    Échec : ValueError si l'arc est incohérent ou hors du domaine borné.
    Le point est évalué sur le cercle exact, sans corde ni validation physique.
    """
    # L'enveloppe commune valide l'identité géométrique et les bornes avant
    # toute utilisation du point dans un verdict (suite #123).
    enveloppe_deposee_xyz(mouvement)
    arc = mouvement["arc_g17"]
    cx, cy = map(float, arc["centre_xy_mm"])
    rayon = float(arc["rayon_mm"])
    a0 = float(arc["angle_debut_rad"])
    balayage = float(arc["balayage_signe_rad"])
    debut = float(arc["debut_depot_fraction"])
    z0, z1 = float(mouvement["start"][2]), float(mouvement["end"][2])
    angle = a0 + balayage * debut
    z = z0 + (z1 - z0) * debut
    return (cx + rayon * math.cos(angle), cy + rayon * math.sin(angle), z)


def longueur_deposee_dans_boite(mouvement, bornes):
    """Renvoie la longueur déposée G17 en mm dans la boîte cartésienne donnée.

    Mouvement de l'interpréteur avec centre physique en mm et angles en rad.
    Bornes (xmin, ymin, xmax, ymax, zmin, zmax) en mm, bords inclus.
    Refuse une géométrie absente ou hors capacité ; n'utilise pas de corde.
    """
    arc = mouvement.get("arc_g17")
    if arc is None:
        raise ValueError("Arc G17 sans géométrie qualifiée dans le repère physique.")
    xmin, ymin, xmax, ymax, zmin, zmax = bornes
    cx, cy = arc["centre_xy_mm"]
    rayon = arc["rayon_mm"]
    a0, balayage = arc["angle_debut_rad"], arc["balayage_signe_rad"]
    start, end = mouvement["start"], mouvement["end"]
    z0, z1 = start[2], end[2]
    debut = arc["debut_depot_fraction"]
    if (
        any(not math.isfinite(x) or abs(x) > _MAX_COORDONNEE_MM for x in (*bornes, *start, *end, cx, cy, rayon))
        or max(abs(cx), abs(cy)) + rayon > _MAX_COORDONNEE_MM
        or xmin > xmax or ymin > ymax or zmin > zmax
    ):
        raise ValueError("Bornes ou arc hors domaine de mesure des régions.")

    coupures = {debut, 1.0}
    # Les intersections avec les six faces découpent la courbe en intervalles
    # où l'appartenance est constante ; une tangence seule ne dépose aucune longueur.
    for x in (xmin, xmax):
        rapport = (x - cx) / rayon
        if abs(rapport) <= 1:
            angle = math.acos(rapport)
            _ajouter_angles(coupures, (angle, -angle), a0, balayage, debut)
    for y in (ymin, ymax):
        rapport = (y - cy) / rayon
        if abs(rapport) <= 1:
            angle = math.asin(rapport)
            _ajouter_angles(coupures, (angle, math.pi - angle), a0, balayage, debut)
    if z1 != z0:
        for z in (zmin, zmax):
            fraction = (z - z0) / (z1 - z0)
            if debut < fraction < 1:
                coupures.add(fraction)

    pas = sorted(coupures)
    total = 0.0
    for a, b in zip(pas, pas[1:]):
        t = (a + b) / 2
        angle = a0 + balayage * t
        x, y = cx + rayon * math.cos(angle), cy + rayon * math.sin(angle)
        z = z0 + (z1 - z0) * t
        if xmin <= x <= xmax and ymin <= y <= ymax and zmin <= z <= zmax:
            total += b - a
    return mouvement["length"] * total


def vitesses_axes_nominales_g17(mouvement):
    """Renvoie les maxima absolus X/Y/Z en mm/s de l’arc G17 entier.

    Le mouvement analytique fournit centre et rayon en mm, angles en radians,
    coordonnées physiques en mm et avance F en mm/min. ValueError si l’arc
    ou l’avance ne permet pas le calcul. Le calcul à F constant inclut le
    trajet avant dépôt ; il ne qualifie ni accélération, ni jonction,
    ni interpolation du micrologiciel et ne change aucun réglage.
    """
    try:
        if mouvement["trajectory"] != "arc_g17_xy" or isinstance(mouvement["feed"], bool):
            raise ValueError("Arc G17 et avance F explicites requis.")
        arc = mouvement["arc_g17"]
        rayon, angle, balayage = (float(arc[k]) for k in ("rayon_mm", "angle_debut_rad", "balayage_signe_rad"))
        dz = float(mouvement["end"][2]) - float(mouvement["start"][2])
        longueur_declaree, feed = float(mouvement["length"]), float(mouvement["feed"]) / 60
    except (KeyError, TypeError, ValueError, IndexError, OverflowError) as erreur:
        raise ValueError("Paramètres de vitesse nominale G17 absents ou invalides.") from erreur
    # La vérification du cercle entier ne dépend pas de la fraction déposée :
    # un déplacement à vide peut demander les mêmes vitesses (#123).
    enveloppe_deposee_xyz({**mouvement, "arc_g17": {**arc, "debut_depot_fraction": 0.0}})
    longueur_xy = rayon * abs(balayage)
    longueur = math.hypot(longueur_xy, dz)
    if not 0 < feed < math.inf or longueur <= 0 or not math.isclose(longueur, longueur_declaree, rel_tol=1e-8):
        raise ValueError("Longueur de l’arc ou avance F nominale incohérente.")
    vitesse_xy = feed * (longueur_xy / longueur)
    candidats = {0.0, 1.0}
    _ajouter_angles(candidats, (0.0, math.pi / 2, math.pi, -math.pi / 2), angle, balayage, 0.0)
    angles = [angle + fraction * balayage for fraction in candidats]
    return {
        "X": vitesse_xy * max(abs(math.sin(a)) for a in angles),
        "Y": vitesse_xy * max(abs(math.cos(a)) for a in angles),
        "Z": feed * (abs(dz) / longueur),
    }
