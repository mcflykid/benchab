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
